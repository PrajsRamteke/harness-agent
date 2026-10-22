"""The free tier's stand-in list (OpenCode's own catalog) while models.dev is off."""
from unittest import mock

import pytest

from jarvis.auth import catalog_cache, zen_catalog
from jarvis.constants import providers as pv
from jarvis.constants.providers import (
    HARNESS_AGENT_FALLBACK_MODEL,
    harness_agent_models_for_picker,
)


@pytest.fixture(autouse=True)
def _isolated_catalog_cache(tmp_path, monkeypatch):
    """Tests must never write into the user's real ~/.config catalog cache.

    ``harness_agent_models_for_picker(live=True)`` persists through
    ``catalog_cache.write`` — without this, fixture models like
    ``zeta-new-free`` leak into the live /model picker.
    """
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path)
    return tmp_path

_FREE = {"input": 0, "output": 0}
_PAID = {"input": 1, "output": 2}

CATALOG = {
    "opencode": {
        "models": {
            "nemotron-3-ultra-free": {"name": "Nemotron 3 Ultra Free", "cost": _FREE,
                                       "release_date": "2026-06-04"},
            "zeta-new-free": {"name": "Zeta New Free", "cost": _FREE,
                               "release_date": "2026-09-01"},
            "retired-free": {"name": "Retired Free", "cost": _FREE,
                              "release_date": "2026-05-01"},
            "expensive": {"name": "Expensive", "cost": _PAID},
        }
    }
}


def test_fetch_lists_the_catalogs_free_models():
    """The free set comes from OpenCode's own catalog — priced $0, not
    deprecated — exactly like the OpenCode CLI shows it."""
    with mock.patch.object(zen_catalog, "_get_json", return_value=CATALOG):
        assert zen_catalog.fetch_free_models() == [
            ("zeta-new-free", "Zeta New Free"),
            ("nemotron-3-ultra-free", "Nemotron 3 Ultra Free"),
            ("retired-free", "Retired Free"),
        ]


def test_free_model_missing_from_the_served_list_is_still_offered():
    """The gateway's served list lags behind for free models: it left out
    nemotron-3-ultra-free and longcat-2.5-preview-free while Zen served both.
    Intersecting with it hid working models, so it isn't used as a gate."""
    catalog = {
        "opencode": {
            "models": {
                "nemotron-3-ultra-free": {"name": "Nemotron 3 Ultra Free", "cost": _FREE},
                "longcat-2.5-preview-free": {"name": "LongCat 2.5 Preview Free", "cost": _FREE},
            }
        }
    }
    with mock.patch.object(zen_catalog, "_get_json", return_value=catalog):
        ids = [mid for mid, _ in (zen_catalog.fetch_free_models() or [])]
    assert sorted(ids) == ["longcat-2.5-preview-free", "nemotron-3-ultra-free"]


def test_deprecated_and_broken_ids_are_dropped():
    """Deprecated models are dropped, as OpenCode does (and the models.dev path);
    ids verified broken on use (deepseek-v4-flash-free -> 400) are deny-listed."""
    catalog = {
        "opencode": {
            "models": {
                "mimo-v2.5-free": {"name": "MiMo V2.5 Free", "cost": _FREE, "status": "deprecated"},
                "deepseek-v4-flash-free": {"name": "DeepSeek Free", "cost": _FREE},
                "big-pickle": {"name": "Big Pickle", "cost": _FREE},
            }
        }
    }
    with mock.patch.object(zen_catalog, "_get_json", return_value=catalog):
        ids = [mid for mid, _ in (zen_catalog.fetch_free_models() or [])]
    assert ids == ["big-pickle"]


def test_fetch_returns_none_when_offline():
    with mock.patch.object(zen_catalog, "_get_json", side_effect=OSError("offline")):
        assert zen_catalog.fetch_free_models() is None


def test_request_headers_avoid_blocked_urllib_ua():
    """The gateway 403s urllib's default UA; we must send a real one."""
    assert "Python-urllib" not in zen_catalog.REQUEST_HEADERS["User-Agent"]
    assert zen_catalog.REQUEST_HEADERS["User-Agent"].startswith("opencode/")


def test_fetch_returns_none_when_nothing_is_free():
    with mock.patch.object(
        zen_catalog, "_get_json", return_value={"opencode": {"models": {}}}
    ):
        assert zen_catalog.fetch_free_models() is None


@pytest.fixture
def models_dev_off(monkeypatch):
    monkeypatch.setenv("HARNESS_MODELS_DEV", "0")


def test_picker_live_uses_opencodes_catalog_while_models_dev_is_off(models_dev_off):
    with mock.patch(
        "jarvis.auth.zen_catalog.fetch_free_models",
        return_value=[
            ("zeta-new-free", "Zeta New Free"),
            ("nemotron-3-ultra-free", "Nemotron 3 Ultra Free"),
        ],
    ):
        models = harness_agent_models_for_picker(live=True)
    assert models == [
        ("zeta-new-free", "Zeta New Free"),
        ("nemotron-3-ultra-free", "Nemotron 3 Ultra Free"),
    ]
    assert pv.harness_agent_default_model() == "zeta-new-free"
    assert pv.is_harness_agent_model("nemotron-3-ultra-free")


def test_picker_live_falls_back_offline(models_dev_off):
    """Nothing fetched, nothing cached: the fallback model alone, never empty."""
    with mock.patch("jarvis.auth.zen_catalog.fetch_free_models", return_value=None):
        models = harness_agent_models_for_picker(live=True)
    assert models == [(HARNESS_AGENT_FALLBACK_MODEL, "Big Pickle")]
    assert pv.harness_agent_default_model() == HARNESS_AGENT_FALLBACK_MODEL


def test_picker_static_never_touches_network():
    with mock.patch(
        "jarvis.auth.zen_catalog.fetch_free_models",
        side_effect=AssertionError("network used"),
    ):
        models = harness_agent_models_for_picker()
    assert models[0][0] == HARNESS_AGENT_FALLBACK_MODEL


def test_opencodes_catalog_is_not_fetched_while_models_dev_is_on():
    """The free list comes from Zen's (models.dev); OpenCode's own 5 MB copy of
    the catalog is only downloaded as the stand-in."""
    with mock.patch(
        "jarvis.auth.zen_catalog.fetch_free_models",
        side_effect=AssertionError("duplicate catalog downloaded"),
    ):
        harness_agent_models_for_picker(live=True)
