"""Live discovery of OpenRouter's free tier."""
from unittest import mock

import pytest

from jarvis.auth import catalog_cache, openrouter_catalog as oc
from jarvis.constants import providers


def _entry(mid, *, prompt="0", completion="0", name=None, ctx=262144,
           modalities=("text",), out=("text",), params=("tools",)):
    return {
        "id": mid,
        "name": name or mid,
        "context_length": ctx,
        "pricing": {"prompt": prompt, "completion": completion},
        "architecture": {"input_modalities": list(modalities),
                         "output_modalities": list(out)},
        "supported_parameters": list(params),
    }


PAYLOAD = {"data": [
    _entry("vendor/free-big:free", name="Vendor: Free Big (free)", ctx=1_000_000),
    _entry("vendor/free-small:free", name="Vendor: Free Small (free)", ctx=65536),
    _entry("vendor/free-vision:free", modalities=("text", "image")),
    _entry("vendor/free-no-tools:free", params=()),
    _entry("vendor/audio-gen", out=("audio",)),
    _entry("vendor/paid", prompt="0.000002", completion="0.000004"),
]}


def test_fetch_keeps_only_free_text_models():
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD):
        ids = [m.id for m in oc.fetch_free_models()]
    assert "vendor/paid" not in ids, "priced models must not appear as free"
    assert "vendor/audio-gen" not in ids, "non-text output is unusable here"
    assert set(ids) == {
        "vendor/free-big:free", "vendor/free-small:free",
        "vendor/free-vision:free", "vendor/free-no-tools:free",
    }


def test_tool_capable_models_sort_first_then_widest_context():
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD):
        models = oc.fetch_free_models()
    assert models[0].id == "vendor/free-big:free"   # 1M ctx, tools
    assert models[-1].id == "vendor/free-no-tools:free"


def test_tool_less_models_are_listed_but_labelled_and_last():
    """Every free model shows up — a missing row is more confusing than a
    caveated one — but the ones that can't run here sort to the bottom."""
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD), \
         mock.patch.object(oc, "HIDE_NO_TOOLS", False), \
         mock.patch.object(catalog_cache, "write", lambda *a, **k: None):
        models = oc.free_models(live=True)
    by_id = {m.id: m for m in models}
    assert "vendor/free-no-tools:free" in by_id
    assert "no tool use" in by_id["vendor/free-no-tools:free"].label
    assert models[-1].id == "vendor/free-no-tools:free"
    assert not by_id["vendor/free-no-tools:free"].usable


def test_tool_less_models_can_be_hidden_on_request():
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD), \
         mock.patch.object(oc, "HIDE_NO_TOOLS", True), \
         mock.patch.object(catalog_cache, "write", lambda *a, **k: None):
        ids = [m.id for m in oc.free_models(live=True)]
    assert "vendor/free-no-tools:free" not in ids


def test_vision_support_is_carried_through():
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD):
        by_id = {m.id: m for m in oc.fetch_free_models()}
    assert by_id["vendor/free-vision:free"].supports_images
    assert not by_id["vendor/free-big:free"].supports_images


def test_label_drops_redundant_free_suffix_and_shows_context():
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD):
        by_id = {m.id: m for m in oc.fetch_free_models()}
    assert by_id["vendor/free-big:free"].label == "Vendor: Free Big — 1M ctx, free"
    assert by_id["vendor/free-small:free"].label == "Vendor: Free Small — 65K ctx, free"


def test_fetch_returns_none_when_offline():
    with mock.patch.object(oc, "_get_json", side_effect=OSError("offline")):
        assert oc.fetch_free_models() is None


def test_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path)
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD):
        oc.refresh_free_models()
    assert oc.cache_is_fresh()
    cached = {m.id: m for m in oc.cached_free_models()}
    assert cached["vendor/free-vision:free"].supports_images
    assert cached["vendor/free-big:free"].context_length == 1_000_000


def test_cached_read_never_touches_network(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path)
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD):
        oc.refresh_free_models()
    with mock.patch.object(oc, "_get_json", side_effect=AssertionError("network used")):
        assert oc.free_models() and oc.cached_free_models()


def test_expired_cache_is_still_served(tmp_path, monkeypatch):
    """Stale-while-revalidate: an old entry beats an empty picker."""
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path)
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD):
        oc.refresh_free_models()
    monkeypatch.setenv("HARNESS_MODEL_CATALOG_TTL", "0")
    assert not oc.cache_is_fresh()
    assert oc.cached_free_models()


def test_cold_cache_is_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path / "nothing-here")
    assert oc.cached_free_models() == []
    assert not oc.cache_is_fresh()


# ─── integration with the provider registry ──────────────────────────────

def test_every_free_model_reaches_the_picker():
    """All $0 models are offered, including ones flagged as unusable."""
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD), \
         mock.patch.object(catalog_cache, "write", lambda *a, **k: None), \
         mock.patch.object(oc, "blocked_ids", return_value=set()):
        ids = [m.id for m in oc.free_models(live=True)]
    assert set(ids) == {
        "vendor/free-big:free", "vendor/free-small:free",
        "vendor/free-vision:free", "vendor/free-no-tools:free",
    }


