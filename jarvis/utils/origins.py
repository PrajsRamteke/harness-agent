"""Which tool an agent, skill or MCP server comes from (Claude Code, Cursor, …).

Jarvis reads other tools' folders and config files as well as its own, so
every list tags each entry with its tool id. Agents and skills get it from
their folder (``tool_for_path``); MCP servers from the config file they were
read from (``mcp/config.py:_global_sources`` uses the same ids).
"""
from __future__ import annotations

import pathlib
from typing import Iterable

# id → (label, icon). Order = display order when tools are listed together.
TOOLS: dict[str, tuple[str, str]] = {
    "project":     ("Project", "▣"),       # MCP only: the shared .mcp.json
    "jarvis":      ("Jarvis", "✦"),
    "claude":      ("Claude Code", "◆"),
    "cursor":      ("Cursor", "⌘"),
    "codex":       ("Codex", "◎"),
    "gemini":      ("Gemini CLI", "✶"),
    "antigravity": ("Antigravity", "▲"),
    "opencode":    ("OpenCode", "◇"),
    "agents":      ("Agents", "▤"),         # the shared ~/.agents folder (`npx skills`, Codex)
    "kiro":        ("Kiro", "◈"),
    "copilot":     ("Copilot", "◍"),
    "windsurf":    ("Windsurf", "≈"),
    "vscode":      ("VS Code", "⬡"),
}

# A folder name inside a path → the tool that owns it.
_MARKERS = {
    ".harness": "jarvis",
    "harness-agent": "jarvis",
    ".skills": "jarvis",
    ".claude": "claude",
    ".cursor": "cursor",
    ".codex": "codex",
    ".gemini": "gemini",
    "antigravity": "antigravity",
    ".opencode": "opencode",
    "opencode": "opencode",          # ~/.config/opencode
    ".agents": "agents",
    ".kiro": "kiro",
    ".copilot": "copilot",
    ".windsurf": "windsurf",
    ".vscode": "vscode",
}


def tool_for_path(path: str | pathlib.PurePath) -> str:
    """The tool that owns ``path``. The nearest known folder wins, so a project
    under ``~/.codex/worktrees/`` still reads its ``.claude/skills`` as claude."""
    for part in reversed(pathlib.PurePath(str(path)).parts):
        tool = _MARKERS.get(part)
        if tool:
            return tool
    return "jarvis"


def tool_label(tool: str) -> str:
    return TOOLS.get(tool, (tool or "?", ""))[0]


def tool_icon(tool: str) -> str:
    return TOOLS.get(tool, ("", "•"))[1]


def other_tools(tool: str, also: Iterable[str] = ()) -> list[str]:
    """``also`` without ``tool`` or repeats, in first-seen order."""
    return [t for t in dict.fromkeys(also) if t and t != tool]


def tool_tag(tool: str, also: Iterable[str] = ()) -> str:
    """Short row tag: ``claude``, or ``claude +2`` when two more tools have it."""
    extra = other_tools(tool, also)
    return tool + (f" +{len(extra)}" if extra else "")


def note_duplicate(winner: dict, loser: dict) -> None:
    """``loser`` has the same name as ``winner`` (which shadows it): remember its
    tool on the winner as ``also`` so lists can say "claude +2"."""
    also = winner.setdefault("also", [])
    tool = loser.get("tool")
    if tool and tool != winner.get("tool") and tool not in also:
        also.append(tool)


def tools_in(records: Iterable[dict]) -> list[str]:
    """The tools a list of records comes from, in ``TOOLS`` order."""
    seen = {r.get("tool") for r in records if r.get("tool")}
    return [t for t in TOOLS if t in seen] + sorted(t for t in seen if t not in TOOLS)
