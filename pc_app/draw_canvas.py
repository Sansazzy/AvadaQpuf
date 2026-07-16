"""Lienzo en tiempo real: pinta el movimiento de la varita en un plano 2D.

Un puntero rojo se mueve segun el giroscopio y deja su rastro. Tras unos
segundos sin movimiento significativo, el lienzo se limpia y el puntero
vuelve al centro.
"""

from __future__ import annotations

import math
import queue
import threading
import time
import tkinter as tk

from gesture_engine import GestureStore, MotionTracker
from wifi_receiver import UdpImuReceiver

CANVAS_SIZE = 720
POINTER_RADIUS = 6
TICK_MS = 16  # ~60 FPS


def run_canvas(store: GestureStore) -> None:
    settings = store.settings
    center = CANVAS_SIZE / 2
    scale = settings.draw_scale
    clear_after_s = settings.draw_clear_after_s
    move_threshold = settings.motion_threshold

    sample_q: "queue.Queue" = queue.Queue()
    stop = threading.Event()

    def rx_loop() -> None:
        try:
            with UdpImuReceiver(settings.udp_port) as rx:
                for sample in rx.samples():
                    if stop.is_set():
                        break
                    sample_q.put(sample)
        except OSError:
            pass

    rx_thread = threading.Thread(target=rx_loop, daemon=True)
    rx_thread.start()

    root = tk.Tk()
    root.title("AvadaQPuff - Trazo en vivo")
    root.resizable(False, False)

    canvas = tk.Canvas(
        root, width=CANVAS_SIZE, height=CANVAS_SIZE, bg="white", highlightthickness=0
    )
    canvas.pack()

    tracker = MotionTracker()
    state = {
        "px": center,
        "py": center,
        "last_move": time.time(),
        "pointer": None,
    }

    def clear_canvas() -> None:
        canvas.delete("all")
        tracker.reset()
        state["px"] = center
        state["py"] = center
        state["pointer"] = None

    def to_canvas(x: float, y: float) -> tuple[float, float]:
        cx = min(max(center + x * scale, 0.0), CANVAS_SIZE)
        cy = min(max(center + y * scale, 0.0), CANVAS_SIZE)
        return cx, cy

    def tick() -> None:
        if stop.is_set():
            return

        while True:
            try:
                sample = sample_q.get_nowait()
            except queue.Empty:
                break

            x, y = tracker.update(sample)
            nx, ny = to_canvas(x, y)
            speed = math.hypot(sample.gx, sample.gy, sample.gz)

            if speed >= move_threshold:
                canvas.create_line(
                    state["px"], state["py"], nx, ny,
                    fill="red", width=3, capstyle=tk.ROUND,
                )
                state["last_move"] = time.time()

            state["px"], state["py"] = nx, ny

        if state["pointer"] is not None:
            canvas.delete(state["pointer"])
        state["pointer"] = canvas.create_oval(
            state["px"] - POINTER_RADIUS, state["py"] - POINTER_RADIUS,
            state["px"] + POINTER_RADIUS, state["py"] + POINTER_RADIUS,
            fill="red", outline="",
        )

        if time.time() - state["last_move"] >= clear_after_s:
            clear_canvas()
            state["last_move"] = time.time()

        root.after(TICK_MS, tick)

    def on_close() -> None:
        stop.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(TICK_MS, tick)
    try:
        root.mainloop()
    finally:
        stop.set()
