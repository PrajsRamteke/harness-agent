"""Web remote providers: key paste + checks, OAuth sign-in from any device, switching.

Nothing here touches the real config: key files live in ``tmp_path``, key env
vars are cleared, OAuth token saves and session switches are stubbed.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jarvis import state
from jarvis.constants.api_keys import API_KEY_SPECS
from jarvis.web import providers_api as pv
from jarvis.web.bridge import WebBridge

STATIC = Path(pv.__file__).with_name("static")


@pytest.fixture
def keys(tmp_path, monkeypatch):
    """Key files under tmp_path, no key env vars, nobody signed in."""
    for spec in API_KEY_SPECS:
        monkeypatch.setitem(spec, "file_path", tmp_path / f"{spec['id']}_key")
        monkeypatch.delenv(spec["env_var"], raising=False)
    monkeypatch.setattr(pv, "_signed_in", lambda card_id: False)
    monkeypatch.setattr(state, "provider", "opencode_zen")
    monkeypatch.setattr(state, "harness_agent_free", True)
    monkeypatch.setattr(state, "auth_mode", "api_key")
    monkeypatch.setattr(state, "MODEL", "free-model")
    return {spec["id"]: spec["file_path"] for spec in API_KEY_SPECS}


class Recorder:
    """Stands in for ``bridge.request_action`` (the TUI main thread)."""

    def __init__(self, result=None):
        self.calls: list[tuple[str, dict]] = []
        self.result = result or {"ok": True, "message": "done"}

    def __call__(self, action, data):
        self.calls.append((action, dict(data)))
        return dict(self.result)


# ─── Pasted key text ────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [
    "sk-or-v1-abcdefabcdefabcdef",
    "  sk-or-v1-abcdefabcdefabcdef\n",
    'export OPENROUTER_API_KEY="sk-or-v1-abcdefabcdefabcdef"',
    "OPENROUTER_API_KEY=sk-or-v1-abcdefabcdefabcdef",
    "Bearer sk-or-v1-abcdefabcdefabcdef",
    "'sk-or-v1-abcdefabcdefabcdef'",
])
def test_clean_key_takes_the_key_out_of_whatever_was_pasted(raw):
    assert pv.clean_key(raw) == "sk-or-v1-abcdefabcdefabcdef"


def test_detect_key_by_prefix():
    assert pv.detect_key("sk-ant-api03-xxxx") == "anthropic_api"
    assert pv.detect_key("sk-or-v1-xxxx") == "openrouter"
    assert pv.detect_key("sk-ant-oat01-xxxx") == "anthropic"  # sign-in token, not an API key
    assert pv.detect_key("oc-123456") == ""


def test_format_check_points_to_the_right_provider():
    wrong = pv.check_key_format("openrouter", "sk-ant-api03-" + "x" * 20)
    assert wrong["suggest"] == "anthropic_api" and not wrong["ok"]
    token = pv.check_key_format("anthropic_api", "sk-ant-oat01-" + "x" * 20)
    assert token["suggest"] == "anthropic"
    assert "start with sk-or-" in pv.check_key_format("openrouter", "x" * 24)["error"]
    assert "one line" in pv.check_key_format("opencode", "abc def ghi jkl mno")["error"]
    assert "short" in pv.check_key_format("opencode", "abc")["error"]
    assert pv.check_key_format("opencode", "k" * 24) is None
    assert pv.check_key_format("kimchi", "k" * 24)["error"] == "Unknown provider"  # removed


# ─── Listing ────────────────────────────────────────────────────────────


def test_list_shows_status_and_never_the_key(keys, monkeypatch):
    keys["openrouter"].write_text("sk-or-v1-secretsecretsecret1234")
    monkeypatch.setenv("OPENCODE_API_KEY", "opencode-env-secret-5678")
    data = pv.list_providers()
    rows = {r["id"]: r for r in data["providers"]}

    assert rows["openrouter"]["connected"] and rows["openrouter"]["source"] == "file"
    assert rows["openrouter"]["hint"] == "…1234"
    assert rows["opencode"]["source"] == "env" and rows["opencode"]["env_var"] == "OPENCODE_API_KEY"
    assert "kimchi" not in rows
    assert not rows["anthropic_api"]["connected"]
    assert rows["harness_agent"]["active"] and data["active"] == "harness_agent"
    assert data["connected"] == 2  # the free tier doesn't count
    assert "secret" not in json.dumps(data)


@pytest.mark.parametrize("provider,mode,free,card", [
    ("anthropic", "oauth", False, "anthropic"),
    ("anthropic", "api_key", False, "anthropic_api"),
    ("opencode_zen", "api_key", True, "harness_agent"),
    ("opencode_zen", "api_key", False, "opencode_zen"),
    ("openai_codex", "oauth", False, "openai_codex"),
    ("openrouter", "api_key", False, "openrouter"),
])
def test_active_card_follows_provider_and_auth(monkeypatch, provider, mode, free, card):
    monkeypatch.setattr(state, "provider", provider)
    monkeypatch.setattr(state, "auth_mode", mode)
    monkeypatch.setattr(state, "harness_agent_free", free)
    assert pv.active_card_id() == card


# ─── Saving keys ────────────────────────────────────────────────────────


def test_a_key_the_provider_refuses_is_never_saved(keys, monkeypatch):
    monkeypatch.setattr(pv, "verify_key", lambda card_id, key: "rejected")
    run = Recorder()
    res = pv.save_key("openrouter", "sk-or-v1-" + "b" * 24, use=True, run_action=run)
    assert not res["ok"] and res["rejected"]
    assert not keys["openrouter"].exists()
    assert run.calls == []


def test_saving_writes_the_cleaned_key_then_applies_it_on_the_main_thread(keys, monkeypatch):
    monkeypatch.setattr(pv, "verify_key", lambda card_id, key: "ok")
    run = Recorder({"ok": True, "message": "OpenRouter connected"})
    res = pv.save_key("openrouter", 'export OPENROUTER_API_KEY="sk-or-v1-goodkey1234567890"', use=True, run_action=run)
    assert res["ok"] and res["verified"]
    assert keys["openrouter"].read_text() == "sk-or-v1-goodkey1234567890"
    assert run.calls == [("provider_key_saved", {"id": "openrouter", "use": True, "replaced": False})]


def test_replacing_a_key_says_so(keys, monkeypatch):
    import jarvis.commands.control as control

    keys["opencode"].write_text("oc-old-" + "k" * 16)
    monkeypatch.setattr(pv, "verify_key", lambda card_id, key: "ok")
    monkeypatch.setattr(control, "apply_key_change", lambda provider, removed=False: "")
    monkeypatch.setattr(state, "provider", "opencode")  # already the live provider
    run = lambda action, data: pv.run_provider_action(action, data, console_print=lambda *a: None)
    res = pv.save_key("opencode", "oc-new-" + "n" * 16, use=True, run_action=run)
    assert res["ok"] and res["message"] == "Saved the new OpenCode Go key"
    assert keys["opencode"].read_text().startswith("oc-new-")


def test_unverifiable_key_is_saved_but_not_marked_verified(keys, monkeypatch):
    monkeypatch.setattr(pv, "verify_key", lambda card_id, key: "unknown")
    res = pv.save_key("opencode", "oc-" + "z" * 24, use=False, run_action=Recorder())
    assert res["ok"] and res["verified"] is False
    assert keys["opencode"].exists()


def test_env_var_key_cannot_be_overwritten_from_the_web(keys, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-fromenv000000000000")
    monkeypatch.setattr(pv, "verify_key", lambda *a: pytest.fail("should not check"))
    res = pv.save_key("openrouter", "sk-or-v1-" + "c" * 24, use=True, run_action=Recorder())
    assert not res["ok"] and "OPENROUTER_API_KEY" in res["error"]
    assert not keys["openrouter"].exists()


def test_verify_key_reads_401_as_rejected(monkeypatch):
    import urllib.error

    def refuse(req, timeout):
        assert req.get_header("Authorization") == "Bearer sk-or-v1-nope"
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(pv.urllib.request, "urlopen", refuse)
    assert pv.verify_key("openrouter", "sk-or-v1-nope") == "rejected"

    def offline(req, timeout):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(pv.urllib.request, "urlopen", offline)
    assert pv.verify_key("openrouter", "sk-or-v1-nope") == "unknown"
    assert pv.verify_key("opencode", "anything") == "unknown"  # no cheap check exists


def test_remove_key(keys, monkeypatch):
    import jarvis.commands.control as control

    keys["opencode"].write_text("oc-" + "k" * 20)
    monkeypatch.setattr(control, "apply_key_change", lambda provider, removed=False: "")
    assert pv.remove_key("opencode")["ok"]
    assert not keys["opencode"].exists()

    monkeypatch.setenv("OPENCODE_API_KEY", "oc-env-" + "k" * 10)
    res = pv.remove_key("opencode")
    assert not res["ok"] and "OPENCODE_API_KEY" in res["error"]


# ─── Switching ──────────────────────────────────────────────────────────


def test_use_needs_a_connection_first(keys):
    res = pv.run_provider_action("provider_use", {"id": "opencode"}, console_print=lambda *a: None)
    assert not res["ok"] and res["error"] == "Add a key for OpenCode Go first"
    res = pv.use_card("anthropic")
    assert res["error"] == "Sign in to Claude Pro / Max first"


def test_use_switches_through_the_model_picker_path(keys, monkeypatch):
    import jarvis.commands.control as control

    keys["openrouter"].write_text("sk-or-v1-" + "d" * 24)
    seen = {}

    def fake_select(model, *, source=""):
        seen.update(model=model, source=source)
        state.provider, state.harness_agent_free, state.MODEL = "openrouter", False, model

    monkeypatch.setattr(control, "_apply_model_selection", fake_select)
    monkeypatch.setattr(pv, "model_for_source", lambda source: "vendor/some-model:free")
    printed = []
    res = pv.run_provider_action("provider_use", {"id": "openrouter"}, console_print=printed.append)
    assert res["ok"] and res["model"] == "vendor/some-model:free"
    assert seen == {"model": "vendor/some-model:free", "source": "openrouter"}
    assert printed and "Now using OpenRouter" in printed[0]


def test_model_for_source_keeps_a_model_the_source_serves(monkeypatch):
    import jarvis.constants.providers as p

    monkeypatch.setattr(p, "models_for_source", lambda source, cached=False, live=False: [("a", ""), ("b", "")])
    monkeypatch.setattr(state, "MODEL", "b")
    assert pv.model_for_source("anthropic_api") == "b"
    monkeypatch.setattr(state, "MODEL", "elsewhere")
    monkeypatch.setattr(p, "ANTHROPIC_DEFAULT_MODEL", "retired")
    assert pv.model_for_source("anthropic_api") == "a"  # default gone from the list → first served


def test_signing_out_of_the_live_account_lands_on_the_free_tier(keys, monkeypatch):
    import jarvis.auth.connect.oauth_actions as oa

    monkeypatch.setattr(state, "provider", "openai_codex")
    monkeypatch.setattr(state, "auth_mode", "oauth")

    def disconnect(spec):
        state.client = None
        return True, "✓ signed out", None

    monkeypatch.setattr(oa, "disconnect_oauth", disconnect)
    fell = []
    monkeypatch.setattr(pv, "_fall_back_to_free", lambda: fell.append(True))
    res = pv.sign_out("openai_codex")
    assert res["ok"] and "Harness Agent" in res["message"] and fell


# ─── OAuth sign-in ──────────────────────────────────────────────────────


@pytest.fixture
def no_listener(monkeypatch):
    monkeypatch.setattr(pv, "_start_codex_listener", lambda flow, run: None)
    yield
    for flow in list(pv._flows.values()):
        pv._drop_flow(flow)


def test_claude_sign_in_from_any_device(no_listener, monkeypatch):
    import jarvis.auth.oauth_tokens as ot

    start = pv.start_oauth("anthropic", run_action=Recorder())
    assert start["ok"] and start["url"].startswith("https://claude.ai/oauth/authorize?")
    flow = pv._flows[start["flow"]]
    assert f"state={flow.state}" in start["url"] and flow.verifier not in start["url"]

    wrong = pv.finish_oauth(start["flow"], "abc#not-this-state", run_action=Recorder())
    assert not wrong["ok"] and "older sign-in link" in wrong["error"]

    exchanged, saved = [], []
    monkeypatch.setattr(ot, "exchange_oauth_code", lambda code, verifier, st: exchanged.append((code, verifier, st))
                        or (200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}))
    monkeypatch.setattr(ot, "save_oauth_tokens", saved.append)
    run = Recorder({"ok": True, "message": "Signed in to Claude Pro / Max · now using claude-x"})
    res = pv.finish_oauth(start["flow"], f"thecode#{flow.state}", run_action=run)

    assert res["ok"]
    assert exchanged == [("thecode", flow.verifier, flow.state)]
    assert saved[0]["access_token"] == "at" and saved[0]["refresh_token"] == "rt"
    assert run.calls == [("provider_signed_in", {"id": "anthropic"})]
    assert pv.oauth_status(start["flow"])["status"] == "done"


def test_a_failed_exchange_can_be_retried(no_listener, monkeypatch):
    import jarvis.auth.oauth_tokens as ot

    start = pv.start_oauth("anthropic", run_action=Recorder())
    st = pv._flows[start["flow"]].state
    monkeypatch.setattr(ot, "exchange_oauth_code", lambda *a: (400, {"error": "invalid_grant"}))
    res = pv.finish_oauth(start["flow"], f"old#{st}", run_action=Recorder())
    assert not res["ok"] and "expired" in res["error"]
    assert pv.oauth_status(start["flow"])["status"] == "waiting"


def test_new_link_replaces_the_old_one(no_listener):
    first = pv.start_oauth("anthropic", run_action=Recorder())
    second = pv.start_oauth("anthropic", run_action=Recorder())
    assert first["flow"] != second["flow"]
    assert pv.oauth_status(first["flow"])["status"] == "expired"
    assert pv.finish_oauth(first["flow"], "x", run_action=Recorder())["expired"]


@pytest.mark.parametrize("paste", [
    "http://localhost:1455/auth/callback?code=CODE&state={st}",
    "localhost:1455/auth/callback?code=CODE&state={st}",
    "code=CODE&state={st}",
])
def test_chatgpt_callback_address_pasted_from_a_phone(no_listener, monkeypatch, paste):
    import jarvis.auth.codex_oauth_tokens as ct

    start = pv.start_oauth("openai_codex", run_action=Recorder())
    assert "auth.openai.com" in start["url"]
    st = pv._flows[start["flow"]].state
    monkeypatch.setattr(ct, "exchange_codex_oauth_code", lambda code, verifier, redirect_uri: (
        (200, {"access_token": "a", "refresh_token": "r", "id_token": ""}) if code == "CODE" else (400, {})
    ))
    persisted = []
    monkeypatch.setattr(ct, "persist_codex_oauth_bundle", lambda body, api_key=None: persisted.append(body))
    run = Recorder()
    res = pv.finish_oauth(start["flow"], paste.format(st=st), run_action=run)
    assert res["ok"], res
    assert persisted and run.calls == [("provider_signed_in", {"id": "openai_codex"})]


def test_chatgpt_refusal_in_the_pasted_address(no_listener):
    start = pv.start_oauth("openai_codex", run_action=Recorder())
    res = pv.finish_oauth(start["flow"], "http://localhost:1455/auth/callback?error=access_denied", run_action=Recorder())
    assert not res["ok"] and "access_denied" in res["error"]


def test_chatgpt_sign_in_on_this_computer_finishes_by_itself(monkeypatch):
    """The localhost callback lands → exchange → switch, with nothing pasted."""
    import jarvis.auth.codex_oauth_tokens as ct
    import jarvis.constants.codex_oauth as co

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(co, "CODEX_OAUTH_CALLBACK_PORT", port)
    monkeypatch.setattr(ct, "exchange_codex_oauth_code", lambda *a, **k: (200, {"access_token": "a", "refresh_token": "r"}))
    monkeypatch.setattr(ct, "persist_codex_oauth_bundle", lambda body, api_key=None: body)
    run = Recorder()
    start = pv.start_oauth("openai_codex", run_action=run)
    flow = pv._flows[start["flow"]]
    try:
        assert start["listening"]
        body = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/auth/callback?code=c&state={flow.state}", timeout=5,
        ).read()
        assert b"Signed in" in body
        for _ in range(50):
            if pv.oauth_status(start["flow"])["status"] == "done":
                break
            time.sleep(0.05)
        assert pv.oauth_status(start["flow"])["status"] == "done"
        assert run.calls == [("provider_signed_in", {"id": "openai_codex"})]
    finally:
        pv._drop_flow(flow, join=True)


def test_callback_wait_stops_when_asked():
    from jarvis.auth.codex_oauth_callback import CodexOAuthCallbackError, wait_for_codex_oauth_callback

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    t0 = time.monotonic()
    with pytest.raises(CodexOAuthCallbackError, match="cancelled"):
        wait_for_codex_oauth_callback(expected_state="s", port=port, timeout=30, stop=stop)
    assert time.monotonic() - t0 < 3
    with socket.socket() as s:  # the port is free again
        s.bind(("127.0.0.1", port))


# ─── HTTP routes ────────────────────────────────────────────────────────


def test_http_routes_need_the_token_and_run_on_the_bridge(keys, monkeypatch):
    from jarvis.web.server import start_web_server, stop_web_server

    monkeypatch.setattr(pv, "verify_key", lambda card_id, key: "ok")
    bridge = WebBridge()
    actions = []
    bridge.set_handlers(on_action=lambda action, data, done: (actions.append(action), done({"ok": True, "message": "ok"})))
    sub = bridge.subscribe()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server, _urls, port = start_web_server(bridge=bridge, app=None, port=port, host="127.0.0.1")
    base = f"http://127.0.0.1:{port}"

    def post(path, payload, token=bridge.token):
        req = urllib.request.Request(
            base + path, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, {}

    try:
        assert post("/api/providers/key", {"id": "opencode", "key": "k" * 24}, token="wrong")[0] == 401
        status, body = post("/api/providers/key", {"id": "opencode", "key": "oc-" + "k" * 20})
        assert status == 200 and body["ok"] and body["verified"]
        assert keys["opencode"].exists() and actions == ["provider_key_saved"]
        rows = {r["id"]: r for r in body["providers"]["providers"]}
        assert rows["opencode"]["connected"] and "k" * 20 not in json.dumps(body)
        assert "provider" in body["state"]

        events = []
        while not sub.empty():
            events.append(json.loads(sub.get_nowait())["type"])
        assert "providers" in events and "state" in events

        with urllib.request.urlopen(f"{base}/api/providers?token={bridge.token}", timeout=5) as r:
            assert json.loads(r.read())["connected"] == 1
    finally:
        stop_web_server(server, bridge)


# ─── Page wiring ────────────────────────────────────────────────────────


def test_provider_command_opens_in_the_browser_not_on_the_computer():
    catalog = (STATIC / "js" / "catalog.js").read_text()
    entry = re.search(r"\{[^{}]*cmd: '/provider'[^{}]*\}", catalog).group(0)
    assert "picker: 'provider'" in entry and "laptop" not in entry
    for cmd in ("/provider", "/login", "/key"):
        assert f"'{cmd}': 'provider'" in catalog
    html = (STATIC / "index.html").read_text()
    # the dialog exists; the sidebar card that used to open it is gone (⌘K, /provider
    # and the model picker's "Add a provider" row still do)
    assert 'id="providers"' in html and 'id="providers-card"' not in html


# ─── Providers from models.dev ──────────────────────────────────────────


@pytest.fixture
def catalog(keys, monkeypatch):
    from jarvis.auth import models_dev

    for var in ("DEEPSEEK_API_KEY", "LMSTUDIO_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    raw = {
        "deepseek": {
            "id": "deepseek", "name": "DeepSeek", "npm": "@ai-sdk/openai-compatible",
            "api": "https://api.deepseek.com", "env": ["DEEPSEEK_API_KEY"],
            "doc": "https://platform.deepseek.com",
            "models": {"deepseek-chat": {
                "id": "deepseek-chat", "name": "DeepSeek Chat", "tool_call": True,
                "modalities": {"input": ["text"], "output": ["text"]},
                "limit": {"context": 128000, "output": 8192}, "cost": {"input": 0.3, "output": 1.2},
            }},
        },
        "lmstudio": {
            "id": "lmstudio", "name": "LMStudio", "npm": "@ai-sdk/openai-compatible",
            "api": "http://127.0.0.1:1234/v1", "env": ["LMSTUDIO_API_KEY"],
            "models": {"local": {"id": "local", "tool_call": True,
                                 "modalities": {"input": ["text"], "output": ["text"]}}},
        },
    }
    models_dev.store(models_dev.trim(raw))
    return models_dev


def test_catalog_providers_are_listed_after_the_built_ins(catalog):
    rows = pv.list_providers()["providers"]
    ids = [r["id"] for r in rows]
    assert ids[: len(pv.CARDS)] == [c.id for c in pv.CARDS]
    deep = next(r for r in rows if r["id"] == "md:deepseek")
    assert deep["catalog"] and deep["kind"] == "key" and not deep["connected"]
    assert deep["env_var"] == "DEEPSEEK_API_KEY"
    assert deep["link"] == "https://platform.deepseek.com"
    assert deep["checked"] is True
    local = next(r for r in rows if r["id"] == "md:lmstudio")
    assert local["local"] and local["checked"] is False


def test_saving_a_catalog_key_verifies_then_stores_it(catalog, monkeypatch):
    from jarvis.auth import catalog_keys

    seen = []
    monkeypatch.setattr(pv, "verify_key", lambda cid, key, **k: seen.append((cid, key)) or "ok")
    run = Recorder()
    res = pv.save_key("md:deepseek", "export DEEPSEEK_API_KEY=sk-deep-abcdef123456", use=True, run_action=run)
    assert res["ok"] and res["verified"]
    assert seen == [("md:deepseek", "sk-deep-abcdef123456")]
    assert catalog_keys.get_key("md:deepseek") == "sk-deep-abcdef123456"
    assert run.calls == [("provider_key_saved", {"id": "md:deepseek", "use": True, "replaced": False})]
    row = next(r for r in pv.list_providers()["providers"] if r["id"] == "md:deepseek")
    assert row["connected"] and row["hint"] == "…3456"


def test_a_refused_catalog_key_is_never_saved(catalog, monkeypatch):
    from jarvis.auth import catalog_keys

    monkeypatch.setattr(pv, "verify_key", lambda *a, **k: "rejected")
    res = pv.save_key("md:deepseek", "sk-deep-abcdef123456", use=True, run_action=Recorder())
    assert not res["ok"] and res["rejected"]
    assert not catalog_keys.has_key("md:deepseek")


def test_catalog_key_can_reference_an_env_var(catalog, monkeypatch):
    from jarvis.auth import catalog_keys

    monkeypatch.setattr(pv, "verify_key", lambda *a, **k: pytest.fail("a reference is not sent anywhere"))
    assert not pv.save_key("md:deepseek", "$MY_DEEPSEEK", use=False, run_action=Recorder())["ok"]
    monkeypatch.setenv("MY_DEEPSEEK", "sk-from-env-123456")
    assert pv.save_key("md:deepseek", "$MY_DEEPSEEK", use=False, run_action=Recorder())["ok"]
    assert catalog_keys.get_key("md:deepseek") == "sk-from-env-123456"
    row = next(r for r in pv.list_providers()["providers"] if r["id"] == "md:deepseek")
    assert row["hint"] == "$MY_DEEPSEEK"


def test_catalog_check_request_uses_the_providers_base_url(catalog):
    url, headers = pv._check_request("md:deepseek", "k")
    assert url == "https://api.deepseek.com/models" and headers["Authorization"] == "Bearer k"
    assert pv._check_request("md:lmstudio", "k") is None  # local: nothing to ask


def test_use_and_remove_a_catalog_provider(catalog, monkeypatch):
    from jarvis.auth import catalog_keys

    catalog_keys.save("md:deepseek", "sk-deep-abcdef123456")
    picked = []

    def fake_apply(model, source=""):
        picked.append((model, source))
        state.provider = source

    monkeypatch.setattr("jarvis.commands.control._apply_model_selection", fake_apply)
    res = pv.use_card("md:deepseek")
    assert res["ok"] and picked == [("deepseek-chat", "md:deepseek")]

    monkeypatch.setattr("jarvis.commands.control.apply_key_change", lambda p, removed=False: "")
    assert pv.remove_key("md:deepseek")["ok"]
    assert not catalog_keys.has_key("md:deepseek")
