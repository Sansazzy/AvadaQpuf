"""AvadaQPuff - CLI para monitorizar, grabar y lanzar gestos."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gesture_engine import Gesture, GestureMatcher, GestureRecorder, GestureStore
from key_mapper import press_key
from wifi_receiver import UdpImuReceiver

CONFIG_PATH = Path(__file__).parent / "config" / "spells.json"


def cmd_monitor(store: GestureStore) -> None:
    from gesture_engine import MotionTracker

    print(f"Escuchando UDP puerto {store.settings.udp_port}...")
    print("Mueve la varita y pulsa el botón para verificar. Ctrl+C para salir.\n")

    tracker = MotionTracker(store.settings)
    count = 0
    with UdpImuReceiver(store.settings.udp_port, store.settings.invert_button) as rx:
        try:
            for sample in rx.samples():
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
    with UdpImuReceiver(store.settings.udp_port, store.settings.invert_button) as rx:
        try:
            for sample in rx.samples():
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

    print("Abriendo estudio 720x720.")
    print("Dibuja moviendo la varita; usa 'Grabar hechizo' para registrar gestos.")
    run_canvas(store, CONFIG_PATH)


def cmd_cast(store: GestureStore) -> None:
    if not store.gestures:
        print("No hay gestos en spells.json. Usa: python main.py record --name X --key Y")
        sys.exit(1)

    print("Modo hechizos activo. Ctrl+C para salir.")
    for g in store.gestures:
        print(f"  - {g.name} → {g.key}")

    matcher = GestureMatcher(store)
    with UdpImuReceiver(store.settings.udp_port, store.settings.invert_button) as rx:
        try:
            for sample in rx.samples():
                match = matcher.feed(sample)
                if match:
                    print(f"\n✦ {match.name} → tecla '{match.key}'")
                    press_key(match.key)
        except KeyboardInterrupt:
            print("\nFin modo hechizos.")


def main() -> None:
    parser = argparse.ArgumentParser(description="AvadaQPuff")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("monitor", help="Ver posición en tiempo real")
    sub.add_parser("draw", help="Lienzo 2D que pinta el movimiento")

    rec = sub.add_parser("record", help="Grabar un gesto")
    rec.add_argument("--name", required=True, help="Nombre del hechizo")
    rec.add_argument("--key", required=True, help="Tecla a simular (ej: 1, q, f)")

    sub.add_parser("cast", help="Detectar gestos y pulsar teclas")

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


if __name__ == "__main__":
    main()
