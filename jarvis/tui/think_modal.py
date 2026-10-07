"""Modal thinking-effort picker."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


from .. import state
from ..repl import thinking
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, hint_line, picker_row
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


_LEVEL = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5, "max": 6, "ultra": 7}
_METER_WIDTH = 7


def _meter(effort: str) -> str:
    if effort == "on":
        return "▰" * _METER_WIDTH
    n = _LEVEL.get(effort, 0)
    return "▰" * n + "▱" * (_METER_WIDTH - n)


def think_rows(info: dict) -> list[dict]:
    """Picker rows from ``thinking.public()``: ``{value, title, detail, right,
    available, active}`` — pure data, so the layout is testable without a screen."""
    rows = []
    for c in info.get("choices") or []:
        value = str(c.get("value") or "")
        ok = bool(c.get("available", True))
        title = "on" if value == "on" else value
        rows.append({
            "value": value,
            "title": title,
            "detail": (c.get("detail") if ok else c.get("why")) or "",
            "right": _meter(value) if ok else "",
            "available": ok,
            "active": ok and value == info.get("current"),
        })
    return rows


def status_text(info: dict) -> str:
    """The line under the title: what this model takes (and what's in use)."""
    model = (state.MODEL or "").split("/")[-1] or "this model"
    bits = [f"[{ui.FG}]{model}[/] [{ui.FG_DIM}]· {info.get('summary', '')}[/]"]
    if info.get("source"):
        bits.append(f"[{ui.FG_DIM}]source: {info['source']}{' (narrowed by a refusal)' if info.get('learned') else ''}[/]")
    if info.get("note"):
        bits.append(f"[{ui.WARN}]{info['note']}[/]")
    return "\n".join(bits)


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

    def __init__(self) -> None:
        super().__init__()
        # Read once: what the current model takes, so every row below agrees.
        self._info = thinking.public()

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("∴  Thinking effort", id="modal_title")
                yield Static(status_text(self._info), id="modal_status")
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
        highlighted = None
        for i, r in enumerate(think_rows(self._info)):
            label = picker_row(
                r["title"],
                detail=r["detail"],
                right=r["right"],
                active=r["active"],
                right_style=ui.ACCENT_3 if r["active"] else ui.FG_DIM,
                title_style=None if r["available"] else ui.FG_DIM,
                detail_style=ui.FG_DIM,
            )
            # A level the model lacks stays listed (so it's clear why the list
            # is short) but the cursor skips it.
            opts.add_option(Option(label, id=r["value"], disabled=not r["available"]))
            if r["active"] and highlighted is None:
                highlighted = i
        if highlighted is None:
            highlighted = next((i for i, r in enumerate(think_rows(self._info)) if r["available"]), 0)
        opts.highlighted = highlighted
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
