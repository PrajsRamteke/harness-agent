"""Localhost OAuth callback server for OpenAI Codex (ChatGPT) login."""
from __future__ import annotations

import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Optional


class CodexOAuthCallbackError(Exception):
    pass


def _success_html() -> bytes:
    return (
        b"<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2rem'>"
        b"<h2>Signed in</h2><p>You can close this tab and return to Jarvis.</p>"
        b"</body></html>"
    )


def _error_html(message: str) -> bytes:
    safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f"<!DOCTYPE html><html><body style='font-family:sans-serif;padding:2rem'>"
        f"<h2>Sign-in failed</h2><p>{safe}</p></body></html>"
    ).encode()


def wait_for_codex_oauth_callback(
    *,
    expected_state: str,
    port: int,
    timeout: float = 300.0,
    on_ready: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    """Block until browser hits ``/auth/callback``; return ``(code, state)``."""
    result: dict[str, object] = {"done": False}
    redirect_uri = f"http://localhost:{port}/auth/callback"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            return

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/auth/callback":
                self.send_response(404)
                self.end_headers()
                return
            params = urllib.parse.parse_qs(parsed.query)
            state = (params.get("state") or [""])[0]
            if state != expected_state:
                result["error"] = CodexOAuthCallbackError("OAuth state mismatch")
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_error_html("State mismatch — restart sign-in in Jarvis."))
                result["done"] = True
                return
            if (params.get("error") or [""])[0]:
                err = (params.get("error") or [""])[0]
                desc = (params.get("error_description") or [err])[0]
                result["error"] = CodexOAuthCallbackError(desc)
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_error_html(desc))
                result["done"] = True
                return
            code = (params.get("code") or [""])[0]
            if not code:
                result["error"] = CodexOAuthCallbackError("Missing authorization code")
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_error_html("Missing authorization code."))
                result["done"] = True
                return
            result["code"] = code
            result["state"] = state
            result["done"] = True
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_success_html())

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True

    server = ReusableHTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 0.5
    ready = threading.Event()

    def _serve() -> None:
        ready.set()
        end = time.monotonic() + timeout
        while time.monotonic() < end and not result.get("done"):
            server.handle_request()
        server.server_close()

    if on_ready:
        on_ready(redirect_uri)

    worker = threading.Thread(target=_serve, daemon=True)
    worker.start()
    if not ready.wait(timeout=2.0):
        server.server_close()
        raise CodexOAuthCallbackError("callback server failed to start")

    worker.join(timeout=max(0.0, timeout))
    if not result.get("done"):
        raise CodexOAuthCallbackError("Timed out waiting for browser sign-in")
    if result.get("error"):
        raise result["error"]  # type: ignore[misc]
    return str(result["code"]), str(result["state"])


def pick_codex_callback_port(preferred: int = 1455) -> int:
    """Return ``preferred`` if bindable, else raise with a helpful message."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", preferred))
        except OSError as e:
            raise CodexOAuthCallbackError(
                f"localhost:{preferred} is in use — quit Codex CLI or free the port, then retry"
            ) from e
    return preferred
