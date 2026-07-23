"""Recibe paquetes UDP del ESP32 con datos del MPU6050."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass
class ImuSample:
    t: int
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    btn: int = 0
    cam: int = 1
    device_id: str = "wand"


class UdpImuReceiver:
    def __init__(
        self, port: int = 4210, invert_button: bool = False, timeout: float = 1.0
    ) -> None:
        self.port = port
        self.invert_button = invert_button
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None

    def __enter__(self) -> "UdpImuReceiver":
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("0.0.0.0", self.port))
        self._sock.settimeout(self.timeout)
        return self

    def __exit__(self, *_) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def samples(self, yield_timeouts: bool = False) -> Iterator[Optional[ImuSample]]:
        """Itera muestras del socket.

        Si yield_timeouts es True, emite None cuando pasa el timeout sin
        recibir nada (útil para ejecutar watchdogs periódicos).
        """
        if not self._sock:
            raise RuntimeError("Receiver no iniciado")

        while True:
            try:
                data, _ = self._sock.recvfrom(1024)
            except TimeoutError:
                if yield_timeouts:
                    yield None
                continue

            try:
                payload = json.loads(data.decode("utf-8"))
                btn = int(payload.get("btn", 0))
                if self.invert_button:
                    btn = 0 if btn else 1
                yield ImuSample(
                    t=int(payload["t"]),
                    ax=float(payload["ax"]),
                    ay=float(payload["ay"]),
                    az=float(payload["az"]),
                    gx=float(payload["gx"]),
                    gy=float(payload["gy"]),
                    gz=float(payload["gz"]),
                    btn=btn,
                    cam=int(payload.get("cam", 1)),
                    device_id=str(payload.get("id", "wand")),
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
