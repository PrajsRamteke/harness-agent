"""Project lifecycle: the idle reaper rule, the warm pool selection, the single reaper lock."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from jarvis.storage import workspace as ws
from jarvis.web import launcher, reaper, registry

NOW = 1_000_000.0
IDLE = 31 * 60   # seconds idle, past the default 30 minutes


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "WORKSPACE_FILE", tmp_path / "workspace.json")
    monkeypatch.setattr(ws, "LOCK_FILE", tmp_path / "workspace.lock")
    monkeypatch.setattr(registry, "INSTANCES_DIR", tmp_path / "instances")
    registry._cache.clear()
    return tmp_path


def _rec(**kw):
    base = {"id": "i1", "pid": 999_999, "cwd": "/w/api", "headless": True, "busy": False,
            "needs_approval": False, "terminals": 0, "last_active": NOW - IDLE}
    return {**base, **kw}


def _proj(startup="lazy", pid="api", path="/w/api"):
    return {"id": pid, "path": path, "startup": startup}


SETTINGS = {"idle_stop_minutes": 30, "warm_limit": 3}


# ─── should_reap ─────────────────────────────────────────────────────────

def test_an_idle_headless_lazy_project_is_reaped():
    assert reaper.should_reap(_rec(), _proj("lazy"), SETTINGS, NOW)
    assert reaper.should_reap(_rec(), _proj("warm"), SETTINGS, NOW)


@pytest.mark.parametrize("rec,project,settings,self_id", [
    (dict(busy=True), _proj(), SETTINGS, ""),                          # working
    (dict(needs_approval=True), _proj(), SETTINGS, ""),                # waiting for an answer
    (dict(terminals=2), _proj(), SETTINGS, ""),                        # shells open
    (dict(attached=True), _proj(), SETTINGS, ""),                      # a browser is on it
    (dict(), _proj("pinned"), SETTINGS, ""),                           # pinned
    (dict(headless=False), _proj(), SETTINGS, ""),                     # started in a terminal
    (dict(), None, SETTINGS, ""),                                      # not in the workspace
    (dict(last_active=NOW - 29 * 60), _proj(), SETTINGS, ""),          # idle too short
    (dict(), _proj(), {**SETTINGS, "idle_stop_minutes": 0}, ""),       # reaping off
    (dict(), _proj(), SETTINGS, "i1"),                                 # the serving instance itself
    (dict(last_active=None), _proj(), SETTINGS, ""),                   # says nothing about its use
    (dict(last_active="x"), _proj(), SETTINGS, ""),
])
def test_should_reap_refuses(rec, project, settings, self_id):
    assert not reaper.should_reap(_rec(**rec), project, settings, NOW, self_id)


def test_the_idle_line_is_exclusive():
    just = _rec(last_active=NOW - 30 * 60)
    assert not reaper.should_reap(just, _proj(), SETTINGS, NOW)
    assert reaper.should_reap(just, _proj(), SETTINGS, NOW + 1)


# ─── reap_once ───────────────────────────────────────────────────────────

def _write_ws(tmp, projects, **settings):
    ws.WORKSPACE_FILE.write_text(json.dumps({
        "projects": projects, "order": [p["id"] for p in projects],
        "settings": {"idle_stop_minutes": 30, **settings},
    }))
    ws.load(fresh=True)


def _register(rid, cwd, **info):
    reg = registry.Registration(instance_id=rid, port=1, token="t", info=lambda: {"cwd": str(cwd), "headless": True, **info})
    reg.start()
    return reg


def test_reap_once_stops_only_what_the_rule_allows(isolated, monkeypatch):
    tmp = isolated
    for n in ("idle", "busy", "pin", "stranger"):
        (tmp / n).mkdir()
    _write_ws(tmp, [
        {"id": "idle", "path": str(tmp / "idle")},
        {"id": "busy", "path": str(tmp / "busy"), "startup": "warm"},
        {"id": "pin", "path": str(tmp / "pin"), "startup": "pinned"},
    ])
    old = time.time() - IDLE
    regs = [
        _register("idle", tmp / "idle", last_active=old),
        _register("busy", tmp / "busy", last_active=old, busy=True),
        _register("pin", tmp / "pin", last_active=old),
        _register("stranger", tmp / "stranger", last_active=old),
    ]
    monkeypatch.setattr(reaper, "_own_pid", lambda: -5)  # the test process owns every fake record
    stopped_ids: list[str] = []
    try:
        out = reaper.reap_once(stop=lambda i: stopped_ids.append(i) or {"ok": True})
    finally:
        for r in regs:
            r.stop()
    assert out == ["idle"] == stopped_ids


def test_reap_once_never_stops_its_own_process(isolated):
    tmp = isolated
    (tmp / "me").mkdir()
    _write_ws(tmp, [{"id": "me", "path": str(tmp / "me")}])
    reg = _register("me", tmp / "me", last_active=time.time() - IDLE)  # pid = this process
    try:
        assert reaper.reap_once(stop=lambda i: pytest.fail("stopped itself")) == []
    finally:
        reg.stop()


# ─── Warm pool selection ─────────────────────────────────────────────────

def _warm(names_startup, running=(), starting=(), *, limit=3, count=0, max_running=16, missing=()):
    projects = [{"id": n, "path": f"/w/{n}", "startup": s} for n, s in names_startup]
    got = reaper.warm_candidates(
        projects, {"warm_limit": limit}, {f"/w/{n}" for n in running}, {f"/w/{n}" for n in starting},
        max_running=max_running, running_count=count or len(running),
        isdir=lambda p: p.split("/")[-1] not in missing,
    )
    return [p["id"] for p in got]


def test_pinned_are_always_started_and_do_not_use_warm_slots():
    spec = [("a", "pinned"), ("b", "warm"), ("c", "warm"), ("d", "pinned"), ("e", "lazy")]
    assert _warm(spec, limit=1) == ["a", "d", "b"]
    assert _warm(spec, limit=0) == ["a", "d"]


def test_warm_limit_counts_warm_projects_already_running():
    spec = [("a", "warm"), ("b", "warm"), ("c", "warm")]
    assert _warm(spec, running=["a"], limit=2) == ["b"]
    assert _warm(spec, running=["a", "b"], limit=2) == []


def test_running_starting_and_missing_folders_are_skipped():
    spec = [("a", "pinned"), ("b", "pinned"), ("c", "warm"), ("d", "warm")]
    assert _warm(spec, running=["a"], starting=["c"], missing=["d"]) == ["b"]


def test_the_pool_respects_max_running():
    spec = [("a", "pinned"), ("b", "pinned"), ("c", "warm")]
    assert _warm(spec, count=15, max_running=16) == ["a"]
    assert _warm(spec, count=16, max_running=16) == []


def test_start_warm_pool_opens_each_through_the_launcher(isolated, monkeypatch):
    tmp = isolated
    for n in ("p", "w", "z"):
        (tmp / n).mkdir()
    _write_ws(tmp, [
        {"id": "w", "path": str(tmp / "w"), "startup": "warm"},
        {"id": "p", "path": str(tmp / "p"), "startup": "pinned"},
        {"id": "z", "path": str(tmp / "z")},
        {"id": "gone", "path": str(tmp / "gone"), "startup": "pinned"},
    ])
    opened: list[str] = []
    monkeypatch.setattr(launcher, "_launches", {})
    done = reaper.start_warm_pool(opener=lambda path, **kw: opened.append(path) or {"ok": True}, gap=0)
    assert [Path(p).name for p in opened] == ["p", "w"] == [Path(p).name for p in done]


def test_start_warm_pool_stops_at_too_many(isolated, monkeypatch):
    tmp = isolated
    for n in ("a", "b"):
        (tmp / n).mkdir()
    _write_ws(tmp, [{"id": n, "path": str(tmp / n), "startup": "pinned"} for n in ("a", "b")])
    calls: list[str] = []
    reaper.start_warm_pool(opener=lambda p, **kw: calls.append(p) or {"ok": False, "code": "too_many"}, gap=0)
    assert len(calls) == 1


def test_the_pool_is_claimed_once_per_live_starter(isolated):
    assert reaper.claim_warm_pool() is True
    assert reaper.claim_warm_pool() is False               # this process is alive and already did it
    marker = registry.INSTANCES_DIR / reaper.WARM_MARKER_NAME
    marker.write_text(json.dumps({"pid": 999_999_999, "at": 1}))
    assert reaper.claim_warm_pool() is True                # the starter died: someone else may start it


# ─── reaper.lock ─────────────────────────────────────────────────────────

def test_exactly_one_holder_of_the_reaper_lock(isolated):
    first = reaper.try_lock()
    assert first is not None
    try:
        assert reaper.try_lock() is None                  # a second instance can't take it
    finally:
        reaper.unlock(first)
    again = reaper.try_lock()
    assert again is not None                              # released: the next one takes over
    reaper.unlock(again)
    assert (registry.INSTANCES_DIR / "reaper.lock").exists()


def test_lifecycle_loop_elects_one_reaper(isolated, monkeypatch):
    passes: list[str] = []
    monkeypatch.setattr(reaper, "reap_once", lambda **kw: passes.append(kw["self_id"]) or [])
    a = reaper.Lifecycle("a", interval=0.05, warm_delay=60)
    b = reaper.Lifecycle("b", interval=0.05, warm_delay=60)
    a.start()
    b.start()
    try:
        deadline = time.time() + 3
        while not passes and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.2)
        assert [a.is_reaper, b.is_reaper].count(True) == 1
        assert len(set(passes)) == 1
    finally:
        a.stop()
        b.stop()


def test_background_is_off_under_pytest():
    assert reaper.enabled() is False and reaper.start_background("x") is None
