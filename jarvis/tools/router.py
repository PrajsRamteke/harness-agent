"""Select a smaller tool schema set for each model request."""
from __future__ import annotations

import json
import re
from typing import Iterable

from . import TOOL_GROUPS, TOOL_NAME_TO_GROUP, FUNC
from .plan import PLAN_MODE_ALLOWED
from ..storage.skills import skill_count
from ..utils.schema import sanitize_tools
from .. import state

WEB_RE = re.compile(
    r"\b(web|internet|search online|look up|latest|today|news|price|weather|url|https?://|docs?|documentation)\b",
    re.I,
)
MAC_RE = re.compile(
    r"\b(click|type|press|open app|launch|focus|safari|finder|whatsapp|messages|mail|calendar|reminders|clipboard|screen|ui|macos|speak|speck|read aloud|text to speech|tts|aloud|voice|sound|notify)\b",
    re.I,
)
OCR_RE = re.compile(
    r"\b(ocr|screenshot|image|photo|picture|png|jpe?g|heic|tiff?|resume|cv|voter|license|licence|passport|id card|personal id)\b",
    re.I,
)
VISION_RE = re.compile(
    r"\b(screenshot|screen ?shot|screen|look(?:s|ing)? (?:at|like)|see (?:it|the|what|how)|visual(?:ly)?|"
    r"ui|ux|layout|design|render(?:s|ed|ing)?|css|style[sd]?|styling|pixel|responsive|frontend|front-end|"
    r"web ?page|website|localhost|browser|component|button|modal|dialog|navbar|sidebar|theme|colou?rs?|"
    r"font|spacing|padding|margin|align(?:ed|ment)?|overflow|broken|glitch|mockup|figma|simulator|window)\b",
    re.I,
)
BG_RE = re.compile(
    r"\b(background|in parallel|meanwhile|while (?:it|that|you)|long[- ]running|slow|pytest|tests?|"
    r"test suite|jest|vitest|build|compile|install|npm|pnpm|yarn|bun|cargo|gradle|mvn|make|docker|"
    r"dev server|server|watch|benchmark|lint|typecheck|tsc|deploy|migrat\w*|job)\b",
    re.I,
)
MEMORY_RE = re.compile(r"\b(remember|memory|forget|my name|preference|about me)\b", re.I)
LESSON_RE = re.compile(r"\b(lesson|learned|remember how|same task)\b", re.I)
SKILL_RE = re.compile(r"\b(skill|skills|sk\.md|skill\.md|reusable instr|my skills|available skills)\b", re.I)


def _extensions_re() -> re.Pattern:
    """Installing skills / MCP servers: the words, launchers, or "add linear"."""
    from ..mcp.catalog import CATALOG

    # Ids that are everyday words ("add time", "connect git") don't count on their own.
    generic = {"time", "git", "fetch", "memory", "close", "jam", "filesystem", "sequential-thinking"}
    names = "|".join(re.escape(c["id"]) for c in CATALOG if c["id"] not in generic)
    return re.compile(
        r"\b(mcp|mcps|mcp[- ]server|modelcontextprotocol|\.mcp\.json|skills?|skill\.md|npx -y|uvx|claude mcp add|"
        r"authenticate|sign[- ]?in to|oauth)\b"
        rf"|\b(?:add|install|connect|enable|set ?up|hook up)\s+(?:the\s+)?(?:{names})\b"
        r"|github\.com/[\w.-]+/[\w.-]+/(?:tree|blob)/[^\s]*skills?"
        r"|skills\.sh/",
        re.I,
    )


EXT_RE = _extensions_re()


def _block_to_text(block) -> str:
    if isinstance(block, str):
        return block
    if hasattr(block, "model_dump"):
        block = block.model_dump()
    if not isinstance(block, dict):
        return ""
    kind = block.get("type")
    if kind == "text":
        return block.get("text", "")
    # Skip tool_result blocks — their content is tool output (file paths,
    # URLs, raw data), not user intent. Including them causes false-positive
    # trigger detection (e.g. ".png" in OCR results re-activating OCR tools).
    # Only user messages and assistant text should drive tool selection.
    if kind == "tool_result":
        return ""
    return ""


def _latest_text(messages: list[dict], max_messages: int = 4) -> str:
    chunks = []
    for msg in reversed(messages[-max_messages:]):
        content = msg.get("content", "")
        if isinstance(content, list):
            chunks.extend(_block_to_text(block) for block in content)
        else:
            chunks.append(_block_to_text(content))
    return "\n".join(chunks)


def _recent_tool_groups(messages: list[dict], max_messages: int = 4) -> set[str]:
    groups = set()
    for msg in messages[-max_messages:]:
        content = msg.get("content", "")
        if not isinstance(content, list):
            continue
        for block in content:
            if hasattr(block, "model_dump"):
                block = block.model_dump()
            if isinstance(block, dict) and block.get("type") == "tool_use":
                group = TOOL_NAME_TO_GROUP.get(block.get("name"))
                if group:
                    groups.add(group)
    return groups


def _coding_agent_active() -> bool:
    """Whether the context-pack tools should be exposed this turn.

    ``resolve_context`` / ``read_bundle`` bundle large slices of a codebase and
    are only useful while actually working on code. We expose them solely when
    the coding agent is active so the model can't reach for them on unrelated
    turns. Matches the bundled coding agent by name and, defensively, any active
    agent whose body documents the context tools (covers renamed/custom coding
    agents) — without touching any prompt text.
    """
    rec = state.active_agent
    if rec is None and state.active_agent_name:
        try:
            rec = state.resolve_active_agent()
        except Exception:
            rec = None
    if not rec:
        return False
    if (rec.get("name") or "").strip().lower() == "coding":
        return True
    return "resolve_context" in (rec.get("_body") or "")


