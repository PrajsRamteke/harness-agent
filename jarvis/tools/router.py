"""Select a smaller tool schema set for each model request."""
from __future__ import annotations

import json
import re
from typing import Iterable

from . import TOOL_GROUPS, TOOL_NAME_TO_GROUP

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
MEMORY_RE = re.compile(r"\b(remember|memory|forget|my name|preference|about me)\b", re.I)
LESSON_RE = re.compile(r"\b(lesson|skill|learned|remember how|same task)\b", re.I)


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
    if kind == "tool_result":
        content = block.get("content", "")
        if isinstance(content, str):
            return content[:1000]
        return json.dumps(content)[:1000]
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

    if WEB_RE.search(text) or "internet" in active:
        groups.append("internet")
    if MAC_RE.search(text) or "mac" in active:
        groups.append("mac")
    if OCR_RE.search(text) or "ocr" in active:
        groups.append("ocr")
    if MEMORY_RE.search(text) or "memory" in active:
        groups.append("memory")
    if LESSON_RE.search(text) or "lessons" in active:
        groups.append("lessons")

    # MCP group — always include when there are connected MCP tools
    mcp_tools = TOOL_GROUPS.get("mcp", [])
    if mcp_tools:
        groups.append("mcp")
        # Also consider MCP group active if there was a recent MCP tool_use
        if "mcp" in active:
            pass  # already in the list

    return _dedupe_tools(groups)
