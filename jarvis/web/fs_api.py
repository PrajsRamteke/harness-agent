"""Folders for the web remote's folder picker — names only, never file contents.

``list_dirs`` answers "what's inside this folder" with the sub-folders, which
of them look like projects (git, package.json, …) and which already have a
Jarvis running. ``recent_dirs`` remembers where Jarvis was opened so a phone
needs one tap to get back there. ``change_cwd`` moves the running Jarvis to
another folder (the web "Move this chat here").

Anyone holding the remote's token can already run commands through Jarvis, so
listing folder names adds nothing they couldn't get; contents are never read.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ..constants import CONFIG_DIR

RECENT_FILE = CONFIG_DIR / "recent_dirs.json"
MAX_ENTRIES = 400       # a folder with more sub-folders is cut (and says so)
MAX_RECENT = 12

# Files that make a folder look like a project, with the label the page shows.
_MARKERS = (
    (".git", "git"),
    ("package.json", "Node"),
    ("pyproject.toml", "Python"),
    ("requirements.txt", "Python"),
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("pom.xml", "Java"),
    ("build.gradle", "Gradle"),
    ("Gemfile", "Ruby"),
    ("composer.json", "PHP"),
    ("pubspec.yaml", "Flutter"),
    ("Package.swift", "Swift"),
    ("CLAUDE.md", "CLAUDE.md"),
    ("AGENTS.md", "AGENTS.md"),
    (".harness", "Jarvis"),
)

# Folders worth a shortcut when they exist (relative to home).
_SHORTCUTS = ("Desktop", "Documents", "Downloads", "Developer", "Projects", "projects",
              "code", "Code", "dev", "src", "work", "repos", "GitHub", "git")


class FsError(Exception):
    """A folder the picker can't show: ``code`` for the page, a message for people."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def home() -> Path:
    return Path.home()


def display(path: str | Path) -> str:
    """``/Users/me/x`` → ``~/x`` (what people recognise)."""
    p = str(path)
    h = str(home())
    if p == h:
        return "~"
    if p.startswith(h + os.sep):
        return "~" + p[len(h):]
    return p


def resolve_dir(raw: str | None, *, default: str | Path | None = None) -> Path:
    """A folder path from the page → an absolute, existing, readable folder.

    Accepts ``~`` and ``~/x``; relative paths are taken from ``default`` (the
    shown project's folder). Raises ``FsError`` with a message that says what
    to do.
    """
    text = (raw or "").strip()
    if not text:
        base = Path(default) if default else home()
        return resolve_dir(str(base))
    if "\x00" in text:
        raise FsError("bad_path", "That isn't a valid folder path.")
    if text == "~" or text.startswith("~/"):
        path = home() / text[2:]          # the same home `display` abbreviates
    else:
        path = Path(text).expanduser()    # ~user, absolute or relative
    if not path.is_absolute():
        path = (Path(default) if default else home()) / path
    try:
        path = path.resolve(strict=False)
    except (OSError, RuntimeError):
        raise FsError("bad_path", "That isn't a valid folder path.")
    if not path.exists():
        raise FsError("not_found", f"{display(path)} doesn't exist.")
    if not path.is_dir():
        raise FsError("not_a_folder", f"{display(path)} is a file, not a folder.")
    if not os.access(path, os.R_OK | os.X_OK):
        raise FsError("no_access", f"Jarvis isn't allowed to open {display(path)}.")
    return path


def _markers(path: Path) -> list[str]:
    out: list[str] = []
    for name, label in _MARKERS:
        try:
            if (path / name).exists() and label not in out:
                out.append(label)
        except OSError:
            continue
    return out


def _running_by_dir() -> dict[str, list[dict[str, Any]]]:
    from . import registry

    by_dir: dict[str, list[dict[str, Any]]] = {}
    for rec in registry.list_instances(prune=False):
        cwd = os.path.realpath(str(rec["cwd"])) if rec.get("cwd") else ""
        if cwd:
            by_dir.setdefault(cwd, []).append({"id": rec.get("id"), "project": rec.get("project") or ""})
    return by_dir


def _segments(path: Path) -> list[dict[str, str]]:
    """Breadcrumb: ``~ › Desktop › app`` inside home, ``/ › usr › local`` outside."""
    h = home()
    parts: list[dict[str, str]] = []
    try:
        rel = path.relative_to(h)
        parts.append({"name": "~", "path": str(h)})
        cur = h
        for piece in rel.parts:
            cur = cur / piece
            parts.append({"name": piece, "path": str(cur)})
        return parts
    except ValueError:
        pass
    cur = Path(path.anchor or "/")
    parts.append({"name": str(cur), "path": str(cur)})
    for piece in path.parts[1:]:
        cur = cur / piece
        parts.append({"name": piece, "path": str(cur)})
    return parts


