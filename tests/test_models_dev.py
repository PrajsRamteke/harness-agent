"""models.dev catalog: trimming, fetching, keys, and how it plugs into providers."""
from __future__ import annotations

import gzip
import json
import os
import stat
from unittest.mock import MagicMock

import pytest

from jarvis import state
from jarvis.auth import catalog_keys, models_dev
from jarvis.constants import providers as pv


def _m(mid, *, name=None, tools=True, images=False, reasoning=False, ctx=128000, out=8192,
       cost=(1.0, 2.0), release="2026-01-01", status=None, output=("text",), provider=None,
       interleaved=None):
    row = {
        "id": mid, "name": name or mid, "tool_call": tools, "reasoning": reasoning,
        "attachment": images,
        "modalities": {"input": ["text", "image"] if images else ["text"], "output": list(output)},
        "limit": {"context": ctx, "output": out},
        "release_date": release,
    }
    if cost is not None:
        row["cost"] = {"input": cost[0], "output": cost[1]}
    if status:
        row["status"] = status
    if provider:
        row["provider"] = provider
    if interleaved is not None:
        row["interleaved"] = interleaved
    return row


def _prov(name, models, *, npm="@ai-sdk/openai-compatible", api="https://api.example.com/v1", env=("X_API_KEY",), doc=""):
    p = {"id": name.lower(), "name": name, "npm": npm, "env": list(env), "doc": doc,
         "models": {m["id"]: m for m in models}}
    if api is not None:
        p["api"] = api
    return p


RAW = {
    "deepseek": _prov("DeepSeek", [
        _m("deepseek-chat", release="2026-03-01", interleaved={"field": "reasoning_content"}, reasoning=True),
        _m("deepseek-old", release="2025-01-01"),
        _m("deepseek-gone", status="deprecated"),
        _m("deepseek-image-gen", output=("image",)),
        _m("deepseek-no-tools", tools=False, release="2026-09-01"),
    ], api="https://api.deepseek.com", env=("DEEPSEEK_API_KEY",), doc="https://platform.deepseek.com"),
    "groq": _prov("Groq", [_m("llama-x", images=True)], npm="@ai-sdk/groq", api=None, env=("GROQ_API_KEY",)),
    "openai": _prov("OpenAI", [
        _m("gpt-9", reasoning=True, out=128000), _m("gpt-9-pro"), _m("gpt-9-codex"),
    ], npm="@ai-sdk/openai", api=None, env=("OPENAI_API_KEY",)),
    "mystery-sdk": _prov("Mystery", [_m("m1")], npm="@mystery/sdk", api=None),
    "minimax": _prov("MiniMax", [_m("MiniMax-M9")], npm="@ai-sdk/anthropic",
                     api="https://api.minimax.io/anthropic/v1", env=("MINIMAX_API_KEY",)),
    "minimax-cn": _prov("MiniMax (China)", [_m("MiniMax-M9")], npm="@ai-sdk/anthropic",
                        api="https://api.minimax.cn/anthropic/v1", env=("MINIMAX_API_KEY",)),
    "gateway": _prov("Gateway", [
        _m("chat-model"),
        _m("resp-model", provider={"npm": "@ai-sdk/openai"}),
        _m("claude-ish", provider={"npm": "@ai-sdk/anthropic"}),
        _m("elsewhere", provider={"npm": "@ai-sdk/openai-compatible", "api": "https://other.example/v1"}),
    ]),
    "cf": _prov("Cloudflare Workers AI", [_m("cf-model")],
                api="https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT}/ai/v1",
                env=("CF_ACCOUNT", "CF_API_KEY")),
    "lmstudio": _prov("LMStudio", [_m("local-model", cost=(0, 0))], api="http://127.0.0.1:1234/v1",
                      env=("LMSTUDIO_API_KEY",)),
    "github-copilot": _prov("GitHub Copilot", [_m("gpt-x")], env=("GITHUB_TOKEN",)),
    "kimchi": _prov("Kimchi", [_m("k")]),  # removed from Jarvis; never offered
    # Built-ins: enrichment only.
    "openrouter": _prov("OpenRouter", [
        _m("vendor/paid-model", cost=(3.0, 15.0), images=True, release="2026-08-01"),
        _m("vendor/free-model:free", cost=(0, 0)),
    ], npm="@openrouter/ai-sdk-provider", api="https://openrouter.ai/api/v1", env=("OPENROUTER_API_KEY",)),
    "opencode-go": _prov("OpenCode Go", [
        _m("kimi-k2.6"), _m("brand-new-go"), _m("gpt-go", provider={"npm": "@ai-sdk/openai"}),
        _m("minimax-anthropic", provider={"npm": "@ai-sdk/anthropic"}),
        _m("old-but-served", status="deprecated", cost=(0.95, 4.0)),
    ], api="https://opencode.ai/zen/go/v1", env=("OPENCODE_API_KEY",)),
    "anthropic": _prov("Anthropic", [_m("claude-new-1", images=True)], npm="@ai-sdk/anthropic", api=None,
                       env=("ANTHROPIC_API_KEY",)),
    # models.dev's "opencode" is OpenCode Zen.
    "opencode": _prov("OpenCode Zen", [
        _m("zen-frontier", cost=(5.0, 25.0), images=True, release="2026-09-01"),
        _m("zen-free", cost=(0, 0), images=True, release="2026-08-01"),
        _m("zen-retired", cost=(1.0, 1.0), release="2026-07-01"),
        _m("claude-routed", cost=(3.0, 15.0)),
        # Deprecated: the newest free model, so it would be the default if kept.
        _m("zen-dead-free", cost=(0, 0), release="2026-09-15", status="deprecated"),
        _m("mimo-v2.5-free", cost=(0, 0), status="deprecated"),  # the free tier's default
    ], api="https://opencode.ai/zen/v1", env=("OPENCODE_ZEN_API_KEY",)),
}


