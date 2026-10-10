"""Google Antigravity sign-in + client tests (no live network)."""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

import httpx
import pytest

from jarvis import state
from jarvis.auth import antigravity_catalog as cat
from jarvis.auth import antigravity_client as agc
from jarvis.auth import antigravity_oauth as ago
from jarvis.auth import catalog_cache
from jarvis.constants.antigravity_oauth import ANTIGRAVITY_SKIP_THOUGHT_SIGNATURE
from jarvis.constants.providers import PROVIDER_ANTIGRAVITY


@pytest.fixture(autouse=True)
def ag_env(tmp_path, monkeypatch):
    """Private token file + catalog cache; nothing reaches Google."""
    monkeypatch.setattr(ago, "ANTIGRAVITY_OAUTH_FILE", tmp_path / "antigravity_oauth.json")
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path / "cache")
    # The OAuth client is never in the source — fake one for these tests.
    monkeypatch.setenv("HARNESS_ANTIGRAVITY_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("HARNESS_ANTIGRAVITY_CLIENT_SECRET", "test-client-secret")

    def _no_network(*_a, **_k):
        raise AssertionError("tried to reach the network")

    monkeypatch.setattr(httpx, "post", _no_network)
    monkeypatch.setattr(httpx, "get", _no_network)
    return tmp_path


def _sign_in(project: str = "proj-1", email: str = "me@example.com") -> dict:
    tokens = {"access_token": "ya29.tok", "refresh_token": "1//refresh",
              "expires_at": int(time.time()) + 3600, "email": email, "project_id": project}
    ago.save_antigravity_tokens(tokens)
    return tokens


# ── sign-in ──────────────────────────────────────────────────────────────────


def test_authorize_url_asks_for_offline_access_with_pkce():
    url = ago.build_antigravity_authorize_url(code_challenge="chal", state="st8")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert qs["redirect_uri"] == ["http://localhost:51121/oauth-callback"]
    assert qs["code_challenge"] == ["chal"] and qs["code_challenge_method"] == ["S256"]
    assert qs["access_type"] == ["offline"] and qs["prompt"] == ["consent"]
    assert "https://www.googleapis.com/auth/cloud-platform" in qs["scope"][0]
    assert qs["state"] == ["st8"]


def test_oauth_client_is_not_in_source_and_reads_env_then_file(monkeypatch, tmp_path):
    from jarvis.constants import antigravity_oauth as consts
    assert consts.antigravity_client() == ("test-client-id", "test-client-secret")
    monkeypatch.delenv("HARNESS_ANTIGRAVITY_CLIENT_ID")
    monkeypatch.delenv("HARNESS_ANTIGRAVITY_CLIENT_SECRET")
    assert consts.antigravity_client() == ("", "")
    f = tmp_path / "antigravity_client.json"
    f.write_text('{"client_id": "file-id", "client_secret": "file-secret"}')
    monkeypatch.setattr(consts, "ANTIGRAVITY_CLIENT_FILE", f)
    assert consts.antigravity_client() == ("file-id", "file-secret")


def test_missing_oauth_client_is_a_clear_error(monkeypatch, tmp_path):
    from jarvis.constants import antigravity_oauth as consts
    monkeypatch.delenv("HARNESS_ANTIGRAVITY_CLIENT_ID")
    monkeypatch.delenv("HARNESS_ANTIGRAVITY_CLIENT_SECRET")
    monkeypatch.setattr(consts, "ANTIGRAVITY_CLIENT_FILE", tmp_path / "none.json")
    with pytest.raises(RuntimeError, match="OAuth client"):
        ago.build_antigravity_authorize_url(code_challenge="c", state="s")
    status, body = ago.exchange_antigravity_code("code", "verifier")
    assert status == 400 and "OAuth client" in ago.explain_token_error(status, body)
    assert ago.antigravity_refresh({"refresh_token": "r"}) is None


def test_complete_login_saves_tokens_project_and_email(monkeypatch):
    monkeypatch.setattr(ago, "_post_form", lambda url, fields: (
        200, {"access_token": "a1", "refresh_token": "r1", "expires_in": 3599}))
    monkeypatch.setattr(ago, "_fetch_email", lambda tok: "dev@example.com")
    monkeypatch.setattr(ago, "discover_project", lambda tok: "managed-123")
    bundle, err = ago.complete_antigravity_login("4/0code", "verifier")
    assert err == ""
    saved = ago.load_antigravity_tokens()
    assert saved == bundle
    assert saved["project_id"] == "managed-123" and saved["email"] == "dev@example.com"
    assert saved["expires_at"] > time.time() + 3000


def test_complete_login_without_refresh_token_fails(monkeypatch):
    monkeypatch.setattr(ago, "_post_form", lambda url, fields: (200, {"access_token": "a1"}))
    bundle, err = ago.complete_antigravity_login("code", "verifier")
    assert bundle is None and "refresh token" in err
    assert ago.load_antigravity_tokens() is None


def test_expired_token_is_refreshed_once(monkeypatch):
    tokens = _sign_in()
    tokens["expires_at"] = int(time.time()) - 10
    ago.save_antigravity_tokens(tokens)
    calls = []

    def fake_form(url, fields):
        calls.append(fields)
        return 200, {"access_token": "fresh", "expires_in": 3600}

    monkeypatch.setattr(ago, "_post_form", fake_form)
    got = ago.get_fresh_antigravity_token()
    assert got["access_token"] == "fresh" and got["refresh_token"] == "1//refresh"
    assert calls[0]["grant_type"] == "refresh_token"
    assert ago.get_fresh_antigravity_token()["access_token"] == "fresh"
    assert len(calls) == 1


def test_discover_project_reads_load_code_assist(monkeypatch):
    def fake_post(url, **kw):
        assert url.endswith("/v1internal:loadCodeAssist")
        assert kw["json"]["metadata"]["ideType"] == "ANTIGRAVITY"
        return httpx.Response(200, json={"cloudaicompanionProject": {"id": "p-42"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert ago.discover_project("tok") == "p-42"


def test_discover_project_onboards_then_falls_back(monkeypatch):
    seen = []

    def fake_post(url, **kw):
        seen.append(url.rsplit(":", 1)[-1])
        if url.endswith("loadCodeAssist"):
            return httpx.Response(200, json={"allowedTiers": [{"id": "free-tier", "isDefault": True}]})
        assert kw["json"]["tierId"] == "free-tier"
        return httpx.Response(200, json={"done": True, "response": {"cloudaicompanionProject": {"id": "new-p"}}})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert ago.discover_project("tok", onboard_wait=0) == "new-p"
    assert seen == ["loadCodeAssist", "onboardUser"]


def test_callback_server_takes_a_custom_path():
    from jarvis.auth.codex_oauth_callback import pick_codex_callback_port, wait_for_codex_oauth_callback

    port = 51999
    pick_codex_callback_port(port)
    out = {}

    def run():
        out["got"] = wait_for_codex_oauth_callback(expected_state="s1", port=port, timeout=5,
                                                   path="/oauth-callback")

    t = threading.Thread(target=run)
    t.start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/oauth-callback?code=4%2Fabc&state=s1", timeout=1)
            break
        except Exception:
            time.sleep(0.05)
    t.join(5)
    assert out["got"] == ("4/abc", "s1")


# ── catalog ──────────────────────────────────────────────────────────────────


def test_parse_models_drops_internal_rows_and_ranks_best_first():
    payload = {"models": {
        "chat_20706": {"displayName": "internal"},
        "tab_flash_lite_preview": {},
        "gemini-3-pro-image": {"displayName": "Image"},
        "gemini-3-flash": {"displayName": "Gemini 3 Flash", "quotaInfo": {"remainingFraction": 0.42}},
        "claude-sonnet-4-6": {"displayName": "Claude Sonnet 4.6"},
        "gemini-3.1-pro-high": {"displayName": "Gemini 3.1 Pro (High)", "maxOutputTokens": 65535},
        "gemini-3.1-pro-low": {"displayName": "Gemini 3.1 Pro (Low)"},
        "claude-opus-4-6-thinking": {"displayName": "Claude Opus 4.6 (Thinking)"},
    }}
    ids = [m.id for m in cat.parse_models(payload)]
    assert ids == ["gemini-3.1-pro-high", "gemini-3.1-pro-low", "claude-opus-4-6-thinking",
                   "claude-sonnet-4-6", "gemini-3-flash"]
    flash = next(m for m in cat.parse_models(payload) if m.id == "gemini-3-flash")
    assert cat.quota_note(flash) == "42% left"


def test_picker_uses_seeds_until_fetched_then_live_list_only():
    from jarvis.constants.providers import antigravity_models_for_picker, connected_model_sources

    assert PROVIDER_ANTIGRAVITY not in connected_model_sources()
    _sign_in()
    assert PROVIDER_ANTIGRAVITY in connected_model_sources()
    assert [m for m, _ in antigravity_models_for_picker()] == [m for m, _ in cat.SEEDS]
    catalog_cache.write(cat.CACHE_NAME, cat._encode(cat.parse_models(
        {"models": {"gemini-3-flash": {"displayName": "Gemini 3 Flash"}}})))
    assert [m for m, _ in antigravity_models_for_picker()] == ["gemini-3-flash"]


def test_model_ids_stay_strict_to_the_line_up():
    from jarvis.constants.providers import (
        PROVIDER_ANTHROPIC, model_belongs_to_provider, model_pricing, normalize_model_for_provider,
    )

    _sign_in()
    catalog_cache.write(cat.CACHE_NAME, cat._encode(cat.parse_models({"models": {
        "claude-sonnet-4-6": {}, "gemini-3-flash": {}}})))
    assert model_belongs_to_provider("claude-sonnet-4-6", PROVIDER_ANTIGRAVITY)
    assert not model_belongs_to_provider("claude-opus-5-5", PROVIDER_ANTIGRAVITY)
    # The shared Claude ids still belong to Anthropic too.
    assert model_belongs_to_provider("claude-sonnet-5-5", PROVIDER_ANTHROPIC)
    assert model_pricing("gemini-3-flash", PROVIDER_ANTIGRAVITY) == (0.0, 0.0)
    assert normalize_model_for_provider("gpt-6-luna", PROVIDER_ANTIGRAVITY) == "claude-sonnet-4-6"
    cat.mark_unavailable("claude-sonnet-4-6")
    assert normalize_model_for_provider("claude-sonnet-4-6", PROVIDER_ANTIGRAVITY) == "gemini-3-flash"


def test_thinking_caps_follow_the_model_family():
    from jarvis.auth.thinking_caps import thinking_caps

    assert thinking_caps("gemini-3-flash", PROVIDER_ANTIGRAVITY).levels == ("minimal", "low", "medium", "high")
    pro_high = thinking_caps("gemini-3.1-pro-high", PROVIDER_ANTIGRAVITY)
    assert pro_high.mode == "fixed"
    claude = thinking_caps("claude-opus-4-6-thinking", PROVIDER_ANTIGRAVITY)
    assert claude.levels == ("low", "medium", "high") and claude.can_off
    assert thinking_caps("claude-sonnet-4-6", PROVIDER_ANTIGRAVITY).mode == "none"


# ── request shape ────────────────────────────────────────────────────────────


HISTORY = [
    {"role": "user", "content": "list files"},
    {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "let me look", "signature": "sig-abc"},
        {"type": "text", "text": "Looking."},
        {"type": "tool_use", "id": "toolu_1", "name": "run_bash", "input": {"cmd": "ls"}},
    ]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": [
            {"type": "text", "text": "a.py"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBOR"}},
        ]},
    ]},
]


