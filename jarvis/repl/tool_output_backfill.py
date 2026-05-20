"""Backfill in-memory tool_output_history after session resume or empty deque."""
from __future__ import annotations

import json

from .. import state
from ..constants import TOOL_UI_HISTORY_SIZE
from .tool_runs import is_dock_tool, list_runs


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    return str(content or "")


def backfill_tool_output_history() -> bool:
    """Populate tool_output_history from live runs, then from session messages."""
    if state.tool_output_history:
        return True

    for run in list_runs():
        body = (run.get("content") or "").strip()
        if body:
            state.record_tool_output(
                str(run.get("name") or "tool"),
                str(run.get("label") or "")[:120],
                body,
            )

    if state.tool_output_history:
        return True

    use_meta: dict[str, tuple[str, str]] = {}
    for msg in state.messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tid = block.get("id")
            if not tid:
                continue
            inp = block.get("input") or {}
            ap = json.dumps(inp, ensure_ascii=False)[:120]
            use_meta[str(tid)] = (str(block.get("name") or "tool"), ap)

    collected: list[tuple[str, str, str]] = []
    for msg in state.messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = str(block.get("tool_use_id") or "")
            name, ap = use_meta.get(tid, ("tool", ""))
            body = _tool_result_text(block.get("content")).strip()
            if body:
                collected.append((name, ap, body))

    for name, ap, body in collected[-TOOL_UI_HISTORY_SIZE:]:
        state.record_tool_output(name, ap, body)

    return bool(state.tool_output_history)


def peekable_file_runs() -> list[dict]:
    """Runs for ^P: live registry first, else file tools from backfilled history."""
    runs = list_runs()
    if runs:
        return runs
    backfill_tool_output_history()
    out: list[dict] = []
    for i, entry in enumerate(state.tool_output_history):
        name = entry.get("name") or "tool"
        if not is_dock_tool(name):
            continue
        out.append(
            {
                "id": f"hist-{i}",
                "name": name,
                "status": "done",
                "label": (entry.get("args") or "")[:120],
                "paths": [],
                "started": entry.get("ts") or 0,
                "ended": entry.get("ts"),
                "chars": len(entry.get("content") or ""),
                "content": entry.get("content") or "",
                "error": str(entry.get("content") or "").lstrip().upper().startswith("ERROR"),
            }
        )
    return out


def build_inspector_entries() -> list[dict]:
    """Unified list for the ^F tools modal: files first, then all tools (deduped)."""
    backfill_tool_output_history()
    entries: list[dict] = []
    seen: set[str] = set()

    def _add(
        *,
        eid: str,
        section: str,
        name: str,
        subtitle: str,
        content: str,
        ts: float = 0,
        status: str | None = None,
    ) -> None:
        body = (content or "").strip()
        key = f"{name}\0{subtitle}\0{body[:240]}"
        if key in seen:
            return
        seen.add(key)
        entries.append(
            {
                "id": eid,
                "section": section,
                "name": name,
                "subtitle": subtitle,
                "content": content,
                "ts": ts,
                "status": status,
            }
        )

    for run in peekable_file_runs():
        _add(
            eid=str(run.get("id") or ""),
            section="file",
            name=str(run.get("name") or "tool"),
            subtitle=str(run.get("label") or ""),
            content=str(run.get("content") or ""),
            ts=float(run.get("started") or 0),
            status=run.get("status"),
        )

    for i, entry in enumerate(state.tool_output_history):
        _add(
            eid=f"hist-{i}",
            section="tool",
            name=str(entry.get("name") or "tool"),
            subtitle=str(entry.get("args") or "")[:120],
            content=str(entry.get("content") or ""),
            ts=float(entry.get("ts") or 0),
        )

    return entries


def inspector_has_entries() -> bool:
    return bool(build_inspector_entries())
