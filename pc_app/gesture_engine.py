"""Convierte IMU en trayectoria 2D y detecta patrones guardados."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from wifi_receiver import ImuSample

Point = Tuple[float, float]
Trajectory = List[Point]


@dataclass
class Gesture:
    name: str
    key: str
    templates: List[Trajectory] = field(default_factory=list)
    threshold: float = 0.62


@dataclass
class AppSettings:
    udp_port: int = 4210
    motion_threshold: float = 8.0
    still_ms: int = 180
    match_threshold: float = 0.62
    confidence_margin: float = 0.07
    cooldown_ms: int = 450
    draw_scale: float = 6.0
    draw_clear_after_s: float = 5.0
    use_button: bool = True
    invert_button: bool = False
    record_samples: int = 3
    # Reducción de ruido y mapeo de ejes
    deadband: float = 2.0
    smoothing: float = 0.35          # 0..1: más bajo = más suave (más lag)
    gyro_still_threshold: float = 6.0  # °/s por debajo de esto se estima el bias
    gyro_bias_rate: float = 0.02       # velocidad de adaptación del bias
    invert_x: bool = True
    invert_y: bool = False
    swap_axes: bool = False
    # --- Transporte: "ble" (Bluetooth, sin red) o "udp" (WiFi) ---
    transport: str = "ble"
    ble_wand_name: str = "AvadaQPuff-Wand"
    ble_glove_name: str = "AvadaQPuff-Glove"
    # --- Identificadores de dispositivo (enrutado por "id") ---
    wand_id: str = "wand"
    glove_id: str = "glove"
    # --- Guante: inclinación (acelerómetro) -> WASD ---
    glove_key_forward: str = "w"
    glove_key_back: str = "s"
    glove_key_left: str = "a"
    glove_key_right: str = "d"
    glove_tilt_on: float = 22.0   # grados para ENGANCHAR una dirección
    glove_tilt_off: float = 14.0  # grados para SOLTARLA (histéresis anti-parpadeo)
    glove_invert_pitch: bool = False  # adelante/atrás (W/S)
    glove_invert_roll: bool = False   # izquierda/derecha (A/D)
    glove_swap_axes: bool = False     # intercambia pitch<->roll según el montaje
    glove_watchdog_ms: int = 150      # sin paquetes del guante -> soltar teclas
    # --- Cámara (varita, air-mouse relativo horizontal) ---
    camera_enabled: bool = True
    camera_sensitivity: float = 8.0   # píxeles de ratón por grado girado
    camera_deadband: float = 3.0      # °/s por debajo de esto no mueve la cámara
    camera_smoothing: float = 0.5     # 0..1: más bajo = más suave (más lag)
    camera_grace_ms: int = 200        # espera tras soltar el botón antes de reanudar
    camera_invert: bool = False       # invierte el sentido horizontal


def _parse_templates(item: dict) -> List[Trajectory]:
    """Soporta el formato nuevo (templates) y el antiguo (template)."""
    if "templates" in item:
        return [[tuple(p) for p in tpl] for tpl in item["templates"]]
    if "template" in item:
        return [[tuple(p) for p in item["template"]]]
    return []


@dataclass
class GestureStore:
    gestures: List[Gesture] = field(default_factory=list)
    settings: AppSettings = field(default_factory=AppSettings)

    @classmethod
    def load(cls, path: Path) -> "GestureStore":
        raw = json.loads(path.read_text(encoding="utf-8"))
        settings = AppSettings(**raw.get("settings", {}))
        gestures = [
            Gesture(
                name=item["name"],
                key=item["key"],
                templates=_parse_templates(item),
                threshold=item.get("threshold", settings.match_threshold),
            )
            for item in raw.get("gestures", [])
        ]
        return cls(gestures=gestures, settings=settings)

    def save(self, path: Path) -> None:
        payload = {
            "gestures": [
                {
                    "name": g.name,
                    "key": g.key,
                    "templates": g.templates,
                    "threshold": g.threshold,
                }
                for g in self.gestures
            ],
            "settings": self.settings.__dict__,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class MotionTracker:
    """Integra el giroscopio en un plano 2D con reducción de ruido.

    Cadena de proceso por muestra:
      1. Filtro paso bajo (EMA) para quitar jitter.
      2. Resta del bias (offset del giroscopio en reposo), estimado solo
         cuando la varita está casi quieta.
      3. Deadband para ignorar micro-movimientos.
      4. Mapeo de ejes: yaw (gz) -> X, pitch (gy) -> Y, con inversión/swap
         configurables (para corregir el efecto espejo).
      5. Integración: ángulo acumulado = velocidad * dt.

    El bias y el filtro se conservan entre gestos (reset() no los borra).
    """

    def __init__(self, settings: AppSettings) -> None:
        self.s = settings
        self.x = 0.0
        self.y = 0.0
        self._last_t: Optional[int] = None
        self._lp_gy = 0.0
        self._lp_gz = 0.0
        self._bias_gy = 0.0
        self._bias_gz = 0.0

    def reset(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self._last_t = None

    def update(self, sample: ImuSample) -> Point:
        if self._last_t is None:
            self._last_t = sample.t
            self._lp_gy = sample.gy
            self._lp_gz = sample.gz
            return self.x, self.y

        dt = max((sample.t - self._last_t) / 1000.0, 0.001)
        self._last_t = sample.t

        a = self.s.smoothing
        self._lp_gy += a * (sample.gy - self._lp_gy)
        self._lp_gz += a * (sample.gz - self._lp_gz)

        gy_c = self._lp_gy - self._bias_gy
        gz_c = self._lp_gz - self._bias_gz

        if math.hypot(gy_c, gz_c) < self.s.gyro_still_threshold:
            r = self.s.gyro_bias_rate
            self._bias_gy += r * (self._lp_gy - self._bias_gy)
            self._bias_gz += r * (self._lp_gz - self._bias_gz)
            gy_c = self._lp_gy - self._bias_gy
            gz_c = self._lp_gz - self._bias_gz

        db = self.s.deadband
        gy_c = gy_c if abs(gy_c) > db else 0.0
        gz_c = gz_c if abs(gz_c) > db else 0.0

        vx, vy = gz_c, gy_c
        if self.s.swap_axes:
            vx, vy = vy, vx
        if self.s.invert_x:
            vx = -vx
        if self.s.invert_y:
            vy = -vy

        self.x += vx * dt
        self.y += vy * dt
        return self.x, self.y


# --- Reconocedor tipo $1 (Wobbrock et al.) adaptado ---
# Remuestrea a N puntos equidistantes, centra y escala; compara con una
# búsqueda de rotación ACOTADA (no invariante total) para tolerar pequeñas
# variaciones de orientación sin confundir "arriba" con "abajo".

N_RESAMPLE = 64
_PHI = 0.5 * (-1.0 + math.sqrt(5.0))
_ANGLE_RANGE = math.radians(25.0)
_ANGLE_PRECISION = math.radians(2.0)
_HALF_DIAGONAL = 0.5 * math.sqrt(2.0)


def _resample(points: Trajectory, n: int = N_RESAMPLE) -> Optional[np.ndarray]:
    if len(points) < 2:
        return None
    arr = np.asarray(points, dtype=float)
    seg = np.linalg.norm(np.diff(arr, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(seg)])
    total = cumulative[-1]
    if total < 1e-6:
        return None
    targets = np.linspace(0.0, total, n)
    out = np.zeros((n, 2))
    for i, t in enumerate(targets):
        idx = np.searchsorted(cumulative, t)
        idx = min(max(idx, 1), len(arr) - 1)
        t0, t1 = cumulative[idx - 1], cumulative[idx]
        alpha = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
        out[i] = arr[idx - 1] * (1 - alpha) + arr[idx] * alpha
    return out


def _preprocess(points: Trajectory) -> Optional[np.ndarray]:
    """Remuestrea -> centra en el centroide -> escala uniforme a caja unidad."""
    arr = _resample(points)
    if arr is None:
        return None
    arr = arr - arr.mean(axis=0)  # centrar en centroide
    span = np.max(arr.max(axis=0) - arr.min(axis=0))
    if span < 1e-6:
        return None
    arr /= span  # escala uniforme (conserva la relación de aspecto)
    return arr


def _rotate(points: np.ndarray, theta: float) -> np.ndarray:
    c, s = math.cos(theta), math.sin(theta)
    rot = np.array([[c, -s], [s, c]])
    return points @ rot.T


def _path_distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.linalg.norm(a - b, axis=1)))


def _distance_at_best_angle(candidate: np.ndarray, template: np.ndarray) -> float:
    """Búsqueda de sección áurea de la rotación óptima dentro de ±rango."""
    a, b = -_ANGLE_RANGE, _ANGLE_RANGE
    x1 = _PHI * a + (1 - _PHI) * b
    x2 = (1 - _PHI) * a + _PHI * b
    f1 = _path_distance(_rotate(candidate, x1), template)
    f2 = _path_distance(_rotate(candidate, x2), template)
    while abs(b - a) > _ANGLE_PRECISION:
        if f1 < f2:
            b, x2, f2 = x2, x1, f1
            x1 = _PHI * a + (1 - _PHI) * b
            f1 = _path_distance(_rotate(candidate, x1), template)
        else:
            a, x1, f1 = x1, x2, f2
            x2 = (1 - _PHI) * a + _PHI * b
            f2 = _path_distance(_rotate(candidate, x2), template)
    return min(f1, f2)


def trajectory_similarity(a: Trajectory, b: Trajectory) -> float:
    pa = _preprocess(a)
    pb = _preprocess(b)
    if pa is None or pb is None:
        return 0.0
    dist = _distance_at_best_angle(pa, pb)
    return float(max(0.0, 1.0 - dist / _HALF_DIAGONAL))


class GestureRecorder:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.tracker = MotionTracker(settings)
        self.buffer: Trajectory = []
        self.recording = False
        self._still_since: Optional[float] = None

    def feed(self, sample: ImuSample) -> Optional[Trajectory]:
        point = self.tracker.update(sample)
        speed = math.hypot(sample.gx, sample.gy, sample.gz)

        if not self.recording:
            if speed >= self.settings.motion_threshold:
                self.recording = True
                self.buffer = [point]
                self._still_since = None
            return None

        self.buffer.append(point)

        if speed < self.settings.motion_threshold:
            if self._still_since is None:
                self._still_since = time.time()
            elif (time.time() - self._still_since) * 1000 >= self.settings.still_ms:
                finished = self.buffer.copy()
                self.recording = False
                self.buffer = []
                self.tracker.reset()
                self._still_since = None
                return finished
        else:
            self._still_since = None

        return None


class ButtonGestureRecorder:
    """Delimita el gesto con una señal externa (botón de la varita o tecla).

    `active` es True mientras se hace el gesto; al pasar a False se cierra
    y se devuelve la trayectoria capturada (empezando en el centro).
    """

    def __init__(self, settings: AppSettings) -> None:
        self.tracker = MotionTracker(settings)
        self.buffer: Trajectory = []
        self.recording = False
        self._prev_active = False

    def feed(self, sample: ImuSample, active: bool) -> Optional[Trajectory]:
        started = active and not self._prev_active
        stopped = (not active) and self._prev_active
        self._prev_active = active

        if started:
            self.tracker.reset()
            self.buffer = []
            self.recording = True

        point = self.tracker.update(sample)

        if self.recording and active:
            self.buffer.append(point)

        if stopped and self.recording:
            self.recording = False
            result = self.buffer.copy()
            self.buffer = []
            return result
        return None


def best_match(
    trajectory: Trajectory, gestures: List[Gesture]
) -> Tuple[Optional[Gesture], float, float]:
    """Devuelve (mejor gesto, su puntuación, puntuación del 2º mejor).

    El 2º mejor sirve para medir el "margen de confianza": si dos gestos
    puntúan casi igual, la detección es ambigua y conviene rechazarla.
    """
    scored = []
    for gesture in gestures:
        score = max(
            (trajectory_similarity(trajectory, tpl) for tpl in gesture.templates),
            default=0.0,
        )
        scored.append((score, gesture))

    if not scored:
        return None, 0.0, 0.0

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    return best, best_score, second_score


class GestureMatcher:
    def __init__(self, store: GestureStore) -> None:
        self.store = store
        self.use_button = store.settings.use_button
        self.button_recorder = ButtonGestureRecorder(store.settings)
        self.motion_recorder = GestureRecorder(store.settings)
        self._last_cast = 0.0

    def feed(self, sample: ImuSample) -> Optional[Gesture]:
        if self.use_button:
            trajectory = self.button_recorder.feed(sample, bool(sample.btn))
        else:
            trajectory = self.motion_recorder.feed(sample)

        if not trajectory or len(trajectory) < 5:
            return None

        now = time.time() * 1000
        if now - self._last_cast < self.store.settings.cooldown_ms:
            return None

        gesture, score, second = best_match(trajectory, self.store.gestures)
        if (
            gesture
            and score >= gesture.threshold
            and (score - second) >= self.store.settings.confidence_margin
        ):
            self._last_cast = now
            return gesture
        return None
