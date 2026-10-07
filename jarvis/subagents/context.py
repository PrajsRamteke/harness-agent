"""Which subagent (if any) the current thread is working for.

Kept import-free so low-level modules (tool events, diffs, approvals) can ask
"am I inside a subagent?" without import cycles. A subagent's tool calls run
on its own thread, so a thread-local is enough.
"""
from __future__ import annotations

import threading
from typing import Any

_local = threading.local()


def set_current(team: Any, agent: Any) -> None:
    _local.team = team
    _local.agent = agent


def clear_current() -> None:
    _local.team = None
    _local.agent = None


def current() -> tuple[Any, Any] | None:
    """(team, agent) for a subagent thread, else None."""
    team = getattr(_local, "team", None)
    agent = getattr(_local, "agent", None)
    if team is None or agent is None:
        return None
    return team, agent


def current_label() -> str:
    """The subagent's display name on its thread, else ""."""
    ctx = current()
    return ctx[1].name if ctx else ""