def test_gemini_history_uses_skip_signature_and_named_responses():
    contents = agc.to_contents(HISTORY, model="gemini-3-flash")
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    model_parts = contents[1]["parts"]
    assert all(not p.get("thought") for p in model_parts)  # Gemini: thoughts not replayed
    call = next(p for p in model_parts if "functionCall" in p)
    assert call["functionCall"] == {"name": "run_bash", "args": {"cmd": "ls"}, "id": "toolu_1"}
    assert call["thoughtSignature"] == ANTIGRAVITY_SKIP_THOUGHT_SIGNATURE
    resp = contents[2]["parts"][0]["functionResponse"]
    assert resp == {"name": "run_bash", "response": {"output": "a.py"}, "id": "toolu_1"}
    assert contents[2]["parts"][-1] == {"inlineData": {"mimeType": "image/png", "data": "iVBOR"}}


def test_claude_history_replays_signed_thinking_first():
    contents = agc.to_contents(HISTORY, model="claude-opus-4-6-thinking")
    first = contents[1]["parts"][0]
    assert first == {"text": "let me look", "thought": True, "thoughtSignature": "sig-abc"}
    call = next(p for p in contents[1]["parts"] if "functionCall" in p)
    assert "thoughtSignature" not in call
    cfg = agc.generation_config("claude-opus-4-6-thinking", max_tokens=8192,
                                thinking={"type": "enabled", "effort": "medium"}, contents=contents)
    assert cfg["thinkingConfig"] == {"include_thoughts": True, "thinking_budget": 16384}
    assert cfg["maxOutputTokens"] == agc.CLAUDE_THINKING_MAX_OUTPUT


