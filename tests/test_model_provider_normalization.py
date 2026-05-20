"""Model ↔ provider compatibility normalization."""
from jarvis.constants.providers import (
    CODEX_DEFAULT_MODEL,
    OPENCODE_DEFAULT_MODEL,
    PROVIDER_OPENAI_CODEX,
    PROVIDER_OPENCODE,
    normalize_model_for_provider,
)


def test_codex_rejects_opencode_model():
    assert (
        normalize_model_for_provider("deepseek-v4-flash", PROVIDER_OPENAI_CODEX)
        == CODEX_DEFAULT_MODEL
    )


def test_opencode_rejects_codex_model():
    assert (
        normalize_model_for_provider("gpt-5.5", PROVIDER_OPENCODE)
        == OPENCODE_DEFAULT_MODEL
    )


def test_codex_keeps_valid_model():
    assert normalize_model_for_provider("gpt-5.4", PROVIDER_OPENAI_CODEX) == "gpt-5.4"
