"""Shared backdrop, frame, and OptionList styling for centered TUI modals."""
from __future__ import annotations

from typing import TypeVar

from textual.screen import ModalScreen

TDismiss = TypeVar("TDismiss")

TUI_MODAL_CHROME_CSS = """
.tui-modal-screen {
    background: rgba(0, 0, 0, 0.55);
}
.tui-modal-screen #modal {
    height: auto;
    background: #12151a;
    border: tall #7aa2f7;
}
.tui-modal-screen #modal_title {
    color: #bb9af7;
    text-style: bold;
    padding-bottom: 1;
}
.tui-modal-screen #modal_hint {
    color: #7aa2f7;
    padding-top: 1;
}
.tui-modal-screen OptionList {
    background: #12151a;
    color: #e6e6e6;
    border: none;
    overflow-y: auto;
    scrollbar-size-vertical: 1;
}
.tui-modal-screen OptionList:focus > .option-list--option-highlighted,
.tui-modal-screen OptionList > .option-list--option-highlighted {
    background: #2b3340;
    color: #ffffff;
}
"""


class TuiModalScreen(ModalScreen[TDismiss]):
    """Adds ``tui-modal-screen`` for shared chrome in ``TUI_MODAL_CHROME_CSS``."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.add_class("tui-modal-screen")