def _dedupe_tools(groups: Iterable[str]) -> list[dict]:
    out = []
    seen = set()
    for group in groups:
        for tool in TOOL_GROUPS.get(group, []):
            name = tool["name"]
            if name not in seen:
                out.append(tool)
                seen.add(name)
    return out


# Groups added by what the conversation is about (or by the model using them).
# Once one is in, it stays for the rest of the session: the tool list is the
# very start of every request, so a group dropping out (its trigger word scrolled
# past the last few messages) and coming back used to throw away the whole
# prompt cache — and on Claude 5 models invalidate earlier thinking — for a few
# hundred tokens of schema. Order is fixed by _GROUP_ORDER, never by arrival.
_STICKY_GROUPS = (
    "context", "internet", "mac", "vision", "background", "ocr", "memory",
    "lessons", "extensions",
)
_GROUP_ORDER = (
    "core", "context", "skills", "internet", "mac", "vision", "loop",
    "background", "ocr", "memory", "lessons", "extensions", "mcp", "plan",
)
_sticky: dict = {"key": None, "groups": set()}


def _conversation_key(messages: list[dict]) -> tuple:
    """Same session and same opening message = same conversation (history
    only ever grows at the end, so the first message never changes)."""
    first = messages[0] if messages else None
    opening = _block_to_text(first.get("content")) if isinstance(first, dict) and isinstance(
        first.get("content"), str) else _latest_text(messages[:1]) if messages else ""
    return (state.current_session_id, opening[:500])


def _sticky_groups(messages: list[dict]) -> set[str]:
    """This conversation's sticky groups (reset for a new / resumed one)."""
    key = _conversation_key(messages)
    if _sticky["key"] != key or not messages:
        _sticky["key"] = key
        _sticky["groups"] = set()
    return _sticky["groups"]


def reset_sticky_groups() -> None:
    _sticky["key"] = None
    _sticky["groups"] = set()


def select_tools(messages: list[dict]) -> list[dict]:
    """Return only the tool groups likely needed for this turn.

    Core file/code tools are always available. Specialized groups are added
    from the latest user/task text and from recent tool_use blocks, and then
    stay for the rest of the session (see _STICKY_GROUPS).
    """
    text = _latest_text(messages)
    groups = ["core"]
    active = _recent_tool_groups(messages)
    sticky = _sticky_groups(messages)
    active |= sticky

    # Context-pack tools — coding-only. Exposed solely while the coding agent is
    # active (or mid tool-loop if it already started using them). This keeps the
    # model from reaching for whole-codebase bundling on non-coding turns.
    if _coding_agent_active() or "context" in active:
        groups.append("context")

    # Skills — always include when any skill is discovered. Headers are injected
    # into the system prompt and the model must be able to call skill_load on
    # every turn, not only when the user mentions "skill" in their message.
    if skill_count() > 0 or SKILL_RE.search(text) or "skills" in active:
        groups.append("skills")

    if WEB_RE.search(text) or "internet" in active:
        groups.append("internet")
    mac = bool(MAC_RE.search(text) or "mac" in active)
    if mac:
        groups.append("mac")
    # Eyes: whenever the task is visual, or the agent is driving the Mac GUI.
    if mac or VISION_RE.search(text) or "vision" in active:
        groups.append("vision")
    # /loop pacing — only while a loop exists.
    from ..loop import active as _loop_active

    if _loop_active() is not None:
        groups.append("loop")
    # Background jobs: coding work (tests/builds) or while any job exists.
    from .background import jobs as _bg_jobs

    if (_coding_agent_active() or BG_RE.search(text) or "background" in active
            or _bg_jobs()):
        groups.append("background")
    if OCR_RE.search(text) or "ocr" in active:
        groups.append("ocr")
    if MEMORY_RE.search(text) or "memory" in active:
        groups.append("memory")
    if LESSON_RE.search(text) or "lessons" in active:
        groups.append("lessons")

    # Installing skills / MCP servers — when the user talks about adding one, or a
    # server is waiting for its sign-in (so the agent can retry it afterwards).
    from .extensions import sign_in_waiting

    if EXT_RE.search(text) or "extensions" in active or sign_in_waiting():
        groups.append("extensions")

    # MCP group — only when at least one MCP tool is registered (server connected).
    # Skills may mention mcp__* tool names even when disconnected; never expose
    # schemas that are not backed by a FUNC handler.
    mcp_tools = [t for t in TOOL_GROUPS.get("mcp", []) if t["name"] in FUNC]
    if mcp_tools:
        groups.append("mcp")

    # Plan mode — read-only research until the user approves a plan. Expose
    # the exit_plan_mode gate and strip every tool that can mutate the
    # machine, repo, or stored data (writes, shell, mac control, MCP).
    if state.plan_mode:
        groups.append("plan")

    sticky.update(g for g in groups if g in _STICKY_GROUPS)
    for g in sticky:
        if g not in groups:
            groups.append(g)
    rank = {g: i for i, g in enumerate(_GROUP_ORDER)}
    groups.sort(key=lambda g: rank.get(g, len(rank)))

    # Defense in depth: sanitize every tool schema right before it leaves
    # the process. Anthropic rejects top-level oneOf/anyOf/allOf, and some
    # MCP servers (ClickUp, GitHub, TestSprite, …) ship those. Doing it here
    # means a stale TOOL_GROUPS entry from an older registration cycle can't
    # leak the broken shape into the API call.
    tools = sanitize_tools(_dedupe_tools(groups))
    tools = [t for t in tools if t["name"] in FUNC]
    if state.plan_mode:
        tools = [t for t in tools if t["name"] in PLAN_MODE_ALLOWED]
    return tools