def test_claude_thinking_is_skipped_when_the_tool_loop_has_no_signature():
    history = json.loads(json.dumps(HISTORY))
    history[1]["content"] = history[1]["content"][1:]  # no thinking block
    contents = agc.to_contents(history, model="claude-opus-4-6-thinking")
    cfg = agc.generation_config("claude-opus-4-6-thinking", max_tokens=8192,
                                thinking={"type": "enabled", "effort": "high"}, contents=contents)
    assert "thinkingConfig" not in cfg


def test_gemini_thinking_levels():
    flash = agc.generation_config("gemini-3-flash", max_tokens=4096,
                                  thinking={"type": "enabled", "effort": "xhigh"}, contents=[])
    assert flash["thinkingConfig"] == {"includeThoughts": True, "thinkingLevel": "high"}
    off = agc.generation_config("gemini-3-flash", max_tokens=4096, thinking={"type": "disabled"}, contents=[])
    assert off["thinkingConfig"] == {"includeThoughts": False, "thinkingLevel": "minimal"}
    fixed = agc.generation_config("gemini-3-pro-low", max_tokens=4096, thinking=None, contents=[])
    assert fixed["thinkingConfig"]["thinkingLevel"] == "low"


def test_schema_cleaning():
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "mode": {"const": "fast"},
            "path": {"type": ["string", "null"], "minLength": 1},
            "n": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": 3},
            "opts": {"type": "object"},
        },
        "required": ["mode", "missing"],
    }
    gem = agc.clean_schema(schema)
    assert "$schema" not in gem and "additionalProperties" not in gem
    assert gem["properties"]["mode"] == {"enum": ["fast"], "type": "string"}
    assert gem["properties"]["path"]["type"] == "string"
    assert "minLength: 1" in gem["properties"]["path"]["description"]
    assert gem["properties"]["n"]["type"] == "integer" and "default" not in gem["properties"]["n"]
    assert gem["required"] == ["mode"]
    assert "properties" not in gem["properties"]["opts"]
    claude = agc.clean_schema(schema, placeholder=True)
    assert claude["properties"]["opts"]["required"] == [agc.PLACEHOLDER]


