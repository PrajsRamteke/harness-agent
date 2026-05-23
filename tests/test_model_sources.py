"""Model source / picker tests."""
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
    assert harness[0][1] == "deepseek-v4-flash-free"
    assert rows[0][0] == PROVIDER_HARNESS_AGENT


def test_harness_agent_models_for_picker_never_empty():
    models = harness_agent_models_for_picker()
    assert len(models) >= 3


def test_connected_model_sources_includes_anthropic_variants():
    sources = connected_model_sources()
    # At least one Anthropic source appears; both when nothing is configured.
    assert PROVIDER_ANTHROPIC_API in sources or PROVIDER_ANTHROPIC_AUTH in sources
