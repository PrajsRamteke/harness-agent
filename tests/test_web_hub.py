"""One link for every running Jarvis: registry, project list, forwarding."""
from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jarvis.web import handler as web_handler
from jarvis.web import hub, registry
from jarvis.web.bridge import WebBridge
from jarvis.web.server import start_web_server, stop_web_server

STATIC = Path(web_handler.__file__).with_name("static")


@pytest.fixture(autouse=True)
def instances_dir(tmp_path, monkeypatch):
    path = tmp_path / "instances"
    monkeypatch.setattr(registry, "INSTANCES_DIR", path)
    return path


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# ─── Registry ────────────────────────────────────────────────────────────


def test_registration_is_listed_private_and_removed(instances_dir):
    reg = registry.Registration(
        instance_id="a1", port=8765, token="tok", info=lambda: {"project": "billing", "busy": True},
    )
    reg.start()
    try:
        rows = registry.list_instances()
        assert [r["id"] for r in rows] == ["a1"]
        assert rows[0]["project"] == "billing" and rows[0]["busy"] is True
        assert rows[0]["token"] == "tok"
        assert (instances_dir / "a1.json").stat().st_mode & 0o777 == 0o600
        assert instances_dir.stat().st_mode & 0o777 == 0o700
    finally:
        reg.stop()
    assert registry.list_instances() == []


def test_a_failing_info_callback_still_lists_the_instance():
    def boom():
        raise RuntimeError("no state yet")

    reg = registry.Registration(instance_id="a2", port=1, token="t", info=boom)
    reg.start()
    try:
        assert [r["id"] for r in registry.list_instances()] == ["a2"]
    finally:
        reg.stop()


def test_dead_and_stale_entries_are_ignored_and_dead_ones_removed(instances_dir):
    instances_dir.mkdir(parents=True)
    now = time.time()

    def write(name, **fields):
        rec = {"id": name, "pid": os.getpid(), "port": 1, "token": "t", "started": now, "updated": now, **fields}
        (instances_dir / f"{name}.json").write_text(json.dumps(rec))

    write("live")
    write("dead", pid=_dead_pid())
    write("stale", updated=now - registry.STALE_SECS - 5)
    (instances_dir / "junk.json").write_text("{not json")
    (instances_dir / "bad id.json").write_text(json.dumps({"id": "../x", "pid": os.getpid(), "updated": now}))

    assert [r["id"] for r in registry.list_instances()] == ["live"]
    assert not (instances_dir / "dead.json").exists()
    assert registry.get("live") is not None
    assert registry.get("dead") is None
    assert registry.get("../etc") is None


def test_changes_are_written_at_once_and_unchanged_state_only_on_heartbeat(instances_dir, monkeypatch):
    info = {"busy": False}
    reg = registry.Registration(instance_id="t1", port=1, token="t", info=lambda: dict(info))
    reg.start()
    try:
        assert reg.tick() is False            # nothing changed, heartbeat not due
        info["busy"] = True
        assert reg.tick() is True             # a change lands on the next tick (≤ 1 s)
        assert registry.get("t1")["busy"] is True
        monkeypatch.setattr(registry, "HEARTBEAT_SECS", 0.0)
        assert reg.tick() is True             # heartbeat due: rewritten unchanged
    finally:
        reg.stop()


def test_instances_are_listed_oldest_first(instances_dir):
    instances_dir.mkdir(parents=True)
    now = time.time()
    for name, started in (("b", now - 5), ("a", now - 10), ("c", now)):
        (instances_dir / f"{name}.json").write_text(json.dumps(
            {"id": name, "pid": os.getpid(), "port": 1, "token": "t", "started": started, "updated": now}))
    assert [r["id"] for r in registry.list_instances()] == ["a", "b", "c"]


