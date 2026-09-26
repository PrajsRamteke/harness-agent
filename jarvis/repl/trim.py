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
    Image blocks use a fixed estimate (~3K tokens / 12K chars) to avoid
    inflating the budget with base64 data.
    """
    # Approximate image cost: most multimodal APIs charge ~1000-3000 tokens
    # per image regardless of size.  We use 2000 tokens ≈ 8000 chars as a
    # conservative estimate that prevents premature trimming of text content.
    _IMAGE_ESTIMATE_CHARS = 8_000

    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for b in content:
            if isinstance(b, dict):
                kind = b.get("type")
                if kind == "tool_result":
                    inner = b.get("content", "")
                    # screenshot results: text + image blocks (never count base64)
                    total += _content_chars(inner) if isinstance(inner, list) else len(str(inner))
                elif kind == "text":
                    total += len(b.get("text", ""))
                elif kind == "thinking":
                    total += len(b.get("thinking", ""))
                elif kind == "tool_use":
                    total += len(str(b.get("input", "")))
                elif kind == "image":
                    total += _IMAGE_ESTIMATE_CHARS
                else:
                    total += len(str(b))
            else:
                total += len(str(b))
        return total
    return len(str(content))


def _total_chars(messages: List[Dict]) -> int:
    return sum(_content_chars(m.get("content", "")) for m in messages)


def estimate_session_tokens(messages: List[Dict]) -> tuple[int, int, int]:
    """Rough token counts for the status bar when no live API usage is available.

    Mirrors stream.py semantics:
      - total_in: full conversation context (latest request input size)
      - total_out: cumulative assistant output
      - total_tokens: last-turn total (context + latest assistant reply)
    """
    if not messages:
        return 0, 0, 0
    total_in = _total_chars(messages) // 4
    assistant_chars = sum(
        _content_chars(m.get("content", ""))
        for m in messages
        if m.get("role") == "assistant"
    )
    total_out = assistant_chars // 4
    last_asst = 0
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            last_asst = _content_chars(msg.get("content", "")) // 4
            break
    total_tokens = total_in + last_asst
    return total_in, total_out, total_tokens


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

# Tool screenshots kept as real images; older ones become a one-line note.
KEEP_TOOL_IMAGES = 3
_IMAGE_GONE = "[earlier screenshot removed to save context — take a new one if needed]"
_IMAGE_NO_VISION = "[screenshot omitted — the current model can't view images]"


def prune_tool_images(messages: List[Dict], *, vision: bool,
                      keep: int = KEEP_TOOL_IMAGES) -> List[Dict]:
    """Keep only the newest ``keep`` images inside tool results (none when
    the model has no vision). Returns a copy when anything changed — never
    mutates ``state.messages``, so switching back to a vision model later
    still has the recent ones."""
    budget = keep if vision else 0
    seen = 0
    out = list(messages)
    for mi in range(len(messages) - 1, -1, -1):
        content = messages[mi].get("content")
        if not isinstance(content, list):
            continue
        new_content = None
        for bi in range(len(content) - 1, -1, -1):
            block = content[bi]
            if not _is_tool_result_block(block) or not isinstance(block.get("content"), list):
                continue
            inner = block["content"]
            n_images = sum(1 for x in inner if isinstance(x, dict) and x.get("type") == "image")
            if not n_images:
                continue
            allowed = max(0, budget - seen)
            seen += n_images
            if allowed >= n_images:
                continue
            kept = 0
            rebuilt = []
            for x in reversed(inner):
                if isinstance(x, dict) and x.get("type") == "image":
                    if kept < allowed:
                        kept += 1
                        rebuilt.append(x)
                    else:
                        rebuilt.append({"type": "text", "text": _IMAGE_NO_VISION if not vision else _IMAGE_GONE})
                else:
                    rebuilt.append(x)
            if new_content is None:
                new_content = list(content)
            new_content[bi] = {**block, "content": list(reversed(rebuilt))}
        if new_content is not None:
            out[mi] = {**messages[mi], "content": new_content}
    return out


# Fields the Messages API accepts per assistant block type. Blocks built by the
# OpenAI-style clients (Harness Agent / OpenCode / Kimchi / Codex) are plain
# objects and may carry extras.
_WIRE_BLOCK_KEYS = {
    "text": ("type", "text"),
    "tool_use": ("type", "id", "name", "input"),
    "thinking": ("type", "thinking", "signature"),
}


def _foreign_block_to_dict(block: Any) -> Dict:
    data = block.model_dump() if hasattr(block, "model_dump") else dict(vars(block))
    keys = _WIRE_BLOCK_KEYS.get(data.get("type"))
    if keys:
        data = {k: data[k] for k in keys if k in data}
    return data


def anthropic_wire_messages(messages: List[Dict]) -> List[Dict]:
    """History the Anthropic SDK (Anthropic / OpenRouter) can send.

    After switching mid-session from an OpenAI-style provider, earlier replies
    are that client's own block objects: the SDK can't JSON-encode them
    (``TypeError: Object of type _ContentBlock is not JSON serializable``), and
    their thinking blocks carry no signature, which the Messages API rejects.
    Those blocks become plain dicts and unsigned thinking is dropped. SDK
    blocks pass through untouched. Returns a copy only when something changed —
    never mutates ``state.messages``.
    """
    from pydantic import BaseModel

    out = list(messages)
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        new_content = []
        changed = False
        for block in content:
            if not isinstance(block, (dict, BaseModel)):
                block = _foreign_block_to_dict(block)
                changed = True
            if isinstance(block, dict) and block.get("type") == "thinking" and not block.get("signature"):
                changed = True
                continue
            new_content.append(block)
        if changed:
            if not new_content and msg.get("role") == "assistant":
                new_content = [{"type": "text", "text": "…"}]
            out[mi] = {**msg, "content": new_content}
    return out
