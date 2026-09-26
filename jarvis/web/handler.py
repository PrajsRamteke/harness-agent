"""HTTP request handler for Jarvis web remote (API + static assets)."""
from __future__ import annotations

import hmac
import ipaddress
import json
import mimetypes
import queue
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bridge import CLOSE_SENTINEL, WebBridge
from .pickers_api import (
    get_skill,
    list_agents,
    list_mcp_servers,
    list_models,
    list_sessions,
    list_skills,
)
from .state_api import snapshot_from_state

if TYPE_CHECKING:
    from ..tui.app import JarvisTUI

_STATIC_DIR = Path(__file__).with_name("static")
_HEARTBEAT_SECS = 8.0
_POLL_WAIT_SECS = 20.0
_PING = b'data: {"type": "ping", "data": {}, "ts": 0}\n\n'
_INDEX_PATH = _STATIC_DIR / "index.html"

_MIME = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}


def _normalize_answers(result: dict[str, Any]) -> dict[str, Any]:
    """Accept ``selected`` (older web clients) as ``selected_ids``.

    ``ask_user_question`` consumers (plan approval, the tool result) read
    ``selected_ids`` / ``labels`` — the same shape the TUI askbar returns.
    """
    answers = []
    for ans in result.get("answers") or []:
        if not isinstance(ans, dict):
            continue
        ans = dict(ans)
        if "selected_ids" not in ans and isinstance(ans.get("selected"), list):
            ans["selected_ids"] = ans.pop("selected")
        ans.setdefault("selected_ids", [])
        ans.setdefault("labels", [])
        answers.append(ans)
    out = dict(result)
    out["answers"] = answers
    return out


