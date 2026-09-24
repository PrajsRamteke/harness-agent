"""Emit live tool activity events to consoles (web SSE + TUI)."""
from __future__ import annotations

from typing import Any


def _emit(event_type: str, data: dict[str, Any]) -> None:
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
