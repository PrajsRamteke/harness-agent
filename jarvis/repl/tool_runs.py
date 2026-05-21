"""Live registry of file-oriented tool runs for the TUI activity dock (^P peek)."""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from .tool_activity import _norm_input

_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}
_order: list[str] = []

# Tools whose paths appear in the parallel-files dock.
FILE_DOCK_TOOLS = frozenset({
    "read_file",
    "read_document",
    "write_file",
    "edit_file",
    "multi_edit",
    "read_bundle",
    "resolve_context",
    "glob_files",
    "list_dir",
    "search_code",
    "rank_files",
    "fast_find",
    "git_diff",
    "read_images_text",
    "read_image_text",
})


def multi_edit_paths(raw_input: Any) -> list[str]:
    """Unique file paths from a multi_edit call, in first-seen order."""
    d = _norm_input(raw_input)
    raw = d.get("edits")
    if not isinstance(raw, list):
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        p = str(item.get("path") or "").strip()
        if p and p not in seen:
            seen.add(p)
            paths.append(p)
    return paths


def _children_of(parent_id: str) -> list[str]:
    with _lock:
        return [tid for tid in _order if _runs.get(tid, {}).get("parent_id") == parent_id]


def _target_run_ids(tool_id: str) -> list[str]:
    """Parent multi_edit id → expanded child rows; otherwise the id itself."""
    children = _children_of(tool_id)
    return children if children else [tool_id]


