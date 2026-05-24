"""Session snapshot and settings mutations for the web remote API."""
from __future__ import annotations

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


def _count_tool_results(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    return sum(
        1 for raw in content
        if _block_dict(raw).get("type") == "tool_result"
    )


def snapshot_messages() -> list[dict[str, str]]:
    """Build web transcript entries — mirrors TUI session replay ordering."""
    from .. import state

    out: list[dict[str, str]] = []
    trace = bool(state.show_internal)

    for msg in state.messages:
        role = msg.get("role") or ""
        content = msg.get("content")

        if role == "user":
            text = _content_text(content)
            if text:
                out.append({"role": "you", "text": text, "title": "You"})
            if trace and isinstance(content, list):
                hide_results = _count_tool_results(content) >= 2
                for raw in content:
                    block = _block_dict(raw)
                    if block.get("type") != "tool_result":
                        continue
                    if hide_results:
                        continue
                    body = _tool_result_text(block)
                    if body:
                        out.append({
                            "role": "log",
                            "text": body[:2000],
                            "title": "tool result",
                        })
            continue

        if role == "assistant":
            if isinstance(content, list):
                if trace:
                    for raw in content:
                        block = _block_dict(raw)
                        btype = block.get("type")
                        if btype == "thinking":
                            t = str(block.get("thinking") or "").strip()
                            if t:
                                out.append({"role": "thinking", "text": t, "title": "thinking"})
                        elif btype == "tool_use":
                            name = str(block.get("name") or "tool")
                            args = str(block.get("input") or "")[:800].strip()
                            detail = f"tool: {name}"
                            if args:
                                detail = f"{detail}\n{args}"
                            out.append({"role": "log", "text": detail, "title": f"tool · {name}"})
                text = _content_text(content)
                if text:
                    out.append({"role": "assistant", "text": text, "title": "Jarvis"})
            else:
                text = _content_text(content)
                if text:
                    out.append({"role": "assistant", "text": text, "title": "Jarvis"})
            continue

        text = _content_text(content) if not isinstance(content, str) else content.strip()
        if text:
            out.append({"role": role, "text": text, "title": role})

    return out


def snapshot_from_state(*, busy: bool = False) -> dict[str, Any]:
    from .. import state

    messages = snapshot_messages()

    queue_items: list[str] = []
    for item in state.prompt_queue:
        if isinstance(item, tuple):
            queue_items.append(str(item[0]).strip())
        else:
            queue_items.append(str(item).strip())

    return {
        "messages": messages,
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
