"""Session library: what the session lists (web sidebar, Sessions dialog, TUI
``/session``) show, on top of ``storage/sessions.py``. UI-free.

* **Project folders.** Every session records the folder it was started in
  (``sessions.cwd``). Chats from before that column existed get a best guess
  once per database (``backfill_folders``): the git root most of the chat's
  file paths live under (files it changed count most). A chat with no
  absolute paths stays "Folder not recorded".
* **Search** matches titles, models, ``#id`` and the text of every message;
  ``snippet`` cuts the first hit out of a prompt / reply / tool call so a list
  can show *why* a chat matched.
* **Markdown export** of a stored session (``markdown``).
* ``public_row`` / ``folder_info`` are the JSON shapes the UIs read.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import sessions as db

BACKFILL_KEY = "cwd_backfill_v1"
SNIPPET_CHARS = 140
_SCAN_MESSAGES = 40          # first N messages of a chat are enough to place it
_SCAN_CHARS = 20_000         # … and the start of each
_ABS_PATH = re.compile(r"""(/(?:Users|home)/[^"\s\\'`<>|:;,()\[\]{}]+)""")
_SKIP_PARTS = ("/.config/", "/Library/", "/.claude/", "/.cache/", "/node_modules/", "/.Trash/")
_MARKERS = (".git", ".harness", "CLAUDE.md", "AGENTS.md")

_backfill_lock = threading.Lock()
_backfilled: set = set()


# ─── Folder names ────────────────────────────────────────────────────────

def home() -> str:
    return os.path.expanduser("~")


def display(cwd: str) -> str:
    """``/Users/me/code/x`` → ``~/code/x``."""
    if not cwd:
        return ""
    h = home()
    if cwd == h:
        return "~"
    if cwd.startswith(h + os.sep):
        return "~" + cwd[len(h):]
    return cwd


def name_of(cwd: str) -> str:
    if not cwd:
        return "Folder not recorded"
    return os.path.basename(cwd.rstrip(os.sep)) or cwd


def here() -> str:
    try:
        return os.getcwd()
    except OSError:
        return ""


def folder_info(row: Dict, *, current: str = "") -> Dict[str, Any]:
    cwd = str(row.get("cwd") or "")
    return {
        "cwd": cwd,
        "name": name_of(cwd),
        "display": display(cwd) or "Chats from before Jarvis kept track of folders",
        "count": int(row.get("count") or 0),
        "archived": int(row.get("archived") or 0),
        "pinned": int(row.get("pinned") or 0),
        "last": float(row.get("last") or 0),
        "current": bool(cwd) and cwd == current,
        "known": bool(cwd),
        "exists": bool(cwd) and os.path.isdir(cwd),
    }


# ─── Guessing the folder of old chats ────────────────────────────────────

def _meta(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS session_meta (key TEXT PRIMARY KEY, value TEXT)")


def _root_finder():
    cache: Dict[str, Optional[str]] = {}
    h = home()
    stop = {h, os.path.dirname(h), os.sep}

    def root_of(path: str) -> Optional[str]:
        if any(part in path for part in _SKIP_PARTS):
            return None
        d = path.rstrip(".")
        seen: List[str] = []
        found: Optional[str] = None
        while d and d not in stop and d.startswith(h + os.sep):
            if d in cache:
                found = cache[d]
                break
            seen.append(d)
            if os.path.basename(d).startswith("."):
                d = os.path.dirname(d)  # inside a dot folder: never a project of its own
                continue
            if any(os.path.exists(os.path.join(d, m)) for m in _MARKERS):
                found = d
                break
            d = os.path.dirname(d)
        for s in seen:
            cache[s] = found
        return found

    return root_of


def guess_folder(c: sqlite3.Connection, session_id: int, root_of=None) -> str:
    """The project folder a chat most likely belongs to ("" when unclear)."""
    root_of = root_of or _root_finder()
    votes: Counter = Counter()
    try:
        for (path,) in c.execute("SELECT path FROM session_file_changes WHERE session_id=?", (session_id,)):
            r = root_of(str(path))
            if r:
                votes[r] += 3  # a file it changed is the strongest hint
    except sqlite3.OperationalError:
        pass  # no file-change ledger yet
    rows = c.execute(
        "SELECT substr(content_json, 1, ?) FROM messages WHERE session_id=? ORDER BY idx LIMIT ?",
        (_SCAN_CHARS, session_id, _SCAN_MESSAGES))
    for (text,) in rows:
        for m in _ABS_PATH.findall(text or ""):
            r = root_of(m)
            if r:
                votes[r] += 1
    return votes.most_common(1)[0][0] if votes else ""


def backfill_folders(*, force: bool = False) -> int:
    """Once per database: guess the folder of chats saved before ``cwd`` existed.
    Returns how many were placed. Fast (~0.3 s for hundreds of chats)."""
    key = str(db.SESSIONS_DB)
    if key in _backfilled and not force:
        return 0
    with _backfill_lock:
        if key in _backfilled and not force:
            return 0
        placed = 0
        c = db.db_conn()
        try:
            _meta(c)
            done = c.execute("SELECT value FROM session_meta WHERE key=?", (BACKFILL_KEY,)).fetchone()
            if done and not force:
                _backfilled.add(key)
                return 0
            ids = [r[0] for r in c.execute(
                "SELECT id FROM sessions WHERE cwd='' AND cwd_guessed=0 "
                "AND EXISTS (SELECT 1 FROM messages m WHERE m.session_id=sessions.id)")]
            root_of = _root_finder()
            found = []
            for sid in ids:
                folder = guess_folder(c, sid, root_of)
                if folder:
                    found.append((folder, sid))
            with c:
                c.executemany("UPDATE sessions SET cwd=?, cwd_guessed=1 WHERE id=? AND cwd=''", found)
                c.execute("INSERT OR REPLACE INTO session_meta (key, value) VALUES (?, '1')", (BACKFILL_KEY,))
            placed = len(found)
        except sqlite3.Error:
            return 0
        finally:
            c.close()
        _backfilled.add(key)
        return placed


# ─── Message text (snippets, export) ─────────────────────────────────────

def _blocks(content) -> List[Dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in (content or []) if isinstance(b, dict)]


def _strip_notes(text: str) -> str:
    """Drop what Jarvis adds to a prompt (attachment notes, the "send now" head)."""
    try:
        from ..media import NOTE_HEAD

        if NOTE_HEAD in text:
            text = text.split(NOTE_HEAD, 1)[0]
    except Exception:
        pass
    try:
        from ..prompt_queue import strip_steer

        text = strip_steer(text)[0]
    except Exception:
        pass
    return text


def message_texts(role: str, content) -> List[tuple]:
    """``[(kind, text)]`` for one stored message: kind is you / jarvis / tool."""
    out: List[tuple] = []
    for b in _blocks(content):
        t = b.get("type")
        if t == "text":
            text = str(b.get("text") or "")
            if role == "user":
                text = _strip_notes(text)
            if text.strip():
                out.append(("you" if role == "user" else "jarvis", text))
        elif t == "tool_use":
            args = b.get("input")
            out.append(("tool", f"{b.get('name') or 'tool'} {json.dumps(args, ensure_ascii=False) if args else ''}".strip()))
        elif t == "tool_result":
            body = b.get("content")
            if isinstance(body, list):
                body = "".join(str(x.get("text") or "") for x in body if isinstance(x, dict))
            if body:
                out.append(("tool", str(body)))
    return out


def _cut(text: str, start: int, length: int) -> Dict[str, Any]:
    flat = " ".join(text.split())
    # Find the match again in the collapsed text (whitespace moved it).
    needle = " ".join(text[start:start + length].split())
    pos = flat.lower().find(needle.lower()) if needle else -1
    if pos < 0:
        pos, needle = 0, ""
    lead = max(0, pos - SNIPPET_CHARS // 3)
    if lead:
        sp = flat.find(" ", lead)
        lead = sp + 1 if 0 <= sp < pos else lead
    tail = min(len(flat), lead + SNIPPET_CHARS)
    piece = flat[lead:tail]
    return {
        "text": ("…" if lead else "") + piece + ("…" if tail < len(flat) else ""),
        "at": pos - lead + (1 if lead else 0),
        "len": len(needle),
    }


def snippet(session_id: int, query: str) -> Optional[Dict[str, Any]]:
    """Where ``query`` first shows up in a chat: ``{kind, text, at, len}``.
    Prompts and replies win over tool output."""
    q = (query or "").strip()
    if not q:
        return None
    ql = q.lower()
    tool_hit = None
    for row in db.db_first_matches(session_id, q):
        try:
            content = json.loads(row["content_json"])
        except (TypeError, ValueError):
            continue
        for kind, text in message_texts(row["role"], content):
            i = text.lower().find(ql)
            if i < 0:
                continue
            hit = {"kind": kind, **_cut(text, i, len(q))}
            if kind != "tool":
                return hit
            tool_hit = tool_hit or hit
    return tool_hit


def markdown(session_id: int) -> Optional[str]:
    """A stored chat as Markdown (prompts, replies, tool calls folded)."""
    info = db.db_session_info(session_id)
    msgs = db.db_load_session(session_id)
    if info is None or msgs is None:
        return None
    import time as _t

    title = (info.get("title") or "").strip() or f"Session #{session_id}"
    when = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(float(info.get("created_at") or 0)))
    lines = [f"# {title}", ""]
    meta = [f"Session #{session_id}", when]
    if info.get("model"):
        meta.append(f"model `{info['model']}`")
    if info.get("cwd"):
        meta.append(f"folder `{display(info['cwd'])}`")
    lines += ["_" + " · ".join(meta) + "_", ""]
    for m in msgs:
        for kind, text in message_texts(m.get("role", ""), m.get("content")):
            if kind == "you":
                lines += ["## You", "", text.strip(), ""]
            elif kind == "jarvis":
                lines += ["## Jarvis", "", text.strip(), ""]
            else:
                body = text if len(text) <= 1500 else text[:1500] + "\n… (cut)"
                lines += ["<details><summary>tool</summary>", "", "```", body.replace("```", "ʼʼʼ"), "```", "", "</details>", ""]
    return "\n".join(lines).rstrip() + "\n"


def export_name(session_id: int) -> str:
    title = db.db_get_title(session_id) or f"session-{session_id}"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:60] or f"session-{session_id}"
    return f"{slug}.md"


# ─── Rows ─────────────────────────────────────────────────────────────────

def public_row(row, *, current_sid=None, current_cwd: str = "") -> Dict[str, Any]:
    from ..utils.time_fmt import _fmt_ts

    r = dict(row)
    sid = int(r["id"])
    title = (r.get("title") or "").strip()
    cwd = str(r.get("cwd") or "")
    return {
        "id": sid,
        "title": title or f"Session #{sid}",
        "auto_title": not title,  # "Session #N" stands in for none
        "model": r.get("model") or "",
        "msg_count": int(r.get("msg_count") or 0),
        "created_at": float(r.get("created_at") or 0),
        "updated_at": float(r.get("updated_at") or 0),
        "updated_label": _fmt_ts(float(r.get("updated_at") or 0)),
        "active": current_sid is not None and sid == current_sid,
        "cwd": cwd,
        "project": name_of(cwd) if cwd else "",
        "cwd_display": display(cwd),
        "cwd_guessed": bool(r.get("cwd_guessed")),
        "here": bool(cwd) and cwd == current_cwd,
        "pinned": bool(r.get("pinned")),
        "archived": bool(r.get("archived")),
    }