def test_split_project_path():
    assert hub.split_project_path("/p/abc/api/state") == ("abc", "/api/state")
    assert hub.split_project_path("/p/abc") == ("abc", "")
    assert hub.split_project_path("/p/abc/") == ("abc", "/")
    assert hub.split_project_path("/api/state") is None
    assert hub.split_project_path("/p/../x") is None
    assert hub.split_project_path("/p/a b/x") is None


# ─── Two real servers: the first one reaches the second ──────────────────


class _App:
    _busy = False

    def __init__(self, url: str) -> None:
        self._web_primary_url = url


class _Remote:
    def __init__(self, instance_id: str, port: int) -> None:
        self.bridge = WebBridge()
        self.submitted: list[str] = []
        self.bridge.set_handlers(on_submit=lambda text, files=None: self.submitted.append(text))
        self.server, _urls, self.port = start_web_server(
            bridge=self.bridge, app=_App(f"http://{instance_id}.test/"), port=port,
            host="127.0.0.1", instance_id=instance_id,
        )
        self.id = instance_id
        self.token = self.bridge.token

    def url(self, path: str, token: str | None = None) -> str:
        sep = "&" if "?" in path else "?"
        return f"http://127.0.0.1:{self.port}{path}{sep}token={token or self.token}"

    def stop(self) -> None:
        stop_web_server(self.server, self.bridge)


@pytest.fixture
def pair():
    a, b = _Remote("proja", 29210), _Remote("projb", 29230)
    yield a, b
    a.stop()
    b.stop()


def _get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def test_project_list_marks_this_one(pair):
    a, b = pair
    status, body = _get(a.url("/api/projects"))
    assert status == 200
    data = json.loads(body)
    assert data["self"] == "proja"
    assert data["link"] == "http://proja.test/"
    assert [(p["id"], p["self"]) for p in data["projects"]] == [("proja", True), ("projb", False)]
    assert all("port" not in p and "token" not in p for p in data["projects"])
    assert _get(f"http://127.0.0.1:{a.port}/api/projects")[0] == 401


def test_local_pages_get_the_other_projects_own_links_for_failover(pair):
    a, b = pair
    data = json.loads(_get(a.url("/api/projects"))[1])
    rows = {p["id"]: p for p in data["projects"]}
    # this computer / the LAN may already be handed a token: it learns where B lives
    assert rows["projb"]["link"] == f"http://127.0.0.1:{b.port}/?token={b.token}"
    assert "link" not in rows["proja"]
    # the link really opens B
    assert _get(rows["projb"]["link"].replace("/?", "/api/state?"))[0] == 200


def test_tunnel_visits_never_see_another_projects_token(pair):
    a, b = pair
    for headers in ({"X-Forwarded-For": "1.2.3.4"}, {"Cf-Ray": "abc"}, {"Host": "demo.trycloudflare.com"}):
        status, body = _get(a.url("/api/projects"), headers=headers)
        assert status == 200
        assert b.token not in body.decode()
        assert all("link" not in p for p in json.loads(body)["projects"])


def test_project_rows_never_carry_secrets(pair):
    a, b = pair
    rows = hub.project_rows("proja")
    text = json.dumps(rows)
    assert a.token not in text and b.token not in text
    assert all(set(r) >= {"id", "project", "session_id", "busy", "needs_approval"} for r in rows)


def test_project_rows_say_how_long_a_reply_has_run(instances_dir):
    instances_dir.mkdir(parents=True)
    now = time.time()

    def write(name, **fields):
        rec = {"id": name, "pid": os.getpid(), "port": 1, "token": "t", "started": now, "updated": now, **fields}
        (instances_dir / f"{name}.json").write_text(json.dumps(rec))

    write("working", busy=True, busy_since=now - 75)
    write("idle", busy=False, busy_since=None)
    write("old", busy=True)  # an instance from before busy_since existed
    rows = {r["id"]: r for r in hub.project_rows(None)}
    assert 74 <= rows["working"]["busy_for"] <= 80
    assert rows["idle"]["busy_for"] is None
    assert rows["old"]["busy_for"] is None


