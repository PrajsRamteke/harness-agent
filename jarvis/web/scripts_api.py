"""Package scripts for the web terminal — every ``package.json`` in the project
and the scripts it declares, so the panel can offer them in a dropdown.

UI-free: ``list_scripts(root)`` finds the files (``git ls-files`` in a repo —
fast and it honours ``.gitignore`` —, else a bounded walk that skips
``node_modules`` and build output), reads each one's ``scripts``, works out
which package manager runs them (``packageManager`` field, then the nearest
lockfile, up from the package) and hands back the exact command per script
(``pnpm run dev:staging``). Nothing here runs anything: the page types the
command into a shell the user already has, like typing it by hand.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
from typing import Any

MAX_PACKAGES = 300            # package.json files read per scan
MAX_SCRIPTS = 200             # scripts kept per package
MAX_JSON_BYTES = 1_000_000    # a bigger package.json is skipped
MAX_BODY_CHARS = 400          # a script's command as shown in the list
MAX_WALK_DIRS = 30_000        # folders a non-git walk looks at
MAX_DEPTH = 8                 # … and how deep it goes
GIT_TIMEOUT = 6.0
WALK_SECONDS = 4.0
CACHE_SECONDS = 5.0
UP_LEVELS = 12                # how far above a package a lockfile is looked for

SKIP_DIRS = {
    "node_modules", ".git", ".hg", ".svn", "dist", "build", "out", ".next", ".nuxt",
    ".output", ".svelte-kit", ".turbo", ".cache", ".parcel-cache", ".vercel", ".netlify",
    "coverage", "target", "vendor", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".gradle", ".idea", "Pods", "bower_components", ".yarn", ".pnpm-store",
    "tmp", ".expo", ".docusaurus", "storybook-static",
}

# Lockfile → the package manager that wrote it (first match wins per folder).
LOCKFILES = (
    ("bun.lockb", "bun"), ("bun.lock", "bun"),
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"), ("npm-shrinkwrap.json", "npm"),
)
MANAGERS = ("npm", "pnpm", "yarn", "bun")

# Scripts npm runs by itself around install / publish: listed, but last and dimmed.
LIFECYCLE = {
    "preinstall", "install", "postinstall", "prepare", "prepublish", "prepublishOnly",
    "prepack", "postpack", "publish", "postpublish", "preversion", "version",
    "postversion", "dependencies", "preuninstall", "uninstall", "postuninstall",
}

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _real(path: str) -> str:
    try:
        return os.path.realpath(os.path.expanduser(path))
    except OSError:
        return path


# ─── Finding package.json files ─────────────────────────────────────────

def _skip(rel: str) -> bool:
    """A path inside a folder no one runs scripts from (node_modules, build output …)."""
    return any(part in SKIP_DIRS for part in rel.split("/")[:-1])


def _git_files(root: str) -> list[str] | None:
    """Every package.json git knows (tracked + untracked, not ignored), relative to
    ``root``. None when ``root`` isn't in a repo or git isn't there."""
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
             "--", "package.json", "*/package.json"],
            capture_output=True, timeout=GIT_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = []
    for raw in proc.stdout.split(b"\0"):
        rel = raw.decode("utf-8", "replace").strip()
        if rel and (rel == "package.json" or rel.endswith("/package.json")) and not _skip(rel):
            out.append(rel)
    return sorted(set(out))


def _walk_files(root: str) -> tuple[list[str], bool]:
    """package.json files under ``root`` without git: (paths, cut short)."""
    out: list[str] = []
    seen = 0
    deadline = time.monotonic() + WALK_SECONDS
    stack: list[tuple[str, int]] = [("", 0)]
    while stack:
        rel_dir, depth = stack.pop()
        seen += 1
        if seen > MAX_WALK_DIRS or time.monotonic() > deadline:
            return sorted(out), True
        try:
            with os.scandir(os.path.join(root, rel_dir) if rel_dir else root) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH and entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                        stack.append((rel, depth + 1))
                elif entry.name == "package.json" and entry.is_file():
                    out.append(rel)
            except OSError:
                continue
    return sorted(out), False


def _read_json(path: str) -> dict[str, Any] | None:
    try:
        if os.path.getsize(path) > MAX_JSON_BYTES:
            return None
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ─── Which package manager runs a package's scripts ─────────────────────

def _declared(data: dict[str, Any] | None) -> str:
    """``"packageManager": "pnpm@9.1.0"`` → ``pnpm``."""
    raw = (data or {}).get("packageManager")
    if isinstance(raw, str):
        name = raw.split("@", 1)[0].strip().lower()
        if name in MANAGERS:
            return name
    return ""


def detect_manager(pkg_dir: str, *, known: dict[str, dict[str, Any] | None] | None = None) -> str:
    """The package manager for the package in ``pkg_dir``: a ``packageManager``
    field or a lockfile in that folder or the nearest folder above it; npm otherwise.
    ``known`` maps folders to their already-parsed package.json (saves re-reading)."""
    known = known if known is not None else {}
    here = _real(pkg_dir)
    home = _real("~")
    for _ in range(UP_LEVELS):
        if here in known:
            data = known[here]
        else:
            path = os.path.join(here, "package.json")
            data = _read_json(path) if os.path.isfile(path) else None
            known[here] = data
        found = _declared(data)
        if found:
            return found
        for name, manager in LOCKFILES:
            if os.path.isfile(os.path.join(here, name)):
                return manager
        parent = os.path.dirname(here)
        if parent == here or here == home:
            break
        here = parent
    return "npm"


def run_command(manager: str, script: str) -> str:
    """The command a person would type: ``npm run dev:staging``."""
    manager = manager if manager in MANAGERS else "npm"
    return f"{manager} run {shlex.quote(script)}"


def _is_hook(name: str, scripts: dict[str, Any]) -> bool:
    if name in LIFECYCLE:
        return True
    for prefix in ("pre", "post"):
        if name.startswith(prefix) and name[len(prefix):] in scripts:
            return True
    return False


# ─── The list ───────────────────────────────────────────────────────────

def _scan(root: str) -> dict[str, Any]:
    started = time.monotonic()
    files = _git_files(root)
    source = "git"
    truncated = False
    if files is None:
        files, truncated = _walk_files(root)
        source = "walk"
    if len(files) > MAX_PACKAGES:
        files, truncated = files[:MAX_PACKAGES], True

    known: dict[str, dict[str, Any] | None] = {}
    packages: list[dict[str, Any]] = []
    for rel in files:
        path = os.path.join(root, rel)
        pkg_dir = os.path.dirname(path)
        data = _read_json(path)
        known[_real(pkg_dir)] = data
        if not data:
            continue
        raw = data.get("scripts")
        if not isinstance(raw, dict):
            continue
        scripts = {k: v for k, v in raw.items() if isinstance(k, str) and k.strip() and isinstance(v, str)}
        if not scripts:
            continue
        manager = detect_manager(pkg_dir, known=known)
        rows = []
        for name, body in list(scripts.items())[:MAX_SCRIPTS]:
            body = " ".join(body.split())
            rows.append({
                "name": name,
                "body": body if len(body) <= MAX_BODY_CHARS else body[: MAX_BODY_CHARS - 1] + "…",
                "run": run_command(manager, name),
                "hook": _is_hook(name, scripts),
            })
        # What people run first, hooks after — otherwise the file's own order.
        rows.sort(key=lambda r: r["hook"])
        rel_dir = os.path.dirname(rel)
        name = data.get("name")
        packages.append({
            "dir": rel_dir,
            "path": _real(pkg_dir),
            "name": name.strip() if isinstance(name, str) and name.strip() else (os.path.basename(rel_dir) or os.path.basename(root)),
            "manager": manager,
            "scripts": rows,
        })
    # The project's own package first, then by folder.
    packages.sort(key=lambda p: (p["dir"] != "", p["dir"].count("/"), p["dir"].lower()))
    return {
        "ok": True,
        "root": root,
        "packages": packages,
        "count": sum(len(p["scripts"]) for p in packages),
        "truncated": truncated,
        "source": source,
        "ms": int((time.monotonic() - started) * 1000),
    }


def list_scripts(root: str | None = None, *, refresh: bool = False) -> dict[str, Any]:
    """Every package with scripts under ``root`` (default: the project folder).
    Cached for a few seconds; ``refresh`` scans again (a package.json was edited)."""
    root = _real(root or os.getcwd())
    if not os.path.isdir(root):
        return {"ok": False, "error": "The project folder is gone.", "root": root, "packages": []}
    now = time.monotonic()
    with _lock:
        hit = _cache.get(root)
        if hit and not refresh and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    result = _scan(root)
    with _lock:
        _cache[root] = (time.monotonic(), result)
    return result


def clear_cache() -> None:
    with _lock:
        _cache.clear()


__all__ = ["list_scripts", "detect_manager", "run_command", "clear_cache", "SKIP_DIRS"]
