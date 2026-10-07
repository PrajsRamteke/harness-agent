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


def _tool_entry(block: dict, results: dict[str, tuple[str, bool]],
                raw_outputs: dict[str, str] | None = None) -> dict[str, Any]:
    from .console_mux import tool_row_fields

    tid = str(block.get("id") or "")
    name = str(block.get("name") or "tool")
    done = tid in results
    output, is_err = results.get(tid, ("", False))
    fields = tool_row_fields(name, block.get("input"), output if done else None)
    error = is_err or bool(fields.pop("summary_error", False))
    entry: dict[str, Any] = {
        "role": "tool",
        "id": tid,
        "name": name,
        "title": fields.get("title") or name,
        "args": fields.get("args") or "",
        "summary": fields.get("summary") or "",
        "status": ("error" if error else "done") if done else "pending",
        "text": name,
    }
    # Full output lives in tool_output_history (keyed by tool id); keep it out
    # of every transcript payload (SSE/poll/snapshot) so loads stay fast, and
    # let the page fetch it on demand from /api/tool-output?id=<tool id>.
    if done and fields.get("has_full"):
        entry["has_full"] = True
        entry["full_chars"] = int(fields.get("output_chars") or 0)
    # Screenshots: the transcript's tool_result holds base64 only; the raw
    # output (with the image's path) is still in this run's tool history.
    if name == "spawn_agents":
        # The parallel-agents card: live board, or rebuilt from the result.
        try:
            from ..subagents import board_for

            board = board_for(tid, block.get("input"), output if done else None)
        except Exception:
            board = None
        if board:
            entry["agents"] = board
    raw = (raw_outputs or {}).get(tid)
    if raw:
        from ..media import images_in_output

        images = images_in_output(raw)
        if images:
            entry["images"] = images
    return entry


def subagents_board(tool_id: str) -> dict[str, Any] | None:
    """Full board (reports included) for one spawn_agents call, or None."""
    from .. import state
    from ..subagents import board_for

    tool_id = str(tool_id or "")
    if not tool_id:
        return None
    for m in reversed(state.messages):
        if m.get("role") != "assistant" or not isinstance(m.get("content"), list):
            continue
        for b in m["content"]:
            d = _block_dict(b)
            if d.get("type") == "tool_use" and str(d.get("id")) == tool_id:
                results = _tool_results(state.messages)
                out = results.get(tool_id)
                return board_for(tool_id, d.get("input"), out[0] if out else None)
    return board_for(tool_id)


def _raw_image_outputs() -> dict[str, str]:
    """tool id → raw output, for recent tool calls that attached an image."""
    from .. import state

    return {
        str(e.get("id")): str(e.get("content") or "")
        for e in list(state.tool_output_history)
        if e.get("id") and "[[jarvis:image " in str(e.get("content") or "")
    }


def tool_output_text(tool_id: str) -> dict[str, Any] | None:
    """Full recorded output for one tool call (for the web "full output" viewer).

    Live registry first (freshest, has running output), then the recorded
    history deque, then the session messages as a last resort. Returns a dict
    with ``found``/``output``/``chars``/``truncated`` — or None when unknown.
    """
    tid = (tool_id or "").strip()
    if not tid:
        return None
    from .. import state
    from ..constants import TOOL_UI_VIEWER_MAX_CHARS

    try:
        from ..repl.tool_runs import run_by_id

        run = run_by_id(tid)
        if run and (run.get("content") or run.get("status") in ("running", "queued")):
            raw = str(run.get("content") or "")
            return {
                "id": tid,
                "name": str(run.get("name") or "tool"),
                "args": str(run.get("label") or run.get("args") or "")[:240],
                "status": str(run.get("status") or ""),
                "output": raw,
                "chars": len(raw),
                "truncated": False,
            }
    except Exception:
        pass

    for entry in list(state.tool_output_history):
        if str(entry.get("id") or "") == tid and str(entry.get("content") or ""):
            raw = str(entry.get("content") or "")
            out = raw
            cut = len(raw) > TOOL_UI_VIEWER_MAX_CHARS
            if cut:
                out = raw[:TOOL_UI_VIEWER_MAX_CHARS] + (
                    f"\n\n… truncated for viewer ({len(raw):,} chars total)"
                )
            return {
                "id": tid,
                "name": str(entry.get("name") or "tool"),
                "args": str(entry.get("args") or "")[:240],
                "output": out,
                "chars": len(raw),
                "truncated": cut,
            }

    # Last resort: the raw tool_result in the transcript (uncapped).
    for msg in state.messages:
        content = msg.get("content")
        if msg.get("role") != "user" or not isinstance(content, list):
            continue
        for raw_block in content:
            block = _block_dict(raw_block)
            if block.get("type") == "tool_result" and str(block.get("tool_use_id") or "") == tid:
                raw = _tool_result_text(block)
                if raw.strip():
                    out = raw
                    cut = len(raw) > TOOL_UI_VIEWER_MAX_CHARS
                    if cut:
                        out = raw[:TOOL_UI_VIEWER_MAX_CHARS] + (
                            f"\n\n… truncated for viewer ({len(raw):,} chars total)"
                        )
                    return {
                        "id": tid,
                        "name": "tool",
                        "args": "",
                        "output": out,
                        "chars": len(raw),
                        "truncated": cut,
                    }
    return None


