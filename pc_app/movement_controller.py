"""Traduce la inclinación del guante (acelerómetro) en teclas WASD sostenidas.

Idea: el guante es un "joystick de inclinación".
  - pitch (adelante/atrás)  -> W / S
  - roll  (izquierda/derecha) -> A / D
  - mano plana (dentro de la zona muerta) -> ninguna tecla
  - inclinaciones combinadas -> diagonales (W+D, etc.), salen gratis

Detalles de robustez:
  - Histéresis (glove_tilt_on / glove_tilt_off) para que la tecla no
    parpadee en el borde del umbral.
  - Watchdog: si el guante deja de enviar (toggle apagado, WiFi caído...),
    se sueltan TODAS las teclas para no dejar al personaje andando solo.
"""

from __future__ import annotations

import math
import time
from typing import Optional, Set

from gesture_engine import AppSettings
from key_mapper import hold_key, release_key
from wifi_receiver import ImuSample


def _accel_angles(ax: float, ay: float, az: float) -> tuple[float, float]:
    """Devuelve (pitch, roll) en grados a partir de la gravedad."""
    pitch = math.degrees(math.atan2(ax, math.hypot(ay, az)))
    roll = math.degrees(math.atan2(ay, math.hypot(ax, az)))
    return pitch, roll


class MovementController:
    def __init__(self, settings: AppSettings) -> None:
        self.s = settings
        self._pressed: Set[str] = set()
        self._pitch_dir: Optional[str] = None
        self._roll_dir: Optional[str] = None
        self._last_packet: float = 0.0

    def _axis_dir(
        self, angle: float, cur_dir: Optional[str], pos_key: str, neg_key: str
    ) -> Optional[str]:
        on, off = self.s.glove_tilt_on, self.s.glove_tilt_off
        if cur_dir == pos_key:
            return pos_key if angle > off else None
        if cur_dir == neg_key:
            return neg_key if angle < -off else None
        if angle > on:
            return pos_key
        if angle < -on:
            return neg_key
        return None

    def compute(self, sample: ImuSample) -> tuple[float, float, Set[str]]:
        """Calcula (pitch_efectivo, roll_efectivo, teclas deseadas) SIN pulsar.

        Actualiza el estado interno (direcciones e histéresis y el instante del
        último paquete), pero no toca el teclado. Útil para visualizar.
        """
        self._last_packet = time.time()

        pitch, roll = _accel_angles(sample.ax, sample.ay, sample.az)
        if self.s.glove_swap_axes:
            pitch, roll = roll, pitch
        if self.s.glove_invert_pitch:
            pitch = -pitch
        if self.s.glove_invert_roll:
            roll = -roll

        self._pitch_dir = self._axis_dir(
            pitch, self._pitch_dir, self.s.glove_key_forward, self.s.glove_key_back
        )
        self._roll_dir = self._axis_dir(
            roll, self._roll_dir, self.s.glove_key_right, self.s.glove_key_left
        )

        desired: Set[str] = set()
        if self._pitch_dir:
            desired.add(self._pitch_dir)
        if self._roll_dir:
            desired.add(self._roll_dir)
        return pitch, roll, desired

    def update(self, sample: ImuSample) -> Set[str]:
        """Procesa una muestra del guante y aplica las teclas. Devuelve el set activo."""
        _, _, desired = self.compute(sample)
        self._apply(desired)
        return desired

    def tick(self) -> None:
        """Llamar periódicamente: suelta las teclas si el guante enmudeció."""
        if not self._pressed:
            return
        if (time.time() - self._last_packet) * 1000.0 >= self.s.glove_watchdog_ms:
            self.release_all()

    def _apply(self, desired: Set[str]) -> None:
        for key in self._pressed - desired:
            release_key(key)
        for key in desired - self._pressed:
            hold_key(key)
        self._pressed = desired

    def release_all(self) -> None:
        for key in self._pressed:
            release_key(key)
        self._pressed = set()
        self._pitch_dir = None
        self._roll_dir = None
