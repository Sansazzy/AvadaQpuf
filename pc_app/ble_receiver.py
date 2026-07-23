"""Recibe datos del IMU por Bluetooth LE desde varita y guante.

Identidad BLE (debe coincidir con los .ino C3):

  WAND:  name AQ-Wand
         service a0e1d000-1c3b-4a56-8901-abcdef000001
         char    a0e1d001-1c3b-4a56-8901-abcdef000001

  GLOVE: name AQ-Glove
         service a0e1d010-1c3b-4a56-8901-abcdef000001
         char    a0e1d011-1c3b-4a56-8901-abcdef000001

Match por nombre O por Service UUID (ya no se confunden: UUIDs distintos).
"""

from __future__ import annotations

import asyncio
import queue
import struct
import threading
import time
import traceback
from typing import Dict, Iterator, List, Optional, Set

from bleak import BleakClient, BleakScanner

from wifi_receiver import ImuSample

# Perfil por dispositivo: nombres + UUIDs propios.
DEVICE_PROFILES: Dict[str, dict] = {
    "wand": {
        "names": ["AQ-Wand", "AvadaQPuff-Wand"],
        "service": "a0e1d000-1c3b-4a56-8901-abcdef000001",
        "char": "a0e1d001-1c3b-4a56-8901-abcdef000001",
    },
    "glove": {
        "names": ["AQ-Glove", "AvadaQPuff-Glove"],
        "service": "a0e1d010-1c3b-4a56-8901-abcdef000001",
        "char": "a0e1d011-1c3b-4a56-8901-abcdef000001",
    },
}

_ACCEL_LSB = 8192.0
_GYRO_LSB = 65.5
_PACKET = struct.Struct("<hhhhhh")


def _norm_uuid(u: str) -> str:
    return str(u).lower().replace("-", "")


# service_uuid_norm -> device_id
_SERVICE_TO_ID = {
    _norm_uuid(p["service"]): did for did, p in DEVICE_PROFILES.items()
}


