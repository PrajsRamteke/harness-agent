"""Web terminal (jarvis/web/terminal_api.py): real PTY shells, and who may open one."""
from __future__ import annotations

import email.message
import os
import time
from types import SimpleNamespace

import pytest

from jarvis.web import terminal_api
from jarvis.web.bridge import WebBridge
from jarvis.web.handler import WebHandler


@pytest.fixture(autouse=True)
def _clean():
    yield
    terminal_api.close_all()


def _read_until(session, needle: str, timeout: float = 8.0) -> str:
    cursor, out = 0, ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        res = session.read(cursor, 0.5)
        out += res["data"]
        cursor = res["cursor"]
        if needle in out:
            return out
    return out


def test_shell_echoes_and_a_big_paste_arrives_whole(monkeypatch, tmp_path):
    monkeypatch.setenv("SHELL", "/bin/sh")
    res = terminal_api.open_terminal(cols=80, rows=24, cwd=str(tmp_path))
    assert res["ok"]
    s = terminal_api.get_terminal(res["terminal"]["id"])
    assert s.write("echo hel''lo-term\n")
    assert "hello-term" in _read_until(s, "hello-term")
    # Bigger than a PTY's input buffer: write() must loop until all of it went in.
    big = "x" * 20000
    assert s.write(f"printf '%s' '{big}' | wc -c\n")
    assert "20000" in _read_until(s, "20000")


def test_close_ends_the_shell_and_never_double_closes_the_fd(monkeypatch, tmp_path):
    monkeypatch.setenv("SHELL", "/bin/sh")
    tid = terminal_api.open_terminal(cwd=str(tmp_path))["terminal"]["id"]
    s = terminal_api.get_terminal(tid)
    assert terminal_api.close_terminal(tid)
    s._reader.join(timeout=3)
    assert not s._reader.is_alive()
    assert s.exited and s._master == -1
    assert terminal_api.get_terminal(tid) is None
    assert s.write("echo nope\n") is False
    # A fresh fd may reuse the old number: it must stay open.
    r, w = os.pipe()
    try:
        os.write(w, b"ok")
        assert os.read(r, 2) == b"ok"
    finally:
        os.close(r)
        os.close(w)


def test_live_count_leaves_out_closed_shells(monkeypatch, tmp_path):
    monkeypatch.setenv("SHELL", "/bin/sh")
    assert terminal_api.live_count() == 0
    first = terminal_api.open_terminal(cwd=str(tmp_path))["terminal"]["id"]
    terminal_api.open_terminal(cwd=str(tmp_path))
    assert terminal_api.live_count() == 2
    terminal_api.close_terminal(first)
    assert terminal_api.live_count() == 1


def test_ctrl_c_interrupts_and_a_resize_reaches_the_program(monkeypatch, tmp_path):
    """The PTY is the shell's controlling terminal: Ctrl+C sends SIGINT to the
    running command and a resize sends SIGWINCH (Claude Code redraws for the
    new width). Without it, `sleep` ignored Ctrl+C and every resize was lost.
    zsh when there is one: bash claims the terminal by itself, zsh doesn't."""
    shell = "/bin/zsh" if os.access("/bin/zsh", os.X_OK) else "/bin/sh"
    monkeypatch.setenv("SHELL", shell)
    monkeypatch.setenv("PS1", "$ ")
    (tmp_path / ".zshrc").write_text("PS1='$ '\n")
    monkeypatch.setenv("ZDOTDIR", str(tmp_path))
    s = terminal_api.get_terminal(terminal_api.open_terminal(cwd=str(tmp_path))["terminal"]["id"])
    _read_until(s, "$ ")
    # A foreground program gets SIGWINCH when the page resizes.
    s.write("sh -c 'trap \"echo got-''winch; exit\" WINCH; while :; do sleep 0.1; done'\n")
    time.sleep(0.6)
    s.resize(101, 33)
    assert "got-winch" in _read_until(s, "got-winch", timeout=5.0)
    s.write("stty size\n")
    assert "33 101" in _read_until(s, "33 101", timeout=5.0)
    # Ctrl+C stops a running command.
    s.write("sleep 30\n")
    time.sleep(0.6)
    t0 = time.monotonic()
    s.write("\x03")
    s.write("echo back-''again\n")
    assert "back-again" in _read_until(s, "back-again", timeout=5.0)
    assert time.monotonic() - t0 < 5.0


