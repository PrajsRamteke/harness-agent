"""Live discovery of the Codex (ChatGPT OAuth) model line-up."""
from unittest import mock

import pytest

from jarvis.auth import catalog_cache, codex_catalog as cc
from jarvis.constants import providers
from jarvis.constants.providers import PROVIDER_OPENAI_CODEX


def _entry(slug, *, name=None, desc="", priority=5, visibility="list",
           modalities=("text", "image")):
    return {
        "slug": slug,
        "display_name": name or slug,
        "description": desc,
        "priority": priority,
        "visibility": visibility,
        "input_modalities": list(modalities),
    }


# Shaped like the real /backend-api/codex/models response.
PAYLOAD = {"models": [
    _entry("gpt-5.5", name="GPT-5.5", desc="Legacy coding model.", priority=12),
    _entry("gpt-6-luna", name="GPT-6-Luna", desc="Fast and affordable model.", priority=3),
    _entry("gpt-5.6-terra", name="GPT-5.6-Terra", desc="Balanced.", priority=7,
           modalities=("text",)),
    _entry("codex-auto-review", priority=43, visibility="hide"),
]}


@pytest.fixture(autouse=True)
def _restore_model_registry(monkeypatch):
    """codex_models_for_picker registers discovered ids globally."""
    monkeypatch.setattr(providers, "MODEL_INFO", dict(providers.MODEL_INFO))
    monkeypatch.setattr(providers, "PRICING", dict(providers.PRICING))
    monkeypatch.setattr(
        providers, "IMAGE_SUPPORTING_MODELS", set(providers.IMAGE_SUPPORTING_MODELS)
    )


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_cache, "CACHE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setattr(
        "jarvis.auth.codex_oauth_tokens.get_fresh_codex_oauth_token",
        lambda: {"access_token": "tok", "refresh_token": "r"},
    )
    monkeypatch.setattr(
        "jarvis.auth.codex_oauth_tokens.load_codex_oauth_tokens",
        lambda: {"access_token": "tok", "refresh_token": "r"},
    )


def test_fetch_orders_by_backend_priority_and_skips_hidden_models():
    with mock.patch.object(cc, "_get_json", return_value=PAYLOAD):
        models = cc.fetch_models("tok")
    assert [m.id for m in models] == ["gpt-6-luna", "gpt-5.6-terra", "gpt-5.5"]


def test_fetch_asks_for_a_recent_client_version_and_sends_the_token():
    """Too old a client_version returns only legacy models (which 404)."""
    with mock.patch.object(cc, "_get_json", return_value=PAYLOAD) as get:
        cc.fetch_models("tok")
    url, token, _timeout = get.call_args.args
    assert f"client_version={cc.CLIENT_VERSION}" in url
    assert token == "tok"


def test_label_and_vision_come_from_the_catalog():
    with mock.patch.object(cc, "_get_json", return_value=PAYLOAD):
        by_id = {m.id: m for m in cc.fetch_models("tok")}
    assert by_id["gpt-6-luna"].label == "GPT-6-Luna — Fast and affordable model"
    assert by_id["gpt-6-luna"].supports_images
    assert not by_id["gpt-5.6-terra"].supports_images


def test_fetch_is_none_without_token_or_network():
    assert cc.fetch_models("") is None
    with mock.patch.object(cc, "_get_json", side_effect=OSError("offline")):
        assert cc.fetch_models("tok") is None


def test_refresh_needs_a_sign_in(cache_dir, monkeypatch):
    monkeypatch.setattr(
        "jarvis.auth.codex_oauth_tokens.get_fresh_codex_oauth_token", lambda: None
    )
    with mock.patch.object(cc, "_get_json") as get:
        assert cc.refresh_models() is None
    get.assert_not_called()


def test_refresh_round_trips_through_the_cache(cache_dir, signed_in):
    with mock.patch.object(cc, "_get_json", return_value=PAYLOAD):
        cc.refresh_models()
    with mock.patch.object(cc, "_get_json", side_effect=AssertionError("network")):
        assert [m.id for m in cc.cached_models()] == [
            "gpt-6-luna", "gpt-5.6-terra", "gpt-5.5",
        ]


