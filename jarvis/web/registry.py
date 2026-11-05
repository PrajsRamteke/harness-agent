"""Which Jarvis web remotes are running on this computer (UI-free).

Every ``jarvis --web`` writes ``instances/<id>.json`` under the config dir and
refreshes it every few seconds. Any instance's page can then list the others
and reach them through its own link (``hub.py``), so several projects / chats
open in one browser tab, behind one link and one tunnel.

The files hold each instance's loopback port and token, so the folder is
``700`` and the files ``600`` — the same trust as ``mcp_secrets.json``.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..constants import CONFIG_DIR

INSTANCES_DIR = CONFIG_DIR / "instances"
TICK_SECS = 1.0       # how often a change (busy, approval, title) is noticed
HEARTBEAT_SECS = 3.0  # rewritten at least this often, changed or not
STALE_SECS = 15.0  # a file this old belongs to a process that died without cleaning up

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
CACHE_SECS = 5.0      # git branch / workspace project: looked up at most this often (stable between ticks)

_cache: dict[tuple[str, str], tuple[float, Any]] = {}
_cache_lock = threading.Lock()
_touched = [0.0]      # last non-polling web request (see ``touch``)

# Set when this process was "stopped" from the page while other projects still
# run through it: it stays up only to serve the page, hidden from every list.
_retired = threading.Event()
_current: "Registration | None" = None


def valid_id(value: str) -> bool:
    return bool(_ID_RE.match(value or ""))


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


def _path(instance_id: str) -> Path:
    return INSTANCES_DIR / f"{instance_id}.json"


def _write(instance_id: str, record: dict[str, Any]) -> None:
    INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(INSTANCES_DIR, 0o700)
    except OSError:
        pass
    target = _path(instance_id)
    tmp = target.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)  # a reader never sees half a file


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def touch() -> None:
    """A web request that is not polling (a prompt, an edit, a terminal key) just came in."""
    _touched[0] = time.time()


def last_touch() -> float:
    return _touched[0]


def _cached(kind: str, key: str, compute: Callable[[], Any]) -> Any:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get((kind, key))
        if hit is not None and now - hit[0] < CACHE_SECS:
            return hit[1]
    value = compute()
    with _cache_lock:
        _cache[(kind, key)] = (now, value)
    return value


def _read_branch(cwd: str) -> str:
    """The branch from ``.git/HEAD`` (a worktree's ``.git`` file points at it); '' when not a repo."""
    try:
        here = Path(cwd).resolve()
        for folder in (here, *here.parents):
            git = folder / ".git"
            if git.is_file():
                line = git.read_text(errors="replace").strip()
                if not line.startswith("gitdir:"):
                    return ""
                git = (folder / line[7:].strip()).resolve()
            if git.is_dir():
                head = (git / "HEAD").read_text(errors="replace").strip()
                if head.startswith("ref:"):
                    ref = head[4:].strip()
                    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
                return head[:8]  # detached: the short commit
    except (OSError, ValueError):
        pass
    return ""


def git_branch(cwd: str) -> str:
    """Current git branch of ``cwd`` (cached a few seconds; '' when it isn't a repo)."""
    return _cached("branch", cwd, lambda: _read_branch(cwd))


def workspace_project_id(cwd: str) -> str | None:
    """The workspace project ``cwd`` belongs to (cached a few seconds), or None."""
    def find() -> str | None:
        try:
            from ..storage import workspace

            proj = workspace.find_by_path(cwd, within=True)
            return str(proj["id"]) if proj else None
        except Exception:
            return None

    return _cached("project", cwd, find)


def remember_last_state(rec: dict[str, Any]) -> None:
    """Write what a stopping instance looked like into its workspace project. Never raises.

    A no-op when the folder is not in the workspace.
    """
    try:
        cwd = str(rec.get("cwd") or "")
        if not cwd:
            return
        from ..storage import workspace

        proj = workspace.find_by_path(cwd, within=True)
        if proj is None:
            return
        sid = rec.get("session_id")
        sid = int(sid) if isinstance(sid, int) or str(sid).isdigit() else None
        last = rec.get("last_active")
        workspace.set_last_state(proj["id"], {
            "branch": str(rec.get("branch") or _read_branch(cwd)),
            "session_id": sid,
            "session_title": str(rec.get("session_title") or ""),
            "model": str(rec.get("model") or ""),
            "last_active": float(last) if isinstance(last, (int, float)) else time.time(),
        })
    except Exception:
        pass


def retire() -> None:
    """Hide this process's entry (see ``launcher.stop_project``); written at once."""
    _retired.set()
    if _current is not None:
        _current._flush()


def retired() -> bool:
    return _retired.is_set()


def list_instances(*, prune: bool = True) -> list[dict[str, Any]]:
    """Live instances, oldest first (a stable order for the page's list).

    A retired one (only still up to serve a page) is left out: it is not a project.
    """
    try:
        files = sorted(INSTANCES_DIR.glob("*.json"))
    except OSError:
        return []
    now = time.time()
    out: list[dict[str, Any]] = []
    for path in files:
        rec = _read(path)
        if rec is None or not valid_id(str(rec.get("id") or "")):
            continue
        alive = _pid_alive(int(rec.get("pid") or 0))
        fresh = now - float(rec.get("updated") or 0) <= STALE_SECS
        if not (alive and fresh):
            if prune and (not alive or now - float(rec.get("updated") or 0) > STALE_SECS * 4):
                try:
                    path.unlink()
                except OSError:
                    pass
            continue
        if rec.get("retired"):
            continue
        out.append(rec)
    out.sort(key=lambda r: (float(r.get("started") or 0), str(r.get("id"))))
    return out


def get(instance_id: str) -> dict[str, Any] | None:
    if not valid_id(instance_id):
        return None
    for rec in list_instances(prune=False):
        if rec.get("id") == instance_id:
            return rec
    return None


def forget(instance_id: str) -> None:
    """Drop an entry whose process was killed (it never got to remove its own)."""
    if valid_id(instance_id):
        try:
            _path(instance_id).unlink()
        except OSError:
            pass


class Registration:
    """This process's entry: written now, refreshed by a daemon thread, removed on stop."""

    def __init__(
        self,
        *,
        instance_id: str,
        port: int,
        token: str,
        info: Callable[[], dict[str, Any]],
    ) -> None:
        self.id = instance_id
        self._base = {
            "id": instance_id,
            "pid": os.getpid(),
            "port": port,
            "token": token,
            "started": time.time(),
        }
        try:
            from ..constants import VERSION

            self._base["version"] = str(VERSION)
        except Exception:
            pass
        self._info = info
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_info: dict[str, Any] | None = None
        self._last_write = 0.0

    def _read_info(self) -> dict[str, Any]:
        try:
            return dict(self._info() or {})
        except Exception:
            return {}  # a field that can't be read this tick is left out; the rest still lands

    def _flush(self, info: dict[str, Any] | None = None) -> None:
        info = self._read_info() if info is None else info
        rec = {**self._base, **info, "updated": time.time()}
        if _retired.is_set():
            rec["retired"] = True
        try:
            _write(self.id, rec)
        except OSError:
            return  # read-only config dir: the remote still works on its own link
        self._last_info = info
        self._last_write = rec["updated"]

    def snapshot(self) -> dict[str, Any]:
        """This process's record as it would be written now (for the last-state note on stop)."""
        return {**self._base, **self._read_info()}

    def tick(self) -> bool:
        """Write if something changed or the heartbeat is due. True when written."""
        info = self._read_info()
        if info == self._last_info and time.time() - self._last_write < HEARTBEAT_SECS:
            return False
        self._flush(info)
        return True

    def start(self) -> None:
        global _current
        _current = self
        self._flush()
        atexit.register(self.stop)  # a normal exit leaves no entry behind
        self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-web-registry")
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(TICK_SECS):
            self.tick()

    def stop(self) -> None:
        self._stop.set()
        try:
            _path(self.id).unlink()
        except OSError:
            pass
