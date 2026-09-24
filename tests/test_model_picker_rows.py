"""Model picker always exposes Harness Agent rows — and never blocks on I/O."""
import pytest

from jarvis.constants.providers import PROVIDER_HARNESS_AGENT
from jarvis.tui.model_modal import model_picker_rows, _BUILTIN_HARNESS_ROWS

_FETCH = "jarvis.auth.zen_catalog.fetch_free_models"
_CACHED = "jarvis.auth.zen_catalog.cached_free_models"


@pytest.fixture(autouse=True)
def _cold_cache(monkeypatch):
    """Default to an empty catalog cache so these assertions don't depend on
    whatever the developer's machine happens to have fetched."""
    monkeypatch.setattr(_CACHED, lambda *a, **k: [])
    monkeypatch.setattr(
        "jarvis.auth.openrouter_catalog.cached_free_models", lambda *a, **k: []
    )


def test_model_picker_rows_always_includes_harness_agent(monkeypatch):
    monkeypatch.setattr(_FETCH, lambda *a, **k: None)
    rows = model_picker_rows()
    harness = [(src, mid) for src, mid, _ in rows if src == PROVIDER_HARNESS_AGENT]
    assert len(harness) >= len(_BUILTIN_HARNESS_ROWS)
    assert rows[0][0] == PROVIDER_HARNESS_AGENT
    assert rows[0][1] == "mimo-v2.5-free"


def test_model_picker_rows_surfaces_live_free_models(monkeypatch):
    """A brand-new free model from the catalog appears without a code change."""
    monkeypatch.setattr(_FETCH, lambda *a, **k: [("brand-new-free", "Brand New Free")])
    monkeypatch.setattr("jarvis.auth.catalog_cache.write", lambda *a, **k: None)
    rows = model_picker_rows(live=True)
    assert rows[0] == (PROVIDER_HARNESS_AGENT, "brand-new-free", "Brand New Free")


def test_model_picker_rows_reads_cache_without_network(monkeypatch):
    """The default (what the picker uses on open) must never hit the network."""
    monkeypatch.setattr(_FETCH, lambda *a, **k: pytest.fail("network used on open"))
    monkeypatch.setattr(_CACHED, lambda *a, **k: [("cached-free", "Cached Free")])
    rows = model_picker_rows()
    ids = [mid for src, mid, _ in rows if src == PROVIDER_HARNESS_AGENT]
    assert "cached-free" in ids
    # The built-in set is still there — a thin cache never shrinks the picker.
    assert set(mid for mid, _ in _BUILTIN_HARNESS_ROWS) <= set(ids)
    assert rows[0][1] == "mimo-v2.5-free"


def test_model_picker_rows_falls_back_offline(monkeypatch):
    monkeypatch.setattr(_FETCH, lambda *a, **k: None)
    rows = model_picker_rows()
    assert rows, "picker must never be empty"
    assert rows[0][1] == "mimo-v2.5-free"
