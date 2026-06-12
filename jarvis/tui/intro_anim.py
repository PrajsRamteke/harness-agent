"""Shine-sweep animation frames for the welcome art (TUI startup).

A bright diagonal band sweeps left→right across the block-letter banner
once, then the app settles into the normal static welcome banner. Frames
are plain Rich ``Text`` objects — the driver in ``app.py`` clears and
rewrites the transcript RichLog per frame during the brief startup window
where nothing else prints.
"""
from __future__ import annotations

from rich.color import Color
from rich.text import Text


def _blend(base: str, target: str, t: float) -> str:
    """Mix *base* toward *target* by t (0..1). Falls back to base on parse errors."""
    try:
        b = Color.parse(base).get_truecolor()
        g = Color.parse(target).get_truecolor()
    except Exception:
        return base
    return "#{:02x}{:02x}{:02x}".format(
        round(b[0] + (g[0] - b[0]) * t),
        round(b[1] + (g[1] - b[1]) * t),
        round(b[2] + (g[2] - b[2]) * t),
    )


def sweep_centers(art: str, step: int = 7, lead: int = 8, tail: int = 16) -> list[float]:
    """Band-center positions for one full left→right sweep across *art*."""
    width = max((len(line) for line in art.split("\n")), default=0)
    return [float(x) for x in range(-lead, width + tail, step)]


def shine_frame(art: str, center: float, base_color: str, glow: str = "#ffffff") -> Text:
    """One frame: *art* in *base_color* with a bright diagonal band at *center*.

    ``frame.plain`` is always exactly *art*, so swapping frames never shifts
    the layout.
    """
    core = f"bold {_blend(base_color, glow, 0.92)}"
    mid = f"bold {_blend(base_color, glow, 0.55)}"
    edge = _blend(base_color, glow, 0.25)

    out = Text()
    lines = art.split("\n")
    for row, line in enumerate(lines):
        band = center - row * 1.5  # slight slant — reads as a light sweep
        for col, ch in enumerate(line):
            if ch == " ":
                out.append(" ")
                continue
            d = abs(col - band)
            if d <= 2.5:
                style = core
            elif d <= 6:
                style = mid
            elif d <= 10:
                style = edge
            else:
                style = base_color
            out.append(ch, style)
        if row < len(lines) - 1:
            out.append("\n")
    return out