def test_request_is_wrapped_like_antigravity():
    body = agc.build_request(model="claude-sonnet-4-6", messages=HISTORY,
                             tools=[{"name": "run_bash", "description": "Run",
                                     "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}}}],
                             system=[{"type": "text", "text": "Be brief."}], max_tokens=1000,
                             thinking=None, project="proj-1", session_id="s1")
    assert body["project"] == "proj-1" and body["model"] == "claude-sonnet-4-6"
    assert body["requestType"] == "agent" and body["userAgent"] == "antigravity"
    req = body["request"]
    assert req["systemInstruction"]["role"] == "user"
    assert req["systemInstruction"]["parts"][0]["text"].endswith("Be brief.")
    assert req["tools"][0]["functionDeclarations"][0]["name"] == "run_bash"
    assert req["toolConfig"] == {"functionCallingConfig": {"mode": "VALIDATED"}}
    assert req["sessionId"] == "s1"


# ── end to end against a fake Cloud Code ─────────────────────────────────────


def _sse(*objs) -> bytes:
    return b"".join(b"data: " + json.dumps({"response": o}).encode() + b"\r\n\r\n" for o in objs)


def _client(handler) -> agc.AntigravityClient:
    client = agc.AntigravityClient(read_timeout=5)
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_stream_text_thinking_tool_call_and_usage():
    _sign_in()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse(
            {"candidates": [{"content": {"role": "model", "parts": [
                {"text": "hmm", "thought": True, "thoughtSignature": "SIG"}]}}]},
            {"candidates": [{"content": {"role": "model", "parts": [{"text": "Hello "}]}}]},
            {"candidates": [{"content": {"role": "model", "parts": [
                {"text": "world"},
                {"functionCall": {"name": "read_file", "args": {"path": "a.py", agc.PLACEHOLDER: True}}}]},
                "finishReason": "STOP"}],
             "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 5, "thoughtsTokenCount": 2,
                               "totalTokenCount": 18}},
        ), headers={"content-type": "text/event-stream"})

    client = _client(handler)
    with client.messages.stream(model="claude-opus-4-6-thinking", messages=[{"role": "user", "content": "hi"}],
                                system="sys", max_tokens=100,
                                thinking={"type": "enabled", "effort": "low"}) as stream:
        kinds = [k for k, _ in stream.delta_stream]
        final = stream.get_final_message()
    assert "streamGenerateContent?alt=sse" in seen["url"]
    assert seen["url"].startswith("https://daily-cloudcode-pa.sandbox.googleapis.com/")
    assert seen["headers"]["authorization"] == "Bearer ya29.tok"
    assert seen["headers"]["user-agent"].startswith("antigravity/")
    assert seen["body"]["project"] == "proj-1"
    assert "thinking" in kinds and "text" in kinds and "tool_input" in kinds
    blocks = [b.model_dump() for b in final.content]
    assert blocks[0] == {"type": "thinking", "thinking": "hmm", "signature": "SIG"}
    assert blocks[1] == {"type": "text", "text": "Hello world"}
    assert blocks[2]["type"] == "tool_use" and blocks[2]["input"] == {"path": "a.py"}
    assert final.stop_reason == "tool_use"
    assert final.usage.input_tokens == 11 and final.usage.output_tokens == 7


