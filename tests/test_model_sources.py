"""Model source / picker tests."""
import pytest

from jarvis.constants.providers import (
    MODEL_SOURCE_LABELS,
    all_model_picker_rows,
    connected_model_sources,
    harness_agent_models_for_picker,
    model_option_id,
    parse_model_option_id,
    PROVIDER_ANTHROPIC_API,
    PROVIDER_ANTHROPIC_AUTH,
    PROVIDER_HARNESS_AGENT,
    PROVIDER_KIMCHI,
    PROVIDER_OPENAI_CODEX_AUTH,
    PROVIDER_OPENROUTER,
)


def test_model_source_labels():
    assert MODEL_SOURCE_LABELS[PROVIDER_ANTHROPIC_API] == "Anthropic API"
    assert MODEL_SOURCE_LABELS[PROVIDER_ANTHROPIC_AUTH] == "Anthropic Auth"


def test_model_option_id_roundtrip():
    oid = model_option_id(PROVIDER_ANTHROPIC_AUTH, "claude-sonnet-4-6")
    src, mid = parse_model_option_id(oid)
    assert src == PROVIDER_ANTHROPIC_AUTH
    assert mid == "claude-sonnet-4-6"


def test_connected_model_sources_includes_harness_agent():
    sources = connected_model_sources()
    assert PROVIDER_HARNESS_AGENT in sources
    assert sources[0] == PROVIDER_HARNESS_AGENT


def test_all_model_picker_rows_always_includes_harness_agent():
    rows = all_model_picker_rows()
    harness = [(src, mid) for src, mid, _ in rows if src == PROVIDER_HARNESS_AGENT]
    assert len(harness) >= 3
    assert harness[0][1] == "mimo-v2.5-free"
    assert rows[0][0] == PROVIDER_HARNESS_AGENT


def test_harness_agent_models_for_picker_never_empty():
    models = harness_agent_models_for_picker()
    assert len(models) >= 3


# ── /model shows a provider only while its credential exists ─────────────────

_KEY_ENV_VARS = (
    "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "OPENCODE_API_KEY",
    "OPENCODE_ZEN_API_KEY", "KIMCHI_API_KEY",
)


@pytest.fixture()
def no_credentials(tmp_path, monkeypatch):
    """Every key file points into tmp_path (absent) and no OAuth tokens."""
    import jarvis.constants.paths as paths

    for var in _KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for name in ("KEY_FILE", "OPENROUTER_KEY_FILE", "OPENCODE_KEY_FILE",
                 "OPENCODE_ZEN_KEY_FILE", "KIMCHI_KEY_FILE"):
        monkeypatch.setattr(paths, name, tmp_path / name.lower())
    monkeypatch.setattr("jarvis.auth.oauth_tokens.load_oauth_tokens", lambda: None)
    monkeypatch.setattr("jarvis.auth.codex_oauth_tokens.load_codex_oauth_tokens", lambda: None)
    # Offline: no discovered OpenRouter models from the user's cache.
    monkeypatch.setattr("jarvis.auth.openrouter_catalog.free_models", lambda live=False: [])
    return paths


def _sources(rows):
    return {src for src, _mid, _desc in rows}


def test_without_credentials_only_harness_agent_is_listed(no_credentials):
    assert connected_model_sources() == [PROVIDER_HARNESS_AGENT]
    rows = all_model_picker_rows(cached=True)
    assert _sources(rows) == {PROVIDER_HARNESS_AGENT}
    assert len(rows) >= 3


def test_api_key_provider_listed_only_while_key_exists(no_credentials):
    key_file = no_credentials.OPENROUTER_KEY_FILE
    key_file.write_text("sk-or-test\n")
    assert PROVIDER_OPENROUTER in connected_model_sources()
    assert PROVIDER_OPENROUTER in _sources(all_model_picker_rows(cached=True))

    key_file.unlink()  # removed mid-session → gone on the next /model
    assert PROVIDER_OPENROUTER not in connected_model_sources()
    assert _sources(all_model_picker_rows(cached=True)) == {PROVIDER_HARNESS_AGENT}


def test_env_key_counts_as_configured(no_credentials, monkeypatch):
    monkeypatch.setenv("KIMCHI_API_KEY", "k")
    assert connected_model_sources() == [PROVIDER_HARNESS_AGENT, PROVIDER_KIMCHI]


def test_oauth_sources_follow_stored_tokens(no_credentials, monkeypatch):
    tokens = {"access_token": "a", "refresh_token": "r"}
    monkeypatch.setattr("jarvis.auth.oauth_tokens.load_oauth_tokens", lambda: tokens)
    monkeypatch.setattr("jarvis.auth.codex_oauth_tokens.load_codex_oauth_tokens", lambda: tokens)
    sources = connected_model_sources()
    assert sources[0] == PROVIDER_HARNESS_AGENT
    assert PROVIDER_ANTHROPIC_AUTH in sources and PROVIDER_OPENAI_CODEX_AUTH in sources
    assert PROVIDER_ANTHROPIC_API not in sources  # OAuth alone ≠ API key
