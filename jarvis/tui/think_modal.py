"""Modal thinking-effort picker."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


from ..constants import THINK_EFFORTS
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, hint_line, picker_row
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


_DESCRIPTIONS = {
    "xhigh": "maximum reasoning · slowest",
    "high": "strong reasoning · default",
    "medium": "balanced reasoning",
    "low": "lighter reasoning · faster",
    "minimal": "minimal reasoning",
    "none": "thinking off · fastest",
}
_LEVEL = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5}


def _meter(effort: str) -> str:
    n = _LEVEL.get(effort, 0)
    return "▰" * n + "▱" * (5 - n)


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
        height: auto;
        max-height: 10;
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
                yield Static("∴  Thinking effort", id="modal_title")
                yield Static(
                    f"[{ui.FG_DIM}]How hard the model reasons before answering (if it supports it).[/]",
                    id="modal_status",
                )
                yield OptionList(id="think_list")
                yield Static(
                    hint_line(("↑↓", "navigate"), ("↵", "select"), ("esc", "close")),
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
            label = picker_row(
                effort,
                detail=_DESCRIPTIONS.get(effort, ""),
                right=_meter(effort),
                active=selected,
                right_style=ui.ACCENT_3 if selected else ui.FG_DIM,
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
