"""Keep every request inside the model's context window.

Context packs (``resolve_context`` / ``read_bundle``, up to ~30K tokens each)
used to stay in the conversation forever — trimming never touched them — so a
few coding tasks in one session pushed the prompt past the model's window. From
then on every request failed ("prompt is too long") or crawled, and the chat
was stuck. Three layers fix that, all on the *request copy* (``state.messages``
and saved sessions keep the full history):

1. ``collapse_old_packs`` — always. Packs from earlier turns shrink to a short
   stub (task + file list + "re-read with read_bundle"), except the newest one
   from before this turn. Decided per user turn, so the request prefix stays
   stable inside a tool loop (prompt cache, Claude 5 thinking blocks).
2. ``fit_to_window`` — when a request would use more than ``TRIGGER`` of the
   window: collapse every older pack, stub older tool output, leave out the
   oldest turns (replaced by a recap of what the user asked), and as a last
   resort stub older tool output inside the current turn.
3. ``is_context_overflow`` — a provider still refused the prompt as too long:
   the caller fits it harder (``target=RETRY_TARGET``) and retries.

``pack_char_cap`` sizes new packs to the model and to the room that's left.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .. import state

PACK_PREFIX = "=== Connected Context Pack ==="
STUB_PREFIX = "=== Earlier context pack (collapsed) ==="
TOOL_STUB = "[output trimmed to save context]"

CHARS_PER_TOKEN = 3.5        # code tokenises denser than prose; err on the big side
IMAGE_TOKENS = 1600          # rough cost of one image block
DEFAULT_WINDOW = 128_000     # unknown model: assume a common window
TRIGGER = 0.85               # fit when a request would use more than this…
TARGET = 0.70                # …and bring it down to about this
RETRY_TARGET = 0.50          # after a provider said "too long"
PACK_SHARE = 0.15            # one new pack: at most this share of the window
PACK_MIN_CHARS = 8_000

_CTX_CACHE: dict[str, int | None] = {}


# ── model window ─────────────────────────────────────────────────────────


def _catalog_context(model: str) -> int | None:
    """Context window models.dev lists for ``model`` on the active provider."""
    try:
        from ..auth import models_dev

        m = models_dev.find_model(model, state.provider or "")
    except Exception:
        return None
    return (m.context or None) if m is not None else None


def context_window(model: str) -> int | None:
    """Best-known context window for ``model`` (None when unknown)."""
    # The active provider's own listing wins (memoised in models_dev); the
    # same id can carry a different window on another provider.
    found = _catalog_context(model)
    if found:
        return found
    if model in _CTX_CACHE:
        return _CTX_CACHE[model]
    ctx: int | None = None
    low = (model or "").lower()
    if low.startswith("claude"):
        ctx = 200_000
    else:
        try:
            from ..auth import catalog_cache
            from ..auth.openrouter_catalog import CACHE_NAME

            payload, _fresh = catalog_cache.read(CACHE_NAME)
            for row in payload or []:
                if isinstance(row, dict) and row.get("id") == model:
                    ctx = int(row.get("context_length") or 0) or None
                    break
        except Exception:
            ctx = None
    _CTX_CACHE[model] = ctx
    return ctx


def input_limit(model: str | None = None) -> int:
    """Tokens a prompt may use: the window minus room for the reply."""
    from ..constants.models import API_MAX_TOKENS

    window = context_window(model or state.MODEL) or DEFAULT_WINDOW
    return max(4_000, window - min(API_MAX_TOKENS, window // 4))


# ── size estimates ───────────────────────────────────────────────────────


def _block(b: Any) -> Any:
    if isinstance(b, dict):
        return b
    dump = getattr(b, "model_dump", None)
    return dump() if callable(dump) else b


def _content_tokens(content: Any) -> float:
    if isinstance(content, str):
        return len(content) / CHARS_PER_TOKEN
    if not isinstance(content, list):
        return 0.0
    total = 0.0
    for raw in content:
        b = _block(raw)
        if not isinstance(b, dict):
            total += len(str(b)) / CHARS_PER_TOKEN
            continue
        kind = b.get("type")
        if kind in ("image", "document"):
            total += IMAGE_TOKENS
        elif kind == "tool_result":
            total += _content_tokens(b.get("content"))
        elif kind == "tool_use":
            total += len(str(b.get("input") or "")) / CHARS_PER_TOKEN + 10
        else:
            text = b.get("text") or b.get("thinking") or ""
            total += len(str(text)) / CHARS_PER_TOKEN
    return total


def estimate_tokens(messages: Iterable[dict], system: Any = None, tools: Any = None) -> int:
    total = 0.0
    for m in messages or []:
        if isinstance(m, dict):
            total += _content_tokens(m.get("content")) + 4
    if system:
        total += _content_tokens(system if not isinstance(system, str) else system)
    if tools:
        total += len(str(tools)) / CHARS_PER_TOKEN
    return int(total)


# ── structure helpers ────────────────────────────────────────────────────


def _blocks_of(m: dict) -> list:
    content = m.get("content")
    return content if isinstance(content, list) else []


def _is_tool_result(b: Any) -> bool:
    return isinstance(b, dict) and b.get("type") == "tool_result"


def is_turn_start(m: Any) -> bool:
    """A user message that starts a turn — text, no tool results (so the
    history can be cut right before it without orphaning a tool_use)."""
    if not isinstance(m, dict) or m.get("role") != "user":
        return False
    content = m.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list) or not content:
        return False
    return not any(_is_tool_result(_block(b)) for b in content)


def current_turn_start(messages: list) -> int:
    for i in range(len(messages) - 1, -1, -1):
        if is_turn_start(messages[i]):
            return i
    return 0


def _result_text(b: dict) -> str:
    content = b.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(x.get("text", "")) for x in content if isinstance(x, dict) and x.get("type") == "text"
        )
    return ""


def is_pack(b: Any) -> bool:
    return _is_tool_result(b) and _result_text(b).startswith(PACK_PREFIX)


_FILE_HEADER = re.compile(r"^--- (\S+) \(", re.M)
_SKIPPED = re.compile(r"^--- (\S+) \(SKIPPED", re.M)


def pack_stub(text: str) -> str:
    """The short stand-in for an older context pack."""
    task = re.search(r"^Task: (.*)$", text, re.M)
    paths = re.search(r"^Paths: (.*)$", text, re.M)
    files = [f for f in dict.fromkeys(_FILE_HEADER.findall(text)) if f not in _SKIPPED.findall(text)]
    lines = [STUB_PREFIX]
    if task:
        lines.append(f"Task: {task.group(1)[:300]}")
    elif paths:
        lines.append(f"Paths: {paths.group(1)[:300]}")
    if files:
        shown = files[:40]
        more = f" (+{len(files) - len(shown)} more)" if len(files) > len(shown) else ""
        lines.append(f"Files it held: {', '.join(shown)}{more}")
    lines.append(
        f"Its full text ({len(text):,} chars) was left out to keep the conversation inside the "
        "context window, and may be out of date if files changed since. Re-read what you need "
        "with read_bundle([...]) or read_file before relying on it."
    )
    return "\n".join(lines)


def _replace_blocks(m: dict, fn) -> dict:
    """Copy of message *m* with each block passed through *fn* (block → block)."""
    content = m.get("content")
    if not isinstance(content, list):
        return m
    new = [fn(_block(b)) for b in content]
    if all(n is o or n == _block(o) for n, o in zip(new, content)):
        return m
    return {**m, "content": new}


def _stub_pack(b: Any) -> Any:
    if not is_pack(b):
        return b
    stub = {"type": "tool_result", "tool_use_id": b.get("tool_use_id", ""), "content": pack_stub(_result_text(b))}
    if b.get("is_error"):
        stub["is_error"] = True
    return stub


def _stub_tool_output(b: Any) -> Any:
    if not _is_tool_result(b) or is_pack(b):
        return b
    text = _result_text(b)
    if text.startswith(STUB_PREFIX) or text == TOOL_STUB:
        return b
    has_image = isinstance(b.get("content"), list) and any(
        isinstance(x, dict) and x.get("type") == "image" for x in b["content"]
    )
    if len(text) < 600 and not has_image:
        return b  # short results (EDITED …, exit=0) cost little and say a lot
    stub = {"type": "tool_result", "tool_use_id": b.get("tool_use_id", ""), "content": TOOL_STUB}
    if b.get("is_error"):
        stub["is_error"] = True
    return stub


# ── layer 1: older packs ─────────────────────────────────────────────────


def collapse_old_packs(messages: list, *, keep_previous: bool = True) -> list:
    """Stub every context pack from before the current turn, except (with
    *keep_previous*) the newest of them — follow-up questions usually build on
    it. Packs made in the current turn always stay whole. Returns a new list;
    *messages* is not modified."""
    start = current_turn_start(messages)
    keep_at: tuple[int, int] | None = None
    if keep_previous:
        for i in range(start - 1, -1, -1):
            blocks = _blocks_of(messages[i]) if isinstance(messages[i], dict) else []
            hits = [j for j, b in enumerate(blocks) if is_pack(_block(b))]
            if hits:
                keep_at = (i, hits[-1])
                break
    out = list(messages)
    for i in range(start):
        m = out[i]
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        blocks = _blocks_of(m)
        if not any(is_pack(_block(b)) for b in blocks):
            continue
        new_blocks = [
            _block(b) if keep_at == (i, j) else _stub_pack(_block(b)) for j, b in enumerate(blocks)
        ]
        out[i] = {**m, "content": new_blocks}
    return out


# ── layer 2: fit an oversized request ────────────────────────────────────


def _recap(dropped: list) -> str:
    asks = []
    for m in dropped:
        if not is_turn_start(m):
            continue
        content = m.get("content")
        text = content if isinstance(content, str) else " ".join(
            str(_block(b).get("text", "")) for b in content if isinstance(_block(b), dict)
            and _block(b).get("type") == "text"
        )
        text = " ".join(text.split())
        if text:
            asks.append(text[:240] + ("…" if len(text) > 240 else ""))
    asks = asks[-15:]
    lines = [
        f"[Context note: the first {len(dropped)} messages of this conversation were left out "
        "so it fits the model's context window. Files may have changed since — re-read anything "
        "you need instead of assuming."
    ]
    if asks:
        lines.append("Earlier requests from the user, oldest first:")
        lines += [f"{n}. {a}" for n, a in enumerate(asks, 1)]
    lines[-1] += "]"
    return "\n".join(lines)


def _prepend_text(m: dict, text: str) -> dict:
    content = m.get("content")
    if isinstance(content, str):
        return {**m, "content": [{"type": "text", "text": text}, {"type": "text", "text": content}]}
    return {**m, "content": [{"type": "text", "text": text}, *[_block(b) for b in (content or [])]]}


def fit_to_window(
    messages: list,
    *,
    limit: int,
    fixed_tokens: int = 0,
    target: float = TARGET,
    ceiling: int | None = None,
) -> tuple[list, list[str]]:
    """Shrink *messages* (a request copy) until system + tools + messages fit in
    ``target * limit`` tokens (and under *ceiling*, when given — after a
    provider refused a request our estimate thought fitted). Returns
    (messages, steps taken). Each step only runs while still over; the current
    turn's user message is always kept."""
    goal = int(limit * target)
    if ceiling is not None:
        goal = min(goal, ceiling)
    steps: list[str] = []

    def size(msgs: list) -> int:
        return fixed_tokens + estimate_tokens(msgs)

    if size(messages) <= goal:
        return messages, steps
    start = current_turn_start(messages)

    # 1. Every pack from before this turn → stub (no exception for the newest).
    out = collapse_old_packs(messages, keep_previous=False)
    if out != messages:
        steps.append("collapsed earlier context packs")
    if size(out) <= goal:
        return out, steps

    # 2. Long tool output from before this turn → stub.
    before = out
    out = [_replace_blocks(m, _stub_tool_output) if i < start and isinstance(m, dict) else m
           for i, m in enumerate(out)]
    if out != before:
        steps.append("trimmed earlier tool output")
    if size(out) <= goal:
        return out, steps

    # 3. Leave out the oldest whole turns; a recap of what the user asked
    #    goes in front of the first turn that stays.
    starts = [i for i, m in enumerate(out) if is_turn_start(m) and 0 < i <= start]
    for cut in starts:
        candidate = out[cut:]
        candidate = [_prepend_text(candidate[0], _recap(out[:cut])), *candidate[1:]]
        if size(candidate) <= goal or cut == starts[-1]:
            steps.append(f"left out the oldest {cut} messages (recap kept)")
            out, start = candidate, 0
            break
    if size(out) <= goal:
        return out, steps

    # 4. Inside the current turn: packs but the newest → stub, then long tool
    #    output except the last few results.
    pack_spots = [(i, j) for i, m in enumerate(out) if isinstance(m, dict)
                  for j, b in enumerate(_blocks_of(m)) if is_pack(_block(b))]
    if len(pack_spots) > 1:
        keep = pack_spots[-1]
        out = [
            {**m, "content": [_block(b) if (i, j) == keep else _stub_pack(_block(b))
                              for j, b in enumerate(_blocks_of(m))]}
            if any(p[0] == i for p in pack_spots) else m
            for i, m in enumerate(out)
        ]
        steps.append("collapsed older packs in this turn")
    if size(out) > goal:
        result_msgs = [i for i, m in enumerate(out) if isinstance(m, dict)
                       and any(_is_tool_result(_block(b)) for b in _blocks_of(m))]
        protect = set(result_msgs[-3:])
        before = out
        out = [_replace_blocks(m, _stub_tool_output) if i in result_msgs and i not in protect else m
               for i, m in enumerate(out)]
        if out != before:
            steps.append("trimmed older tool output in this turn")
    if size(out) > goal:
        # Last resort: even the newest pack goes.
        before = out
        out = [_replace_blocks(m, _stub_pack) if isinstance(m, dict) else m for m in out]
        if out != before:
            steps.append("collapsed the newest context pack")
    return out, steps


# ── layer 3: the provider still said "too long" ──────────────────────────

_OVERFLOW_HINTS = (
    "prompt is too long", "context_length_exceeded", "maximum context length",
    "exceed context limit", "exceeds the context window", "context window",
    "too many tokens", "input is too long", "request too large", "request_too_large",
    "reduce the length", "input tokens exceed", "token limit", "maximum prompt length",
)


def is_context_overflow(err: BaseException) -> bool:
    status = getattr(err, "status_code", None)
    text = str(err).lower()
    if status == 413:
        return True
    return any(h in text for h in _OVERFLOW_HINTS)


# ── new packs ────────────────────────────────────────────────────────────


def pack_char_cap(requested: int, default: int) -> int:
    """Chars a new context pack may use: what was asked (or the default), no
    more than PACK_SHARE of the model's window, and no more than half the room
    the conversation has left."""
    cap = requested if requested and requested > 0 else default
    limit = input_limit()
    cap = min(cap, int(limit * PACK_SHARE * CHARS_PER_TOKEN))
    used = int(getattr(state, "total_in", 0) or 0)
    if used:
        room = max(0, limit - used)
        cap = min(cap, int(room * 0.5 * CHARS_PER_TOKEN))
    return max(PACK_MIN_CHARS, cap)