@pytest.fixture
def catalog(monkeypatch):
    """Seed the (per-test, isolated) cache with RAW, and clear provider envs."""
    for var in ("DEEPSEEK_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "MINIMAX_API_KEY",
                "CF_ACCOUNT", "CF_API_KEY", "LMSTUDIO_API_KEY", "GITHUB_TOKEN", "X_API_KEY",
                "HARNESS_MODELS_DEV"):
        monkeypatch.delenv(var, raising=False)
    models_dev.store(models_dev.trim(RAW), etag='"v1"')
    return models_dev


# ── trimming ─────────────────────────────────────────────────────────────────


def test_trim_keeps_usable_providers_and_drops_the_rest(catalog):
    provs = models_dev.providers()
    assert "md:deepseek" in provs and "md:groq" in provs and "md:minimax" in provs
    # No base URL and no known one / sign-in flows / built-ins under another name.
    for gone in ("md:mystery-sdk", "md:github-copilot", "md:kimchi"):
        assert gone not in provs
    # Built-ins are never offered twice.
    for native in ("md:openrouter", "md:opencode-go", "md:anthropic"):
        assert native not in provs


def test_base_urls_come_from_api_or_the_known_table(catalog):
    assert models_dev.get_provider("md:deepseek").api == "https://api.deepseek.com"
    assert models_dev.get_provider("md:groq").api == "https://api.groq.com/openai/v1"
    mm = models_dev.get_provider("md:minimax")
    assert mm.wire == models_dev.WIRE_ANTHROPIC
    assert mm.api == "https://api.minimax.io/anthropic"  # the SDK appends /v1/messages


def test_models_filter_deprecated_and_non_text_and_sort_newest_usable_first(catalog):
    ids = [m.id for m in models_dev.models("md:deepseek")]
    assert "deepseek-gone" not in ids and "deepseek-image-gen" not in ids
    assert ids == ["deepseek-chat", "deepseek-old", "deepseek-no-tools"]
    no_tools = models_dev.get_model("md:deepseek", "deepseek-no-tools")
    assert not no_tools.usable and "no tool use" in no_tools.label


def test_per_model_wire_overrides(catalog):
    ids = {m.id: m for m in models_dev.models("md:gateway")}
    assert ids["chat-model"].wire == "chat"
    assert ids["resp-model"].wire == "responses"
    assert "claude-ish" not in ids, "an Anthropic-only model can't ride the OpenAI client"
    assert "elsewhere" not in ids, "a model on another base URL can't share the client"


def test_openai_itself_uses_chat_except_responses_only_models(catalog):
    by_id = {m.id: m for m in models_dev.models("md:openai")}
    assert by_id["gpt-9"].wire == "chat"
    assert by_id["gpt-9-pro"].wire == "responses"
    assert by_id["gpt-9-codex"].wire == "responses"


def test_labels_carry_context_price_and_caveats(catalog):
    m = models_dev.get_model("md:deepseek", "deepseek-chat")
    assert m.label == "deepseek-chat — 128K ctx, $1/$2"
    assert m.reasoning_field == "reasoning_content"
    free = models_dev.get_model("md:lmstudio", "local-model")
    assert "free" in free.label


def test_default_model_is_the_newest_usable_plain_model(catalog):
    assert models_dev.default_model("md:deepseek") == "deepseek-chat"
    assert models_dev.default_model("md:openai") == "gpt-9"


