"""Modal thinking-effort picker."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..constants import THINK_EFFORTS
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse


_DESCRIPTIONS = {
    "xhigh": "maximum reasoning",
    "high": "strong reasoning",
    "medium": "balanced reasoning",
    "low": "lighter reasoning",
    "minimal": "minimal reasoning",
    "none": "disable thinking",
}


class ThinkPickerScreen(TuiModalScreen[str | None]):
    """Pick a thinking effort. Returns the selected effort, or None."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ThinkPickerScreen #modal {
        width: 58%;
        max-width: 80;
        max-height: 70%;
    }
    ThinkPickerScreen OptionList {
        height: 9;
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
                yield Static("✦  Thinking Effort", id="modal_title")
                yield OptionList(id="think_list")
                yield Static(
                    "[#f0b3ff]↑↓[/] navigate   [#f0b3ff]↵[/] select   [#f0b3ff]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._prev_scroll_y = self.app.scroll_sensitivity_y
        self.app.scroll_sensitivity_y = 1.0
        opts = self.query_one("#think_list", OptionList)
        for effort in THINK_EFFORTS:
            selected = (
                state.think_mode and effort == state.think_effort
            ) or (not state.think_mode and effort == "none")
            marker = "● " if selected else "  "
            label = Text.assemble(
                (marker, "bold #3fb950"),
                (f"{effort:<9s}", "bold #79c0ff" if selected else "#79c0ff"),
                ("  ", ""),
                (_DESCRIPTIONS.get(effort, ""), "#8b949e"),
            )
            opts.add_option(Option(label, id=effort))
        opts.highlighted = list(THINK_EFFORTS).index(
            state.think_effort if state.think_mode and state.think_effort in THINK_EFFORTS else "none"
        )
        opts.focus()

    def on_unmount(self) -> None:
        disable_mouse()
        try:
            self.app.scroll_sensitivity_y = self._prev_scroll_y
        except AttributeError:
            pass

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id) if event.option.id else None)

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#think_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#think_list", OptionList).action_cursor_up()
