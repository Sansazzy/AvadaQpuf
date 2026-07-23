"""Estudio visual de AvadaQPuff.

Modos dentro de una sola ventana:
  - Dibujar: el puntero rojo pinta el movimiento; se limpia tras unos
    segundos sin actividad.
  - Grabar: capturas un hechizo repitiendo el gesto varias veces usando el
    boton de la varita (o la barra espaciadora como respaldo), le pones
    nombre y le asignas una tecla pulsandola.

Todas las llamadas a Tkinter ocurren en el hilo principal (en el bucle
`tick`). Un hilo aparte solo recibe paquetes UDP y los deja en una cola.
"""

from __future__ import annotations

import math
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog
from typing import Optional

from gesture_engine import (
    ButtonGestureRecorder,
    Gesture,
    GestureStore,
    MotionTracker,
    best_match,
)
from transport import make_receiver

CANVAS_SIZE = 720
POINTER_RADIUS = 6
TICK_MS = 16  # ~60 FPS
MIN_POINTS = 4  # descarta repeticiones demasiado cortas


class Studio:
    def __init__(self, store: GestureStore, config_path: Path) -> None:
        self.store = store
        self.config_path = config_path
        self.settings = store.settings
        self.center = CANVAS_SIZE / 2
        self.scale = self.settings.draw_scale
        self.clear_after_s = self.settings.draw_clear_after_s
        self.move_threshold = self.settings.motion_threshold
        self.samples_needed = max(1, self.settings.record_samples)

        self.sample_q: "queue.Queue" = queue.Queue()
        self.stop = threading.Event()

        # Estado de dibujo
        self.mode = "draw"  # "draw" | "record" | "test"
        self.draw_tracker = MotionTracker(self.settings)
        self.px = self.center
        self.py = self.center
        self.pointer_id: Optional[int] = None
        self.last_move = time.time()

        # Estado de grabacion
        self.rec = ButtonGestureRecorder(self.settings)
        self.pending_templates: list = []
        self.rec_prev = (self.center, self.center)
        self.space_active = False
        self._space_release_job: Optional[str] = None

        # Estado de prueba
        self.test_rec = ButtonGestureRecorder(self.settings)
        self.test_prev = (self.center, self.center)

        # Diagnóstico de entrada
        self._last_btn = False
        self._packets = 0
        self._last_packet_t = 0.0
        self._rx = None  # referencia al receptor (para status BLE)

        self._build_ui()
        self._start_receiver()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        self.root = tk.Tk()
        self.root.title("AvadaQPuff - Estudio")
        self.root.resizable(False, False)

        self.canvas = tk.Canvas(
            self.root, width=CANVAS_SIZE, height=CANVAS_SIZE,
            bg="white", highlightthickness=0,
        )
        self.canvas.grid(row=0, column=0, rowspan=6, padx=8, pady=8)

        panel = tk.Frame(self.root)
        panel.grid(row=0, column=1, sticky="n", padx=(0, 10), pady=8)

        tk.Label(panel, text="Hechizos", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(panel, width=26, height=12)
        self.listbox.pack(pady=(2, 8))

        self.record_btn = tk.Button(
            panel, text="Grabar hechizo", width=22, command=self.start_recording
        )
        self.record_btn.pack(pady=2)

        self.cancel_btn = tk.Button(
            panel, text="Cancelar grabación", width=22,
            command=self.cancel_recording, state="disabled",
        )
        self.cancel_btn.pack(pady=2)

        self.test_btn = tk.Button(
            panel, text="Probar", width=22, command=self.toggle_test
        )
        self.test_btn.pack(pady=2)

        tk.Button(
            panel, text="Borrar seleccionado", width=22, command=self.delete_selected
        ).pack(pady=2)

        self.result_label = tk.Label(
            panel, text="", width=26, height=2, wraplength=180,
            justify="left", font=("Segoe UI", 11, "bold"), fg="#1976d2",
        )
        self.result_label.pack(pady=(6, 0))

        self.status = tk.Label(
            panel, text="", width=26, height=3, wraplength=180,
            justify="left", fg="#333",
        )
        self.status.pack(pady=(8, 0))

        # Indicadores de diagnóstico
        self.signal_label = tk.Label(panel, text="Señal: —", width=26, anchor="w")
        self.signal_label.pack(pady=(10, 0))
        self.btn_label = tk.Label(
            panel, text="Botón: ○ libre", width=26, anchor="w",
            font=("Segoe UI", 10, "bold"),
        )
        self.btn_label.pack(pady=(2, 0))

        self.root.bind("<KeyPress-space>", self._space_press)
        self.root.bind("<KeyRelease-space>", self._space_release)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._refresh_list()
        self._set_status("Modo dibujo. Mueve la varita.")

    def _set_status(self, text: str) -> None:
        self.status.config(text=text)

    def _refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for g in self.store.gestures:
            self.listbox.insert(tk.END, f"{g.name}  →  {g.key}")

    # ---------- Receptor ----------
    def _start_receiver(self) -> None:
        def rx_loop() -> None:
            try:
                print(
                    f"[draw] Arrancando receptor "
                    f"(transport={getattr(self.settings, 'transport', '?')})...",
                    flush=True,
                )
                with make_receiver(self.settings, devices=["wand"]) as rx:
                    self._rx = rx
                    for sample in rx.samples():
                        if self.stop.is_set():
                            break
                        # El estudio es para la varita; ignora el guante.
                        if sample.device_id != self.settings.wand_id:
                            continue
                        self.sample_q.put(sample)
            except OSError as exc:
                print(f"[draw] Error del receptor: {exc!r}", flush=True)
            except Exception as exc:
                print(f"[draw] Error inesperado del receptor: {exc!r}", flush=True)
            finally:
                self._rx = None
                print("[draw] Receptor detenido.", flush=True)

        self.rx_thread = threading.Thread(target=rx_loop, daemon=True)
        self.rx_thread.start()

    # ---------- Helpers ----------
    def _to_canvas(self, x: float, y: float) -> tuple:
        cx = min(max(self.center + x * self.scale, 0.0), CANVAS_SIZE)
        cy = min(max(self.center + y * self.scale, 0.0), CANVAS_SIZE)
        return cx, cy

    def _drain_queue(self) -> None:
        while True:
            try:
                self.sample_q.get_nowait()
            except queue.Empty:
                break

    def _space_press(self, _event) -> None:
        # Mantener pulsada = dibujar (igual que el botón físico).
        if self._space_release_job is not None:
            self.root.after_cancel(self._space_release_job)
            self._space_release_job = None
        self.space_active = True

    def _space_release(self, _event) -> None:
        # El auto-repeat de Windows envía release+press seguidos; posponemos
        # la liberación unos ms y la cancelamos si llega otro press.
        if self._space_release_job is not None:
            self.root.after_cancel(self._space_release_job)
        self._space_release_job = self.root.after(40, self._do_space_release)

    def _do_space_release(self) -> None:
        self.space_active = False
        self._space_release_job = None

    # ---------- Modo prueba ----------
    def toggle_test(self) -> None:
        if self.mode == "record":
            return
        if self.mode == "test":
            self.mode = "draw"
            self.test_btn.config(text="Probar", relief="raised")
            self.result_label.config(text="")
            self.canvas.delete("all")
            self.pointer_id = None
            self.draw_tracker.reset()
            self.px, self.py = self.center, self.center
            self.last_move = time.time()
            self._set_status("Modo dibujo.")
            return

        if not self.store.gestures:
            self._set_status("No hay hechizos guardados para probar.")
            return

        self.mode = "test"
        self.test_rec = ButtonGestureRecorder(self.settings)
        self.space_active = False
        self.test_btn.config(text="Salir de prueba", relief="sunken")
        self.canvas.delete("all")
        self.pointer_id = None
        self._drain_queue()
        self._set_status(
            "Prueba: haz un gesto con el botón (o Espacio). No pulsa teclas, "
            "solo muestra qué hechizo detecta y con qué confianza."
        )

    def _process_test(self, sample) -> None:
        was_recording = self.test_rec.recording
        active = bool(sample.btn) or self.space_active
        trajectory = self.test_rec.feed(sample, active)

        if self.test_rec.recording and not was_recording:
            self.canvas.delete("all")
            self.pointer_id = None
            self.result_label.config(text="")
            self.test_prev = (self.center, self.center)

        if self.test_rec.recording:
            px, py = self._to_canvas(self.test_rec.tracker.x, self.test_rec.tracker.y)
            self.canvas.create_line(
                self.test_prev[0], self.test_prev[1], px, py,
                fill="#ff9800", width=3, capstyle=tk.ROUND,
            )
            self.test_prev = (px, py)

        if trajectory is not None and len(trajectory) >= MIN_POINTS:
            gesture, score, second = best_match(trajectory, self.store.gestures)
            margin_ok = (score - second) >= self.settings.confidence_margin
            if gesture and score >= gesture.threshold and margin_ok:
                self.result_label.config(
                    text=f"✦ {gesture.name}  ({score * 100:.0f}%)", fg="#2e7d32"
                )
            elif gesture:
                reason = "confianza baja" if not margin_ok else "por debajo del umbral"
                self.result_label.config(
                    text=f"? {gesture.name} {score * 100:.0f}% ({reason})",
                    fg="#c62828",
                )
            else:
                self.result_label.config(text="Sin coincidencia", fg="#c62828")

    # ---------- Modo grabacion ----------
    def start_recording(self) -> None:
        self.mode = "record"
        self.pending_templates = []
        self.rec = ButtonGestureRecorder(self.settings)
        self.space_active = False
        self.canvas.delete("all")
        self.pointer_id = None
        self.record_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.test_btn.config(state="disabled")
        self._drain_queue()
        self._set_status(
            f"Muestra 1/{self.samples_needed}: MANTÉN pulsado el botón de la "
            "varita (o Espacio) mientras dibujas y SUÉLTALO para confirmar."
        )

    def cancel_recording(self) -> None:
        self.mode = "draw"
        self.pending_templates = []
        self.record_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.test_btn.config(state="normal")
        self.canvas.delete("all")
        self.pointer_id = None
        self.draw_tracker.reset()
        self.px, self.py = self.center, self.center
        self.last_move = time.time()
        self._set_status("Grabación cancelada. Modo dibujo.")

    def _on_rep_captured(self, trajectory) -> None:
        if len(trajectory) < MIN_POINTS:
            self._set_status("Gesto demasiado corto, repite esa muestra.")
            return

        self.pending_templates.append(trajectory)
        # Dibuja la muestra capturada en verde tenue
        pts = [self._to_canvas(x, y) for x, y in trajectory]
        for a, b in zip(pts, pts[1:]):
            self.canvas.create_line(
                a[0], a[1], b[0], b[1], fill="#8bc34a", width=2
            )

        done = len(self.pending_templates)
        if done >= self.samples_needed:
            self._finalize_recording()
        else:
            self._set_status(
                f"Muestra {done + 1}/{self.samples_needed}: repite el mismo gesto."
            )

    def _finalize_recording(self) -> None:
        name = simpledialog.askstring(
            "Nuevo hechizo", "Nombre del hechizo:", parent=self.root
        )
        if not name:
            self.cancel_recording()
            return

        key = self._capture_key()
        if not key:
            self.cancel_recording()
            return

        gesture = Gesture(
            name=name,
            key=key,
            templates=self.pending_templates,
            threshold=self.settings.match_threshold,
        )
        for i, g in enumerate(self.store.gestures):
            if g.name == name:
                self.store.gestures[i] = gesture
                break
        else:
            self.store.gestures.append(gesture)

        self.store.save(self.config_path)
        self._refresh_list()
        self.mode = "draw"
        self.record_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.test_btn.config(state="normal")
        self.canvas.delete("all")
        self.pointer_id = None
        self.draw_tracker.reset()
        self.px, self.py = self.center, self.center
        self.last_move = time.time()
        self._drain_queue()
        self._set_status(f"Guardado '{name}' → tecla '{key}'.")

    def _capture_key(self) -> Optional[str]:
        top = tk.Toplevel(self.root)
        top.title("Asignar tecla")
        top.resizable(False, False)
        tk.Label(
            top, text="Pulsa la tecla a asignar\npara este hechizo…",
            padx=30, pady=30, font=("Segoe UI", 11),
        ).pack()

        result = {"key": None}

        def on_key(event) -> None:
            result["key"] = event.keysym.lower()
            top.destroy()

        top.bind("<Key>", on_key)
        top.grab_set()
        top.focus_force()
        self.root.wait_window(top)
        return result["key"]

    def delete_selected(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        gesture = self.store.gestures[idx]
        if not messagebox.askyesno("Borrar", f"¿Borrar '{gesture.name}'?"):
            return
        del self.store.gestures[idx]

        self.store.save(self.config_path)
        self._refresh_list()
        self._set_status(f"Borrado '{gesture.name}'.")

    # ---------- Bucle principal ----------
    def _process_draw(self, sample) -> None:
        x, y = self.draw_tracker.update(sample)
        nx, ny = self._to_canvas(x, y)
        speed = math.hypot(sample.gx, sample.gy, sample.gz)

        if speed >= self.move_threshold:
            self.canvas.create_line(
                self.px, self.py, nx, ny,
                fill="red", width=3, capstyle=tk.ROUND,
            )
            self.last_move = time.time()

        self.px, self.py = nx, ny

    def _process_record(self, sample) -> None:
        was_recording = self.rec.recording
        active = bool(sample.btn) or self.space_active
        trajectory = self.rec.feed(sample, active)

        if self.rec.recording and not was_recording:
            self.canvas.delete("live")
            self.rec_prev = (self.center, self.center)

        if self.rec.recording:
            px, py = self._to_canvas(self.rec.tracker.x, self.rec.tracker.y)
            self.canvas.create_line(
                self.rec_prev[0], self.rec_prev[1], px, py,
                fill="#1976d2", width=3, capstyle=tk.ROUND, tags="live",
            )
            self.rec_prev = (px, py)

        if trajectory is not None:
            self.canvas.delete("live")
            self._on_rep_captured(trajectory)

    def _update_indicators(self) -> None:
        receiving = (time.time() - self._last_packet_t) < 1.0
        rx = self._rx
        st = None
        if rx is not None and hasattr(rx, "status"):
            try:
                st = rx.status().get(self.settings.wand_id)
            except Exception:
                st = None

        if receiving:
            self.signal_label.config(
                text=f"Señal: recibiendo ({self._packets})", fg="#2e7d32"
            )
        elif st and st.get("connected"):
            self.signal_label.config(
                text=f"BLE: conectado, sin datos ({st['packets']})", fg="#ef6c00"
            )
        elif st is not None:
            self.signal_label.config(text="BLE: buscando varita...", fg="#c62828")
        else:
            self.signal_label.config(text="Señal: ⚠ sin datos", fg="#c62828")

        active = self._last_btn or self.space_active
        if active:
            self.btn_label.config(text="Botón: ● PULSADO", fg="#c62828")
        else:
            self.btn_label.config(text="Botón: ○ libre", fg="#333")

    def tick(self) -> None:
        if self.stop.is_set():
            return

        while True:
            try:
                sample = self.sample_q.get_nowait()
            except queue.Empty:
                break
            self._packets += 1
            self._last_btn = bool(sample.btn)
            self._last_packet_t = time.time()
            if self.mode == "draw":
                self._process_draw(sample)
            elif self.mode == "record":
                self._process_record(sample)
            else:
                self._process_test(sample)

        self._update_indicators()

        if self.mode == "draw":
            if self.pointer_id is not None:
                self.canvas.delete(self.pointer_id)
            self.pointer_id = self.canvas.create_oval(
                self.px - POINTER_RADIUS, self.py - POINTER_RADIUS,
                self.px + POINTER_RADIUS, self.py + POINTER_RADIUS,
                fill="red", outline="",
            )
            if time.time() - self.last_move >= self.clear_after_s:
                self.canvas.delete("all")
                self.pointer_id = None
                self.draw_tracker.reset()
                self.px, self.py = self.center, self.center
                self.last_move = time.time()

        self.root.after(TICK_MS, self.tick)

    def _on_close(self) -> None:
        self.stop.set()
        self.root.destroy()

    def run(self) -> None:
        self.root.after(TICK_MS, self.tick)
        try:
            self.root.mainloop()
        finally:
            self.stop.set()


def run_canvas(store: GestureStore, config_path: Path) -> None:
    Studio(store, config_path).run()
