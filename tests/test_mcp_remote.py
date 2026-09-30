"""Hosted MCP servers: streamable HTTP / SSE, and the browser sign-in (OAuth).

The end-to-end tests start ``tests/mcp_demo_server.py`` (a real MCP server with
an in-memory OAuth provider) on a free port and drive Jarvis's registry against
it, with the "browser" played by an HTTP GET that follows the redirect back to
Jarvis's callback listener.
"""
import json
import pathlib
import socket
import subprocess
import sys
import time

import httpx
import pytest

from jarvis.mcp import auth as mcp_auth
from jarvis.mcp.auth import AuthCancelled, FileTokenStorage, coordinator
from jarvis.mcp.registry import AUTH_REQUIRED_MSG, mcp_registry, needs_auth

DEMO = pathlib.Path(__file__).with_name("mcp_demo_server.py")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(cond, secs=15.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def demo(ext_env, monkeypatch):
    """Start the demo server in a mode; yields ``start(mode) -> url``."""
    procs: list[subprocess.Popen] = []
    monkeypatch.setenv("HARNESS_MCP_OAUTH_PORT", str(free_port()))
    coordinator.stop_listener()

    def start(mode: str, ttl: int = 3600) -> str:
        port = free_port()
        p = subprocess.Popen(
            [sys.executable, str(DEMO), str(port), mode, str(ttl)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        procs.append(p)
        assert wait_for(lambda: _up(port), 20), "demo server did not start"
        return f"http://127.0.0.1:{port}"

    yield start
    for name in list(mcp_registry._servers) + list(mcp_registry._pending):
        mcp_registry.disconnect(name)
    coordinator.stop_listener()
    for p in procs:
        p.terminate()
        p.wait(timeout=10)


def _up(port: int) -> bool:
    try:
        httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5)
        return True
    except httpx.HTTPError:
        return False


def browser(url: str, follow: bool = True) -> httpx.Response:
    return httpx.get(url, follow_redirects=follow, timeout=10)


# ── transports ─────────────────────────────────────────────────────────────


def test_streamable_http_server_connects_and_answers(demo):
    base = demo("open")
    assert mcp_registry.connect("open1", {"type": "http", "url": f"{base}/mcp"}) is None
    assert [t.name for t in mcp_registry.get_server_tools("open1")] == ["add"]
    assert "5" in mcp_registry._call_mcp_tool("open1", "add", {"a": 2, "b": 3})
    assert mcp_registry.get_server_health("open1")["status"] == "live"
    mcp_registry.disconnect("open1")
    assert not mcp_registry.is_connected("open1")


def test_sse_declared_but_streamable_falls_back(demo):
    base = demo("open")
    assert mcp_registry.connect("open2", {"type": "sse", "url": f"{base}/mcp"}) is None
    assert mcp_registry._servers["open2"].transport == "http"


def test_http_declared_but_sse_only_falls_back(demo):
    base = demo("sse")
    assert mcp_registry.connect("old", {"type": "http", "url": f"{base}/sse"}) is None
    assert mcp_registry._servers["old"].transport == "sse"


def test_unreachable_server_gets_a_plain_message(ext_env):
    err = mcp_registry.connect("nowhere", {"type": "http", "url": f"http://127.0.0.1:{free_port()}/mcp"})
    assert err and "reach" in err
    assert mcp_registry.get_server_health("nowhere")["status"] == "failed"
    assert not mcp_registry._pending


def test_connect_events_reach_listeners(demo):
    base = demo("open")
    seen = []
    fn = lambda ev, name: seen.append((ev, name))  # noqa: E731
    mcp_registry.add_listener(fn)
    try:
        mcp_registry.connect("evt", {"type": "http", "url": f"{base}/mcp"})
        mcp_registry.disconnect("evt")
    finally:
        mcp_registry.remove_listener(fn)
    kinds = [e for e, n in seen if n == "evt"]
    assert "connecting" in kinds and "connected" in kinds and "disconnected" in kinds


# ── sign-in ────────────────────────────────────────────────────────────────


def test_oauth_flow_end_to_end(demo, ext_env):
    base = demo("oauth")
    cfg = {"type": "http", "url": f"{base}/mcp"}

    # Startup connect: never starts a browser sign-in — it just reports "sign in".
    with mcp_registry.startup_connect():
        err = mcp_registry.connect("hosted", cfg)
    assert needs_auth(err) and err == AUTH_REQUIRED_MSG
    assert not coordinator.is_pending("hosted")
    health = mcp_registry.get_server_health("hosted", cfg)
    assert health["status"] == "auth" and health["needs_auth"]

    # The Authenticate button: a link, ready straight away.
    res = mcp_registry.authenticate("hosted", cfg)
    assert res["ok"] and res["url"].startswith(f"{base}/authorize?")
    assert [p["name"] for p in coordinator.pending()] == ["hosted"]
    assert mcp_registry.get_server_health("hosted", cfg)["auth"]["url"] == res["url"]
    assert not mcp_registry.is_connected("hosted")
    assert "hosted" not in {n for n, _t, _e in mcp_registry.list_connected()}  # not "live" yet

    # The user approves in the browser → Jarvis's callback listener → connected.
    page = browser(res["url"])
    assert "Signed in to hosted" in page.text
    assert wait_for(lambda: mcp_registry.is_connected("hosted"))
    assert "7" in mcp_registry._call_mcp_tool("hosted", "add", {"a": 3, "b": 4})
    assert not coordinator.is_pending("hosted")
    saved = list((ext_env.home / ".config/harness-agent/mcp-auth").glob("*.json"))
    assert saved and all(oct(p.stat().st_mode)[-3:] == "600" for p in saved)

    # Restart: the saved login connects silently, no sign-in.
    mcp_registry.disconnect("hosted")
    with mcp_registry.startup_connect():
        assert mcp_registry.connect("hosted", cfg) is None
    mcp_registry.disconnect("hosted")

    # Expired access token: refreshed with the refresh token, still silent.
    f = saved[0]
    data = json.loads(f.read_text())
    data["expires_at"] = time.time() - 60
    f.write_text(json.dumps(data))
    with mcp_registry.startup_connect():
        assert mcp_registry.connect("hosted", cfg) is None
    mcp_registry.disconnect("hosted")

    # Sign out forgets the login.
    mcp_registry.sign_out("hosted", cfg)
    assert not list((ext_env.home / ".config/harness-agent/mcp-auth").glob("*.json"))
    with mcp_registry.startup_connect():
        assert needs_auth(mcp_registry.connect("hosted", cfg))


def test_sign_in_finished_on_another_device_by_pasting(demo):
    base = demo("oauth")
    cfg = {"type": "http", "url": f"{base}/mcp"}
    res = mcp_registry.authenticate("phone", cfg)
    location = browser(res["url"], follow=False).headers["location"]  # where the phone's browser ended up
    assert coordinator.submit("phone", "")["ok"] is False
    assert coordinator.submit("phone", "http://localhost/nothing")["ok"] is False
    assert coordinator.submit("phone", location)["ok"]
    assert wait_for(lambda: mcp_registry.is_connected("phone"))


def test_cancelling_a_sign_in_cleans_up(demo):
    base = demo("oauth")
    cfg = {"type": "http", "url": f"{base}/mcp"}
    res = mcp_registry.authenticate("later", cfg)
    assert res["ok"] and coordinator.is_pending("later")
    mcp_registry.disconnect("later")
    assert not coordinator.is_pending("later") and not mcp_registry._pending
    assert wait_for(lambda: mcp_registry.get_server_health("later")["status"] != "auth")


def test_pending_sign_in_does_not_hold_the_process_open(demo):
    import threading

    base = demo("oauth")
    res = mcp_registry.authenticate("slow", {"type": "http", "url": f"{base}/mcp"})
    assert res["ok"] and coordinator.is_pending("slow")
    # A non-daemon thread waiting on the browser would keep Jarvis from quitting for minutes.
    blockers = [t.name for t in threading.enumerate()
                if t.is_alive() and not t.daemon and t is not threading.main_thread()]
    assert blockers == []
    mcp_registry.disconnect("slow")
    assert wait_for(lambda: not any(t.name.startswith("jarvis-mcp-signin") and t.is_alive() for t in threading.enumerate()))


def test_connect_again_while_signing_in_reuses_the_link(demo):
    base = demo("oauth")
    cfg = {"type": "http", "url": f"{base}/mcp"}
    first = mcp_registry.authenticate("dup", cfg)
    second = mcp_registry.authenticate("dup", cfg)
    assert first["url"] == second["url"]
    assert mcp_registry.connect("dup", cfg) == AUTH_REQUIRED_MSG
    mcp_registry.disconnect("dup")


def test_auth_events_reach_listeners(demo):
    base = demo("oauth")
    cfg = {"type": "http", "url": f"{base}/mcp"}
    seen = []
    fn = lambda ev, name: seen.append(ev)  # noqa: E731
    mcp_registry.add_listener(fn)
    try:
        res = mcp_registry.authenticate("ev2", cfg)
        browser(res["url"])
        assert wait_for(lambda: mcp_registry.is_connected("ev2"))
    finally:
        mcp_registry.remove_listener(fn)
    assert seen.index("auth_required") < seen.index("auth_done") <= seen.index("connected")


def test_static_authorization_header_skips_browser_sign_in(demo):
    base = demo("oauth")
    cfg = {"type": "http", "url": f"{base}/mcp", "headers": {"Authorization": "Bearer nope"}}
    err = mcp_registry.connect("keyed", cfg)
    assert err and not needs_auth(err) and ("rejected" in err or "refused" in err or "401" in err)
    assert not coordinator.is_pending("keyed")


# ── pieces ─────────────────────────────────────────────────────────────────


def test_coordinator_deliver_matches_by_state():
    c = mcp_auth.AuthCoordinator()
    req = c.begin("s", "https://auth.example/authorize?client_id=1&state=STATE1")
    assert req.state == "STATE1" and c.is_pending("s")
    ok, _ = c.deliver("WRONG", code="c")
    assert not ok
    ok, name = c.deliver("STATE1", code="the-code")
    assert ok and name == "s"
    assert c.get("s").status == "working"
    assert c.wait(req, timeout=1) == ("the-code", "STATE1")
    c.finish("s", True, "Connected")
    assert c.get("s").status == "done" and not c.is_pending("s")


def test_coordinator_wait_raises_when_cancelled():
    c = mcp_auth.AuthCoordinator()
    req = c.begin("s", "https://auth.example/authorize?state=abc")
    c.cancel("s")
    with pytest.raises(AuthCancelled):
        c.wait(req, timeout=1)


def test_coordinator_refusal_is_reported():
    c = mcp_auth.AuthCoordinator()
    c.begin("s", "https://auth.example/authorize?state=abc")
    ok, msg = c.deliver("abc", error="access_denied", desc="No thanks")
    assert not ok and "No thanks" in msg
    assert c.get("s").status == "error"


def test_coordinator_expires_old_links(monkeypatch):
    c = mcp_auth.AuthCoordinator()
    req = c.begin("s", "https://auth.example/authorize?state=abc")
    monkeypatch.setattr(mcp_auth, "FLOW_TTL", 0.0)
    time.sleep(0.01)
    assert c.get("s") is None or c.get("s").status == "cancelled"
    assert not c.is_pending("s")
    assert req.status == "cancelled"


def test_coordinator_notifies_listeners():
    c = mcp_auth.AuthCoordinator()
    seen = []
    c.add_listener(lambda kind, data: seen.append((kind, data["name"])))
    c.begin("s", "https://auth.example/authorize?state=abc")
    c.deliver("abc", code="x")
    c.finish("s", True)
    assert [k for k, _ in seen] == ["auth_required", "auth_working", "auth_done"]


def test_token_storage_round_trip_and_redirect_mismatch(ext_env):
    import asyncio

    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    st = FileTokenStorage("srv", "https://x.example/mcp", "http://localhost:1111/callback")
    assert not st.has_tokens()
    asyncio.run(st.set_tokens(OAuthToken(access_token="a", refresh_token="r", expires_in=100)))
    assert st.has_tokens() and st.expires_at() > time.time()
    assert asyncio.run(st.get_tokens()).refresh_token == "r"
    info = OAuthClientInformationFull(client_id="cid", redirect_uris=["http://localhost:1111/callback"])
    asyncio.run(st.set_client_info(info))
    assert asyncio.run(st.get_client_info()).client_id == "cid"
    # Registered for another port → register again rather than reuse.
    moved = FileTokenStorage("srv", "https://x.example/mcp", "http://localhost:2222/callback")
    assert asyncio.run(moved.get_client_info()) is None
    st.clear()
    assert not st.has_tokens()
    # A different URL under the same name is a different login.
    assert FileTokenStorage("srv", "https://other.example/mcp", "x").path != st.path


def test_callback_page_for_unknown_state(ext_env, monkeypatch):
    monkeypatch.setenv("HARNESS_MCP_OAUTH_PORT", str(free_port()))
    coordinator.stop_listener()
    try:
        uri = coordinator.redirect_uri()
        r = httpx.get(uri + "?code=1&state=nobody", timeout=5)
        assert r.status_code == 400 and "no longer waiting" in r.text
        assert httpx.get(uri.replace("/callback", "/elsewhere"), timeout=5).status_code == 404
    finally:
        coordinator.stop_listener()


def test_authenticate_right_after_a_link_expires_starts_a_fresh_one(demo, monkeypatch):
    base = demo("oauth")
    cfg = {"type": "http", "url": f"{base}/mcp"}
    first = mcp_registry.authenticate("stale", cfg)
    assert first["ok"]
    coordinator.cancel("stale")  # what expiry does: the waiting connect is still winding down
    second = mcp_registry.authenticate("stale", cfg)
    assert second["ok"] and second["url"] != first["url"], second
    mcp_registry.disconnect("stale")
