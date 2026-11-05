"""Project lifecycle: the warm pool at start-up and the idle reaper (UI-free).

* **Warm pool** — when a web remote starts, every workspace project whose
  ``startup`` is ``pinned`` (always) or ``warm`` (up to ``settings.warm_limit``)
  and that isn't running is started headless, in the background, once per
  "generation" (a marker file names the instance that did it).
* **Reaper** — exactly one instance (the one holding the exclusive flock on
  ``instances/reaper.lock``) stops idle *headless* instances of workspace
  projects whose startup is ``lazy`` / ``warm``. ``should_reap`` is the pure rule.

Neither runs under pytest or with ``HARNESS_WARM_POOL=0``.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

from . import registry

REAP_INTERVAL = 30.0   # seconds between reaper passes
WARM_DELAY = 2.0       # start-up work first; the pool starts after this
WARM_GAP = 0.3         # between two warm starts (don't spike the CPU)
LOCK_NAME = "reaper.lock"
WARM_LOCK_NAME = "warm_pool.lock"
WARM_MARKER_NAME = "warm_pool.json"
REAPABLE_STARTUP = ("lazy", "warm")


def enabled() -> bool:
    if os.environ.get("HARNESS_WARM_POOL", "").strip().lower() in ("0", "off", "false", "no"):
        return False
    return "PYTEST_CURRENT_TEST" not in os.environ


# ─── flock helpers ───────────────────────────────────────────────────────────

def try_lock(name: str = LOCK_NAME) -> int | None:
    """An exclusive, non-blocking flock on ``instances/<name>``: the fd, or None when someone holds it."""
    try:
        import fcntl
    except ImportError:  # Windows: no election, no reaper
        return None
    try:
        registry.INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(registry.INSTANCES_DIR / name), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def unlock(fd: int | None) -> None:
    if fd is None:
        return
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


# ─── Reaper ──────────────────────────────────────────────────────────────────

def should_reap(rec: dict[str, Any], project: dict[str, Any] | None, settings: dict[str, Any],
                now: float, self_id: str = "") -> bool:
    """True when this instance may be stopped for being idle. Every condition must hold."""
    if project is None or project.get("startup") not in REAPABLE_STARTUP:
        return False                      # not in the workspace / pinned
    if not rec.get("headless"):
        return False                      # a terminal's Jarvis is the user's to quit
    if self_id and str(rec.get("id") or "") == self_id:
        return False
    try:
        minutes = float(settings.get("idle_stop_minutes") or 0)
    except (TypeError, ValueError):
        return False
    if minutes <= 0:
        return False
    if rec.get("busy") or rec.get("needs_approval") or rec.get("attached"):
        return False
    if int(rec.get("terminals") or 0) > 0:
        return False
    last = rec.get("last_active")
    if not isinstance(last, (int, float)) or isinstance(last, bool):
        return False                      # an instance that doesn't say when it was last used is left alone
    return now - float(last) > minutes * 60


def _own_pid() -> int:
    return os.getpid()


def reap_once(*, now: float | None = None, self_id: str = "",
              stop: Callable[[str], dict[str, Any]] | None = None) -> list[str]:
    """One pass: stop every idle instance ``should_reap`` allows. Returns the ids stopped. Never raises."""
    try:
        from ..storage import workspace

        if stop is None:
            from . import launcher

            stop = launcher.stop_project
        data = workspace.load()
        settings = data["settings"]
        if int(settings.get("idle_stop_minutes") or 0) <= 0:
            return []
        now = time.time() if now is None else now
        stopped: list[str] = []
        for rec in registry.list_instances():
            if int(rec.get("pid") or 0) == _own_pid():
                continue                  # never itself: it serves the page
            proj = workspace.find_by_path(str(rec.get("cwd") or "/nonexistent"), within=True, data=data) \
                if rec.get("cwd") else None
            if should_reap(rec, proj, settings, now, self_id):
                res = stop(str(rec["id"]))
                if res.get("ok"):
                    stopped.append(str(rec["id"]))
        return stopped
    except Exception:
        return []


# ─── Warm pool ───────────────────────────────────────────────────────────────

def warm_candidates(projects: list[dict[str, Any]], settings: dict[str, Any], running: set[str],
                    starting: set[str], *, max_running: int, running_count: int,
                    isdir: Callable[[str], bool] = os.path.isdir) -> list[dict[str, Any]]:
    """Projects to start, in order: pinned first (always), then warm ones up to ``warm_limit``.

    ``running`` / ``starting`` hold real folder paths. A warm project that already
    runs uses one of the ``warm_limit`` slots; pinned ones never do. Missing
    folders are skipped; the total never exceeds ``max_running``.
    """
    try:
        slots = int(settings.get("warm_limit") or 0)
    except (TypeError, ValueError):
        slots = 0
    room = max_running - running_count - len(starting)
    real = lambda p: os.path.realpath(p["path"])  # noqa: E731
    slots -= sum(1 for p in projects if p.get("startup") == "warm" and real(p) in running)
    out: list[dict[str, Any]] = []
    for want in ("pinned", "warm"):
        for proj in projects:
            if room <= 0:
                return out
            if proj.get("startup") != want:
                continue
            path = real(proj)
            if path in running or path in starting or not isdir(path):
                continue
            if want == "warm":
                if slots <= 0:
                    continue
                slots -= 1
            out.append(proj)
            room -= 1
    return out


def claim_warm_pool() -> bool:
    """True for the one instance that should start the pool (a marker names who did; stale ones are retaken)."""
    fd = None
    try:
        import fcntl

        registry.INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(registry.INSTANCES_DIR / WARM_LOCK_NAME), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        marker = registry.INSTANCES_DIR / WARM_MARKER_NAME
        try:
            owner = int(json.loads(marker.read_text()).get("pid") or 0)
        except (OSError, ValueError, AttributeError):
            owner = 0
        if owner and registry._pid_alive(owner):
            return False
        tmp = marker.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"pid": os.getpid(), "at": time.time()}))
        os.replace(tmp, marker)
        return True
    except Exception:
        return False
    finally:
        unlock(fd)


def start_warm_pool(*, opener: Callable[..., dict[str, Any]] | None = None, gap: float = WARM_GAP,
                    stop: threading.Event | None = None) -> list[str]:
    """Start every pinned / warm project that isn't running. Returns the folders started. Never raises."""
    started: list[str] = []
    try:
        from ..storage import workspace

        from . import launcher

        opener = opener or launcher.open_project
        data = workspace.load()
        live = registry.list_instances()
        running = {os.path.realpath(str(r["cwd"])) for r in live if r.get("cwd")}
        starting = {os.path.realpath(p) for p in launcher.starting_paths()}
        picks = warm_candidates(workspace.projects(data), data["settings"], running, starting,
                                max_running=launcher.MAX_RUNNING, running_count=len(live))
        for proj in picks:
            if stop is not None and stop.is_set():
                break
            res = opener(proj["path"], reuse=True)
            if res.get("ok") and not res.get("reused"):
                started.append(proj["path"])
            elif res.get("code") == "too_many":
                break
            if gap:
                time.sleep(gap)
    except Exception:
        pass
    return started


