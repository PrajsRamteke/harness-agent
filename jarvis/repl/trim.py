"""Context window trimming — keeps token cost from ballooning over long sessions.

Strategy:
  - Keep the last KEEP_TURNS full user+assistant pairs always intact.
  - For older tool-result messages, replace the content with a short stub.
  - NEVER stub Connected Context Pack results (they contain ALL the file
    content the model needs to work with — stubbing breaks the workflow).
  - Never drop user or assistant text messages — only collapses old tool outputs.

This is a lossy compression: old tool outputs are replaced with a stub.
The conversation logic still works because the model sees the original
assistant request and a stub result, so the flow remains coherent.

KEEP_TURNS = 10  →  last 10 user/assistant exchanges kept verbatim.
Older tool outputs (can be 3-6 KB each) are collapsed to ~10 tokens.
Context pack results (114K+ chars) are never stubbed.
"""
from typing import List, Dict, Any
import copy

# Number of recent user/assistant exchanges to preserve in full.
KEEP_TURNS = 10

# Approximate token budget at which we start trimming.
# A rough heuristic: each character ≈ 0.25 tokens.
# Increased from 80K to 200K to accommodate Connected Context Packs (114K+ chars).
CHAR_BUDGET = 200_000  # ~50K tokens

# Context pack signature — tool results starting with this are NEVER stubbed.
_CONTEXT_PACK_PREFIX = "=== Connected Context Pack ==="


def _is_tool_result_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "tool_result"


def _is_context_pack(block: Any) -> bool:
    """Check if a tool_result contains a Connected Context Pack bundle."""
    content = block.get("content", "")
    if isinstance(content, str):
        return content.startswith(_CONTEXT_PACK_PREFIX)
    return False


def _content_chars(content: Any) -> int:
    """Count total characters in a message content field.

    Handles:
      - plain str (user typed text)
      - list of blocks (tool_use, tool_result, text, thinking, image)
    Tool_result content is counted from the 'content' key, not 'text'.
    """
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for b in content:
            if isinstance(b, dict):
                kind = b.get("type")
                if kind == "tool_result":
                    total += len(str(b.get("content", "")))
                elif kind == "text":
                    total += len(b.get("text", ""))
                elif kind == "thinking":
                    total += len(b.get("thinking", ""))
                elif kind == "tool_use":
                    total += len(str(b.get("input", "")))
                else:
                    total += len(str(b))
            else:
                total += len(str(b))
        return total
    return len(str(content))


def _total_chars(messages: List[Dict]) -> int:
    return sum(_content_chars(m.get("content", "")) for m in messages)


def _stub_tool_results(msg: Dict) -> Dict:
    """Return a copy of a user message with tool_result blocks collapsed.

    Context pack results are preserved verbatim — never stubbed.
    """
    content = msg.get("content", "")
    if not isinstance(content, list):
        return msg
    new_content = []
    for block in content:
        if _is_tool_result_block(block):
            # ── preserve context packs ─────────────────────────────────
            if _is_context_pack(block):
                new_content.append(block)
                continue
            # ── stub everything else ───────────────────────────────────
            stub = {
                "type": "tool_result",
                "tool_use_id": block.get("tool_use_id", ""),
                "content": "[output trimmed to save context]",
            }
            if block.get("is_error"):
                stub["is_error"] = True
            new_content.append(stub)
        else:
            new_content.append(block)
    return {**msg, "content": new_content}


def trim_messages(messages: List[Dict]) -> List[Dict]:
    """Return a (possibly trimmed) copy of messages for the API call.

    Does NOT mutate state.messages — only affects what's sent to the API.
    """
    if _total_chars(messages) <= CHAR_BUDGET:
        return messages  # nothing to do

    # Find turn boundaries (user messages start a turn).
    # We want to keep the last KEEP_TURNS user messages and everything after them.
    user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]

    if len(user_indices) <= KEEP_TURNS:
        return messages  # not enough history to trim anything

    cutoff_idx = user_indices[-KEEP_TURNS]  # first index of the "keep" window

    trimmed = []
    for i, msg in enumerate(messages):
        if i >= cutoff_idx:
            trimmed.append(msg)  # keep verbatim
        else:
            # Older message — collapse tool results to stubs
            # (context packs are preserved inside _stub_tool_results)
            trimmed.append(_stub_tool_results(msg))

    return trimmed