def test_picker_lists_discovered_free_models_before_seeds():
    free = [oc.FreeModel("vendor/new-free:free", "Vendor: New Free — free")]
    with mock.patch.object(oc, "free_models", return_value=free):
        rows = providers.openrouter_models_for_picker()
    ids = [mid for mid, _ in rows]
    assert ids[0] == "vendor/new-free:free"
    # Seeds remain as an offline floor, and nothing is duplicated.
    assert "deepseek/deepseek-v4.1-flash" in ids
    assert len(ids) == len(set(ids))


def test_discovered_models_register_pricing_and_vision():
    free = [oc.FreeModel("vendor/vision-free:free", "Vision Free", supports_images=True)]
    with mock.patch.object(oc, "free_models", return_value=free):
        providers.openrouter_models_for_picker()
    assert providers.PRICING["vendor/vision-free:free"] == (0.0, 0.0)
    assert providers.model_supports_images("vendor/vision-free:free")
    assert providers.infer_provider_for_model("vendor/vision-free:free") == \
        providers.PROVIDER_OPENROUTER


def test_curated_specs_win_over_discovered_pricing():
    """A ModelSpec's curated price must not be clobbered by the live catalog."""
    before = providers.PRICING["deepseek/deepseek-v4.1-flash"]
    providers.register_dynamic_model(
        "deepseek/deepseek-v4.1-flash", "bogus", providers.PROVIDER_OPENROUTER
    )
    assert providers.PRICING["deepseek/deepseek-v4.1-flash"] == before


def test_default_model_follows_the_live_catalog():
    free = [oc.FreeModel("vendor/top-free:free", "Top Free")]
    with mock.patch.object(oc, "usable_free_models", return_value=free):
        assert providers.openrouter_default_model() == "vendor/top-free:free"


def test_default_model_never_picks_an_unusable_model(tmp_path, monkeypatch):
    """A tool-less model is listed for the user but must not be auto-selected."""
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path)
    only_toolless = {"data": [
        _entry("vendor/no-tools:free", params=()),
        _entry("vendor/works:free", ctx=1000),
    ]}
    with mock.patch.object(oc, "_get_json", return_value=only_toolless):
        oc.refresh_free_models()
    assert providers.openrouter_default_model() == "vendor/works:free"


def test_default_model_falls_back_to_seed_when_catalog_is_cold():
    with mock.patch.object(oc, "usable_free_models", return_value=[]):
        assert providers.openrouter_default_model() == providers.OPENROUTER_DEFAULT_MODEL


def test_normalize_uses_live_default_for_openrouter():
    free = [oc.FreeModel("vendor/top-free:free", "Top Free")]
    with mock.patch.object(oc, "usable_free_models", return_value=free):
        got = providers.normalize_model_for_provider(
            "claude-opus-4-8", providers.PROVIDER_OPENROUTER
        )
    assert got == "vendor/top-free:free"


# ─── self-healing when OpenRouter refuses a model ────────────────────────

def test_refused_model_is_marked_restricted_not_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path)
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD):
        oc.refresh_free_models()
    assert oc.free_models()[0].id == "vendor/free-big:free"

    oc.mark_unavailable("vendor/free-big:free")
    assert "vendor/free-big:free" in oc.blocked_ids()
    models = oc.free_models()
    by_id = {m.id: m for m in models}
    # Still listed — vanishing rows are what made this confusing to begin with.
    assert "vendor/free-big:free" in by_id
    assert by_id["vendor/free-big:free"].restricted
    assert "restricted" in by_id["vendor/free-big:free"].label
    assert not by_id["vendor/free-big:free"].usable
    assert models[-1].id == "vendor/free-big:free"
    # ...but never auto-selected.
    assert "vendor/free-big:free" not in {m.id for m in oc.usable_free_models()}


def test_refusals_age_out(tmp_path, monkeypatch):
    """A model that gets un-gated upstream comes back on its own."""
    import time

    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path)
    oc.mark_unavailable("vendor/free-big:free")
    monkeypatch.setattr(catalog_cache, "read",
                        lambda name: ({"vendor/free-big:free": time.time() - oc.BLOCKED_TTL - 1}, True))
    assert oc.blocked_ids() == set()


def test_explicit_refresh_retries_refused_models(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path)
    oc.mark_unavailable("vendor/free-big:free")
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD):
        oc.refresh_free_models(retry_blocked=True)
    assert oc.blocked_ids() == set()
    assert "vendor/free-big:free" in {m.id for m in oc.usable_free_models()}


def test_plain_refresh_keeps_refusals(tmp_path, monkeypatch):
    """A background refresh must not re-offer a model we know is refused."""
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path)
    oc.mark_unavailable("vendor/free-big:free")
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD):
        oc.refresh_free_models()
    assert "vendor/free-big:free" in oc.blocked_ids()


def test_refused_default_is_never_handed_back_as_the_fallback(tmp_path, monkeypatch):
    """The 403 handler asks for a new default — it must not get the dead one."""
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path)
    with mock.patch.object(oc, "_get_json", return_value=PAYLOAD):
        oc.refresh_free_models()
    assert providers.openrouter_default_model() == "vendor/free-big:free"
    oc.mark_unavailable("vendor/free-big:free")
    # Next by the same ranking: usable, then widest context.
    assert providers.openrouter_default_model() == "vendor/free-vision:free"
