"""Local HTTP server for Jarvis web remote control."""
from __future__ import annotations

import errno
import os
import socket
import threading
from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Callable

from .. import file_changes
from . import registry
from .bridge import WebBridge
from .handler import WebHandler
from .sync import StateWatcher

if TYPE_CHECKING:
    from ..tui.app import JarvisTUI


class _JarvisHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    # socketserver's default listen backlog is 5. The page loads ~20 ES
    # modules at once (plus the SSE stream), and on macOS connections past
    # the backlog are reset (net::ERR_CONNECTION_RESET) — the app never boots.
    request_queue_size = 128


def resolve_web_port(host: str, preferred: int, *, max_tries: int = 20) -> int:
    """Return the first bindable port starting at ``preferred``."""
    last_err: OSError | None = None
    for offset in range(max_tries):
        port = preferred + offset
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
            return port
        except OSError as exc:
            last_err = exc
            continue
    detail = f"no free port in range {preferred}-{preferred + max_tries - 1}"
    if last_err is not None:
        raise OSError(last_err.errno or errno.EADDRINUSE, detail) from last_err
    raise OSError(errno.EADDRINUSE, detail)


def _local_urls(port: int, token: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(host: str) -> None:
        url = f"http://{host}:{port}/?token={token}"
        if url not in seen:
            seen.add(url)
            urls.append(url)

    add("127.0.0.1")
    add("localhost")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            add(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    return urls


def _instance_info(app: Any, bridge: WebBridge) -> Callable[[], dict[str, Any]]:
    """What the project switcher shows about this instance (read every few seconds)."""
    cache: dict[Any, str] = {}

    def info() -> dict[str, Any]:
        from .. import state
        from . import state_api

        key = (state.current_session_id, len(state.messages))
        if key not in cache:  # the title only changes with the session or a new message
            cache.clear()
            cache[key] = state_api._session_title(state.current_session_id)
        return {
            "cwd": os.getcwd(),
            "project": state_api._project_name(),
            "session_id": state.current_session_id,
            "session_title": cache[key],
            "model": str(state.MODEL or ""),
            "busy": bool(getattr(app, "_busy", False)),
            "needs_approval": bool(bridge.pending_events()),
        }

    return info


def start_web_server(
    *,
    bridge: WebBridge,
    app: JarvisTUI,
    port: int,
    host: str = "0.0.0.0",
    instance_id: str | None = None,
) -> tuple[_JarvisHTTPServer, list[str], int]:
    instance_id = instance_id or str(os.getpid())
    handler = type(
        "JarvisWebHandler",
        (WebHandler,),
        {"bridge": bridge, "app": app, "instance_id": instance_id},
    )
    bound_port = resolve_web_port(host, port)
    server = _JarvisHTTPServer((host, bound_port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="jarvis-web")
    thread.start()
    from . import hub

    watcher = StateWatcher(
        bridge, busy=lambda: bool(getattr(app, "_busy", False)),
        projects=lambda: hub.project_rows(instance_id),
    )
    watcher.start()
    server.state_watcher = watcher  # type: ignore[attr-defined]

    def push_change(fid: str) -> None:
        # Runs on the tool's thread right after it wrote the file; the diff is
        # only worth building while a browser is watching.
        if bridge.has_subscribers():
            bridge.emit("change", file_changes.change_event(fid))

    file_changes.subscribe(push_change)
    server.change_listener = push_change  # type: ignore[attr-defined]

    def push_mcp(event: str, name: str) -> None:
        # A server connected / failed / needs its sign-in — from a tool call, the
        # terminal, or a browser sign-in finishing on its own.
        if bridge.has_subscribers():
            bridge.emit("mcp", {"event": event, "name": name})

    from ..mcp.registry import mcp_registry

    mcp_registry.add_listener(push_mcp)
    server.mcp_listener = push_mcp  # type: ignore[attr-defined]
    # Old attachments (never sent: a day; sent: HARNESS_UPLOAD_KEEP_DAYS).
    from .. import media

    media.prune_in_background()
    # Listed for the project switcher: every page of every running Jarvis
    # reaches this one through its own link.
    registration = registry.Registration(
        instance_id=instance_id, port=bound_port, token=bridge.token,
        info=_instance_info(app, bridge),
    )
    registration.start()
    server.registration = registration  # type: ignore[attr-defined]
    urls = _local_urls(bound_port, bridge.token)
    return server, urls, bound_port


def stop_web_server(server: _JarvisHTTPServer | None, bridge: WebBridge | None) -> None:
    """Shut the remote down: end SSE streams, stop syncing, free the port."""
    if bridge is not None:
        bridge.close()
    if server is None:
        return
    registration = getattr(server, "registration", None)
    if registration is not None:
        registration.stop()
    watcher = getattr(server, "state_watcher", None)
    if watcher is not None:
        watcher.stop()
    listener = getattr(server, "change_listener", None)
    if listener is not None:
        file_changes.unsubscribe(listener)
    mcp_listener = getattr(server, "mcp_listener", None)
    if mcp_listener is not None:
        from ..mcp.registry import mcp_registry

        mcp_registry.remove_listener(mcp_listener)
    server.shutdown()      # waits for serve_forever's loop (≤ poll interval)
    server.server_close()


def primary_remote_url(urls: list[str]) -> str:
    """Prefer LAN address for phone access; fall back to localhost."""
    for url in urls:
        if "127.0.0.1" not in url and "localhost" not in url:
            return url
    return urls[0] if urls else ""


def default_web_port() -> int:
    raw = os.environ.get("HARNESS_WEB_PORT", "8765").strip()
    try:
        return int(raw)
    except ValueError:
        return 8765


def web_enabled_from_env() -> bool:
    raw = os.environ.get("HARNESS_WEB", "").strip().lower()
    return raw in ("1", "true", "yes", "on")
