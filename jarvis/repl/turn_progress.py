"""Report high-level turn progress to the UI (TUI activity line). Legacy REPL ignores."""

from __future__ import annotations


def report_turn_phase(label: str) -> None:
    """Notify the active console of the current phase (API, streaming, tools, etc.)."""
    from ..console import console

    fn = getattr(console, "report_turn_phase", None)
    if callable(fn):
        fn(label)