def test_another_projects_api_is_reached_through_this_link(pair):
    a, b = pair
    own = json.loads(_get(a.url("/api/state"))[1])
    other = json.loads(_get(a.url("/p/projb/api/state"))[1])
    assert own["remote_url"] == "http://proja.test/"
    assert other["remote_url"] == "http://projb.test/"
    # this server's own id with the prefix is just this server
    assert json.loads(_get(a.url("/p/proja/api/state"))[1])["remote_url"] == "http://proja.test/"
    # the query string and the other side's status code come back unchanged
    status, body = _get(a.url("/p/projb/api/tool-output?id=zzz"))
    assert status == 404 and "no full output" in json.loads(body)["error"]


def test_forwarding_needs_this_links_token_and_hides_the_other_one(pair):
    a, b = pair
    assert _get(f"http://127.0.0.1:{a.port}/p/projb/api/state")[0] == 401
    assert _get(a.url("/p/projb/api/state", token="wrong"))[0] == 401
    # the other project's own token is not this link's token
    assert _get(a.url("/p/projb/api/state", token=b.token))[0] == 401
    # a token for A never works on B directly
    assert _get(b.url("/api/state", token=a.token))[0] == 401


def test_post_bodies_reach_the_other_project(pair):
    a, b = pair
    req = urllib.request.Request(
        a.url("/p/projb/api/prompt"), data=json.dumps({"text": "hello from a"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert json.loads(resp.read()) == {"ok": True}
    assert b.submitted == ["hello from a"] and a.submitted == []


def test_raw_upload_bodies_and_headers_pass_through(pair):
    a, b = pair
    payload = b"hello world\n" * 1000
    req = urllib.request.Request(
        a.url("/p/projb/api/upload"), data=payload,
        headers={"Content-Type": "text/plain", "X-File-Name": "notes.txt"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        out = json.loads(resp.read())
    assert out["ok"] is True
    # served back through the same door, with byte ranges
    uid = out["file"]["id"]
    req = urllib.request.Request(a.url(f"/p/projb/api/media/{uid}"), headers={"Range": "bytes=0-4"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 206 and resp.read() == b"hello"


def test_event_stream_flows_through_live(pair):
    a, b = pair
    conn = http.client.HTTPConnection("127.0.0.1", a.port, timeout=10)
    conn.request("GET", f"/p/projb/api/events?token={a.token}")
    resp = conn.getresponse()
    assert resp.status == 200 and "text/event-stream" in resp.getheader("Content-Type")
    first = resp.fp.readline().decode()
    assert first.startswith("data: ") and json.loads(first[6:])["type"] == "snapshot"
    resp.fp.readline()  # blank line ending the event
    b.bridge.emit("ping_test", {"n": 1})
    seen = None
    for _ in range(10):
        line = resp.fp.readline().decode()
        if line.startswith("data: ") and json.loads(line[6:])["type"] == "ping_test":
            seen = json.loads(line[6:])
            break
    conn.close()
    assert seen and seen["data"] == {"n": 1}


def test_unknown_project_and_paths_that_are_not_api(pair):
    a, b = pair
    status, body = _get(a.url("/p/ghost/api/state"))
    assert status == 404 and "no longer running" in json.loads(body)["error"]
    assert _get(a.url("/p/projb/not-api"))[0] == 404
    assert _get(a.url("/p/projb/api/../secrets"))[0] in (404, 400)


def test_project_page_is_served_by_this_link_and_ghosts_go_home(pair):
    a, b = pair
    status, body = _get(a.url("/p/projb/"))
    assert status == 200 and b'id="projects-sec"' in body
    opener = urllib.request.build_opener(_NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as exc:
        opener.open(f"http://127.0.0.1:{a.port}/p/ghost/?token={a.token}", timeout=10)
    assert exc.value.code == 302 and exc.value.headers["Location"] == f"/?token={a.token}"
    # a bare visit from the LAN gets this link's token, keeping the project prefix
    with pytest.raises(urllib.error.HTTPError) as exc:
        opener.open(f"http://127.0.0.1:{a.port}/p/projb/", timeout=10)
    assert exc.value.headers["Location"] == f"/p/projb/?token={a.token}"


def test_a_project_that_stops_leaves_the_list_and_forwarding_fails_cleanly(pair):
    a, b = pair
    b.stop()
    assert [p["id"] for p in json.loads(_get(a.url("/api/projects"))[1])["projects"]] == ["proja"]
    assert _get(a.url("/p/projb/api/state"))[0] == 404


def test_an_entry_whose_server_vanished_answers_bad_gateway(pair, instances_dir):
    a, b = pair
    # alive pid and fresh heartbeat, but nothing listens on the port
    rec = json.loads((instances_dir / "projb.json").read_text())
    b.stop()
    rec["id"], rec["port"], rec["updated"] = "projc", 9, time.time()
    (instances_dir / "projc.json").write_text(json.dumps(rec))
    status, body = _get(a.url("/p/projc/api/state"))
    assert status == 502 and "no longer running" in json.loads(body)["error"]


def test_state_watcher_pushes_project_list_changes():
    from jarvis.web.sync import StateWatcher

    bridge = WebBridge()
    sub = bridge.subscribe()
    rows = [{"id": "a", "busy": False}]
    watcher = StateWatcher(bridge, busy=lambda: False, projects=lambda: [dict(r) for r in rows])
    watcher.tick()                      # first look: the page fetched the list itself
    rows[0]["busy"] = True
    watcher.tick()
    watcher.tick()                      # unchanged: no repeat
    events = []
    while not sub.empty():
        events.append(json.loads(sub.get_nowait()))
    projects = [e for e in events if e["type"] == "projects"]
    assert len(projects) == 1 and projects[0]["data"]["projects"][0]["busy"] is True


def test_request_hostname_is_strict():
    class H(web_handler.WebHandler):
        def __init__(self, host):
            self.headers = {"Host": host}

    assert H("192.168.1.5:8765")._request_hostname() == "192.168.1.5"
    assert H("localhost")._request_hostname() == "localhost"
    assert H("[::1]:8765")._request_hostname() == "[::1]"
    assert H("evil.com/x?:1")._request_hostname() is None
    assert H("")._request_hostname() is None


# ─── The page ────────────────────────────────────────────────────────────


def test_sidebar_has_a_project_list_and_no_model_agent_provider_cards():
    html = (STATIC / "index.html").read_text()
    assert 'id="projects-sec"' in html and 'id="proj-banner"' in html
    for gone in ("model-card", "agent-card", 'id="providers-card"', "select-card"):
        assert gone not in html
    # the pickers stay reachable without the cards
    js = "\n".join(f.read_text() for f in (STATIC / "js").glob("*.js"))
    for kind in ("model", "agent"):
        assert f"openPicker('{kind}')" in js or f"onOpenPicker('{kind}')" in js
    assert "'/model'" in js and "'/agent'" in js


def test_every_request_to_the_project_goes_through_the_base_path():
    """A call that forgets BASE would silently talk to the wrong project."""
    # fetchProjects asks the server the page came from, on purpose; fetchStateAt
    # takes the project's base as a parameter (projects.js prefetches with it).
    allowed = {"fetchProjects", "fetchStateAt"}
    bad = []
    for f in (STATIC / "js").glob("*.js"):
        lines = f.read_text().splitlines()
        for i, line in enumerate(lines):
            if not re.search(r"fetch\(|new EventSource\(|xhr\.open\(", line):
                continue
            window = "\n".join(lines[max(0, i - 1): i + 2])
            if "/api/" in window and "BASE" not in window:
                if any(name in "\n".join(lines[max(0, i - 4): i + 1]) for name in allowed):
                    continue
                bad.append(f"{f.name}:{i + 1}: {line.strip()}")
    assert bad == []
