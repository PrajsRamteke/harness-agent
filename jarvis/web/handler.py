"""HTTP request handler for Jarvis web remote (API + static assets)."""
from __future__ import annotations

import json
import mimetypes
import queue
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bridge import WebBridge
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
_INDEX_PATH = _STATIC_DIR / "index.html"

_MIME = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}


class WebHandler(BaseHTTPRequestHandler):
    bridge: WebBridge
    app: JarvisTUI | None = None

    def log_message(self, *_args: Any) -> None:
        return

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip() == self.bridge.token
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        token = (qs.get("token") or [""])[0]
        return token == self.bridge.token

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
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
        return snapshot_from_state(busy=self._busy())

    def _parse_query(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)
        return path, qs

    def _query_str(self, qs: dict[str, list[str]], key: str, default: str = "") -> str:
        return (qs.get(key) or [default])[0]

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
        self.send_header("Cache-Control", "no-cache")
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
                    line = sub.get(timeout=15.0)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.bridge.unsubscribe(sub)

    def _handle_api_get(self, path: str, qs: dict[str, list[str]]) -> None:
        if path == "/api/sessions":
            limit = int(self._query_str(qs, "limit", "50") or 50)
            offset = int(self._query_str(qs, "offset", "0") or 0)
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
                self._send_json(400, {"error": "missing action"})
                return
            payload = data.get("data")
            if not isinstance(payload, dict):
                payload = {k: v for k, v in data.items() if k != "action"}
            result = self.bridge.request_action(action, payload)
            status = 200 if result.get("ok") else 400
            body = dict(result)
            body["state"] = self._snapshot()
            if result.get("ok") and action.startswith("session_"):
                self.bridge.emit("snapshot", body["state"])
            elif result.get("ok") and action in ("model_select", "agent_select", "session_new"):
                self.bridge.emit("snapshot", body["state"])
            return self._send_json(status, body)

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
                payload = json.dumps(result, ensure_ascii=False)
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
                self._send_json(401, {"error": "missing or invalid token"})
                return
            self._serve_index()
            return

        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return

        if path == "/api/events":
            self._stream_events()
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