def list_dirs(raw: str | None = None, *, show_hidden: bool = False, default: str | Path | None = None) -> dict[str, Any]:
    """The picker's view of one folder: breadcrumb, its sub-folders, what's running where."""
    path = resolve_dir(raw, default=default)
    running = _running_by_dir()
    dirs: list[dict[str, Any]] = []
    hidden = 0
    try:
        entries = list(os.scandir(path))
    except PermissionError:
        raise FsError("no_access", f"Jarvis isn't allowed to look inside {display(path)}.")
    except OSError as exc:
        raise FsError("unreadable", f"Couldn't read {display(path)}: {exc.strerror or exc}.")
    for entry in entries:
        try:
            if not entry.is_dir(follow_symlinks=True):
                continue
        except OSError:
            continue
        if entry.name.startswith("."):
            hidden += 1
            if not show_hidden:
                continue
        dirs.append({"name": entry.name, "entry": entry})
    dirs.sort(key=lambda d: (d["name"].lower(), d["name"]))
    total = len(dirs)
    rows: list[dict[str, Any]] = []
    for d in dirs[:MAX_ENTRIES]:
        entry = d["entry"]
        full = Path(entry.path)
        readable = os.access(full, os.R_OK | os.X_OK)
        rows.append({
            "name": d["name"],
            "path": str(full),
            "hidden": d["name"].startswith("."),
            "link": entry.is_symlink(),
            "readable": readable,
            "markers": _markers(full) if readable else [],
            "running": running.get(os.path.realpath(full), []),
        })
    parent = path.parent if path.parent != path else None
    return {
        "ok": True,
        "path": str(path),
        "display": display(path),
        "name": path.name or str(path),
        "parent": str(parent) if parent else "",
        "home": str(home()),
        "segments": _segments(path),
        "markers": _markers(path),
        "running": running.get(str(path), []),
        "dirs": rows,
        "total": total,
        "truncated": max(0, total - MAX_ENTRIES),
        "hidden": hidden,
        "writable": os.access(path, os.W_OK),
    }


# ─── Recent folders ───────────────────────────────────────────────────────

def _read_recent() -> list[dict[str, Any]]:
    try:
        data = json.loads(RECENT_FILE.read_text())
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("path")] if isinstance(data, list) else []


def remember_dir(path: str | Path) -> None:
    """Jarvis was opened (or moved) here: put it first in the recent list."""
    p = str(path)
    if not p or p == str(home()):
        return
    rows = [r for r in _read_recent() if r.get("path") != p]
    rows.insert(0, {"path": p, "at": time.time()})
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = RECENT_FILE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rows[:MAX_RECENT * 2]))
        os.replace(tmp, RECENT_FILE)
    except OSError:
        pass


def recent_dirs() -> list[dict[str, Any]]:
    """Recent folders that still exist, newest first, with what's running there."""
    running = _running_by_dir()
    out: list[dict[str, Any]] = []
    for rec in _read_recent():
        p = Path(str(rec["path"]))
        if not p.is_dir():
            continue
        out.append({
            "path": str(p), "display": display(p), "name": p.name or str(p),
            "at": float(rec.get("at") or 0), "markers": _markers(p),
            "running": running.get(os.path.realpath(p), []),
        })
        if len(out) >= MAX_RECENT:
            break
    return out


def shortcuts() -> list[dict[str, str]]:
    h = home()
    out = [{"name": "Home", "path": str(h), "display": "~"}]
    for name in _SHORTCUTS:
        p = h / name
        if p.is_dir() and all(o["path"] != str(p) for o in out):
            out.append({"name": name, "path": str(p), "display": display(p)})
    return out


# ─── Move the running Jarvis ──────────────────────────────────────────────

def change_cwd(raw: str) -> dict[str, Any]:
    """``os.chdir`` + everything that reads the project folder (the ``/cd`` command, done fully)."""
    from .. import state
    from ..constants import set_cwd

    try:
        path = resolve_dir(raw)
    except FsError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}
    before = os.getcwd()
    if str(path) == before:
        return {"ok": True, "cwd": before, "display": display(before), "unchanged": True}
    try:
        os.chdir(path)
    except OSError as exc:
        return {"ok": False, "code": "no_access", "error": f"Couldn't open {display(path)}: {exc.strerror or exc}."}
    set_cwd(path)
    # What Jarvis reads from the project folder: its context file, agents,
    # skills and slash commands (all cached per folder).
    state.project_context_file = ""
    state.project_context_path = ""
    state.project_context_content = ""
    try:
        from ..project_context import detect_project_context

        detect_project_context(path)
    except Exception:
        pass
    for mod in ("skills", "commands"):
        try:
            module = __import__(f"jarvis.storage.{mod}", fromlist=["invalidate_cache"])
            module.invalidate_cache()
        except Exception:
            pass
    try:
        from ..storage.agents import discover_agents

        discover_agents(force=True)
    except Exception:
        pass
    remember_dir(path)
    return {"ok": True, "cwd": str(path), "display": display(path), "from": before}
