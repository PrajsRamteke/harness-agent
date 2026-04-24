"""Temporarily enable terminal mouse tracking for the lifetime of a modal.

The app runs with mouse=False so users can natively select text on the main
screen. Modals (/ palette, session picker) need wheel/touchpad scroll, so they
enable mouse tracking on mount and restore on unmount by writing the standard
xterm DEC private-mode sequences directly to the driver's output stream.
"""
from __future__ import annotations

import sys

# SGR-extended mouse tracking (1006) + any-event tracking (1003).
_ENABLE = "\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h"
_DISABLE = "\x1b[?1006l\x1b[?1003l\x1b[?1002l\x1b[?1000l"


def _write(seq: str) -> None:
    try:
        sys.__stdout__.write(seq)
        sys.__stdout__.flush()
    except Exception:
        pass


def enable_mouse() -> None:
    _write(_ENABLE)


def disable_mouse() -> None:
    _write(_DISABLE)
