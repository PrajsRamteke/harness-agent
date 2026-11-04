"""Web Push for the web remote: alerts on a phone or desktop while the page is closed.

The page's own alerts (``theme.js`` ``replyReady`` / ``needsAttention``) only
fire while its tab is alive. A phone locks, iOS suspends a home-screen app, a
desktop tab gets discarded — and "start a task, walk away" breaks. Web Push
reaches the browser's push service instead, which wakes the service worker
(``static/sw.js``) even with no page open.

Pieces, all standard and dependency-light (``cryptography`` is already pulled
in by ``mcp`` → ``pyjwt[crypto]``; without it push just reports unavailable):

* VAPID (RFC 8292) — one P-256 key for this computer, shared by every running
  Jarvis (``web_push_vapid.json``), so a device subscribes once.
* Subscriptions — one per device/browser (``web_push_subscriptions.json``),
  with the token of the server the page came from (the link a notification
  opens needs it). Gone subscriptions (404 / 410) are dropped on send.
* Payload encryption (RFC 8291, ``aes128gcm``) — the push service never sees
  what Jarvis says.
* Presence — a visible page reports itself every ~30 s; while one does, this
  Jarvis sends nothing (you're already looking).

Events: a turn that ran at least ``HARNESS_PUSH_MIN_SECONDS`` (default 20)
finished, and an approval / question / value prompt still open after
``PROMPT_DELAY`` seconds. ``HARNESS_WEB_PUSH=0`` turns all of it off.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.parse
from typing import Any, Callable, Iterator

from ..constants import CONFIG_DIR

log = logging.getLogger(__name__)

VAPID_FILE = CONFIG_DIR / "web_push_vapid.json"
SUBS_FILE = CONFIG_DIR / "web_push_subscriptions.json"
_LOCK_FILE = CONFIG_DIR / "web_push.lock"

MAX_SUBSCRIPTIONS = 20          # devices kept; the oldest go first
MAX_ENDPOINT_LEN = 2048
PRESENCE_TTL = 75.0             # a page re-reports every 30 s while visible
PROMPT_DELAY = 4.0              # answered at the terminal by then: no buzz
DEFAULT_MIN_TURN_SECONDS = 20.0
SEND_TIMEOUT = 15.0
JWT_LIFETIME = 12 * 3600        # push services cap it at 24 h
JWT_REUSE = JWT_LIFETIME - 3600
RECORD_SIZE = 4096
TTL_TURN = 6 * 3600             # how long the push service holds an undelivered alert
TTL_PROMPT = 3600
BODY_MAX = 180
# RFC 8292 wants a contact URI. Apple rejects ``localhost`` addresses here.
DEFAULT_SUBJECT = "mailto:jarvis-web-remote@example.com"

_P256_POINT_LEN = 65
_AUTH_SECRET_LEN = 16

_lock = threading.RLock()
_vapid: tuple[Any, str] | None = None            # (private key, public key b64url)
_jwt_cache: dict[str, tuple[float, str]] = {}    # aud → (made at, header value)
_presence: dict[str, float] = {}                 # page client id → visible until
_instance_id: str | None = None


class PushError(Exception):
    """Push can't work here (missing library, unreadable key file, bad input)."""


