"""Rich-Console-compatible shim that routes output to a Textual RichLog.

Only the subset of the Rich Console API actually used across the codebase is
implemented: ``print``, ``rule``, ``status`` (as a no-op context manager), and
``input`` (unused in TUI mode — raises if called). Renderables are forwarded
to the app's RichLog from any thread via ``App.call_from_thread``.
"""
import re
from contextlib import contextmanager
from typing import Any

from rich.console import Console as _RichConsole
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from textual.geometry import Size
from textual.widgets import RichLog, Static


def _truncate_rich_log_lines(log: RichLog, line_count: int) -> None:
    """Drop lines from ``line_count`` onward (used to replace in-progress stream block)."""
    log.lines = log.lines[:line_count]
    log._line_cache.clear()
    if log.lines:
        log._widest_line_width = max(s.cell_length for s in log.lines)
    else:
        log._widest_line_width = 0
    log.virtual_size = Size(log._widest_line_width, len(log.lines))
    log.refresh(layout=True)


class TUIConsole:
    def __init__(self, app, log_widget, status_widget=None):
        self._app = app
        self._log = log_widget
        self._status = status_widget
        self._renderer = _RichConsole(file=None, record=False, width=120)
        # In-log streaming: line index in RichLog.lines where the current reply started
        self._stream_line_anchor = 0
        self._as_title = ""
        self._as_buffer = ""
        self._as_dirty = 0
        self._as_flush_every = 28

    # ─── assistant streaming (worker thread → main via call_from_thread) ─
    def assistant_stream_start(self, title: str) -> None:
        self._as_title = title
        self._as_buffer = ""
        self._as_dirty = 0

        def _anchor() -> int:
            return len(self._log.lines)

        self._stream_line_anchor = self._app.call_from_thread(_anchor)

    def assistant_stream_push(self, chunk: str) -> None:
        if not chunk:
            return
        self._as_buffer += chunk
        self._as_dirty += len(chunk)
        if self._as_dirty >= self._as_flush_every:
            self._as_dirty = 0
            self._flush_streaming_panel()

    def assistant_stream_flush(self) -> None:
        self._as_dirty = 0
        if not self._as_buffer:
            return
        self._flush_streaming_panel()

    def _flush_streaming_panel(self) -> None:
        buf = self._as_buffer
        title = self._as_title
        anchor = self._stream_line_anchor

        def _upd():
            _truncate_rich_log_lines(self._log, anchor)
            body = buf if buf.strip() else " "
            self._log.write(
                Panel(
                    Markdown(body),
                    title=title,
                    title_align="left",
                    border_style="magenta",
                    padding=(0, 1),
                ),
                scroll_end=True,
            )

        try:
            self._app.call_from_thread(_upd)
        except Exception:
            pass

    def assistant_stream_commit(self, text: str, title: str, was_flagged: bool) -> None:
        """Replace the in-log stream preview with the final scrubbed panel."""
        del was_flagged
        from .. import state as _state

        anchor = self._stream_line_anchor

        def _commit():
            _truncate_rich_log_lines(self._log, anchor)
            self._as_buffer = ""
            self._as_dirty = 0
            _state._assistant_stream_ui_active = False
            if re.search(r"\S", text):
                self._log.write(
                    Panel(
                        Markdown(text),
                        title=title,
                        title_align="left",
                        border_style="magenta",
                        padding=(0, 1),
                    ),
                    scroll_end=True,
                )

        try:
            self._app.call_from_thread(_commit)
        except Exception:
            pass

    def assistant_stream_abort(self) -> None:
        """Remove a partial in-log stream (cancel / error)."""
        from .. import state as _state

        def _abort():
            self._as_buffer = ""
            self._as_dirty = 0
            if _state._assistant_stream_ui_active:
                _truncate_rich_log_lines(self._log, self._stream_line_anchor)
            _state._assistant_stream_ui_active = False

        try:
            self._app.call_from_thread(_abort)
        except Exception:
            _state._assistant_stream_ui_active = False

    # ─── internal ───────────────────────────────────────────────────────
    def _write(self, renderable):
        try:
            self._app.call_from_thread(self._log.write, renderable)
        except Exception:
            try:
                self._log.write(renderable)
            except Exception:
                pass

    def _terminal_width(self) -> int:
        try:
            return max(24, int(self._log.size.width))
        except Exception:
            return 80

    # ─── Rich.Console surface ──────────────────────────────────────────
    def print(self, *objects: Any, sep: str = " ", end: str = "\n", **kwargs):
        if not objects:
            self._write(Text(""))
            return
        self._renderer.width = self._terminal_width()
        if all(isinstance(obj, str) for obj in objects):
            text = sep.join(objects)
            if end and end != "\n":
                text += end
            self._write(Text.from_markup(text))
            return
        for obj in objects:
            if isinstance(obj, str):
                self._write(Text.from_markup(obj))
            else:
                self._write(obj)

    def rule(self, title: str = "", *, style: str = "rule.line", **kwargs):
        self._write(Rule(title=title, style=style))

    @contextmanager
    def status(self, message: str = "", **kwargs):
        prev = None
        if self._status is not None:
            try:
                prev = getattr(self._status, "renderable", None)
                self._app.call_from_thread(self._status.update, message)
            except Exception:
                pass
        try:
            yield self
        finally:
            if self._status is not None:
                try:
                    self._app.call_from_thread(self._status.update, prev or "")
                except Exception:
                    pass

    def clear(self, *args, **kwargs):
        try:
            self._app.call_from_thread(self._log.clear)
        except Exception:
            try:
                self._log.clear()
            except Exception:
                pass

    def input(self, *args, **kwargs):  # noqa: D401 — unused in TUI mode
        raise RuntimeError("TUIConsole.input called — input flows through the Input widget")
