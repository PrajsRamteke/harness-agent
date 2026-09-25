"""Compact, human-readable tool-call rows for the transcript.

Every tool call renders as one headline plus a short result line::

    ● Read jarvis/repl/stream.py
      ⎿  758 lines

``tool_title`` / ``tool_args`` build the headline from the tool name and its
input; ``tool_summary`` condenses the raw output into a few lines. All
functions are pure (no Textual imports) so they are cheap to unit-test.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from typing import Any

_TITLES = {
    "read_file": "Read",
    "read_document": "Read",
    "read_bundle": "Read",
    "resolve_context": "Context",
    "write_file": "Write",
    "edit_file": "Edit",
    "multi_edit": "Edit",
    "list_dir": "List",
    "glob_files": "Glob",
    "search_code": "Search",
    "rank_files": "Rank",
    "fast_find": "Find",
    "run_bash": "Bash",
    "run_bg": "Background",
    "bg_output": "Job",
    "bg_kill": "Stop job",
    "screenshot": "Screenshot",
    "git_status": "Git",
    "git_diff": "Git",
    "git_log": "Git",
    "web_search": "Web Search",
    "verified_search": "Web Search",
    "fetch_url": "Fetch",
    "memory_save": "Remember",
    "memory_list": "Memory",
    "memory_delete": "Forget",
    "lesson_save": "Lesson",
    "lesson_search": "Lessons",
    "lesson_list": "Lessons",
    "lesson_delete": "Lesson",
    "ask_user_question": "Ask",
    "exit_plan_mode": "Plan",
    "read_image_text": "OCR",
    "read_images_text": "OCR",
    "launch_app": "Launch",
    "focus_app": "Focus",
    "quit_app": "Quit",
    "list_apps": "Apps",
    "frontmost_app": "Apps",
    "applescript": "AppleScript",
    "read_ui": "Read UI",
    "click_element": "Click",
    "click_at": "Click",
    "click_menu": "Menu",
    "type_text": "Type",
    "key_press": "Keys",
    "clipboard_get": "Clipboard",
    "clipboard_set": "Clipboard",
    "open_url": "Open",
    "notify": "Notify",
    "speck": "Speak",
    "shortcut_run": "Shortcut",
    "mac_control": "Mac",
    "wait": "Wait",
    "check_permissions": "Permissions",
    "skill_load": "Skill",
}


# One single-width glyph per tool family (no emoji — they render at
# inconsistent widths across terminals). Colored by status in the row.
_ICONS = {
    "read_file": "→", "read_document": "→", "read_bundle": "→",
    "resolve_context": "✦",
    "write_file": "←", "edit_file": "✎", "multi_edit": "✎",
    "list_dir": "▤", "glob_files": "◉", "fast_find": "◉", "rank_files": "◉",
    "search_code": "✱",
    "run_bash": "$",
    "run_bg": "&", "bg_output": "&", "bg_kill": "&",
    "screenshot": "◩",
    "git_status": "⎇", "git_diff": "⎇", "git_log": "⎇",
    "web_search": "◍", "verified_search": "◍",
    "fetch_url": "%", "open_url": "%",
    "memory_save": "◆", "memory_list": "◆", "memory_delete": "◆",
    "lesson_save": "◇", "lesson_search": "◇", "lesson_list": "◇", "lesson_delete": "◇",
    "ask_user_question": "?",
    "exit_plan_mode": "≡",
    "read_image_text": "◳", "read_images_text": "◳",
    "launch_app": "▢", "focus_app": "▢", "quit_app": "▢", "list_apps": "▢", "frontmost_app": "▢",
    "applescript": "⌘", "read_ui": "▦",
    "click_element": "⊙", "click_at": "⊙", "click_menu": "⊙",
    "type_text": "▭", "key_press": "⌥",
    "clipboard_get": "⎘", "clipboard_set": "⎘",
    "notify": "!", "speck": "♪", "wait": "◷",
    "shortcut_run": "▶", "mac_control": "◐",
    "skill_load": "✧",
}


def tool_icon(name: str) -> str:
    if is_mcp(name or ""):
        return "◈"
    return _ICONS.get(name or "", "•")


def _norm(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        try:
            return raw.model_dump()
        except Exception:
            return {}
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
            return val if isinstance(val, dict) else {}
        except Exception:
            return {}
    return {}


def clip(text: Any, width: int = 96) -> str:
    """Single-line, whitespace-collapsed, ellipsized."""
    one = " ".join(str(text or "").split())
    if len(one) > width:
        return one[: max(1, width - 1)] + "…"
    return one


def short_path(path: Any) -> str:
    """Project-relative when inside cwd, ``~/…`` under home, else as given."""
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        p = pathlib.Path(raw).expanduser()
        if p.is_absolute():
            cwd = pathlib.Path.cwd()
            try:
                return str(p.relative_to(cwd)) or "."
            except ValueError:
                home = pathlib.Path.home()
                try:
                    return "~/" + str(p.relative_to(home))
                except ValueError:
                    return str(p)
    except Exception:
        pass
    return raw


def is_mcp(name: str) -> bool:
    return (name or "").startswith("mcp__")


def _mcp_parts(name: str) -> tuple[str, str]:
    parts = name.split("__", 2)
    if len(parts) == 3:
        return parts[1], parts[2]
    return "mcp", name


def tool_title(name: str) -> str:
    if is_mcp(name):
        _server, tool = _mcp_parts(name)
        return tool.replace("_", " ").replace("-", " ").strip().title() or "MCP"
    if name in _TITLES:
        return _TITLES[name]
    return (name or "tool").replace("_", " ").strip().title()


def _kv(d: dict, limit: int = 3) -> str:
    bits: list[str] = []
    for k, v in d.items():
        if len(bits) >= limit:
            break
        if isinstance(v, (dict, list)):
            if not v:
                continue
            v = f"[{len(v)}]" if isinstance(v, list) else "{…}"
        elif v is None or v == "":
            continue
        bits.append(f"{k}: {clip(v, 40)}")
    return ", ".join(bits)


def tool_args(name: str, raw_input: Any, width: int = 96) -> str:
    """One-line argument summary for the headline."""
    d = _norm(raw_input)
    c = lambda s, w=width: clip(s, w)  # noqa: E731

    if name == "read_file":
        s = short_path(d.get("path"))
        off, lim = d.get("offset") or 0, d.get("limit") or 0
        if off or lim:
            try:
                start = int(off) + 1
                s += f"  L{start}" + (f"–{int(off) + int(lim)}" if lim else "+")
            except (TypeError, ValueError):
                pass
        return c(s)
    if name in ("write_file", "edit_file", "read_image_text"):
        return c(short_path(d.get("path")))
    if name == "multi_edit":
        seen: list[str] = []
        for e in d.get("edits") or []:
            if isinstance(e, dict) and e.get("path"):
                p = short_path(e["path"])
                if p not in seen:
                    seen.append(p)
        if not seen:
            return ""
        head = ", ".join(seen[:3])
        return c(head + (f" +{len(seen) - 3} more" if len(seen) > 3 else ""))
    if name in ("read_document", "read_bundle", "resolve_context", "read_images_text"):
        paths = d.get("paths")
        if isinstance(paths, list) and paths:
            if len(paths) == 1:
                return c(short_path(paths[0]))
            return c(", ".join(short_path(p) for p in paths[:3])
                     + (f" +{len(paths) - 3} more" if len(paths) > 3 else ""))
        return c(short_path(d.get("path") or d.get("root") or d.get("directory") or "."))
    if name == "list_dir":
        return c(short_path(d.get("path") or "."))
    if name == "glob_files":
        base = short_path(d.get("path") or ".")
        pat = d.get("pattern") or ""
        return c(pat if base in ("", ".") else f"{pat}  in {base}")
    if name in ("search_code", "rank_files"):
        key = "pattern" if name == "search_code" else "query"
        base = short_path(d.get("path") or ".")
        q = f'"{d.get(key) or ""}"'
        return c(q if base in ("", ".") else f"{q}  in {base}")
    if name == "fast_find":
        return c(d.get("query") or "")
    if name == "run_bash":
        from ..utils.display_paths import shorten_command

        return c(shorten_command(d.get("cmd") or ""))
    if name == "run_bg":
        from ..utils.display_paths import shorten_command

        return c(shorten_command(d.get("cmd") or ""))
    if name in ("bg_output", "bg_kill"):
        if not d.get("job_id"):
            return "all jobs"
        extra = f"  wait {d['wait']}s" if name == "bg_output" and d.get("wait") else ""
        return f"#{d.get('job_id')}{extra}"
    if name == "screenshot":
        if d.get("path"):
            return c(short_path(d["path"]))
        if d.get("url"):
            return c(str(d["url"]))
        if d.get("app"):
            return c(f"{d['app']} window")
        if isinstance(d.get("region"), dict):
            r = d["region"]
            return f"region {r.get('width', '?')}×{r.get('height', '?')} at {r.get('x', 0)},{r.get('y', 0)}"
        return "screen"
    if name == "git_status":
        return "status"
    if name == "git_diff":
        return c("diff " + short_path(d.get("path") or "")).strip()
    if name == "git_log":
        return f"log -n {d.get('n') or 10}"
    if name in ("web_search", "verified_search", "lesson_search"):
        return c(d.get("query") or "")
    if name in ("fetch_url", "open_url"):
        return c(d.get("url") or "")
    if name == "memory_save":
        return c(d.get("text") or d.get("fact") or "")
    if name in ("memory_delete", "lesson_delete"):
        return f"#{d.get('id', '')}"
    if name == "lesson_save":
        return c(d.get("task") or "")
    if name == "ask_user_question":
        qs = d.get("questions")
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            return c(qs[0].get("prompt") or "")
        return ""
    if name in ("launch_app", "focus_app", "quit_app"):
        return c(d.get("name") or "")
    if name == "applescript":
        return c(d.get("code") or "")
    if name == "type_text":
        return c(d.get("text") or "")
    if name == "key_press":
        return c(d.get("keys") or "")
    if name == "click_element":
        return c(f'"{d.get("query", "")}" in {d.get("app", "")}')
    if name == "wait":
        return f"{d.get('seconds', 0)}s"
    if is_mcp(name):
        server, _tool = _mcp_parts(name)
        kv = _kv(d, 2)
        return c(f"{server}" + (f"  {kv}" if kv else ""))
    return c(_kv(d))


# ── output → summary ─────────────────────────────────────────────────────

_EXIT_RE = re.compile(r"^exit=(-?\d+)\s*$")


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _nonempty_lines(text: str) -> list[str]:
    return [ln for ln in (text or "").splitlines() if ln.strip()]


def _tail(lines: list[str], n: int, width: int) -> list[str]:
    out = [clip(ln, width) for ln in lines[-n:]]
    hidden = len(lines) - len(out)
    if hidden > 0:
        out.insert(0, f"… +{hidden} lines")
    return out


def _head(lines: list[str], n: int, width: int) -> list[str]:
    out = [clip(ln, width) for ln in lines[:n]]
    hidden = len(lines) - len(out)
    if hidden > 0:
        out.append(f"… +{hidden} lines")
    return out


def is_error_output(out: str) -> bool:
    s = (out or "").lstrip()
    return s.startswith(("ERROR", "BLOCKED", "TIMEOUT", "USER DENIED"))


def tool_summary(name: str, raw_input: Any, output: str, width: int = 100) -> tuple[list[str], bool]:
    """Return ``(lines, is_error)`` — what goes after ``⎿`` in the row."""
    out = str(output or "")
    stripped = out.strip()
    from ..utils.tool_repair import REPAIR_NOTE_MARKER

    if REPAIR_NOTE_MARKER in stripped:
        stripped = stripped.split(REPAIR_NOTE_MARKER, 1)[0].rstrip()

    if not stripped:
        return (["(no output)"], False)

    if stripped.startswith("USER DENIED"):
        return (["denied by user"], True)
    if is_error_output(stripped):
        first = stripped.splitlines()[0]
        first = re.sub(r"^ERROR:\s*", "", first)
        return ([clip(first, width)], True)

    from ..utils.display_paths import shorten_paths

    stripped = shorten_paths(stripped)
    lines = stripped.splitlines()
    d = _norm(raw_input)

    if name == "run_bash":
        body = lines
        code = 0
        if body and body[0].startswith("$ "):
            body = body[1:]
        if body:
            m = _EXIT_RE.match(body[0].strip())
            if m:
                code = int(m.group(1))
                body = body[1:]
        body = [ln for ln in body if ln.strip()]
        if not body:
            return ([f"exit {code}" if code else "done"], code != 0)
        tail = _tail(body, 4, width)
        if code:
            tail.append(f"exit {code}")
        return (tail, code != 0)
    if name == "screenshot":
        m = re.search(r"— (\d+×\d+) px", lines[0]) if lines else None
        dims = m.group(1) if m else ""
        if "can't view images" in stripped:
            return ([f"Captured {dims} · model has no vision — sent OCR text".replace("  ", " ")], False)
        return ([f"Captured {dims} · attached for the model" if dims else "Captured"], False)
    if name == "run_bg":
        m = re.match(r"started background job #(\d+) \(pid (\d+)\)", stripped)
        if m:
            return ([f"started job #{m.group(1)} · pid {m.group(2)}"], False)
        m = re.search(r"^exit=(-?\d+)", stripped, flags=re.M)
        code = int(m.group(1)) if m else 0
        return ([f"finished at once · exit {code}"], code != 0)
    if name == "bg_output":
        head = lines[0] if lines else ""
        m = re.search(r"#(\d+) (running for [^:]+|finished · exit (-?\d+) after [^:]+|killed after [^:]+)", head)
        if m:
            code = m.group(3)
            body = [ln for ln in lines[3:] if ln.strip() and not ln.startswith("---")]
            tail = _tail(body, 2, width) if code not in (None, "0") else []
            return ([f"#{m.group(1)} {m.group(2)}"] + tail, code not in (None, "0"))
        return (_head(lines, 3, width), False)
    if name == "bg_kill":
        return ([clip(lines[0], width)] if lines else ["stopped"], False)
    if name == "read_file":
        return ([f"Read {_plural(len(lines), 'line')}"], False)
    if name == "write_file":
        n = len(str(d.get("content") or "").splitlines())
        return ([f"Wrote {_plural(n, 'line')}" if n else "Written"], False)
    if name == "edit_file":
        m = re.search(r"\((\d+) replacements?\)", stripped)
        n = int(m.group(1)) if m else 1
        return ([f"Applied {_plural(n, 'edit')}"], False)
    if name == "multi_edit":
        m = re.search(r"(\d+) succeeded,\s*(\d+) failed", stripped)
        if m:
            ok, bad = int(m.group(1)), int(m.group(2))
            msg = f"Applied {_plural(ok, 'edit')}" + (f", {bad} failed" if bad else "")
            return ([msg], bad > 0 and ok == 0)
        return (["Applied edits"], False)
    if name in ("glob_files", "list_dir", "fast_find", "rank_files"):
        if stripped.startswith("no matches"):
            return (["No matches"], False)
        noun = "entry" if name == "list_dir" else "file"
        n = len(_nonempty_lines(stripped))
        return ([_plural(n, noun).replace("entrys", "entries")], False)
    if name == "search_code":
        if stripped.lower().startswith("no matches") or stripped.startswith("(no"):
            return (["No matches"], False)
        n = len(_nonempty_lines(stripped))
        return ([_plural(n, "match").replace("matchs", "matches")], False)
    if name in ("read_bundle", "resolve_context", "read_document"):
        files = len(re.findall(r"^(?:=+|#+|---)\s*\S", stripped, flags=re.M))
        chars = len(stripped)
        size = f"{chars / 1000:.1f}k chars" if chars >= 1000 else f"{chars} chars"
        return ([f"{_plural(files, 'file')} · {size}" if files > 1 else f"Loaded {size}"], False)
    if name in ("web_search", "verified_search"):
        n = len(re.findall(r"^\s*(?:\d+[.)]|[-*•])\s", stripped, flags=re.M))
        return ([_plural(n, "result") if n else clip(lines[0], width)], False)
    if name == "fetch_url":
        chars = len(stripped)
        return ([f"Fetched {chars / 1000:.1f}k chars" if chars >= 1000 else f"Fetched {chars} chars"], False)
    if name in ("git_status", "git_diff", "git_log"):
        return (_head(lines, 3, width), False)
    if name == "ask_user_question":
        try:
            payload = json.loads(stripped)
            if payload.get("cancelled"):
                return (["cancelled"], False)
            labels = [", ".join(a.get("labels") or []) for a in payload.get("answers") or []]
            return ([clip(" · ".join(x for x in labels if x) or "answered", width)], False)
        except Exception:
            pass
    return (_head(lines, 2, width), False)


def preview_lines(output: str, max_lines: int = 12, width: int = 160,
                  more_hint: str = "^F full output") -> list[str]:
    """Raw output preview — first lines, clipped, cwd-relative paths."""
    from ..utils.display_paths import shorten_paths

    text = shorten_paths((output or "").rstrip())
    text = re.sub(r"^\[\[jarvis:image .+?\]\]$", "◩ image attached for the model", text, flags=re.M)
    lines = text.splitlines()
    out = [ln[:width] + ("…" if len(ln) > width else "") for ln in lines[:max_lines]]
    if len(lines) > max_lines:
        out.append(f"… {len(lines) - max_lines} more lines · {more_hint}")
    return out


def env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")
