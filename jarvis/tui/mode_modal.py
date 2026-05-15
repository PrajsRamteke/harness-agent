"""Centered mode picker modal — select default / coding / reverse_eng / setup.

Mirrors the think-effort picker:
* ↑/↓ to navigate
* Enter to switch to the highlighted mode
* Esc to cancel
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..constants import MODE_DEFAULT, MODE_CODING, MODE_REVERSE_ENG, MODE_SETUP
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse


_MODES: list[tuple[str, str, str]] = [
    # (id, icon + label, description)
    (MODE_DEFAULT,     "  default       ", "base system prompt only"),
    (MODE_CODING,      "⚡ coding        ", "large-codebase rules, context bundle workflow"),
    (MODE_REVERSE_ENG, "🔐 reverse_eng   ", "security expert / RE workflow"),
    (MODE_SETUP,       "🛠  setup        ", "teaches agent where MCP / skill / settings files live"),
]


class ModePickerScreen(TuiModalScreen[str | None]):
    """Pick the active mode. Returns the selected mode id, or None on cancel."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ModePickerScreen #modal {
        width: 62%;
        max-width: 90;
        max-height: 70%;
        padding: 2 3;
    }
    ModePickerScreen OptionList {
        height: 8;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("🎛  mode", id="modal_title")
                yield OptionList(id="mode_list")
                yield Static("↑/↓ navigate • Enter select • Esc cancel", id="modal_hint")

    def on_mount(self) -> None:
        enable_mouse()
        try:
            self._prev_scroll_y = self.app.scroll_sensitivity_y
            self.app.scroll_sensitivity_y = 1.0
        except AttributeError:
            self._prev_scroll_y = None

        opts = self.query_one("#mode_list", OptionList)
        active = state.active_mode
        active_index = 0
        for idx, (mode_id, label, desc) in enumerate(_MODES):
            selected = mode_id == active
            marker = " ●" if selected else "  "
            label_text = Text.assemble(
                (marker, "green bold"),
                (label, "bold cyan" if selected else "cyan"),
                (f"  {desc}", "dim"),
            )
            opts.add_option(Option(label_text, id=mode_id))
            if selected:
                active_index = idx
        opts.highlighted = active_index
        opts.focus()

    def on_unmount(self) -> None:
        disable_mouse()
        if self._prev_scroll_y is not None:
            try:
                self.app.scroll_sensitivity_y = self._prev_scroll_y
            except AttributeError:
                pass

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id) if event.option.id else None)

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#mode_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#mode_list", OptionList).action_cursor_up()
