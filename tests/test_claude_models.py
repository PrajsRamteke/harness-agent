"""Claude 5 family model registration tests.

Covers the current Anthropic lineup: Opus 5.5, Fable 5.1, Mythos 5.1, Opus 5,
Sonnet 5 — and asserts the retired 4.x entries are gone.
"""
from jarvis.auth.anthropic_models import anthropic_auth_models_for_picker
from jarvis.constants.providers import (
    ANTHROPIC_AUTH_MODEL_IDS,
    ANTHROPIC_DEFAULT_MODEL,
    ANTHROPIC_MODELS,
    MODEL_INFO,
    PROVIDER_ANTHROPIC,
    PROVIDER_ANTHROPIC_API,
    PROVIDER_ANTHROPIC_AUTH,
    models_for_source,
)

# Newest first — must match MODELS / ANTHROPIC_AUTH_MODEL_IDS ordering.
EXPECTED = [
    ("claude-opus-5-5", (4.0, 20.0)),
    ("claude-fable-5-1", (10.0, 50.0)),
    ("claude-mythos-5-1", (10.0, 50.0)),
    ("claude-opus-5", (5.0, 25.0)),
    ("claude-sonnet-5", (2.0, 10.0)),
]

RETIRED = (
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
)


def test_claude5_models_registered_with_pricing():
    for model_id, pricing in EXPECTED:
        assert model_id in MODEL_INFO, model_id
        _, provider, got_pricing = MODEL_INFO[model_id]
        assert provider == PROVIDER_ANTHROPIC
        assert got_pricing == pricing


def test_retired_anthropic_models_removed():
    for model_id in RETIRED:
        assert model_id not in MODEL_INFO, model_id


def test_anthropic_api_picker_lists_claude5_newest_first():
    assert [mid for mid, _ in ANTHROPIC_MODELS] == [mid for mid, _ in EXPECTED]


def test_auth_catalog_is_newest_first():
    assert list(ANTHROPIC_AUTH_MODEL_IDS) == [mid for mid, _ in EXPECTED]
    assert ANTHROPIC_AUTH_MODEL_IDS[0] == "claude-opus-5-5"


def test_claude5_models_in_both_picker_sources():
    api_ids = [mid for mid, _ in models_for_source(PROVIDER_ANTHROPIC_API)]
    auth_ids = [mid for mid, _ in models_for_source(PROVIDER_ANTHROPIC_AUTH)]
    for model_id, _ in EXPECTED:
        assert model_id in api_ids
        assert model_id in auth_ids


def test_sonnet_5_is_the_anthropic_default():
    assert ANTHROPIC_DEFAULT_MODEL == "claude-sonnet-5"


def test_auth_picker_orders_newest_first_without_live_ids():
    from jarvis import state

    state.anthropic_model_ids = None
    rows = anthropic_auth_models_for_picker()
    assert rows[0][0] == "claude-opus-5-5"
    state.anthropic_model_ids = None
