"""Model ↔ provider compatibility normalization."""
from jarvis.constants.providers import (
    CODEX_DEFAULT_MODEL,
    OPENCODE_DEFAULT_MODEL,
    POLLINATIONS_DEFAULT_MODEL,
    PROVIDER_OPENAI_CODEX,
    PROVIDER_OPENCODE,
    PROVIDER_POLLINATIONS,
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


def test_pollinations_keeps_valid_model():
    assert normalize_model_for_provider("openai-fast", PROVIDER_POLLINATIONS) == "openai-fast"


def test_pollinations_rejects_foreign_model():
    assert (
        normalize_model_for_provider("claude-sonnet-4-6", PROVIDER_POLLINATIONS)
        == POLLINATIONS_DEFAULT_MODEL
    )
