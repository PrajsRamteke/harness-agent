import json
import threading
import urllib.request

from jarvis.browser_bridge.server import BrowserBridgeServer, BrowserBridgeState


def test_browser_bridge_health_endpoint():
    state = BrowserBridgeState()
    server = BrowserBridgeServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["ok"] is True
        assert payload["service"] == "harness-browser-bridge"
        assert payload["extension_connected"] is False
    finally:
        server.shutdown()
        server.server_close()
