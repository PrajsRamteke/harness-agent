"""The web theme is kept on this computer, so every project / address / tab shows the same one."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.web import appearance_api as api
from jarvis.web import handler as web_handler

STATIC = Path(web_handler.__file__).with_name("static")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "APPEARANCE_FILE", tmp_path / "web_appearance.json")
    return api.APPEARANCE_FILE


@pytest.fixture()
def server(store):
    from jarvis.web.handler import WebHandler
    from jarvis.web.server import _JarvisHTTPServer

    events: list[tuple[str, dict]] = []
    bridge = SimpleNamespace(token="tok", emit=lambda kind, data: events.append((kind, data)))
    handler = type("H", (WebHandler,), {"bridge": bridge, "app": None})
    srv = _JarvisHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, payload=None, token="tok"):
        req = urllib.request.Request(
            base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="GET" if payload is None else "POST",
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read())

    yield SimpleNamespace(call=call, events=events)
    srv.shutdown()
    srv.server_close()


def test_nothing_saved_yet_is_null(store):
    assert api.load() is None


def test_save_keeps_only_valid_values(store):
    saved = api.save({
        "palette": "Nord", "mode": "bogus", "soft": "1", "softAmt": 5, "glass": True, "glassOp": 500,
        "layout": {"text": "l", "font": "comic"}, "accent": "custom", "custom": "#ABCDEF",
    })
    assert saved == {
        "palette": "nord", "mode": "dark", "soft": True, "softAmt": 10, "glass": True, "glassOp": 100,
        "layout": {"text": "l", "width": "default", "radius": "default", "font": "geist"},
        "accent": "custom", "custom": "#abcdef",
    }
    assert api.load() == saved


def test_non_objects_are_refused_and_nothing_is_written(store):
    assert api.save("nord") is None
    assert api.save(None) is None
    assert not store.exists()


def test_a_corrupt_file_reads_as_unset(store):
    store.write_text("{not json", encoding="utf-8")
    assert api.load() is None


def test_a_choice_in_one_page_is_what_every_other_page_gets(server):
    assert server.call("/api/appearance") == {"ok": True, "appearance": None}
    posted = server.call("/api/appearance", {"appearance": {"palette": "dracula", "mode": "dark"}})
    assert posted["appearance"]["palette"] == "dracula"
    # Another origin / project / tab asks later and gets the same theme.
    assert server.call("/api/appearance")["appearance"]["palette"] == "dracula"
    assert server.events == [("appearance", posted["appearance"])]


def test_the_endpoint_needs_the_token(server):
    with pytest.raises(urllib.error.HTTPError) as err:
        server.call("/api/appearance", token="wrong")
    assert err.value.code == 401


def test_a_bad_body_is_a_400_not_a_saved_theme(server, store):
    with pytest.raises(urllib.error.HTTPError) as err:
        server.call("/api/appearance", {"appearance": "nord"})
    assert err.value.code == 400
    assert not store.exists()


def test_the_page_syncs_the_saved_theme_on_load_focus_and_events():
    theme = (STATIC / "js" / "theme.js").read_text()
    assert "export async function syncAppearance" in theme
    assert "export function applyRemoteLook" in theme
    assert "'/api/appearance'" in theme
    assert "visibilitychange" in theme and "'focus'" in theme
    assert "syncAppearance();" in (STATIC / "js" / "app.js").read_text()
    events = (STATIC / "js" / "events.js").read_text()
    assert "case 'appearance':" in events and "applyRemoteLook(data)" in events
