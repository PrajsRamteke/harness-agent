"""Event bus bridging the TUI session to browser clients."""
from __future__ import annotations

import json
import queue
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PendingPrompt:
    prompt_id: str
    kind: str
    payload: dict
    result_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1))
    resolved: bool = False


class WebBridge:
    """Thread-safe fan-out of session events to SSE subscribers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: list[queue.Queue[str]] = []
        self._history: list[dict[str, Any]] = []
        self._pending: dict[str, PendingPrompt] = {}
        self.token = secrets.token_urlsafe(18)
        self._on_submit: Callable[[str], None] | None = None
        self._on_cancel: Callable[[], None] | None = None
        self._on_settings: Callable[[dict[str, Any], Callable[[dict[str, Any]], None]], None] | None = None
        self._on_action: Callable[[str, dict[str, Any], Callable[[dict[str, Any]], None]], None] | None = None

    def set_handlers(
        self,
        *,
        on_submit: Callable[[str], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        on_settings: Callable[[dict[str, Any], Callable[[dict[str, Any]], None]], None] | None = None,
        on_action: Callable[[str, dict[str, Any], Callable[[dict[str, Any]], None]], None] | None = None,
    ) -> None:
        self._on_submit = on_submit
        self._on_cancel = on_cancel
        self._on_settings = on_settings
        self._on_action = on_action

    def submit_prompt(self, text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        if self._on_submit is None:
            return False
        self._on_submit(text)
        return True

    def cancel_turn(self) -> bool:
        if self._on_cancel is None:
            return False
        self._on_cancel()
        return True

    def request_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        """Apply settings on the TUI main thread when a handler is wired."""
        if self._on_settings is None:
            from .state_api import apply_settings
            return apply_settings(data)
        result_q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

        def done(result: dict[str, Any]) -> None:
            try:
                result_q.put(result, timeout=5.0)
            except queue.Full:
                pass

        self._on_settings(data, done)
        try:
            return result_q.get(timeout=8.0)
        except queue.Empty:
            return {}

    def request_action(self, action: str, data: dict[str, Any]) -> dict[str, Any]:
        """Run a picker mutation on the TUI main thread when wired."""
        if self._on_action is None:
            from .actions_api import run_web_action
            from ..console import console

            return run_web_action(action, data, console_print=console.print)
        result_q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

        def done(result: dict[str, Any]) -> None:
            try:
                result_q.put(result, timeout=5.0)
            except queue.Full:
                pass

        self._on_action(action, data, done)
        try:
            return result_q.get(timeout=60.0)
        except queue.Empty:
            return {"ok": False, "error": "timeout"}

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        evt = {"type": event_type, "data": data or {}, "ts": time.time()}
        with self._lock:
            self._history.append(evt)
            if len(self._history) > 600:
                self._history = self._history[-500:]
            subs = list(self._subscribers)
        line = json.dumps(evt, ensure_ascii=False)
        for sub in subs:
            try:
                sub.put_nowait(line)
            except queue.Full:
                pass

    def has_subscribers(self) -> bool:
        with self._lock:
            return bool(self._subscribers)

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)

    def pending_events(self) -> list[dict[str, Any]]:
        """Unresolved prompt events for reconnecting web clients."""
        with self._lock:
            pending = [p for p in self._pending.values() if not p.resolved]
        events: list[dict[str, Any]] = []
        for p in pending:
            events.append({
                "type": p.kind,
                "data": {"id": p.prompt_id, **p.payload},
                "ts": time.time(),
            })
        return events

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def new_prompt(self, kind: str, payload: dict[str, Any]) -> str:
        prompt_id = uuid.uuid4().hex
        pending = PendingPrompt(prompt_id=prompt_id, kind=kind, payload=payload)
        with self._lock:
            self._pending[prompt_id] = pending
        self.emit(kind, {"id": prompt_id, **payload})
        return prompt_id

    def resolve_prompt(self, prompt_id: str, result: Any) -> bool:
        with self._lock:
            pending = self._pending.get(prompt_id)
            if pending is None or pending.resolved:
                return False
            pending.resolved = True
        try:
            pending.result_queue.put_nowait(result)
        except queue.Full:
            return False
        self.emit("prompt_resolved", {"id": prompt_id})
        return True

    def dismiss_prompt(self, prompt_id: str) -> None:
        """Drop a pending prompt after another channel answered."""
        with self._lock:
            pending = self._pending.pop(prompt_id, None)
        if pending is None or pending.resolved:
            return
        pending.resolved = True
        try:
            pending.result_queue.put_nowait(None)
        except queue.Full:
            pass
        self.emit("prompt_resolved", {"id": prompt_id})

    def wait_prompt(self, prompt_id: str, timeout: float = 3600.0) -> Any:
        with self._lock:
            pending = self._pending.get(prompt_id)
        if pending is None:
            return None
        try:
            value = pending.result_queue.get(timeout=timeout)
            return None if value is None else value
        except queue.Empty:
            return None
        finally:
            with self._lock:
                self._pending.pop(prompt_id, None)
