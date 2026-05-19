"""Startup auth resolution when Codex OAuth is missing."""
from unittest.mock import MagicMock, patch

from jarvis.auth.client import _resolve_provider, make_client
from jarvis.constants.providers import PROVIDER_ANTHROPIC, PROVIDER_OPENAI_CODEX


def test_resolve_provider_ignores_stale_codex_pin(tmp_path, monkeypatch):
    provider_file = tmp_path / "provider"
    provider_file.write_text(PROVIDER_OPENAI_CODEX)
    monkeypatch.setattr("jarvis.constants.paths.PROVIDER_FILE", provider_file)
    monkeypatch.setattr("jarvis.auth.client.load_codex_oauth_tokens", lambda: None)
    monkeypatch.setattr("jarvis.auth.client.load_oauth_tokens", lambda: {"access_token": "a", "refresh_token": "r"})
    monkeypatch.setattr("jarvis.auth.client.KEY_FILE", tmp_path / "missing-key")
    assert _resolve_provider(interactive=True) == PROVIDER_ANTHROPIC


def test_make_client_noninteractive_skips_oauth_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.auth.client.AUTH_MODE_FILE", tmp_path / "auth_mode")
    monkeypatch.setattr("jarvis.auth.client.PROVIDER_FILE", tmp_path / "provider")
    (tmp_path / "auth_mode").write_text("oauth")
    monkeypatch.setattr("jarvis.auth.client.load_oauth_tokens", lambda: None)
    monkeypatch.setattr("jarvis.auth.client.KEY_FILE", tmp_path / "missing-key")
    assert make_client(interactive=False) is None


def test_make_client_falls_back_when_codex_oauth_missing(tmp_path, monkeypatch):
    provider_file = tmp_path / "provider"
    provider_file.write_text(PROVIDER_OPENAI_CODEX)
    monkeypatch.setattr("jarvis.constants.paths.PROVIDER_FILE", provider_file)
    monkeypatch.setattr("jarvis.auth.client._build_codex_client", lambda: None)
    monkeypatch.setattr("jarvis.auth.client._pick_fallback_provider", lambda: PROVIDER_ANTHROPIC)

    fake_client = MagicMock()
    with patch("jarvis.auth.client._build_client_from_mode", return_value=fake_client):
        with patch("jarvis.auth.client.sync_anthropic_model_ids"):
            monkeypatch.setattr("jarvis.auth.client.load_oauth_tokens", lambda: {"access_token": "a", "refresh_token": "r"})
            monkeypatch.setattr("jarvis.auth.client.AUTH_MODE_FILE", tmp_path / "auth_mode")
            monkeypatch.setattr("jarvis.auth.client.KEY_FILE", tmp_path / "key")
            client = make_client(interactive=True)
    assert client is fake_client
