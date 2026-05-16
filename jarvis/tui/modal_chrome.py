"""Shared backdrop, frame, and OptionList styling for centered TUI modals.

The actual CSS now lives in :mod:`jarvis.tui.theme` so every widget,
modal, and chat panel pulls colors from a single source. This module
re-exports the modal CSS plus a few row-formatting helpers.

To pick up the current theme's modal CSS at runtime, call ``get_modal_chrome_css()``
instead of caching the ``TUI_MODAL_CHROME_CSS`` string at import time.
"""
from __future__ import annotations

from typing import TypeVar

from textual.screen import ModalScreen

from . import theme as _theme

TDismiss = TypeVar("TDismiss")


# Standard column width shared by every list-style modal.
ROW_NAME_WIDTH = 22


# Backwards-compatible alias — every modal imports this name.
# Evaluated at import time; refreshes when the app calls reload_chrome_css()
# after a theme switch.
TUI_MODAL_CHROME_CSS: str = _theme.MODAL_CSS


def get_modal_chrome_css() -> str:
    """Return the current theme's modal chrome CSS string.

    Use this in ``DEFAULT_CSS`` concatenations to ensure fresh theme colors
    are picked up even after a runtime theme switch.
    """
    return _theme.MODAL_CSS


def reload_chrome_css() -> None:
    """Re-read modal CSS from the current theme (call after ``set_theme``)."""
    global TUI_MODAL_CHROME_CSS
    TUI_MODAL_CHROME_CSS = _theme.MODAL_CSS


class TuiModalScreen(ModalScreen[TDismiss]):
    """Adds ``tui-modal-screen`` for shared chrome in ``TUI_MODAL_CHROME_CSS``."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.add_class("tui-modal-screen")


# ── Shared row formatters ─────────────────────────────────────────────────


def marker_for(active: bool) -> tuple[str, str]:
    """Return (text, style) for the active-row indicator.

    ``●`` for active vs. two-space placeholder so every row keeps the same
    two-column gutter regardless of state.
    """
    if active:
        return ("● ", f"bold {_theme.OK}")
    return ("  ", "")
