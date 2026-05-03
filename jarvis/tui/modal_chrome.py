"""Shared backdrop, frame, and OptionList styling for centered TUI modals."""
from __future__ import annotations

from typing import TypeVar

from textual.screen import ModalScreen

TDismiss = TypeVar("TDismiss")

TUI_MODAL_CHROME_CSS = """
.tui-modal-screen {
    background: rgba(0, 0, 0, 0.60);
}
.tui-modal-screen #modal {
    height: auto;
    background: #161b22;
    border: tall #30363d;
}
.tui-modal-screen #modal_title {
    color: #bb9af7;
    text-style: bold;
    padding-bottom: 1;
}
.tui-modal-screen #modal_hint {
    color: #8b949e;
    padding-top: 1;
}
.tui-modal-screen OptionList {
    background: #161b22;
    color: #e6edf3;
    border: none;
    overflow-y: auto;
    scrollbar-size-vertical: 1;
    scrollbar-color: #21262d #161b22;
}
.tui-modal-screen OptionList:focus > .option-list--option-highlighted,
.tui-modal-screen OptionList > .option-list--option-highlighted {
    background: #1f2937;
    color: #ffffff;
}
.tui-modal-screen Input {
    background: #0d1117;
    color: #e6edf3;
    border: tall #30363d;
}
.tui-modal-screen Input:focus {
    border: tall #58a6ff;
}
"""


class TuiModalScreen(ModalScreen[TDismiss]):
    """Adds ``tui-modal-screen`` for shared chrome in ``TUI_MODAL_CHROME_CSS``."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.add_class("tui-modal-screen")
