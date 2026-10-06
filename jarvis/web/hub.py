"""One link for every running Jarvis: list the others and forward to them.

``GET /api/projects`` lists the live instances (``registry``). A page loaded
from ``/p/<id>/`` talks to that instance through this one: ``/p/<id>/api/x``
is forwarded to ``127.0.0.1:<its port>/api/x`` with *its* token swapped in, so
the browser only ever holds the token of the link it opened. Streams (SSE,
long-poll), uploads and byte-range media pass through unchanged.

Every instance can do this, so there is no hub to elect, nothing to take over
when a terminal closes, and a lone instance behaves exactly as before.
"""
from __future__ import annotations

import http.client
import json
import re
import time
import urllib.parse
from typing import TYPE_CHECKING, Any, Callable

from . import registry

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

_PROJECT_PATH = re.compile(r"^/p/([A-Za-z0-9_-]{1,40})(/.*)?$")
# Connection-scoped headers that must not be copied from one hop to the next.
_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
        "proxy-authenticate", "proxy-authorization", "server", "date"}
_FORWARD_REQUEST = ("Content-Type", "Content-Length", "Range", "X-File-Name", "Accept", "If-None-Match")
_UPSTREAM_TIMEOUT = 130.0  # provider key checks and uploads are the slow calls


def split_project_path(path: str) -> tuple[str, str] | None:
    """``/p/<id>/rest`` → ``(id, "/rest")``; ``/p/<id>`` → ``(id, "")``."""
    m = _PROJECT_PATH.match(path or "")
    if not m:
        return None
    return m.group(1), m.group(2) or ""


def project_rows(self_id: str | None) -> list[dict[str, Any]]:
    """One row per running Jarvis — no tokens or ports (safe to broadcast)."""
    rows: list[dict[str, Any]] = []
    now = time.time()
    for rec in registry.list_instances():
        since = rec.get("busy_since")
        rows.append({
            "id": rec.get("id"),
            "self": rec.get("id") == self_id,
            "project": rec.get("project") or "",
            "cwd": rec.get("cwd") or "",
            "session_id": rec.get("session_id"),
            "session_title": rec.get("session_title") or "",
            "model": rec.get("model") or "",
            "busy": bool(rec.get("busy")),
            # Seconds the running reply has taken (worked out here: every instance shares this clock, a phone may not).
            "busy_for": max(0, int(now - since)) if rec.get("busy") and isinstance(since, (int, float)) else None,
            "needs_approval": bool(rec.get("needs_approval")),
            "headless": bool(rec.get("headless")),  # opened from the web: the page may stop it
            "version": rec.get("version") or "",
        })
    return rows


def projects_payload(
    self_id: str | None, *, direct_host: str | None = None, link: str = "",
) -> dict[str, Any]:
    """What the page's project switcher shows.

    ``direct_host`` (the host name the browser used — only for a visit from
    this computer / the LAN, which may already be handed this link's token)
    adds each other project's own link, so a page can move over when the
    terminal it came through is closed. ``link`` is this server's shareable link.
    """
    rows = project_rows(self_id)
    if direct_host:
        by_id = {rec.get("id"): rec for rec in registry.list_instances(prune=False)}
        for row in rows:
            rec = by_id.get(row["id"])
            if rec and not row["self"]:
                row["link"] = f"http://{direct_host}:{int(rec.get('port') or 0)}/?token={rec.get('token') or ''}"
    return {"self": self_id or "", "link": link, "projects": rows}


def _bad_gateway(handler: BaseHTTPRequestHandler, message: str) -> None:
    body = json.dumps({"error": message}).encode("utf-8")
    handler.close_connection = True
    handler.send_response(502)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except OSError:
        pass


def forward(
    handler: BaseHTTPRequestHandler,
    record: dict[str, Any],
    rest: str,
    *,
    json_error: Callable[[BaseHTTPRequestHandler, str], None] = _bad_gateway,
) -> None:
    """Send this request to ``record``'s instance and stream its answer back."""
    parsed = urllib.parse.urlparse(rest)
    path = parsed.path
    if not path.startswith("/api/") or ".." in path:
        handler.send_response(404)
        handler.end_headers()
        return
    token = str(record.get("token") or "")
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if k != "token"]
    query.append(("token", token))
    target = f"{path}?{urllib.parse.urlencode(query)}"

    headers = {name: handler.headers[name] for name in _FORWARD_REQUEST if handler.headers.get(name)}
    headers["Authorization"] = f"Bearer {token}"
    method = handler.command
    length = int(handler.headers.get("Content-Length") or 0) if method == "POST" else 0

    conn = http.client.HTTPConnection("127.0.0.1", int(record.get("port") or 0), timeout=_UPSTREAM_TIMEOUT)
    try:
        try:
            conn.putrequest(method, target, skip_host=False, skip_accept_encoding=True)
            for name, value in headers.items():
                conn.putheader(name, value)
            conn.endheaders()
            remaining = length
            while remaining > 0:
                chunk = handler.rfile.read(min(1 << 16, remaining))
                if not chunk:
                    break
                conn.send(chunk)
                remaining -= len(chunk)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException):
            json_error(handler, "That project is no longer running.")
            return

        handler.send_response(resp.status)
        for name, value in resp.getheaders():
            if name.lower() not in _HOP:
                handler.send_header(name, value)
        handler.send_header("Connection", "close")
        handler.close_connection = True  # the body may run until the upstream closes (streams)
        handler.end_headers()
        try:
            while True:
                chunk = resp.read1(1 << 16)
                if not chunk:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
        except (OSError, http.client.HTTPException):
            pass  # the browser left, or the project stopped mid-stream
    finally:
        conn.close()
