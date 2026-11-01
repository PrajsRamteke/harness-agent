"""Web terminal (jarvis/web/terminal_api.py): real PTY shells, and who may open one."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from jarvis.web import terminal_api


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
