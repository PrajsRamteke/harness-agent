"""Local HTTP server for Jarvis web remote control."""
from __future__ import annotations

import errno
import os
import socket
import threading
from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING

from .bridge import WebBridge
from .handler import WebHandler

if TYPE_CHECKING:
    from ..tui.app import JarvisTUI


class _JarvisHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


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


def start_web_server(
    *,
    bridge: WebBridge,
    app: JarvisTUI,
    port: int,
    host: str = "0.0.0.0",
) -> tuple[_JarvisHTTPServer, list[str], int]:
    handler = type(
        "JarvisWebHandler",
        (WebHandler,),
        {"bridge": bridge, "app": app},
    )
    bound_port = resolve_web_port(host, port)
    server = _JarvisHTTPServer((host, bound_port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="jarvis-web")
    thread.start()
    urls = _local_urls(bound_port, bridge.token)
    return server, urls, bound_port


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
