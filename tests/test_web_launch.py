"""Open a folder from the web: folder listing, starting / stopping a Jarvis, moving a chat."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from jarvis import state
from jarvis.web import fs_api, launcher, registry
from jarvis.web.bridge import WebBridge
from jarvis.web.server import start_web_server, stop_web_server


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    inst = tmp_path / "instances"
    monkeypatch.setattr(registry, "INSTANCES_DIR", inst)
    monkeypatch.setattr(launcher, "LOG_DIR", inst / "logs")
    monkeypatch.setattr(fs_api, "RECENT_FILE", tmp_path / "recent_dirs.json")
    monkeypatch.setenv("FAKE_INSTANCES", str(inst))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(fs_api, "home", lambda: home)
    return home


def _tree(home: Path) -> Path:
    work = home / "work"
    (work / "api" / ".git").mkdir(parents=True)
    (work / "api" / "pyproject.toml").write_text("")
    (work / "web").mkdir()
    (work / "web" / "package.json").write_text("{}")
    (work / "notes").mkdir()
    (work / ".cache").mkdir()
    (work / "README.md").write_text("a file, never listed")
    return work


# ─── Folder listing ──────────────────────────────────────────────────────


def test_lists_sub_folders_with_project_markers_and_breadcrumb(isolated):
    work = _tree(isolated)
    data = fs_api.list_dirs(str(work))
    assert data["ok"] and data["display"] == "~/work"
    assert [d["name"] for d in data["dirs"]] == ["api", "notes", "web"]  # files and .hidden left out
    by = {d["name"]: d for d in data["dirs"]}
    assert by["api"]["markers"][:2] == ["git", "Python"] and by["web"]["markers"] == ["Node"]
    assert by["notes"]["markers"] == []
    assert data["hidden"] == 1 and data["parent"] == str(isolated)
    assert [s["name"] for s in data["segments"]] == ["~", "work"]
    shown = fs_api.list_dirs(str(work), show_hidden=True)
    assert ".cache" in [d["name"] for d in shown["dirs"]]


def test_paths_with_tilde_and_relative_ones(isolated):
    work = _tree(isolated)
    assert fs_api.list_dirs("~/work")["path"] == str(work.resolve())
    assert fs_api.list_dirs("")["path"] == str(isolated.resolve())          # empty → home
    assert fs_api.resolve_dir("api", default=work) == (work / "api").resolve()


@pytest.mark.parametrize("raw,code", [
    ("~/nope", "not_found"),
    ("~/work/README.md", "not_a_folder"),
    ("bad\x00path", "bad_path"),
])
def test_bad_folders_say_what_is_wrong(isolated, raw, code):
    _tree(isolated)
    with pytest.raises(fs_api.FsError) as exc:
        fs_api.list_dirs(raw)
    assert exc.value.code == code and str(exc.value)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_a_folder_jarvis_may_not_open(isolated):
    locked = isolated / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        with pytest.raises(fs_api.FsError) as exc:
            fs_api.list_dirs(str(locked))
        assert exc.value.code == "no_access"
        assert fs_api.list_dirs(str(isolated))["dirs"][0]["readable"] is False
    finally:
        locked.chmod(0o755)


def test_huge_folders_are_cut_and_say_so(isolated, monkeypatch):
    monkeypatch.setattr(fs_api, "MAX_ENTRIES", 5)
    for i in range(8):
        (isolated / f"d{i}").mkdir()
    data = fs_api.list_dirs(str(isolated))
    assert len(data["dirs"]) == 5 and data["truncated"] == 3 and data["total"] == 8


def test_running_jarvis_is_marked_on_its_folder(isolated):
    work = _tree(isolated)
    reg = registry.Registration(instance_id="r1", port=1, token="t",
                                info=lambda: {"cwd": str(work / "api"), "project": "api"})
    reg.start()
    try:
        by = {d["name"]: d for d in fs_api.list_dirs(str(work))["dirs"]}
        assert [r["id"] for r in by["api"]["running"]] == ["r1"]
        assert by["web"]["running"] == []
        assert "token" not in json.dumps(by["api"])
    finally:
        reg.stop()


def test_recent_folders_newest_first_and_only_existing(isolated):
    work = _tree(isolated)
    fs_api.remember_dir(work / "api")
    fs_api.remember_dir(work / "web")
    fs_api.remember_dir(work / "api")          # again: moves to the top, no duplicate
    fs_api.remember_dir(isolated)              # home is never "recent"
    fs_api.remember_dir(isolated / "gone")
    rows = fs_api.recent_dirs()
    assert [r["name"] for r in rows] == ["api", "web"]
    assert rows[0]["display"] == "~/work/api"


def test_shortcuts_only_list_folders_that_exist(isolated):
    (isolated / "Desktop").mkdir()
    names = [s["name"] for s in fs_api.shortcuts()]
    assert names[0] == "Home" and "Desktop" in names and "Documents" not in names


# ─── Moving the chat ─────────────────────────────────────────────────────


def test_moving_the_chat_changes_folder_and_project_context(isolated, monkeypatch):
    work = _tree(isolated)
    (work / "api" / "CLAUDE.md").write_text("# api")
    monkeypatch.chdir(work / "web")
    res = fs_api.change_cwd("~/work/api")
    assert res["ok"] and Path(os.getcwd()) == (work / "api").resolve()
    assert res["display"] == "~/work/api"
    assert state.project_context_file == "CLAUDE.md"
    assert fs_api.recent_dirs()[0]["name"] == "api"
    again = fs_api.change_cwd(str(work / "api"))
    assert again["ok"] and again.get("unchanged")
    bad = fs_api.change_cwd("~/nowhere")
    assert not bad["ok"] and bad["code"] == "not_found"
    assert Path(os.getcwd()) == (work / "api").resolve()  # a failed move stays put


# ─── Starting a Jarvis (a stand-in child process) ────────────────────────

_FAKE = r'''
import os, sys, time, signal
sys.path.insert(0, {root!r})
from jarvis.web import registry
registry.INSTANCES_DIR = __import__("pathlib").Path(os.environ["FAKE_INSTANCES"])
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "crash":
    print("RuntimeError: no provider configured", flush=True)
    sys.exit(3)
if mode == "hang":
    time.sleep(60)
reg = registry.Registration(instance_id=str(os.getpid()), port=1, token="t",
                            info=lambda: {{"cwd": os.getcwd(), "project": os.path.basename(os.getcwd()),
                                          "session_id": 7, "headless": True}})
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
reg.start()
while True:
    time.sleep(0.1)
'''


@pytest.fixture
def fake_child(tmp_path, monkeypatch):
    script = tmp_path / "fake_jarvis.py"
    script.write_text(_FAKE.format(root=str(Path(__file__).resolve().parents[1])))
    monkeypatch.setattr(launcher, "command", lambda: [sys.executable, str(script)])
    started: list[str] = []
    yield started
    for rec in registry.list_instances():
        if int(rec["pid"]) == os.getpid():  # the test's own server
            continue
        try:
            os.kill(int(rec["pid"]), 15)
        except OSError:
            pass


def _wait(launch_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = launcher.launch_status(launch_id)
        if st and st["status"] != "starting":
            return st
        time.sleep(0.1)
    raise AssertionError("launch never finished")


def test_open_starts_a_jarvis_in_the_folder_and_it_is_listed(isolated, fake_child):
    work = _tree(isolated)
    res = launcher.open_project("~/work/api")
    assert res["ok"] and res["launch"]["status"] == "starting" and res["launch"]["name"] == "api"
    st = _wait(res["launch"]["id"])
    assert st["status"] == "ready" and st["stage"] == "ready"
    rec = registry.get(st["instance_id"])
    assert Path(rec["cwd"]).resolve() == (work / "api").resolve() and rec["headless"] is True
    assert fs_api.recent_dirs()[0]["name"] == "api"
    # opening it again finds the running one instead of a second copy…
    again = launcher.open_project(str(work / "api"))
    assert again["reused"] and again["instance_id"] == st["instance_id"]
    # …unless a second session is asked for
    second = launcher.open_project(str(work / "api"), reuse=False)
    assert _wait(second["launch"]["id"])["status"] == "ready"
    assert len([r for r in registry.list_instances() if Path(r["cwd"]).name == "api"]) == 2


def test_stop_ends_a_web_opened_jarvis(isolated, fake_child):
    _tree(isolated)
    st = _wait(launcher.open_project("~/work/web")["launch"]["id"])
    res = launcher.stop_project(st["instance_id"])
    assert res["ok"] and not res.get("forced")
    assert registry.get(st["instance_id"]) is None
    assert launcher.stop_project(st["instance_id"]) == {"ok": True, "already": True}


def test_a_terminal_jarvis_is_never_stopped_from_the_web(isolated):
    reg = registry.Registration(instance_id="term", port=1, token="t", info=lambda: {"project": "cli", "headless": False})
    reg.start()
    try:
        res = launcher.stop_project("term")
        assert not res["ok"] and res["code"] == "terminal" and "terminal" in res["error"]
        assert registry.get("term") is not None
    finally:
        reg.stop()


def test_a_jarvis_that_crashes_while_starting_reports_why(isolated, fake_child, monkeypatch):
    _tree(isolated)
    monkeypatch.setenv("FAKE_MODE", "crash")
    st = _wait(launcher.open_project("~/work/notes")["launch"]["id"])
    assert st["status"] == "failed" and st["code"] == "exited"
    assert "no provider configured" in st["error"] and "RuntimeError" in st["log"]


def test_a_jarvis_that_never_gets_ready_is_stopped(isolated, fake_child, monkeypatch):
    _tree(isolated)
    monkeypatch.setenv("FAKE_MODE", "hang")
    monkeypatch.setattr(launcher, "LAUNCH_TIMEOUT", 1.0)
    st = _wait(launcher.open_project("~/work/notes")["launch"]["id"])
    assert st["status"] == "failed" and st["code"] == "timeout"


def test_open_refuses_bad_folders_and_too_many_projects(isolated, monkeypatch):
    _tree(isolated)
    assert launcher.open_project("~/missing")["code"] == "not_found"
    assert launcher.open_project("~/work/README.md")["code"] == "not_a_folder"
    monkeypatch.setattr(launcher, "MAX_RUNNING", 0)
    res = launcher.open_project("~/work/api")
    assert not res["ok"] and res["code"] == "too_many"


def test_the_child_does_not_inherit_the_terminals_tunnel(isolated, monkeypatch, tmp_path):
    seen = {}

    class FakePopen:
        pid = 999999

        def __init__(self, cmd, **kw):
            seen.update(kw)

        def poll(self):
            return 0

    _tree(isolated)
    monkeypatch.setenv("HARNESS_WEB_TUNNEL", "1")
    monkeypatch.setattr(launcher.subprocess, "Popen", FakePopen)
    launcher.open_project("~/work/api")
    env = seen["env"]
    assert "HARNESS_WEB_TUNNEL" not in env and env["PWD"].endswith("api")
    assert seen["start_new_session"] is True and seen["stdin"] is launcher.subprocess.DEVNULL


# ─── Over HTTP ───────────────────────────────────────────────────────────


class _App:
    _busy = False
    _web_primary_url = ""


@pytest.fixture
def server(isolated):
    bridge = WebBridge()
    app = _App()
    srv, _urls, port = start_web_server(bridge=bridge, app=app, port=29310, host="127.0.0.1", instance_id="host1")
    yield bridge, port, app
    stop_web_server(srv, bridge)


def _call(port, token, path, payload=None):
    url = f"http://127.0.0.1:{port}{path}{'&' if '?' in path else '?'}token={token}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_folder_endpoints(server, isolated):
    bridge, port, _app = server
    _tree(isolated)
    status, data = _call(port, bridge.token, "/api/fs/dirs?path=~/work")
    assert status == 200 and [d["name"] for d in data["dirs"]] == ["api", "notes", "web"]
    status, data = _call(port, bridge.token, "/api/fs/dirs?path=~/nope")
    assert status == 200 and data == {"ok": False, "code": "not_found", "error": data["error"]}
    status, data = _call(port, bridge.token, "/api/fs/start")
    assert data["ok"] and data["shortcuts"][0]["name"] == "Home"
    assert _call(port, "wrong", "/api/fs/dirs")[0] == 401


def test_open_and_launch_status_endpoints(server, isolated, fake_child):
    bridge, port, _app = server
    _tree(isolated)
    _s, res = _call(port, bridge.token, "/api/projects/open", {"path": "~/work/web"})
    assert res["ok"]
    lid = res["launch"]["id"]
    deadline = time.time() + 20
    while time.time() < deadline:
        _s, st = _call(port, bridge.token, f"/api/projects/launch?id={lid}")
        if st["launch"]["status"] != "starting":
            break
        time.sleep(0.1)
    assert st["launch"]["status"] == "ready"
    _s, projects = _call(port, bridge.token, "/api/projects")
    row = next(p for p in projects["projects"] if p["id"] == st["launch"]["instance_id"])
    assert row["headless"] is True and row["project"] == "web"
    assert _call(port, bridge.token, "/api/projects/launch?id=nope")[0] == 404
    _s, stopped = _call(port, bridge.token, "/api/projects/stop", {"id": row["id"]})
    assert stopped["ok"]


def test_move_endpoint_refuses_while_busy(server, isolated, monkeypatch):
    bridge, port, app = server
    work = _tree(isolated)
    monkeypatch.chdir(work / "web")
    app._busy = True
    _s, res = _call(port, bridge.token, "/api/cwd", {"path": "~/work/api"})
    assert not res["ok"] and res["code"] == "busy"
    app._busy = False
    _s, res = _call(port, bridge.token, "/api/cwd", {"path": "~/work/api"})
    assert res["ok"] and Path(os.getcwd()) == (work / "api").resolve()
    _s, snap = _call(port, bridge.token, "/api/state")
    assert snap["cwd_display"] == "~/work/api" and snap["project"] == "api" and snap["headless"] is False


def test_state_carries_the_folder_and_headless_flag(monkeypatch, isolated):
    from jarvis.web.state_api import state_fields

    monkeypatch.chdir(isolated)
    monkeypatch.setattr(state, "headless", True)
    fields = state_fields()
    assert fields["cwd"] == str(isolated.resolve()) or fields["cwd"] == os.getcwd()
    assert fields["cwd_display"] == "~" and fields["headless"] is True


def test_headless_never_sends_desktop_notifications(monkeypatch):
    from jarvis.utils import notify

    monkeypatch.setattr(state, "headless", True)
    assert notify.desktop_notify("t", "m") is False


def test_cli_headless_turns_the_web_remote_on_without_a_tunnel(monkeypatch):
    from jarvis import cli

    calls = []
    monkeypatch.setattr(sys, "argv", ["jarvis", "--headless"])
    monkeypatch.setenv("HARNESS_WEB_TUNNEL", "1")
    monkeypatch.setattr("jarvis.bootstrap.ensure_harness_agent_defaults", lambda: None)
    monkeypatch.setattr("jarvis.tui.app.run", lambda: calls.append("run"))
    for name in ("headless", "web_enabled", "web_tunnel"):
        monkeypatch.setattr(state, name, state.__dict__[name])
    cli.main()
    assert calls == ["run"] and state.headless and state.web_enabled and not state.web_tunnel
