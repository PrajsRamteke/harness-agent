"""Browser sign-in (OAuth) for remote MCP servers — what the *Authenticate* button drives.

A remote server that answers ``401`` is signed in with the MCP SDK's
``OAuthClientProvider`` (discovery, dynamic client registration, PKCE). A
server that doesn't offer dynamic registration (Slack) signs in with a
*pre-registered app* instead: the config's ``oauth: {clientId, clientSecret?,
callbackPort?, scopes?}`` (Claude Code's shape) is handed to the SDK as the
client, and the browser comes back to that fixed port. This module supplies the
parts around it that are Jarvis-specific:

* ``FileTokenStorage`` — tokens + the registered client, per server, mode 600
  under ``~/.config/harness-agent/mcp-auth/``. Expired access tokens refresh
  with the stored refresh token, so a restart never asks again.
* ``AuthCoordinator`` — one *pending sign-in* per server. The provider hands
  it the authorize URL (``redirect_handler``); the UI shows a button that opens
  it. The browser then lands on ``http://localhost:<port>/callback`` (a tiny
  loopback listener here) and the provider's ``callback_handler`` wakes up. When
  the browser is on another device the user pastes the address it ended on
  (``submit``), exactly like the ChatGPT sign-in.

Nothing here opens a browser by itself — the user may be on a phone. The
terminal UI calls ``open_browser`` from its button.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import pathlib
import re
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import httpx
from mcp.client.auth import OAuthClientProvider, OAuthTokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

logger = logging.getLogger("jarvis.mcp.auth")

AUTH_DIR = pathlib.Path.home() / ".config" / "harness-agent" / "mcp-auth"
CALLBACK_PATH = "/callback"
DEFAULT_PORT = 33418
FLOW_TTL = 600.0  # a sign-in link stays good this long
_LIVE = ("pending", "working")  # waiting for the browser / exchanging the code


class AuthNeeded(Exception):
    """Sign-in is required but this connect is not allowed to start one (startup)."""


class AuthCancelled(Exception):
    """The pending sign-in was cancelled or timed out."""


# ── token storage ─────────────────────────────────────────────────────────


def _key(server_name: str, server_url: str) -> str:
    digest = hashlib.sha256(f"{server_name}\n{server_url}".encode()).hexdigest()[:12]
    safe = re.sub(r"[^\w.-]+", "_", server_name) or "server"
    return f"{safe}-{digest}"


class FileTokenStorage:
    """``TokenStorage`` for the SDK: one JSON file per (server name, url)."""

    def __init__(
        self,
        server_name: str,
        server_url: str,
        redirect_uri: str,
        static_client: OAuthClientInformationFull | None = None,
    ) -> None:
        self.path = AUTH_DIR / f"{_key(server_name, server_url)}.json"
        self.redirect_uri = redirect_uri
        # A pre-registered app: always this client, never registered or stored.
        self.static_client = static_client

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    def has_tokens(self) -> bool:
        return bool((self._read().get("tokens") or {}).get("access_token"))

    def expires_at(self) -> float | None:
        val = self._read().get("expires_at")
        return float(val) if isinstance(val, (int, float)) else None

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        if not raw:
            return None
        try:
            return OAuthToken.model_validate(raw)
        except Exception:
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        data["expires_at"] = time.time() + tokens.expires_in if tokens.expires_in else None
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        if self.static_client is not None:
            return self.static_client
        raw = self._read().get("client_info")
        if not raw:
            return None
        try:
            info = OAuthClientInformationFull.model_validate(raw)
        except Exception:
            return None
        # Registered for another callback address (port changed): register again.
        if self.redirect_uri not in {str(u) for u in (info.redirect_uris or [])}:
            return None
        return info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        if self.static_client is not None:
            return
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)


def has_saved_login(server_name: str, server_url: str) -> bool:
    return FileTokenStorage(server_name, server_url, "").has_tokens()


def forget_login(server_name: str, server_url: str) -> None:
    FileTokenStorage(server_name, server_url, "").clear()


# ── pending sign-ins ──────────────────────────────────────────────────────


class AuthRequest:
    """One server waiting for its user to sign in."""

    def __init__(self, name: str, url: str, redirect_uri: str) -> None:
        self.name = name
        self.url = url
        self.redirect_uri = redirect_uri
        self.state = (urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("state") or [""])[0]
        self.created = time.monotonic()
        self.status = "pending"  # pending → working → done | error | cancelled
        self.message = ""
        self.code: str | None = None
        self.returned_state: str | None = None
        self.cond = threading.Condition()

    def expired(self) -> bool:
        return time.monotonic() - self.created > FLOW_TTL

    def public(self) -> dict[str, Any]:
        left = max(0, int(FLOW_TTL - (time.monotonic() - self.created)))
        return {
            "name": self.name,
            "url": self.url,
            "status": self.status,
            "message": self.message,
            "expires_in": left,
            "redirect_uri": self.redirect_uri,
        }


def _page(title: str, body: str, ok: bool) -> bytes:
    color = "#4ade80" if ok else "#f87171"
    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<body style=\"margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0a0a;"
        "color:#fafafa;font:16px/1.5 system-ui,sans-serif\">"
        "<div style=\"max-width:380px;padding:32px;text-align:center\">"
        f"<div style=\"font-size:40px;color:{color}\">{'✓' if ok else '✕'}</div>"
        f"<h1 style=\"font-size:20px;margin:12px 0 6px\">{html.escape(title)}</h1>"
        f"<p style=\"color:#a3a3a3;margin:0\">{html.escape(body)}</p></div></body>"
    ).encode()


class AuthCoordinator:
    def __init__(self) -> None:
        self._requests: dict[str, AuthRequest] = {}
        self._lock = threading.Lock()
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._server: ThreadingHTTPServer | None = None
        self._port = 0
        self._server_error = ""
        # Pre-registered apps come back to the exact port they registered.
        self._fixed: dict[int, ThreadingHTTPServer] = {}

    # ── listeners (the UIs) ──────────────────────────────────────────────

    def add_listener(self, fn: Callable[[str, dict[str, Any]], None]) -> None:
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[str, dict[str, Any]], None]) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def _notify(self, kind: str, req: AuthRequest) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(kind, req.public())
            except Exception:
                logger.debug("auth listener failed", exc_info=True)

    # ── loopback listener ────────────────────────────────────────────────

    def redirect_uri(self, *, start: bool = True, port: int | None = None) -> str:
        """The address the browser returns to. Starts the listener on first use
        (``start=False`` only names the address, e.g. for a startup connect).

        ``port`` pins it (a pre-registered app's redirect URL): that exact port
        or nothing — falling back to another one would only make the provider
        refuse the redirect."""
        if port:
            if start and not self._ensure_fixed(int(port)):
                return ""
            return f"http://localhost:{int(port)}{CALLBACK_PATH}"
        if start:
            self._ensure_listener()
        port = self._port or preferred_port()
        return f"http://localhost:{port}{CALLBACK_PATH}" if (self._port or not start) else ""

    def _ensure_fixed(self, port: int) -> bool:
        with self._lock:
            if (self._server is not None and self._port == port) or port in self._fixed:
                return True
        if port == preferred_port():
            self._ensure_listener()
            with self._lock:
                if self._server is not None and self._port == port:
                    return True
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(self))
        except OSError as exc:
            self._server_error = f"port {port} is already in use ({exc.strerror or exc})"
            return False
        server.daemon_threads = True
        with self._lock:
            self._fixed[port] = server
        threading.Thread(target=server.serve_forever, daemon=True, name=f"jarvis-mcp-oauth-{port}").start()
        return True

    def listening(self) -> bool:
        return self._server is not None

    def _ensure_listener(self) -> None:
        with self._lock:
            if self._server is not None:
                return
            preferred = preferred_port()
            server = None
            for port in (preferred, 0):
                try:
                    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(self))
                    break
                except OSError as exc:
                    self._server_error = str(exc)
            if server is None:
                return
            server.daemon_threads = True
            self._server = server
            self._port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True, name="jarvis-mcp-oauth-callback").start()

    def stop_listener(self) -> None:
        with self._lock:
            server, self._server, self._port = self._server, None, 0
            fixed, self._fixed = list(self._fixed.values()), {}
        for srv in [server, *fixed]:
            if srv is None:
                continue
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass

    # ── requests ─────────────────────────────────────────────────────────

    def get(self, name: str) -> AuthRequest | None:
        with self._lock:
            req = self._requests.get(name)
        if req is not None and req.status in _LIVE and req.expired():
            self._settle(req, "cancelled", "The sign-in link expired. Start again.")
            return None
        return req

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            names = [n for n, r in self._requests.items() if r.status in _LIVE]
        out = []
        for name in names:
            req = self.get(name)
            if req is not None and req.status in _LIVE:
                out.append(req.public())
        return out

    def is_pending(self, name: str) -> bool:
        req = self.get(name)
        return req is not None and req.status in _LIVE

    def begin(self, name: str, url: str, redirect_uri: str = "") -> AuthRequest:
        """The SDK produced an authorize URL: publish it to the UIs."""
        req = AuthRequest(name, url, redirect_uri or self.redirect_uri())
        with self._lock:
            old = self._requests.get(name)
            self._requests[name] = req
        if old is not None and old.status in _LIVE:
            self._settle(old, "cancelled", "Replaced by a newer sign-in", notify=False)
        self._notify("auth_required", req)
        return req

    def wait(self, req: AuthRequest, timeout: float = FLOW_TTL) -> tuple[str, str | None]:
        """Block until the browser (or a paste) delivers the code. Runs in a worker thread."""
        end = time.monotonic() + timeout
        with req.cond:
            while req.status in _LIVE and req.code is None:
                left = end - time.monotonic()
                if left <= 0 or req.expired():
                    break
                req.cond.wait(min(left, 1.0))
            if req.code is not None:
                return req.code, req.returned_state
        if req.status in _LIVE:
            self._settle(req, "cancelled", "Timed out waiting for sign-in")
        raise AuthCancelled(req.message or "Sign-in cancelled")

    def deliver(self, state: str, code: str = "", error: str = "", desc: str = "") -> tuple[bool, str]:
        """A callback arrived (loopback hit or pasted address). Returns ``(ok, message)``."""
        with self._lock:
            req = next(
                (r for r in self._requests.values() if r.status == "pending" and r.state and r.state == state),
                None,
            )
        if req is None:
            return False, "This sign-in link is no longer waiting. Start again from Jarvis."
        if error:
            msg = desc or error
            self._settle(req, "error", f"Sign-in was refused: {msg}")
            return False, req.message
        if not code:
            return False, "No authorization code in that address."
        with req.cond:
            req.code = code
            req.returned_state = state
            req.status = "working"
            req.message = "Signing in…"
            req.cond.notify_all()
        self._notify("auth_working", req)
        return True, req.name

    def submit(self, name: str, pasted: str) -> dict[str, Any]:
        """The user pasted the address their browser ended on (sign-in on another device)."""
        req = self.get(name)
        if req is None or req.status not in _LIVE:
            return {"ok": False, "error": "This sign-in link expired. Start again.", "expired": True}
        if req.status == "working":
            return {"ok": True}
        raw = (pasted or "").strip()
        if not raw:
            return {"ok": False, "error": "Paste the address first."}
        if not raw.startswith(("http://", "https://")) and ("code=" in raw or "error=" in raw):
            raw = "http://localhost/?" + raw.split("?", 1)[-1]
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
        code = (qs.get("code") or [""])[0]
        state = (qs.get("state") or [""])[0] or req.state
        if not code and not qs.get("error"):
            return {"ok": False, "error": "Couldn't find a code in that address. Copy the whole address bar."}
        ok, msg = self.deliver(
            state,
            code=code,
            error=(qs.get("error") or [""])[0],
            desc=(qs.get("error_description") or [""])[0],
        )
        return {"ok": True} if ok else {"ok": False, "error": msg}

    def _settle(self, req: AuthRequest, status: str, message: str = "", *, notify: bool = True) -> None:
        with req.cond:
            if req.status not in _LIVE and status != "done":
                return
            req.status = status
            if message:
                req.message = message
            req.cond.notify_all()
        if notify:
            kind = {"done": "auth_done", "error": "auth_error", "cancelled": "auth_cancelled"}.get(status, "auth_error")
            self._notify(kind, req)

    def finish(self, name: str, ok: bool, message: str = "") -> None:
        """The connect that was waiting on this sign-in ended."""
        with self._lock:
            req = self._requests.get(name)
        if req is None:
            return
        if ok:
            self._settle(req, "done", message or "Connected")
        elif req.status in _LIVE:
            self._settle(req, "error", message or "Sign-in failed")
        elif message and req.status != "done":
            req.message = message

    def cancel(self, name: str) -> bool:
        with self._lock:
            req = self._requests.get(name)
        if req is None or req.status not in _LIVE:
            return False
        self._settle(req, "cancelled", "Sign-in cancelled")
        return True

    def forget(self, name: str) -> None:
        self.cancel(name)
        with self._lock:
            self._requests.pop(name, None)

    def open_browser(self, name: str) -> bool:
        """Open the sign-in page on *this* computer (terminal UI)."""
        req = self.get(name)
        if req is None or req.status not in _LIVE:
            return False
        try:
            import webbrowser

            return bool(webbrowser.open(req.url))
        except Exception:
            return False


def _make_handler(coord: AuthCoordinator) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            return

        def _send(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != CALLBACK_PATH:
                self._send(404, _page("Not found", "Nothing to see here.", False))
                return
            qs = urllib.parse.parse_qs(parsed.query)
            ok, msg = coord.deliver(
                (qs.get("state") or [""])[0],
                code=(qs.get("code") or [""])[0],
                error=(qs.get("error") or [""])[0],
                desc=(qs.get("error_description") or [""])[0],
            )
            if ok:
                self._send(200, _page(f"Signed in to {msg}", "You can close this tab and go back to Jarvis.", True))
            else:
                self._send(400, _page("Sign-in didn't finish", msg, False))

    return Handler


coordinator = AuthCoordinator()


def preferred_port() -> int:
    try:
        return int(os.getenv("HARNESS_MCP_OAUTH_PORT", "") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


# ── provider for the SDK ──────────────────────────────────────────────────


def _fix_token_payload(data: Any) -> tuple[dict[str, Any] | None, str]:
    """Bring a token endpoint's answer into RFC 6749 shape. ``(payload, error)``.

    Providers that predate OAuth-for-MCP answer in their own dialect — Slack
    replies HTTP 200 with ``{"ok": false, "error": …}`` on failure and calls its
    user token ``token_type: "user"`` (the SDK only accepts ``Bearer``)."""
    if not isinstance(data, dict):
        return None, "the token endpoint didn't answer with JSON"
    if data.get("ok") is False or (data.get("error") and not data.get("access_token")):
        err = str(data.get("error_description") or data.get("error") or "unknown error")
        return None, err
    out = dict(data)
    if not out.get("access_token"):
        nested = out.get("authed_user") if isinstance(out.get("authed_user"), dict) else {}
        for key in ("access_token", "refresh_token", "expires_in", "scope"):
            if nested.get(key) and not out.get(key):
                out[key] = nested[key]
    if not out.get("access_token"):
        return None, "no access token in the answer"
    out["token_type"] = "Bearer"
    if isinstance(out.get("expires_in"), str) and out["expires_in"].isdigit():
        out["expires_in"] = int(out["expires_in"])
    return {k: out[k] for k in ("access_token", "token_type", "expires_in", "scope", "refresh_token") if out.get(k) is not None}, ""


async def _normalized(response: httpx.Response, *, refresh: bool = False) -> httpx.Response:
    if response.status_code != 200:
        return response
    body = await response.aread()
    try:
        data = json.loads(body.decode("utf-8") or "null")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return response
    fixed, err = _fix_token_payload(data)
    if fixed is None:
        if refresh:
            return httpx.Response(400, content=body, request=response.request)
        raise OAuthTokenError(f"sign-in was refused: {err}")
    return httpx.Response(
        200,
        content=json.dumps(fixed).encode(),
        headers={"content-type": "application/json"},
        request=response.request,
    )


class _Provider(OAuthClientProvider):
    """Same as the SDK's, plus:

    * a stored token's real expiry is honoured after a restart (so it refreshes
      instead of being sent expired and re-running the whole browser sign-in);
    * a pre-registered app can pin the scopes it asks for;
    * token answers in a provider's own dialect are accepted (``_fix_token_payload``).
    """

    forced_scope: str | None = None

    async def _initialize(self) -> None:
        await super()._initialize()
        expires = getattr(self.context.storage, "expires_at", lambda: None)()
        if expires and self.context.current_tokens:
            self.context.token_expiry_time = expires

    async def _perform_authorization(self) -> httpx.Request:
        if self.forced_scope:
            self.context.client_metadata.scope = self.forced_scope
        return await super()._perform_authorization()

    async def _handle_token_response(self, response: httpx.Response) -> None:
        await super()._handle_token_response(await _normalized(response))

    async def _handle_refresh_response(self, response: httpx.Response) -> bool:
        return await super()._handle_refresh_response(await _normalized(response, refresh=True))


def client_settings(oauth: Any) -> dict[str, Any] | None:
    """``{client_id, client_secret, port, scope}`` from a config's ``oauth`` block
    (already ``${VAR}``-expanded), or ``None`` for automatic registration."""
    if not isinstance(oauth, dict):
        return None
    low = {str(k).lower().replace("_", ""): v for k, v in oauth.items()}
    client_id = str(low.get("clientid") or "").strip()
    if not client_id or "${" in client_id:
        return None
    secret = str(low.get("clientsecret") or "").strip()
    if "${" in secret:
        secret = ""
    try:
        port = int(low.get("callbackport") or 0) or preferred_port()
    except (TypeError, ValueError):
        port = preferred_port()
    scopes = low.get("scopes") if low.get("scopes") is not None else low.get("scope")
    if isinstance(scopes, list):
        scopes = " ".join(str(x) for x in scopes)
    return {"client_id": client_id, "client_secret": secret, "port": port, "scope": str(scopes or "").strip()}


def build_provider(
    name: str,
    url: str,
    *,
    interactive: bool | Callable[[], bool] = True,
    coord: AuthCoordinator | None = None,
    client: dict[str, Any] | None = None,
) -> tuple[OAuthClientProvider, FileTokenStorage]:
    """``httpx`` auth for ``name`` at ``url``.

    ``interactive`` (a bool, or a callable read each time a sign-in is needed)
    false — a startup auto-connect — raises ``AuthNeeded`` instead of publishing
    a sign-in, so opening Jarvis never spawns sign-in requests.

    ``client`` is the server's ``oauth`` block: with a client id the SDK signs
    in as that pre-registered app (no dynamic registration) and the browser
    returns to its fixed ``callbackPort``.
    """
    coord = coord or coordinator
    app = client_settings(client)

    def allowed() -> bool:
        return bool(interactive()) if callable(interactive) else bool(interactive)

    redirect_uri = coord.redirect_uri(start=allowed(), port=app["port"] if app else None)
    if not redirect_uri:
        raise RuntimeError(f"can't listen for the sign-in callback ({coord._server_error or 'no port'})")
    static = None
    if app:
        static = OAuthClientInformationFull(
            client_id=app["client_id"],
            client_secret=app["client_secret"] or None,
            redirect_uris=[redirect_uri],  # type: ignore[list-item]
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post" if app["client_secret"] else "none",
        )
    storage = FileTokenStorage(name, url, redirect_uri, static_client=static)

    async def redirect_handler(auth_url: str) -> None:
        if not allowed():
            raise AuthNeeded(name)
        coord.begin(name, auth_url, redirect_uri)

    async def callback_handler() -> tuple[str, str | None]:
        req = coord.get(name)
        if req is None:
            raise AuthCancelled("Sign-in cancelled")
        # A daemon thread, not the loop's default executor: those threads are
        # joined at interpreter exit, which would hold Jarvis open for up to
        # ten minutes while a sign-in is still waiting.
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def settle(setter: Callable[[Any], None], value: Any) -> None:
            if not fut.done():
                setter(value)

        def work() -> None:
            try:
                got = coord.wait(req)
            except BaseException as exc:  # noqa: BLE001 — handed to the awaiting task
                loop.call_soon_threadsafe(settle, fut.set_exception, exc)
            else:
                loop.call_soon_threadsafe(settle, fut.set_result, got)

        threading.Thread(target=work, daemon=True, name=f"jarvis-mcp-signin-{name}").start()
        try:
            return await fut
        except asyncio.CancelledError:
            coord.cancel(name)  # frees the waiting thread
            raise

    metadata = OAuthClientMetadata(
        client_name="Jarvis",
        redirect_uris=[redirect_uri],  # type: ignore[list-item]
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    provider = _Provider(
        server_url=url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        timeout=FLOW_TTL,
    )
    if app and app["scope"]:
        provider.forced_scope = app["scope"]
    return provider, storage


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0
