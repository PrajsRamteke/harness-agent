"""OpenAI Codex OAuth tests (no live network)."""
import json
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import patch

from jarvis.auth.codex_oauth_callback import wait_for_codex_oauth_callback
from jarvis.auth.codex_oauth_tokens import (
    build_codex_authorize_url,
    exchange_codex_api_key,
    exchange_codex_oauth_code,
    persist_codex_oauth_bundle,
)
from jarvis.auth.connect.oauth_actions import activate_oauth, disconnect_oauth, is_active_oauth
from jarvis.auth.connect.oauth_status import oauth_connection_status
from jarvis.auth.pkce import _pkce_pair
from jarvis.constants.oauth_providers import OAUTH_ID_ANTHROPIC, OAUTH_ID_OPENAI_CODEX, oauth_provider


def test_build_codex_authorize_url_includes_pkce_and_originator():
    _v, challenge, state = _pkce_pair()
    url = build_codex_authorize_url(
        client_id="app_test",
        redirect_uri="http://localhost:1455/auth/callback",
        code_challenge=challenge,
        state=state,
    )
    assert "auth.openai.com/oauth/authorize" in url
    assert "codex_cli_simplified_flow=true" in url
    assert f"state={state}" in url or f"state={state.replace('-', '%2D')}" in url


def test_exchange_codex_oauth_code_requires_verifier():
    status, body = exchange_codex_oauth_code("", "")
    assert status == 400
    assert isinstance(body, dict)


@patch("jarvis.auth.codex_oauth_tokens._http_form")
def test_exchange_codex_oauth_code_posts_form(mock_form):
    mock_form.return_value = (200, {"access_token": "a", "refresh_token": "r", "id_token": "i"})
    status, body = exchange_codex_oauth_code("code123", "verifier", redirect_uri="http://localhost:1455/auth/callback")
    assert status == 200
    assert body["access_token"] == "a"
    args = mock_form.call_args
    assert args[0][1]["grant_type"] == "authorization_code"
    assert args[0][1]["code_verifier"] == "verifier"


@patch("jarvis.auth.codex_oauth_tokens._http_form")
def test_exchange_codex_api_key(mock_form):
    mock_form.return_value = (200, {"access_token": "sk-codex"})
    status, body = exchange_codex_api_key("id.jwt.token")
    assert status == 200
    assert body["access_token"] == "sk-codex"
    assert mock_form.call_args[0][1]["requested_token"] == "openai-api-key"


def test_persist_codex_oauth_bundle(tmp_path, monkeypatch):
    path = tmp_path / "codex_oauth.json"
    monkeypatch.setattr("jarvis.auth.codex_oauth_tokens.CODEX_OAUTH_FILE", path)
    bundle = persist_codex_oauth_bundle(
        {"access_token": "acc", "refresh_token": "ref", "id_token": "id"},
        api_key="sk-test",
    )
    assert bundle["openai_api_key"] == "sk-test"
    saved = json.loads(path.read_text())
    assert saved["access_token"] == "acc"
    assert saved["refresh_token"] == "ref"


def test_callback_server_receives_code():
    port = 19876
    result: dict = {}

    def run():
        try:
            code, state = wait_for_codex_oauth_callback(
                expected_state="state-xyz",
                port=port,
                timeout=5.0,
            )
            result["ok"] = (code, state)
        except Exception as e:
            result["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.3)
    url = f"http://127.0.0.1:{port}/auth/callback?code=abc123&state=state-xyz"
    with urllib.request.urlopen(url, timeout=2) as resp:
        assert resp.status == 200
    t.join(timeout=3)
    assert result.get("ok") == ("abc123", "state-xyz")


def test_oauth_providers_codex_available():
    spec = oauth_provider(OAUTH_ID_OPENAI_CODEX)
    assert spec is not None
    assert spec.available is True


def test_oauth_status_anthropic_and_codex_independent(tmp_path, monkeypatch):
    anthropic_path = tmp_path / "oauth.json"
    codex_path = tmp_path / "codex_oauth.json"
    monkeypatch.setattr("jarvis.auth.oauth_tokens.OAUTH_FILE", anthropic_path)
    monkeypatch.setattr("jarvis.auth.codex_oauth_tokens.CODEX_OAUTH_FILE", codex_path)

    anthropic = oauth_provider(OAUTH_ID_ANTHROPIC)
    codex = oauth_provider(OAUTH_ID_OPENAI_CODEX)
    assert anthropic and codex

    assert oauth_connection_status(anthropic).connected is False
    assert oauth_connection_status(codex).connected is False

    codex_path.write_text(json.dumps({
        "access_token": "a",
        "refresh_token": "r",
        "id_token": "i",
        "expires_at": 9999999999,
    }))
    assert oauth_connection_status(codex).connected is True
    assert oauth_connection_status(anthropic).connected is False


def test_activate_and_disconnect_codex_oauth(tmp_path, monkeypatch):
    from jarvis import state

    codex_path = tmp_path / "codex_oauth.json"
    monkeypatch.setattr("jarvis.auth.codex_oauth_tokens.CODEX_OAUTH_FILE", codex_path)
    monkeypatch.setattr("jarvis.constants.paths.PROVIDER_FILE", tmp_path / "provider")
    monkeypatch.setattr("jarvis.constants.paths.AUTH_MODE_FILE", tmp_path / "auth_mode")

    codex_path.write_text(json.dumps({
        "access_token": "acc",
        "refresh_token": "ref",
        "id_token": "id",
        "expires_at": 9999999999,
    }))
    spec = oauth_provider(OAUTH_ID_OPENAI_CODEX)
    assert spec

    with patch("jarvis.auth.connect.oauth_actions._build_codex_client") as mock_client:
        mock_client.return_value.validate = lambda: True
        ok, msg, model_ids = activate_oauth(spec)
        assert ok is True
        assert model_ids
        assert is_active_oauth(spec)

    ok, msg, _ = disconnect_oauth(spec)
    assert ok is True
    assert not codex_path.exists()
    assert is_active_oauth(spec) is False
