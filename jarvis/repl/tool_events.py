"""Emit live tool activity events to consoles (web SSE + TUI)."""
from __future__ import annotations

import threading
from typing import Any

# The tool call the current thread is running (a handler that needs its own
# id — spawn_agents keys its live board by it).
_current = threading.local()


def set_current_tool(tool_id: str | None) -> None:
    _current.id = tool_id or ""


def current_tool_id() -> str:
    return getattr(_current, "id", "") or ""


def _emit(event_type: str, data: dict[str, Any]) -> None:
    # A subagent's tool calls feed its row on the agents board, not the transcript.
    from ..subagents.context import current as _subagent

    sub = _subagent()
    if sub is not None:
        try:
            _subagent_event(sub[0], sub[1], event_type, data)
        except Exception:
            pass
        return
    try:
        from ..console import console

        fn = getattr(console, "emit_tool_event", None)
        if callable(fn):
            fn(event_type, data)
    except Exception:
        pass


def emit_tool_wave_reset() -> None:
    _emit("tool_wave_reset", {})


def emit_tool_start(*, tool_id: str, name: str, label: str, input: Any = None) -> None:
    _emit("tool_start", {"id": tool_id, "name": name, "label": label, "input": input})


def emit_tool_done(
    *,
    tool_id: str,
    name: str,
    label: str,
    error: bool = False,
    repaired: bool = False,
    input: Any = None,
    output: str = "",
) -> None:
    # ``input`` / ``output`` feed the TUI tool rows; the web mux strips them
    # before broadcasting so SSE payloads stay small.
    _emit(
        "tool_done",
        {
            "id": tool_id,
            "name": name,
            "label": label,
            "error": error,
            "repaired": repaired,
            "input": input,
            "output": output,
        },
    )


def _subagent_event(team: Any, agent: Any, event_type: str, data: dict[str, Any]) -> None:
    tid = str(data.get("id") or "")
    name = str(data.get("name") or "")
    label = str(data.get("label") or name)
    if event_type == "tool_start":
        team.log(agent, label, kind="tool", status="running", key=tid)
        team.set_activity(agent, label)
    elif event_type == "tool_done":
        status = "error" if data.get("error") else "done"
        team.log(agent, label, kind="tool", status=status, key=tid)