def _split_multi_edit_output(content: str, paths: list[str]) -> dict[str, tuple[str, bool]]:
    """Map each path to (per-file output snippet, is_error)."""
    summary_lines: list[str] = []
    per_path: dict[str, list[str]] = {p: [] for p in paths}
    for line in (content or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^\d+\s+succeeded,\s+\d+\s+failed\s*$", stripped):
            summary_lines.append(stripped)
            continue
        matched = False
        for path in paths:
            if f" EDITED {path}" in stripped or f" ERROR {path}:" in stripped:
                per_path[path].append(stripped)
                matched = True
                break
        if not matched and stripped.upper().startswith("ERROR"):
            summary_lines.append(stripped)

    summary = "\n".join(summary_lines)
    out: dict[str, tuple[str, bool]] = {}
    for path in paths:
        lines = per_path.get(path) or []
        has_edit = any(" EDITED " in ln for ln in lines)
        has_err = any(" ERROR " in ln for ln in lines)
        body = "\n".join(lines)
        if summary and body:
            text = f"{summary}\n{body}"
        else:
            text = body or summary or content
        out[path] = (text, has_err and not has_edit)
    return out


def _register_run_entry(
    tool_id: str,
    name: str,
    label: str,
    paths: list[str],
    *,
    parent_id: str = "",
    notify: bool = False,
) -> None:
    with _lock:
        _runs[tool_id] = {
            "id": tool_id,
            "name": name,
            "status": "queued",
            "label": label,
            "paths": paths,
            "parent_id": parent_id,
            "started": time.time(),
            "ended": None,
            "chars": 0,
            "content": "",
            "error": False,
        }
        if tool_id not in _order:
            _order.append(tool_id)
    if notify:
        _notify_ui()


def expand_dock_tool_use(data: dict) -> list[dict]:
    """Expand one tool_use into N dock rows when it touches multiple files."""
    name = str(data.get("name") or "")
    if name != "multi_edit":
        return [data]
    paths = multi_edit_paths(data.get("input"))
    if len(paths) < 2:
        return [data]
    return [{**data, "_dock_label": path} for path in paths]


def _notify_ui() -> None:
    try:
        from ..console import console

        fn = getattr(console, "refresh_tool_activity", None)
        if callable(fn):
            fn()
    except Exception:
        pass


def _notify_wave_begin() -> None:
    try:
        from ..console import console

        fn = getattr(console, "reset_tool_activity_panel", None)
        if callable(fn):
            fn()
    except Exception:
        pass


def _display_for_tool(name: str, raw_input: Any) -> tuple[str, list[str]]:
    """Return (primary label, all paths) for dock / picker rows."""
    d = _norm_input(raw_input)
    paths: list[str] = []

    if name == "read_file":
        p = str(d.get("path") or "").strip()
        return (p, [p] if p else [])
    if name in ("write_file", "edit_file", "read_image_text", "git_diff"):
        p = str(d.get("path") or "").strip()
        return (p, [p] if p else [])
    if name == "multi_edit":
        paths = multi_edit_paths(raw_input)
        if len(paths) == 1:
            return (paths[0], paths)
        if paths:
            return (f"{len(paths)} files", paths)
        return ("multi_edit", [])
    if name == "read_document":
        raw_paths = d.get("paths")
        if isinstance(raw_paths, list) and raw_paths:
            paths = [str(x) for x in raw_paths if x]
            if paths:
                return (
                    paths[0] if len(paths) == 1 else f"{len(paths)} documents",
                    paths,
                )
        p = str(d.get("path") or "").strip()
        return (p, [p] if p else [])
    if name in ("read_bundle", "resolve_context"):
        raw_paths = d.get("paths")
        if isinstance(raw_paths, list) and raw_paths:
            paths = [str(x) for x in raw_paths if x]
            if len(paths) == 1:
                return paths[0], paths
            return f"{len(paths)} paths", paths
        root = str(d.get("path") or d.get("root") or "").strip()
        if root:
            return root, [root]
        return (name.replace("_", " "), [])
    if name == "read_images_text":
        raw_paths = d.get("paths")
        if isinstance(raw_paths, list) and raw_paths:
            paths = [str(x) for x in raw_paths if x]
            return (f"{len(paths)} images", paths)
        return (str(d.get("directory") or "."), [])
    if name == "list_dir":
        p = str(d.get("path") or ".").strip()
        return (p, [p])
    if name == "glob_files":
        pat = str(d.get("pattern") or "").strip()
        base = str(d.get("path") or ".").strip()
        return (f"{pat} @ {base}", [base] if base else [])
    if name == "search_code":
        pat = str(d.get("pattern") or "").strip()
        base = str(d.get("path") or ".").strip()
        return (f"{pat} in {base}", [base])
    if name == "rank_files":
        q = str(d.get("query") or "").strip()
        base = str(d.get("path") or ".").strip()
        return (f"{q} @ {base}", [base])
    if name == "fast_find":
        q = str(d.get("query") or "").strip()
        return (q or "find", [])

    p = str(d.get("path") or "").strip()
    if p:
        return (p, [p])
    return (name, [])


def is_dock_tool(name: str) -> bool:
    return name in FILE_DOCK_TOOLS


def begin_wave() -> None:
    """Clear runs at the start of a new tool-execution wave."""
    with _lock:
        _runs.clear()
        _order.clear()
    _notify_wave_begin()


def parallel_file_panel_count() -> int:
    return len(list_runs())


def show_parallel_file_panel() -> bool:
    """Parallel summary UI only when 2+ file-path tools run together."""
    return parallel_file_panel_count() >= 2


def compact_file_tool_ui() -> bool:
    """When True, hide per-tool verbose panels; use the summary + ^P peek."""
    return show_parallel_file_panel()


def flush_tool_ui() -> None:
    """Push one transcript update after batching register_queued calls."""
    _notify_ui()


def register_queued(tool_id: str, name: str, raw_input: Any, *, notify: bool = True) -> None:
    if not is_dock_tool(name):
        return
    if name == "multi_edit":
        paths = multi_edit_paths(raw_input)
        if len(paths) >= 2:
            for i, path in enumerate(paths):
                _register_run_entry(
                    f"{tool_id}#{i}",
                    name,
                    path,
                    [path],
                    parent_id=tool_id,
                )
            if notify:
                _notify_ui()
            return
    label, paths = _display_for_tool(name, raw_input)
    _register_run_entry(tool_id, name, label, paths, notify=notify)


def set_running(tool_id: str) -> None:
    ids = _target_run_ids(tool_id)
    with _lock:
        now = time.time()
        for tid in ids:
            run = _runs.get(tid)
            if run:
                run["status"] = "running"
                run["started"] = now
    _notify_ui()


def set_done(tool_id: str, content: str) -> None:
    raw = content or ""
    children = _children_of(tool_id)
    if children:
        paths = [_runs[tid]["label"] for tid in children if tid in _runs]
        per_path = _split_multi_edit_output(raw, paths)
        now = time.time()
        with _lock:
            for tid in children:
                run = _runs.get(tid)
                if not run:
                    continue
                path = run.get("label") or ""
                snippet, err = per_path.get(path, (raw, raw.lstrip().upper().startswith("ERROR")))
                run["status"] = "error" if err else "done"
                run["ended"] = now
                run["chars"] = len(snippet)
                run["content"] = snippet
                run["error"] = err
        _notify_ui()
        return

    err = raw.lstrip().upper().startswith("ERROR")
    with _lock:
        run = _runs.get(tool_id)
        if run:
            run["status"] = "error" if err else "done"
            run["ended"] = time.time()
            run["chars"] = len(raw)
            run["content"] = raw
            run["error"] = err
    _notify_ui()


def set_cancelled(tool_id: str) -> None:
    ids = _target_run_ids(tool_id)
    with _lock:
        now = time.time()
        for tid in ids:
            run = _runs.get(tid)
            if run:
                run["status"] = "cancelled"
                run["ended"] = now
                run["error"] = True
    _notify_ui()


def cancel_pending(tool_ids: list[str]) -> None:
    pending: set[str] = set()
    for tid in tool_ids:
        pending.update(_target_run_ids(tid))
    with _lock:
        now = time.time()
        for tid in pending:
            run = _runs.get(tid)
            if run and run["status"] in ("queued", "running"):
                run["status"] = "cancelled"
                run["ended"] = now
                run["error"] = True
    _notify_ui()


def list_runs() -> list[dict[str, Any]]:
    """All runs in invocation order (every parallel file row)."""
    with _lock:
        return [_runs[tid] for tid in _order if tid in _runs]


def run_by_id(tool_id: str) -> dict[str, Any] | None:
    with _lock:
        r = _runs.get(tool_id)
        return dict(r) if r else None
