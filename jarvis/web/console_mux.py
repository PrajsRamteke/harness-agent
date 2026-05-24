"""Console wrapper that mirrors TUI output and prompts to browser clients."""
from __future__ import annotations

import json
import queue
import threading
from contextlib import contextmanager
from typing import Any

from .bridge import WebBridge
from .plaintext import to_plain


def _show_thinking_to_web() -> bool:
    from .. import state

    return bool(state.show_internal)


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
            self._bridge.emit(
                "message",
                {"role": "thinking", "text": self._stream_buffer.strip()},
            )
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
        if _show_thinking_to_web() and thinking_blocks and not self._thinking_committed:
            for block in thinking_blocks:
                if block.strip():
                    self._bridge.emit("message", {"role": "thinking", "text": block.strip()})
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

    def refresh_tool_activity(self) -> None:
        self._primary.refresh_tool_activity()

    refresh_tool_dock = refresh_tool_activity

    def reset_tool_activity_panel(self) -> None:
        self._primary.reset_tool_activity_panel()

    def prompt_shell_approval(self, cmd: str) -> str:
        if self._web_connected():
            prompt_id = self._bridge.new_prompt("shell_approval", {"cmd": cmd})
            try:
                return _norm_shell_result(self._bridge.wait_prompt(prompt_id, timeout=3600.0))
            finally:
                self._bridge.dismiss_prompt(prompt_id)
        return self._primary.prompt_shell_approval(cmd)

    def prompt_ask_user_question(self, questions) -> str:
        if self._web_connected():
            from ..tui.ask_user import AskQuestion, questions_to_payload

            if questions and isinstance(questions[0], AskQuestion):
                qs_payload = questions_to_payload(questions)
            else:
                qs_payload = questions
            payload = {"questions": qs_payload}
            prompt_id = self._bridge.new_prompt("ask_user", payload)
            default = json.dumps({"answers": [], "cancelled": True})
            try:
                result = self._bridge.wait_prompt(prompt_id, timeout=3600.0)
                return result if isinstance(result, str) else default
            finally:
                self._bridge.dismiss_prompt(prompt_id)
        return self._primary.prompt_ask_user_question(questions)

    def input(self, prompt: str = "", *, password: bool = False, **kwargs) -> str:
        if self._web_connected():
            prompt_id = self._bridge.new_prompt(
                "text_input",
                {"prompt": prompt, "password": password},
            )
            try:
                result = self._bridge.wait_prompt(prompt_id, timeout=3600.0)
                if result is None:
                    raise EOFError("Input cancelled")
                return str(result)
            finally:
                self._bridge.dismiss_prompt(prompt_id)
        return self._primary.input(prompt, password=password, **kwargs)
