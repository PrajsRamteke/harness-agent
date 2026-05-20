"""Scrollable viewer for full tool call output (read_file, shell, etc.)."""
from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, RichLog, Static
from textual.widgets.option_list import Option

from rich.text import Text

from .. import state
from ..repl.tool_display import viewer_text
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, ROW_NAME_WIDTH
from .mouse_toggle import enable_mouse, disable_mouse


def _label(entry: dict) -> Text:
    name = entry.get("name") or "tool"
    args = (entry.get("args") or "")[:48]
    ts = time.strftime("%H:%M:%S", time.localtime(entry.get("ts") or 0))
    chars = len(entry.get("content") or "")
    label = Text()
    label.append(f"{ts}  ", style="dim")
    label.append(f"{name:<{ROW_NAME_WIDTH}}", style="bold")
    label.append(f"  {args}  ", style="dim")
    label.append(f"{chars:,} chars", style="dim")
    return label


class ToolOutputViewerScreen(TuiModalScreen[None]):
    """Pick a recent tool call and scroll through its full output."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ToolOutputViewerScreen #modal {
        width: 92%;
        max-width: 140;
        max-height: 88%;
    }
    ToolOutputViewerScreen #tool_output_list {
        height: auto;
        max-height: 8;
        min-height: 3;
        margin-top: 1;
    }
    ToolOutputViewerScreen #tool_output_log {
        height: 1fr;
        min-height: 12;
        margin-top: 1;
        border: round #2a323d;
        padding: 0 1;
        scrollbar-size-vertical: 1;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("tab", "focus_output", "Scroll output", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("pageup", "page_up", show=False),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⚙  Tool output (scrollable)", id="modal_title")
                yield Static("", id="modal_status")
                yield OptionList(id="tool_output_list")
                yield RichLog(
                    id="tool_output_log",
                    wrap=True,
                    highlight=False,
                    markup=False,
                    auto_scroll=False,
                )
                yield Static(
                    "[#f0b3ff]↑↓[/] pick tool   [#f0b3ff]tab[/] scroll output   "
                    "[#f0b3ff]PgUp/PgDn[/] in output   [#f0b3ff]esc[/] close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._populate()
        opts = self.query_one("#tool_output_list", OptionList)
        if opts.option_count:
            opts.highlighted = opts.option_count - 1
            self._show_index(opts.highlighted)
        opts.focus()

    def action_focus_output(self) -> None:
        self.query_one("#tool_output_log", RichLog).focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def _populate(self) -> None:
        opts = self.query_one("#tool_output_list", OptionList)
        status = self.query_one("#modal_status", Static)
        opts.clear_options()
        entries = list(state.tool_output_history)
        if not entries:
            status.update("No tool output yet — run a tool, then open ^F again.")
            return
        status.update(f"{len(entries)} recent tool result(s)")
        for i, entry in enumerate(entries):
            opts.add_option(Option(_label(entry), id=str(i)))

    def _show_index(self, index: int | None) -> None:
        log = self.query_one("#tool_output_log", RichLog)
        entries = list(state.tool_output_history)
        if index is None or index < 0 or index >= len(entries):
            return
        entry = entries[index]
        log.clear()
        header = Text()
        header.append(f"{entry.get('name', 'tool')}\n", style="bold")
        header.append(f"{entry.get('args', '')}\n\n", style="dim")
        log.write(header)
        log.write(Text(viewer_text(entry.get("content") or "")))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id != "tool_output_list":
            return
        self._show_index(event.option_list.highlighted)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "tool_output_list":
            return
        self._show_index(event.option_list.highlighted)
        self.query_one("#tool_output_log", RichLog).focus()

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#tool_output_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#tool_output_list", OptionList).action_cursor_up()

    def action_page_down(self) -> None:
        self.query_one("#tool_output_log", RichLog).scroll_page_down(animate=False)

    def action_page_up(self) -> None:
        self.query_one("#tool_output_log", RichLog).scroll_page_up(animate=False)
