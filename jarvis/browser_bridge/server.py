"""Minimal local WebSocket server for the Harness browser extension."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import socket
import socketserver
import threading
import uuid
from dataclasses import dataclass
from typing import Any


GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


@dataclass
class PendingCall:
    request_id: str
    result: queue.Queue


class BrowserBridgeState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.client: socket.socket | None = None
        self.pending: dict[str, PendingCall] = {}
        self.connected = False
        self.last_hello: dict[str, Any] = {}

    def clear_client(self, conn: socket.socket) -> None:
        """Drop the client only if it is still the active socket.

        A reconnecting extension can open a new socket before the old socket's
        handler thread finishes its read loop. Without this guard the old
        handler's cleanup would wipe the freshly-attached client and leave the
        bridge falsely 'disconnected'.
        """
        with self.lock:
            if self.client is not conn:
                return
        self.set_client(None)

    def set_client(self, conn: socket.socket | None) -> None:
        with self.lock:
            self.client = conn
            self.connected = conn is not None
            if conn is None:
                for call in self.pending.values():
                    try:
                        call.result.put_nowait({"error": "browser extension disconnected"})
                    except queue.Full:
                        pass
                self.pending.clear()

    def send_json(self, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        frame = _encode_frame(raw)
        with self.lock:
            if self.client is None:
                raise RuntimeError("browser extension is not connected")
            self.client.sendall(frame)

    def call_tool(self, name: str, args: dict[str, Any], timeout: float = 60.0) -> Any:
        request_id = uuid.uuid4().hex
        call = PendingCall(request_id=request_id, result=queue.Queue(maxsize=1))
        with self.lock:
            self.pending[request_id] = call
        try:
            self.send_json({
                "type": "tool_call",
                "requestId": request_id,
                "payload": {"name": name, "args": args},
            })
            result = call.result.get(timeout=timeout)
        except queue.Empty:
            result = {"error": f"browser tool '{name}' timed out"}
        finally:
            with self.lock:
                self.pending.pop(request_id, None)
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(str(result["error"]))
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        return result

    def handle_message(self, message: dict[str, Any]) -> None:
        mtype = message.get("type")
        if mtype == "hello":
            self.last_hello = message.get("payload") or {}
            self.send_json({"type": "hello_ack"})
            return
        if mtype == "ping":
            self.send_json({"type": "pong"})
            return
        if mtype != "tool_result":
            return
        request_id = str(message.get("responseToRequestId") or "")
        payload = message.get("payload") or {}
        with self.lock:
            call = self.pending.get(request_id)
        if call:
            try:
                call.result.put_nowait(payload)
            except queue.Full:
                pass


class _BridgeHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: BrowserBridgeServer = self.server  # type: ignore[assignment]
        if not _accept_connection(self.request, server.bridge_state):
            return
        server.bridge_state.set_client(self.request)
        try:
            while True:
                payload = _read_frame(self.request)
                if payload is None:
                    break
                try:
                    message = json.loads(payload.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    server.bridge_state.handle_message(message)
        except (OSError, ConnectionError):
            # Abrupt disconnect (Jarvis/extension closed, network reset) — a
            # normal end of session, not an error worth a traceback.
            pass
        finally:
            server.bridge_state.clear_client(self.request)


class BrowserBridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: BrowserBridgeState) -> None:
        self.bridge_state = state
        super().__init__(address, _BridgeHandler)


_STATE = BrowserBridgeState()
_SERVER: BrowserBridgeServer | None = None
_THREAD: threading.Thread | None = None


def bridge_state() -> BrowserBridgeState:
    return _STATE


def browser_bridge_port() -> int:
    raw = os.environ.get("HARNESS_BROWSER_BRIDGE_PORT", "10086")
    try:
        return int(raw)
    except ValueError:
        return 10086


def start_browser_bridge(host: str = "127.0.0.1", port: int | None = None) -> BrowserBridgeServer:
    global _SERVER, _THREAD
    if _SERVER is not None:
        return _SERVER
    bind_port = browser_bridge_port() if port is None else port
    _SERVER = BrowserBridgeServer((host, bind_port), _STATE)
    _THREAD = threading.Thread(
        target=_SERVER.serve_forever,
        name="jarvis-browser-bridge",
        daemon=True,
    )
    _THREAD.start()
    return _SERVER


def ensure_browser_bridge(
    host: str = "127.0.0.1",
    port: int | None = None,
    *,
    enabled: bool | None = None,
) -> BrowserBridgeServer | None:
    """Start the local WebSocket bridge once per process when enabled."""
    if enabled is None:
        from .. import state

        enabled = state.browser_bridge_enabled
    if not enabled:
        return None
    try:
        server = start_browser_bridge(host, port)
    except OSError:
        return None
    from .. import state

    state.browser_bridge_port = int(server.server_address[1])
    return server


def _accept_connection(conn: socket.socket, state: BrowserBridgeState) -> bool:
    data = conn.recv(8192).decode("utf-8", errors="replace")
    if data.startswith("GET /health "):
        _send_health(conn, state)
        return False
    if "Upgrade: websocket" not in data:
        _send_http(conn, 404, {"ok": False, "error": "not found"})
        return False
    key = ""
    for line in data.splitlines():
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
            break
    if not key:
        return False
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    )
    conn.sendall(response.encode("ascii"))
    return True


def _send_health(conn: socket.socket, state: BrowserBridgeState) -> None:
    _send_http(conn, 200, {
        "ok": True,
        "service": "harness-browser-bridge",
        "extension_connected": state.connected,
    })


def _send_http(conn: socket.socket, status: int, payload: dict[str, Any]) -> None:
    reason = "OK" if status == 200 else "Not Found"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + body
    try:
        conn.sendall(response)
    except OSError:
        pass


def _read_exact(conn: socket.socket, n: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < n:
        chunk = conn.recv(n - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


def _read_frame(conn: socket.socket) -> bytes | None:
    header = _read_exact(conn, 2)
    if not header:
        return None
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F
    if length == 126:
        length = int.from_bytes(_read_exact(conn, 2) or b"", "big")
    elif length == 127:
        length = int.from_bytes(_read_exact(conn, 8) or b"", "big")
    mask = _read_exact(conn, 4) if masked else b""
    payload = _read_exact(conn, length) or b""
    if opcode == 8:
        return None
    if masked and mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return payload


def _encode_frame(payload: bytes) -> bytes:
    size = len(payload)
    if size < 126:
        header = bytes([0x81, size])
    elif size < 65536:
        header = bytes([0x81, 126]) + size.to_bytes(2, "big")
    else:
        header = bytes([0x81, 127]) + size.to_bytes(8, "big")
    return header + payload
