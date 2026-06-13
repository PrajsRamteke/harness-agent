"""Model preference survives restarts and startup fallbacks."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from jarvis.bootstrap import ensure_harness_agent_defaults
from jarvis.constants.providers import HARNESS_AGENT_DEFAULT_MODEL, PROVIDER_ANTHROPIC
from jarvis.storage.settings import Settings


@pytest.fixture
def global_settings(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr("jarvis.storage.settings.SETTINGS_FILE", settings_file)
    monkeypatch.setattr("jarvis.storage.settings.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("jarvis.storage.settings._singleton", None)
    return settings_file


def test_save_last_model_writes_global_only(global_settings, monkeypatch):
    from jarvis import state
    from jarvis.storage.prefs import load_saved_model, save_last_model
    from jarvis.storage.settings import Settings

    monkeypatch.setattr(
        "jarvis.storage.settings.get_settings",
        lambda: Settings(global_settings),
    )

    state.MODEL = "claude-sonnet-4-6"
    state.provider = PROVIDER_ANTHROPIC
    save_last_model()

    assert load_saved_model() == "claude-sonnet-4-6"
    doc = json.loads(global_settings.read_text())
    assert doc["model"] == "claude-sonnet-4-6"
    assert doc["provider"] == PROVIDER_ANTHROPIC


def test_project_settings_do_not_override_saved_model(global_settings, tmp_path, monkeypatch):
    global_settings.write_text(
        json.dumps({"model": "claude-sonnet-4-6", "provider": "anthropic"}) + "\n"
    )
    project_dir = tmp_path / "proj" / ".harness"
    project_dir.mkdir(parents=True)
    (project_dir / "settings.json").write_text(
        json.dumps({"model": "deepseek-v4-flash-free"}) + "\n"
    )
    monkeypatch.chdir(tmp_path / "proj")
    monkeypatch.setattr(
        "jarvis.storage.settings._read_project_settings",
        lambda: {"model": "deepseek-v4-flash-free"},
    )

    s = Settings(global_settings)
    s.load()
    assert s.get("model") == "claude-sonnet-4-6"


def test_bootstrap_respects_saved_model(monkeypatch):
    monkeypatch.setattr("jarvis.storage.prefs.should_use_first_run_harness_defaults", lambda: False)

    from jarvis import state

    state.MODEL = "claude-sonnet-4-6"
    state.provider = "anthropic"
    ensure_harness_agent_defaults()
    assert state.MODEL == "claude-sonnet-4-6"


def test_first_install_uses_harness_even_with_credentials(tmp_path, monkeypatch, global_settings):
    """Fresh install must not auto-jump to Anthropic when API keys exist on disk."""
    provider_file = tmp_path / "provider"
    provider_file.write_text("anthropic")
    monkeypatch.setattr("jarvis.auth.client.PROVIDER_FILE", provider_file)
    monkeypatch.setattr("jarvis.constants.paths.PROVIDER_FILE", provider_file)
    monkeypatch.setattr("jarvis.auth.client.KEY_FILE", tmp_path / "key")
    (tmp_path / "key").write_text("sk-test-key")
    monkeypatch.setattr("jarvis.storage.prefs.load_saved_model", lambda: "")
    monkeypatch.setattr("jarvis.storage.prefs.should_use_first_run_harness_defaults", lambda: True)
    monkeypatch.setattr("jarvis.auth.client._build_opencode_zen_client_for_model", lambda *a, **k: MagicMock())
    monkeypatch.setattr("jarvis.auth.harness_agent.build_harness_agent_client", lambda: MagicMock())

    from jarvis import state
    from jarvis.auth.client import make_client
    from jarvis.constants.providers import HARNESS_AGENT_DEFAULT_MODEL, PROVIDER_OPENCODE_ZEN

    client = make_client(interactive=False)
    assert client is not None
    assert state.provider == PROVIDER_OPENCODE_ZEN
    assert state.MODEL == HARNESS_AGENT_DEFAULT_MODEL
    assert state.harness_agent_free is True


def test_stale_provider_file_does_not_override_saved_model(
    tmp_path, monkeypatch, global_settings
):
    """Saved settings win over a stale ~/.config/harness-agent/provider file."""
    global_settings.write_text(
        json.dumps({"model": "deepseek-v4-flash-free", "provider": "opencode_zen"}) + "\n"
    )
    provider_file = tmp_path / "provider"
    provider_file.write_text("anthropic")
    key_file = tmp_path / "key"
    key_file.write_text("sk-test")
    monkeypatch.setattr("jarvis.auth.client.PROVIDER_FILE", provider_file)
    monkeypatch.setattr("jarvis.constants.paths.PROVIDER_FILE", provider_file)
    monkeypatch.setattr("jarvis.auth.client.KEY_FILE", key_file)
    monkeypatch.setattr("jarvis.storage.prefs.load_saved_model", lambda: "deepseek-v4-flash-free")
    monkeypatch.setattr(
        "jarvis.storage.prefs.load_saved_preferences",
        lambda: ("deepseek-v4-flash-free", "opencode_zen"),
    )
    monkeypatch.setattr("jarvis.storage.prefs.should_use_first_run_harness_defaults", lambda: False)
    monkeypatch.setattr("jarvis.auth.client._build_opencode_zen_client_for_model", lambda *a, **k: MagicMock())

    from jarvis import state
    from jarvis.auth.client import make_client
    from jarvis.constants.providers import PROVIDER_OPENCODE_ZEN

    client = make_client(interactive=False)
    assert client is not None
    assert state.MODEL == "deepseek-v4-flash-free"
    assert state.provider == PROVIDER_OPENCODE_ZEN
    assert state.harness_agent_free is True
    assert provider_file.read_text().strip() == PROVIDER_OPENCODE_ZEN


def test_make_client_does_not_clobber_saved_model_on_free_tier_fallback(
    tmp_path, monkeypatch, global_settings
):
    global_settings.write_text(
        json.dumps({"model": "claude-sonnet-4-6", "provider": "anthropic"}) + "\n"
    )
    provider_file = tmp_path / "provider"
    monkeypatch.setattr("jarvis.auth.client.PROVIDER_FILE", provider_file)
    monkeypatch.setattr("jarvis.constants.paths.PROVIDER_FILE", provider_file)
    monkeypatch.setattr("jarvis.auth.client.AUTH_MODE_FILE", tmp_path / "auth_mode")
    monkeypatch.setattr("jarvis.auth.client.KEY_FILE", tmp_path / "missing-key")
    monkeypatch.setattr("jarvis.auth.client._has_usable_provider_credentials", lambda: False)
    monkeypatch.setattr("jarvis.auth.client._resolve_provider", lambda **kwargs: "opencode_zen")
    monkeypatch.setattr("jarvis.storage.prefs.load_saved_model", lambda: "claude-sonnet-4-6")
    monkeypatch.setattr("jarvis.storage.prefs.should_use_first_run_harness_defaults", lambda: False)
    monkeypatch.setattr("jarvis.auth.client._build_opencode_zen_client_for_model", lambda *a, **k: MagicMock())
    monkeypatch.setattr("jarvis.auth.harness_agent.build_harness_agent_client", lambda: MagicMock())

    from jarvis import state
    from jarvis.auth.client import make_client
    from jarvis.constants.providers import PROVIDER_OPENCODE_ZEN

    state.MODEL = "claude-sonnet-4-6"
    client = make_client(interactive=False)
    assert client is not None
    assert state.MODEL == "claude-sonnet-4-6"
    assert state.provider == PROVIDER_OPENCODE_ZEN
    assert json.loads(global_settings.read_text())["model"] == "claude-sonnet-4-6"


def test_make_client_first_run_without_saved_model_uses_harness_default(
    tmp_path, monkeypatch, global_settings
):
    provider_file = tmp_path / "provider"
    monkeypatch.setattr("jarvis.auth.client.PROVIDER_FILE", provider_file)
    monkeypatch.setattr("jarvis.constants.paths.PROVIDER_FILE", provider_file)
    monkeypatch.setattr("jarvis.auth.client.AUTH_MODE_FILE", tmp_path / "auth_mode")
    monkeypatch.setattr("jarvis.auth.client.KEY_FILE", tmp_path / "missing-key")
    (tmp_path / "auth_mode").write_text("oauth")
    monkeypatch.setattr("jarvis.auth.client.load_oauth_tokens", lambda: None)
    monkeypatch.setattr("jarvis.auth.client.load_codex_oauth_tokens", lambda: None)
    monkeypatch.setattr("jarvis.auth.client._has_usable_provider_credentials", lambda: False)
    monkeypatch.setattr("jarvis.auth.client._resolve_provider", lambda **kwargs: "opencode_zen")
    monkeypatch.setattr("jarvis.storage.prefs.load_saved_model", lambda: "")
    monkeypatch.setattr("jarvis.storage.prefs.should_use_first_run_harness_defaults", lambda: True)
    monkeypatch.setattr("jarvis.auth.client._build_opencode_zen_client_for_model", lambda *a, **k: MagicMock())
    monkeypatch.setattr("jarvis.auth.harness_agent.build_harness_agent_client", lambda: MagicMock())

    from jarvis import state
    from jarvis.auth.client import make_client
    from jarvis.constants.providers import PROVIDER_OPENCODE_ZEN

    state.MODEL = HARNESS_AGENT_DEFAULT_MODEL
    client = make_client(interactive=False)
    assert client is not None
    assert state.provider == PROVIDER_OPENCODE_ZEN
    assert state.MODEL == HARNESS_AGENT_DEFAULT_MODEL
    assert state.harness_agent_free is True