def test_busy_host_falls_through_and_401_refreshes(monkeypatch):
    _sign_in()
    monkeypatch.setattr(ago, "_post_form", lambda url, fields: (200, {"access_token": "new", "expires_in": 3600}))
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        hits.append((host, request.headers["authorization"]))
        if host.startswith("daily"):
            return httpx.Response(503, json={"error": {"code": 503, "message": "busy"}})
        if request.headers["authorization"] == "Bearer ya29.tok":
            return httpx.Response(401, json={"error": {"code": 401, "message": "expired"}})
        return httpx.Response(200, content=_sse(
            {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}))

    with _client(handler).messages.stream(model="gemini-3-flash",
                                          messages=[{"role": "user", "content": "hi"}]) as stream:
        final = stream.get_final_message()
    assert final.content[0].text == "ok"
    assert hits == [
        ("daily-cloudcode-pa.sandbox.googleapis.com", "Bearer ya29.tok"),
        ("autopush-cloudcode-pa.sandbox.googleapis.com", "Bearer ya29.tok"),
        ("autopush-cloudcode-pa.sandbox.googleapis.com", "Bearer new"),
    ]


def test_errors_surface_as_openai_status_errors():
    from openai import APIStatusError

    from jarvis.repl import api_errors

    _sign_in()

    def handler(request):
        return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                                   "message": "You have exhausted your capacity on this model. Your quota will reset after 2h."}})

    client = _client(handler)
    with pytest.raises(APIStatusError) as info:
        with client.messages.stream(model="gemini-3-flash", messages=[{"role": "user", "content": "hi"}]):
            pass
    assert info.value.status_code == 429
    state.provider = PROVIDER_ANTIGRAVITY
    assert api_errors.classify(info.value).kind in ("quota", "rate_limit")


