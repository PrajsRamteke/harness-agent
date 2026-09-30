"""Web remote: the QR code dialog's endpoint and markup."""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from jarvis.web.bridge import WebBridge
from jarvis.web.qr_ascii import qr_svg
from jarvis.web.server import start_web_server, stop_web_server

STATIC = Path(__file__).resolve().parents[1] / "jarvis" / "web" / "static"


@pytest.fixture
def remote():
    bridge = WebBridge()
    server, _urls, port = start_web_server(bridge=bridge, app=None, port=28932, host="127.0.0.1")

    def get(path, token=True):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
        if token:
            req.add_header("Authorization", f"Bearer {bridge.token}")
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, res.headers.get("Content-Type"), res.read().decode()

    yield get
    stop_web_server(server, bridge)


def test_qr_svg_is_standalone_svg():
    svg = qr_svg("http://192.168.1.5:8765/?token=abc")
    assert "<svg" in svg and "</svg>" in svg


def test_qr_endpoint_serves_svg_for_the_given_link(remote):
    link = urllib.parse.quote("http://192.168.1.5:8765/?token=abc", safe="")
    status, ctype, body = remote(f"/api/qr?url={link}")
    assert status == 200 and ctype == "image/svg+xml" and "<svg" in body


def test_qr_endpoint_needs_token_and_a_http_link(remote):
    with pytest.raises(urllib.error.HTTPError) as e:
        remote("/api/qr?url=http://x", token=False)
    assert e.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as e:
        remote("/api/qr?url=javascript:alert(1)")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        remote("/api/qr")          # no url and no remote link known
    assert e.value.code == 404


def test_page_has_qr_button_and_dialog():
    html = (STATIC / "index.html").read_text()
    assert 'id="qr-btn"' in html and 'id="qr"' in html
    assert "initQr" in (STATIC / "js" / "app.js").read_text()
