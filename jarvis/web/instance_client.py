"""Call another running Jarvis's web API from this process (UI-free).

``hub.forward`` streams a *browser's* request through to another instance. This
is the server-side sibling: the workspace summary, the cross-project terminal
list and ``ask_project`` need to *ask* another Jarvis something and read the
JSON answer. Same trust model as ``hub.forward`` — the instance's loopback port
and token come from ``registry``, nothing leaves 127.0.0.1.
"""
from __future__ import annotations

import http.client
import json
import urllib.parse
from typing import Any

from . import registry

DEFAULT_TIMEOUT = 10.0


class InstanceError(Exception):
    """The instance is gone, unreachable or answered something that isn't JSON."""


def call(
    instance_id: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    forwarded_for: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """``(status, json)`` from ``instance_id``'s ``/api/…`` route.

    ``forwarded_for``: set it (to the visitor's address) when the request being served
    didn't come direct, so the other instance refuses local-only features too.
    """
    rec = registry.get(instance_id)
    if rec is None:
        raise InstanceError("That project is no longer running.")
    if not path.startswith("/api/") or ".." in path:
        raise InstanceError("Only /api/ routes can be called.")
    token = str(rec.get("token") or "")
    parsed = urllib.parse.urlparse(path)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if k != "token"]
    query.append(("token", token))
    target = f"{parsed.path}?{urllib.parse.urlencode(query)}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    payload: bytes | None = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(payload))
    if forwarded_for:
        headers["X-Forwarded-For"] = forwarded_for
    conn = http.client.HTTPConnection("127.0.0.1", int(rec.get("port") or 0), timeout=timeout)
    try:
        conn.request(method.upper(), target, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
    except (OSError, http.client.HTTPException) as exc:
        raise InstanceError("That project didn't answer.") from exc
    finally:
        conn.close()
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError as exc:
        raise InstanceError("That project sent an unreadable answer.") from exc
    return resp.status, data if isinstance(data, dict) else {"data": data}


def get_json(instance_id: str, path: str, **kw: Any) -> dict[str, Any]:
    status, data = call(instance_id, "GET", path, **kw)
    if status >= 400 and "ok" not in data:
        data = {"ok": False, "error": str(data.get("error") or f"HTTP {status}")}
    return data


def post_json(instance_id: str, path: str, body: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    status, data = call(instance_id, "POST", path, body or {}, **kw)
    if status >= 400 and "ok" not in data:
        data = {"ok": False, "error": str(data.get("error") or f"HTTP {status}")}
    return data
