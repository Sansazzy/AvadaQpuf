"""AvadaQPuff - CLI para monitorizar, grabar y lanzar gestos."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from gesture_engine import Gesture, GestureMatcher, GestureRecorder, GestureStore
from key_mapper import press_key
from transport import make_receiver

CONFIG_PATH = Path(__file__).parent / "config" / "spells.json"


def cmd_monitor(store: GestureStore) -> None:
    from gesture_engine import MotionTracker

    print(f"Escuchando UDP puerto {store.settings.udp_port}...")
    print("Mueve la varita y pulsa el botón para verificar. Ctrl+C para salir.\n")

    tracker = MotionTracker(store.settings)
    count = 0
    with make_receiver(store.settings, devices=["wand"]) as rx:
        try:
            for sample in rx.samples():
                if sample.device_id != store.settings.wand_id:
                    continue
                count += 1
                x, y = tracker.update(sample)
                btn = "●PULSADO" if sample.btn else "○ libre "
                print(
                    f"#{count:6d}  BOTON:{btn}  pos=({x:+7.2f},{y:+7.2f})  "
                    f"gyro=({sample.gx:+7.1f},{sample.gy:+7.1f},{sample.gz:+7.1f})",
                    end="\r",
                )
        except KeyboardInterrupt:
            print(f"\nFin monitor. Paquetes recibidos: {count}")
            if count == 0:
                print(
                    "No llegó ningún paquete. Revisa PC_IP, el puerto y el "
                    "firewall de Windows (permite Python en redes privadas)."
                )


def cmd_record(store: GestureStore, name: str, key: str) -> None:
    print(f"Grabando gesto '{name}' → tecla '{key}'")
    print("Haz el movimiento y quédate quieto al terminar.\n")

    recorder = GestureRecorder(store.settings)
    with make_receiver(store.settings, devices=["wand"]) as rx:
        try:
            for sample in rx.samples():
                if sample.device_id != store.settings.wand_id:
                    continue
                trajectory = recorder.feed(sample)
                if trajectory:
                    new_gesture = Gesture(name=name, key=key, templates=[trajectory])
                    # Reemplaza si ya existe un gesto con el mismo nombre
                    for i, g in enumerate(store.gestures):
                        if g.name == name:
                            store.gestures[i] = new_gesture
                            break
                    else:
                        store.gestures.append(new_gesture)
                    store.save(CONFIG_PATH)
                    print(f"\nGesto guardado ({len(trajectory)} puntos).")
                    return
                print("... grabando", end="\r")
        except KeyboardInterrupt:
            print("\nGrabación cancelada.")


def cmd_draw(store: GestureStore) -> None:
    from draw_canvas import run_canvas

    s = store.settings
    print("Abriendo estudio 720x720.")
    print("Dibuja moviendo la varita; usa 'Grabar hechizo' para registrar gestos.")
    print(f"[draw] transporte={getattr(s, 'transport', 'udp')}")
    if getattr(s, "transport", "udp") == "ble":
        print(
            f"[draw] Esperando BLE '{s.ble_wand_name}'. "
            "Mira los logs [BLE] de escaneo/conexión."
        )
    run_canvas(store, CONFIG_PATH)


def cmd_glove_draw(store: GestureStore) -> None:
    from glove_canvas import run_glove_canvas

    print("Abriendo visor del guante.")
    print("Activa el toggle del guante e inclínalo para ver la señal.")
    run_glove_canvas(store)


def cmd_glove(store: GestureStore) -> None:
    from movement_controller import MovementController, _accel_angles

    s = store.settings
    print(f"Modo GUANTE activo (id='{s.glove_id}'). Ctrl+C para salir.")
    print(
        f"Inclina el guante: adelante/atrás → {s.glove_key_forward}/{s.glove_key_back}, "
        f"izq/der → {s.glove_key_left}/{s.glove_key_right}."
    )
    print("Activa el toggle del guante (LED encendido) para que envíe.\n")

    controller = MovementController(s)
    # Timeout corto para que el watchdog reaccione aunque no lleguen paquetes.
    with make_receiver(s, timeout=0.05, devices=["glove"]) as rx:
        try:
            for sample in rx.samples(yield_timeouts=True):
                if sample is None or sample.device_id != s.glove_id:
                    controller.tick()
                    continue

                keys = controller.update(sample)
                pitch, roll = _accel_angles(sample.ax, sample.ay, sample.az)
                held = "+".join(sorted(keys)) if keys else "-----"
                print(
                    f"pitch={pitch:+6.1f}°  roll={roll:+6.1f}°  teclas: {held:9s}",
                    end="\r",
                )
        except KeyboardInterrupt:
            controller.release_all()
            print("\nFin modo guante.")


def cmd_camera(store: GestureStore) -> None:
    from camera_controller import CameraController

    s = store.settings
    print("Modo CÁMARA (varita). Ctrl+C para salir.")
    print(
        "Con el botón SUELTO, gira la muñeca (yaw) para mover la cámara.\n"
        "Con el botón PULSADO la cámara se congela (para dibujar hechizos).\n"
    )

    cam = CameraController(s)
    with make_receiver(s, devices=["wand"]) as rx:
        try:
            for sample in rx.samples():
                if sample.device_id != s.wand_id:
                    continue
                dx = cam.update(sample)
                if not sample.cam:
                    estado = "PAUSA (embrague)"
                elif sample.btn:
                    estado = "CONGELADA (botón)"
                else:
                    estado = "activa          "
                print(
                    f"cámara: {estado}  yaw(gz)={sample.gz:+7.1f}°/s  dx={dx:+5.0f}px",
                    end="\r",
                )
        except KeyboardInterrupt:
            print("\nFin modo cámara.")


def cmd_cast(store: GestureStore) -> None:
    if not store.gestures:
        print("No hay gestos en spells.json. Usa: python main.py record --name X --key Y")
        sys.exit(1)

    print("Modo hechizos activo. Ctrl+C para salir.")
    for g in store.gestures:
        print(f"  - {g.name} → {g.key}")

    matcher = GestureMatcher(store)
    with make_receiver(store.settings, devices=["wand"]) as rx:
        try:
            for sample in rx.samples():
                if sample.device_id != store.settings.wand_id:
                    continue
                match = matcher.feed(sample)
                if match:
                    print(f"\n✦ {match.name} → tecla '{match.key}'")
                    press_key(match.key)
        except KeyboardInterrupt:
            print("\nFin modo hechizos.")


def cmd_play(store: GestureStore) -> None:
    """Todo activo a la vez: varita (cámara + hechizos) y guante (WASD)."""
    from camera_controller import CameraController
    from movement_controller import MovementController

    s = store.settings
    print("=== AvadaQPuff: TODO ACTIVO === (Ctrl+C para salir)\n")
    print(f"Transporte: {getattr(s, 'transport', 'udp')}")
    if getattr(s, "transport", "udp") == "ble":
        print(
            f"  BLE wand='{s.ble_wand_name}'  glove='{s.ble_glove_name}'\n"
            "  Enciende los dispositivos y mira los logs [BLE] abajo.\n"
        )
    print(f"Varita (id='{s.wand_id}'):")
    print("  · botón SUELTO  → cámara (gira la muñeca)")
    print("  · botón PULSADO → dibujas un hechizo (cámara congelada)")
    if store.gestures:
        for g in store.gestures:
            print(f"      - {g.name} → tecla '{g.key}'")
    else:
        print("      (sin hechizos guardados; usa 'draw' para grabar)")
    print(f"Guante (id='{s.glove_id}'):")
    print(
        f"  · inclínalo → {s.glove_key_forward}/{s.glove_key_back}/"
        f"{s.glove_key_left}/{s.glove_key_right} (toggle ON para enviar)\n"
    )

    matcher = GestureMatcher(store)
    cam = CameraController(s)
    move = MovementController(s)

    last_status_print = 0.0
    wand_packets = 0
    glove_packets = 0

    # Timeout corto para que el watchdog del guante reaccione aunque no lleguen paquetes.
    with make_receiver(s, timeout=0.05) as rx:
        try:
            for sample in rx.samples(yield_timeouts=True):
                now = time.time()
                if sample is None:
                    move.tick()
                    # Cada 3 s, resume el estado BLE / paquetes.
                    if now - last_status_print >= 3.0:
                        last_status_print = now
                        st = getattr(rx, "status", lambda: {})()
                        if st:
                            parts = []
                            for did, info in st.items():
                                age = info["last_sample_age_s"]
                                age_s = f"{age:.1f}s" if age is not None else "—"
                                parts.append(
                                    f"{did}:{'OK' if info['connected'] else 'NO'} "
                                    f"pk={info['packets']} age={age_s}"
                                )
                            print("[play] " + " | ".join(parts), flush=True)
                        else:
                            print(
                                f"[play] esperando datos... "
                                f"wand_pk={wand_packets} glove_pk={glove_packets}",
                                flush=True,
                            )
                    continue

                if sample.device_id == s.glove_id:
                    glove_packets += 1
                    if glove_packets == 1:
                        print("[play] ✓ primer paquete del GUANTE", flush=True)
                    move.update(sample)
                elif sample.device_id == s.wand_id:
                    wand_packets += 1
                    if wand_packets == 1:
                        print(
                            f"[play] ✓ primer paquete de la VARITA "
                            f"(btn={sample.btn} cam={sample.cam})",
                            flush=True,
                        )
                    cam.update(sample)
                    match = matcher.feed(sample)
                    if match:
                        print(f"✦ {match.name} → tecla '{match.key}'")
                        press_key(match.key)

                move.tick()
        except KeyboardInterrupt:
            move.release_all()
            print(
                f"\nFin. Paquetes: wand={wand_packets} glove={glove_packets}. "
                "Teclas liberadas."
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="AvadaQPuff")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("monitor", help="Ver posición en tiempo real")
    sub.add_parser("draw", help="Lienzo 2D que pinta el movimiento")

    rec = sub.add_parser("record", help="Grabar un gesto")
    rec.add_argument("--name", required=True, help="Nombre del hechizo")
    rec.add_argument("--key", required=True, help="Tecla a simular (ej: 1, q, f)")

    sub.add_parser("cast", help="Detectar gestos y pulsar teclas")
    sub.add_parser("glove", help="Guante: inclinación → WASD (prueba Fase 2)")
    sub.add_parser("glove-draw", help="Visor visual del guante (verificar señal)")
    sub.add_parser("camera", help="Cámara air-mouse con la varita (prueba Fase 3)")
    sub.add_parser("play", help="TODO activo: varita (cámara+hechizos) y guante (WASD)")

    args = parser.parse_args()
    store = GestureStore.load(CONFIG_PATH)

    if args.command == "monitor":
        cmd_monitor(store)
    elif args.command == "draw":
        cmd_draw(store)
    elif args.command == "record":
        cmd_record(store, args.name, args.key)
    elif args.command == "cast":
        cmd_cast(store)
    elif args.command == "glove":
        cmd_glove(store)
    elif args.command == "glove-draw":
        cmd_glove_draw(store)
    elif args.command == "camera":
        cmd_camera(store)
    elif args.command == "play":
        cmd_play(store)


if __name__ == "__main__":
    main()
