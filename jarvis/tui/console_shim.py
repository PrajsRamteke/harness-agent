"""Rich-Console-compatible shim that routes output to a Textual RichLog.

Implemented: ``print``, ``rule``, ``status`` (no-op context manager),
``prompt_shell_approval`` (blocking Y/n/a modal), ``prompt_ask_user_question``
(status-bar multiple choice), and ``input`` (blocking
text-input modal for worker threads). Renderables are forwarded to the app's
RichLog from any thread via ``App.call_from_thread``.
"""
import queue
import re
import threading
import time
from contextlib import contextmanager
from typing import Any

from rich.console import Console as _RichConsole
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.measure import measure_renderables
from rich.segment import Segment
from rich.text import Text
from textual.geometry import Size
from textual.strip import Strip
from textual.widgets import RichLog, Static


def _safe_from_markup(text: str) -> Text:
    """Parse Rich markup, falling back to plain text on malformed tags."""
    try:
        return Text.from_markup(text)
    except Exception:
        return Text(text)


class _PromptWaiter:
    """One-shot blocking prompt with optional external cancel (web bridge)."""

    def __init__(self, app) -> None:
        self._app = app
        self._q: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._done = threading.Event()

    def deliver(self, value: Any) -> None:
        if self._done.is_set():
            return
        self._done.set()
        try:
            self._q.put_nowait(value)
        except Exception:
            pass

    def wait(self, timeout: float = 3600.0) -> Any:
        from .. import state

        deadline = time.monotonic() + timeout
        while True:
            if state.cancel_requested.is_set():
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                return self._q.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue

    def dismiss_screen(self, screen_type: type, value: Any) -> None:
        def _go() -> None:
            try:
                screen = self._app.screen
                if isinstance(screen, screen_type):
                    screen.dismiss(value)
                    return
            except Exception:
                pass
            self.deliver(value)

        self._app.call_from_thread(_go)


def _at_bottom(log: RichLog) -> bool:
    """True when the user hasn't scrolled up — safe to auto-follow new output.

    Forcing ``scroll_end=True`` on every streaming flush makes it impossible
    to scroll back (or select text) while a reply streams; callers should pass
    ``scroll_end=_at_bottom(log)`` instead.
    """
    try:
        return bool(log.is_vertical_scroll_end)
    except Exception:
        return True


def _truncate_rich_log_lines(log: RichLog, line_count: int) -> None:
    """Drop lines from ``line_count`` onward (used to replace in-progress stream block).

    Deliberately does NOT touch ``virtual_size`` or the scroll offset — this
    runs on every streaming flush, and resizing/clamping here makes the view
    jitter (shrink-clamp-up, write-scroll-down, dozens of times per second).
    Callers that remove content permanently (cancel/abort) must follow up
    with ``_fixup_rich_log_viewport``.
    """
    log.lines = log.lines[:line_count]
    log._line_cache.clear()
    if hasattr(log, '_render_cache'):
        log._render_cache = {}
    log.refresh(layout=True)


def _fixup_rich_log_viewport(log: RichLog) -> None:
    """Sync ``virtual_size`` to the real line count and pull the viewport
    back inside the content after a permanent removal (esc-cancel). Without
    this the scroll offset can point past the end — an all-black transcript
    until the user scrolls."""
    try:
        log.virtual_size = Size(log._widest_line_width, len(log.lines))
        if log.scroll_offset.y > log.max_scroll_y:
            log.scroll_end(animate=False)
        log.refresh(layout=True)
    except Exception:
        pass


def _render_to_strips(
    log: RichLog,
    content,
    *,
    expand: bool = False,
    shrink: bool = True,
) -> tuple[list[Strip], int]:
    """Render *content* the same way ``RichLog.write`` would, without mutating the log."""
    renderable = log._make_renderable(content)
    console = log.app.console
    render_options = console.options

    if isinstance(renderable, Text) and not log.wrap:
        render_options = render_options.update(overflow="ignore", no_wrap=True)

    renderable_width = measure_renderables(
        console, render_options, [renderable]
    ).maximum
    scrollable_content_width = log.scrollable_content_region.width
    render_width = renderable_width
    if expand and renderable_width < scrollable_content_width:
        render_width = max(renderable_width, scrollable_content_width)
    if shrink and renderable_width > scrollable_content_width:
        render_width = min(renderable_width, scrollable_content_width)
    render_width = max(render_width, log.min_width)

    render_options = render_options.update_width(render_width)
    segments = console.render(renderable, render_options)
    lines = list(Segment.split_lines(segments))
    if not lines:
        strips = [Strip.blank(render_width)]
    else:
        strips = Strip.from_lines(lines)
        for strip in strips:
            strip.adjust_cell_length(render_width)
    return strips, render_width