class WebHandler(BaseHTTPRequestHandler):
    bridge: WebBridge
    app: JarvisTUI | None = None

    def log_message(self, *_args: Any) -> None:
        return

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return self._token_ok(auth[7:].strip())
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        token = (qs.get("token") or [""])[0]
        return self._token_ok(token)

    # Headers a reverse proxy / tunnel adds. Their presence means the request
    # did not come straight from a browser on this network.
    _PROXY_HEADERS = (
        "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Forwarded",
        "X-Real-IP", "Cf-Connecting-Ip", "Cf-Ray", "Cdn-Loop", "Ngrok-Trace-Id",
    )

    def _may_hand_out_token(self) -> bool:
        """Only a direct visit from this computer or the LAN gets the token.

        A tunnel (cloudflared / ngrok) connects from 127.0.0.1 too, so the
        client address alone can't tell — proxy headers and the tunnel's
        public Host can.
        """
        for name in self._PROXY_HEADERS:
            if self.headers.get(name):
                return False
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        public_host = (getattr(self.bridge, "public_host", "") or "").lower()
        if public_host and host == public_host:
            return False
        if host.endswith((".trycloudflare.com", ".ngrok-free.app", ".ngrok.app", ".ngrok.io", ".ngrok.dev")):
            return False
        try:
            ip = ipaddress.ip_address((self.client_address or ("",))[0])
        except ValueError:
            return False
        return ip.is_loopback or ip.is_private or ip.is_link_local

    def _token_ok(self, token: str) -> bool:
        return hmac.compare_digest(token.encode("utf-8"), self.bridge.token.encode("utf-8"))

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Assets change with every upgrade and are tiny — always revalidate.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _busy(self) -> bool:
        return bool(getattr(self.app, "_busy", False)) if self.app else False

    def _snapshot(self) -> dict[str, Any]:
        snap = snapshot_from_state(busy=self._busy())
        # LAN link (with token) so "Copy link" works on other devices too.
        snap["remote_url"] = str(
            getattr(self.app, "_web_public_link", "")
            or getattr(self.app, "_web_primary_url", "")
            or ""
        )
        return snap

    def _parse_query(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)
        return path, qs

    def _query_str(self, qs: dict[str, list[str]], key: str, default: str = "") -> str:
        return (qs.get(key) or [default])[0]

    def _query_int(self, qs: dict[str, list[str]], key: str, default: int) -> int:
        try:
            return int(self._query_str(qs, key, str(default)) or default)
        except ValueError:
            return default

    def _serve_index(self) -> None:
        if not _INDEX_PATH.is_file():
            self._send_json(500, {"error": "index.html missing"})
            return
        self._send_bytes(200, _INDEX_PATH.read_bytes(), "text/html; charset=utf-8")

    def _serve_static(self, path: str) -> None:
        if not path.startswith("/static/"):
            self.send_response(404)
            self.end_headers()
            return
        rel = path[len("/static/"):]
        if ".." in rel or rel.startswith("/"):
            self.send_response(403)
            self.end_headers()
            return
        file_path = (_STATIC_DIR / rel).resolve()
        if not str(file_path).startswith(str(_STATIC_DIR.resolve())):
            self.send_response(403)
            self.end_headers()
            return
        if not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        ext = file_path.suffix.lower()
        mime = _MIME.get(ext) or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self._send_bytes(200, file_path.read_bytes(), mime)

    def _stream_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        # no-transform: a tunnel / CDN (Cloudflare) must not compress or buffer
        # the stream — events have to reach the browser as they happen.
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        snap = self._snapshot()
        hello = json.dumps({"type": "snapshot", "data": snap, "ts": 0}, ensure_ascii=False)
        self.wfile.write(f"data: {hello}\n\n".encode("utf-8"))
        for evt in self.bridge.pending_events():
            line = json.dumps(evt, ensure_ascii=False)
            self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
        self.wfile.flush()

        sub = self.bridge.subscribe()
        try:
            while True:
                try:
                    line = sub.get(timeout=_HEARTBEAT_SECS)
                except queue.Empty:
                    # A real event, not an SSE comment: the page watches for
                    # silence to spot connections that died without an error
                    # (laptop sleep, phone lock, Wi-Fi switch) and reconnects.
                    self.wfile.write(_PING)
                    self.wfile.flush()
                    continue
                if line == CLOSE_SENTINEL:
                    break
                self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.bridge.unsubscribe(sub)

    def _poll_events(self, qs: dict[str, list[str]]) -> None:
        """Long-poll transport — for tunnels that hold SSE back until it ends.

        No ``cursor``: the snapshot (+ pending prompts) and the cursor to
        continue from. With ``cursor``: events after it, waiting up to 20 s.
        """
        client_id = self._query_str(qs, "cid")[:64]
        raw = self._query_str(qs, "cursor").strip()
        if not raw:
            self.bridge.touch_poller(client_id)
            snap = self._snapshot()
            cursor = self.bridge.latest_seq()
            events = [{"type": "snapshot", "data": snap, "ts": 0}, *self.bridge.pending_events()]
            body = json.dumps({"cursor": cursor, "events": events}, ensure_ascii=False)
        else:
            try:
                cursor = int(raw)
            except ValueError:
                cursor = -1
            body = self.bridge.poll(cursor, timeout=_POLL_WAIT_SECS, client_id=client_id)
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-transform")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _handle_api_get(self, path: str, qs: dict[str, list[str]]) -> None:
        if path == "/api/sessions":
            limit = self._query_int(qs, "limit", 50)
            offset = self._query_int(qs, "offset", 0)
            self._send_json(200, list_sessions(limit=limit, offset=offset))
            return
        if path == "/api/models":
            self._send_json(200, list_models(query=self._query_str(qs, "q")))
            return
        if path == "/api/agents":
            raw = self._query_str(qs, "include_global", "")
            include = None if raw == "" else raw.lower() in ("1", "true", "yes")
            self._send_json(200, list_agents(include_global=include))
            return
        if path == "/api/skills":
            raw = self._query_str(qs, "include_global", "")
            include = None if raw == "" else raw.lower() in ("1", "true", "yes")
            self._send_json(200, list_skills(include_global=include, query=self._query_str(qs, "q")))
            return
        if path.startswith("/api/skills/") and path != "/api/skills":
            name = urllib.parse.unquote(path[len("/api/skills/"):])
            payload = get_skill(name)
            if payload is None:
                self._send_json(404, {"error": "skill not found"})
                return
            self._send_json(200, payload)
            return
        if path == "/api/mcp":
            self._send_json(200, list_mcp_servers(query=self._query_str(qs, "q")))
            return
        self.send_response(404)
        self.end_headers()

    def _handle_api_post(self, path: str, data: dict[str, Any]) -> None:
        if path == "/api/action":
            action = str(data.get("action") or "").strip()
            if not action:
                self._send_json(400, {"ok": False, "error": "missing action"})
                return
            payload = data.get("data")
            if not isinstance(payload, dict):
                payload = {k: v for k, v in data.items() if k != "action"}
            result = self.bridge.request_action(action, payload)
            if not isinstance(result, dict):
                result = {"ok": False, "error": "invalid action response"}
            body = dict(result)
            try:
                body["state"] = self._snapshot()
            except Exception as exc:
                body["state"] = {}
                if body.get("ok"):
                    body["snapshot_error"] = str(exc)
            if body.get("ok") and body.get("state"):
                self.bridge.emit("snapshot", body["state"])
            return self._send_json(200, body)

        if path == "/api/prompt":
            text = str(data.get("text") or "").strip()
            if not text:
                self._send_json(400, {"error": "empty prompt"})
                return
            ok = self.bridge.submit_prompt(text)
            self._send_json(200 if ok else 503, {"ok": ok})
            return

        if path == "/api/cancel":
            ok = self.bridge.cancel_turn()
            self._send_json(200, {"ok": ok})
            return

        if path == "/api/respond":
            prompt_id = str(data.get("id") or "").strip()
            if not prompt_id:
                self._send_json(400, {"error": "missing id"})
                return
            result = data.get("result")
            if isinstance(result, dict) and "answers" in result:
                payload = json.dumps(_normalize_answers(result), ensure_ascii=False)
            elif result is None:
                payload = "n"
            else:
                payload = str(result)
            ok = self.bridge.resolve_prompt(prompt_id, payload)
            self._send_json(200 if ok else 404, {"ok": ok})
            return

        if path == "/api/settings":
            updated = self.bridge.request_settings(data)
            if updated:
                self.bridge.emit("settings", updated)
            self._send_json(200, {"ok": True, "settings": self._snapshot()})
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path, qs = self._parse_query()

        if path.startswith("/static/"):
            self._serve_static(path)
            return

        if path == "/":
            if not self._authorized():
                if self._may_hand_out_token():
                    # Same network: redirect so the local QR can omit the token.
                    self.send_response(302)
                    self.send_header("Location", f"/?token={self.bridge.token}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                # Through a tunnel / from outside: the page shows "open the
                # link from your terminal" — it never learns the token.
            self._serve_index()
            return

        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return

        if path == "/api/events":
            self._stream_events()
            return

        if path == "/api/poll":
            self._poll_events(qs)
            return

        if path == "/api/state":
            self._send_json(200, self._snapshot())
            return

        if path.startswith("/api/"):
            self._handle_api_get(path, qs)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        path, _qs = self._parse_query()

        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return

        data = self._read_json()

        if path.startswith("/api/"):
            self._handle_api_post(path, data)
            return

        self.send_response(404)
        self.end_headers()