def _idle_shell(monkeypatch, tmp_path):
    """A /bin/sh that has printed its prompt and waits: output appended by the
    test is all that changes after this."""
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("PS1", "$ ")
    tid = terminal_api.open_terminal(cwd=str(tmp_path))["terminal"]["id"]
    s = terminal_api.get_terminal(tid)
    _read_until(s, "$ ")
    time.sleep(0.2)
    return s


def test_a_late_page_gets_a_full_screen_program_repainted_not_its_diffs(monkeypatch, tmp_path):
    """Claude Code paints the alternate screen once, then only changed cells.
    A page attaching after that first paint was trimmed must not draw the
    diffs onto a blank screen: it gets the program's modes, a clear screen,
    and the program is asked (Ctrl+L) to paint everything again."""
    s = _idle_shell(monkeypatch, tmp_path)
    monkeypatch.setattr(terminal_api, "MAX_BUFFER", 4000)
    typed: list[str] = []
    monkeypatch.setattr(s, "write", lambda data: typed.append(data) or True)
    s._append("\x1b[?2004h\x1b[?1049h\x1b[2J\x1b[H\x1b[?1000h\x1b[?1006h\x1b[?25lFIRST PAINT\r\n")
    for i in range(400):
        s._append(f"\x1b[{i % 20 + 1};5Hdiff {i:04d}\r\n")
    assert s.base > 0
    res = s.read(0, 0.0)
    assert res["reset"] is True
    data = res["data"]
    assert data.startswith("\x1b[?1049h")
    for seq in ("\x1b[?1000h", "\x1b[?1006h", "\x1b[?2004h", "\x1b[?25l"):
        assert seq in data
    assert data.endswith("\x1b[H\x1b[2J")
    assert "diff " not in data                    # stale cell updates skipped
    assert res["cursor"] == s.end
    assert typed == ["\x0c"]                     # the program repaints itself
    # A page that kept up reads on normally — no repaint asked for.
    s._append("\x1b[3;5Hdiff live")
    res2 = s.read(res["cursor"], 0.0)
    assert res2["reset"] is False and res2["data"] == "\x1b[3;5Hdiff live"
    assert typed == ["\x0c"]


def test_a_late_page_replays_plain_output_from_a_line_with_its_modes(monkeypatch, tmp_path):
    s = _idle_shell(monkeypatch, tmp_path)
    monkeypatch.setattr(terminal_api, "MAX_BUFFER", 3000)
    typed: list[str] = []
    monkeypatch.setattr(s, "write", lambda data: typed.append(data) or True)
    s._append("\x1b[?2004h")                     # bracketed paste on, then trimmed away
    for i in range(300):
        s._append(f"line {i:04d}\r\n")
    res = s.read(0, 0.0)
    assert res["reset"] is True
    assert res["data"].startswith("\x1b[?2004hline ")   # modes, then a whole line
    assert res["data"].endswith("line 0299\r\n")
    assert typed == []                             # nothing to repaint
    # A program that left the alternate screen is plain output again.
    s._append("\x1b[?1049hvim\x1b[?1049l")
    for i in range(300):
        s._append(f"after {i:04d}\r\n")
    res = s.read(0, 0.0)
    assert not res["data"].startswith("\x1b[?1049h")
    assert typed == []


def test_mode_tracking_survives_sequences_split_across_reads():
    m = terminal_api._Modes()
    m.feed("text \x1b[?10", 0)
    m.feed("49h\x1b[?100", 10)
    m.feed("6;1000h", 19)
    assert m.alt and m.alt_since == 5
    assert m.modes[1006] and m.modes[1000]
    m.feed("\x1bc", 26)                           # full reset
    assert not m.alt and m.preamble() == ""


def test_big_output_comes_in_bounded_reads(monkeypatch, tmp_path):
    s = _idle_shell(monkeypatch, tmp_path)
    start = s.end
    s._append("x" * 50_000)
    first = s.read(start, 0.0, limit=20_000)
    assert len(first["data"]) == 20_000 and first["more"] is True
    rest = ""
    cursor = first["cursor"]
    while True:
        r = s.read(cursor, 0.0, limit=20_000)
        rest += r["data"]
        cursor = r["cursor"]
        if not r["more"]:
            break
    assert len(first["data"]) + len(rest) == 50_000 and cursor == s.end


