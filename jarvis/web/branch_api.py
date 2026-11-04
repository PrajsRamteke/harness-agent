"""Reply actions under an assistant message on the web: Retry and Branch.

* **Retry** (the latest reply only): cut the conversation back to the user's
  last prompt, so ``/retry`` sends it again and the model answers afresh.
* **Branch into new chat**: a new session holding the conversation up to and
  including one reply; the page then opens it in a new header tab.
* **Unanswered prompt** (``prompt_unsent``): the turn was stopped before any
  reply. ``edit`` takes the prompt back out of the conversation (the page puts
  its text in the message box); ``resend`` says whether ``/retry`` can send it
  again as it is (attachments included).

The page names a reply by its text (+ which occurrence of that text, for
replies that repeat) — the transcript it shows can hold command echoes and
notices that aren't in ``state.messages``, so positions wouldn't line up.
UI-free; runs on the terminal's thread via ``run_web_action``.
"""
from __future__ import annotations

import copy
from typing import Any

from .. import state


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def _blocks(msg: dict) -> list[Any]:
    content = msg.get("content")
    return content if isinstance(content, list) else []


def _block(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return raw.model_dump()  # an SDK block that slipped into history
    except Exception:
        return {"type": getattr(raw, "type", ""), "text": getattr(raw, "text", "")}


def is_prompt(msg: dict) -> bool:
    """A message the user typed (text / attachments), not tool results."""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    return any(_block(b).get("type") != "tool_result" for b in content or [])


def find_reply(messages: list[dict], text: str, nth: int = 0) -> tuple[int, int] | None:
    """(message index, block index or -1) of the ``nth`` assistant text equal to ``text``."""
    want = _norm(text)
    if not want:
        return None
    seen = 0
    candidates: list[tuple[int, int]] = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            spots = [(-1, content)]
        else:
            spots = [(j, _block(b).get("text") or "") for j, b in enumerate(content or [])
                     if _block(b).get("type") == "text"]
        for j, t in spots:
            norm = _norm(t)
            if norm == want:
                if seen == nth:
                    return i, j
                seen += 1
            elif norm and (norm.startswith(want[:200]) or want.startswith(norm[:200])):
                candidates.append((i, j))  # a streamed copy that differs at the edges
    return candidates[min(nth, len(candidates) - 1)] if candidates else None


def branch_messages(messages: list[dict], text: str, nth: int = 0) -> list[dict] | None:
    """The conversation up to and including that reply (later blocks of its message dropped,
    so no tool call is left without its result)."""
    spot = find_reply(messages, text, nth)
    if spot is None:
        return None
    i, j = spot
    out = copy.deepcopy(messages[:i + 1])
    if j >= 0:
        last = dict(out[-1])
        last["content"] = list(_blocks(last))[:j + 1]
        out[-1] = last
    return out


def retry_messages(messages: list[dict]) -> list[dict] | None:
    """The conversation cut back to (and ending on) the user's last prompt."""
    for i in range(len(messages) - 1, -1, -1):
        if is_prompt(messages[i]):
            return copy.deepcopy(messages[:i + 1])
    return None


def unanswered_prompt(messages: list[dict]) -> int | None:
    """Index of the user's last prompt when nothing answered it (stopped right away)."""
    if messages and is_prompt(messages[-1]):
        return len(messages) - 1
    return None


def _run_unsent(op: str, messages: list[dict]) -> dict[str, Any]:
    from ..storage.sessions import db_replace_session_messages

    idx = unanswered_prompt(messages)
    if op == "resend":
        # Not in the conversation (stopped before Jarvis took it): the page sends the text anew.
        return {"ok": True, "retry": idx is not None}
    if op != "edit":
        return {"ok": False, "error": f"unknown op: {op}"}
    if idx is None:
        return {"ok": True, "removed": False}
    cut = copy.deepcopy(messages[:idx])
    state.messages = cut
    if state.current_session_id:
        db_replace_session_messages(state.current_session_id, cut)
    return {"ok": True, "removed": True, "session_id": state.current_session_id}


def run(action: str, data: dict[str, Any], *, console_print) -> dict[str, Any]:
    from ..storage.sessions import (
        db_create_session, db_get_title, db_rename_session, db_replace_session_messages,
    )

    text = str(data.get("text") or "")
    try:
        nth = max(0, int(data.get("nth") or 0))
    except (TypeError, ValueError):
        nth = 0
    messages = list(state.messages or [])

    if action == "prompt_unsent":
        return _run_unsent(str(data.get("op") or ""), messages)

    if action == "reply_branch":
        msgs = branch_messages(messages, text, nth)
        if not msgs:
            return {"ok": False, "error": "Couldn't find that reply in this chat."}
        sid = db_create_session(state.MODEL)
        db_replace_session_messages(sid, msgs)
        base = ""
        try:
            base = db_get_title(state.current_session_id) if state.current_session_id else ""
        except Exception:
            pass
        try:
            db_rename_session(sid, f"{base or 'Chat'} · branch")
        except Exception:
            pass
        console_print(f"[green]⑂ branched into session #{sid}[/] [dim](from the web)[/]")
        return {"ok": True, "session_id": sid}

    if action == "reply_retry":
        if text:
            spot = find_reply(messages, text, nth)
            last_prompt = max((i for i, m in enumerate(messages) if is_prompt(m)), default=-1)
            if spot is None or spot[0] < last_prompt:
                return {"ok": False, "error": "Only the latest reply can be retried."}
        cut = retry_messages(messages)
        if not cut:
            return {"ok": False, "error": "Nothing to retry yet — send a message first."}
        state.messages = cut
        if state.current_session_id:
            db_replace_session_messages(state.current_session_id, cut)
        return {"ok": True, "session_id": state.current_session_id}

    return {"ok": False, "error": f"unknown action: {action}"}