def snapshot_messages() -> list[dict[str, Any]]:
    """Build web transcript entries in the same order as the TUI replay.

    Tool calls become structured ``tool`` rows (paired with their results);
    thinking is included only while the trace is on.
    """
    from .. import state
    from ..media import attachments_in
    from ..prompt_queue import strip_steer

    out: list[dict[str, Any]] = []
    trace = bool(state.show_internal)
    results = _tool_results(state.messages)
    raw_outputs = _raw_image_outputs()

    for msg in state.messages:
        role = msg.get("role") or ""
        content = msg.get("content")

        if role == "user":
            shown, files = attachments_in(content)
            if files:
                shown, steered = strip_steer(shown)
                entry = {"role": "you", "text": shown, "title": "You", "attachments": files}
                if steered:
                    entry["steered"] = True
                out.append(entry)
                continue
            text, steered = strip_steer(_content_text(content))
            if text:
                entry = {"role": "you", "text": text, "title": "You"}
                if steered:
                    entry["steered"] = True  # "send now": joined the turn mid-way
                out.append(entry)
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
                    out.append(_tool_entry(block, results, raw_outputs))
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


def _cwd_fields() -> dict[str, Any]:
    """The folder this Jarvis works in (the page's folder chip) and whether it has a terminal."""
    from .. import state
    from .fs_api import display

    try:
        cwd = os.getcwd()
    except OSError:
        cwd = ""
    return {"cwd": cwd, "cwd_display": display(cwd) if cwd else "", "headless": bool(state.headless)}


def _project_name() -> str:
    try:
        return os.path.basename(os.getcwd().rstrip(os.sep)) or os.getcwd()
    except OSError:
        return ""


def jobs_fields() -> list[dict[str, Any]]:
    """Background jobs (``run_bg``) for the Activity tab, newest last.

    Only stable values: a running job carries its wall-clock start (the page
    counts up by itself), a finished one its final duration — so an unchanged
    job list never looks like news to ``StateWatcher``.
    """
    try:
        from ..tools import background
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for job in background.jobs()[-12:]:
        running = job.running
        out.append({
            "id": job.id,
            "cmd": " ".join(job.cmd.split())[:160],
            "status": "running" if running else ("killed" if job.killed else "done"),
            "code": job.code,
            "started": int(job.started_at),
            "secs": None if running else int(round(job.elapsed)),
        })
    return out


def _model_sees_images() -> bool:
    """Can the current model see attached images (the tray warns when it can't)."""
    from .. import state
    from ..constants.providers import model_supports_images

    try:
        return bool(model_supports_images(state.MODEL))
    except Exception:
        return True  # unknown: don't warn


def state_fields(*, busy: bool = False, session_title: str | None = None) -> dict[str, Any]:
    """Everything the web UI shows about the session, except the transcript.

    Cheap enough to poll every second (``StateWatcher``); pass
    ``session_title`` to skip the database read when it is already known.
    """
    from .. import state, prompt_queue
    from ..storage import pin as pin_store

    _, pin_chars = pin_store.pin_stats()
    pin_lines = len(pin_store.pin_items())  # rules, not blank lines

    return {
        "message_count": len(state.messages),
        "session_title": (
            _session_title(state.current_session_id) if session_title is None else session_title
        ),
        "project": _project_name(),
        **_cwd_fields(),
        "global_agents": bool(getattr(state, "global_agents", False)),
        "global_skills": bool(getattr(state, "global_skills", False)),
        "global_mcp": bool(getattr(state, "global_mcp", False)),
        "busy": busy,
        "queue": prompt_queue.labels(),
        "queue_items": prompt_queue.public(),
        "pin": {"lines": pin_lines, "enabled": pin_store.is_enabled(), "chars": pin_chars},
        "model": state.MODEL,
        "vision": _model_sees_images(),
        "session_id": state.current_session_id,
        "agent": state.active_agent_name or "",
        "provider": state.provider,
        "think_mode": state.think_mode,
        "think_effort": state.think_effort,
        "think": _think_fields(),
        "show_internal": state.show_internal,
        "auto_approve": state.auto_approve,
        "tokens_in": state.total_in,
        "tokens_out": state.total_out,
        "tokens_total": state.total_tokens,
        "tokens_cache_read": state.cache_read_tokens,
        "tool_calls": state.tool_calls_count,
        "jobs": jobs_fields(),
    }


def _think_fields() -> dict[str, Any]:
    """What the current model takes for thinking (levels, on/off only, always on,
    none) and what is really sent — the picker's rows ride along."""
    try:
        from ..repl import thinking

        return thinking.public()
    except Exception:
        return {"known": False, "mode": "unknown", "levels": [], "choices": []}


def snapshot_from_state(*, busy: bool = False) -> dict[str, Any]:
    from .. import file_changes, media

    snap = state_fields(busy=busy)
    snap["messages"] = snapshot_messages()
    snap["changes"] = file_changes.summaries()
    snap["upload_limits"] = {"max_mb": media.MAX_FILE_MB, "max_files": media.MAX_FILES}
    return snap


def apply_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Apply toggles from the web UI; returns updated settings subset."""
    from .. import state

    result: dict[str, Any] = {}

    if "think_mode" in data or "think_effort" in data:
        from ..repl import thinking

        # A choice the current model can't take is refused with the reason (the
        # page shows it), not silently turned into another one.
        if "think_mode" in data:
            reason = thinking.set_preference("on" if data["think_mode"] else "off")
            if reason:
                result["error"] = reason
        if "think_effort" in data and "error" not in result:
            effort = str(data["think_effort"]).strip().lower()
            if effort in THINK_EFFORTS:
                reason = thinking.set_preference(effort)
                if reason:
                    result["error"] = reason
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