def test_one_request_waits_on_every_shell(monkeypatch, tmp_path):
    a = _idle_shell(monkeypatch, tmp_path)
    b = _idle_shell(monkeypatch, tmp_path)
    cursors = {a.id: a.end, b.id: b.end, "nope": 0}
    res = terminal_api.read_many(cursors, 0.0)
    assert res["terminals"] == {"nope": {"ok": False, "code": "gone", "id": "nope"}}
    del cursors["nope"]
    t0 = time.monotonic()
    assert terminal_api.read_many(cursors, 0.3)["terminals"] == {}
    assert time.monotonic() - t0 >= 0.25
    import threading
    threading.Timer(0.2, lambda: b._append("hello b")).start()
    t0 = time.monotonic()
    res = terminal_api.read_many(cursors, 5.0)
    assert time.monotonic() - t0 < 2.0
    assert list(res["terminals"]) == [b.id]
    assert res["terminals"][b.id]["data"] == "hello b"


def test_hub_marks_tunnel_visitors_so_the_other_instance_refuses_a_shell(monkeypatch):
    """A tunnel visitor reaching another project through /p/<id>/ must not look
    like this computer to that instance (it sees 127.0.0.1)."""
    from jarvis.web import hub

    sent = {}

    class FakeConn:
        def __init__(self, *a, **k): pass
        def putrequest(self, method, target, **k): sent["target"] = target
        def putheader(self, name, value): sent.setdefault("headers", {})[name] = value
        def endheaders(self): raise ConnectionRefusedError

        def close(self): pass

    monkeypatch.setattr(hub.http.client, "HTTPConnection", FakeConn)

    def handler(direct: bool):
        h = SimpleNamespace(
            headers={}, command="GET", client_address=("127.0.0.1", 5000),
            _may_hand_out_token=lambda: direct,
            send_response=lambda *a: None, send_header=lambda *a: None, end_headers=lambda: None,
            wfile=SimpleNamespace(write=lambda b: None),
        )
        return h

    record = {"port": 1, "token": "t"}
    hub.forward(handler(False), record, "/api/terminal/list", json_error=lambda *a: None)
    assert "X-Forwarded-For" in sent["headers"]
    sent.clear()
    hub.forward(handler(True), record, "/api/terminal/list", json_error=lambda *a: None)
    assert "X-Forwarded-For" not in sent.get("headers", {})


# ─── Who may open a shell (the QR dialog's "Terminal on the link" switch) ──

TUNNEL_HEADERS = {"Host": "ab.ngrok-free.app", "X-Forwarded-For": "8.8.8.8"}
LAN_HEADERS = {"Host": "192.168.0.105:8765"}


def _handler(headers: dict[str, str], client_ip: str = "127.0.0.1", *, shell: bool = False):
    h = WebHandler.__new__(WebHandler)
    h.bridge = WebBridge()
    h.bridge.terminal_over_tunnel = shell
    h.app = None
    msg = email.message.Message()
    for key, value in headers.items():
        msg[key] = value
    h.headers = msg
    h.client_address = (client_ip, 50000)
    return h


def test_tunnel_visitor_gets_a_shell_only_while_the_switch_is_on():
    off = _handler(TUNNEL_HEADERS)
    assert off._terminal_allowed() is False
    assert off._terminal_tunnel_state() == {
        "ok": True, "allowed": False, "on": False, "can_toggle": False,
    }
    on = _handler(TUNNEL_HEADERS, shell=True)
    assert on._terminal_allowed() is True
    assert on._terminal_tunnel_state()["on"] is True


def test_your_own_network_always_gets_a_shell():
    h = _handler(LAN_HEADERS, client_ip="192.168.0.20")
    assert h._terminal_allowed() is True
    assert h._terminal_tunnel_state()["can_toggle"] is True


def test_a_tunnel_page_cannot_hand_itself_a_shell():
    h = _handler(TUNNEL_HEADERS)
    res = h._terminal_tunnel_request(True)
    assert res["ok"] is False and res["code"] == "remote"
    assert h.bridge.terminal_over_tunnel is False


def test_the_switch_flips_from_this_computer_or_the_lan():
    h = _handler(LAN_HEADERS, client_ip="192.168.0.20")
    assert h._terminal_tunnel_request(True) == {
        "ok": True, "allowed": True, "on": True, "can_toggle": True,
    }
    assert h.bridge.terminal_over_tunnel is True
    assert h._terminal_tunnel_request(False)["on"] is False


