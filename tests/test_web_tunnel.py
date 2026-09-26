"""Anywhere mode: tunnel URL parsing, the tunnel process, and token hand-out policy."""
from __future__ import annotations

import email.message
import stat
import time

import pytest

from jarvis.web import handler as web_handler
from jarvis.web import tunnel as tun
from jarvis.web.bridge import WebBridge


# ─── Output parsing ─────────────────────────────────────────────────────

CLOUDFLARE_BANNER = [
    "2026-09-26T10:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...",
    "2026-09-26T10:00:01Z INF +--------------------------------------------------------------------------------------------+",
    "2026-09-26T10:00:01Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |",
    "2026-09-26T10:00:01Z INF |  https://sunny-river-bold-lake.trycloudflare.com                                            |",
]


def test_cloudflare_url_is_read_from_the_banner():
    urls = [tun.parse_url("cloudflare", ln) for ln in CLOUDFLARE_BANNER]
    assert urls[:3] == [None, None, None]
    assert urls[3] == "https://sunny-river-bold-lake.trycloudflare.com"


def test_ngrok_url_and_auth_error_are_read_from_json_logs():
    started = ('{"addr":"http://127.0.0.1:8765","lvl":"info","msg":"started tunnel","name":"command_line",'
               '"obj":"tunnels","t":"2026-09-26T10:00:00Z","url":"https://ab12-cd34.ngrok-free.app"}')
    assert tun.parse_url("ngrok", started) == "https://ab12-cd34.ngrok-free.app"
    auth = ('{"err":"authentication failed: Usage of ngrok requires a verified account and authtoken.\\n'
            'ERR_NGROK_4018","lvl":"eror","msg":"failed to reconnect session"}')
    assert "ngrok config add-authtoken" in tun.parse_error("ngrok", auth)
    assert tun.parse_error("ngrok", '{"lvl":"info","msg":"client session established"}') is None


def test_public_link_carries_the_token():
    assert tun.public_link("https://x.trycloudflare.com/", "tok") == "https://x.trycloudflare.com/?token=tok"


def test_provider_choice_prefers_setting_then_whatever_is_installed(monkeypatch):
    installed = {"ngrok": "/bin/ngrok"}
    monkeypatch.setattr(tun, "binary_for", lambda p: installed.get(p))
    assert tun.pick_provider("auto") == "ngrok"
    assert tun.pick_provider("cloudflare") == "ngrok"  # not installed → fall back
    installed["cloudflare"] = "/bin/cloudflared"
    assert tun.pick_provider("auto") == "cloudflare"
    assert tun.pick_provider("ngrok") == "ngrok"
    installed.clear()
    assert tun.pick_provider("auto") is None


# ─── The tunnel process ─────────────────────────────────────────────────