def test_cache_is_fresh_when_nobody_is_signed_in(cache_dir, monkeypatch):
    """Signed out, there's nothing to refresh — don't make the picker try."""
    monkeypatch.setattr(
        "jarvis.auth.codex_oauth_tokens.load_codex_oauth_tokens", lambda: None
    )
    assert cc.cache_is_fresh()


def test_refused_model_is_labelled_and_sorted_last(cache_dir, signed_in):
    with mock.patch.object(cc, "_get_json", return_value=PAYLOAD):
        cc.refresh_models()
    cc.mark_unavailable("gpt-6-luna")
    models = cc.models_for_display()
    assert models[-1].id == "gpt-6-luna"
    assert models[-1].label.endswith(", unavailable")
    assert not models[-1].usable


def test_explicit_refresh_retries_refused_models(cache_dir, signed_in):
    cc.mark_unavailable("gpt-6-luna")
    with mock.patch.object(cc, "_get_json", return_value=PAYLOAD):
        cc.refresh_models(retry_refused=True)
    assert cc.refused_ids() == set()


def test_refusals_age_out(cache_dir):
    cc.mark_unavailable("gpt-5.5")
    with mock.patch.object(cc.time, "time", return_value=10**12):
        assert cc.refused_ids() == set()


# ── provider wiring ───────────────────────────────────────────────────────────

def test_picker_uses_the_live_line_up_without_dead_seeds(cache_dir, signed_in):
    with mock.patch.object(cc, "_get_json", return_value=PAYLOAD):
        cc.refresh_models()
    ids = [mid for mid, _ in providers.codex_models_for_picker()]
    assert ids == ["gpt-6-luna", "gpt-5.6-terra", "gpt-5.5"]
    assert "gpt-5.6-luna" not in ids, "seeds must not pad a live list"


def test_picker_falls_back_to_seeds_on_a_cold_cache(cache_dir):
    assert providers.codex_models_for_picker() == list(providers.CODEX_MODELS)


def test_default_skips_refused_models(cache_dir, signed_in):
    with mock.patch.object(cc, "_get_json", return_value=PAYLOAD):
        cc.refresh_models()
    assert providers.codex_default_model() == "gpt-6-luna"
    cc.mark_unavailable("gpt-6-luna")
    assert providers.codex_default_model() == "gpt-5.6-terra"


def test_saved_but_refused_model_is_replaced_on_normalize(cache_dir, signed_in):
    """The bug: settings.json kept gpt-5.5 and every turn 404'd."""
    with mock.patch.object(cc, "_get_json", return_value=PAYLOAD):
        cc.refresh_models()
    assert providers.normalize_model_for_provider(
        "gpt-5.5", PROVIDER_OPENAI_CODEX) == "gpt-5.5"
    cc.mark_unavailable("gpt-5.5")
    assert providers.normalize_model_for_provider(
        "gpt-5.5", PROVIDER_OPENAI_CODEX) == "gpt-6-luna"


def test_discovered_model_survives_a_restart(cache_dir, signed_in):
    """A live-only id picked last session still belongs to Codex on boot,
    before the picker has registered it."""
    payload = {"models": [_entry("gpt-7-new", priority=1)]}
    with mock.patch.object(cc, "_get_json", return_value=payload):
        cc.refresh_models()
    assert "gpt-7-new" not in providers.MODEL_INFO
    assert providers.model_belongs_to_provider("gpt-7-new", PROVIDER_OPENAI_CODEX)


# ── stream fallback ───────────────────────────────────────────────────────────

class _Err(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status_code = status


@pytest.mark.parametrize("status, msg, refused", [
    (404, "Error code: 404 - {'error': {'message': 'The model `gpt-5.5` does not "
          "exist or you do not have access to it.', 'code': 'model_not_found'}}", True),
    (400, "Error code: 400 - {'detail': \"The 'gpt-5.4' model is not supported "
          "when using Codex with a ChatGPT account.\"}", True),
    (400, "Error code: 400 - {'detail': 'Invalid input'}", False),
    (401, "unauthorized", False),
])
def test_only_model_refusals_trigger_the_codex_fallback(status, msg, refused):
    from jarvis.repl.stream import _codex_refused_model

    assert _codex_refused_model(_Err(status, msg)) is refused
