"""Visor del GUANTE (equivalente a `draw` pero para el guante).

Muestra en tiempo real la inclinación del guante como un "joystick":
  - un punto rojo que se mueve según pitch (adelante/atrás) y roll (izq/der);
  - la zona muerta (rectángulos de enganche/soltado con histéresis);
  - las teclas W/A/S/D iluminadas cuando esa dirección está activa;
  - estado de la señal (si llegan paquetes del guante).

NO pulsa teclas: es solo para verificar que llega información y afinar los
umbrales. Usa la MISMA lógica que MovementController (vía .compute()).
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from typing import Optional, Set

from gesture_engine import GestureStore
from movement_controller import MovementController
from wifi_receiver import UdpImuReceiver

CANVAS_SIZE = 420
MAX_ANGLE = 45.0  # grados que llenan el borde del recuadro
TICK_MS = 16
POINTER_R = 8


class GloveStudio:
    def __init__(self, store: GestureStore) -> None:
        self.store = store
        self.s = store.settings
        self.center = CANVAS_SIZE / 2
        self.scale = self.center / MAX_ANGLE

        self.controller = MovementController(self.s)
        self.sample_q: "queue.Queue" = queue.Queue()
        self.stop = threading.Event()

        self._packets = 0
        self._last_glove_t = 0.0
        self._pitch = 0.0
        self._roll = 0.0
        self._keys: Set[str] = set()

        self._build_ui()
        self._start_receiver()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        self.root = tk.Tk()
        self.root.title("AvadaQPuff - Guante")
        self.root.resizable(False, False)

        self.canvas = tk.Canvas(
            self.root, width=CANVAS_SIZE, height=CANVAS_SIZE,
            bg="white", highlightthickness=0,
        )
        self.canvas.grid(row=0, column=0, rowspan=6, padx=8, pady=8)

        panel = tk.Frame(self.root)
        panel.grid(row=0, column=1, sticky="n", padx=(0, 10), pady=8)

        tk.Label(panel, text="Guante", font=("Segoe UI", 12, "bold")).pack(anchor="w")

        self.signal_label = tk.Label(panel, text="Señal: —", width=28, anchor="w")
        self.signal_label.pack(pady=(8, 0))

        self.angle_label = tk.Label(
            panel, text="pitch: —   roll: —", width=28, anchor="w",
            font=("Consolas", 11),
        )
        self.angle_label.pack(pady=(6, 0))

        self.keys_label = tk.Label(
            panel, text="Teclas: —", width=28, anchor="w",
            font=("Segoe UI", 12, "bold"), fg="#1976d2",
        )
        self.keys_label.pack(pady=(6, 0))

        tk.Label(
            panel,
            text=(
                "Activa el toggle del guante\n(LED encendido) e inclínalo.\n\n"
                "Adelante/atrás → W/S\nIzquierda/derecha → A/D\n\n"
                "No pulsa teclas: solo verifica\nla señal y ayuda a afinar\n"
                "los umbrales de spells.json."
            ),
            justify="left", fg="#444", wraplength=200,
        ).pack(pady=(14, 0))

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- Receptor UDP ----------
    def _start_receiver(self) -> None:
        def rx_loop() -> None:
            try:
                with UdpImuReceiver(
                    self.s.udp_port, self.s.invert_button
                ) as rx:
                    for sample in rx.samples():
                        if self.stop.is_set():
                            break
                        self.sample_q.put(sample)
            except OSError:
                pass

        self.rx_thread = threading.Thread(target=rx_loop, daemon=True)
        self.rx_thread.start()

    # ---------- Dibujo ----------
    def _key_pos(self) -> dict:
        m = 26
        return {
            self.s.glove_key_forward: (self.center, m),                 # arriba (W)
            self.s.glove_key_back: (self.center, CANVAS_SIZE - m),      # abajo (S)
            self.s.glove_key_left: (m, self.center),                    # izq (A)
            self.s.glove_key_right: (CANVAS_SIZE - m, self.center),     # der (D)
        }

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")

        # Zonas muertas: soltado (off) y enganche (on).
        for angle, color, dash in (
            (self.s.glove_tilt_off, "#cfd8dc", (4, 3)),
            (self.s.glove_tilt_on, "#90a4ae", None),
        ):
            r = angle * self.scale
            c.create_rectangle(
                self.center - r, self.center - r,
                self.center + r, self.center + r,
                outline=color, width=2, dash=dash,
            )

        # Ejes centrales.
        c.create_line(self.center, 0, self.center, CANVAS_SIZE, fill="#eceff1")
        c.create_line(0, self.center, CANVAS_SIZE, self.center, fill="#eceff1")

        # Etiquetas de teclas (iluminadas si activas).
        for key, (x, y) in self._key_pos().items():
            active = key in self._keys
            c.create_text(
                x, y, text=key.upper(),
                font=("Segoe UI", 16, "bold"),
                fill="#2e7d32" if active else "#b0bec5",
            )

        # Punto de inclinación.
        px = self.center + self._roll * self.scale
        py = self.center - self._pitch * self.scale
        px = min(max(px, 0), CANVAS_SIZE)
        py = min(max(py, 0), CANVAS_SIZE)
        c.create_oval(
            px - POINTER_R, py - POINTER_R, px + POINTER_R, py + POINTER_R,
            fill="red", outline="",
        )

    # ---------- Bucle ----------
    def tick(self) -> None:
        if self.stop.is_set():
            return

        while True:
            try:
                sample = self.sample_q.get_nowait()
            except queue.Empty:
                break
            self._packets += 1
            if sample.device_id != self.s.glove_id:
                continue
            self._last_glove_t = time.time()
            self._pitch, self._roll, self._keys = self.controller.compute(sample)

        receiving = (time.time() - self._last_glove_t) < 0.5
        if receiving:
            self.signal_label.config(
                text=f"Señal: recibiendo ({self._packets})", fg="#2e7d32"
            )
            self.angle_label.config(
                text=f"pitch:{self._pitch:+6.1f}  roll:{self._roll:+6.1f}"
            )
        else:
            self.signal_label.config(text="Señal: ⚠ sin datos del guante", fg="#c62828")
            # Sin datos: el toggle está apagado o descansando; centra el punto.
            self._pitch, self._roll, self._keys = 0.0, 0.0, set()
            self.angle_label.config(text="pitch: —   roll: —")

        held = "+".join(sorted(k.upper() for k in self._keys)) if self._keys else "—"
        self.keys_label.config(text=f"Teclas: {held}")

        self._redraw()
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


def run_glove_canvas(store: GestureStore) -> None:
    GloveStudio(store).run()
