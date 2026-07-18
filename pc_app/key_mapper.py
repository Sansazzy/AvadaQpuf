"""Emula pulsaciones de teclado en Windows."""

from __future__ import annotations

import time

import keyboard


def press_key(key: str, hold_s: float = 0.05) -> None:
    """Pulsa una tecla manteniéndola un instante.

    Un press_and_release de 0 ms a veces lo ignoran los juegos, por eso
    se mantiene pulsada ~50 ms antes de soltar.
    """
    keyboard.press(key)
    time.sleep(hold_s)
    keyboard.release(key)


def hold_key(key: str) -> None:
    """Mantiene una tecla pulsada (sin soltarla). Para movimiento sostenido."""
    keyboard.press(key)


def release_key(key: str) -> None:
    """Suelta una tecla previamente mantenida con hold_key."""
    keyboard.release(key)
