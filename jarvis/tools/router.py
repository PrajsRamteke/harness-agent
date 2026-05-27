"""Select a smaller tool schema set for each model request."""
from __future__ import annotations

import json
import re
from typing import Iterable

from . import TOOL_GROUPS, TOOL_NAME_TO_GROUP, FUNC
from ..storage.skills import skill_count
from ..utils.schema import sanitize_tools

WEB_RE = re.compile(
    r"\b(web|internet|search online|look up|latest|today|news|price|weather|url|https?://|docs?|documentation)\b",
    re.I,
)
BROWSER_RE = re.compile(
    r"\b(browser|chrome|webbridge|web bridge|webpage|web page|website|web site|"
    r"https?://|www\.|\.(?:com|org|net|io|dev|app|co)(?:/|\b)|"
    r"(?:go to|visit|open|navigate to|browse|check)\s+(?:the\s+)?(?:\w+\s+){0,4}"
    r"(?:site|page|website|portal|dashboard|store|tab|url|link)\b|"
    r"page|tab|tabs|navigate|browse|surf|login|sign.?in|sign.?up|checkout|"
    r"add to cart|form|submit|dom|selector|snapshot|"
    r"(?:click|fill|type|scroll|screenshot).*(?:page|site|tab|browser|web|form|button|link|element)|"
    r"(?:page|site|tab|browser|web).*(?:click|fill|type|scroll|screenshot)|"
    r"use (?:the )?(?:browser|chrome|extension))\b",
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
MEMORY_RE = re.compile(r"\b(remember|memory|forget|my name|preference|about me)\b", re.I)
LESSON_RE = re.compile(r"\b(lesson|learned|remember how|same task)\b", re.I)
SKILL_RE = re.compile(r"\b(skill|skills|sk\.md|skill\.md|reusable instr|my skills|available skills)\b", re.I)


def _browser_connected() -> bool:
    """True when the Chrome WebBridge extension is attached to the bridge."""
    try:
        from ..browser_bridge.server import bridge_state

        return bool(bridge_state().connected)
    except Exception:
        return False


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


def select_tools(messages: list[dict]) -> list[dict]:
    """Return only the tool groups likely needed for this turn.

    Core file/code tools are always available. Specialized groups are added
    from the latest user/task text and from recent tool_use blocks so a
    multi-step tool loop keeps the tools it already started using.
    """
    text = _latest_text(messages)
    groups = ["core"]
    active = _recent_tool_groups(messages)

    # Skills — always include when any skill is discovered. Headers are injected
    # into the system prompt and the model must be able to call skill_load on
    # every turn, not only when the user mentions "skill" in their message.
    if skill_count() > 0 or SKILL_RE.search(text) or "skills" in active:
        groups.append("skills")

    if WEB_RE.search(text) or "internet" in active:
        groups.append("internet")
    browser_intent = bool(BROWSER_RE.search(text) or "browser" in active)
    # When the Chrome extension is live, always expose browser_* tools so the
    # model never falls back to launch_app/AppleScript for web work just because
    # the phrasing ("open youtube") missed the intent regex.
    if browser_intent or _browser_connected():
        groups.append("browser")
    # Explicit web-page intent uses the Chrome extension — skip the overlapping
    # Mac GUI tools. Mere connection doesn't suppress them, so genuine desktop
    # tasks (WhatsApp, Finder, …) still get Mac tools.
    if not browser_intent and (MAC_RE.search(text) or "mac" in active):
        groups.append("mac")
    if OCR_RE.search(text) or "ocr" in active:
        groups.append("ocr")
    if MEMORY_RE.search(text) or "memory" in active:
        groups.append("memory")
    if LESSON_RE.search(text) or "lessons" in active:
        groups.append("lessons")

    # MCP group — only when at least one MCP tool is registered (server connected).
    # Skills may mention mcp__* tool names even when disconnected; never expose
    # schemas that are not backed by a FUNC handler.
    mcp_tools = [t for t in TOOL_GROUPS.get("mcp", []) if t["name"] in FUNC]
    if mcp_tools:
        groups.append("mcp")

    # Defense in depth: sanitize every tool schema right before it leaves
    # the process. Anthropic rejects top-level oneOf/anyOf/allOf, and some
    # MCP servers (ClickUp, GitHub, TestSprite, …) ship those. Doing it here
    # means a stale TOOL_GROUPS entry from an older registration cycle can't
    # leak the broken shape into the API call.
    tools = sanitize_tools(_dedupe_tools(groups))
    return [t for t in tools if t["name"] in FUNC]
