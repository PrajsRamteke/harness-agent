"""Open a project from the browser: start a Jarvis with no terminal in a folder.

``open_project`` starts ``jarvis --headless`` in the folder — the same app as
in a terminal, drawn nowhere, with its web remote on — detached from this
process (its own session), so it keeps running when this terminal closes.
A watcher thread follows it until it is listed in ``registry`` with a session
(``ready``), or it exits / takes too long (``failed``, with the end of its log).
The page polls ``launch_status`` and switches to it when it's ready.

``stop_project`` ends a Jarvis that was opened this way; one that runs in a
terminal is the user's to quit there.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..constants import CONFIG_DIR
from . import fs_api, registry

LAUNCH_TIMEOUT = 45.0   # a cold start (imports, catalogs) takes a few seconds; this is the "stuck" line
STOP_TIMEOUT = 10.0    # idle: well under a second; right after starting it may finish startup work first
STOP_ALL_WORKERS = 8   # projects stopped at the same time by `stop_all`
EXIT_DELAY = 0.4       # stopping the serving Jarvis: time for its answer to reach the page
RETIRED_POLL = 1.0     # a retired Jarvis checks this often whether it still serves anyone
RETIRED_EMPTY_POLLS = 2  # empty lists in a row before it goes (one bad read is not "alone")
MAX_RUNNING = 16
LOG_DIR = CONFIG_DIR / "instances" / "logs"
KEEP_LOGS = 20
SNAPSHOT_FILE = CONFIG_DIR / "workspace_snapshot.json"   # what "Stop all" leaves open, for "Restore"
RESUME_ENV = "HARNESS_RESUME_SESSION"                    # the chat a launched Jarvis opens with
# Errors that no retry fixes: the saved folder is gone.
_PERMANENT = ("not_found", "not_a_folder")
# Inherited settings that belong to the terminal Jarvis, not to a project opened from it.
_DROP_ENV = ("HARNESS_WEB_TUNNEL", "HARNESS_WEB", "HARNESS_UPDATED_REEXEC", "HARNESS_UPDATE_RESULT", RESUME_ENV)


def command() -> list[str]:
    """How a project is started (tests swap this for a stand-in)."""
    return [sys.executable, "-m", "jarvis", "--headless"]


@dataclass
class Launch:
    id: str
    path: str
    status: str = "starting"      # starting | ready | failed
    stage: str = "spawn"          # spawn → boot → ready
    pid: int = 0
    instance_id: str = ""
    code: str = ""
    error: str = ""
    log: str = ""
    log_path: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id, "path": self.path, "display": fs_api.display(self.path),
            "name": Path(self.path).name or self.path, "status": self.status, "stage": self.stage,
            "instance_id": self.instance_id, "code": self.code, "error": self.error, "log": self.log,
            "elapsed": round((self.finished or time.time()) - self.started, 1),
        }


_launches: dict[str, Launch] = {}
_lock = threading.Lock()


def _running_in(path: Path) -> list[dict[str, Any]]:
    real = os.path.realpath(path)
    return [r for r in registry.list_instances() if r.get("cwd") and os.path.realpath(str(r["cwd"])) == real]


def _log_tail(path: str, lines: int = 25) -> str:
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return ""
    # A headless Textual app may still write escape codes on exit: keep readable lines.
    keep = [ln.rstrip() for ln in text.splitlines() if ln.strip() and "\x1b" not in ln]
    return "\n".join(keep[-lines:])


def _reason(tail: str) -> str:
    """The line of the log that says what went wrong."""
    for line in reversed(tail.splitlines()):
        s = line.strip()
        if s and ("Error" in s or "error:" in s.lower() or "Exception" in s):
            return s[:300]
    return ""


def _prune_logs() -> None:
    try:
        logs = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in logs[KEEP_LOGS:]:
        try:
            old.unlink()
        except OSError:
            pass


def _finish(launch: Launch, status: str, *, code: str = "", error: str = "") -> None:
    with _lock:
        launch.status = status
        launch.code = code
        launch.error = error
        launch.finished = time.time()
        if status == "ready":
            launch.stage = "ready"


def _watch(launch: Launch, proc: subprocess.Popen) -> None:
    deadline = time.time() + LAUNCH_TIMEOUT
    iid = str(proc.pid)
    while time.time() < deadline:
        rec = registry.get(iid)
        if rec is not None:
            with _lock:
                launch.stage = "boot"
                launch.instance_id = iid
            if rec.get("session_id") is not None:  # it has a chat to show
                _finish(launch, "ready")
                break
        rc = proc.poll()
        if rc is not None:
            tail = _log_tail(launch.log_path)
            with _lock:
                launch.log = tail
            _finish(launch, "failed", code="exited",
                    error=_reason(tail) or f"Jarvis stopped while starting (exit code {rc}).")
            return
        time.sleep(0.2)
    else:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
        tail = _log_tail(launch.log_path)
        with _lock:
            launch.log = tail
        _finish(launch, "failed", code="timeout",
                error=f"Jarvis didn't start within {int(LAUNCH_TIMEOUT)} seconds.")
        return
    # Ready: it lives on its own now. Reap it when it exits, so it never lingers
    # as a zombie while this Jarvis runs.
    try:
        proc.wait()
    except Exception:
        pass


def open_project(raw_path: str, *, reuse: bool = True, resume_session: int | None = None) -> dict[str, Any]:
    """Start (or, with ``reuse``, find) a Jarvis in a folder. Never raises.

    ``resume_session``: the saved chat it opens with (a missing one starts a new chat).
    """
    try:
        path = fs_api.resolve_dir(raw_path)
    except fs_api.FsError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}
    if reuse:
        existing = _running_in(path)
        if existing:
            return {"ok": True, "reused": True, "instance_id": existing[0]["id"],
                    "launch": {"status": "ready", "instance_id": existing[0]["id"], "path": str(path),
                               "display": fs_api.display(path), "name": path.name}}
    if len(registry.list_instances()) >= MAX_RUNNING:
        return {"ok": False, "code": "too_many",
                "error": f"{MAX_RUNNING} projects are already open. Stop one you're done with first."}

    launch = Launch(id=uuid.uuid4().hex[:12], path=str(path))
    env = {k: v for k, v in os.environ.items() if k not in _DROP_ENV}
    env["PWD"] = str(path)
    env["HARNESS_LAUNCHED_BY"] = str(os.getpid())
    if resume_session:
        env[RESUME_ENV] = str(int(resume_session))
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in path.name)[:40] or "root"
        launch.log_path = str(LOG_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}-{launch.id[:4]}.log")
        log = open(launch.log_path, "ab")
    except OSError as exc:
        return {"ok": False, "code": "no_log", "error": f"Couldn't prepare the project's log: {exc}."}
    try:
        proc = subprocess.Popen(
            command(), cwd=str(path), env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,   # its own session: survives this terminal closing
            close_fds=True,
        )
    except OSError as exc:
        log.close()
        return {"ok": False, "code": "spawn_failed", "error": f"Couldn't start Jarvis: {exc.strerror or exc}."}
    finally:
        _prune_logs()
    log.close()  # the child holds its own copy
    launch.pid = proc.pid
    with _lock:
        _launches[launch.id] = launch
        # Forget finished launches after a while.
        cutoff = time.time() - 600
        for lid in [k for k, v in _launches.items() if v.finished and v.finished < cutoff]:
            _launches.pop(lid, None)
    fs_api.remember_dir(path)
    threading.Thread(target=_watch, args=(launch, proc), daemon=True, name=f"jarvis-launch-{launch.id}").start()
    return {"ok": True, "launch": launch.public()}


def launch_status(launch_id: str) -> dict[str, Any] | None:
    with _lock:
        launch = _launches.get(launch_id or "")
        return launch.public() if launch else None


def _terminate_self() -> None:
    os.kill(os.getpid(), signal.SIGTERM)  # the app's handler exits cleanly


def _exit_soon() -> None:
    """End this process once the answer in flight is out."""
    threading.Timer(EXIT_DELAY, lambda: _terminate_self()).start()


def _others(self_id: str) -> list[dict[str, Any]]:
    return [r for r in registry.list_instances() if str(r.get("id") or "") != self_id]


def _exit_when_alone() -> None:
    """A retired Jarvis: go once no other project is left to serve."""
    empty = 0
    while empty < RETIRED_EMPTY_POLLS:
        time.sleep(RETIRED_POLL)
        if not registry.retired():
            return
        empty = 0 if registry.list_instances() else empty + 1
    _terminate_self()


def _retire_self() -> dict[str, Any]:
    """Stop being a project but keep serving the page the others are reached through.

    The page came from this Jarvis's port; ending it would cut the browser off
    from every other project opened through it. So it leaves the list now and
    exits by itself when the last other project is gone.
    """
    registry.retire()
    threading.Thread(target=_exit_when_alone, daemon=True, name="jarvis-retired").start()
    return {"ok": True, "self": True, "retired": True}


def stop_project(instance_id: str) -> dict[str, Any]:
    """End a Jarvis that was opened from the web. Never raises.

    Stopping the one that serves this page while others still run retires it
    instead (``_retire_self``): the page keeps working until the last one stops.
    """
    rec = registry.get(instance_id)
    if rec is None:
        return {"ok": True, "already": True}
    if not rec.get("headless"):
        return {"ok": False, "code": "terminal",
                "error": f"{rec.get('project') or 'This Jarvis'} runs in a terminal — quit it there (Ctrl+C twice)."}
    pid = int(rec.get("pid") or 0)
    if pid <= 0:
        return {"ok": False, "code": "bad_entry", "error": "That project's entry is damaged."}
    if pid == os.getpid():
        if _others(instance_id):
            return _retire_self()
        # The last one, serving this page: answer first, then go.
        _exit_soon()
        return {"ok": True, "self": True}
    result = _stop_other(instance_id, pid)
    if result.get("ok") and registry.retired() and not registry.list_instances():
        _exit_soon()  # that was the last project this retired Jarvis served
        result["self"] = True
    return result


def _stop_other(instance_id: str, pid: int) -> dict[str, Any]:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"ok": True, "already": True}
    except PermissionError:
        return {"ok": False, "code": "no_access", "error": "Not allowed to stop that process."}
    deadline = time.time() + STOP_TIMEOUT
    while time.time() < deadline:
        if registry.get(instance_id) is None:
            return {"ok": True}
        time.sleep(0.15)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    registry.forget(instance_id)  # killed: its own cleanup never ran
    return {"ok": True, "forced": True}


def stop_all(self_id: str) -> dict[str, Any]:
    """End every Jarvis opened from the web, the one serving this page last. Never raises.

    Others stop in parallel (each waits for its own clean exit), then this one is
    scheduled to go once the answer is out. A Jarvis running in a terminal is not
    ours to quit: it is listed in ``skipped``, and — when it is the one serving
    this page — its web terminals are closed, the only part of it the page started.
    ``stopped`` / ``failed`` / ``skipped`` hold ``{id, project}`` rows; ``self`` is
    true when the serving Jarvis is on its way out (the page should say goodbye).
    """
    rows: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    saved: list[dict[str, Any]] = []
    for rec in registry.list_instances():
        rid = str(rec.get("id") or "")
        if not rid:
            continue
        row = {"id": rid, "project": str(rec.get("project") or "")}
        if rec.get("headless"):
            rows[rid] = row
            saved.append(_snapshot_entry(rec))
        else:
            skipped.append(row)
    _save_workspace(saved)  # before anything stops: this is what "Restore" brings back
    others = [rid for rid in rows if rid != self_id]
    stopped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    if others:
        with ThreadPoolExecutor(max_workers=min(len(others), STOP_ALL_WORKERS)) as pool:
            results = list(pool.map(stop_project, others))
        for rid, res in zip(others, results):
            if res.get("ok"):
                stopped.append(rows[rid])
            else:
                failed.append({**rows[rid], "error": str(res.get("error") or "Couldn't stop it.")})
    going = self_id in rows
    if not going and registry.retired() and not failed:
        _exit_soon()  # serving the page for the others only, and they are gone
        going = True
    elif going:
        res = stop_project(self_id)
        if res.get("ok"):
            stopped.append(rows[self_id])
        else:
            going = False
            failed.append({**rows[self_id], "error": str(res.get("error") or "Couldn't stop it.")})
    else:
        from . import terminal_api

        terminal_api.close_all()
    return {"ok": not failed, "stopped": stopped, "failed": failed, "skipped": skipped, "self": going,
            "saved": len(saved)}


# ─── The saved workspace: "Stop all" now, "Restore" tomorrow ────────────────

def _snapshot_entry(rec: dict[str, Any]) -> dict[str, Any]:
    sid = rec.get("session_id")
    return {
        "project": str(rec.get("project") or ""),
        "cwd": str(rec.get("cwd") or ""),
        "title": str(rec.get("session_title") or rec.get("title") or ""),
        "session_id": int(sid) if isinstance(sid, int) or str(sid).isdigit() else None,
    }


def _save_workspace(entries: list[dict[str, Any]]) -> None:
    """Write what is open now (nothing open: leave the last snapshot alone). Never raises."""
    if not entries:
        return
    try:
        SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SNAPSHOT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"saved_at": time.time(), "projects": entries}))
        os.chmod(tmp, 0o600)
        os.replace(tmp, SNAPSHOT_FILE)
    except OSError:
        pass


def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {"project": entry["project"], "display": fs_api.display(entry["cwd"]),
            "title": entry["title"], "session_id": entry["session_id"]}


def _read_workspace() -> tuple[float, list[dict[str, Any]]] | None:
    try:
        raw = json.loads(SNAPSHOT_FILE.read_text())
        entries = [e for e in raw["projects"] if isinstance(e, dict) and e.get("cwd")]
        return float(raw["saved_at"]), [{**_snapshot_entry(e)} for e in entries]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def saved_workspace() -> dict[str, Any] | None:
    """The snapshot as the page shows it: ``{saved_at, projects: [{project, display, title, session_id}]}``."""
    found = _read_workspace()
    if found is None or not found[1]:
        return None
    return {"saved_at": found[0], "projects": [_public_entry(e) for e in found[1]]}


def forget_workspace() -> None:
    try:
        SNAPSHOT_FILE.unlink()
    except OSError:
        pass


def restore_workspace() -> dict[str, Any]:
    """Start a Jarvis for every saved project, each opening its chat. Never raises.

    Starts are asynchronous (the page watches the Projects list fill up). A chat that
    is already running is not started twice; one that can't start for a passing reason
    (too many open) stays saved, one whose folder is gone is dropped.
    """
    found = _read_workspace()
    if found is None or not found[1]:
        forget_workspace()
        return {"ok": True, "restored": [], "failed": []}
    running = {str(r.get("session_id")) for r in registry.list_instances() if r.get("session_id") is not None}
    restored: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    keep: list[dict[str, Any]] = []
    for entry in found[1]:
        sid = entry["session_id"]
        if sid is not None and str(sid) in running:
            restored.append(_public_entry(entry))
            continue
        res = open_project(entry["cwd"], reuse=sid is None, resume_session=sid)
        if res.get("ok"):
            restored.append(_public_entry(entry))
            continue
        failed.append({**_public_entry(entry), "error": str(res.get("error") or "Couldn't start it.")})
        if res.get("code") not in _PERMANENT:
            keep.append(entry)
    if keep:
        try:
            tmp = SNAPSHOT_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"saved_at": found[0], "projects": keep}))
            os.chmod(tmp, 0o600)
            os.replace(tmp, SNAPSHOT_FILE)
        except OSError:
            pass
    else:
        forget_workspace()
    return {"ok": not failed, "restored": restored, "failed": failed}
