"""Console wrapper that mirrors TUI output and prompts to browser clients."""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from .bridge import WebBridge
from .plaintext import to_plain


def _show_thinking_to_web() -> bool:
    from .. import state

    return bool(state.show_internal)


def tool_row_fields(name: str, tool_input: Any, output: Any = None) -> dict[str, Any]:
    """Readable ``title`` / ``args`` (+ ``summary`` once finished) for a web tool row.

    Same wording as the TUI rows (``tui/tool_format``) so both views agree.
    """
    fields: dict[str, Any] = {}
    try:
        from ..tui.tool_format import tool_args, tool_summary, tool_title

        fields["title"] = tool_title(name or "tool")
        fields["args"] = tool_args(name, tool_input, 120) if tool_input is not None else ""
        if output is not None:
            lines, is_err = tool_summary(name, tool_input, str(output or ""), 120)
            fields["summary"] = "\n".join(lines[:4])
            fields["summary_error"] = bool(is_err)
    except Exception:
        fields.setdefault("title", name or "tool")
    return fields


def _norm_shell_result(result: Any) -> str:
    if result is None:
        return "n"
    return str(result).strip().lower() or "y"


class WebMuxConsole:
    """Delegates to TUIConsole while broadcasting events to WebBridge."""

    def __init__(self, primary: Any, bridge: WebBridge) -> None:
        self._primary = primary
        self._bridge = bridge
        self._stream_kind: str | None = None
        self._stream_buffer = ""
        self._thinking_committed = False
        self._broadcast_suppressed = 0
        # Thinking texts already sent — the same block reaches us from both
        # thinking_stream_finalize and assistant_stream_commit.
        self._sent_thinking: list[str] = []

    @contextmanager
    def suppress_broadcast(self):
        """Pause log/rule forwarding to web clients (TUI output still runs)."""
        self._broadcast_suppressed += 1
        try:
            yield
        finally:
            self._broadcast_suppressed -= 1

    def _should_broadcast(self) -> bool:
        return self._broadcast_suppressed <= 0

    def _emit_thinking(self, text: str) -> None:
        body = (text or "").strip()
        if not body or body in self._sent_thinking:
            return
        self._sent_thinking = (self._sent_thinking + [body])[-16:]
        self._bridge.emit("message", {"role": "thinking", "text": body})

    def _reset_stream_state(self) -> None:
        self._stream_kind = None
        self._stream_buffer = ""

    def _web_connected(self) -> bool:
        return self._bridge.has_subscribers()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._primary, name)

    def print(self, *objects: Any, sep: str = " ", end: str = "\n", **kwargs) -> None:
        self._primary.print(*objects, sep=sep, end=end, **kwargs)
        if not self._should_broadcast():
            return
        text = to_plain(*objects, sep=sep)
        if end and end != "\n":
            text += end
        if text.strip():
            self._bridge.emit("log", {"text": text})

    def rule(self, title: str = "", *, style: str = "rule.line", **kwargs) -> None:
        self._primary.rule(title, style=style, **kwargs)
        if not self._should_broadcast():
            return
        label = title or "—"
        self._bridge.emit("log", {"text": f"── {label} ──"})

    @contextmanager
    def status(self, message: str = "", **kwargs):
        self._bridge.emit("status", {"text": message})
        with self._primary.status(message, **kwargs):
            yield self

    def thinking_stream_start(self) -> None:
        self._stream_kind = "thinking"
        self._stream_buffer = ""
        self._thinking_committed = False
        self._primary.thinking_stream_start()
        if _show_thinking_to_web():
            self._bridge.emit("stream_start", {"kind": "thinking"})

    def thinking_stream_push(self, chunk: str) -> None:
        if chunk:
            self._stream_buffer += chunk
            if _show_thinking_to_web():
                self._bridge.emit("stream_delta", {"kind": "thinking", "chunk": chunk})
        self._primary.thinking_stream_push(chunk)

    def thinking_stream_flush(self) -> None:
        self._primary.thinking_stream_flush()

    def thinking_stream_finalize(self) -> None:
        self._primary.thinking_stream_finalize()
        if _show_thinking_to_web() and self._stream_buffer.strip():
            self._emit_thinking(self._stream_buffer)
            self._thinking_committed = True
        if _show_thinking_to_web():
            self._bridge.emit("stream_end", {"kind": "thinking"})
        self._reset_stream_state()

    def assistant_stream_start(self, title: str) -> None:
        self._stream_kind = "assistant"
        self._stream_buffer = ""
        self._thinking_committed = False
        self._primary.assistant_stream_start(title)
        self._bridge.emit("stream_start", {"kind": "assistant", "title": title})

    def assistant_stream_push(self, chunk: str) -> None:
        if chunk:
            self._stream_buffer += chunk
            self._bridge.emit("stream_delta", {"kind": "assistant", "chunk": chunk})
        self._primary.assistant_stream_push(chunk)

    def assistant_stream_flush(self) -> None:
        self._primary.assistant_stream_flush()

    def assistant_stream_commit(
        self,
        text: str,
        title: str,
        was_flagged: bool,
        thinking_blocks: list[str] | None = None,
    ) -> None:
        self._primary.assistant_stream_commit(text, title, was_flagged, thinking_blocks)
        if _show_thinking_to_web() and thinking_blocks:
            for block in thinking_blocks:
                self._emit_thinking(block)
        if text.strip():
            self._bridge.emit(
                "message",
                {"role": "assistant", "title": title, "text": text.strip()},
            )
        self._bridge.emit("stream_end", {"kind": "assistant"})
        self._reset_stream_state()
        self._thinking_committed = False

    def assistant_stream_abort(self) -> None:
        self._primary.assistant_stream_abort()
        self._bridge.emit("stream_end", {"kind": self._stream_kind or "assistant", "aborted": True})
        self._reset_stream_state()
        self._thinking_committed = False

    def report_turn_phase(self, label: str) -> None:
        self._primary.report_turn_phase(label)
        self._bridge.emit("activity", {"label": label})

    # Hooks the TUI renders natively — mirror them to web clients too (the
    # web UI used to get these as printed panels).
    def file_diff(self, path: str, before: str, after: str, action: str = "edit") -> None:
        fn = getattr(self._primary, "file_diff", None)
        if callable(fn):
            fn(path, before, after, action)
        if not self._should_broadcast():
            return
        import difflib

        body = [
            ln for ln in difflib.unified_diff(
                before.splitlines(), after.splitlines(), lineterm="", n=2
            )
            if not ln.startswith(("---", "+++"))
        ]
        if body:
            added = sum(1 for ln in body if ln.startswith("+"))
            removed = sum(1 for ln in body if ln.startswith("-"))
            try:
                from ..tui.tool_format import short_path

                shown = short_path(path) or path
            except Exception:
                shown = path
            self._bridge.emit("diff", {
                "path": shown,
                "action": action,
                "lines": body[:160],
                "hidden": max(0, len(body) - 160),
                "added": added,
                "removed": removed,
            })

    def show_thinking(self, text: str) -> None:
        fn = getattr(self._primary, "show_thinking", None)
        if callable(fn):
            fn(text)
        if _show_thinking_to_web():
            self._emit_thinking(text)

    def show_plan(self, plan: str) -> None:
        fn = getattr(self._primary, "show_plan", None)
        if callable(fn):
            fn(plan)
        if (plan or "").strip():
            self._bridge.emit("message", {"role": "assistant", "title": "proposed plan", "text": plan.strip()})

    def show_reply(self, text: str, flagged: bool = False) -> None:
        fn = getattr(self._primary, "show_reply", None)
        if callable(fn):
            fn(text, flagged)
        if (text or "").strip():
            self._bridge.emit("message", {"role": "assistant", "title": "jarvis", "text": text.strip()})

    def emit_tool_event(self, event_type: str, data: dict[str, Any]) -> None:
        data = data or {}
        fn = getattr(self._primary, "emit_tool_event", None)
        if callable(fn):
            fn(event_type, data)
        slim = {k: v for k, v in data.items() if k not in ("input", "output")}
        slim.update(tool_row_fields(
            str(data.get("name") or ""),
            data.get("input"),
            data.get("output") if event_type == "tool_done" else None,
        ))
        if event_type == "tool_done" and slim.pop("summary_error", False):
            slim["error"] = True
        self._bridge.emit(event_type, slim)

    def refresh_tool_activity(self) -> None:
        self._primary.refresh_tool_activity()

    refresh_tool_dock = refresh_tool_activity

    def reset_tool_activity_panel(self) -> None:
        self._primary.reset_tool_activity_panel()

    def _dual_channel_prompt(
        self,
        *,
        kind: str,
        web_payload: dict[str, Any],
        tui_call: Callable[[], Any],
        web_cancel: Callable[[Any], None],
    ) -> Any:
        """Show the same prompt on TUI and web; whichever answers first wins."""
        if not self._web_connected():
            return tui_call()

        prompt_id = self._bridge.new_prompt(kind, web_payload)
        web_answer: list[Any] = []
        web_finished = threading.Event()

        def wait_web() -> None:
            try:
                answer = self._bridge.wait_prompt(prompt_id, timeout=3600.0)
                web_answer.append(answer)
                if answer is not None:
                    web_cancel(answer)
            finally:
                web_finished.set()

        threading.Thread(target=wait_web, daemon=True).start()
        try:
            result = tui_call()
            if not web_finished.is_set() or (web_answer and web_answer[0] is None):
                self._bridge.dismiss_prompt(prompt_id)
            return result
        except EOFError:
            if not web_finished.is_set():
                self._bridge.dismiss_prompt(prompt_id)
            raise

    def prompt_shell_approval(self, cmd: str) -> str:
        return _norm_shell_result(
            self._dual_channel_prompt(
                kind="shell_approval",
                web_payload={"cmd": cmd},
                tui_call=lambda: self._primary.prompt_shell_approval(cmd),
                web_cancel=lambda answer: self._primary.cancel_shell_approval(
                    _norm_shell_result(answer)
                ),
            )
        )

    def prompt_ask_user_question(self, questions) -> str:
        from ..tui.ask_user import AskQuestion, questions_to_payload

        if questions and isinstance(questions[0], AskQuestion):
            qs_payload = questions_to_payload(questions)
        else:
            qs_payload = questions
        payload = {"questions": qs_payload}
        default = json.dumps({"answers": [], "cancelled": True})

        def _web_cancel(answer: Any) -> None:
            if isinstance(answer, dict):
                text = json.dumps(answer, ensure_ascii=False)
            else:
                text = str(answer)
            self._primary.cancel_ask_user_question(text)

        result = self._dual_channel_prompt(
            kind="ask_user",
            web_payload=payload,
            tui_call=lambda: self._primary.prompt_ask_user_question(questions),
            web_cancel=_web_cancel,
        )
        return result if isinstance(result, str) else default

    def input(self, prompt: str = "", *, password: bool = False, **kwargs) -> str:
        return self._dual_channel_prompt(
            kind="text_input",
            web_payload={"prompt": prompt, "password": password},
            tui_call=lambda: self._primary.input(prompt, password=password, **kwargs),
            web_cancel=lambda answer: self._primary.cancel_text_input(
                None if answer is None else str(answer)
            ),
        )
