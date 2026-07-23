"""Analiza y dibuja las trayectorias guardadas en spells.json.

Genera:
  - analyze_out.txt : resumen + render ASCII de cada muestra
  - shapes_<name>.png : imagen con las muestras superpuestas (si hay matplotlib)
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG = Path(__file__).parent / "config" / "spells.json"
OUT = Path(__file__).parent / "analyze_out.txt"


def ascii_plot(points, w=60, h=24):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    dx = (maxx - minx) or 1.0
    dy = (maxy - miny) or 1.0
    grid = [[" "] * w for _ in range(h)]
    for i, (x, y) in enumerate(points):
        col = int((x - minx) / dx * (w - 1))
        row = int((y - miny) / dy * (h - 1))  # y hacia abajo = fila mayor
        ch = "*"
        if i == 0:
            ch = "S"
        elif i == len(points) - 1:
            ch = "E"
        grid[row][col] = ch
    return "\n".join("".join(r) for r in grid)


def describe(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (
        f"puntos={len(points)}  "
        f"x=[{min(xs):.1f},{max(xs):.1f}] rango {max(xs)-min(xs):.1f}  "
        f"y=[{min(ys):.1f},{max(ys):.1f}] rango {max(ys)-min(ys):.1f}  "
        f"inicio=({points[0][0]:.1f},{points[0][1]:.1f}) "
        f"fin=({points[-1][0]:.1f},{points[-1][1]:.1f})"
    )


def main():
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    lines = []
    for g in data.get("gestures", []):
        lines.append("=" * 64)
        lines.append(f"GESTO '{g['name']}'  -> tecla '{g['key']}'")
        for ti, tpl in enumerate(g.get("templates", [])):
            lines.append("-" * 64)
            lines.append(f"  muestra {ti + 1}: {describe(tpl)}")
            lines.append(ascii_plot(tpl))
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Escrito {OUT}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        for g in data.get("gestures", []):
            plt.figure(figsize=(5, 5))
            for ti, tpl in enumerate(g.get("templates", [])):
                xs = [p[0] for p in tpl]
                ys = [p[1] for p in tpl]
                plt.plot(xs, ys, marker=".", label=f"muestra {ti + 1}")
                plt.scatter([xs[0]], [ys[0]], c="green", s=60, zorder=5)
                plt.scatter([xs[-1]], [ys[-1]], c="red", s=60, zorder=5)
            plt.gca().invert_yaxis()  # y hacia abajo, como el lienzo
            plt.gca().set_aspect("equal", "box")
            plt.title(f"Gesto '{g['name']}'")
            plt.legend()
            out_png = Path(__file__).parent / f"shapes_{g['name']}.png"
            plt.savefig(out_png, dpi=90, bbox_inches="tight")
            plt.close()
            print(f"Escrito {out_png}")
    except Exception as exc:  # noqa: BLE001
        print(f"(sin imagen: {exc})")


if __name__ == "__main__":
    main()
