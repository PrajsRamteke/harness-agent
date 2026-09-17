"""Model picker always exposes Harness Agent rows."""
from jarvis.constants.providers import PROVIDER_HARNESS_AGENT
from jarvis.tui.model_modal import model_picker_rows, _BUILTIN_HARNESS_ROWS

_OFFLINE = "jarvis.auth.zen_catalog.fetch_free_models"


def test_model_picker_rows_always_includes_harness_agent(monkeypatch):
    monkeypatch.setattr(_OFFLINE, lambda *a, **k: None)
    rows = model_picker_rows()
    harness = [(src, mid) for src, mid, _ in rows if src == PROVIDER_HARNESS_AGENT]
    assert len(harness) >= len(_BUILTIN_HARNESS_ROWS)
    assert rows[0][0] == PROVIDER_HARNESS_AGENT
    assert rows[0][1] == "nemotron-3-ultra-free"


def test_model_picker_rows_surfaces_live_free_models(monkeypatch):
    """A brand-new free model from the catalog appears without a code change."""
    monkeypatch.setattr(
        _OFFLINE,
        lambda *a, **k: [("brand-new-free", "Brand New Free")],
    )
    rows = model_picker_rows()
    assert rows[0] == (PROVIDER_HARNESS_AGENT, "brand-new-free", "Brand New Free")


def test_model_picker_rows_falls_back_offline(monkeypatch):
    monkeypatch.setattr(_OFFLINE, lambda *a, **k: None)
    rows = model_picker_rows()
    assert rows, "picker must never be empty"
    assert rows[0][1] == "nemotron-3-ultra-free"
