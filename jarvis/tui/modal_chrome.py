"""Shared backdrop, frame, and OptionList styling for centered TUI modals.

Every modal in `jarvis.tui` inherits these styles via ``TuiModalScreen`` +
``TUI_MODAL_CHROME_CSS``. Keep token use here disciplined so the visual
language stays uniform — colors are GitHub-Dark accents:

  bg-base        #0d1117    page background
  bg-elev        #161b22    modal / inputs surface
  bg-hover       #1f2937    highlighted row
  border-mute    #30363d    resting borders
  border-focus   #58a6ff    focused borders / accents
  fg-base        #e6edf3    primary text
  fg-mute        #8b949e    secondary text
  accent-title   #bb9af7    modal title
  accent-key     #f0b3ff    key hints

Row formatters in modals should also share the same column geometry so the
catalog feels uniform:

    MARKER (2)  NAME (22)  DESC (rest, dim)
"""
from __future__ import annotations

from typing import TypeVar

from textual.screen import ModalScreen

TDismiss = TypeVar("TDismiss")


# Standard column widths shared by every list-style modal.
ROW_NAME_WIDTH = 22


TUI_MODAL_CHROME_CSS = """
/* ── Backdrop ──────────────────────────────────────────────── */
.tui-modal-screen {
    background: rgba(0, 0, 0, 0.62);
    align: center middle;
}

/* ── Frame ─────────────────────────────────────────────────── */
.tui-modal-screen #modal {
    height: auto;
    background: #161b22;
    border: round #30363d;
    padding: 1 2;
}

/* ── Title row (emoji + label) ─────────────────────────────── */
.tui-modal-screen #modal_title {
    color: #bb9af7;
    text-style: bold;
    padding: 0 1 1 1;
    border-bottom: hkey #21262d;
    margin-bottom: 1;
    width: 100%;
}

/* ── Status / sub-title strip just under the title ─────────── */
.tui-modal-screen #modal_status {
    color: #8b949e;
    padding: 0 1;
    margin-bottom: 1;
    width: 100%;
    height: auto;
}

/* ── Footer hint row (key cheatsheet) ──────────────────────── */
.tui-modal-screen #modal_hint {
    color: #6e7681;
    padding: 1 1 0 1;
    border-top: hkey #21262d;
    margin-top: 1;
    width: 100%;
}

/* ── Inputs ────────────────────────────────────────────────── */
.tui-modal-screen Input {
    background: #0d1117;
    color: #e6edf3;
    border: tall #30363d;
    padding: 0 1;
    height: 3;
}
.tui-modal-screen Input:focus {
    border: tall #58a6ff;
}

/* ── Option lists ──────────────────────────────────────────── */
.tui-modal-screen OptionList {
    background: #161b22;
    color: #e6edf3;
    border: none;
    padding: 0;
    overflow-y: auto;
    scrollbar-size-vertical: 0;
    scrollbar-color: transparent transparent;
}
.tui-modal-screen OptionList > .option-list--option {
    padding: 0 1;
}
.tui-modal-screen OptionList > .option-list--option-highlighted,
.tui-modal-screen OptionList:focus > .option-list--option-highlighted {
    background: #1f2937;
    color: #ffffff;
    text-style: none;
}
.tui-modal-screen OptionList > .option-list--option-disabled {
    color: #6e7681;
}

/* ── TextArea (used in import / multiline modals) ─────────── */
.tui-modal-screen TextArea {
    background: #0d1117;
    color: #e6edf3;
    border: tall #30363d;
    padding: 0 1;
}
.tui-modal-screen TextArea:focus {
    border: tall #58a6ff;
}
"""


class TuiModalScreen(ModalScreen[TDismiss]):
    """Adds ``tui-modal-screen`` for shared chrome in ``TUI_MODAL_CHROME_CSS``."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.add_class("tui-modal-screen")


# ── Shared row formatters ─────────────────────────────────────────────────


def marker_for(active: bool) -> tuple[str, str]:
    """Return (text, style) for the active-row indicator.

    Using ``●`` for active and a thin bullet placeholder keeps every row
    exactly two columns wide regardless of state — required for clean
    alignment in the option list.
    """
    if active:
        return ("● ", "bold #3fb950")
    return ("  ", "")
