"""Live state of a team of parallel subagents (UI-free).

One ``Team`` per ``spawn_agents`` call, keyed by that tool call's id, holding
one ``AgentRun`` per subagent. Runners mutate their agent under the team lock
and call :meth:`Team.changed`; a small notifier thread coalesces those into at
most ``PUBLISH_HZ`` snapshots a second and hands them to the console
(``console.subagents_update``) — the TUI board and the web card both render
from that one snapshot shape.
"""
from __future__ import annotations

import difflib
import os
import threading
import time
from collections import OrderedDict
from typing import Any

PUBLISH_HZ = 4
LOG_KEEP = 40            # activity entries kept per agent
REPORT_SNAPSHOT_MAX = 8000  # chars of a report in a UI snapshot

# queued → running → one of the finished states
FINISHED = ("done", "error", "stopped", "cancelled", "timeout")
STATUS_LABELS = {
    "queued": "Queued",
    "running": "Running",
    "done": "Done",
    "error": "Failed",
    "stopped": "Stopped",
    "cancelled": "Cancelled",
    "timeout": "Timed out",
}


class AgentRun:
    def __init__(self, index: int, name: str, task: str, mode: str) -> None:
        self.index = index
        self.name = name
        self.task = task
        self.mode = mode                  # "explore" (read-only) | "edit"
        self.status = "queued"
        self.activity = "Waiting to start"
        self.steps = 0                    # tool calls
        self.turns = 0                    # model requests
        self.tokens_in = 0
        self.tokens_out = 0
        self.started: float | None = None   # wall clock (time.time)
        self.finished: float | None = None
        self.report = ""
        self.error = ""
        self.log: list[dict[str, Any]] = []
        self.files: "OrderedDict[str, list[int]]" = OrderedDict()  # path → [added, removed]
        self.thread_id = 0
        self.stream: Any = None
        self.stop_reason = ""             # why stop() was called ("stopped" | "cancelled" | "timeout")

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED

    def elapsed(self, now: float | None = None) -> float:
        if self.started is None:
            return 0.0
        end = self.finished if self.finished is not None else (now or time.time())
        return max(0.0, end - self.started)

    def snapshot(self, now: float, *, report: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "i": self.index,
            "name": self.name,
            "task": self.task,
            "mode": self.mode,
            "status": self.status,
            "activity": self.activity,
            "steps": self.steps,
            "turns": self.turns,
            "tokens": self.tokens_in + self.tokens_out,
            "tokens_out": self.tokens_out,
            "started": self.started,
            "finished": self.finished,
            "elapsed": round(self.elapsed(now), 1),
            "error": self.error,
            "log": [dict(e) for e in self.log[-12:]],
            "files": [{"path": p, "added": a, "removed": r} for p, (a, r) in self.files.items()],
        }
        if report and self.report:
            text = self.report
            if len(text) > REPORT_SNAPSHOT_MAX:
                text = text[:REPORT_SNAPSHOT_MAX].rstrip() + "\n\n… (report shortened for display)"
            d["report"] = text
        d["has_report"] = bool(self.report)
        return d


