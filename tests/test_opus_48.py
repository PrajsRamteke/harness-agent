"""Claude Opus 4.8 model registration tests."""
from jarvis.auth.anthropic_models import anthropic_auth_models_for_picker
from jarvis.constants.providers import (
    ANTHROPIC_AUTH_MODEL_IDS,
    ANTHROPIC_MODELS,
    MODEL_INFO,
    PROVIDER_ANTHROPIC,
    PROVIDER_ANTHROPIC_API,
    PROVIDER_ANTHROPIC_AUTH,
    models_for_source,
)

OPUS_48 = "claude-opus-4-8"


def test_opus_48_in_model_info():
    assert OPUS_48 in MODEL_INFO
    desc, provider, pricing = MODEL_INFO[OPUS_48]
    assert provider == PROVIDER_ANTHROPIC
    assert "4.8" in desc
    assert pricing == (5.0, 25.0)


def test_opus_48_in_anthropic_api_models():
    api_ids = [mid for mid, _ in ANTHROPIC_MODELS]
    assert OPUS_48 in api_ids


def test_opus_48_first_in_auth_catalog():
    assert ANTHROPIC_AUTH_MODEL_IDS[0] == OPUS_48


def test_opus_48_in_model_picker_sources():
    api_ids = [mid for mid, _ in models_for_source(PROVIDER_ANTHROPIC_API)]
    auth_ids = [mid for mid, _ in models_for_source(PROVIDER_ANTHROPIC_AUTH)]
    assert OPUS_48 in api_ids
    assert OPUS_48 in auth_ids


def test_opus_48_first_in_auth_picker():
    from jarvis import state

    state.anthropic_model_ids = None
    rows = anthropic_auth_models_for_picker()
    assert rows[0][0] == OPUS_48
    state.anthropic_model_ids = None