@pytest.fixture
def fake_cloudflared(tmp_path, monkeypatch):
    script = tmp_path / "cloudflared"
    script.write_text(
        "#!/bin/sh\n"
        "echo 'INF Requesting new quick Tunnel on trycloudflare.com...'\n"
        "echo 'INF |  https://fake-quick-tunnel.trycloudflare.com  |'\n"
        "exec sleep 30\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(tun, "binary_for", lambda p: str(script) if p == "cloudflare" else None)
    monkeypatch.setattr(tun, "_doh_resolves", lambda host: True)
    return script


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_tunnel_goes_live_and_stop_kills_the_process(fake_cloudflared):
    seen = []
    t = tun.Tunnel(provider="cloudflare", port=8765, on_change=lambda x: seen.append(x.status))
    t.start()
    assert _wait(lambda: t.status == "live")
    assert t.url == "https://fake-quick-tunnel.trycloudflare.com"
    assert seen == ["live"]
    proc = t._proc
    t.stop()
    assert t.status == "stopped"
    assert _wait(lambda: proc.poll() is not None)


def test_tunnel_that_exits_early_reports_why(tmp_path, monkeypatch):
    script = tmp_path / "cloudflared"
    script.write_text("#!/bin/sh\necho 'ERR failed to request quick Tunnel: dial tcp: no route'\nexit 1\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(tun, "binary_for", lambda p: str(script))
    t = tun.Tunnel(provider="cloudflare", port=8765)
    t.start()
    assert _wait(lambda: t.status == "error")
    assert "quick Tunnel" in t.error or "exited" in t.error


# ─── Who gets the token ─────────────────────────────────────────────────

def _handler(client_ip: str, headers: dict[str, str], public_host: str = ""):
    h = web_handler.WebHandler.__new__(web_handler.WebHandler)
    h.bridge = WebBridge()
    h.bridge.public_host = public_host
    msg = email.message.Message()
    for k, v in headers.items():
        msg[k] = v
    h.headers = msg
    h.client_address = (client_ip, 50000)
    return h


@pytest.mark.parametrize(
    "ip, headers, public_host, expected",
    [
        ("192.168.0.20", {"Host": "192.168.0.105:8765"}, "", True),          # phone on the Wi-Fi
        ("127.0.0.1", {"Host": "localhost:8765"}, "", True),                  # this computer
        # cloudflared / ngrok connect from 127.0.0.1 but add proxy headers:
        ("127.0.0.1", {"Host": "x.trycloudflare.com", "Cf-Connecting-Ip": "8.8.8.8"}, "", False),
        ("127.0.0.1", {"Host": "ab.ngrok-free.app", "X-Forwarded-For": "8.8.8.8"}, "", False),
        # even with no proxy headers, the tunnel's public host never gets it
        ("127.0.0.1", {"Host": "x.trycloudflare.com"}, "", False),
        ("127.0.0.1", {"Host": "my.custom.domain"}, "my.custom.domain", False),
        ("8.8.8.8", {"Host": "203.0.113.5:8765"}, "", False),               # public internet
    ],
)
def test_token_is_only_handed_out_on_the_local_network(ip, headers, public_host, expected):
    assert _handler(ip, headers, public_host)._may_hand_out_token() is expected


def test_cloudflare_waits_for_dns_before_going_live(fake_cloudflared, monkeypatch):
    """A QR shown before the hostname exists makes phones cache "not found"."""
    answers = iter([False, False, True])
    monkeypatch.setattr(tun, "_doh_resolves", lambda host: next(answers, True))
    t = tun.Tunnel(provider="cloudflare", port=8765)
    t.start()
    assert _wait(lambda: t.url != "")
    assert t.status == "starting"          # URL known, DNS not ready yet
    assert _wait(lambda: t.status == "live", timeout=6)
    t.stop()


# ─── Long-poll transport (tunnels that buffer SSE) ──────────────────────

def test_poll_returns_events_after_the_cursor_in_order():
    import json as _json

    b = WebBridge()
    start = b.latest_seq()
    b.emit("message", {"role": "you", "text": "hi"})
    b.emit("stream_delta", {"kind": "assistant", "chunk": "He"})
    body = _json.loads(b.poll(start, timeout=0.1, client_id="c1"))
    assert [e["type"] for e in body["events"]] == ["message", "stream_delta"]
    assert body["cursor"] == b.latest_seq()
    # nothing new → an empty answer after the wait
    again = _json.loads(b.poll(body["cursor"], timeout=0.1, client_id="c1"))
    assert again["events"] == [] and again["cursor"] == body["cursor"]


def test_poll_wakes_up_as_soon_as_an_event_arrives():
    import json as _json
    import threading as _threading

    b = WebBridge()
    cur = b.latest_seq()
    _threading.Timer(0.2, lambda: b.emit("busy", {"busy": True})).start()
    t0 = time.monotonic()
    body = _json.loads(b.poll(cur, timeout=5, client_id="c1"))
    assert time.monotonic() - t0 < 2
    assert body["events"][0]["type"] == "busy"


def test_poll_client_that_fell_behind_is_told_to_resync(monkeypatch):
    import json as _json

    from jarvis.web import bridge as bridge_mod

    b = WebBridge()
    b._log = bridge_mod.deque(maxlen=5)
    for i in range(20):
        b.emit("stream_delta", {"chunk": str(i)})
    assert _json.loads(b.poll(2, timeout=0.1))["resync"] is True


def test_pollers_count_as_connected_browsers(monkeypatch):
    from jarvis.web import bridge as bridge_mod

    b = WebBridge()
    assert not b.has_subscribers()
    b.poll(b.latest_seq(), timeout=0.01, client_id="phone")
    assert b.has_subscribers() and b.subscriber_count() == 1  # approvals reach it
    monkeypatch.setattr(bridge_mod, "POLLER_TTL", -1)
    assert not b.has_subscribers()


def test_close_releases_a_waiting_poll():
    import json as _json
    import threading as _threading

    b = WebBridge()
    _threading.Timer(0.2, b.close).start()
    body = _json.loads(b.poll(b.latest_seq(), timeout=5))
    assert body.get("closed") is True
