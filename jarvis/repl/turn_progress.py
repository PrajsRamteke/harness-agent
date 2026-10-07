"""Report high-level turn progress to the UI (TUI activity line). Legacy REPL ignores."""

from __future__ import annotations


def report_turn_phase(label: str) -> None:
    """Notify the active console of the current phase (API, streaming, tools, etc.)."""
    from ..console import console
    from ..subagents.context import current as _subagent

    if _subagent() is not None:
        return  # a subagent's progress shows on its board row, not the activity line

    fn = getattr(console, "report_turn_phase", None)
    if callable(fn):
        fn(label)