def test_closing_the_anywhere_link_turns_the_switch_off():
    from jarvis.tui.mixins.web_remote import WebRemoteMixin

    fake = SimpleNamespace(_web_bridge=WebBridge(), _web_tunnel=None, _web_public_link="x",
                           _render_footer=lambda: None)
    fake._web_bridge.terminal_over_tunnel = True
    WebRemoteMixin._stop_tunnel(fake, quiet=True)
    assert fake._web_bridge.terminal_over_tunnel is False


# ─── Shells across workspace projects: two real servers behind one link ─────

def _post(url: str, body: dict, headers: dict | None = None):
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _getj(url: str, headers: dict | None = None):
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    """Two real web servers (proja = the page's host, projb = a workspace project the
    page reaches through ``/p/projb/``) and a workspace that names both folders.

    Both servers live in this process, so they share ``terminal_api``'s module-level
    shell table: this proves the routing, tokens, grouping and tunnel policy — not
    process isolation (that is the same code path as any two ``jarvis --web``)."""
    from test_web_hub import _Remote

    from jarvis.storage import workspace as ws
    from jarvis.web import registry

    monkeypatch.setattr(registry, "INSTANCES_DIR", tmp_path / "instances")
    monkeypatch.setattr(ws, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(ws, "WORKSPACE_FILE", tmp_path / "cfg" / "workspace.json")
    monkeypatch.setattr(ws, "LOCK_FILE", tmp_path / "cfg" / "workspace.lock")
    monkeypatch.setenv("SHELL", "/bin/sh")
    dirs = {"proja": tmp_path / "a", "projb": tmp_path / "b"}
    for d in dirs.values():
        d.mkdir()
    pa = ws.add(dirs["proja"], name="Alpha")
    pb = ws.add(dirs["projb"], name="Beta")

    real_list = registry.list_instances
    monkeypatch.setattr(registry, "list_instances", lambda **kw: [
        {**rec, "cwd": str(dirs[rec["id"]])} for rec in real_list(**kw) if rec["id"] in dirs])
    a, b = _Remote("proja", 29310), _Remote("projb", 29330)
    yield a, b, pa, pb
    a.stop()
    b.stop()


def test_a_shell_opened_through_another_project_is_listed_under_it(two_projects):
    a, b, pa, pb = two_projects
    # open through the link of A, aimed at B: the hub swaps in B's own token
    status, res = _post(a.url("/p/projb/api/terminal/open"), {"cols": 80, "rows": 24})
    assert status == 200 and res["ok"], res
    tid = res["terminal"]["id"]
    # typing and reading go the same way, one held poll for the instance
    assert _post(a.url("/p/projb/api/terminal/input"), {"id": tid, "data": "echo cross-''project\n"})[1]["ok"]
    deadline, out, cursor = time.monotonic() + 8, "", 0
    while "cross-project" not in out and time.monotonic() < deadline:
        _s, got = _getj(a.url(f"/p/projb/api/terminal/poll?ids={tid}:{cursor}&wait=500"))
        piece = got["terminals"].get(tid, {})
        out += piece.get("data", "")
        cursor = piece.get("cursor", cursor)
    assert "cross-project" in out
    # B's own token is the one that worked: it is not A's, and A's does not open B directly
    assert _post(b.url("/api/terminal/open", token=a.token), {})[0] == 401

    # the workspace view groups every shell by project, with each instance's policy
    status, view = _getj(a.url("/api/workspace/terminals"))
    assert status == 200 and view["ok"]
    groups = {g["instance_id"]: g for g in view["groups"]}
    assert set(groups) == {"proja", "projb"}
    gb = groups["projb"]
    assert (gb["project_id"], gb["name"], gb["self"], gb["allowed"]) == (pb["id"], "Beta", False, True)
    assert gb["color"] == pb["color"] and tid in {t["id"] for t in gb["terminals"]}
    assert groups["proja"]["self"] is True and groups["proja"]["project_id"] == pa["id"]


def test_a_tunnel_visitor_gets_no_shell_in_any_project(two_projects):
    a, b, _pa, _pb = two_projects
    tunnel = {"X-Forwarded-For": "8.8.8.8", "Host": "demo.trycloudflare.com"}
    # through the link, the other project refuses it too (the hub forwards the marker)
    status, res = _post(a.url("/p/projb/api/terminal/open"), {}, headers=tunnel)
    assert status == 403 and res["code"] == "local_only"
    # …and the all-terminals view says so per project instead of listing shells
    status, view = _getj(a.url("/api/workspace/terminals"), headers=tunnel)
    assert status == 200
    assert view["groups"] and all(g["allowed"] is False for g in view["groups"])
