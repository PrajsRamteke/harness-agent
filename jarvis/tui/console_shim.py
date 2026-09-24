"""Rich-Console-compatible shim that routes output into the TUI transcript.

Everything the agent loop and slash commands print lands here:

* ``print`` / ``rule`` / ``clear`` — generic output → ``NoticeBlock``
* ``assistant_stream_*`` — live reply → ``AssistantBlock`` (streaming markdown)
* ``thinking_stream_*`` — reasoning deltas → ``ThinkingBlock``
* ``emit_tool_event`` — tool start/done → ``ToolBlock`` rows
* ``file_diff`` — edit/write diffs → ``DiffBlock`` under the tool row
* ``prompt_*`` / ``input`` — blocking prompts for worker threads

Streaming is decoupled from rendering: worker threads only append deltas
to a lock-protected buffer; the app's UI pump (``pump()``, ~24×/s while
busy) drains them into the widgets. The network thread never waits on a
repaint, and a burst of tokens costs one markdown update, not hundreds.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from contextlib import contextmanager
from typing import Any

from rich.console import Console as _RichConsole
from rich.text import Text

_log = logging.getLogger("jarvis.tui")


def _safe_from_markup(text: str) -> Text:
    """Parse Rich markup, falling back to plain text on malformed tags."""
    try:
        return Text.from_markup(text)
    except Exception:
        return Text(text)


def _at_bottom(log) -> bool:
    """True when the user hasn't scrolled up (kept for callers/tests)."""
    try:
        return bool(log.following)
    except Exception:
        try:
            return bool(log.is_vertical_scroll_end)
        except Exception:
            return True


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
            if state.turn_cancelled():
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

        if threading.current_thread() is threading.main_thread():
            _go()  # call_from_thread raises on the UI thread
        else:
            self._app.call_from_thread(_go)


