"""Escaneo BLE de diagnostico.

Uso:  python ble_scan.py
"""

from __future__ import annotations

import asyncio

from bleak import BleakScanner

from ble_receiver import DEVICE_PROFILES, _norm_uuid, _SERVICE_TO_ID


async def main() -> None:
    print("Escaneando BLE 8 s...\n")
    for did, p in DEVICE_PROFILES.items():
        print(f"  {did}: names={p['names']}")
        print(f"         service={p['service']}")
    print()

    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    print(f"Total: {len(found)} dispositivo(s)\n")
    if not found:
        print("Nada. Bluetooth ON + dispositivos encendidos + cerca.")
        return

    for addr, (dev, adv) in found.items():
        name = adv.local_name or dev.name or "(sin nombre)"
        uuids = list(adv.service_uuids or [])
        role = None
        for u in uuids:
            role = _SERVICE_TO_ID.get(_norm_uuid(u))
            if role:
                break
        if name in ("AQ-Wand", "AvadaQPuff-Wand"):
            role = role or "wand"
        if name in ("AQ-Glove", "AvadaQPuff-Glove"):
            role = role or "glove"
        mark = f" <<<< {role.upper()}" if role else ""
        print(f"[{addr}] '{name}' rssi={adv.rssi}{mark}")
        for u in uuids:
            print(f"    uuid: {u}")


if __name__ == "__main__":
    asyncio.run(main())
