"""Movimiento RELATIVO del ratón en Windows vía SendInput (ctypes).

Se usa movimiento relativo (MOUSEEVENTF_MOVE sin MOVE_ABSOLUTE) porque es lo
que los juegos leen para girar la cámara; SetCursorPos absoluto suele ser
ignorado por el motor del juego. No requiere instalar nada (ctypes es estándar).
"""

from __future__ import annotations

import ctypes

MOUSEEVENTF_MOVE = 0x0001
INPUT_MOUSE = 0

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def move_mouse(dx: int, dy: int) -> None:
    """Mueve el ratón de forma relativa (dx, dy) en píxeles."""
    if dx == 0 and dy == 0:
        return
    mi = _MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, 0)
    inp = _INPUT(INPUT_MOUSE, _INPUTUNION(mi=mi))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
