"""Parallel subagents: the lead agent splits a big task into 1–20 briefs and
``spawn_agents`` runs them at the same time, each with its own conversation
and tools, returning their reports in one tool result.

- ``team``    — live state (Team / AgentRun), snapshots, stop, registry (UI-free)
- ``runner``  — one agent's model ↔ tool loop on its own thread
- ``tool``    — the ``spawn_agents`` tool, its schema, settings, result text
- ``context`` — which agent the current thread works for (import-free)
"""
from .tool import (  # noqa: F401
    SUBAGENT_TOOLS, SPAWN_AGENTS_TOOL, board_for, enabled, format_result, max_parallel,
    spawn_agents,
)
from .team import get as get_team, running_teams, stop  # noqa: F401
