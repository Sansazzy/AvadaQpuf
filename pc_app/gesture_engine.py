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
    template: Trajectory
    threshold: float = 0.62


@dataclass
class AppSettings:
    udp_port: int = 4210
    motion_threshold: float = 8.0
    still_ms: int = 180
    match_threshold: float = 0.62
    cooldown_ms: int = 450
    draw_scale: float = 6.0
    draw_clear_after_s: float = 5.0


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
                template=[tuple(p) for p in item["template"]],
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
                    "template": g.template,
                    "threshold": g.threshold,
                }
                for g in self.gestures
            ],
            "settings": self.settings.__dict__,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class MotionTracker:
    """Integra el giroscopio en un plano 2D.

    Con la varita apuntando al frente:
      - yaw (gz)   -> eje X (mover la punta a izquierda/derecha)
      - pitch (gy) -> eje Y (mover la punta arriba/abajo)
    Si los ejes salen invertidos, cambia el signo o intercambia gy/gz.
    """

    def __init__(self, deadband: float = 1.5) -> None:
        self.deadband = deadband
        self.x = 0.0
        self.y = 0.0
        self._last_t: Optional[int] = None

    def reset(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self._last_t = None

    def update(self, sample: ImuSample) -> Point:
        if self._last_t is None:
            self._last_t = sample.t
            return self.x, self.y

        dt = max((sample.t - self._last_t) / 1000.0, 0.001)
        self._last_t = sample.t

        gz = sample.gz if abs(sample.gz) > self.deadband else 0.0
        gy = sample.gy if abs(sample.gy) > self.deadband else 0.0

        self.x += gz * dt
        self.y += gy * dt
        return self.x, self.y


def normalize_trajectory(points: Trajectory, target_len: int = 48) -> np.ndarray:
    if len(points) < 2:
        return np.zeros((target_len, 2))

    arr = np.array(points, dtype=float)
    arr -= arr[0]

    span = np.max(np.abs(arr))
    if span > 1e-6:
        arr /= span

    # Remuestreo uniforme por longitud de arco
    seg = np.linalg.norm(np.diff(arr, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(seg)])
    total = cumulative[-1]
    if total < 1e-6:
        return np.zeros((target_len, 2))

    targets = np.linspace(0.0, total, target_len)
    resampled = np.zeros((target_len, 2))
    for i, t in enumerate(targets):
        idx = np.searchsorted(cumulative, t)
        idx = min(max(idx, 1), len(arr) - 1)
        t0, t1 = cumulative[idx - 1], cumulative[idx]
        alpha = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
        resampled[i] = arr[idx - 1] * (1 - alpha) + arr[idx] * alpha
    return resampled


def trajectory_similarity(a: Trajectory, b: Trajectory) -> float:
    na = normalize_trajectory(a)
    nb = normalize_trajectory(b)
    dist = np.mean(np.linalg.norm(na - nb, axis=1))
    return float(max(0.0, 1.0 - dist / 0.75))


class GestureRecorder:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.tracker = MotionTracker()
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


class GestureMatcher:
    def __init__(self, store: GestureStore) -> None:
        self.store = store
        self.recorder = GestureRecorder(store.settings)
        self._last_cast = 0.0

    def feed(self, sample: ImuSample) -> Optional[Gesture]:
        trajectory = self.recorder.feed(sample)
        if not trajectory or len(trajectory) < 5:
            return None

        now = time.time() * 1000
        if now - self._last_cast < self.store.settings.cooldown_ms:
            return None

        best: Optional[Gesture] = None
        best_score = 0.0
        for gesture in self.store.gestures:
            score = trajectory_similarity(trajectory, gesture.template)
            if score >= gesture.threshold and score > best_score:
                best = gesture
                best_score = score

        if best:
            self._last_cast = now
        return best
