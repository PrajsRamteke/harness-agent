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


def test_make_client_first_run_uses_harness_agent(tmp_path, monkeypatch):
    """Stale auth marker files without tokens should still boot Harness Agent."""
    monkeypatch.setattr("jarvis.auth.client.AUTH_MODE_FILE", tmp_path / "auth_mode")
    monkeypatch.setattr("jarvis.auth.client.PROVIDER_FILE", tmp_path / "provider")
    monkeypatch.setattr("jarvis.auth.client.KEY_FILE", tmp_path / "missing-key")
    (tmp_path / "auth_mode").write_text("oauth")
    monkeypatch.setattr("jarvis.auth.client.load_oauth_tokens", lambda: None)
    monkeypatch.setattr("jarvis.auth.client.load_codex_oauth_tokens", lambda: None)
    monkeypatch.setattr("jarvis.auth.client._has_usable_provider_credentials", lambda: False)
    monkeypatch.setattr("jarvis.auth.harness_agent.build_harness_agent_client", lambda: MagicMock())
    from jarvis import state
    from jarvis.constants.providers import HARNESS_AGENT_DEFAULT_MODEL, PROVIDER_OPENCODE_ZEN
    client = make_client(interactive=False)
    assert client is not None
    assert state.provider == PROVIDER_OPENCODE_ZEN
    assert state.MODEL == HARNESS_AGENT_DEFAULT_MODEL
    assert state.harness_agent_free is True


def test_make_client_falls_back_when_codex_oauth_missing(tmp_path, monkeypatch):
    provider_file = tmp_path / "provider"
    provider_file.write_text(PROVIDER_OPENAI_CODEX)
    monkeypatch.setattr("jarvis.auth.client.PROVIDER_FILE", provider_file)
    monkeypatch.setattr("jarvis.constants.paths.PROVIDER_FILE", provider_file)
    monkeypatch.setattr("jarvis.auth.client._build_codex_client", lambda: None)
    monkeypatch.setattr(
        "jarvis.auth.client._pick_fallback_provider",
        lambda **kwargs: PROVIDER_ANTHROPIC,
    )

    fake_client = MagicMock()
    with patch("jarvis.auth.client._build_client_from_mode", return_value=fake_client):
        with patch("jarvis.auth.client.sync_anthropic_model_ids"):
            monkeypatch.setattr("jarvis.auth.client.load_oauth_tokens", lambda: {"access_token": "a", "refresh_token": "r"})
            monkeypatch.setattr("jarvis.auth.client.AUTH_MODE_FILE", tmp_path / "auth_mode")
            monkeypatch.setattr("jarvis.auth.client.KEY_FILE", tmp_path / "key")
            client = make_client(interactive=True)
    assert client is fake_client
