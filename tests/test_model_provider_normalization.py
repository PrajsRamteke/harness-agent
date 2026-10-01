"""Model ↔ provider compatibility normalization."""
from jarvis.constants.providers import (
    CODEX_DEFAULT_MODEL,
    PROVIDER_OPENAI_CODEX,
    PROVIDER_OPENCODE,
    normalize_model_for_provider,
    opencode_go_default_model,
)


def test_codex_rejects_opencode_model():
    assert (
        normalize_model_for_provider("deepseek-v4-flash", PROVIDER_OPENAI_CODEX)
        == CODEX_DEFAULT_MODEL
    )


def test_opencode_rejects_codex_model():
    # OpenCode Go's default is live (models.dev); the Codex id is never kept.
    assert normalize_model_for_provider("gpt-5.5", PROVIDER_OPENCODE) == opencode_go_default_model()
    assert normalize_model_for_provider("gpt-5.5", PROVIDER_OPENCODE) != "gpt-5.5"


def test_codex_keeps_valid_model():
    assert (
        normalize_model_for_provider("gpt-5.6-terra", PROVIDER_OPENAI_CODEX)
        == "gpt-5.6-terra"
    )