def _refresh_rich_log_layout(log: RichLog) -> None:
    log._line_cache.clear()
    if hasattr(log, "_render_cache"):
        log._render_cache = {}
    log.virtual_size = Size(log._widest_line_width, len(log.lines))
    log.refresh()


def _replace_rich_log_block(
    log: RichLog,
    anchor: int,
    old_line_count: int,
    content,
) -> int:
    """Swap ``old_line_count`` lines at *anchor* for newly rendered *content*."""
    new_strips, render_width = _render_to_strips(log, content)
    anchor = max(0, min(anchor, len(log.lines)))
    log.lines = log.lines[:anchor] + new_strips + log.lines[anchor + old_line_count:]
    log._widest_line_width = max(log._widest_line_width, render_width)
    for strip in new_strips:
        log._widest_line_width = max(log._widest_line_width, strip.cell_length)
    _refresh_rich_log_layout(log)
    return len(new_strips)


def _append_rich_log_block(
    log: RichLog,
    content,
    *,
    scroll_end: bool | None = None,
) -> int:
    """Append *content* and return how many lines were added."""
    before = len(log.lines)
    log.write(content, scroll_end=scroll_end)
    return len(log.lines) - before


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
        # How many lines the live reply panel currently occupies — lets the
        # abort path replace exactly its own block instead of truncating to
        # the end of the log (which would eat lines printed after it).
        self._as_line_count = 0
        # In-log thinking stream (extended thinking / reasoning deltas)
        self._think_line_anchor = 0
        self._think_buffer = ""
        self._think_dirty = 0
        self._think_flush_every = 48
        self._think_finalized = False
        self._think_rendered = False
        self._active_shell_waiter: _PromptWaiter | None = None
        self._active_ask_waiter: _PromptWaiter | None = None
        self._active_input_waiter: _PromptWaiter | None = None
        # Live spinner beside a slash command (e.g. /upgrade) in the transcript.
        self._cmd_progress_anchor: int | None = None
        self._cmd_progress_line_count: int = 0
        self._cmd_progress_command: str = ""
        self._cmd_progress_phase: str = ""
        self._cmd_progress_spinner_i: int = 0

    # ─── thinking streaming (reasoning deltas before reply text) ───────
    def thinking_stream_start(self) -> None:
        from .. import state as _state

        self._think_buffer = ""
        self._think_dirty = 0
        self._think_finalized = False
        self._think_rendered = False
        _state._thinking_stream_ui_active = True

        def _anchor() -> int:
            return len(self._log.lines)

        self._think_line_anchor = self._app.call_from_thread(_anchor)

    def thinking_stream_push(self, chunk: str) -> None:
        from .. import state as _state

        if not chunk or self._think_finalized:
            return
        first = not self._think_buffer
        self._think_buffer += chunk
        self._think_dirty += len(chunk)
        if first or self._think_dirty >= self._think_flush_every:
            self._think_dirty = 0
            self._flush_thinking_panel()

    def thinking_stream_flush(self) -> None:
        self._think_dirty = 0
        if not self._think_buffer or self._think_finalized:
            return
        self._flush_thinking_panel()

    def thinking_stream_finalize(self) -> None:
        """Stop updating the live thinking panel; leave the last preview visible."""
        from .. import state as _state

        self._think_finalized = True
        self._think_dirty = 0
        if self._think_buffer.strip():
            _state._thinking_stream_ui_active = True
        else:
            _state._thinking_stream_ui_active = False

    def _flush_thinking_panel(self) -> None:
        from .. import state as _state

        if not _state.show_internal or self._think_finalized:
            return
        buf = self._think_buffer
        anchor = self._think_line_anchor

        def _upd():
            from .. import state as _state

            if self._think_finalized or not _state._thinking_stream_ui_active:
                return
            self._think_rendered = True
            follow = _at_bottom(self._log)
            _truncate_rich_log_lines(self._log, anchor)
            preview = buf if buf.strip() else " "
            if len(preview) > 4000:
                preview = "…" + preview[-3999:]
            self._log.write(
                Panel(
                    Text(preview, style="dim"),
                    title="thinking",
                    title_align="left",
                    border_style=self._think_border(),
                    padding=(0, 1),
                ),
                scroll_end=follow,
            )

        try:
            self._app.call_from_thread(_upd)
        except Exception:
            pass

    def thinking_stream_reset(self) -> None:
        """Forget thinking state at the start of a stream iteration.

        Without this, an iteration that streams thinking and then issues only
        tool calls (no text) leaves the active flag + an old anchor behind;
        the next commit or cancel would truncate at that stale anchor and
        wipe the tool panels written since. Called from the stream layer
        before each API response is consumed. Worker-thread safe.
        """
        from .. import state as _state

        def _reset() -> None:
            self._think_buffer = ""
            self._think_dirty = 0
            self._think_finalized = False
            self._think_rendered = False
            self._think_line_anchor = len(self._log.lines)
            _state._thinking_stream_ui_active = False

        try:
            if threading.current_thread() is threading.main_thread():
                _reset()
            else:
                self._app.call_from_thread(_reset)
        except Exception:
            pass

    # ─── assistant streaming (worker thread → main via call_from_thread) ─
    def assistant_stream_start(self, title: str) -> None:
        self._as_title = title
        self._as_buffer = ""
        self._as_dirty = 0
        self._as_line_count = 0

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

    # ─── theme helpers ───────────────────────────────────────────────
    @staticmethod
    def _asst_border() -> str:
        from . import theme as _t
        return _t.ACCENT_2

    @staticmethod
    def _think_border() -> str:
        from . import theme as _t
        return _t.SEP

    def _flush_streaming_panel(self) -> None:
        buf = self._as_buffer
        title = self._as_title
        anchor = self._stream_line_anchor

        def _upd():
            from .. import state as _state

            # A cancel may land between scheduling and execution — repainting
            # then would resurrect a ghost partial panel.
            if not _state._assistant_stream_ui_active:
                return
            follow = _at_bottom(self._log)
            _truncate_rich_log_lines(self._log, anchor)
            body = buf if buf.strip() else " "
            self._log.write(
                Panel(
                    Markdown(body),
                    title=title,
                    title_align="left",
                    border_style=self._asst_border(),
                    padding=(0, 1),
                ),
                scroll_end=follow,
            )
            self._as_line_count = len(self._log.lines) - anchor

        try:
            self._app.call_from_thread(_upd)
        except Exception:
            pass

    def assistant_stream_commit(self, text: str, title: str, was_flagged: bool,
                                thinking_blocks: list[str] | None = None) -> None:
        """Replace the in-log stream preview with the final scrubbed panel.

        If *thinking_blocks* are provided, render them first (above the text
        panel) so thinking content appears before the assistant's reply.
        """
        del was_flagged
        from .. import state as _state

        anchor = self._stream_line_anchor

        def _commit():
            follow = _at_bottom(self._log)
            _truncate_rich_log_lines(self._log, anchor)
            self._as_buffer = ""
            self._as_dirty = 0
            self._as_line_count = 0
            _state._assistant_stream_ui_active = False
            streamed_thinking = (
                _state._thinking_stream_ui_active
                and self._think_rendered
                and self._think_buffer.strip()
            )
            if thinking_blocks and _state.show_internal:
                if streamed_thinking:
                    _truncate_rich_log_lines(self._log, self._think_line_anchor)
                for tb in thinking_blocks:
                    self._log.write(
                        Panel(
                            Text(tb, style="dim"),
                            title="thinking",
                            title_align="left",
                            border_style=self._think_border(),
                            padding=(0, 1),
                        ),
                        scroll_end=follow,
                    )
            self._think_buffer = ""
            self._think_dirty = 0
            self._think_finalized = False
            self._think_rendered = False
            _state._thinking_stream_ui_active = False
            if re.search(r"\S", text):
                self._log.write(
                    Panel(
                        Markdown(text),
                        title=title,
                        title_align="left",
                        border_style=self._asst_border(),
                        padding=(0, 1),
                    ),
                    scroll_end=follow,
                )

        try:
            self._app.call_from_thread(_commit)
        except Exception:
            pass

    def _command_progress_panel(self) -> Panel:
        from . import theme as _t

        frames = _t.SPINNER_FRAMES
        sp = frames[self._cmd_progress_spinner_i % len(frames)]
        phase = self._cmd_progress_phase or "working…"
        body = (
            f"{self._cmd_progress_command}  "
            f"[{_t.ACCENT}]{sp}[/] [{_t.FG_DIM}]{phase}[/]"
        )
        return Panel(
            _safe_from_markup(body),
            title="you",
            title_align="left",
            border_style=_t.OK,
            padding=(0, 1),
        )

    def _render_command_progress_panel(self) -> None:
        if self._cmd_progress_anchor is None:
            return
        panel = self._command_progress_panel()
        anchor = self._cmd_progress_anchor
        old_count = self._cmd_progress_line_count

        def _upd() -> None:
            follow = _at_bottom(self._log)
            self._cmd_progress_line_count = _replace_rich_log_block(
                self._log, anchor, old_count, panel
            )
            if follow:
                self._log.scroll_end(animate=False)

        try:
            if threading.current_thread() is threading.main_thread():
                _upd()
            else:
                self._app.call_from_thread(_upd)
        except Exception:
            pass

    def start_command_progress(self, command: str, *, phase: str = "upgrading…") -> None:
        """Show the user's slash command with a live spinner beside it."""
        self._cmd_progress_command = (command or "").strip()
        self._cmd_progress_phase = phase
        self._cmd_progress_spinner_i = 0

        def _start() -> None:
            self._cmd_progress_anchor = len(self._log.lines)
            self._cmd_progress_line_count = _append_rich_log_block(
                self._log, self._command_progress_panel(), scroll_end=True
            )

        try:
            if threading.current_thread() is threading.main_thread():
                _start()
            else:
                self._app.call_from_thread(_start)
        except Exception:
            self._cmd_progress_anchor = None
            self._cmd_progress_line_count = 0

    def update_command_progress(self, phase: str) -> None:
        phase = (phase or "").strip()
        if not phase or self._cmd_progress_anchor is None:
            return
        if phase == self._cmd_progress_phase:
            return
        self._cmd_progress_phase = phase
        self._render_command_progress_panel()

    def tick_command_progress_spinner(self) -> None:
        if self._cmd_progress_anchor is None:
            return
        self._cmd_progress_spinner_i += 1
        self._render_command_progress_panel()

    def finish_command_progress(self) -> None:
        """Replace the live panel with a plain user panel (no spinner)."""
        if self._cmd_progress_anchor is None:
            return
        command = self._cmd_progress_command
        anchor = self._cmd_progress_anchor
        old_count = self._cmd_progress_line_count
        from . import theme as _t

        panel = Panel(
            Markdown(command),
            title="you",
            title_align="left",
            border_style=_t.OK,
            padding=(0, 1),
        )
        self._cmd_progress_anchor = None
        self._cmd_progress_line_count = 0
        self._cmd_progress_command = ""
        self._cmd_progress_phase = ""

        def _finish() -> None:
            _replace_rich_log_block(self._log, anchor, old_count, panel)

        try:
            if threading.current_thread() is threading.main_thread():
                _finish()
            else:
                self._app.call_from_thread(_finish)
        except Exception:
            pass

    def report_turn_phase(self, label: str) -> None:
        """Update the TUI activity line (spinner + phase + clock). Safe from any thread."""
        app = self._app
        if not hasattr(app, "_sync_activity_phase"):
            return

        def _go() -> None:
            app._sync_activity_phase(label)
            self.update_command_progress(label)
            # Avoid leaving the footer stuck on the initial "thinking…" for whole turns.
            if hasattr(app, "_set_status") and label:
                short = label if len(label) <= 56 else label[:53] + "…"
                try:
                    app._set_status(short)
                except Exception:
                    pass

        try:
            if threading.current_thread() is threading.main_thread():
                _go()
            else:
                app.call_from_thread(_go)
        except Exception:
            pass

    def refresh_tool_activity(self) -> None:
        """Refresh the parallel-files panel in the transcript (worker-thread safe)."""
        app = self._app
        if not hasattr(app, "_refresh_tool_dock"):
            return

        def _go() -> None:
            app._refresh_tool_dock()

        try:
            if threading.current_thread() is threading.main_thread():
                _go()
            else:
                app.call_from_thread(_go)
        except Exception:
            pass

    refresh_tool_dock = refresh_tool_activity  # backwards compat

    def reset_tool_activity_panel(self) -> None:
        app = self._app
        if not hasattr(app, "reset_tool_activity_panel"):
            return

        def _go() -> None:
            app.reset_tool_activity_panel()

        try:
            if threading.current_thread() is threading.main_thread():
                _go()
            else:
                app.call_from_thread(_go)
        except Exception:
            pass

    def assistant_stream_abort(self) -> None:
        """Stop a live stream IN PLACE (cancel / error).

        Everything already streamed stays in the transcript — the partial
        reply panel is re-rendered once with the full buffer (the last
        ~flush-interval of characters may not have been painted yet) and an
        "interrupted" marker. Nothing is deleted: an earlier version removed
        the partial panel here, which read as the whole chat being wiped.
        """
        from .. import state as _state

        def _abort():
            was_streaming = _state._assistant_stream_ui_active
            buf = self._as_buffer
            self._as_buffer = ""
            self._as_dirty = 0
            if was_streaming:
                from . import theme as _t

                follow = _at_bottom(self._log)
                anchor = self._stream_line_anchor
                if buf.strip():
                    # Replace ONLY the live panel's block — lines printed
                    # after it (e.g. "⏹ cancelled by user") must survive.
                    self._as_line_count = _replace_rich_log_block(
                        self._log,
                        anchor,
                        self._as_line_count,
                        Panel(
                            Markdown(buf),
                            title=f"{self._as_title} · ⏹ interrupted",
                            title_align="left",
                            border_style=_t.WARN,
                            padding=(0, 1),
                        ),
                    )
                elif self._as_line_count:
                    # Nothing streamed — drop the empty placeholder block.
                    self._log.lines = (
                        self._log.lines[:anchor]
                        + self._log.lines[anchor + self._as_line_count:]
                    )
                    self._as_line_count = 0
                    _refresh_rich_log_layout(self._log)
                if follow:
                    self._log.scroll_end(animate=False)
            _state._assistant_stream_ui_active = False
            # Keep any thinking panel (live or finalized) as-is — just stop
            # updating it and forget the bookkeeping.
            self._think_buffer = ""
            self._think_dirty = 0
            self._think_finalized = False
            self._think_rendered = False
            _state._thinking_stream_ui_active = False
            if was_streaming:
                _fixup_rich_log_viewport(self._log)

        try:
            self._app.call_from_thread(_abort)
        except Exception:
            _state._assistant_stream_ui_active = False
            _state._thinking_stream_ui_active = False

    def reset_stream_ui(self) -> None:
        """Drop all stream bookkeeping at the start of a turn.

        A previous turn can leave ``_thinking_stream_ui_active`` set with an
        old anchor (e.g. it streamed thinking but was cancelled before the
        text committed). If that stale anchor survives into this turn, the
        next cancel truncates at it — wiping every line written since.
        Re-anchoring to the current end of the log makes that impossible.
        """
        from .. import state as _state

        def _reset() -> None:
            self._as_buffer = ""
            self._as_dirty = 0
            self._as_line_count = 0
            self._think_buffer = ""
            self._think_dirty = 0
            self._think_finalized = False
            self._think_rendered = False
            self._stream_line_anchor = len(self._log.lines)
            self._think_line_anchor = len(self._log.lines)
            _state._assistant_stream_ui_active = False
            _state._thinking_stream_ui_active = False

        try:
            if threading.current_thread() is threading.main_thread():
                _reset()
            else:
                self._app.call_from_thread(_reset)
        except Exception:
            pass

    # ─── internal ───────────────────────────────────────────────────────
    def _write(self, renderable):
        def _go() -> None:
            self._log.write(renderable, scroll_end=_at_bottom(self._log))

        try:
            self._app.call_from_thread(_go)
        except Exception:
            try:
                _go()
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
            self._write(_safe_from_markup(text))
            return
        for obj in objects:
            if isinstance(obj, str):
                self._write(_safe_from_markup(obj))
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

    def cancel_pending_prompts(self) -> None:
        """Unblock worker-thread prompts (shell approval, ask-user, text input)."""
        if self._active_shell_waiter is not None:
            self._active_shell_waiter.deliver("n")
        if self._active_ask_waiter is not None:
            self._active_ask_waiter.deliver('{"answers":[],"cancelled":true}')
        if self._active_input_waiter is not None:
            self._active_input_waiter.deliver(None)

        def _go() -> None:
            from .shell_approval_modal import ShellApprovalScreen

            if self._active_shell_waiter is not None:
                self._active_shell_waiter.dismiss_screen(ShellApprovalScreen, "n")
            if getattr(self._app, "_ask_user", None) and self._app._ask_user.active:
                self._app._ask_user.cancel()

        try:
            if threading.current_thread() is threading.main_thread():
                _go()
            else:
                self._app.call_from_thread(_go)
        except Exception:
            pass

    def cancel_shell_approval(self, result: str = "n") -> None:
        """Unblock a pending shell approval (e.g. answered on web remote)."""
        waiter = self._active_shell_waiter
        if waiter is None:
            return
        from .shell_approval_modal import ShellApprovalScreen

        waiter.dismiss_screen(ShellApprovalScreen, result)

    def cancel_ask_user_question(self, payload: str) -> None:
        """Unblock a pending ask-user flow with a JSON payload from web remote."""
        waiter = self._active_ask_waiter
        if waiter is None:
            return

        def _go() -> None:
            if self._app._ask_user.active:
                self._app._ask_user.finish_with(payload)
            else:
                waiter.deliver(payload)

        self._app.call_from_thread(_go)

    def cancel_text_input(self, result: str | None) -> None:
        """Unblock a pending text input modal (e.g. answered on web remote)."""
        waiter = self._active_input_waiter
        if waiter is None:
            return
        from .text_input_modal import TextInputScreen

        waiter.dismiss_screen(TextInputScreen, result)

    def prompt_shell_approval(self, cmd: str) -> str:
        """Block (from worker thread) until the user approves a shell command.

        Returns one of: ``y`` (run), ``n`` (deny), ``a`` (always approve for session)
        — same contract as the Rich REPL ``approve? [Y/n/a]`` prompt.
        """
        from .shell_approval_modal import ShellApprovalScreen

        waiter = _PromptWaiter(self._app)
        self._active_shell_waiter = waiter

        def on_done(result: str | None) -> None:
            if result is None:
                r = "n"
            else:
                r = str(result).strip().lower() or "y"
            waiter.deliver(r)

        def push() -> None:
            self._app.push_screen(ShellApprovalScreen(cmd), on_done)

        self._app.call_from_thread(push)
        try:
            out = waiter.wait()
            from .. import state as _state
            if _state.cancel_requested.is_set():
                raise KeyboardInterrupt()
            return out if isinstance(out, str) and out else "n"
        finally:
            self._active_shell_waiter = None

    def prompt_ask_user_question(self, questions) -> str:
        """Block until the user answers structured multiple-choice questions.

        Returns JSON from ``ask_user_question`` (answers + optional cancelled).
        """
        import json

        waiter = _PromptWaiter(self._app)
        self._active_ask_waiter = waiter

        def on_done(result: str | None) -> None:
            waiter.deliver(result if result is not None else "")

        def push() -> None:
            self._app.begin_ask_user_question(questions, on_done)

        self._app.call_from_thread(push)
        try:
            out = waiter.wait()
            if out is None or out == "":
                return json.dumps({"answers": [], "cancelled": True})
            return str(out)
        finally:
            self._active_ask_waiter = None

    def input(self, prompt: str = "", *, password: bool = False, **kwargs) -> str:  # noqa: D401
        """Show a text input modal and return the entered text.

        Can only be called from a worker thread (not the main Textual thread)
        because it blocks with a Queue. Raises ``EOFError`` if the user cancels.

        The prompt text is printed to the transcript before the modal opens.
        """
        if threading.current_thread() is threading.main_thread():
            raise RuntimeError(
                "TUIConsole.input cannot be called from the main thread; "
                "use the Input widget or route through _run_turn instead."
            )

        # Print the prompt to the transcript first
        if prompt:
            self.print(prompt, end="")

        from .text_input_modal import TextInputScreen

        waiter = _PromptWaiter(self._app)
        self._active_input_waiter = waiter

        def on_done(result: str | None) -> None:
            waiter.deliver(result)

        placeholder = "(paste here)"
        if password:
            placeholder = "(password, hidden)"

        def push() -> None:
            self._app.push_screen(
                TextInputScreen(
                    title="Input required",
                    body=prompt,
                    placeholder=placeholder,
                    password=password,
                ),
                on_done,
            )

        self._app.call_from_thread(push)
        try:
            result = waiter.wait()
        finally:
            self._active_input_waiter = None

        if result is None:
            raise EOFError("Input cancelled")
        return str(result)