class BleImuReceiver:
    def __init__(
        self,
        name_to_id: Optional[Dict[str, str]] = None,
        invert_button: bool = False,
        timeout: float = 1.0,
        verbose: bool = True,
        device_ids: Optional[List[str]] = None,
    ) -> None:
        self.invert_button = invert_button
        self.timeout = timeout
        self.verbose = verbose

        # Qué device_ids buscamos
        wanted = list(device_ids) if device_ids is not None else list(DEVICE_PROFILES)
        # Compat: name_to_id puede restringir / ampliar nombres
        self.id_aliases: Dict[str, List[str]] = {}
        self.name_to_id: Dict[str, str] = {}
        for did in wanted:
            if did not in DEVICE_PROFILES:
                continue
            names = list(DEVICE_PROFILES[did]["names"])
            if name_to_id:
                for n, d in name_to_id.items():
                    if d == did and n not in names:
                        names.insert(0, n)
            self.id_aliases[did] = names
            for n in names:
                self.name_to_id[n] = did

        self._q: "queue.Queue[ImuSample]" = queue.Queue()
        self._stop = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._counts: Dict[str, int] = {did: 0 for did in self.id_aliases}
        self._connected: Set[str] = set()
        self._last_sample_t: Dict[str, float] = {}
        self._status_lock = threading.Lock()

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[BLE] {msg}", flush=True)

    def status(self) -> Dict[str, dict]:
        with self._status_lock:
            now = time.time()
            out = {}
            for did, aliases in self.id_aliases.items():
                last = self._last_sample_t.get(did, 0.0)
                out[did] = {
                    "name": aliases[0],
                    "connected": did in self._connected,
                    "packets": self._counts.get(did, 0),
                    "last_sample_age_s": (now - last) if last else None,
                }
            return out

    def __enter__(self) -> "BleImuReceiver":
        for did, aliases in self.id_aliases.items():
            p = DEVICE_PROFILES[did]
            self._log(
                f"Buscando {did}: names={aliases} "
                f"service={p['service']}"
            )
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="ble-rx"
        )
        self._thread.start()
        return self

    def __exit__(self, *_) -> None:
        self._log("Cerrando receptor BLE...")
        self._stop.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(lambda: None)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._log("Receptor BLE cerrado.")

    def samples(self, yield_timeouts: bool = False) -> Iterator[Optional[ImuSample]]:
        while True:
            try:
                yield self._q.get(timeout=self.timeout)
            except queue.Empty:
                if yield_timeouts:
                    yield None
                continue

    def _decode(self, data: bytes, device_id: str) -> Optional[ImuSample]:
        if len(data) < 14:
            self._log(
                f"{device_id}: paquete corto ({len(data)} bytes, esperados 14)"
            )
            return None
        ax, ay, az, gx, gy, gz = _PACKET.unpack_from(data, 0)
        btn = data[12]
        cam = data[13]
        if self.invert_button:
            btn = 0 if btn else 1
        return ImuSample(
            t=int(time.monotonic() * 1000.0),
            ax=ax / _ACCEL_LSB,
            ay=ay / _ACCEL_LSB,
            az=az / _ACCEL_LSB,
            gx=gx / _GYRO_LSB,
            gy=gy / _GYRO_LSB,
            gz=gz / _GYRO_LSB,
            btn=int(btn),
            cam=int(cam),
            device_id=device_id,
        )

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception:
            self._log("Error fatal en el loop BLE:")
            traceback.print_exc()
        finally:
            self._loop.close()

    def _match_device(self, device, adv) -> Optional[str]:
        """Identifica por nombre O por Service UUID (únicos por dispositivo)."""
        local = (getattr(adv, "local_name", None) or device.name or "") or ""
        if local in self.name_to_id:
            did = self.name_to_id[local]
            if did in self.id_aliases:
                return did

        for u in getattr(adv, "service_uuids", None) or []:
            did = _SERVICE_TO_ID.get(_norm_uuid(u))
            if did is not None and did in self.id_aliases:
                return did
        return None

    async def _main(self) -> None:
        tasks: Dict[str, asyncio.Task] = {}
        scan_n = 0
        while not self._stop.is_set():
            missing = [
                did
                for did in self.id_aliases
                if did not in tasks or tasks[did].done()
            ]
            if missing:
                scan_n += 1
                self._log(f"Escaneo #{scan_n}: buscando {missing}...")
                try:
                    found = await BleakScanner.discover(
                        timeout=5.0, return_adv=True
                    )
                except Exception as exc:
                    self._log(f"Error al escanear: {exc!r}")
                    traceback.print_exc()
                    found = {}

                self._log(f"Escaneo #{scan_n}: {len(found)} dispositivo(s)")
                matched: Dict[str, object] = {}
                for addr, (device, adv) in found.items():
                    local = (
                        getattr(adv, "local_name", None)
                        or device.name
                        or "(sin nombre)"
                    )
                    uuids = list(getattr(adv, "service_uuids", None) or [])
                    rssi = getattr(adv, "rssi", None)
                    role = None
                    for u in uuids:
                        role = _SERVICE_TO_ID.get(_norm_uuid(u))
                        if role:
                            break
                    tag = f" ★ {role.upper()}" if role else ""
                    self._log(
                        f"  · '{local}' addr={addr} rssi={rssi}{tag}"
                    )

                    did = self._match_device(device, adv)
                    if did is None:
                        continue
                    if did not in missing or did in matched:
                        if did not in self.id_aliases:
                            self._log(
                                f"  → '{local}' es {did}, "
                                f"pero este receptor no lo busca"
                            )
                        continue

                    how = "nombre" if local in self.name_to_id else "service UUID"
                    matched[did] = device
                    self._log(f"  → MATCH {did} por {how} ('{local}')")

                for did, device in matched.items():
                    self._log(
                        f"Conectando {did} @ {device.address}..."
                    )
                    tasks[did] = asyncio.create_task(
                        self._handle(device, did), name=f"ble-{did}"
                    )

                for did in missing:
                    if did not in matched:
                        p = DEVICE_PROFILES[did]
                        self._log(
                            f"NO encontrado {did} "
                            f"(names={self.id_aliases[did]} "
                            f"service={p['service']})"
                        )
            await asyncio.sleep(2.0)

        self._log("Deteniendo conexiones BLE...")
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)

    async def _handle(self, device, device_id: str) -> None:
        label = f"{device_id}@{device.address}"
        char_uuid = DEVICE_PROFILES[device_id]["char"]

        def callback(_sender, data: bytearray, did: str = device_id) -> None:
            sample = self._decode(bytes(data), did)
            if sample is None:
                return
            self._q.put(sample)
            with self._status_lock:
                self._counts[did] = self._counts.get(did, 0) + 1
                self._last_sample_t[did] = time.time()
                n = self._counts[did]
            if n == 1:
                self._log(
                    f"{did}: primer paquete OK "
                    f"(btn={sample.btn} cam={sample.cam} "
                    f"gyro=({sample.gx:.1f},{sample.gy:.1f},{sample.gz:.1f}))"
                )
            elif n % 200 == 0:
                self._log(f"{did}: {n} paquetes")

        try:
            self._log(f"{label}: abriendo BleakClient...")
            async with BleakClient(device, timeout=15.0) as client:
                with self._status_lock:
                    self._connected.add(device_id)
                self._log(f"{label}: CONECTADO")

                try:
                    for svc in client.services:
                        self._log(f"{label}: svc {svc.uuid}")
                        for ch in svc.characteristics:
                            self._log(
                                f"{label}:   char {ch.uuid} "
                                f"[{','.join(ch.properties)}]"
                            )
                except Exception as exc:
                    self._log(f"{label}: listar servicios: {exc!r}")

                self._log(f"{label}: NOTIFY {char_uuid}")
                await client.start_notify(char_uuid, callback)
                self._log(f"{label}: esperando IMU...")

                while not self._stop.is_set() and client.is_connected:
                    await asyncio.sleep(0.5)

                try:
                    await client.stop_notify(char_uuid)
                except Exception:
                    pass
        except Exception as exc:
            self._log(f"{label}: FALLO: {exc!r}")
            traceback.print_exc()
            await asyncio.sleep(2.0)
        finally:
            with self._status_lock:
                self._connected.discard(device_id)
            self._log(f"{label}: desconectado (reintento en escaneo).")
