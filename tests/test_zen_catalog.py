"""Live free-model discovery from the public OpenCode catalog."""
from unittest import mock

from jarvis.auth import zen_catalog
from jarvis.constants.providers import (
    HARNESS_AGENT_DEFAULT_MODEL,
    harness_agent_models_for_picker,
)

_FREE = {"input": 0, "output": 0}
_PAID = {"input": 1, "output": 2}

CATALOG = {
    "opencode": {
        "models": {
            "nemotron-3-ultra-free": {"name": "Nemotron 3 Ultra Free", "cost": _FREE},
            "zeta-new-free": {"name": "Zeta New Free", "cost": _FREE},
            "retired-free": {"name": "Retired Free", "cost": _FREE},
            "expensive": {"name": "Expensive", "cost": _PAID},
        }
    }
}
# "retired-free" is in the catalog but not served -> must be excluded.
SERVED = {
    "data": [
        {"id": "expensive"},
        {"id": "zeta-new-free"},
        {"id": "nemotron-3-ultra-free"},
    ]
}


def test_fetch_intersects_catalog_and_served():
    with mock.patch.object(zen_catalog, "_get_json", side_effect=[CATALOG, SERVED]):
        assert zen_catalog.fetch_free_models() == [
            ("zeta-new-free", "Zeta New Free"),
            ("nemotron-3-ultra-free", "Nemotron 3 Ultra Free"),
        ]


def test_fetch_returns_none_when_offline():
    with mock.patch.object(zen_catalog, "_get_json", side_effect=OSError("offline")):
        assert zen_catalog.fetch_free_models() is None


def test_request_headers_avoid_blocked_urllib_ua():
    """The gateway 403s urllib's default UA; we must send a real one."""
    assert "Python-urllib" not in zen_catalog.REQUEST_HEADERS["User-Agent"]
    assert zen_catalog.REQUEST_HEADERS["User-Agent"].startswith("opencode/")


def test_fetch_returns_none_when_nothing_is_free():
    with mock.patch.object(
        zen_catalog, "_get_json", side_effect=[{"opencode": {"models": {}}}, {"data": []}]
    ):
        assert zen_catalog.fetch_free_models() is None


def test_picker_live_keeps_default_first():
    with mock.patch(
        "jarvis.auth.zen_catalog.fetch_free_models",
        return_value=[
            ("zeta-new-free", "Zeta New Free"),
            (HARNESS_AGENT_DEFAULT_MODEL, "Nemotron 3 Ultra Free"),
        ],
    ):
        models = harness_agent_models_for_picker(live=True)
    assert models[0][0] == HARNESS_AGENT_DEFAULT_MODEL
    assert ("zeta-new-free", "Zeta New Free") in models
    assert len(models) == 2


def test_picker_live_falls_back_offline():
    with mock.patch("jarvis.auth.zen_catalog.fetch_free_models", return_value=None):
        models = harness_agent_models_for_picker(live=True)
    assert len(models) >= 3
    assert models[0][0] == HARNESS_AGENT_DEFAULT_MODEL


def test_picker_static_never_touches_network():
    with mock.patch(
        "jarvis.auth.zen_catalog.fetch_free_models",
        side_effect=AssertionError("network used"),
    ):
        models = harness_agent_models_for_picker()
    assert models[0][0] == HARNESS_AGENT_DEFAULT_MODEL