# ─── small helpers ────────────────────────────────────────────────────────


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    text = (text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _enabled_by_env() -> bool:
    return os.environ.get("HARNESS_WEB_PUSH", "1").strip() != "0"


def min_turn_seconds() -> float:
    raw = os.environ.get("HARNESS_PUSH_MIN_SECONDS", "")
    try:
        value = float(raw) if raw.strip() else DEFAULT_MIN_TURN_SECONDS
    except ValueError:
        return DEFAULT_MIN_TURN_SECONDS
    return max(0.0, value)


def _subject() -> str:
    value = os.environ.get("HARNESS_PUSH_SUBJECT", "").strip()
    return value if value.startswith(("mailto:", "https://")) else DEFAULT_SUBJECT


def available() -> bool:
    """Can this computer send Web Push at all?"""
    if not _enabled_by_env():
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric import ec  # noqa: F401
    except ImportError:
        return False
    return True


def configure(*, instance_id: str | None) -> None:
    """Which ``/p/<id>`` a notification from this Jarvis opens."""
    global _instance_id
    _instance_id = instance_id or None


@contextlib.contextmanager
def _file_lock() -> Iterator[None]:
    """Every running Jarvis shares the files: serialise read-modify-write."""
    with _lock:
        try:
            import fcntl
        except ImportError:  # Windows: in-process lock only
            yield
            return
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(_LOCK_FILE, "a") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _write_private(path: Any, text: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


# ─── VAPID key ────────────────────────────────────────────────────────────


def _load_vapid() -> tuple[Any, str]:
    """This computer's VAPID key (made on first use): ``(private key, public b64url)``."""
    global _vapid
    if _vapid is not None:
        return _vapid
    if not available():
        raise PushError("Web Push needs the 'cryptography' package (pip install cryptography).")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    with _file_lock():
        if _vapid is not None:
            return _vapid
        key = None
        try:
            raw = json.loads(VAPID_FILE.read_text("utf-8"))
            key = serialization.load_pem_private_key(str(raw["private_key"]).encode("ascii"), password=None)
        except FileNotFoundError:
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode("ascii")
            _write_private(VAPID_FILE, json.dumps({"private_key": pem}))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PushError(f"Can't read {VAPID_FILE} ({type(exc).__name__}). Delete it to make a new key.") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise PushError(f"{VAPID_FILE} does not hold a P-256 key. Delete it to make a new one.")
        public = key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
        )
        _vapid = (key, b64u(public))
        return _vapid


def public_key() -> str:
    """The VAPID public key a page passes to ``pushManager.subscribe``."""
    return _load_vapid()[1]


def _vapid_authorization(endpoint: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    parts = urllib.parse.urlsplit(endpoint)
    aud = f"{parts.scheme}://{parts.netloc}"
    now = time.time()
    with _lock:
        cached = _jwt_cache.get(aud)
        if cached and now - cached[0] < JWT_REUSE:
            return cached[1]
    key, pub = _load_vapid()
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps(
        {"aud": aud, "exp": int(now) + JWT_LIFETIME, "sub": _subject()}, separators=(",", ":"),
    ).encode())
    signing_input = f"{header}.{claims}".encode("ascii")
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    signature = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    value = f"vapid t={header}.{claims}.{signature}, k={pub}"
    with _lock:
        _jwt_cache[aud] = (now, value)
    return value


# ─── RFC 8291 payload encryption ──────────────────────────────────────────


def encrypt(payload: bytes, p256dh: bytes, auth: bytes, *, salt: bytes | None = None,
            server_key: Any = None) -> bytes:
    """``aes128gcm`` body for one subscription (single record, no padding)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(p256dh) != _P256_POINT_LEN or len(auth) != _AUTH_SECRET_LEN:
        raise PushError("bad subscription keys")
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
    as_private = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = as_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
    )
    shared = as_private.exchange(ec.ECDH(), ua_public)
    salt = salt if salt is not None else os.urandom(16)
    # HKDF(auth, ecdh, "WebPush: info" ‖ ua_public ‖ as_public, 32)
    prk_key = _hmac(auth, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + p256dh + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    if len(payload) + 1 + 16 > RECORD_SIZE:
        raise PushError("notification too large")
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


# ─── subscriptions ────────────────────────────────────────────────────────


def clean_subscription(data: Any) -> dict[str, str] | None:
    """``PushSubscription.toJSON()`` → ``{endpoint, p256dh, auth}``, or None if unusable."""
    if not isinstance(data, dict):
        return None
    endpoint = str(data.get("endpoint") or "").strip()
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    if not endpoint.startswith("https://") or len(endpoint) > MAX_ENDPOINT_LEN:
        return None
    try:
        p256dh = b64u_decode(str(keys.get("p256dh") or ""))
        auth = b64u_decode(str(keys.get("auth") or ""))
    except (ValueError, TypeError):
        return None
    if len(p256dh) != _P256_POINT_LEN or p256dh[0] != 4 or len(auth) != _AUTH_SECRET_LEN:
        return None
    return {"endpoint": endpoint, "p256dh": b64u(p256dh), "auth": b64u(auth)}


def _read_subs() -> list[dict[str, Any]]:
    try:
        raw = json.loads(SUBS_FILE.read_text("utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        log.warning("web push: unreadable %s (%s)", SUBS_FILE, exc)
        return []
    rows = raw.get("subscriptions") if isinstance(raw, dict) else None
    out = []
    for row in rows or []:
        if isinstance(row, dict) and clean_subscription(
            {"endpoint": row.get("endpoint"), "keys": {"p256dh": row.get("p256dh"), "auth": row.get("auth")}}
        ):
            out.append(row)
    return out


def _write_subs(rows: list[dict[str, Any]]) -> None:
    _write_private(SUBS_FILE, json.dumps({"subscriptions": rows}, indent=1))


def subscriptions() -> list[dict[str, Any]]:
    with _file_lock():
        return _read_subs()


def add_subscription(data: Any, *, token: str = "", label: str = "") -> dict[str, str]:
    """Store (or refresh) one device. Raises ``PushError`` on a bad subscription."""
    clean = clean_subscription(data)
    if clean is None:
        raise PushError("That push subscription is not valid.")
    row: dict[str, Any] = {**clean, "token": str(token or ""), "label": str(label or "")[:80], "added": time.time()}
    with _file_lock():
        rows = [r for r in _read_subs() if r.get("endpoint") != clean["endpoint"]]
        rows.append(row)
        _write_subs(rows[-MAX_SUBSCRIPTIONS:])
    return clean


def remove_subscription(endpoint: str) -> bool:
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        return False
    with _file_lock():
        rows = _read_subs()
        kept = [r for r in rows if r.get("endpoint") != endpoint]
        if len(kept) == len(rows):
            return False
        _write_subs(kept)
    return True


def has_subscription(endpoint: str) -> bool:
    return any(r.get("endpoint") == endpoint for r in subscriptions())


# ─── presence ─────────────────────────────────────────────────────────────


def note_presence(client_id: str, visible: bool) -> None:
    client_id = str(client_id or "")[:64]
    if not client_id:
        return
    with _lock:
        if visible:
            _presence[client_id] = time.monotonic() + PRESENCE_TTL
        else:
            _presence.pop(client_id, None)


def someone_watching() -> bool:
    now = time.monotonic()
    with _lock:
        for cid in [c for c, until in _presence.items() if until < now]:
            del _presence[cid]
        return bool(_presence)


# ─── sending ──────────────────────────────────────────────────────────────


def _open_url(row: dict[str, Any]) -> str:
    """Where tapping the notification goes (relative to the subscribing origin)."""
    path = f"/p/{_instance_id}/" if _instance_id else "/"
    token = str(row.get("token") or "")
    return path + (f"?token={urllib.parse.quote(token)}" if token else "")


def send_one(row: dict[str, Any], message: dict[str, Any], *, ttl: int, urgency: str = "normal") -> int:
    """POST one encrypted push. Returns the push service's HTTP status (0: network error)."""
    import httpx

    body = json.dumps({**message, "url": _open_url(row)}, ensure_ascii=False).encode("utf-8")
    encrypted = encrypt(body, b64u_decode(row["p256dh"]), b64u_decode(row["auth"]))
    headers = {
        "TTL": str(int(ttl)),
        "Urgency": urgency,
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Authorization": _vapid_authorization(row["endpoint"]),
    }
    try:
        resp = httpx.post(row["endpoint"], content=encrypted, headers=headers, timeout=SEND_TIMEOUT)
    except httpx.HTTPError as exc:
        log.info("web push: %s unreachable (%s)", urllib.parse.urlsplit(row["endpoint"]).netloc, exc)
        return 0
    if resp.status_code >= 300:
        log.info("web push: %s → HTTP %s %s", urllib.parse.urlsplit(row["endpoint"]).netloc,
                 resp.status_code, resp.text[:200])
    return resp.status_code


def broadcast(message: dict[str, Any], *, ttl: int, urgency: str = "normal",
              only: str | None = None) -> dict[str, int]:
    """Send to every device (or just ``only``); drop the ones the push service says are gone."""
    rows = [r for r in subscriptions() if only is None or r.get("endpoint") == only]
    results: dict[str, int] = {}
    for row in rows:
        try:
            results[row["endpoint"]] = send_one(row, message, ttl=ttl, urgency=urgency)
        except PushError as exc:
            log.warning("web push: %s", exc)
            results[row["endpoint"]] = 0
    for endpoint, status in results.items():
        if status in (404, 410):
            remove_subscription(endpoint)
    return results


def _in_background(fn: Callable[[], Any]) -> None:
    def run() -> None:
        try:
            fn()
        except Exception:  # a notification must never take a turn down with it
            log.exception("web push failed")

    threading.Thread(target=run, daemon=True, name="jarvis-web-push").start()


def _should_send() -> bool:
    if not available() or someone_watching():
        return False
    try:
        return bool(subscriptions())
    except OSError:
        return False


def _project() -> str:
    try:
        from .state_api import _project_name

        return str(_project_name() or "")
    except Exception:
        return ""


def _clip(text: str, limit: int = BODY_MAX) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_duration(seconds: float) -> str:
    total = int(round(max(0.0, seconds)))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def turn_message(seconds: float, reply: str = "", project: str = "") -> dict[str, Any]:
    body = f"Finished in {format_duration(seconds)}"
    snippet = _clip(reply, BODY_MAX - len(body) - 3)
    return {
        "kind": "turn",
        "title": f"Jarvis · {project}" if project else "Jarvis finished",
        "body": f"{body} · {snippet}" if snippet else body,
        "tag": f"jarvis-turn-{_instance_id or 'main'}",
        "requireInteraction": False,
    }


def prompt_message(kind: str, payload: dict[str, Any], project: str = "") -> dict[str, Any]:
    if kind == "shell_approval":
        body = f"Approve command: {_clip(str(payload.get('cmd') or ''), BODY_MAX - 18)}"
    elif kind == "ask_user":
        questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
        first = questions[0] if questions and isinstance(questions[0], dict) else {}
        body = _clip(str(first.get("prompt") or "Jarvis has a question for you."))
    elif kind == "text_input":
        body = _clip(str(payload.get("prompt") or "Jarvis needs a value from you."))
    else:
        body = "Open Jarvis to respond."
    return {
        "kind": "prompt",
        "title": f"Jarvis needs you · {project}" if project else "Jarvis needs you",
        "body": body,
        "tag": f"jarvis-prompt-{_instance_id or 'main'}",
        "requireInteraction": True,
    }


def notify_turn_done(seconds: float, *, interrupted: bool = False, reply: str = "") -> bool:
    """A long turn ended: push it unless it was stopped, short, or someone is watching."""
    if interrupted or seconds < min_turn_seconds() or not _should_send():
        return False
    message = turn_message(seconds, reply, _project())
    _in_background(lambda: broadcast(message, ttl=TTL_TURN, urgency="normal"))
    return True


def notify_prompt(kind: str, payload: dict[str, Any], still_open: Callable[[], bool]) -> None:
    """Jarvis is blocked on an answer: push it if nobody answered within ``PROMPT_DELAY``."""
    if not available():
        return
    project = _project()

    def later() -> None:
        if still_open() and _should_send():
            broadcast(prompt_message(kind, payload, project), ttl=TTL_PROMPT, urgency="high")

    timer = threading.Timer(PROMPT_DELAY, lambda: _in_background(later))
    timer.daemon = True
    timer.start()


def send_test(endpoint: str) -> dict[str, Any]:
    """``/api/push/test``: one alert to this device, now, whoever is watching."""
    if not has_subscription(endpoint):
        return {"ok": False, "error": "This device is not subscribed."}
    results = broadcast(
        {"kind": "test", "title": "Jarvis", "body": "Notifications work on this device.",
         "tag": "jarvis-test", "requireInteraction": False},
        ttl=300, urgency="high", only=endpoint,
    )
    status = results.get(endpoint, 0)
    if 200 <= status < 300:
        return {"ok": True}
    if status in (404, 410):
        return {"ok": False, "error": "The browser dropped this subscription. Turn notifications off and on."}
    if status == 0:
        return {"ok": False, "error": "Couldn't reach the browser's push service from this computer."}
    return {"ok": False, "error": f"The push service refused it (HTTP {status})."}


def reset_for_tests() -> None:
    global _vapid, _instance_id
    with _lock:
        _vapid = None
        _instance_id = None
        _jwt_cache.clear()
        _presence.clear()
