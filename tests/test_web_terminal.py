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
