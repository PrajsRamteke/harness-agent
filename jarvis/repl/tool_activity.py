"""Human-readable activity lines for the TUI from tool name + arguments."""

from __future__ import annotations


def _clip(s: str, max_len: int = 72) -> str:
    if not s:
        return ""
    one_line = " ".join(str(s).split())
    if len(one_line) > max_len:
        return one_line[: max_len - 1] + "…"
    return one_line


def _norm_input(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        return raw.model_dump()
    try:
        return dict(raw)
    except Exception:
        return {}


def describe_tool_activity(name: str, raw_input) -> str:
    """Short label for the activity bar: what this tool is doing, from its real inputs."""
    d = _norm_input(raw_input)
    c = _clip

    if name == "run_bash":
        return f"Shell: {c(d.get('cmd', ''))}"
    if name == "read_file":
        return f"Reading file: {c(d.get('path', ''))}"
    if name == "write_file":
        return f"Writing file: {c(d.get('path', ''))}"
    if name == "edit_file":
        return f"Editing file: {c(d.get('path', ''))}"
    if name == "list_dir":
        return f"Listing directory: {c(d.get('path', '') or '.')}"
    if name == "glob_files":
        return f"Glob files: {c(d.get('pattern', ''))}"
    if name == "search_code":
        path = d.get("path") or "."
        return f"Searching code: {c(d.get('pattern', ''))} in {c(str(path), 36)}"
    if name == "rank_files":
        q = d.get("query", "")
        loc = d.get("path") or "."
        return f"Ranking files: {c(q)} in {c(str(loc), 36)}"
    if name == "fast_find":
        bits = [c(d.get("query", ""))]
        if d.get("ext"):
            bits.append(f"ext={d.get('ext')}")
        if d.get("path"):
            bits.append(f"in {c(str(d.get('path')), 28)}")
        return "Spotlight find: " + " ".join(x for x in bits if x)
    if name == "git_status":
        return "git status"
    if name == "git_diff":
        return f"git diff {c(d.get('path', '') or '.')}"
    if name == "git_log":
        return f"git log (n={d.get('n', 10)})"
    if name == "web_search":
        return f"Web search: {c(d.get('query', ''))}"
    if name == "fetch_url":
        return f"Fetch URL: {c(d.get('url', ''))}"
    if name == "verified_search":
        return f"Verified web search: {c(d.get('query', ''))}"
    if name == "read_image_text":
        return f"OCR (single image): {c(d.get('path', ''))}"
    if name == "read_images_text":
        paths = d.get("paths")
        if paths and isinstance(paths, list):
            return f"OCR batch: {len(paths)} file(s)"
        return (
            f"OCR batch: {c(d.get('directory', '.'), 24)} "
            f"pattern {c(d.get('pattern', '**/*'), 24)}"
        ).strip()
    if name == "memory_save":
        return f"Saving memory: {c(d.get('text', ''))}"
    if name == "memory_list":
        return "Listing saved memory"
    if name == "memory_delete":
        return f"Deleting memory #{d.get('id', '')}"
    if name == "skill_save":
        return f"Saving skill: {c(d.get('task', ''))}"
    if name == "skill_search":
        return f"Searching skills: {c(d.get('query', ''))}"
    if name == "skill_list":
        return "Listing skills"
    if name == "skill_delete":
        return f"Deleting skill #{d.get('id', '')}"
    if name == "launch_app":
        return f"Launch app: {c(d.get('name', ''))}"
    if name == "focus_app":
        return f"Focus app: {c(d.get('name', ''))}"
    if name == "quit_app":
        return f"Quit app: {c(d.get('name', ''))}"
    if name == "list_apps":
        return "Listing running apps"
    if name == "frontmost_app":
        return "Reading frontmost app"
    if name == "applescript":
        return f"AppleScript: {c(d.get('code', ''))}"
    if name == "read_ui":
        app = d.get("app") or "frontmost"
        return f"Reading UI tree: {c(str(app))}"
    if name == "click_element":
        return f'Click UI element "{c(d.get("query", ""))}" in {c(d.get("app", ""))}'
    if name == "wait":
        return f"Waiting {d.get('seconds', 0)}s"
    if name == "check_permissions":
        return "Checking Accessibility permission"
    if name == "type_text":
        return f"Typing: {c(d.get('text', ''))}"
    if name == "key_press":
        return f"Key: {c(d.get('keys', ''))}"
    if name == "click_menu":
        path = d.get("path", [])
        if isinstance(path, list):
            p = " → ".join(str(x) for x in path)
        else:
            p = str(path)
        return f"Menu: {c(d.get('app', ''))} → {c(p)}"
    if name == "click_at":
        return f"Click at screen ({d.get('x')}, {d.get('y')})"
    if name == "clipboard_get":
        return "Reading clipboard"
    if name == "clipboard_set":
        return f"Writing clipboard: {c(d.get('text', ''))}"
    if name == "open_url":
        return f"Open URL: {c(d.get('url', ''))}"
    if name == "notify":
        msg = d.get("message") or ""
        return f"Notify: {c(d.get('title', ''))}" + (f" — {c(msg, 40)}" if msg else "")
    if name == "shortcut_run":
        line = f"Shortcut: {c(d.get('name', ''))}"
        if d.get("input_text"):
            line += f" ({c(str(d.get('input_text')), 40)})"
        return line
    if name == "mac_control":
        act = d.get("action", "")
        if d.get("value"):
            return f"Mac control: {c(str(act))} → {c(str(d.get('value')), 24)}"
        return f"Mac control: {c(str(act))}"

    return f"Tool {name}"
