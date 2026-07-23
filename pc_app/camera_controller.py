"""Cámara tipo air-mouse relativo con la varita.

Reglas (según el diseño acordado):
  - Solo controla la cámara cuando el botón de hechizo está SUELTO.
  - Mientras el botón está PULSADO (dibujando un hechizo) la cámara se congela.
  - Al soltar el botón hay un "grace period" (camera_grace_ms) antes de
    reanudar, para que el último tirón del trazo no dé un volantazo.
  - Solo eje horizontal (yaw = gz). Movimiento relativo del ratón.
  - Suavizado EMA + zona muerta para que la mano quieta no mueva la cámara.

Es "relativo": el desplazamiento del ratón ≈ velocidad angular * dt, así que
mantener la muñeca quieta (a cualquier ángulo) deja la cámara quieta y se puede
descansar el brazo.
"""

from __future__ import annotations

import time
from typing import Optional

from gesture_engine import AppSettings
from mouse_mapper import move_mouse
from wifi_receiver import ImuSample


class CameraController:
    def __init__(self, settings: AppSettings) -> None:
        self.s = settings
        self._last_t: Optional[int] = None
        self._lp = 0.0            # yaw suavizado (EMA)
        self._prev_pressed = False
        self._resume_at = 0.0     # time.time() a partir del cual se reanuda
        self._carry = 0.0         # acumulador de subpíxeles

    def update(self, sample: ImuSample) -> float:
        """Procesa una muestra de la varita y mueve la cámara si procede.

        Devuelve los píxeles horizontales aplicados (para diagnóstico).
        """
        pressed = bool(sample.btn)
        # Embrague: cam=0 -> cámara pausada (para recolocar la muñeca y seguir).
        # No afecta a los hechizos (eso lo gestiona el botón de gestos aparte).
        cam_active = bool(sample.cam)
        now = time.time()

        # Flanco de soltar el botón -> arranca el grace period.
        if self._prev_pressed and not pressed:
            self._resume_at = now + self.s.camera_grace_ms / 1000.0
        self._prev_pressed = pressed

        if self._last_t is None:
            self._last_t = sample.t
            self._lp = sample.gz
            return 0.0

        dt = max((sample.t - self._last_t) / 1000.0, 0.001)
        self._last_t = sample.t

        # El EMA se actualiza siempre para no arrastrar valores viejos al reanudar.
        a = self.s.camera_smoothing
        self._lp += a * (sample.gz - self._lp)

        # Embrague desacoplado: no mueve y deja el estado listo para reanudar
        # limpio (sin saltos ni suavizado viejo).
        if not cam_active:
            self._lp = sample.gz
            self._carry = 0.0
            return 0.0

        # Congelada mientras se pulsa el botón o durante el grace.
        if not self.s.camera_enabled or pressed or now < self._resume_at:
            self._carry = 0.0
            return 0.0

        rate = self._lp
        if abs(rate) < self.s.camera_deadband:
            return 0.0
        if self.s.camera_invert:
            rate = -rate

        # px = sensibilidad(px/grado) * grados girados en este intervalo
        move = self.s.camera_sensitivity * rate * dt + self._carry
        dx = int(move)
        self._carry = move - dx  # conserva la fracción para movimientos suaves
        if dx != 0:
            move_mouse(dx, 0)
        return float(dx)

    def reset(self) -> None:
        self._last_t = None
        self._carry = 0.0