class TUIConsole:
    # Tells shared code (render.py, stream.py, banners.py) that this console
    # draws tool rows / welcome itself, so the legacy verbose prints are skipped.
    renders_tool_rows = True

    def __init__(self, app, log_widget, status_widget=None):
        self._app = app
        self._log = log_widget  # the Transcript
        self._status = status_widget
        self._renderer = _RichConsole(file=None, record=False, width=120)
        self._lock = threading.Lock()
        # assistant stream (a long reply spans a chain of continuation blocks)
        self._as_owner: int | None = None
        self._th_owner: int | None = None
        self._as_block = None
        self._as_chain: list = []
        self._as_pending: list[str] = []
        self._as_title = ""
        self._as_buffer = ""
        # thinking stream
        self._th_block = None
        self._th_pending: list[str] = []
        self._th_buffer = ""
        self._th_streamed = False
        # rough output-token estimate for the activity line (chars / 4)
        self.stream_chars = 0
        # tool rows by tool_use id
        self._tools: dict[str, Any] = {}
        # files edited this session → (lines added, lines removed) for the sidebar
        self.changed_files: dict[str, tuple[int, int]] = {}
        # live slash-command block (/upgrade …)
        self._cmd_block = None
        self._active_shell_waiter: _PromptWaiter | None = None
        self._active_ask_waiter: _PromptWaiter | None = None
        self._active_input_waiter: _PromptWaiter | None = None

    # ─── thread helpers ────────────────────────────────────────────────
    def _on_ui(self, fn, *args):
        """Run ``fn`` on the UI thread and wait for it (direct if already there).

        Coroutine functions invoked from the UI thread are scheduled as a task.
        """
        if threading.current_thread() is threading.main_thread() or not self._app.is_running:
            try:
                result = fn(*args)
            except Exception:
                _log.exception("TUI update failed: %r", fn)
                return None
            if asyncio.iscoroutine(result):
                try:
                    return asyncio.ensure_future(result)
                except RuntimeError:
                    result.close()
                    return None
            return result
        try:
            return self._app.call_from_thread(fn, *args)
        except Exception:
            _log.exception("TUI update failed (from worker): %r", fn)
            return None

    def _transcript(self):
        return self._log

    # ─── stream ownership ─────────────────────────────────────────────
    # The worker that starts a stream owns it. After Esc the UI may start the
    # next turn while the cancelled worker is still unwinding; deltas, commits
    # and aborts from that stale thread must not touch the new turn's blocks.
    def _owns(self, owner: int | None) -> bool:
        me = threading.get_ident()
        return me == owner or threading.current_thread() is threading.main_thread()

    # ─── UI pump (called on the UI thread by the app timer) ────────────
    async def pump(self) -> None:
        from .transcript import ThinkingBlock

        with self._lock:
            th = "".join(self._th_pending)
            self._th_pending.clear()
            asst = "".join(self._as_pending)
            self._as_pending.clear()
        if th:
            if self._th_block is None:
                # Reasoning that resumes after reply text in the same stream.
                self._th_block = self._transcript().add(ThinkingBlock(live=True))
            self._th_block.append(th)
        if asst and self._as_block is not None:
            try:
                blk = self._as_block
                blk.append(asst)
                if blk.should_split():
                    nxt = blk.split_off()
                    self._transcript().add(nxt, after=blk)
                    self._as_chain.append(nxt)
                    self._as_block = nxt
            except Exception:
                _log.exception("stream pump failed")
        if self._as_block is not None:
            self._as_block.tick_pulse()
        if self._th_block is not None:
            self._th_block.tick_pulse()

    # ─── thinking streaming ────────────────────────────────────────────
    def thinking_stream_start(self) -> None:
        from .. import state as _state
        from .transcript import ThinkingBlock

        self._th_owner = threading.get_ident()
        with self._lock:
            self._th_pending.clear()
        self._th_buffer = ""
        self._th_streamed = True
        _state._thinking_stream_ui_active = True

        def _start() -> None:
            self._finish_thinking_ui()
            self._th_block = self._transcript().add(ThinkingBlock(live=True))

        self._on_ui(_start)

    def thinking_stream_push(self, chunk: str) -> None:
        if not chunk or threading.get_ident() != self._th_owner:
            return
        self._th_buffer += chunk
        self.stream_chars += len(chunk)
        with self._lock:
            self._th_pending.append(chunk)

    def thinking_stream_flush(self) -> None:
        """Deltas are drained by the UI pump; nothing to force here."""

    def _finish_thinking_ui(self) -> None:
        with self._lock:
            rest = "".join(self._th_pending)
            self._th_pending.clear()
        blk, self._th_block = self._th_block, None
        if blk is not None:
            if rest:
                blk.append(rest)
            if not blk.text.strip():
                blk.remove()
            else:
                blk.finish()

    def thinking_stream_finalize(self) -> None:
        from .. import state as _state

        if not self._owns(self._th_owner):
            return
        self._on_ui(self._finish_thinking_ui)
        # Stays True while this iteration's reasoning is on screen, so
        # render_assistant doesn't draw the same thinking a second time.
        _state._thinking_stream_ui_active = bool(self._th_buffer.strip())

    def show_thinking(self, text: str) -> None:
        """Render a complete (non-streamed) reasoning block."""
        from .transcript import ThinkingBlock

        if text and text.strip():
            self._on_ui(lambda: self._transcript().add(ThinkingBlock(text.strip())))

    def show_reply(self, text: str, flagged: bool = False) -> None:
        """Render a complete (non-streamed) reply as a normal reply block."""
        from .transcript import AssistantBlock

        def _go() -> None:
            blk = self._transcript().add(AssistantBlock(text))
            if flagged:
                blk.add_class("-flagged")

        if text and text.strip():
            self._on_ui(_go)

    def show_plan(self, plan: str) -> None:
        """Render a plan-mode proposal as a framed plan card."""
        from .transcript import PlanBlock

        if plan and plan.strip():
            self._on_ui(lambda: self._transcript().add(PlanBlock(plan.strip())))

    def thinking_stream_reset(self) -> None:
        """Forget thinking state at the start of a stream iteration."""
        from .. import state as _state

        self._th_owner = threading.get_ident()
        self._th_buffer = ""
        self._th_streamed = False
        _state._thinking_stream_ui_active = False
        self._on_ui(self._finish_thinking_ui)

    # ─── assistant streaming ───────────────────────────────────────────
    def assistant_stream_start(self, title: str) -> None:
        from .transcript import AssistantBlock

        self._as_owner = threading.get_ident()
        self._as_title = title
        self._as_buffer = ""
        with self._lock:
            self._as_pending.clear()

        def _start() -> None:
            self._as_block = self._transcript().add(AssistantBlock(streaming=True))
            self._as_chain = [self._as_block]

        self._on_ui(_start)

    def assistant_stream_push(self, chunk: str) -> None:
        if not chunk or threading.get_ident() != self._as_owner:
            return
        self._as_buffer += chunk
        self.stream_chars += len(chunk)
        with self._lock:
            self._as_pending.append(chunk)

    def assistant_stream_flush(self) -> None:
        """Deltas are drained by the UI pump; nothing to force here."""

    def assistant_stream_commit(self, text: str, title: str, was_flagged: bool,
                                thinking_blocks: list[str] | None = None) -> None:
        """Finalize the live reply; render non-streamed thinking above it."""
        from .. import state as _state
        from .transcript import AssistantBlock, ThinkingBlock

        if not self._owns(self._as_owner):
            return
        streamed_thinking = self._th_streamed

        def _commit() -> None:
            self._finish_thinking_ui()
            with self._lock:
                rest = "".join(self._as_pending)
                self._as_pending.clear()
            blk, self._as_block = self._as_block, None
            chain, self._as_chain = self._as_chain, []
            if blk is not None and rest:
                blk.append(rest)
            head = chain[0] if chain else blk
            if thinking_blocks and not streamed_thinking:
                for tb in thinking_blocks:
                    if tb.strip():
                        self._transcript().add(ThinkingBlock(tb), before=head)
            if not text.strip():
                for b in chain or ([blk] if blk is not None else []):
                    b.remove()
                return
            if head is None:
                head = self._transcript().add(AssistantBlock(text))
            elif len(chain) > 1:
                streamed = "\n\n".join(b.text.strip("\n") for b in chain)
                if streamed.strip() != text.strip():
                    # Final text differs from what streamed (scrubbed) —
                    # collapse the chain back into one block.
                    for b in chain[1:]:
                        b.remove()
                    head.finalize(text)
                else:
                    blk.finalize()
            else:
                head.finalize(text)
            if was_flagged:
                head.add_class("-flagged")

        self._on_ui(_commit)
        self._as_buffer = ""
        self._th_buffer = ""
        self._th_streamed = False
        _state._assistant_stream_ui_active = False
        _state._thinking_stream_ui_active = False

    def assistant_stream_abort(self) -> None:
        """Stop a live stream IN PLACE (cancel / error) — nothing is deleted.

        The partial reply keeps everything streamed so far (including the
        not-yet-drained tail) and gains an "interrupted" marker; an empty
        placeholder is dropped. Runs synchronously on the UI thread, so the
        next turn can never grab (or lose) this turn's block.
        """
        from .. import state as _state

        if not self._owns(self._as_owner):
            return

        def _abort() -> None:
            self._finish_thinking_ui()
            with self._lock:
                rest = "".join(self._as_pending)
                self._as_pending.clear()
            blk, self._as_block = self._as_block, None
            chain, self._as_chain = self._as_chain, []
            if blk is None:
                return
            if rest:
                blk.append(rest)
            if blk.text.strip():
                blk.finalize(interrupted=True)
            elif len(chain) > 1:
                blk.remove()
                chain[-2].finalize(interrupted=True)
            else:
                blk.remove()

        self._on_ui(_abort)
        self._as_buffer = ""
        _state._assistant_stream_ui_active = False
        _state._thinking_stream_ui_active = False

    def reset_stream_ui(self) -> None:
        """Drop all stream bookkeeping at the start of a turn (worker thread)."""
        from .. import state as _state

        me = threading.get_ident()
        self._as_owner = me
        self._th_owner = me
        with self._lock:
            self._as_pending.clear()
            self._th_pending.clear()
        self._as_buffer = ""
        self._th_buffer = ""
        self._th_streamed = False
        self.stream_chars = 0
        self._as_block = None
        self._as_chain = []
        self._th_block = None
        _state._assistant_stream_ui_active = False
        _state._thinking_stream_ui_active = False

    # ─── tool rows ─────────────────────────────────────────────────────
    def emit_tool_event(self, event_type: str, data: dict[str, Any]) -> None:
        data = data or {}
        if event_type == "tool_start":
            self._on_ui(self._tool_start, data)
        elif event_type == "tool_done":
            self._on_ui(self._tool_done, data)

    def _tool_start(self, data: dict) -> None:
        from .transcript import ToolBlock

        tid = str(data.get("id") or "")
        if not tid:
            return
        old = self._tools.get(tid)
        if old is not None and old.status == "running":
            return
        # (a finished row with the same id = provider reusing ids → new row)
        blk = ToolBlock(tid, str(data.get("name") or "tool"), data.get("input"))
        self._tools[tid] = blk
        self._transcript().add(blk)

    def _tool_done(self, data: dict) -> None:
        tid = str(data.get("id") or "")
        blk = self._tools.get(tid)
        if blk is None or blk.status != "running":
            # Late result from a cancelled / forgotten turn — no stray rows.
            return
        blk.finish(
            str(data.get("output") or ""),
            error=bool(data.get("error")) or None,
            repaired=bool(data.get("repaired")),
        )

    def cancel_running_tools(self) -> None:
        def _go() -> None:
            for blk in self._tools.values():
                blk.cancel()

        self._on_ui(_go)

    def running_tool_blocks(self) -> list:
        """Rows that still animate (spinner or finish flash)."""
        return [b for b in self._tools.values() if b.animating]

    def forget_tools(self) -> None:
        self._tools.clear()

    def file_diff(self, path: str, before: str, after: str, action: str = "edit") -> None:
        """Show a diff under the matching running edit/write row.

        The diff itself (difflib + path resolution) is computed here, on the
        calling worker thread; only the finished rows cross to the UI.
        """
        from ..path_resolve import robust_resolve
        from .transcript import DiffBlock, ToolBlock, diff_rows
        from .tool_format import short_path

        rows, added, removed, hidden = diff_rows(before, after, 60)
        if not rows:
            return

        def _res(p: str):
            try:
                return robust_resolve(p)
            except Exception:
                return None

        target = _res(path)
        key = short_path(path)

        def _go() -> None:
            host = None
            for blk in reversed(list(self._tools.values())):
                if blk.tool_name not in ("write_file", "edit_file", "multi_edit") or blk.status != "running":
                    continue
                inp = blk.tool_input if isinstance(blk.tool_input, dict) else {}
                paths = [str(inp["path"])] if inp.get("path") else []
                paths += [str(e["path"]) for e in inp.get("edits") or []
                          if isinstance(e, dict) and e.get("path")]
                if any(p == path or (target is not None and _res(p) == target) for p in paths):
                    host = blk
                    break
            diff = DiffBlock(path, rows=rows, added=added, removed=removed, hidden=hidden, action=action)
            a0, r0 = self.changed_files.pop(key, (0, 0))
            self.changed_files[key] = (a0 + added, r0 + removed)
            t = self._transcript()
            if isinstance(host, ToolBlock) and host.parent is t:
                anchor = host.attached[-1] if host.attached else host
                t.add(diff, after=anchor)
                host.attached.append(diff)
                host.add_diff_stats(added, removed)
            else:
                diff.add_class("-tight")
                t.add(diff)

        self._on_ui(_go)

    # Legacy parallel-files dock hooks — tool rows replace the dock panel.
    def refresh_tool_activity(self) -> None:
        return None

    refresh_tool_dock = refresh_tool_activity

    def reset_tool_activity_panel(self) -> None:
        return None

    # ─── slash-command progress (/upgrade) ─────────────────────────────
    def start_command_progress(self, command: str, *, phase: str = "upgrading…") -> None:
        from .transcript import UserBlock

        def _go() -> None:
            blk = UserBlock((command or "").strip())
            blk.phase = phase
            self._cmd_block = self._transcript().add(blk)

        self._on_ui(_go)

    def update_command_progress(self, phase: str) -> None:
        phase = (phase or "").strip()
        blk = self._cmd_block
        if not phase or blk is None or phase == blk.phase:
            return
        self._on_ui(blk.set_phase, phase)

    def tick_command_progress_spinner(self) -> None:
        return None  # UserBlock.tick is driven by the app's spinner timer

    def finish_command_progress(self) -> None:
        blk, self._cmd_block = self._cmd_block, None
        if blk is not None:
            self._on_ui(blk.set_phase, "")

    # ─── activity line ────────────────────────────────────────────────
    def report_turn_phase(self, label: str) -> None:
        """Update the activity line (spinner + phase + clock). Any thread."""
        app = self._app
        if not hasattr(app, "_sync_activity_phase"):
            return

        def _go() -> None:
            app._sync_activity_phase(label)
            self.update_command_progress(label)

        self._on_ui(_go)

    # ─── welcome ──────────────────────────────────────────────────────
    def show_welcome(self) -> None:
        fn = getattr(self._app, "_mount_welcome", None)
        if callable(fn):
            self._on_ui(fn)

    # ─── Rich.Console surface ──────────────────────────────────────────
    def _write(self, renderable) -> None:
        self._on_ui(self._transcript().write, renderable)

    def _terminal_width(self) -> int:
        try:
            return max(24, int(self._log.scrollable_content_region.width or self._log.size.width))
        except Exception:
            return 80

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
        from rich.rule import Rule

        self._write(Rule(title=title, style=style))

    @contextmanager
    def status(self, message: str = "", **kwargs):
        app = self._app
        prev = getattr(app, "_activity_label", "")
        if message and hasattr(app, "_sync_activity_phase"):
            self._on_ui(app._sync_activity_phase, _safe_from_markup(message).plain)
        try:
            yield self
        finally:
            if hasattr(app, "_sync_activity_phase"):
                self._on_ui(app._sync_activity_phase, prev)

    def clear(self, *args, **kwargs):
        def _go() -> None:
            self._tools.clear()
            self._log.clear()

        self._on_ui(_go)

    # ─── prompts ──────────────────────────────────────────────────────
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

        Returns one of: ``y`` (run), ``n`` (deny), ``a`` (always approve for session).
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
            if _state.turn_cancelled():
                raise KeyboardInterrupt()
            return out if isinstance(out, str) and out else "n"
        finally:
            self._active_shell_waiter = None

    def prompt_ask_user_question(self, questions) -> str:
        """Block until the user answers structured multiple-choice questions."""
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
        """Show a text input modal and return the entered text (worker threads only).

        Raises ``EOFError`` if the user cancels.
        """
        if threading.current_thread() is threading.main_thread():
            raise RuntimeError(
                "TUIConsole.input cannot be called from the main thread; "
                "use the Input widget or route through _run_turn instead."
            )

        if prompt:
            self.print(prompt, end="")

        from .text_input_modal import TextInputScreen

        waiter = _PromptWaiter(self._app)
        self._active_input_waiter = waiter

        def on_done(result: str | None) -> None:
            waiter.deliver(result)

        placeholder = "(password, hidden)" if password else "(paste here)"

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