# ─── The background piece a web remote runs ──────────────────────────────────

class Lifecycle:
    """Warm pool (once, after a short delay) + reaper loop (when this process wins the flock)."""

    def __init__(self, self_id: str = "", *, interval: float = REAP_INTERVAL, warm_delay: float = WARM_DELAY) -> None:
        self.self_id = self_id
        self.interval = interval
        self.warm_delay = warm_delay
        self._stop = threading.Event()
        self._lock_fd: int | None = None
        self._threads: list[threading.Thread] = []

    @property
    def is_reaper(self) -> bool:
        return self._lock_fd is not None

    def start(self) -> None:
        for target, name in ((self._warm, "jarvis-warm-pool"), (self._reap, "jarvis-reaper")):
            t = threading.Thread(target=target, daemon=True, name=name)
            t.start()
            self._threads.append(t)

    def _warm(self) -> None:
        if self._stop.wait(self.warm_delay):
            return
        if claim_warm_pool():
            start_warm_pool(stop=self._stop)

    def _reap(self) -> None:
        while not self._stop.wait(self.interval):
            if self._lock_fd is None:
                self._lock_fd = try_lock()
            if self._lock_fd is not None:
                reap_once(self_id=self.self_id)

    def stop(self) -> None:
        self._stop.set()
        unlock(self._lock_fd)
        self._lock_fd = None


def start_background(instance_id: str = "") -> Lifecycle | None:
    """Start the pool and the reaper for this remote (None when switched off)."""
    if not enabled():
        return None
    life = Lifecycle(instance_id)
    life.start()
    return life
