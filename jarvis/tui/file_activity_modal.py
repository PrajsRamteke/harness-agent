"""Ctrl+P picker — browse parallel file tool runs and scroll their output."""
from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, RichLog, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..repl.tool_display import viewer_text
from ..repl.tool_output_backfill import peekable_file_runs
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, ROW_NAME_WIDTH
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


def _status_glyph(status: str) -> str:
    return {
        "queued": "○",
        "running": "◐",
        "done": "✓",
        "error": "✗",
        "cancelled": "⊘",
    }.get(status, "·")


def _basename(path: str) -> str:
    p = (path or "").rstrip("/")
    if not p:
        return "?"
    return p.rsplit("/", 1)[-1] or p


def _label(run: dict) -> Text:
    name = run.get("name") or "tool"
    status = run.get("status") or "queued"
    label = run.get("label") or ""
    ts = time.strftime("%H:%M:%S", time.localtime(run.get("started") or 0))
    base = _basename(label) if "/" in label or label.endswith(".py") else label[:32]
    t = Text()
    t.append(f"{ts}  ", style="dim")
    t.append(f"{_status_glyph(status)} ", style="bold")
    t.append(f"{name:<{ROW_NAME_WIDTH}}", style="bold")
    t.append(f"  {base}  ", style="")
    if label and label != base:
        t.append(label, style="dim")
    chars = run.get("chars") or 0
    if status in ("done", "error") and chars:
        t.append(f"  {chars:,} chars", style="dim")
    return t


class FileActivityPickerScreen(TuiModalScreen[None]):
    """Pick a file tool run from the current wave and read its full output."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    FileActivityPickerScreen #modal {
        width: 92%;
        max-width: 140;
        max-height: 88%;
    }
    FileActivityPickerScreen #file_activity_list {
        height: auto;
        max-height: 14;
        min-height: 3;
        margin-top: 1;
    }
    FileActivityPickerScreen #file_activity_log {
        height: 1fr;
        min-height: 12;
        margin-top: 1;
        border: round {ui.BORDER};
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
                yield Static("📂  Parallel files (^P)", id="modal_title")
                yield Static("", id="modal_status")
                yield OptionList(id="file_activity_list")
                yield RichLog(
                    id="file_activity_log",
                    wrap=True,
                    highlight=False,
                    markup=False,
                    auto_scroll=False,
                )
                yield Static(
                    f"[{ui.ACCENT_3}]↑↓[/] pick file   [{ui.ACCENT_3}]tab[/] scroll output   "
                    f"[{ui.ACCENT_3}]esc[/] close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._populate()
        opts = self.query_one("#file_activity_list", OptionList)
        if opts.option_count:
            opts.highlighted = opts.option_count - 1
            self._show_highlighted()
        opts.focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def _runs(self) -> list[dict]:
        return peekable_file_runs()

    def _populate(self) -> None:
        opts = self.query_one("#file_activity_list", OptionList)
        status = self.query_one("#modal_status", Static)
        opts.clear_options()
        runs = self._runs()
        if not runs:
            status.update("No file tool output yet — run read_file or similar, then ^P.")
            return
        n_run = sum(1 for r in runs if r.get("status") == "running")
        n_done = sum(1 for r in runs if r.get("status") == "done")
        status.update(
            f"{len(runs)} file(s)"
            + (f" · {n_run} running" if n_run else "")
            + (f" · {n_done} done" if n_done else "")
        )
        for run in runs:
            rid = run.get("id") or ""
            opts.add_option(Option(_label(run), id=str(rid)))

    def _show_highlighted(self) -> None:
        opts = self.query_one("#file_activity_list", OptionList)
        idx = opts.highlighted
        if idx is None:
            return
        try:
            opt = opts.get_option_at_index(idx)
            rid = opt.id
        except Exception:
            return
        self._show_run_id(str(rid) if rid is not None else "")

    def _show_run_id(self, run_id: str) -> None:
        log = self.query_one("#file_activity_log", RichLog)
        runs = {str(r.get("id")): r for r in self._runs()}
        run = runs.get(run_id)
        if not run:
            return
        log.clear()
        header = Text()
        header.append(f"{run.get('name', 'tool')}\n", style="bold")
        header.append(f"{run.get('label', '')}\n\n", style="dim")
        log.write(header)
        content = run.get("content") or ""
        if content:
            log.write(Text(viewer_text(content)))
        elif run.get("status") == "running":
            log.write(Text("Still running…", style="dim italic"))
        elif run.get("status") == "queued":
            log.write(Text("Queued…", style="dim italic"))
        else:
            log.write(Text("(no output captured)", style="dim"))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id != "file_activity_list":
            return
        self._show_highlighted()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "file_activity_list":
            return
        self._show_highlighted()
        self.query_one("#file_activity_log", RichLog).focus()

    def action_focus_output(self) -> None:
        self.query_one("#file_activity_log", RichLog).focus()

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#file_activity_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#file_activity_list", OptionList).action_cursor_up()

    def action_page_down(self) -> None:
        self.query_one("#file_activity_log", RichLog).scroll_page_down(animate=False)

    def action_page_up(self) -> None:
        self.query_one("#file_activity_log", RichLog).scroll_page_up(animate=False)
