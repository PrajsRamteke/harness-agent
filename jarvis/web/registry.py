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


def list_instances(*, prune: bool = True) -> list[dict[str, Any]]:
    """Live instances, oldest first (a stable order for the page's list)."""
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
        try:
            _write(self.id, rec)
        except OSError:
            return  # read-only config dir: the remote still works on its own link
        self._last_info = info
        self._last_write = rec["updated"]

    def tick(self) -> bool:
        """Write if something changed or the heartbeat is due. True when written."""
        info = self._read_info()
        if info == self._last_info and time.time() - self._last_write < HEARTBEAT_SECS:
            return False
        self._flush(info)
        return True

    def start(self) -> None:
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
