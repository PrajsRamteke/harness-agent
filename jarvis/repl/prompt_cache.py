"""Prompt caching for Anthropic-wire requests.

Every request in a tool loop resends tools + system prompt + the whole
history. With ``cache_control`` breakpoints Anthropic serves the unchanged
prefix from cache (~10% of the input price, and a much faster first token).
Breakpoints (3 of the 4 allowed):

  1. the last tool          → tools
  2. the last system block  → tools + system
  3. the last eligible block of the last message → the whole conversation;
     the next request reads it back via the API's 20-position lookback.

Caching is a byte-exact prefix match, so this only pays off because the rest
of the harness keeps the prefix stable: the router never drops a tool group
mid-session (tools/router.py), the system prompt is frozen per user turn
(repl/system.py), and trimming moves in steps (repl/trim.py).

Only the request copy is marked — ``state.messages`` is never touched, so
markers can't pile up past the limit of four.
"""
from __future__ import annotations

import os
from typing import Any

EPHEMERAL = {"type": "ephemeral"}
_MARKABLE = {"text", "image", "document", "tool_result", "tool_use"}

# Set when an endpoint refused the markers; caching stays off for the process.
_disabled_reason: str | None = None


def enabled_for(provider: str, model: str, client: Any) -> bool:
    """Whether this request goes somewhere that understands cache_control."""
    if _disabled_reason or os.getenv("HARNESS_PROMPT_CACHE", "1").strip() == "0":
        return False
    from anthropic import Anthropic

    from ..constants.providers import PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER

    if not isinstance(client, Anthropic):
        return False
    if provider == PROVIDER_ANTHROPIC or provider == "md:anthropic":
        return True
    if provider == PROVIDER_OPENROUTER:
        # OpenRouter passes cache_control through to Anthropic (and Bedrock /
        # Vertex) for Claude; other families cache on their own or not at all.
        return (model or "").startswith("anthropic/")
    return False


def disable(reason: str) -> None:
    global _disabled_reason
    _disabled_reason = reason or "refused"


def _block_type(block: Any) -> str | None:
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def _markable(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    kind = block.get("type")
    if kind not in _MARKABLE:
        return False
    if kind == "text" and not (block.get("text") or "").strip():
        return False
    return True


def _as_blocks(content: Any) -> Any:
    """String content → one text block (same rendering, one consistent shape
    on every request, so the message we marked last time matches byte-for-byte
    when it is resent unmarked)."""
    if isinstance(content, str) and content:
        return [{"type": "text", "text": content}]
    return content


def apply(kwargs: dict) -> None:
    """Add cache breakpoints to a request's tools, system and messages (in place
    on *kwargs*, copying whatever it changes)."""
    tools = kwargs.get("tools")
    if tools:
        tools = list(tools)
        tools[-1] = {**tools[-1], "cache_control": EPHEMERAL}
        kwargs["tools"] = tools

    system = kwargs.get("system")
    if isinstance(system, str) and system:
        kwargs["system"] = [{"type": "text", "text": system, "cache_control": EPHEMERAL}]
    elif isinstance(system, list) and system:
        blocks = [dict(b) if isinstance(b, dict) else b for b in system]
        if isinstance(blocks[-1], dict):
            blocks[-1]["cache_control"] = EPHEMERAL
        kwargs["system"] = blocks

    messages = kwargs.get("messages") or []
    out = []
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str) and content:
            msg = {**msg, "content": _as_blocks(content)}
        out.append(msg)
    if out and isinstance(out[-1], dict) and isinstance(out[-1].get("content"), list):
        last = out[-1]
        blocks = list(last["content"])
        for i in range(len(blocks) - 1, -1, -1):
            if _markable(blocks[i]):
                blocks[i] = {**blocks[i], "cache_control": EPHEMERAL}
                out[-1] = {**last, "content": blocks}
                break
    kwargs["messages"] = out


def strip(kwargs: dict) -> None:
    """Remove every breakpoint ``apply`` added (endpoint refused them)."""
    tools = kwargs.get("tools")
    if tools:
        kwargs["tools"] = [
            {k: v for k, v in t.items() if k != "cache_control"} if isinstance(t, dict) else t
            for t in tools
        ]
    system = kwargs.get("system")
    if isinstance(system, list):
        kwargs["system"] = [
            {k: v for k, v in b.items() if k != "cache_control"} if isinstance(b, dict) else b
            for b in system
        ]
    msgs = []
    for msg in kwargs.get("messages") or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list) and any(
            isinstance(b, dict) and "cache_control" in b for b in content
        ):
            content = [
                {k: v for k, v in b.items() if k != "cache_control"} if isinstance(b, dict) else b
                for b in content
            ]
            msg = {**msg, "content": content}
        msgs.append(msg)
    kwargs["messages"] = msgs


def has_markers(kwargs: dict) -> bool:
    def _in(items: Any) -> bool:
        return isinstance(items, list) and any(
            isinstance(b, dict) and "cache_control" in b for b in items
        )

    if _in(kwargs.get("tools")) or _in(kwargs.get("system")):
        return True
    return any(
        isinstance(m, dict) and _in(m.get("content")) for m in kwargs.get("messages") or []
    )


def refused_markers(err: Exception) -> bool:
    """A 400 that is about the cache_control field itself."""
    return "cache_control" in str(err)


def thinking_binding_error(err: Exception) -> bool:
    """400 for replayed thinking blocks the API won't accept: bound to a
    different conversation (history was edited — Claude 5 "preserved
    thinking"), or a signature from another model / provider."""
    text = str(err).lower()
    return "signature" in text and "thinking" in text


def drop_thinking_blocks(messages: list) -> int:
    """Remove thinking / redacted_thinking blocks from history in place — the
    documented recovery for a binding error. Returns how many were removed."""
    removed = 0
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        kept = [b for b in content if _block_type(b) not in ("thinking", "redacted_thinking")]
        if len(kept) == len(content):
            continue
        removed += len(content) - len(kept)
        messages[i] = {**msg, "content": kept or [{"type": "text", "text": "…"}]}
    return removed


def usage_counts(usage: Any) -> tuple[int, int, int]:
    """(uncached input, cache read, cache write) tokens from an Anthropic usage."""
    def _n(name: str) -> int:
        try:
            return int(getattr(usage, name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    return _n("input_tokens"), _n("cache_read_input_tokens"), _n("cache_creation_input_tokens")


__all__ = [
    "apply", "strip", "has_markers", "enabled_for", "disable", "refused_markers",
    "thinking_binding_error", "drop_thinking_blocks", "usage_counts",
]