class Team:
    def __init__(self, team_id: str, goal: str, agents: list[AgentRun], *,
                 parent_thread: int = 0) -> None:
        self.id = team_id
        self.goal = goal
        self.agents = agents
        self.parent_thread = parent_thread
        self.lock = threading.RLock()
        self.created = time.time()
        self.finished: float | None = None
        self.claims: dict[str, int] = {}   # resolved path → agent index editing it
        self.images: list[dict] = []       # image blocks the user attached — every agent sees them
        self._dirty = threading.Event()
        self._done = threading.Event()
        self._changed_cv = threading.Condition()
        self._notifier: threading.Thread | None = None

    # ── progress ─────────────────────────────────────────────────────
    def changed(self) -> None:
        self._dirty.set()
        with self._changed_cv:
            self._changed_cv.notify_all()

    def wait_change(self, timeout: float) -> None:
        with self._changed_cv:
            self._changed_cv.wait(timeout)

    def all_finished(self) -> bool:
        with self.lock:
            return all(a.is_finished for a in self.agents)

    def counts(self) -> dict[str, int]:
        with self.lock:
            out = {"queued": 0, "running": 0, "done": 0, "failed": 0}
            for a in self.agents:
                if a.status == "queued":
                    out["queued"] += 1
                elif a.status == "running":
                    out["running"] += 1
                elif a.status == "done":
                    out["done"] += 1
                else:
                    out["failed"] += 1
            return out

    def log(self, agent: AgentRun, label: str, *, kind: str = "tool",
            status: str = "running", key: str = "") -> None:
        """Add (or update, by ``key``) one activity entry for ``agent``."""
        label = " ".join(str(label or "").split())  # a board row is one line
        with self.lock:
            if key:
                for e in reversed(agent.log):
                    if e.get("key") == key:
                        e["status"] = status
                        if label:
                            e["label"] = label
                        self.changed()
                        return
            agent.log.append({"label": label, "kind": kind, "status": status,
                              "key": key, "t": round(time.time(), 1)})
            if len(agent.log) > LOG_KEEP:
                del agent.log[: len(agent.log) - LOG_KEEP]
        self.changed()

    def set_activity(self, agent: AgentRun, text: str) -> None:
        text = " ".join(str(text or "").split())
        with self.lock:
            if agent.activity == text:
                return
            agent.activity = text
        self.changed()

    def note_diff(self, agent: AgentRun, path: str, before: str, after: str) -> None:
        added = removed = 0
        for ln in difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(),
                                       lineterm="", n=0):
            if ln.startswith("+") and not ln.startswith("+++"):
                added += 1
            elif ln.startswith("-") and not ln.startswith("---"):
                removed += 1
        rel = _rel(path)
        with self.lock:
            a0, r0 = agent.files.pop(rel, [0, 0])
            agent.files[rel] = [a0 + added, r0 + removed]
        self.changed()

    # ── file ownership between parallel agents ───────────────────────
    def claim(self, agent: AgentRun, path: str) -> AgentRun | None:
        """Let ``agent`` edit ``path``; returns the other agent when it's taken."""
        key = _resolve(path)
        with self.lock:
            owner = self.claims.get(key)
            if owner is not None and owner != agent.index:
                return self.agents[owner]
            self.claims[key] = agent.index
        return None

    # ── stopping ─────────────────────────────────────────────────────
    def stop_agent(self, index: int, reason: str = "stopped") -> bool:
        """Stop one agent (its stream is closed and its thread marked cancelled)."""
        from .. import state

        with self.lock:
            if not 0 <= index < len(self.agents):
                return False
            agent = self.agents[index]
            if agent.is_finished:
                return False
            agent.stop_reason = agent.stop_reason or reason
            if agent.status == "queued":
                agent.status = reason
                agent.activity = STATUS_LABELS.get(reason, reason)
                agent.finished = time.time()
            stream, tid = agent.stream, agent.thread_id
        if tid:
            state.cancel_thread(tid)
        _close_quietly(stream)
        self.changed()
        return True

    def stop_all(self, reason: str = "cancelled") -> None:
        for a in list(self.agents):
            self.stop_agent(a.index, reason)

    def stopped(self, agent: AgentRun) -> bool:
        from .. import state

        return bool(agent.stop_reason) or state.turn_cancelled()

    # ── snapshots + publishing ───────────────────────────────────────
    def snapshot(self, *, reports: bool = True) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            agents = [a.snapshot(now, report=reports) for a in self.agents]
            done = self.finished is not None
            started = [a.started for a in self.agents if a.started]
            ends = [a.finished or now for a in self.agents if a.started]
            elapsed = (max(ends) - min(started)) if started else 0.0
        counts = self.counts()
        return {
            "id": self.id,
            "goal": self.goal,
            "status": "done" if done else "running",
            "created": self.created,
            "finished": self.finished,
            "now": now,
            "elapsed": round(max(0.0, elapsed), 1),
            "counts": counts,
            "total": len(agents),
            "steps": sum(a["steps"] for a in agents),
            "tokens": sum(a["tokens"] for a in agents),
            "agents": agents,
        }

    def start_notifier(self) -> None:
        def _loop() -> None:
            while not self._done.is_set():
                self._dirty.wait(timeout=1.0)
                if self._done.is_set():
                    break
                if self._dirty.is_set():
                    self._dirty.clear()
                    publish(self)
                time.sleep(1.0 / PUBLISH_HZ)
            publish(self)

        self._notifier = threading.Thread(target=_loop, name=f"subagents-{self.id[-6:]}", daemon=True)
        self._notifier.start()

    def close(self) -> None:
        with self.lock:
            if self.finished is None:
                self.finished = time.time()
        self._done.set()
        self._dirty.set()
        if self._notifier is not None:
            self._notifier.join(timeout=2.0)
        else:
            publish(self)


def _close_quietly(stream: Any) -> None:
    if stream is None:
        return
    for attr in ("close", "aclose"):
        fn = getattr(stream, attr, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
            return
    resp = getattr(stream, "response", None)
    if resp is not None:
        try:
            resp.close()
        except Exception:
            pass


def _resolve(path: str) -> str:
    try:
        from ..path_resolve import robust_resolve

        p = robust_resolve(path)
        if p is not None:
            return str(p)
    except Exception:
        pass
    return os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))


def _rel(path: str) -> str:
    full = _resolve(path)
    try:
        rel = os.path.relpath(full, os.getcwd())
        if not rel.startswith(".."):
            return rel
    except ValueError:
        pass
    return full


# ── registry: recent teams by tool id ────────────────────────────────────

_TEAMS_KEEP = 24
_teams: "OrderedDict[str, Team]" = OrderedDict()
_teams_lock = threading.Lock()


def register(team: Team) -> None:
    with _teams_lock:
        _teams[team.id] = team
        while len(_teams) > _TEAMS_KEEP:
            _teams.popitem(last=False)


def get(team_id: str) -> Team | None:
    with _teams_lock:
        return _teams.get(str(team_id or ""))


def running_teams() -> list[Team]:
    with _teams_lock:
        return [t for t in _teams.values() if t.finished is None]


def stop(team_id: str, index: int | None = None) -> bool:
    """Stop one agent of a team, or the whole team (``index=None``)."""
    team = get(team_id)
    if team is None or team.finished is not None:
        return False
    if index is None:
        team.stop_all("stopped")
        return True
    return team.stop_agent(int(index), "stopped")


def publish(team: Team) -> None:
    """Hand the latest snapshot to the console (TUI board / web card)."""
    try:
        from ..console import console

        fn = getattr(console, "subagents_update", None)
        if callable(fn):
            fn(team.id, team.snapshot())
    except Exception:
        pass
