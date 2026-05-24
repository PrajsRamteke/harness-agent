"""Tests for web remote port selection."""
import socket

from jarvis.web.server import resolve_web_port


def test_resolve_web_port_skips_busy_port():
    preferred = 28765
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", preferred))
        blocker.listen(1)
        resolved = resolve_web_port("127.0.0.1", preferred, max_tries=5)
    assert resolved == preferred + 1