def test_template_urls_need_their_env_var(catalog, monkeypatch):
    cf = models_dev.get_provider("md:cf")
    assert cf.template_vars == ("CF_ACCOUNT",)
    assert cf.key_vars == ("CF_API_KEY",)
    assert models_dev.base_url(cf) is None
    assert models_dev.missing_vars(cf) == ["CF_ACCOUNT"]
    monkeypatch.setenv("CF_ACCOUNT", "abc123")
    assert models_dev.base_url(cf) == "https://api.cloudflare.com/client/v4/accounts/abc123/ai/v1"


def test_local_providers_are_flagged(catalog):
    assert models_dev.get_provider("md:lmstudio").local
    assert not models_dev.get_provider("md:deepseek").local


def test_disabled_by_env(catalog, monkeypatch):
    monkeypatch.setenv("HARNESS_MODELS_DEV", "0")
    assert models_dev.providers() == {}
    assert models_dev.refresh() is False


def test_cache_from_an_older_schema_is_ignored(monkeypatch):
    models_dev.CACHE_FILE.write_text(json.dumps({"schema": 0, "providers": {"x": {}}}))
    assert models_dev.providers() == {}
    assert not models_dev.cache_is_fresh()


# ── network ──────────────────────────────────────────────────────────────────


def test_refresh_fetches_gzip_and_then_revalidates_with_etag(monkeypatch):
    calls = []

    def fake_get(url, headers, timeout):
        calls.append(dict(headers))
        if headers.get("If-None-Match") == '"etag-1"':
            return 304, b"", {}
        body = gzip.compress(json.dumps(RAW).encode())
        return 200, body, {"content-encoding": "gzip", "etag": '"etag-1"'}

    monkeypatch.setattr(models_dev, "_http_get", fake_get)
    assert models_dev.refresh() is True
    assert "md:deepseek" in models_dev.providers()
    assert "If-None-Match" not in calls[0]
    assert models_dev.cache_is_fresh()

    assert models_dev.refresh() is True  # unchanged → 304, cache kept
    assert calls[1]["If-None-Match"] == '"etag-1"'
    assert "md:deepseek" in models_dev.providers()

    assert models_dev.refresh(force=True) is True  # /model refresh skips the ETag
    assert "If-None-Match" not in calls[2]


