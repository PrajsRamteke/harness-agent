import socket
import unittest
from unittest import mock

from jarvis.browser_bridge.server import ensure_browser_bridge, start_browser_bridge


class BrowserBridgeStartupTests(unittest.TestCase):
    def setUp(self):
        import jarvis.browser_bridge.server as mod

        if mod._SERVER is not None:
            mod._SERVER.shutdown()
            mod._SERVER.server_close()
        mod._SERVER = None
        mod._THREAD = None
        mod._STATE.set_client(None)
        mod._STATE.last_hello = {}

    def tearDown(self):
        self.setUp()

    def test_ensure_browser_bridge_starts_on_free_port(self):
        with mock.patch("jarvis.state.browser_bridge_enabled", True):
            server = ensure_browser_bridge(port=0)
        self.assertIsNotNone(server)
        host, port = server.server_address  # type: ignore[union-attr]
        self.assertEqual(host, "127.0.0.1")
        sock = socket.create_connection((host, port), timeout=1)
        sock.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        data = sock.recv(4096).decode("utf-8", errors="replace")
        sock.close()
        self.assertIn("harness-browser-bridge", data)

    def test_ensure_browser_bridge_respects_disabled_flag(self):
        server = ensure_browser_bridge(enabled=False)
        self.assertIsNone(server)

    def test_start_browser_bridge_is_idempotent(self):
        first = start_browser_bridge(port=0)
        second = start_browser_bridge(port=0)
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
