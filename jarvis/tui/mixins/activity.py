"""Live activity feedback for the Jarvis TUI: the busy spinner and the
parallel-file "tool dock" panel rendered inside the transcript.

These two concerns are grouped because the spinner tick drives the dock
refresh. Mixed into ``JarvisTUI``.
"""
from __future__ import annotations

import time

from rich.markup import escape as _rich_escape
from rich.panel import Panel
from rich.text import Text
from textual.widgets import RichLog

from ..console_shim import (
    _append_rich_log_block,
    _at_bottom,
    _replace_rich_log_block,
    _truncate_rich_log_lines,
)
from ...repl.tool_runs import list_runs, show_parallel_file_panel
from .. import theme as ui


class ActivityMixin:
    """Spinner + parallel-file dock behaviour for ``JarvisTUI``."""

    # ─── parallel-file "tool dock" panel ───────────────────────────────
    @staticmethod
    def _tool_run_glyph(status: str) -> str:
        return {
            "queued": "○",
            "running": "◐",
            "done": "✓",
            "error": "✗",
            "cancelled": "⊘",
        }.get(status, "·")

    def _format_tool_dock_row(self, run: dict) -> str:
        status = run.get("status") or "queued"
        glyph = self._tool_run_glyph(status)
        if status == "error":
            g_style = ui.ERR
        elif status == "done":
            g_style = ui.OK
        elif status == "running":
            g_style = ui.ACCENT
        else:
            g_style = ui.FG_DIM
        name = run.get("name") or "tool"
        label = (run.get("label") or "").strip() or "…"
        if "/" in label:
            base, _, parent = label.rpartition("/")
            path_bit = f"[{ui.FG}]{_rich_escape(base)}[/] [{ui.FG_DIM}]{_rich_escape(parent)}[/]"
        else:
            path_bit = f"[{ui.FG}]{_rich_escape(label)}[/]"
        tail = ""
        if status in ("done", "error") and run.get("chars"):
            tail = f" [{ui.FG_DIM}]{int(run['chars']):,} chars[/]"
        elif status == "running":
            elapsed = max(0.0, time.monotonic() - float(run.get("started") or 0))
            tail = f" [{ui.FG_DIM}]{elapsed:.1f}s[/]"
        if run.get("repaired"):
            tail += f" [{ui.WARN}]⚒[/]"
        return (
            f"  [{g_style}]{glyph}[/] [{ui.FG_MUTE}]{name:<14}[/] {path_bit}{tail}"
        )

    def reset_tool_activity_panel(self) -> None:
        """Next tool wave appends a fresh panel in the transcript."""
        self._tool_activity_anchor = None
        self._tool_activity_line_count = 0
        self._tool_activity_frozen = False

    def _parallel_panel_verb(self, runs: list[dict]) -> str:
        if any(r.get("name") in ("multi_edit", "edit_file", "write_file") for r in runs):
            return "editing"
        return "reading"

    def _build_tool_activity_panel(self, runs: list[dict]) -> Panel:
        n = len(runs)
        n_run = sum(1 for r in runs if r.get("status") == "running")
        n_q = sum(1 for r in runs if r.get("status") == "queued")
        verb = self._parallel_panel_verb(runs)
        title = f"⚡ {n} parallel file{'s' if n != 1 else ''}"
        if n_run or n_q:
            bits = []
            if n_run:
                bits.append(f"{n_run} {verb}")
            if n_q:
                bits.append(f"{n_q} queued")
            title += f" · {' · '.join(bits)}"
        body_lines = [self._format_tool_dock_row(r) for r in runs]
        body_lines.append(f"[{ui.FG_DIM}]^F inspect files[/]")
        return Panel(
            Text.from_markup("\n".join(body_lines)),
            title=title,
            title_align="left",
            border_style=ui.ACCENT,
            padding=(0, 1),
        )

    def _refresh_tool_dock(self) -> None:
        """Live-update a parallel-files panel inside the conversation transcript."""
        with self._tool_activity_lock:
            if self._tool_activity_frozen:
                return
            runs = list_runs()
            try:
                log = self.query_one("#transcript", RichLog)
            except Exception:
                return

            if not show_parallel_file_panel():
                if self._tool_activity_anchor is not None:
                    _truncate_rich_log_lines(log, self._tool_activity_anchor)
                    self._tool_activity_anchor = None
                    self._tool_activity_line_count = 0
                self._tool_activity_frozen = False
                return

            panel = self._build_tool_activity_panel(runs)
            anchor = self._tool_activity_anchor
            all_settled = all(
                r.get("status") in ("done", "error", "cancelled") for r in runs
            )

            follow = _at_bottom(log)
            if anchor is None:
                anchor = len(log.lines)
                self._tool_activity_anchor = anchor
                self._tool_activity_line_count = _append_rich_log_block(
                    log, panel, scroll_end=follow
                )
            else:
                self._tool_activity_line_count = _replace_rich_log_block(
                    log, anchor, self._tool_activity_line_count, panel
                )
                if follow:
                    log.scroll_end(animate=False)

            if all_settled:
                self._tool_activity_anchor = None
                self._tool_activity_line_count = 0
                self._tool_activity_frozen = True

    # ─── activity / spinner ────────────────────────────────────────────
    def _sync_activity_phase(self, label: str) -> None:
        label = (label or "").strip()
        if label != self._activity_label:
            self._activity_t0 = time.monotonic()
            self._activity_spinner_i = 0
        self._activity_label = label
        self._refresh_activity_widgets()

    def _tick_activity_spinner(self) -> None:
        if self._busy and self._activity_label:
            self._activity_spinner_i += 1
            self._refresh_activity_widgets()
        tick_cmd = getattr(self._tui_console, "tick_command_progress_spinner", None)
        if callable(tick_cmd):
            tick_cmd()
        if (
            not self._tool_activity_frozen
            and show_parallel_file_panel()
            and any(r.get("status") == "running" for r in list_runs())
        ):
            self._refresh_tool_dock()

    def _refresh_activity_widgets(self) -> None:
        label = self._activity_label
        if not label or not self._busy:
            return
        self._write_status_line(busy=True)

    def _start_activity_pulse(self) -> None:
        self._stop_activity_pulse()
        self._activity_timer = self.set_interval(0.1, self._tick_activity_spinner)

    def _stop_activity_pulse(self) -> None:
        if self._activity_timer is not None:
            self._activity_timer.stop()
            self._activity_timer = None
