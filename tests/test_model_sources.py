"""Model source / picker tests."""
from jarvis.constants.providers import (
    MODEL_SOURCE_LABELS,
    connected_model_sources,
    model_option_id,
    parse_model_option_id,
    PROVIDER_ANTHROPIC_API,
    PROVIDER_ANTHROPIC_AUTH,
)


def test_model_source_labels():
    assert MODEL_SOURCE_LABELS[PROVIDER_ANTHROPIC_API] == "Anthropic API"
    assert MODEL_SOURCE_LABELS[PROVIDER_ANTHROPIC_AUTH] == "Anthropic Auth"


def test_model_option_id_roundtrip():
    oid = model_option_id(PROVIDER_ANTHROPIC_AUTH, "claude-sonnet-4-6")
    src, mid = parse_model_option_id(oid)
    assert src == PROVIDER_ANTHROPIC_AUTH
    assert mid == "claude-sonnet-4-6"


def test_connected_model_sources_fallback():
    # With no credentials, show all sources including both Anthropic variants.
    sources = connected_model_sources()
    assert PROVIDER_ANTHROPIC_API in sources
    assert PROVIDER_ANTHROPIC_AUTH in sources
