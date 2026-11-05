"""Web Push for the web remote: keys, encryption, subscriptions, when to send, and the HTTP routes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from jarvis.web import push


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _device() -> tuple[ec.EllipticCurvePrivateKey, dict]:
    """A browser's side of a subscription: its key pair + PushSubscription.toJSON()."""
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    sub = {"endpoint": f"https://push.example.test/send/{os.urandom(4).hex()}",
           "keys": {"p256dh": _b64u(pub), "auth": _b64u(auth)}}
    return key, sub


def _decrypt(body: bytes, ua_key: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """What the browser does with an aes128gcm push body (RFC 8291 §3.4, RFC 8188)."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_public = body[21:21 + idlen]
    ciphertext = body[21 + idlen:]
    assert rs == push.RECORD_SIZE and idlen == 65
    ua_public = ua_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    mac = lambda k, d: hmac.new(k, d, hashlib.sha256).digest()  # noqa: E731
    ikm = mac(mac(auth, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = mac(salt, ikm)
    cek = mac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = mac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plain.endswith(b"\x02")  # last-record delimiter, no padding
    return plain[:-1]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(push, "VAPID_FILE", tmp_path / "web_push_vapid.json")
    monkeypatch.setattr(push, "SUBS_FILE", tmp_path / "web_push_subscriptions.json")
    monkeypatch.setattr(push, "_LOCK_FILE", tmp_path / "web_push.lock")
    monkeypatch.delenv("HARNESS_WEB_PUSH", raising=False)
    monkeypatch.delenv("HARNESS_PUSH_MIN_SECONDS", raising=False)
    monkeypatch.delenv("HARNESS_PUSH_ACTIVE_MINUTES", raising=False)
    push.reset_for_tests()
    yield tmp_path
    push.reset_for_tests()


@pytest.fixture()
def sent(monkeypatch):
    """Capture push POSTs instead of reaching a push service; ``status`` sets the reply."""
    calls: list[dict] = []
    state = SimpleNamespace(status=201, calls=calls)

    def fake_post(url, content, headers, timeout):
        calls.append({"url": url, "body": content, "headers": headers})
        return SimpleNamespace(status_code=state.status, text="")

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    return state


# ─── encryption + VAPID ───────────────────────────────────────────────────


def test_encrypt_round_trips_like_a_browser():
    ua_key, sub = _device()
    auth = push.b64u_decode(sub["keys"]["auth"])
    body = push.encrypt(b'{"title": "hi"}', push.b64u_decode(sub["keys"]["p256dh"]), auth)
    assert _decrypt(body, ua_key, auth) == b'{"title": "hi"}'


def test_encrypt_refuses_bad_keys_and_oversized_payloads():
    _, sub = _device()
    p256dh, auth = push.b64u_decode(sub["keys"]["p256dh"]), push.b64u_decode(sub["keys"]["auth"])
    with pytest.raises(push.PushError):
        push.encrypt(b"x", p256dh[:-1], auth)
    with pytest.raises(push.PushError):
        push.encrypt(b"x" * push.RECORD_SIZE, p256dh, auth)


def test_vapid_key_is_made_once_private_and_shared(isolated):
    first = push.public_key()
    assert len(push.b64u_decode(first)) == 65
    mode = stat.S_IMODE(os.stat(push.VAPID_FILE).st_mode)
    assert mode == 0o600
    push.reset_for_tests()  # another Jarvis process reads the same file
    assert push.public_key() == first


def test_unreadable_vapid_file_is_an_error_not_a_new_key(isolated):
    push.VAPID_FILE.write_text("not json")
    with pytest.raises(push.PushError):
        push.public_key()
    assert push.VAPID_FILE.read_text() == "not json"


def test_vapid_authorization_is_a_valid_es256_jwt():
    value = push._vapid_authorization("https://fcm.googleapis.com/fcm/send/abc")
    assert value.startswith("vapid t=")
    token, k = value[len("vapid t="):].split(", k=")
    assert k == push.public_key()
    header, claims, sig = token.split(".")
    payload = json.loads(push.b64u_decode(claims))
    assert payload["aud"] == "https://fcm.googleapis.com"
    assert payload["sub"].startswith(("mailto:", "https://"))
    assert time.time() < payload["exp"] <= time.time() + 24 * 3600
    raw = push.b64u_decode(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.b64u_decode(k))
    pub.verify(der, f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if wrong
    # Reused for the same push service rather than re-signed on every alert.
    assert push._vapid_authorization("https://fcm.googleapis.com/other") == value


# ─── subscriptions ────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [
    None, {}, {"endpoint": "http://insecure.test/x", "keys": {}},
    {"endpoint": "https://p.test/x", "keys": {"p256dh": "AAAA", "auth": "AAAA"}},
])
def test_clean_subscription_rejects_junk(bad):
    assert push.clean_subscription(bad) is None


def test_add_replace_and_remove_subscriptions(isolated):
    _, sub = _device()
    push.add_subscription(sub, token="t1", label="Chrome on Android")
    push.add_subscription(sub, token="t2")  # same device again: replaced, not duplicated
    rows = push.subscriptions()
    assert len(rows) == 1 and rows[0]["token"] == "t2"
    assert stat.S_IMODE(os.stat(push.SUBS_FILE).st_mode) == 0o600
    assert push.has_subscription(sub["endpoint"])
    assert push.remove_subscription(sub["endpoint"]) is True
    assert push.remove_subscription(sub["endpoint"]) is False
    assert push.subscriptions() == []


def test_subscriptions_are_capped(monkeypatch):
    monkeypatch.setattr(push, "MAX_SUBSCRIPTIONS", 3)
    subs = [_device()[1] for _ in range(5)]
    for s in subs:
        push.add_subscription(s)
    assert [r["endpoint"] for r in push.subscriptions()] == [s["endpoint"] for s in subs[-3:]]


def test_bad_subscription_raises():
    with pytest.raises(push.PushError):
        push.add_subscription({"endpoint": "https://x.test"})


# ─── sending ──────────────────────────────────────────────────────────────


def test_send_is_encrypted_and_opens_this_project(sent):
    ua_key, sub = _device()
    push.configure(instance_id="4242")
    push.add_subscription(sub, token="tok&1")
    results = push.broadcast({"title": "Jarvis", "body": "done"}, ttl=60, urgency="high")
    assert results == {sub["endpoint"]: 201}
    call = sent.calls[0]
    assert call["url"] == sub["endpoint"]
    assert call["headers"]["Content-Encoding"] == "aes128gcm"
    assert call["headers"]["TTL"] == "60" and call["headers"]["Urgency"] == "high"
    assert call["headers"]["Authorization"].startswith("vapid t=")
    message = json.loads(_decrypt(call["body"], ua_key, push.b64u_decode(sub["keys"]["auth"])))
    assert message == {"title": "Jarvis", "body": "done", "url": "/p/4242/?token=tok%261"}


@pytest.mark.parametrize("status", [404, 410])
def test_gone_subscriptions_are_dropped(sent, status):
    _, sub = _device()
    push.add_subscription(sub)
    sent.status = status
    push.broadcast({"title": "x"}, ttl=60)
    assert push.subscriptions() == []


def test_other_failures_keep_the_subscription(sent):
    _, sub = _device()
    push.add_subscription(sub)
    sent.status = 500
    push.broadcast({"title": "x"}, ttl=60)
    assert len(push.subscriptions()) == 1


def _wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_turn_done_rules(sent, monkeypatch):
    _, sub = _device()
    push.add_subscription(sub)
    monkeypatch.setenv("HARNESS_PUSH_MIN_SECONDS", "30")
    assert push.notify_turn_done(10) is False                      # short
    assert push.notify_turn_done(60, interrupted=True) is False    # you pressed Stop
    push.note_presence("page-1", True)
    assert push.notify_turn_done(60) is False                      # someone is looking
    push.note_presence("page-1", False)
    assert push.notify_turn_done(95, reply="All 40 tests pass.") is True
    assert _wait_for(lambda: len(sent.calls) == 1)


def test_turn_done_without_devices_or_when_off(sent, monkeypatch):
    assert push.notify_turn_done(120) is False                     # nobody subscribed
    _, sub = _device()
    push.add_subscription(sub)
    monkeypatch.setenv("HARNESS_WEB_PUSH", "0")
    assert push.notify_turn_done(120) is False


def _age_device(endpoint: str, seconds: float) -> None:
    """Pretend this device last had a Jarvis page open ``seconds`` ago."""
    rows = push.subscriptions()
    for r in rows:
        if r["endpoint"] == endpoint:
            r["seen"] = r["added"] = time.time() - seconds
    push._write_subs(rows)
    push.reset_for_tests()  # forget this process's write throttle too


def test_only_recently_connected_devices_get_alerts(sent):
    _, phone = _device()
    _, laptop = _device()
    push.add_subscription(phone)
    push.add_subscription(laptop)
    _age_device(phone["endpoint"], push.DEFAULT_ACTIVE_MINUTES * 60 + 60)  # left Jarvis long ago
    assert push.broadcast({"title": "x"}, ttl=60) == {laptop["endpoint"]: 201}
    _age_device(laptop["endpoint"], push.DEFAULT_ACTIVE_MINUTES * 60 + 60)
    assert push.notify_turn_done(120) is False                     # nobody connected: silent
    assert push.mark_seen(phone["endpoint"]) is True               # phone opens Jarvis again
    assert push.broadcast({"title": "x"}, ttl=60) == {phone["endpoint"]: 201}
    assert len(push.subscriptions()) == 2                          # quiet devices are kept, not dropped


def test_old_rows_without_seen_use_added_and_window_can_be_disabled(sent, monkeypatch):
    _, sub = _device()
    push.add_subscription(sub)
    rows = push.subscriptions()
    rows[0].pop("seen")
    rows[0]["added"] = time.time() - 86400
    push._write_subs(rows)
    assert push.connected_subscriptions() == []
    monkeypatch.setenv("HARNESS_PUSH_ACTIVE_MINUTES", "0")         # 0 = always deliver
    assert len(push.connected_subscriptions()) == 1


def test_send_test_reaches_a_quiet_device(sent):
    _, sub = _device()
    push.add_subscription(sub)
    _age_device(sub["endpoint"], 86400)
    assert push.send_test(sub["endpoint"]) == {"ok": True}


def test_mark_seen_unknown_and_throttled(monkeypatch):
    assert push.mark_seen("") is False
    assert push.mark_seen("https://push.example.test/unknown") is False
    _, sub = _device()
    push.add_subscription(sub)
    assert push.mark_seen(sub["endpoint"]) is True
    writes = []
    monkeypatch.setattr(push, "_write_subs", lambda rows: writes.append(rows))
    assert push.mark_seen(sub["endpoint"]) is True                 # within SEEN_WRITE_EVERY: no write
    assert writes == []


def test_presence_expires(monkeypatch):
    push.note_presence("p", True)
    assert push.someone_watching()
    monkeypatch.setattr(push.time, "monotonic", lambda: time.monotonic_ns() / 1e9 + push.PRESENCE_TTL + 5)
    assert not push.someone_watching()


def test_prompt_waits_and_skips_answered_ones(sent, monkeypatch):
    monkeypatch.setattr(push, "PROMPT_DELAY", 0.05)
    _, sub = _device()
    push.add_subscription(sub)
    push.notify_prompt("shell_approval", {"cmd": "rm -rf build"}, lambda: False)  # answered already
    time.sleep(0.3)
    assert sent.calls == []
    push.notify_prompt("shell_approval", {"cmd": "rm -rf build"}, lambda: True)
    assert _wait_for(lambda: len(sent.calls) == 1)
    assert sent.calls[0]["headers"]["Urgency"] == "high"


def test_messages_read_well():
    msg = push.turn_message(134, "Fixed  the\nflaky test " + "x" * 400, "harness")
    assert msg["title"] == "Jarvis · harness"
    assert msg["body"].startswith("Finished in 2m 14s · Fixed the flaky test")
    assert len(msg["body"]) <= push.BODY_MAX
    assert push.prompt_message("shell_approval", {"cmd": "git push"})["body"] == "Approve command: git push"
    ask = push.prompt_message("ask_user", {"questions": [{"prompt": "Which database?"}]}, "api")
    assert ask["title"] == "Jarvis needs you · api" and ask["body"] == "Which database?"
    assert ask["requireInteraction"] is True
    assert push.prompt_message("text_input", {"prompt": "API key"})["body"] == "API key"
    assert [push.format_duration(s) for s in (5, 60, 3600, 3725)] == ["5s", "1m", "1h", "1h 2m"]


def test_send_test_reports_outcome(sent):
    _, sub = _device()
    assert push.send_test(sub["endpoint"])["ok"] is False          # not subscribed
    push.add_subscription(sub)
    assert push.send_test(sub["endpoint"]) == {"ok": True}
    sent.status = 410
    out = push.send_test(sub["endpoint"])
    assert out["ok"] is False and "dropped" in out["error"]


def test_bridge_prompt_open_tracks_answers(monkeypatch):
    from jarvis.web.bridge import WebBridge

    seen = []
    monkeypatch.setattr(push, "notify_prompt", lambda kind, payload, still_open: seen.append((kind, still_open)))
    bridge = WebBridge()
    pid = bridge.new_prompt("shell_approval", {"cmd": "ls"})
    kind, still_open = seen[0]
    assert kind == "shell_approval" and still_open() is True
    bridge.resolve_prompt(pid, "y")
    assert still_open() is False


# ─── HTTP routes ──────────────────────────────────────────────────────────


@pytest.fixture()
def server():
    from jarvis.web.handler import WebHandler
    from jarvis.web.server import _JarvisHTTPServer

    bridge = SimpleNamespace(token="tok", emit=lambda *a: None, public_host="")
    handler = type("H", (WebHandler,), {"bridge": bridge, "app": None, "instance_id": "77"})
    srv = _JarvisHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, payload=None, token="tok"):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            base + path, data=None if payload is None else json.dumps(payload).encode(),
            headers=headers, method="GET" if payload is None else "POST",
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, dict(res.headers), res.read()

    yield call
    srv.shutdown()
    srv.server_close()


def test_manifest_echoes_only_a_valid_token(server):
    _, headers, body = server("/manifest.webmanifest?token=tok", token="")
    assert headers["Content-Type"].startswith("application/manifest+json")
    manifest = json.loads(body)
    assert manifest["start_url"] == "/?token=tok"
    assert manifest["display"] == "standalone" and manifest["scope"] == "/"
    sizes = {i["sizes"] for i in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert json.loads(server("/manifest.webmanifest?token=wrong", token="")[2])["start_url"] == "/"


def test_manifest_icons_exist(server):
    manifest = json.loads(server("/manifest.webmanifest", token="")[2])
    for icon in manifest["icons"] + [{"src": "/static/icons/apple-touch-icon.png"}, {"src": "/static/icons/badge-96.png"}]:
        status, headers, body = server(icon["src"], token="")
        assert status == 200 and headers["Content-Type"] == "image/png" and body[:4] == b"\x89PNG"


def test_service_worker_is_served_from_the_root(server):
    status, headers, body = server("/sw.js", token="")
    assert status == 200
    assert headers["Service-Worker-Allowed"] == "/"
    assert headers["Content-Type"].startswith("application/javascript")
    assert b"showNotification" in body


def test_push_api_round_trip(server, sent):
    _, _, body = server("/api/push/config")
    cfg = json.loads(body)
    assert cfg["available"] is True and len(push.b64u_decode(cfg["public_key"])) == 65
    _, sub = _device()
    assert json.loads(server("/api/push/subscribe", {"subscription": sub, "label": "Safari on iOS"})[2]) == {"ok": True}
    rows = push.subscriptions()
    assert rows[0]["token"] == "tok" and rows[0]["label"] == "Safari on iOS"
    cfg = json.loads(server(f"/api/push/config?endpoint={urllib.parse.quote(sub['endpoint'])}")[2])
    assert cfg["subscribed"] is True
    assert json.loads(server("/api/push/test", {"endpoint": sub["endpoint"]})[2]) == {"ok": True}
    assert cfg["active_minutes"] == push.DEFAULT_ACTIVE_MINUTES
    assert json.loads(server("/api/push/test", {"endpoint": sub["endpoint"]})[2]) == {"ok": True}
    _age_device(sub["endpoint"], 86400)
    assert push.connected_subscriptions() == []
    presence = {"client": "c1", "visible": True, "endpoint": sub["endpoint"]}
    assert json.loads(server("/api/push/presence", presence)[2]) == {"ok": True}
    assert push.someone_watching()
    assert len(push.connected_subscriptions()) == 1               # the page reported in: connected
    bad = json.loads(server("/api/push/subscribe", {"subscription": {"endpoint": "nope"}})[2])
    assert bad["ok"] is False
    assert json.loads(server("/api/push/unsubscribe", {"endpoint": sub["endpoint"]})[2])["removed"] is True


def test_push_api_needs_the_token(server):
    with pytest.raises(urllib.error.HTTPError) as err:
        server("/api/push/config", token="bad")
    assert err.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as err:
        server("/api/push/subscribe", {"subscription": {}}, token="bad")
    assert err.value.code == 401

