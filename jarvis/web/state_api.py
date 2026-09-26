"""Session snapshot and settings mutations for the web remote API."""
from __future__ import annotations

import os
from typing import Any

from ..constants import THINK_EFFORTS, DEFAULT_THINK_EFFORT


def message_text(content: Any, *, include_thinking: bool = False) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            btype = block.get("type")
            if btype == "text":
                parts.append(str(block.get("text") or ""))
            elif btype == "thinking" and include_thinking:
                parts.append(str(block.get("thinking") or ""))
            elif btype == "tool_use":
                parts.append(f"[tool: {block.get('name', '?')}]")
            elif btype == "tool_result":
                parts.append(str(block.get("content") or ""))
        elif hasattr(block, "type") and block.type == "text":
            parts.append(getattr(block, "text", "") or "")
    return "\n".join(p for p in parts if p.strip()).strip()


def _block_dict(block: Any) -> dict:
    """Normalise a message content block to a plain dict."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        try:
            return block.model_dump()
        except Exception:
            pass
    if hasattr(block, "__dict__"):
        return {k: v for k, v in block.__dict__.items() if not k.startswith("_")}
    return {}


def _content_text(content: Any) -> str:
    """User/assistant visible text — mirrors TUI ``_content_text``."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for raw in content:
        block = _block_dict(raw)
        btype = block.get("type")
        if btype == "text":
            texts.append(str(block.get("text") or ""))
        elif btype == "image":
            texts.append("[image]")
    return "\n\n".join(t for t in texts if t).strip()


def _tool_result_text(block: dict) -> str:
    body = block.get("content", "")
    if isinstance(body, list):
        parts: list[str] = []
        for item in body:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            elif hasattr(item, "model_dump"):
                parts.append(str(item.model_dump().get("text") or ""))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p).strip()
    return str(body or "").strip()


def _tool_results(messages: list[dict]) -> dict[str, tuple[str, bool]]:
    """``tool_use_id`` → (result text, is_error) across the whole transcript."""
    results: dict[str, tuple[str, bool]] = {}
    for msg in messages:
        content = msg.get("content")
        if msg.get("role") != "user" or not isinstance(content, list):
            continue
        for raw in content:
            block = _block_dict(raw)
            if block.get("type") == "tool_result":
                results[str(block.get("tool_use_id"))] = (
                    _tool_result_text(block),
                    bool(block.get("is_error")),
                )
    return results


def _tool_entry(block: dict, results: dict[str, tuple[str, bool]]) -> dict[str, Any]:
    from .console_mux import tool_row_fields

    tid = str(block.get("id") or "")
    name = str(block.get("name") or "tool")
    done = tid in results
    output, is_err = results.get(tid, ("", False))
    fields = tool_row_fields(name, block.get("input"), output if done else None)
    error = is_err or bool(fields.pop("summary_error", False))
    return {
        "role": "tool",
        "id": tid,
        "name": name,
        "title": fields.get("title") or name,
        "args": fields.get("args") or "",
        "summary": fields.get("summary") or "",
        "status": ("error" if error else "done") if done else "pending",
        "text": name,
    }


def snapshot_messages() -> list[dict[str, Any]]:
    """Build web transcript entries in the same order as the TUI replay.

    Tool calls become structured ``tool`` rows (paired with their results);
    thinking is included only while the trace is on.
    """
    from .. import state

    out: list[dict[str, Any]] = []
    trace = bool(state.show_internal)
    results = _tool_results(state.messages)

    for msg in state.messages:
        role = msg.get("role") or ""
        content = msg.get("content")

        if role == "user":
            text = _content_text(content)
            if text:
                out.append({"role": "you", "text": text, "title": "You"})
            continue

        if role == "assistant":
            if not isinstance(content, list):
                text = _content_text(content)
                if text:
                    out.append({"role": "assistant", "text": text, "title": "Jarvis"})
                continue
            for raw in content:
                block = _block_dict(raw)
                btype = block.get("type")
                if btype == "thinking":
                    t = str(block.get("thinking") or "").strip()
                    if t and trace:
                        out.append({"role": "thinking", "text": t, "title": "thinking"})
                elif btype == "text":
                    t = str(block.get("text") or "").strip()
                    if t:
                        out.append({"role": "assistant", "text": t, "title": "Jarvis"})
                elif btype == "tool_use":
                    out.append(_tool_entry(block, results))
            continue

        text = _content_text(content) if not isinstance(content, str) else content.strip()
        if text:
            out.append({"role": role, "text": text, "title": role})

    return out


def _session_title(session_id: Any) -> str:
    if not session_id:
        return ""
    try:
        from ..storage.sessions import db_conn

        conn = db_conn()
        try:
            row = conn.execute(
                "SELECT title FROM sessions WHERE id = ?", (int(session_id),)
            ).fetchone()
        finally:
            conn.close()
        return str((row[0] if row else "") or "").strip()
    except Exception:
        return ""


def _project_name() -> str:
    try:
        return os.path.basename(os.getcwd().rstrip(os.sep)) or os.getcwd()
    except OSError:
        return ""


def state_fields(*, busy: bool = False, session_title: str | None = None) -> dict[str, Any]:
    """Everything the web UI shows about the session, except the transcript.

    Cheap enough to poll every second (``StateWatcher``); pass
    ``session_title`` to skip the database read when it is already known.
    """
    from .. import state

    queue_items: list[str] = []
    for item in list(state.prompt_queue):
        if isinstance(item, tuple):
            queue_items.append(str(item[0]).strip())
        else:
            queue_items.append(str(item).strip())

    return {
        "message_count": len(state.messages),
        "session_title": (
            _session_title(state.current_session_id) if session_title is None else session_title
        ),
        "project": _project_name(),
        "global_agents": bool(getattr(state, "global_agents", False)),
        "global_skills": bool(getattr(state, "global_skills", False)),
        "global_mcp": bool(getattr(state, "global_mcp", False)),
        "busy": busy,
        "queue": [q for q in queue_items if q],
        "model": state.MODEL,
        "session_id": state.current_session_id,
        "agent": state.active_agent_name or "",
        "provider": state.provider,
        "think_mode": state.think_mode,
        "think_effort": state.think_effort,
        "show_internal": state.show_internal,
        "auto_approve": state.auto_approve,
        "tokens_in": state.total_in,
        "tokens_out": state.total_out,
        "tokens_total": state.total_tokens,
        "tool_calls": state.tool_calls_count,
    }


def snapshot_from_state(*, busy: bool = False) -> dict[str, Any]:
    snap = state_fields(busy=busy)
    snap["messages"] = snapshot_messages()
    return snap


def apply_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Apply toggles from the web UI; returns updated settings subset."""
    from .. import state

    result: dict[str, Any] = {}

    if "think_mode" in data:
        state.think_mode = bool(data["think_mode"])
        if state.think_mode and state.think_effort == "none":
            state.think_effort = DEFAULT_THINK_EFFORT
        state.save_think_config()
        result["think_mode"] = state.think_mode
        result["think_effort"] = state.think_effort

    if "think_effort" in data:
        effort = str(data["think_effort"]).strip().lower()
        if effort in THINK_EFFORTS:
            state.think_effort = effort
            state.think_mode = effort != "none"
            state.save_think_config()
            result["think_mode"] = state.think_mode
            result["think_effort"] = state.think_effort

    if "show_internal" in data:
        state.show_internal = bool(data["show_internal"])
        state.save_trace_config()
        result["show_internal"] = state.show_internal

    if "auto_approve" in data:
        state.auto_approve = bool(data["auto_approve"])
        result["auto_approve"] = state.auto_approve

    return result
