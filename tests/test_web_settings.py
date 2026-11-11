"""Web Settings dialog: every setting as a control (``jarvis/web/settings_api.py``).

Also: settings changed from the web no longer print "web: …" lines in the
transcripts, and the sidebar has one thinking switch.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis import state
from jarvis.storage.settings import get_settings
from jarvis.web import settings_api as sa
from jarvis.web.bridge import WebBridge

STATIC = Path(sa.__file__).with_name("static")


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    for name in ("show_internal", "auto_approve", "global_skills", "global_agents",
                 "global_commands", "global_mcp", "theme", "theme_colors"):
        monkeypatch.setattr(state, name, getattr(state, name))
    yield
    for key in ("subagents.max_parallel", "subagents.max_steps", "theme", "web.tunnel",
                "pet.nudges", "trace.on", "skills.global", "models.prompt_cache", "models.catalog",
                "models.local", "models.openrouter_usage", "notify.push", "notify.push_min_seconds",
                "uploads.max_mb", "uploads.keep_days", "ui.mouse"):
        try:
            get_settings().reset(key)
        except Exception:
            pass


def _row(key):
    for sec in sa.public()["sections"]:
        for it in sec["items"]:
            if it["key"] == key:
                return it
    raise KeyError(key)


def test_public_lists_every_setting_with_value_and_default():
    pub = sa.public()
    assert [s["id"] for s in pub["sections"]] == [sid for sid, _ in sa.SECTIONS]
    keys = [it["key"] for s in pub["sections"] for it in s["items"]]
    assert keys == [it["key"] for it in sa.ITEMS]
    for s in pub["sections"]:
        for it in s["items"]:
            assert it["label"] and it["kind"] in ("switch", "number", "choice")
            assert it.get("icon"), f"{it['key']} has no icon"
            assert "value" in it and "default" in it
    # the page's left nav draws one icon per section, all bundled in icons.js
    icons_js = (STATIC / "js" / "icons.js").read_text()
    bundled = set(json.loads(re.search(r"const ICONS = (\{.*\});", icons_js).group(1)))
    for sec in pub["sections"]:
        assert sec.get("icon"), f"section {sec['id']} has no icon"
        assert sec["icon"] in bundled, f"section icon {sec['icon']!r} is not in icons.js"
    from jarvis.tui.theme import PALETTES

    themes = _row("theme")["options"]
    assert [o[0] for o in themes] == list(PALETTES)
    assert all(o[2]["accent"].startswith("#") and o[2]["bg"].startswith("#") for o in themes)  # menu swatches
    assert pub["file"].endswith("settings.json")


def test_set_reset_and_validation():
    assert sa.apply({"key": "subagents.max_parallel", "value": 5})["ok"]
    assert get_settings().get("subagents.max_parallel") == 5 and _row("subagents.max_parallel")["value"] == 5

    bad = sa.apply({"key": "subagents.max_parallel", "value": 99})
    assert not bad["ok"] and "1–20" in bad["error"]
    assert get_settings().get("subagents.max_parallel") == 5  # unchanged

    assert sa.apply({"op": "reset", "key": "subagents.max_parallel"})["ok"]
    assert get_settings().get("subagents.max_parallel") == 20

    assert sa.apply({"key": "web.tunnel", "value": "ngrok"})["ok"]
    assert not sa.apply({"key": "web.tunnel", "value": "carrier-pigeon"})["ok"]
    assert get_settings().get("web.tunnel") == "ngrok"

    assert sa.apply({"key": "pet.nudges", "value": False})["ok"]
    assert get_settings().get("pet.nudges") is False

    assert not sa.apply({"key": "api_key", "value": "x"})["ok"]  # only listed keys
    assert not sa.apply({"op": "explode", "key": "pet.nudges"})["ok"]


# ─── Settings that used to be environment variables only ──────────────────

ENV_VARS = ("HARNESS_PROMPT_CACHE", "HARNESS_MODELS_DEV", "HARNESS_LOCAL_MODELS", "HARNESS_OPENROUTER_USAGE",
            "HARNESS_WEB_PUSH", "HARNESS_PUSH_MIN_SECONDS", "HARNESS_UPLOAD_MAX_MB", "HARNESS_UPLOAD_KEEP_DAYS",
            "HARNESS_MOUSE")


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_new_settings_are_listed_in_their_sections(clean_env):
    where = {it["key"]: sec["id"] for sec in sa.public()["sections"] for it in sec["items"]}
    assert where["models.prompt_cache"] == where["models.catalog"] == where["models.local"] == "catalogs"
    assert where["notify.push"] == where["notify.push_min_seconds"] == "notifications"
    assert where["uploads.max_mb"] == where["uploads.keep_days"] == "uploads"
    assert where["ui.mouse"] == "terminal"
    for key in ("models.prompt_cache", "models.catalog", "models.local", "models.openrouter_usage",
                "notify.push", "uploads.max_mb", "uploads.keep_days", "ui.mouse"):
        assert not _row(key).get("locked")
    # defaults are today's behaviour, so nobody's setup changes by upgrading
    assert [_row(k)["default"] for k in ("notify.push_min_seconds", "uploads.max_mb", "uploads.keep_days")] == [20, 50, 30]


def test_switches_reach_the_code_that_uses_them(clean_env):
    from jarvis.auth import local_models, models_dev, openrouter_usage
    from jarvis.repl import prompt_cache
    from jarvis.web import push

    for key, probe in (("models.catalog", models_dev.enabled), ("models.local", local_models.enabled),
                       ("models.openrouter_usage", openrouter_usage.enabled), ("notify.push", push._enabled_by_env)):
        assert probe() is True
        assert sa.apply({"key": key, "value": False})["ok"]
        assert probe() is False, key
        assert sa.apply({"op": "reset", "key": key})["ok"] and probe() is True

    sa.apply({"key": "models.prompt_cache", "value": False})
    assert prompt_cache.enabled_for("anthropic", "claude-sonnet-5-5", object()) is False


def test_numbers_reach_the_code_that_uses_them(clean_env):
    from jarvis import media
    from jarvis.web import push

    assert (media.max_file_mb(), media.keep_days(), push.min_turn_seconds()) == (50, 30, 20.0)
    assert sa.apply({"key": "uploads.max_mb", "value": 5})["ok"] and media.max_file_bytes() == 5 * 1024 * 1024
    assert sa.apply({"key": "uploads.keep_days", "value": 7})["ok"] and media.keep_days() == 7
    assert sa.apply({"key": "notify.push_min_seconds", "value": 0})["ok"] and push.min_turn_seconds() == 0
    assert not sa.apply({"key": "uploads.max_mb", "value": 0})["ok"]
    assert not sa.apply({"key": "uploads.keep_days", "value": 9999})["ok"]


def test_an_environment_variable_pins_its_row(clean_env, monkeypatch):
    from jarvis import media
    from jarvis.auth import models_dev

    sa.apply({"key": "models.catalog", "value": True})
    monkeypatch.setenv("HARNESS_MODELS_DEV", "0")
    row = _row("models.catalog")
    assert row["locked"] == "HARNESS_MODELS_DEV" and row["value"] is False  # shows what is in force
    refused = sa.apply({"key": "models.catalog", "value": True})
    assert not refused["ok"] and "HARNESS_MODELS_DEV" in refused["error"]
    assert models_dev.enabled() is False and get_settings().get("models.catalog") is True  # file untouched

    monkeypatch.setenv("HARNESS_UPLOAD_MAX_MB", "7")
    assert _row("uploads.max_mb")["value"] == 7 and _row("uploads.max_mb")["locked"] == "HARNESS_UPLOAD_MAX_MB"
    assert media.max_file_mb() == 7
    monkeypatch.setenv("HARNESS_UPLOAD_MAX_MB", "not-a-number")  # junk falls back, never crashes
    assert media.max_file_mb() == 50


def test_page_knows_every_server_section_before_the_list_arrives():
    """The nav is drawn from SERVER_FALLBACK until /api/settings/all answers; it must match the server."""
    js = (STATIC / "js" / "settings.js").read_text()
    block = re.search(r"const SERVER_FALLBACK = \[(.*?)\n\];", js, re.S).group(1)
    assert re.findall(r"id: '([a-z]+)'", block) == [sid for sid, _ in sa.SECTIONS]
    subs = re.search(r"const SECTION_SUBS = \{(.*?)\n\};", js, re.S).group(1)
    for sid, _ in sa.SECTIONS:
        assert re.search(rf"\b{sid}:", subs), f"{sid} has no subtitle"
    # a section asked for before the list loads is kept, not replaced by Model
    assert "pending" in js and "remember: !pending" in js


def test_locked_rows_are_read_only_on_the_page():
    js = (STATIC / "js" / "settings.js").read_text()
    assert "it.locked" in js and "set-pinned" in js and "Pinned by" in js
    css = (STATIC / "css" / "settings.css").read_text()
    assert ".set-row.is-locked" in css


def test_theme_updates_the_running_state():
    assert sa.apply({"key": "theme", "value": "nord"})["ok"]
    assert get_settings().get("theme") == "nord" and state.theme == "nord"


def test_session_keys_change_the_running_session():
    sa.apply({"key": "trace.on", "value": False})
    assert state.show_internal is False and get_settings().get("trace.on") is False
    sa.apply({"key": "session.auto_approve", "value": True})
    assert state.auto_approve is True
    assert _row("session.auto_approve")["value"] is True
    # auto-approve is for this run only: never written to settings.json
    assert get_settings().get("session.auto_approve") is None


def test_global_scope_switch_goes_through_the_scope_action(monkeypatch):
    from jarvis.storage import skills as sk

    calls = []
    monkeypatch.setattr(sk, "invalidate_cache", lambda: calls.append(1))
    assert sa.apply({"key": "skills.global", "value": True})["ok"]
    assert state.global_skills is True and get_settings().get("skills.global") is True
    assert calls  # the skill list is re-read


def test_run_web_action_dispatches_settings_set():
    from jarvis.web.actions_api import run_web_action

    res = run_web_action("settings_set", {"key": "subagents.max_steps", "value": 80}, console_print=print)
    assert res["ok"] and res["key"] == "subagents.max_steps" and res["settings"]["sections"]
    assert get_settings().get("subagents.max_steps") == 80


def test_http_routes(monkeypatch):
    from jarvis.web.server import start_web_server, stop_web_server

    bridge = WebBridge()
    actions = []

    def on_action(action, data, done):
        actions.append(action)
        from jarvis.web.actions_api import run_web_action

        done(run_web_action(action, data, console_print=print))

    bridge.set_handlers(on_action=on_action)
    sub = bridge.subscribe()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server, _urls, port = start_web_server(bridge=bridge, app=None, port=port, host="127.0.0.1")
    base = f"http://127.0.0.1:{port}"
    try:
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{base}/api/settings/all", timeout=5)
        with urllib.request.urlopen(f"{base}/api/settings/all?token={bridge.token}", timeout=5) as r:
            assert json.loads(r.read())["sections"]
        req = urllib.request.Request(
            f"{base}/api/settings/all", method="POST",
            data=json.dumps({"op": "set", "key": "subagents.max_parallel", "value": 3}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {bridge.token}"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read())
        assert body["ok"] and actions == ["settings_set"]
        events = []
        while not sub.empty():
            events.append(json.loads(sub.get_nowait())["type"])
        assert "settings_all" in events
    finally:
        stop_web_server(server, bridge)


# ─── The terminal side ────────────────────────────────────────────────────


def test_web_toggles_print_nothing_in_the_transcript():
    from jarvis.tui.mixins.web_remote import WebRemoteMixin

    printed = []
    fake = SimpleNamespace(_tui_console=SimpleNamespace(print=lambda *a, **k: printed.append(a)))
    res = WebRemoteMixin._handle_web_settings(fake, {"auto_approve": True})
    assert res == {"auto_approve": True}
    WebRemoteMixin._handle_web_settings(fake, {"auto_approve": False, "show_internal": True})
    assert printed == []


def test_terminal_repaints_what_a_web_setting_changed():
    from jarvis.tui.mixins.web_remote import WebRemoteMixin

    calls = []
    rec = lambda name: (lambda *a, **k: calls.append(name))  # noqa: E731
    fake = SimpleNamespace(**{n: rec(n) for n in (
        "_apply_theme_runtime", "_sync_trace", "_load_sticky_pref", "_sync_sticky_prompt",
        "_pet_apply_visibility", "_render_web_bar", "_render_footer", "_refresh_sidebar")})
    for key, expect in (("theme", "_apply_theme_runtime"), ("trace.on", "_sync_trace"),
                        ("ui.sticky_prompt", "_sync_sticky_prompt"), ("pet.enabled", "_pet_apply_visibility"),
                        ("web.qr", "_render_web_bar")):
        calls.clear()
        WebRemoteMixin._apply_web_setting(fake, key)
        assert expect in calls and "_render_footer" in calls


# ─── Page wiring ──────────────────────────────────────────────────────────


def test_settings_opens_in_the_browser_with_a_gear():
    catalog = (STATIC / "js" / "catalog.js").read_text()
    assert "'/settings': 'settings'" in catalog and "'/setting': 'settings'" in catalog
    assert "laptop: true" not in re.search(r"\{[^{}]*cmd: '/settings'[^{}]*\}", catalog).group(0)
    html = (STATIC / "index.html").read_text()
    assert 'id="settings-btn"' in html and '<div class="modal" id="settings"' in html
    assert "/static/css/settings.css" in html
    assert "kind === 'settings'" in (STATIC / "js" / "pickers.js").read_text()
    # choices open the dialog's own menu, never a native <select>
    js = (STATIC / "js" / "settings.js").read_text()
    assert not re.search(r"<select[\s>]|<option", js.replace("native <select>", ""))
    assert '<div class="set-menu" id="set-menu" role="listbox" hidden>' in html
    assert "set-menu" in js and "data-pick" in js


def test_settings_is_a_two_pane_modal_with_appearance_and_keymap_inside():
    html = (STATIC / "index.html").read_text()
    start = html.index('<div class="modal" id="settings"')
    end = html.index('<div class="modal"', start + 1)
    assert 'id="settings-nav"' in html[start:end]
    for pane in ("appearance", "keymap"):
        assert start < html.index(f'id="{pane}"') < end, f"#{pane} is not a pane of #settings"
        assert f'<div class="modal" id="{pane}"' not in html  # no standalone dialog any more
    js = (STATIC / "js" / "settings.js").read_text()
    assert "export function showSection" in js and "export function openSettings" in js
    assert "jarvis-settings-section" in js  # last section is remembered
    # the two browser-side panes hand their host to settings.js, which opens them as sections
    assert "export function setAppearanceOpener" in (STATIC / "js" / "theme.js").read_text()
    km = (STATIC / "js" / "keymap_editor.js").read_text()
    assert "export function setKeymapHost" in km and "export function paintKeymap" in km
    assert "setAppearanceOpener(" in js and "setKeymapHost(" in js
    assert "openSettings('appearance')" in js and "openSettings('keymap')" in js


def test_sidebar_has_one_thinking_switch_pinned_below_the_sessions():
    html = (STATIC / "index.html").read_text()
    assert "sw-thoughts" not in html and "Tool trace" not in html
    assert html.count('id="sw-thinking"') == 1
    scroll_end = html.index('<div class="side-toggles"')
    assert html.index('id="recent-list"') < scroll_end < html.index('id="sw-thinking"') < html.index('class="side-foot"')
    for name in ("store.js", "actions.js", "palette.js", "catalog.js", "sidebar.js", "chat.js"):
        assert "showThoughts" not in (STATIC / "js" / name).read_text(), name


def test_real_terminal_applies_web_settings(monkeypatch):
    """The running TUI repaints a theme / pet / thinking change from the web."""
    import asyncio

    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.tui.prompt_history as prompt_history
    import jarvis.updater as updater
    from jarvis.tui import theme as tui_theme
    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)

    # _apply_web_setting swallows errors, so check each effect on screen.
    async def run() -> None:
        app = JarvisTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause(0.3)
            res = app._handle_web_action("settings_set", {"key": "theme", "value": "nord"})
            assert res["ok"]
            await pilot.pause(0.2)
            assert app.theme == tui_theme.textual_theme_name("nord")
            res = app._handle_web_action("settings_set", {"key": "pet.enabled", "value": False})
            assert res["ok"]
            await pilot.pause(0.1)
            assert app._pet_pen() is None or app._pet_pen().has_class("hidden")
            app._handle_web_action("settings_set", {"key": "pet.enabled", "value": True})
            res = app._handle_web_action("settings_set", {"key": "trace.on", "value": False})
            assert res["ok"] and state.show_internal is False
            await pilot.pause(0.1)
            assert app._transcript().has_class("-trace-off")

    asyncio.run(run())
    get_settings().reset("pet.enabled")
