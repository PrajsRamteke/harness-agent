"""Web remote: the file-change endpoints, live ``change`` events and the snapshot."""
from __future__ import annotations

import json
import queue
import urllib.error
import urllib.request

import pytest

from jarvis import file_changes as fc
from jarvis import state
from jarvis.web import state_api
from jarvis.web.bridge import WebBridge
from jarvis.web.console_mux import WebMuxConsole
from jarvis.web.server import start_web_server, stop_web_server


@pytest.fixture
def remote(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(fc, "CWD", root)
    monkeypatch.chdir(root)
    monkeypatch.setattr(state, "messages", [])
    monkeypatch.setattr(state, "current_session_id", 7)
    fc.reset()
    bridge = WebBridge()
    server, _urls, port = start_web_server(bridge=bridge, app=None, port=28931, host="127.0.0.1")

    def get(path, token=True):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
        if token:
            req.add_header("Authorization", f"Bearer {bridge.token}")
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read())

    yield root, bridge, get
    stop_web_server(server, bridge)
    fc.reset()


def drain(sub):
    out = []
    while True:
        try:
            out.append(json.loads(sub.get_nowait()))
        except queue.Empty:
            return out


def test_change_events_carry_the_list_and_the_fresh_diff(remote):
    root, bridge, _get = remote
    sub = bridge.subscribe()
    f = root / "app.py"
    f.write_text("a\nb\n")
    fc.record(f, "a\n", "a\nb\n", "edit")

    (evt,) = [e for e in drain(sub) if e["type"] == "change"]
    data = evt["data"]
    assert data["changes"]["totals"]["files"] == 1
    assert data["focus"] == data["detail"]["id"] == fc.file_id(f)
    assert [r[0] for r in data["detail"]["hunks"][0]["rows"]] == [" ", "+"]


def test_no_diff_is_built_when_nobody_is_watching(remote):
    root, _bridge, _get = remote
    seen = []
    orig = fc.change_event
    fc.change_event = lambda fid: seen.append(fid) or orig(fid)
    try:
        f = root / "a.txt"
        f.write_text("1\n")
        fc.record(f, "0\n", "1\n", "edit")
    finally:
        fc.change_event = orig
    assert seen == []           # no subscribers → no work


def test_endpoints_serve_list_diff_and_patch(remote):
    root, _bridge, get = remote
    f = root / "x.py"
    f.write_text("one\ntwo\n")
    fc.record(f, "one\n", "one\ntwo\n", "edit")

    listing = get("/api/changes")
    (row,) = listing["files"]
    assert row["path"] == "x.py" and (row["added"], row["removed"]) == (1, 0)

    detail = get(f"/api/changes/file?id={row['id']}")
    assert detail["hunks"][0]["rows"][-1] == ["+", None, 2, "two"]

    assert "+two" in get("/api/changes/patch")["patch"]
    assert "+two" in get(f"/api/changes/patch?id={row['id']}")["patch"]


def test_unknown_file_id_is_a_404_and_the_token_is_required(remote):
    _root, _bridge, get = remote
    with pytest.raises(urllib.error.HTTPError) as err:
        get("/api/changes/file?id=nope")
    assert err.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as err:
        get("/api/changes", token=False)
    assert err.value.code == 401


def test_only_tracked_files_can_be_read(remote):
    """The endpoint takes an id from the ledger, never a path."""
    root, _bridge, get = remote
    (root / "secret.txt").write_text("hunter2\n")
    with pytest.raises(urllib.error.HTTPError) as err:
        get("/api/changes/file?id=secret.txt")
    assert err.value.code == 404


def test_snapshot_includes_changes_and_jobs(remote):
    root, _bridge, _get = remote
    f = root / "a.txt"
    f.write_text("1\n")
    fc.record(f, None, "1\n", "create")
    snap = state_api.snapshot_from_state()
    assert snap["changes"]["totals"]["files"] == 1
    assert snap["jobs"] == []


def test_inline_diff_event_links_to_the_panel_row():
    bridge = WebBridge()
    sub = bridge.subscribe()

    class _P:
        def file_diff(self, *a, **k):
            pass

    WebMuxConsole(_P(), bridge).file_diff("/tmp/some/notes.txt", "a\n", "b\n", "edit")
    (evt,) = drain(sub)
    assert evt["data"]["id"] == fc.file_id("/tmp/some/notes.txt")


def test_running_jobs_are_stable_state(monkeypatch):
    """A job list that hasn't changed must compare equal tick after tick."""
    import subprocess
    import sys

    from jarvis.tools import background

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        job = background.Job(id=99, cmd="sleep   5", proc=proc, log=background.LOG_DIR / "x.log")
        monkeypatch.setitem(background._jobs, 99, job)
        first = state_api.jobs_fields()
        assert first[-1] == state_api.jobs_fields()[-1]
        assert first[-1]["status"] == "running" and first[-1]["cmd"] == "sleep 5"
        assert first[-1]["secs"] is None
    finally:
        proc.kill()
        proc.wait()