def test_signed_out_is_a_401():
    from openai import APIStatusError

    with pytest.raises(APIStatusError) as info:
        with _client(lambda r: httpx.Response(200)).messages.stream(
                model="gemini-3-flash", messages=[{"role": "user", "content": "hi"}]):
            pass
    assert info.value.status_code == 401


# ── connect + web ────────────────────────────────────────────────────────────


def test_oauth_status_and_disconnect():
    from jarvis.auth.connect.oauth_actions import disconnect_oauth
    from jarvis.auth.connect.oauth_status import oauth_connection_status
    from jarvis.constants.oauth_providers import OAUTH_ID_ANTIGRAVITY, oauth_provider

    spec = oauth_provider(OAUTH_ID_ANTIGRAVITY)
    assert spec is not None and spec.runtime_provider == PROVIDER_ANTIGRAVITY
    assert not oauth_connection_status(spec).connected
    _sign_in(email="me@example.com")
    st = oauth_connection_status(spec)
    assert st.connected and "me@example.com" in st.detail
    ok, _msg, _ = disconnect_oauth(spec)
    assert ok and ago.load_antigravity_tokens() is None


def test_activate_switches_provider_and_model(monkeypatch):
    from jarvis.auth.connect.oauth_actions import activate_oauth
    from jarvis.constants.oauth_providers import OAUTH_ID_ANTIGRAVITY, oauth_provider

    monkeypatch.setattr("jarvis.auth.connect.oauth_actions._secure_write", lambda *a, **k: None)
    monkeypatch.setattr("jarvis.auth.connect.oauth_actions.adopt_provider_model",
                        lambda p: setattr(state, "MODEL", "gemini-3-flash"))
    _sign_in()
    ok, msg, ids = activate_oauth(oauth_provider(OAUTH_ID_ANTIGRAVITY))
    assert ok, msg
    assert state.provider == PROVIDER_ANTIGRAVITY
    assert isinstance(state.client, agc.AntigravityClient)
    assert ids and ids[0] == cat.SEEDS[0][0]


def test_web_card_sign_in_by_pasting_the_callback_address(monkeypatch):
    from jarvis.web import providers_api as pa

    monkeypatch.setattr(pa, "_start_codex_listener", lambda flow, run_action: None)
    monkeypatch.setattr(ago, "_post_form", lambda url, fields: (
        200, {"access_token": "a", "refresh_token": "r", "expires_in": 3600}))
    monkeypatch.setattr(ago, "_fetch_email", lambda tok: "me@example.com")
    monkeypatch.setattr(ago, "discover_project", lambda tok: "p1")
    monkeypatch.setattr(cat, "refresh_models", lambda *a, **k: None)
    actions = []

    def run_action(action, data):
        actions.append((action, data))
        return {"ok": True, "message": "Signed in"}

    row = next(r for r in pa.list_providers()["providers"] if r["id"] == "antigravity")
    assert row["kind"] == "oauth" and not row["connected"]
    started = pa.start_oauth("antigravity", run_action=run_action)
    assert "accounts.google.com" in started["url"]
    flow = pa._get_flow(started["flow"])
    pasted = f"localhost:51121/oauth-callback?state={flow.state}&code=4%2F0abc&scope=x"
    result = pa.finish_oauth(started["flow"], pasted, run_action=run_action)
    assert result["ok"], result
    assert actions == [("provider_signed_in", {"id": "antigravity"})]
    assert ago.load_antigravity_tokens()["project_id"] == "p1"
    row = next(r for r in pa.list_providers()["providers"] if r["id"] == "antigravity")
    assert row["connected"]
