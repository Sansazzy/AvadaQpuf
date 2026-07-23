"""Selecciona el receptor de datos del IMU según el transporte configurado.

Ambos receptores (BLE y UDP) comparten interfaz (context manager + samples()),
así que el resto de la app usa make_receiver() sin saber cuál es.
"""

from __future__ import annotations

from typing import List, Optional

from gesture_engine import AppSettings
from wifi_receiver import UdpImuReceiver


def make_receiver(
    settings: AppSettings,
    timeout: float = 1.0,
    verbose: bool = True,
    devices: Optional[List[str]] = None,
):
    """Crea el receptor.

    devices: lista opcional de ids a buscar ("wand", "glove").
             None = ambos. Para draw/camera usa ["wand"].
    """
    transport = getattr(settings, "transport", "udp")
    print(f"[transport] modo={transport}", flush=True)
    if transport == "ble":
        try:
            from ble_receiver import BleImuReceiver
        except ImportError as exc:
            raise SystemExit(
                "Falta la librería 'bleak' para el transporte BLE.\n"
                "Instálala con:  pip install bleak\n"
                "(o cambia \"transport\" a \"udp\" en config/spells.json)"
            ) from exc
        name_to_id = {
            settings.ble_wand_name: settings.wand_id,
            settings.ble_glove_name: settings.glove_id,
        }
        if devices is not None:
            name_to_id = {
                n: d for n, d in name_to_id.items() if d in devices
            }
        print(
            f"[transport] BLE buscando: {list(name_to_id.values())} "
            f"(nombres={list(name_to_id.keys())})",
            flush=True,
        )
        return BleImuReceiver(
            name_to_id=name_to_id,
            invert_button=settings.invert_button,
            timeout=timeout,
            verbose=verbose,
            device_ids=devices,
        )
    print(f"[transport] UDP puerto={settings.udp_port}", flush=True)
    return UdpImuReceiver(settings.udp_port, settings.invert_button, timeout=timeout)
