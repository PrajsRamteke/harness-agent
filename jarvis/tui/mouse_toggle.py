"""Temporarily enable terminal mouse tracking for the lifetime of a modal.

The app runs with mouse=False so users can natively select text on the main
screen. Modals (command palette, session picker) need click events, so they
enable mouse tracking on mount and restore on unmount.

Uses basic button-event mode (+ SGR encoding).  Crucially excludes ?1003h
(any-event tracking) which:

  a) Floods the terminal with mouse-motion reports, causing visible garbage
     text ("A A ABB" artifacts) in terminals that don't fully support it.
  b) Intercepts all mouse drags, preventing native text selection (copy/paste).
  c) Interferes with click-to-position in the PromptArea / TextArea input.

?1000h — basic button press/release (clicks + scroll wheel)
?1002h — button-event tracking (drag while button held, needed for trackpad
         scrolling in OptionList)
?1006h — SGR-extended coordinate encoding (supports >223 columns/rows)
"""
from __future__ import annotations

import sys

# Minimal mouse tracking: basic events + SGR encoding.
# Deliberately omits ?1003h (any-event tracking) which causes text corruption.
_ENABLE = "\x1b[?1000h\x1b[?1002h\x1b[?1006h"
_DISABLE = "\x1b[?1006l\x1b[?1002l\x1b[?1000l"


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


def reset_mouse_fully() -> None:
    """Reset all mouse tracking modes comprehensively.  Call on app exit to
    restore the terminal to a clean state, even if the app crashed while a
    modal was active."""
    _write(
        "\x1b[?1000l"    # basic button events
        "\x1b[?1001l"    # highlight tracking (VT200)
        "\x1b[?1002l"    # button-event drag
        "\x1b[?1003l"    # any-event tracking
        "\x1b[?1005l"    # UTF-8 coordinate encoding
        "\x1b[?1006l"    # SGR coordinate encoding
        "\x1b[?1015l"    # urxvt coordinate encoding
    )