def test_refresh_failure_keeps_the_old_cache(catalog, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(models_dev, "_http_get", boom)
    assert models_dev.refresh() is True  # still have a cache to serve
    assert "md:deepseek" in models_dev.providers()


def test_refresh_failure_without_cache_reports_false(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(models_dev, "_http_get", boom)
    assert models_dev.refresh() is False
    assert models_dev.providers() == {}


# ── keys ─────────────────────────────────────────────────────────────────────


def test_keys_file_is_private_and_round_trips(catalog):
    assert not catalog_keys.has_key("md:deepseek")
    catalog_keys.save("md:deepseek", "sk-deep-123456789")
    path = catalog_keys._file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert catalog_keys.key_source("md:deepseek") == ("file", "sk-deep-123456789")
    assert catalog_keys.connected() == ["md:deepseek"]
    assert catalog_keys.delete("md:deepseek")
    assert catalog_keys.connected() == []


def test_env_key_connects_a_provider(catalog, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-key-123")
    assert catalog_keys.key_source("md:deepseek") == ("env", "env-key-123")
    assert "md:deepseek" in catalog_keys.connected()


def test_shared_and_generic_env_vars_do_not_auto_connect(catalog, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "mm-key")  # read by minimax AND minimax-cn
    assert "MINIMAX_API_KEY" in models_dev.shared_env_vars()
    assert catalog_keys.key_source("md:minimax") == ("none", "")
    # …but the user can point one of them at the variable.
    catalog_keys.save("md:minimax", "$MINIMAX_API_KEY")
    assert catalog_keys.get_key("md:minimax") == "mm-key"
    assert catalog_keys.is_reference(catalog_keys.saved_value("md:minimax"))
    assert catalog_keys.connected() == ["md:minimax"]


def test_provider_with_missing_url_var_is_not_connected(catalog, monkeypatch):
    monkeypatch.setenv("CF_API_KEY", "cf-key")
    assert catalog_keys.has_key("md:cf")
    assert "md:cf" not in catalog_keys.connected()
    monkeypatch.setenv("CF_ACCOUNT", "acct")
    assert "md:cf" in catalog_keys.connected()


# ── providers.py integration ─────────────────────────────────────────────────


@pytest.fixture
def no_builtin_keys(tmp_path, monkeypatch):
    from jarvis.constants import paths

    for name in ("KEY_FILE", "OPENROUTER_KEY_FILE", "OPENCODE_KEY_FILE", "OPENCODE_ZEN_KEY_FILE"):
        monkeypatch.setattr(paths, name, tmp_path / name.lower())
    for var in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("jarvis.auth.oauth_tokens.load_oauth_tokens", lambda: None)
    monkeypatch.setattr("jarvis.auth.codex_oauth_tokens.load_codex_oauth_tokens", lambda: None)


def test_connected_catalog_provider_is_a_model_source(catalog, no_builtin_keys):
    assert pv.connected_model_sources() == [pv.PROVIDER_HARNESS_AGENT]
    catalog_keys.save("md:deepseek", "k" * 20)
    assert pv.connected_model_sources() == [pv.PROVIDER_HARNESS_AGENT, "md:deepseek"]
    rows = pv.models_for_source("md:deepseek")
    assert rows[0][0] == "deepseek-chat"
    assert pv.provider_label("md:deepseek") == "DeepSeek"
    assert "md:deepseek" in pv.connected_providers()


def test_catalog_models_never_enter_the_id_keyed_tables(catalog, no_builtin_keys):
    catalog_keys.save("md:deepseek", "k" * 20)
    pv.models_for_source("md:deepseek")
    assert "deepseek-chat" not in pv.MODEL_INFO
    assert "deepseek-chat" not in pv.PRICING


def test_model_belongs_is_strict_for_catalog_providers(catalog):
    assert pv.model_belongs_to_provider("deepseek-chat", "md:deepseek")
    assert not pv.model_belongs_to_provider("claude-opus-5-5", "md:deepseek")
    assert pv.normalize_model_for_provider("claude-opus-5-5", "md:deepseek") == "deepseek-chat"


def test_pricing_and_vision_are_per_provider(catalog, monkeypatch):
    # Same id on two providers must not borrow each other's facts.
    assert pv.model_pricing("deepseek-chat", "md:deepseek") == (1.0, 2.0)
    assert pv.model_pricing("unknown", "md:deepseek") is None
    assert pv.model_supports_images("llama-x", "md:groq")
    assert not pv.model_supports_images("deepseek-chat", "md:deepseek")
    monkeypatch.setattr(state, "provider", "md:groq")
    assert pv.model_supports_images("llama-x")


def test_openrouter_lists_paid_models_from_models_dev(catalog, monkeypatch):
    monkeypatch.setattr("jarvis.auth.openrouter_catalog.free_models", lambda live=False: [])
    monkeypatch.setattr("jarvis.auth.openrouter_catalog.usable_free_models", lambda: [])
    monkeypatch.setattr("jarvis.auth.openrouter_catalog.served_ids", lambda: set())
    ids = [m for m, _ in pv.openrouter_models_for_picker()]
    assert "vendor/paid-model" in ids
    assert pv.PRICING["vendor/paid-model"] == (3.0, 15.0)
    assert pv.model_supports_images("vendor/paid-model", pv.PROVIDER_OPENROUTER)
    # OpenRouter's own list never fetched: models.dev's prices decide "free".
    assert pv.model_is_free("vendor/free-model:free", pv.PROVIDER_OPENROUTER, {})
    assert not pv.model_is_free("vendor/paid-model", pv.PROVIDER_OPENROUTER, {})
    assert pv.openrouter_default_model() == "vendor/free-model:free"  # free + tools, from models.dev


def test_openrouter_itself_overrules_models_dev(catalog, monkeypatch):
    """models.dev lags: a ':free' model OpenRouter retired must not be listed (or
    tagged free), and only OpenRouter's own $0 list is the free tier."""
    from jarvis.auth import openrouter_catalog as oc

    on_openrouter = [oc.FreeModel("vendor/live:free", "Live — free")]
    monkeypatch.setattr(oc, "free_models", lambda live=False: list(on_openrouter))
    monkeypatch.setattr(oc, "served_ids", lambda: {"vendor/live:free", "vendor/paid-model"})
    ids = [m for m, _ in pv.openrouter_models_for_picker()]
    assert ids == ["vendor/live:free", "vendor/paid-model"]  # vendor/free-model:free is gone
    free_ids = {pv.PROVIDER_OPENROUTER: {"vendor/live:free"}}
    assert pv.model_is_free("vendor/live:free", pv.PROVIDER_OPENROUTER, free_ids)
    assert not pv.model_is_free("vendor/free-model:free", pv.PROVIDER_OPENROUTER, free_ids)
    assert not pv.model_is_free("deepseek/deepseek-v4-flash-0731:free", pv.PROVIDER_OPENROUTER, free_ids)


def test_opencode_go_comes_from_models_dev_only(catalog):
    """No hard-coded Go models: the list, prices and vision are models.dev's."""
    assert [m.id for m in pv.MODELS if m.provider == pv.PROVIDER_OPENCODE] == []
    ids = [m for m, _ in pv.opencode_go_models_for_picker()]
    assert ids == ["brand-new-go", "gpt-go", "kimi-k2.6"]  # usable, newest, then by name
    assert "minimax-anthropic" not in ids
    assert pv.native_responses_models(pv.PROVIDER_OPENCODE) == {"gpt-go"}
    assert pv.model_pricing("kimi-k2.6", pv.PROVIDER_OPENCODE) == (1.0, 2.0)
    # Listed by models.dev for Go → belongs.
    assert pv.model_belongs_to_provider("brand-new-go", pv.PROVIDER_OPENCODE)
    assert pv.model_belongs_to_provider("kimi-k2.6", pv.PROVIDER_OPENCODE)
    assert pv.opencode_go_default_model() == "brand-new-go"


def test_opencode_gateways_drop_what_they_no_longer_serve(catalog, monkeypatch):
    from jarvis.auth import opencode_catalog

    served = {pv.PROVIDER_OPENCODE: {"kimi-k2.6", "gpt-go"},
              pv.PROVIDER_OPENCODE_ZEN: {"zen-frontier", "zen-free", "claude-routed"}}
    monkeypatch.setattr(opencode_catalog, "served_ids", lambda provider: served.get(provider, set()))
    assert [m for m, _ in pv.opencode_go_models_for_picker()] == ["gpt-go", "kimi-k2.6"]
    assert [m for m, _ in pv.opencode_zen_live_models_for_picker()] == ["zen-frontier", "zen-free", "claude-routed"]
    # A saved model the gateway retired is replaced at startup, a served one kept.
    assert pv.normalize_model_for_provider("brand-new-go", pv.PROVIDER_OPENCODE) == "gpt-go"
    assert pv.normalize_model_for_provider("kimi-k2.6", pv.PROVIDER_OPENCODE) == "kimi-k2.6"
    assert pv.normalize_model_for_provider("zen-retired", pv.PROVIDER_OPENCODE_ZEN) == "zen-free"


def test_opencode_zen_comes_from_models_dev_and_starts_free(catalog):
    ids = [m for m, _ in pv.opencode_zen_live_models_for_picker()]
    assert ids == ["zen-frontier", "zen-free", "zen-retired", "claude-routed"]
    # A key never starts on the paid frontier model by surprise.
    assert pv.opencode_zen_default_model() == "zen-free"
    assert pv.model_pricing("zen-frontier", pv.PROVIDER_OPENCODE_ZEN) == (5.0, 25.0)
    assert pv.model_supports_images("zen-frontier", pv.PROVIDER_OPENCODE_ZEN)
    assert pv.model_is_free("zen-free", pv.PROVIDER_OPENCODE_ZEN, {})


def test_opencode_zen_drops_deprecated_models_like_opencode(catalog, monkeypatch):
    """Zen kept serving deprecated deepseek-v4-flash-free, tagged free, while
    every request got 400 "Model is unavailable". OpenCode hides deprecated
    models; so does the keyed Zen list — but the free tier's own models keep
    working on Zen (they run on the free-tier client, not the key)."""
    from jarvis.auth import opencode_catalog

    served = {"zen-frontier", "zen-free", "zen-dead-free", "mimo-v2.5-free"}
    monkeypatch.setattr(opencode_catalog, "served_ids",
                        lambda provider: served if provider == pv.PROVIDER_OPENCODE_ZEN else set())
    ids = [m for m, _ in pv.opencode_zen_live_models_for_picker()]
    assert ids == ["zen-frontier", "zen-free"]
    assert not pv.model_belongs_to_provider("zen-dead-free", pv.PROVIDER_OPENCODE_ZEN)
    assert pv.normalize_model_for_provider("zen-dead-free", pv.PROVIDER_OPENCODE_ZEN) == "zen-free"
    assert pv.opencode_zen_default_model() == "zen-free"
    assert not pv.model_belongs_to_provider("mimo-v2.5-free", pv.PROVIDER_OPENCODE_ZEN)


def test_harness_agent_lists_zens_free_rows(catalog, monkeypatch):
    """The free tier has no list in code: it is Zen's rows tagged Free — served,
    priced $0 on models.dev, not deprecated — with the same image tags."""
    from jarvis.auth import opencode_catalog

    served = {"zen-frontier", "zen-free", "zen-dead-free", "mimo-v2.5-free", "unpriced-free"}
    monkeypatch.setattr(opencode_catalog, "served_ids",
                        lambda provider: served if provider == pv.PROVIDER_OPENCODE_ZEN else set())
    rows = pv.harness_agent_models_for_picker()
    zen_free = [m for m, _ in pv.opencode_zen_live_models_for_picker()
                if pv.model_is_free(m, pv.PROVIDER_OPENCODE_ZEN)]
    # Paid, deprecated and unpriced ("Not on models.dev yet") never show.
    assert [m for m, _ in rows] == zen_free == ["zen-free"]
    assert pv.harness_agent_default_model() == "zen-free"
    assert pv.model_sees_images("zen-free", pv.PROVIDER_HARNESS_AGENT)
    assert pv.model_pricing("zen-free", pv.PROVIDER_HARNESS_AGENT) == (0.0, 0.0)
    assert pv.is_harness_agent_model("zen-free")
    assert pv.model_belongs_to_provider("zen-free", pv.PROVIDER_HARNESS_AGENT)
    for gone in ("zen-dead-free", "mimo-v2.5-free", "zen-frontier", "unpriced-free"):
        assert not pv.is_harness_agent_model(gone)
    # A free-tier model saved before it was deprecated moves to the default.
    assert pv.normalize_model_for_provider("mimo-v2.5-free", pv.PROVIDER_OPENCODE_ZEN) == "zen-free"


def test_gateway_list_is_what_it_serves_with_models_dev_details(catalog, monkeypatch):
    """models.dev lags the gateway: a served model models.dev doesn't know yet
    is listed by id, one models.dev routes to another wire never shows, and —
    like OpenCode — one models.dev marks deprecated never shows either, even
    while the gateway still serves it (deepseek-v4-flash-free: 400)."""
    from jarvis.auth import opencode_catalog

    served = {"gpt-go", "kimi-k2.6", "old-but-served", "minimax-anthropic", "glm-new"}
    monkeypatch.setattr(opencode_catalog, "served_ids",
                        lambda provider: served if provider == pv.PROVIDER_OPENCODE else set())
    rows = dict(pv.opencode_go_models_for_picker())
    assert list(rows) == ["gpt-go", "kimi-k2.6", "glm-new"]
    # Deprecated: not listed, not kept as a saved model, swapped on startup.
    assert not pv.model_belongs_to_provider("old-but-served", pv.PROVIDER_OPENCODE)
    assert pv.normalize_model_for_provider("old-but-served", pv.PROVIDER_OPENCODE) == "gpt-go"
    assert rows["glm-new"] == "Not on models.dev yet, price unknown"
    assert pv.model_pricing("glm-new", pv.PROVIDER_OPENCODE) is None
    # Usable and kept on startup; the other-wire model is not.
    assert pv.model_belongs_to_provider("glm-new", pv.PROVIDER_OPENCODE)
    assert not pv.model_belongs_to_provider("minimax-anthropic", pv.PROVIDER_OPENCODE)
    assert pv.normalize_model_for_provider("glm-new", pv.PROVIDER_OPENCODE) == "glm-new"
    # The default is never a retiring or undescribed model.
    assert pv.opencode_go_default_model() == "gpt-go"


def test_typed_model_ids_route_to_the_right_provider(catalog):
    """Zen also serves Claude ids: a typed claude-… still goes to Anthropic."""
    from jarvis.commands.control import _provider_for_model

    assert _provider_for_model("claude-routed") == pv.PROVIDER_ANTHROPIC
    assert _provider_for_model("zen-frontier") == pv.PROVIDER_OPENCODE_ZEN
    assert _provider_for_model("brand-new-go") == pv.PROVIDER_OPENCODE
    assert _provider_for_model("vendor/paid-model") == pv.PROVIDER_OPENROUTER


def test_anthropic_api_lists_new_claude_models(catalog):
    ids = [m for m, _ in pv.anthropic_api_models_for_picker()]
    assert ids[0] == pv.ANTHROPIC_MODELS[0][0]
    assert "claude-new-1" in ids


def test_without_a_catalog_everything_is_as_before(no_builtin_keys):
    assert models_dev.providers() == {}
    assert pv.connected_model_sources() == [pv.PROVIDER_HARNESS_AGENT]
    assert pv.opencode_go_models_for_picker() == []  # nothing hard-coded to fall back to
    assert pv.opencode_go_default_model() == ""
    assert pv.opencode_zen_default_model() == pv.HARNESS_AGENT_FALLBACK_MODEL  # Zen's free tier
    assert pv.harness_agent_models_for_picker() == [(pv.HARNESS_AGENT_FALLBACK_MODEL, "Big Pickle")]
    assert pv.native_responses_models(pv.PROVIDER_OPENCODE_ZEN) == set()


# ── client ───────────────────────────────────────────────────────────────────


def test_build_catalog_client_openai_wire(catalog):
    from jarvis.auth.client import _build_catalog_client
    from jarvis.auth.opencode_client import OpenCodeClient

    catalog_keys.save("md:deepseek", "sk-deep-abcdef123456")
    c = _build_catalog_client("md:deepseek")
    assert isinstance(c, OpenCodeClient)
    assert str(c._oai.base_url) == "https://api.deepseek.com/"
    assert c._oai.api_key == "sk-deep-abcdef123456"
    assert c.max_tokens_param == "max_tokens"
    assert c.model_hint("deepseek-chat")["reasoning_field"] == "reasoning_content"

    catalog_keys.save("md:openai", "sk-openai-abcdef123456")
    o = _build_catalog_client("md:openai")
    assert o.max_tokens_param == "max_completion_tokens"
    assert o.api_format("gpt-9-pro") == "responses"
    assert o.api_format("gpt-9") == "chat"


def test_build_catalog_client_anthropic_wire(catalog):
    from anthropic import Anthropic
    from jarvis.auth.client import _build_catalog_client

    catalog_keys.save("md:minimax", "mm-key-abcdef123456")
    c = _build_catalog_client("md:minimax")
    assert isinstance(c, Anthropic)
    assert str(c.base_url).rstrip("/") == "https://api.minimax.io/anthropic"


def test_build_catalog_client_errors_are_runtime_errors(catalog):
    from jarvis.auth.client import _build_catalog_client

    with pytest.raises(RuntimeError, match="no API key"):
        _build_catalog_client("md:deepseek")
    catalog_keys.save("md:cf", "cf-key-abcdef")
    with pytest.raises(RuntimeError, match="CF_ACCOUNT"):
        _build_catalog_client("md:cf")
    with pytest.raises(RuntimeError, match="not in the models.dev catalog"):
        _build_catalog_client("md:nope")


class _FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(())


def _request(client, model, *, thinking=None):
    from jarvis.auth.opencode_client import _OpenCodeMessages

    fake = MagicMock()
    fake.chat.completions = _FakeCompletions()
    msgs = _OpenCodeMessages(fake, owner=client)
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ]
    with msgs.stream(model=model, messages=history, max_tokens=64000, thinking=thinking):
        pass
    return fake.chat.completions.calls[0]


def test_request_shape_follows_the_catalog(catalog):
    from jarvis.auth.client import _build_catalog_client

    catalog_keys.save("md:deepseek", "sk-deep-abcdef123456")
    client = _build_catalog_client("md:deepseek")

    # Interleaved-reasoning model: reasoning_content is sent back, as today.
    kw = _request(client, "deepseek-chat", thinking={"type": "enabled"})
    assistant = [m for m in kw["messages"] if m["role"] == "assistant"][0]
    assert assistant["reasoning_content"] == "hmm"
    assert kw["reasoning_effort"]
    assert kw["max_tokens"] == 8192  # clamped to the model's output limit

    # A plain model: no reasoning field (strict APIs reject it), no effort.
    kw = _request(client, "deepseek-old", thinking={"type": "enabled"})
    assistant = [m for m in kw["messages"] if m["role"] == "assistant"][0]
    assert "reasoning_content" not in assistant
    assert "reasoning_effort" not in kw

    catalog_keys.save("md:openai", "sk-openai-abcdef123456")
    kw = _request(_build_catalog_client("md:openai"), "gpt-9")
    assert "max_tokens" not in kw and kw["max_completion_tokens"] == 64000


def test_builtin_clients_keep_the_old_request_shape():
    """No hints (OpenCode Go/Zen): reasoning_content stays on every
    assistant message and max_tokens is passed through untouched."""
    from jarvis.auth.opencode_client import OpenCodeClient

    kw = _request(OpenCodeClient(api_key="x", base_url="https://example.invalid/"), "kimi-k2.6")
    assistant = [m for m in kw["messages"] if m["role"] == "assistant"][0]
    assert assistant["reasoning_content"] == "hmm"
    assert kw["max_tokens"] == 64000


# ── switching ────────────────────────────────────────────────────────────────


@pytest.fixture
def quiet_switch(tmp_path, monkeypatch):
    """Model/provider switching without touching real settings or files."""
    monkeypatch.setattr("jarvis.commands.control._secure_write", lambda *a, **k: None)
    monkeypatch.setattr("jarvis.commands.control.save_last_model", lambda *a, **k: None)
    monkeypatch.setattr("jarvis.commands.control.header_panel", lambda *a, **k: None)
    monkeypatch.setattr(state, "provider", "opencode_zen")
    monkeypatch.setattr(state, "MODEL", "mimo-v2.5-free")
    monkeypatch.setattr(state, "harness_agent_free", True)
    monkeypatch.setattr(state, "client", None)


def test_picking_a_catalog_model_switches_provider_and_client(catalog, quiet_switch):
    from jarvis.auth.opencode_client import OpenCodeClient
    from jarvis.commands.control import _apply_model_selection

    catalog_keys.save("md:deepseek", "sk-deep-abcdef123456")
    _apply_model_selection("deepseek-old", source="md:deepseek")
    assert state.provider == "md:deepseek"
    assert state.MODEL == "deepseek-old"
    assert isinstance(state.client, OpenCodeClient)
    assert not state.harness_agent_free


def test_provider_command_accepts_a_bare_models_dev_id(catalog, quiet_switch):
    from jarvis.commands.control import _handle_provider

    catalog_keys.save("md:groq", "gsk-abcdef123456")
    _handle_provider("groq")
    assert state.provider == "md:groq"
    assert state.MODEL == "llama-x"


def test_make_client_restores_a_saved_catalog_provider(catalog, quiet_switch, monkeypatch, tmp_path):
    from jarvis.auth import client as cl
    from jarvis.auth.opencode_client import OpenCodeClient

    catalog_keys.save("md:deepseek", "sk-deep-abcdef123456")
    monkeypatch.setattr(cl, "PROVIDER_FILE", tmp_path / "provider")
    monkeypatch.setattr(cl, "_secure_write", lambda *a, **k: None)
    monkeypatch.setattr("jarvis.storage.prefs.should_use_first_run_harness_defaults", lambda: False)
    monkeypatch.setattr("jarvis.storage.prefs.load_saved_model", lambda: "deepseek-old")
    monkeypatch.setattr("jarvis.storage.prefs.load_saved_preferences", lambda: ("deepseek-old", "md:deepseek"))
    monkeypatch.setattr(cl, "_has_usable_provider_credentials", lambda: False)
    c = cl.make_client(interactive=False)
    assert isinstance(c, OpenCodeClient)
    assert state.provider == "md:deepseek"
    assert state.MODEL == "deepseek-old"


def test_make_client_falls_back_to_free_when_the_key_is_gone(catalog, quiet_switch, monkeypatch, tmp_path):
    from jarvis.auth import client as cl

    monkeypatch.setattr(cl, "PROVIDER_FILE", tmp_path / "provider")
    monkeypatch.setattr(cl, "_secure_write", lambda *a, **k: None)
    monkeypatch.setattr("jarvis.storage.prefs.should_use_first_run_harness_defaults", lambda: False)
    monkeypatch.setattr("jarvis.storage.prefs.load_saved_model", lambda: "deepseek-old")
    monkeypatch.setattr("jarvis.storage.prefs.load_saved_preferences", lambda: ("deepseek-old", "md:deepseek"))
    monkeypatch.setattr(cl, "_has_usable_provider_credentials", lambda: False)
    monkeypatch.setattr(cl, "build_harness_agent_client", lambda: MagicMock())
    cl.make_client(interactive=False)
    assert state.provider == pv.PROVIDER_OPENCODE_ZEN
    assert state.harness_agent_free


def test_unrelated_env_key_does_not_move_first_run_off_the_free_tier(catalog, monkeypatch):
    """OPENAI_API_KEY in the shell lists OpenAI in /model — it must not count
    as "credentials" that change which provider a session starts on."""
    from jarvis.auth import client as cl

    monkeypatch.setenv("OPENAI_API_KEY", "sk-unrelated")
    assert "md:openai" in catalog_keys.connected()
    monkeypatch.setattr(cl, "_has_usable_anthropic_auth", lambda: False)
    monkeypatch.setattr(cl, "load_codex_oauth_tokens", lambda: None)
    monkeypatch.setattr(cl, "_has_openrouter_key", lambda: False)
    monkeypatch.setattr(cl, "_has_opencode_key", lambda: False)
    monkeypatch.setattr(cl, "_has_opencode_zen_key", lambda: False)
    for var in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert cl._has_usable_provider_credentials() is False


def test_cost_uses_the_catalog_price(catalog, monkeypatch):
    from jarvis.repl.stats import estimated_cost

    monkeypatch.setattr(state, "provider", "md:deepseek")
    monkeypatch.setattr(state, "MODEL", "deepseek-chat")
    monkeypatch.setattr(state, "total_in", 1_000_000)
    monkeypatch.setattr(state, "total_out", 1_000_000)
    assert estimated_cost() == pytest.approx(3.0)
    monkeypatch.setattr(state, "MODEL", "no-price-known")
    assert estimated_cost() == 0.0
