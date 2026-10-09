"""Provider registry: Anthropic, OpenRouter, OpenCode Go, and OpenCode Zen.

SINGLE source of truth for the built-in model definitions. Every other
provider — and newer models of the built-in ones — comes from the live
models.dev catalog (jarvis/auth/models_dev.py, ids "md:<id>"); see the
"models.dev catalog" section below.

╔══════════════════════════════════════════════════════════════════════════╗
║  TO ADD A MODEL: add ONE line to the MODELS list below. That's it.         ║
║                                                                            ║
║    ModelSpec("model-id", "Human label — note", PROVIDER_X, in$, out$)      ║
║                                                                            ║
║  - in$/out$ are USD per 1M tokens (0.0 for free tiers); used by /cost.     ║
║  - add default=True to make it that provider's default model.              ║
║  Everything else — picker lists, pricing, per-provider defaults — is       ║
║  derived automatically from MODELS. No other file needs touching.          ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import os
import re
from dataclasses import dataclass
from typing import Any

# ── Provider identifiers ──────────────────────────────────────────────────────
PROVIDERS = ("anthropic", "openrouter", "opencode", "opencode_zen", "openai_codex")
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_OPENCODE = "opencode"
PROVIDER_OPENCODE_ZEN = "opencode_zen"
PROVIDER_OPENAI_CODEX = "openai_codex"
# Model-picker only — free OpenCode Zen tier (no API key). Backend: opencode_zen.
PROVIDER_HARNESS_AGENT = "harness_agent"

# Model-picker sources (Anthropic splits API key vs OAuth subscription).
PROVIDER_ANTHROPIC_API = "anthropic_api"
PROVIDER_ANTHROPIC_AUTH = "anthropic_auth"

PROVIDER_OPENAI_CODEX_AUTH = "openai_codex_auth"

# ── Auth mode identifiers ─────────────────────────────────────────────────────
AUTH_API_KEY = "api_key"
AUTH_OAUTH = "oauth"

PROVIDER_LABELS = {
    PROVIDER_ANTHROPIC: "Anthropic",
    PROVIDER_OPENROUTER: "OpenRouter",
    PROVIDER_OPENCODE: "OpenCode Go",
    PROVIDER_OPENCODE_ZEN: "OpenCode Zen",
    PROVIDER_OPENAI_CODEX: "OpenAI Codex",
}

MODEL_SOURCE_LABELS = {
    PROVIDER_HARNESS_AGENT: "Harness Agent",
    PROVIDER_ANTHROPIC_API: "Anthropic API",
    PROVIDER_ANTHROPIC_AUTH: "Anthropic Auth",
    PROVIDER_OPENROUTER: "OpenRouter",
    PROVIDER_OPENCODE: "OpenCode Go",
    PROVIDER_OPENCODE_ZEN: "OpenCode Zen",
    PROVIDER_OPENAI_CODEX_AUTH: "OpenAI Codex Auth",
}

# Picker display order (Harness Agent first — always free, no setup).
MODEL_SOURCES = (
    PROVIDER_HARNESS_AGENT,
    PROVIDER_ANTHROPIC_API,
    PROVIDER_ANTHROPIC_AUTH,
    PROVIDER_OPENROUTER,
    PROVIDER_OPENCODE,
    PROVIDER_OPENCODE_ZEN,
    PROVIDER_OPENAI_CODEX_AUTH,
)

# ── SINGLE SOURCE OF TRUTH: all models ────────────────────────────────────────
@dataclass(frozen=True)
class ModelSpec:
    """One model. Add an entry to MODELS to register it everywhere.

    id            provider model id sent on the wire
    label         human description shown in the /model picker
    provider      one of the PROVIDER_* identifiers above
    input_price   USD per 1M input tokens  (0.0 for free tiers) — /cost only
    output_price  USD per 1M output tokens (0.0 for free tiers) — /cost only
    default       True marks this model as its provider's default
    supports_images  True if the model can natively process image inputs
    """
    id: str
    label: str
    provider: str
    input_price: float = 0.0
    output_price: float = 0.0
    default: bool = False
    supports_images: bool = False


MODELS: list[ModelSpec] = [
    # ── Anthropic (direct API) — Claude 5 family ──────────────────────────────
    # Newest first. Pricing: platform.claude.com/docs/en/about-claude/pricing
    ModelSpec("claude-opus-5-5",   "Opus 5.5 — latest, agentic coding & knowledge work", PROVIDER_ANTHROPIC,  4.0, 20.0, supports_images=True),
    ModelSpec("claude-sonnet-5-5", "Sonnet 5.5 — latest Sonnet, fast everyday coding",   PROVIDER_ANTHROPIC,  2.0, 10.0, supports_images=True),
    ModelSpec("claude-fable-5-1",  "Fable 5.1 — top reasoning, long-horizon",            PROVIDER_ANTHROPIC, 10.0, 50.0, supports_images=True),
    ModelSpec("claude-mythos-5-1", "Mythos 5.1 — Fable 5.1 tier (invite-only)",          PROVIDER_ANTHROPIC, 10.0, 50.0, supports_images=True),
    ModelSpec("claude-opus-5",     "Opus 5 — high capability",                           PROVIDER_ANTHROPIC,  5.0, 25.0, supports_images=True),
    ModelSpec("claude-sonnet-5",   "Sonnet 5 — balanced",                                PROVIDER_ANTHROPIC,  2.0, 10.0, default=True, supports_images=True),

    # ── OpenRouter: no entries here, on purpose ───────────────────────────────
    # Every OpenRouter model, price, free flag and vision flag is read live:
    # the free tier and the list of served ids from openrouter.ai/api/v1/models
    # (jarvis/auth/openrouter_catalog.py), paid models from models.dev. A
    # hard-coded list here went stale (a retired ":free" model kept its Free
    # tag and was the default; a price was 5x off) — see openrouter_default_model.

    # ── OpenCode Go and OpenCode Zen: no entries here, on purpose ─────────────
    # Their models, prices, vision and tool flags come from models.dev
    # ("opencode-go" / "opencode"), limited to what each gateway serves right
    # now (jarvis/auth/opencode_catalog.py). See _gateway_rows.

    # ── Harness Agent (free OpenCode Zen, no API key): no entries either ─────
    # Its list is OpenCode Zen's (above) narrowed to $0 models — see
    # harness_agent_models. A hard-coded list kept retired ids (hy3-free,
    # x-preview-f-free: 401) and a dead default.

    # ── OpenAI Codex (ChatGPT subscription / OAuth) ───────────────────────────
    # Offline seed only — the live line-up comes from the Codex backend (see
    # jarvis/auth/codex_catalog.py). gpt-5.5 (404) and gpt-5.4 / gpt-5.4-mini
    # (400) no longer serve ChatGPT accounts.
    ModelSpec("gpt-6-luna",    "GPT-6-Luna — Fast and affordable model for easier tasks", PROVIDER_OPENAI_CODEX, default=True, supports_images=True),
    ModelSpec("gpt-5.6-terra", "GPT-5.6-Terra — Older balanced model for straightforward work", PROVIDER_OPENAI_CODEX, supports_images=True),
    ModelSpec("gpt-5.6-luna",  "GPT-5.6-Luna — Older fast and efficient model", PROVIDER_OPENAI_CODEX, supports_images=True),

]

# model_id -> (description, provider, (input_price_per_1M, output_price_per_1M))
# Derived from MODELS; kept for back-compat with code that reads MODEL_INFO directly.
MODEL_INFO: dict[str, tuple[str, str, tuple[float, float]]] = {
    m.id: (m.label, m.provider, (m.input_price, m.output_price))
    for m in MODELS
}

# Set of model IDs that support native image inputs (multimodal).
# Mutable: models discovered at runtime (e.g. the live OpenRouter free tier)
# register themselves here via register_dynamic_model().
IMAGE_SUPPORTING_MODELS: set[str] = {m.id for m in MODELS if m.supports_images}


def model_supports_images(model_id: str, provider: str | None = None) -> bool:
    """Return True if the given model ID can natively process image inputs.

    ``provider`` defaults to the active one; catalog providers answer from
    models.dev for that provider specifically.
    """
    explicit = provider is not None
    if provider is None:
        from .. import state

        provider = getattr(state, "provider", "") or ""
    if provider == PROVIDER_HARNESS_AGENT:
        provider = PROVIDER_OPENCODE_ZEN  # the free tier is Zen's gateway: same models
    if is_local_provider(provider):
        try:
            lm = _local().get_model(provider, model_id)
        except Exception:
            lm = None
        return bool(lm and lm.vision)
    if is_catalog_provider(provider):
        try:
            m = _catalog().get_model(provider, model_id)
        except Exception:
            m = None
        return bool(m and m.images)
    if provider in _NATIVE_ENRICHED:
        info = MODEL_INFO.get(model_id)
        if info and info[1] == provider:
            return model_id in IMAGE_SUPPORTING_MODELS
        # models.dev's answer for *this* provider — the id-keyed set may hold
        # another provider's model with the same id.
        try:
            m = _catalog().native_model(provider, model_id)
        except Exception:
            m = None
        if m is not None:
            return m.images
        if info and explicit:  # a model list asking about *this* provider's row
            return False
    return model_id in IMAGE_SUPPORTING_MODELS


def free_model_ids() -> dict[str, set[str]]:
    """Ids the free-tier catalogs list right now (on-disk caches, no network)."""
    out: dict[str, set[str]] = {}
    try:
        from ..auth.openrouter_catalog import cached_free_models

        out[PROVIDER_OPENROUTER] = {m.id for m in cached_free_models()}
    except Exception:
        pass
    try:
        out[PROVIDER_OPENCODE_ZEN] = {mid for mid, _label in harness_agent_models()}
    except Exception:
        pass
    return out


def model_is_free(model_id: str, source: str, free_ids: dict[str, set[str]] | None = None) -> bool:
    """$0 to use — the /model "free" tag (TUI and web). Only claimed when a
    catalog says so: an unknown price is never "free". ``free_ids`` is
    ``free_model_ids()``, passed in when tagging a whole list."""
    if free_ids is None:
        free_ids = free_model_ids()
    if source == PROVIDER_HARNESS_AGENT or is_local_provider(source):
        return True  # the free tier, or a model on your own machine: no cost
    if source == PROVIDER_OPENROUTER:
        # OpenRouter's own $0 list (openrouter.ai/api/v1/models) decides — never
        # the id: a ":free" model OpenRouter retired is not free, it's gone.
        listed = free_ids.get(source)
        if listed:
            return model_id in listed
        try:  # that list never fetched yet: models.dev's prices
            found = _catalog().native_model(source, model_id)
        except Exception:
            found = None
        return bool(found and found.free)
    if source in (PROVIDER_OPENCODE_ZEN, PROVIDER_OPENCODE):
        # models.dev's price for this gateway (both 0), never the id's "-free".
        try:
            found = _catalog().native_model(source, model_id)
        except Exception:
            found = None
        if found is not None:
            return found.free
        return model_id in free_ids.get(source, ())  # Zen's free list (served ∩ $0)
    if is_catalog_provider(source):
        try:
            found = _catalog().get_model(source, model_id)
        except Exception:
            found = None
        return bool(found and found.free)  # models.dev lists both prices as 0
    return False


def model_sees_images(model_id: str, source: str) -> bool:
    """The /model image tag: same answer the request path uses (``model_supports_images``).
    Rows from ``all_model_picker_rows`` have already registered discovered models."""
    try:
        return bool(model_supports_images(model_id, source))
    except Exception:
        return False


# Searching one of these in /model lists only the models that can see images.
VISION_SEARCH_WORDS = frozenset({"image", "images", "vision", "photo", "photos", "picture", "pictures"})


def register_dynamic_model(
    model_id: str,
    label: str,
    provider: str,
    *,
    input_price: float = 0.0,
    output_price: float = 0.0,
    supports_images: bool = False,
) -> None:
    """Register a model discovered at runtime so lookups downstream work.

    Live catalogs (OpenRouter's free tier) surface models that no ModelSpec
    describes. Without this, /cost would fall back to Anthropic pricing and
    image inputs would be silently dropped for models that accept them.
    Static ModelSpec entries always win — they carry curated pricing.
    """
    if not model_id or model_id in MODEL_INFO:
        if supports_images:
            IMAGE_SUPPORTING_MODELS.add(model_id)
        return
    MODEL_INFO[model_id] = (label, provider, (input_price, output_price))
    PRICING[model_id] = (input_price, output_price)
    if supports_images:
        IMAGE_SUPPORTING_MODELS.add(model_id)


# ── models.dev catalog (see jarvis/auth/models_dev.py) ────────────────────────
# Catalog providers are ids like "md:deepseek". Their models are never put in
# MODEL_INFO / PRICING: those are keyed by model id alone, and the same id on
# two providers would make one look like it belongs to the other. Lookups for
# them go through the catalog with the provider in hand instead.
CATALOG_PREFIX = "md:"


def is_catalog_provider(provider: str | None) -> bool:
    return bool(provider) and str(provider).startswith(CATALOG_PREFIX)


def _catalog():
    from ..auth import models_dev

    return models_dev


# ── Local models (see jarvis/auth/local_models.py) ───────────────────────────
# Ids "local:<server>" — Ollama, LM Studio, llama.cpp … on this computer or
# the LAN, plus servers the user added. Like catalog providers their models
# never enter MODEL_INFO / PRICING; everything is read from the last scan.
LOCAL_PREFIX = "local:"


def is_local_provider(provider: str | None) -> bool:
    return bool(provider) and str(provider).startswith(LOCAL_PREFIX)


def _local():
    from ..auth import local_models

    return local_models


def local_picker_sources() -> list[str]:
    """Local servers /model lists (online ones + the one in use)."""
    try:
        from .. import state

        return _local().picker_providers(getattr(state, "provider", "") or "")
    except Exception:
        return []


def _native_extras(provider: str, seen: set[str], keep=None,
                   allow_deprecated: bool = False) -> list[tuple[str, str]]:
    """Models models.dev lists for a built-in provider that ``seen`` lacks
    (and ``keep(model)`` accepts, when given). Models models.dev marks
    deprecated only with ``allow_deprecated`` — i.e. when the provider's own
    served list (which ``keep`` checks) says they still run.

    Registered like any discovered model, so pricing / vision lookups work.
    """
    try:
        found = _catalog().native_models(provider)
    except Exception:
        return []
    hide_no_tools = False
    if provider == PROVIDER_OPENROUTER:
        try:
            from ..auth.openrouter_catalog import HIDE_NO_TOOLS as hide_no_tools
        except Exception:
            hide_no_tools = False
    out: list[tuple[str, str]] = []
    for m in found:
        if m.id in seen or (hide_no_tools and not m.tools):
            continue
        if m.status == "deprecated" and not allow_deprecated:
            continue
        if keep is not None and not keep(m):
            continue
        seen.add(m.id)
        price = m.price or (0.0, 0.0)
        register_dynamic_model(
            m.id, m.label, provider,
            input_price=price[0], output_price=price[1], supports_images=m.images,
        )
        out.append((m.id, m.label))
    return out


def native_responses_models(provider: str) -> set[str]:
    """Models a built-in OpenCode gateway serves on /responses (per models.dev)."""
    try:
        return {m.id for m in _catalog().native_models(provider) if m.wire == "responses"}
    except Exception:
        return set()


def catalog_models_for_picker(provider: str) -> list[tuple[str, str]]:
    """(id, label) rows for a catalog provider — usable first, newest first."""
    try:
        return [(m.id, m.label) for m in _catalog().models(provider)]
    except Exception:
        return []


def catalog_connected_providers() -> list[str]:
    """Catalog providers with a key (and a resolvable URL), by name."""
    try:
        from ..auth import catalog_keys

        return catalog_keys.connected()
    except Exception:
        return []


def provider_label(provider: str) -> str:
    """Display name for any provider id, catalog ones included."""
    if provider in PROVIDER_LABELS:
        return PROVIDER_LABELS[provider]
    if is_local_provider(provider):
        try:
            return _local().label(provider)
        except Exception:
            return provider[len(LOCAL_PREFIX):]
    if is_catalog_provider(provider):
        try:
            p = _catalog().get_provider(provider)
        except Exception:
            p = None
        if p is not None:
            return p.name
        return provider[len(CATALOG_PREFIX):]
    return provider or ""


def model_pricing(model: str, provider: str | None = None) -> tuple[float, float] | None:
    """USD per 1M (input, output) tokens for ``model`` on ``provider``.

    None when unknown. Catalog providers are priced from models.dev, never
    from the id-keyed PRICING table (another provider may share the id).
    """
    if provider == PROVIDER_HARNESS_AGENT or is_local_provider(provider):
        return (0.0, 0.0)  # the free tier / your own hardware: no cost
    if is_catalog_provider(provider):
        try:
            m = _catalog().get_model(provider, model)
        except Exception:
            m = None
        return m.price if m is not None else None
    info = MODEL_INFO.get(model)
    if info and (provider is None or info[1] == provider):
        return PRICING.get(model)
    if provider in _NATIVE_ENRICHED:
        # Live-listed models (OpenRouter, OpenCode Go / Zen …): models.dev's
        # price for *this* provider — the id-keyed table may hold another
        # provider's entry for the same id (kimi-k2.6 is OpenCode Go and Zen).
        try:
            m = _catalog().native_model(provider, model)
        except Exception:
            m = None
        if m is not None:
            return m.price
        if info:  # the table's entry is another provider's price for this id
            return None
    return PRICING.get(model)

# ── Auto-generated model lists from MODEL_INFO ─────────────────────────────────
ANTHROPIC_MODELS = [
    (mid, info[0])
    for mid, info in MODEL_INFO.items()
    if info[1] == PROVIDER_ANTHROPIC
]

# OAuth / Pro-Max subscription catalog (newest first). Live API ids are merged in
# at runtime when OAuth connects successfully.
ANTHROPIC_AUTH_MODEL_IDS = (
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-fable-5-1",
    "claude-mythos-5-1",
    "claude-opus-5",
    "claude-sonnet-5",
)
# Claude 5 models (and Opus 4.7 / 4.8) take adaptive thinking + an effort level
# only: ``{"type": "enabled", "budget_tokens": N}`` is a 400 on all of them, and
# thinking can't be switched off on Opus 5.5 / Sonnet 5.5 / Fable / Mythos.
_ADAPTIVE_THINKING_PREFIXES = (
    "claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-mythos-5",
    "claude-opus-4-7", "claude-opus-4-8",
)
# Jarvis effort → API effort (the API has no "minimal").
_CLAUDE_EFFORT = {"ultra": "max", "max": "max", "xhigh": "xhigh", "high": "high", "medium": "medium", "low": "low", "minimal": "low"}


def claude_uses_adaptive_thinking(model_id: str) -> bool:
    return (model_id or "").lower().startswith(_ADAPTIVE_THINKING_PREFIXES)


def claude_thinking_kwargs(think_mode: bool, effort: str) -> dict[str, Any]:
    """Request fields for thinking on a Claude 5 model (Anthropic API / OAuth).

    On: adaptive thinking (summaries shown, so the transcript has something to
    show) at the chosen effort. Off: the lowest effort — the closest these
    models allow, since ``{"type": "disabled"}`` is refused on several of them.
    """
    if think_mode and effort != "none":
        return {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": _CLAUDE_EFFORT.get(effort, "high")},
        }
    return {"output_config": {"effort": "low"}}


# Used only before any catalog has ever been fetched (first run, offline):
# OpenRouter's own free router, which forwards to whichever free model is up.
# It is an OpenRouter endpoint, not a model choice — the live catalogs pick
# real models as soon as they arrive (``openrouter_default_model``).
OPENROUTER_DEFAULT_MODEL = "openrouter/free"


def openrouter_models_for_picker(live: bool = False) -> list[tuple[str, str]]:
    """OpenRouter rows, all live: every free model OpenRouter serves right now,
    then every other (paid) model models.dev lists for OpenRouter.

    The free tier is read from OpenRouter's public catalog (no API key) — see
    :mod:`jarvis.auth.openrouter_catalog`. models.dev rows that OpenRouter no
    longer serves are dropped, and once OpenRouter's own free list is known,
    models.dev's idea of which models are free is ignored (it lags behind).
    ``live=False`` reads the on-disk caches only and never blocks; ``live=True``
    refreshes the OpenRouter catalog first.
    """
    try:
        from ..auth.openrouter_catalog import free_models, served_ids

        discovered = free_models(live=live)
        served = served_ids()
    except Exception:
        discovered, served = [], set()

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in discovered:
        register_dynamic_model(
            m.id, m.label, PROVIDER_OPENROUTER, supports_images=m.supports_images
        )
        if m.id not in seen:
            seen.add(m.id)
            out.append((m.id, m.label))

    def keep(m) -> bool:
        if served and m.id not in served:
            return False  # retired on OpenRouter; models.dev hasn't caught up
        if discovered and m.free:
            return False  # OpenRouter's own $0 list (above) is the free tier
        return True

    # Then everything else OpenRouter serves (paid models), from models.dev.
    out.extend(_native_extras(PROVIDER_OPENROUTER, seen, keep=keep, allow_deprecated=bool(served)))
    if not out:
        # No catalog fetched yet (first run, offline): OpenRouter's free router,
        # so the provider stays listed and usable until the real list arrives.
        out.append((OPENROUTER_DEFAULT_MODEL, "OpenRouter Free — routes to an available free model"))
    return out


def openrouter_default_model() -> str:
    """Default OpenRouter model: the best free one that actually works.

    Pinning a hard-coded id here rots — OpenRouter retires free models
    regularly, and a dead default means the first request 404s. Models that
    can't call tools or that refused this account are listed in the picker but
    never chosen automatically: this is the id a failed turn falls back to.
    """
    try:
        from ..auth.openrouter_catalog import usable_free_models

        discovered = usable_free_models()
    except Exception:
        discovered = []
    if discovered:
        return discovered[0].id
    # OpenRouter's catalog never fetched yet: a free, tool-capable model from
    # the models.dev cache, then OpenRouter's free router.
    try:
        for m in _catalog().native_models(PROVIDER_OPENROUTER):
            if m.free and m.tools:
                return m.id
    except Exception:
        pass
    return OPENROUTER_DEFAULT_MODEL


def codex_models_for_picker(live: bool = False) -> list[tuple[str, str]]:
    """Codex rows: the account's live line-up, or the seeds when it's unknown.

    Unlike OpenRouter, seeds are *not* appended to a live list — a model the
    backend no longer serves is dead, and offering it is what broke Codex when
    gpt-5.5 was retired. ``live=False`` reads the on-disk cache only.
    """
    try:
        from ..auth.codex_catalog import models_for_display

        discovered = models_for_display(live=live)
    except Exception:
        discovered = []
    if not discovered:
        return list(CODEX_MODELS)
    for m in discovered:
        register_dynamic_model(
            m.id, m.label, PROVIDER_OPENAI_CODEX, supports_images=m.supports_images
        )
    return [(m.id, m.label) for m in discovered]


def codex_default_model() -> str:
    """Default Codex model: the backend's top pick that hasn't refused us."""
    try:
        from ..auth.codex_catalog import models_for_display, refused_ids

        usable = [m for m in models_for_display() if m.usable]
        refused = refused_ids()
    except Exception:
        usable, refused = [], set()
    if usable:
        return usable[0].id
    for mid, _ in CODEX_MODELS:
        if mid not in refused:
            return mid
    return CODEX_DEFAULT_MODEL


# The free tier's model before any catalog has arrived (first run, offline):
# one long-lived free id, shown alone only until the live list is known.
HARNESS_AGENT_FALLBACK_MODEL = "big-pickle"

# Tiers a default skips when there is anything else.
_PREVIEW_TIER = re.compile(r"(preview|-pro\b|exp|beta)")


def _zen_natives() -> dict:
    try:
        return {m.id: m for m in _catalog().native_models(PROVIDER_OPENCODE_ZEN)}
    except Exception:
        return {}


def harness_agent_models() -> list[tuple[str, str]]:
    """The free tier's models: OpenCode Zen's list (``_gateway_rows`` — what the
    gateway serves, described by models.dev, deprecated ones dropped) narrowed
    to the ones models.dev prices at $0, i.e. the rows Zen tags Free. Nothing
    hard-coded; caches only, never the network. With models.dev off (or not
    fetched yet) OpenCode's own free catalog (``auth/zen_catalog``) stands in."""
    natives = _zen_natives()
    if natives:
        return [(mid, label) for mid, label in _gateway_rows(PROVIDER_OPENCODE_ZEN)
                if mid in natives and natives[mid].free]
    try:
        from ..auth.zen_catalog import cached_free_models

        return cached_free_models()
    except Exception:
        return []


def harness_agent_default_model() -> str:
    """The free tier's first model, by rule (as a Zen key starts): the first
    listed that can call tools and isn't a preview / beta tier, when there is
    one. ``HARNESS_AGENT_FALLBACK_MODEL`` while no list is known."""
    ids = [mid for mid, _ in harness_agent_models()]
    natives = _zen_natives()
    usable = [mid for mid in ids if mid not in natives or natives[mid].tools]
    plain = [mid for mid in usable
             if not _PREVIEW_TIER.search(mid.lower())
             and (mid not in natives or natives[mid].status != "beta")]
    pick = plain or usable or ids
    return pick[0] if pick else HARNESS_AGENT_FALLBACK_MODEL


def _refresh_harness_agent_sources() -> None:
    """Network: what Zen serves + models.dev (304 when unchanged), or
    OpenCode's own catalog when models.dev is off."""
    try:
        from ..auth.opencode_catalog import refresh as _served_refresh

        _served_refresh()
    except Exception:
        pass
    try:
        if _catalog().enabled():
            _catalog().refresh()
            return
        from ..auth.zen_catalog import refresh_free_models

        refresh_free_models()
    except Exception:
        pass


def harness_agent_models_for_picker(
    live: bool = False, cached: bool = False
) -> list[tuple[str, str]]:
    """Harness Agent rows for /model, default first — always at least one (no
    credentials needed): before any catalog arrives, the fallback model alone.

    ``live=True`` refreshes the catalogs first (network — worker threads
    only); otherwise only the on-disk caches are read (``cached`` is accepted
    for callers; every read is cached now)."""
    if live:
        _refresh_harness_agent_sources()
    rows = harness_agent_models()
    if not rows:
        return [(HARNESS_AGENT_FALLBACK_MODEL, "Big Pickle")]
    default = harness_agent_default_model()
    return sorted(rows, key=lambda row: row[0] != default)  # stable: default first


def _gateway_served(provider: str) -> set[str]:
    try:
        from ..auth.opencode_catalog import served_ids

        return served_ids(provider)
    except Exception:
        return set()


def _gateway_skipped(provider: str) -> frozenset:
    try:
        return _catalog().native_skipped(provider)
    except Exception:
        return frozenset()


def _gateway_retired(provider: str, model: str) -> bool:
    """models.dev marks ``model`` deprecated on this OpenCode gateway. OpenCode
    itself drops those (provider.ts): the gateway can keep serving an id whose
    upstream is gone — deepseek-v4-flash-free answered 400 "Model is
    unavailable" while still on /zen/v1/models, tagged free."""
    try:
        found = _catalog().native_model(provider, model)
    except Exception:
        return False
    return bool(found and found.status == "deprecated")


def _gateway_rows(provider: str) -> list[tuple[str, str]]:
    """An OpenCode gateway's models, all live. The gateway's served list says
    what exists; models.dev describes each (name, price, vision, tools —
    usable first, newest first) and, like OpenCode, deprecated ones are
    dropped even when still served. A served model models.dev doesn't
    describe yet is still listed (by id, price unknown); one models.dev says
    this client can't reach (another wire) never is. Before the served list
    is known, models.dev's list stands alone."""
    served = _gateway_served(provider)
    keep = (lambda m: m.id in served) if served else None
    rows = _native_extras(provider, set(), keep=keep)
    if served:
        try:
            described = {m.id for m in _catalog().native_models(provider)}
        except Exception:
            described = set()
        unknown = sorted(served - described - _gateway_skipped(provider))
        rows.extend((mid, "Not on models.dev yet, price unknown") for mid in unknown)
    return rows


def opencode_zen_live_models_for_picker() -> list[tuple[str, str]]:
    """OpenCode Zen with an API key (models.dev ``opencode``)."""
    return _gateway_rows(PROVIDER_OPENCODE_ZEN)


def opencode_go_models_for_picker() -> list[tuple[str, str]]:
    """OpenCode Go (models.dev ``opencode-go``)."""
    return _gateway_rows(PROVIDER_OPENCODE)


def _gateway_default(provider: str) -> str:
    """A first model for an OpenCode gateway, chosen from live data.

    Zen: the free tier's pick when it serves a free model (so a key never
    starts on a paid frontier model by surprise), else like Go. Go: the newest model that
    can call tools, skipping preview / pro / beta tiers when there is
    anything else. Nothing cached yet (first run): fetch now — the request
    that needs this model goes over the network anyway.
    """
    def candidates():
        rows = {mid for mid, _ in _gateway_rows(provider)}
        try:
            natives = _catalog().native_models(provider)
        except Exception:
            natives = []
        # Only models models.dev describes as tool-capable and not retiring.
        return [m for m in natives if m.id in rows and m.tools and m.status != "deprecated"]

    found = candidates()
    if not found:
        try:
            from ..auth.opencode_catalog import refresh as _served_refresh

            _catalog().refresh()
            _served_refresh()
        except Exception:
            pass
        found = candidates()
    def plain(ms):
        return [m for m in ms if not _PREVIEW_TIER.search(m.id.lower()) and m.status != "beta"]

    if provider == PROVIDER_OPENCODE_ZEN:
        if any(m.free for m in found):
            return harness_agent_default_model()
    else:
        # A Go subscription starts on one of its own models, not a free trial one.
        found = [m for m in found if not m.free] or found
    pick = plain(found) or found
    if pick:
        return pick[0].id
    # models.dev unreachable on a first run: Zen still serves the free tier.
    return harness_agent_default_model() if provider == PROVIDER_OPENCODE_ZEN else ""


def opencode_go_default_model() -> str:
    return _gateway_default(PROVIDER_OPENCODE)


def opencode_zen_default_model() -> str:
    return _gateway_default(PROVIDER_OPENCODE_ZEN)


def anthropic_api_models_for_picker() -> list[tuple[str, str]]:
    """Anthropic API key: the curated Claude rows, then any other current
    Claude model models.dev lists."""
    rows = list(ANTHROPIC_MODELS)
    rows.extend(_native_extras(PROVIDER_ANTHROPIC, {m for m, _ in rows}))
    return rows
CODEX_MODELS = [
    (mid, info[0])
    for mid, info in MODEL_INFO.items()
    if info[1] == PROVIDER_OPENAI_CODEX
]

# ── Pricing dict (auto-generated from MODEL_INFO) ─────────────────────────────
PRICING: dict[str, tuple[float, float]] = {
    mid: info[2]
    for mid, info in MODEL_INFO.items()
}

# ── Default models per provider (derived from ModelSpec.default flags) ─────────
_DEFAULT_BY_PROVIDER: dict[str, str] = {m.provider: m.id for m in MODELS if m.default}

CODEX_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER[PROVIDER_OPENAI_CODEX]
ANTHROPIC_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER[PROVIDER_ANTHROPIC]

_PROVIDER_DEFAULT_MODEL = {
    PROVIDER_ANTHROPIC: ANTHROPIC_DEFAULT_MODEL,
    PROVIDER_OPENROUTER: OPENROUTER_DEFAULT_MODEL,
    PROVIDER_OPENAI_CODEX: CODEX_DEFAULT_MODEL,
}

OPENROUTER_BASE_URL = "https://openrouter.ai/api"

OPENCODE_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


def _has_anthropic_api() -> bool:
    if os.getenv("ANTHROPIC_API_KEY"):
        return True
    from .paths import KEY_FILE

    try:
        return KEY_FILE.exists() and bool(KEY_FILE.read_text().strip())
    except OSError:
        return False


def _has_anthropic_oauth() -> bool:
    try:
        from ..auth.oauth_tokens import load_oauth_tokens
        return load_oauth_tokens() is not None
    except Exception:
        return False


def _has_openai_codex_oauth() -> bool:
    try:
        from ..auth.codex_oauth_tokens import load_codex_oauth_tokens
        return load_codex_oauth_tokens() is not None
    except Exception:
        return False


def is_harness_agent_model(model: str) -> bool:
    """True when ``model`` is a free Harness Agent (OpenCode Zen public) model:
    on the live list (``harness_agent_models``, on-disk caches), or the
    fallback model while no list is known."""
    m = (model or "").strip()
    if not m:
        return False
    rows = harness_agent_models()
    if rows:
        return any(mid == m for mid, _ in rows)
    return m == HARNESS_AGENT_FALLBACK_MODEL


def connected_model_sources() -> list[str]:
    """Model-picker sources. Harness Agent is always first and always included.

    Every other source appears only while its credential exists (API key in
    env or on disk, or stored OAuth tokens). Nothing is cached, so a key added
    or removed mid-session shows up the next time ``/model`` opens.
    """
    sources: list[str] = [PROVIDER_HARNESS_AGENT]
    if _has_anthropic_api():
        sources.append(PROVIDER_ANTHROPIC_API)
    if _has_anthropic_oauth():
        sources.append(PROVIDER_ANTHROPIC_AUTH)
    if _has_openai_codex_oauth():
        sources.append(PROVIDER_OPENAI_CODEX_AUTH)
    if os.getenv("OPENROUTER_API_KEY"):
        sources.append(PROVIDER_OPENROUTER)
    else:
        from .paths import OPENROUTER_KEY_FILE
        try:
            if OPENROUTER_KEY_FILE.exists() and OPENROUTER_KEY_FILE.read_text().strip():
                sources.append(PROVIDER_OPENROUTER)
        except OSError:
            pass
    if os.getenv("OPENCODE_API_KEY"):
        sources.append(PROVIDER_OPENCODE)
    else:
        from .paths import OPENCODE_KEY_FILE
        try:
            if OPENCODE_KEY_FILE.exists() and OPENCODE_KEY_FILE.read_text().strip():
                sources.append(PROVIDER_OPENCODE)
        except OSError:
            pass
    if os.getenv("OPENCODE_ZEN_API_KEY"):
        sources.append(PROVIDER_OPENCODE_ZEN)
    else:
        from .paths import OPENCODE_ZEN_KEY_FILE
        try:
            if OPENCODE_ZEN_KEY_FILE.exists() and OPENCODE_ZEN_KEY_FILE.read_text().strip():
                sources.append(PROVIDER_OPENCODE_ZEN)
        except OSError:
            pass
    # Providers discovered from models.dev that have a key, after the built-ins.
    sources.extend(catalog_connected_providers())
    # Models running on this computer / the LAN (last scan — no network here).
    sources.extend(local_picker_sources())
    # Harness Agent must always appear — even when other providers are configured.
    out: list[str] = []
    seen: set[str] = set()
    for src in sources:
        if src in seen:
            continue
        seen.add(src)
        out.append(src)
    if PROVIDER_HARNESS_AGENT not in seen:
        out.insert(0, PROVIDER_HARNESS_AGENT)
    elif out and out[0] != PROVIDER_HARNESS_AGENT:
        out.remove(PROVIDER_HARNESS_AGENT)
        out.insert(0, PROVIDER_HARNESS_AGENT)
    return out


def all_model_picker_rows(
    live: bool = False, cached: bool = False
) -> list[tuple[str, str, str]]:
    """All /model rows as (source, model_id, description). Harness Agent always first.

    ``cached=True`` serves discovered models from the on-disk catalog cache so
    the caller never blocks on the network — what the picker uses on open.
    """
    rows: list[tuple[str, str, str]] = [
        (PROVIDER_HARNESS_AGENT, mid, desc)
        for mid, desc in harness_agent_models_for_picker(live=live, cached=cached)
    ]
    try:
        for src in connected_model_sources():
            if src == PROVIDER_HARNESS_AGENT:
                continue
            rows.extend(
                (src, mid, desc)
                for mid, desc in models_for_source(src, live=live, cached=cached)
            )
    except Exception:
        pass
    if not rows:
        rows = [(PROVIDER_HARNESS_AGENT, mid, desc) for mid, desc in harness_agent_models_for_picker()]
    return rows


def model_option_id(source: str, model_id: str) -> str:
    return f"{source}::{model_id}"


def parse_model_option_id(option_id: str) -> tuple[str, str]:
    if "::" in option_id:
        source, model_id = option_id.split("::", 1)
        return source, model_id
    return "", option_id


def models_for_source(source: str, live: bool = False, cached: bool = False):
    if source == PROVIDER_HARNESS_AGENT:
        return harness_agent_models_for_picker(live=live, cached=cached)
    if source == PROVIDER_OPENROUTER:
        return openrouter_models_for_picker(live=live)
    if source == PROVIDER_ANTHROPIC_API:
        return anthropic_api_models_for_picker()
    if source == PROVIDER_ANTHROPIC_AUTH:
        from ..auth.anthropic_models import anthropic_auth_models_for_picker
        return anthropic_auth_models_for_picker()
    if source == PROVIDER_OPENAI_CODEX_AUTH:
        return codex_models_for_picker(live=live)
    if is_catalog_provider(source):
        return catalog_models_for_picker(source)
    if is_local_provider(source):
        return _local().picker_rows(source)
    return models_for(source)


def connected_providers() -> set[str]:
    """Return set of provider identifiers that have configured API keys (file or env).

    If no provider has any configured key, returns all providers (first-run fallback)
    so the model picker isn't an empty list.
    """
    import os
    connected: set[str] = set()

    # ── Environment variables (fast, no file I/O) ──────────────────────────
    if os.getenv("ANTHROPIC_API_KEY"):
        connected.add(PROVIDER_ANTHROPIC)
    if os.getenv("OPENROUTER_API_KEY"):
        connected.add(PROVIDER_OPENROUTER)
    if os.getenv("OPENCODE_API_KEY"):
        connected.add(PROVIDER_OPENCODE)
    if os.getenv("OPENCODE_ZEN_API_KEY"):
        connected.add(PROVIDER_OPENCODE_ZEN)

    # ── Key files on disk ──────────────────────────────────────────────────
    # Lazy import to avoid circular dependency (paths → no providers imports)
    from .paths import (
        KEY_FILE, OPENROUTER_KEY_FILE,
        OPENCODE_KEY_FILE, OPENCODE_ZEN_KEY_FILE,
    )

    def _has_content(p) -> bool:
        try:
            return p.exists() and bool(p.read_text().strip())
        except OSError:
            return False

    if _has_anthropic_api() or _has_anthropic_oauth():
        connected.add(PROVIDER_ANTHROPIC)
    if _has_openai_codex_oauth():
        connected.add(PROVIDER_OPENAI_CODEX)
    if _has_content(OPENROUTER_KEY_FILE):
        connected.add(PROVIDER_OPENROUTER)
    if _has_content(OPENCODE_KEY_FILE):
        connected.add(PROVIDER_OPENCODE)
    if _has_content(OPENCODE_ZEN_KEY_FILE):
        connected.add(PROVIDER_OPENCODE_ZEN)

    # First run — no keys at all → show everything so user can see options
    if not connected:
        connected = set(PROVIDERS)
    # Catalog providers count only for themselves — they never change the
    # first-run "show everything" behaviour of the built-ins above.
    connected.update(catalog_connected_providers())
    connected.update(local_picker_sources())
    return connected


def provider_is_operational(provider: str) -> bool:
    """True when the provider can actually be used for API calls."""
    if provider in connected_providers():
        return True
    if provider != PROVIDER_OPENCODE_ZEN:
        return False
    from .. import state
    if getattr(state, "harness_agent_free", False):
        return True
    from ..auth.harness_agent import should_use_harness_agent_client
    return should_use_harness_agent_client()


def provider_connection_status(provider: str) -> tuple[str, str]:
    """Return (status suffix, style name) for provider picker rows."""
    if provider in connected_providers():
        return "  connected", "ok"
    if provider == PROVIDER_OPENCODE_ZEN and provider_is_operational(provider):
        return "  free tier", "ok"
    return "  not configured", "dim"


def models_for(provider: str):
    if provider == PROVIDER_OPENROUTER:
        return openrouter_models_for_picker()
    if provider == PROVIDER_OPENCODE:
        return opencode_go_models_for_picker()
    if provider == PROVIDER_OPENCODE_ZEN:
        return opencode_zen_live_models_for_picker()
    if provider == PROVIDER_OPENAI_CODEX:
        return codex_models_for_picker()
    if is_catalog_provider(provider):
        return catalog_models_for_picker(provider)
    if is_local_provider(provider):
        return _local().picker_rows(provider)
    return anthropic_api_models_for_picker()


# Built-in providers whose model list models.dev extends at runtime.
_NATIVE_ENRICHED = (PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN)


def _in_native_catalog(provider: str, model: str) -> bool:
    if provider not in _NATIVE_ENRICHED:
        return False
    try:
        return _catalog().native_model(provider, model) is not None
    except Exception:
        return False


def model_belongs_to_provider(model: str, provider: str) -> bool:
    """Return True when ``model`` can be sent on ``provider``."""
    m = (model or "").strip()
    if not m:
        return False
    if provider == PROVIDER_HARNESS_AGENT:
        return is_harness_agent_model(m)
    if is_local_provider(provider):
        try:
            return _local().get_model(provider, m) is not None
        except Exception:
            return False
    if is_catalog_provider(provider):
        try:
            return _catalog().get_model(provider, m) is not None
        except Exception:
            return False
    if provider == PROVIDER_OPENCODE_ZEN and is_harness_agent_model(m):
        return True
    if provider in (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN):
        if _gateway_retired(provider, m):
            return False  # deprecated on models.dev: never listed, so never kept
        served = _gateway_served(provider)
        if served:  # the gateway's own list decides (models.dev may lag)
            return m in served and m not in _gateway_skipped(provider)
    info = MODEL_INFO.get(m)
    if info and info[1] == provider:
        return True
    # A model models.dev lists for this provider — discovered at runtime, or
    # an id another provider registered first (kimi-k2.6 is both OpenCode Go
    # and Zen).
    if _in_native_catalog(provider, m):
        return True
    if info:
        return False
    if provider == PROVIDER_OPENROUTER:
        return "/" in m
    if provider == PROVIDER_ANTHROPIC:
        return m.startswith("claude-")
    if provider == PROVIDER_OPENAI_CODEX:
        # A live-discovered model picked last session isn't registered yet.
        try:
            from ..auth.codex_catalog import cached_models

            return any(cm.id == m for cm in cached_models())
        except Exception:
            return False
    return False


def infer_provider_for_model(model: str) -> str:
    """Map a model id to the provider that should serve it."""
    m = (model or "").strip()
    if not m:
        return PROVIDER_OPENCODE_ZEN
    info = MODEL_INFO.get(m)
    if info:
        prov = info[1]
        if prov == PROVIDER_HARNESS_AGENT:
            return PROVIDER_OPENCODE_ZEN
        return prov
    if "/" in m:
        return PROVIDER_OPENROUTER
    if m.startswith("claude-"):
        return PROVIDER_ANTHROPIC
    return PROVIDER_OPENCODE_ZEN


def normalize_model_for_provider(model: str, provider: str) -> str:
    """Use ``model`` when valid for ``provider``; otherwise the provider default."""
    if provider == PROVIDER_OPENAI_CODEX:
        # A saved model that Codex has since refused (e.g. a retired gpt-5.5)
        # must not survive a restart as the model every turn is sent to.
        try:
            from ..auth.codex_catalog import refused_ids

            refused = refused_ids()
        except Exception:
            refused = set()
        if (model or "").strip() not in refused and model_belongs_to_provider(model, provider):
            return model.strip()
        return codex_default_model()
    if provider == PROVIDER_OPENROUTER:
        # A saved model OpenRouter has since retired: pick a live one now,
        # rather than 404 on the first message.
        try:
            from ..auth.openrouter_catalog import served_ids

            served = served_ids()
        except Exception:
            served = set()
        if served and (model or "").strip() and model.strip() not in served:
            return openrouter_default_model()
    if provider in (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN):
        # Same for a model the OpenCode gateway no longer serves.
        served = _gateway_served(provider)
        if served and (model or "").strip() and model.strip() not in served:
            return _gateway_default(provider)
    if model_belongs_to_provider(model, provider):
        return model.strip()
    if is_local_provider(provider):
        try:
            return _local().default_model(provider) or (model or "").strip()
        except Exception:
            return (model or "").strip()
    if is_catalog_provider(provider):
        try:
            return _catalog().default_model(provider) or (model or "").strip()
        except Exception:
            return (model or "").strip()
    if provider in (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN):
        return _gateway_default(provider)
    if provider == PROVIDER_OPENROUTER:
        return openrouter_default_model()
    return _PROVIDER_DEFAULT_MODEL.get(provider, ANTHROPIC_DEFAULT_MODEL)


def refresh_model_catalogs(retry_blocked: bool = False) -> bool:
    """Refresh every live model catalog into the on-disk cache.

    Safe to call from a background thread — only public GETs, plus the Codex
    line-up with the user's own OAuth token when signed in. Returns True when at least one catalog came back, so a caller that is
    showing cached rows knows whether re-rendering is worthwhile.

    ``retry_blocked=True`` (an explicit ``/model refresh``) also clears the
    record of models that previously refused this account.
    """
    ok = False
    try:
        # The free tier's list comes from Zen's (models.dev); OpenCode's own
        # 5 MB catalog is only its stand-in while models.dev is off.
        if not _catalog().enabled():
            from ..auth.zen_catalog import refresh_free_models as _zen_refresh

            ok = bool(_zen_refresh()) or ok
    except Exception:
        pass
    try:
        from ..auth.openrouter_catalog import refresh_free_models as _or_refresh

        ok = bool(_or_refresh(retry_blocked=retry_blocked)) or ok
    except Exception:
        pass
    try:
        from ..auth.opencode_catalog import refresh as _opencode_refresh

        ok = bool(_opencode_refresh()) or ok
    except Exception:
        pass
    try:
        from ..auth.codex_catalog import refresh_models as _codex_refresh

        ok = bool(_codex_refresh(retry_refused=retry_blocked)) or ok
    except Exception:
        pass
    try:
        # Every provider and model models.dev knows. An unchanged catalog is a
        # 304; an explicit /model refresh (retry_blocked) refetches in full.
        ok = bool(_catalog().refresh(force=retry_blocked)) or ok
    except Exception:
        pass
    try:
        # Ollama, LM Studio … on this computer: a ~1 s probe of local ports.
        ok = bool(refresh_local_models()) or ok
    except Exception:
        pass
    try:
        # Which thinking levels each Anthropic model takes (needs the signed-in
        # client); an explicit refresh also forgets levels a provider refused.
        from ..auth.thinking_caps import refresh_all as _think_refresh

        ok = bool(_think_refresh(retry_refused=retry_blocked)) or ok
    except Exception:
        pass
    return ok


def refresh_local_models() -> bool:
    """Scan local model servers (network, worker thread only). True when
    the picker's local rows changed."""
    lm = _local()
    if not lm.enabled():
        return False
    before = {p: lm.picker_rows(p) for p in local_picker_sources()}
    lm.scan()
    after = {p: lm.picker_rows(p) for p in local_picker_sources()}
    return before != after


def local_models_are_fresh() -> bool:
    try:
        lm = _local()
        return (not lm.enabled()) or lm.scan_is_fresh()
    except Exception:
        return True


def model_catalogs_are_fresh() -> bool:
    """True when every live catalog cache is within its TTL (no refresh needed)."""
    try:
        from ..auth.zen_catalog import cache_is_fresh as _zen_fresh
        from ..auth.openrouter_catalog import cache_is_fresh as _or_fresh
        from ..auth.codex_catalog import cache_is_fresh as _codex_fresh
        from ..auth.opencode_catalog import cache_is_fresh as _opencode_fresh

        return ((_catalog().enabled() or _zen_fresh()) and _or_fresh() and _codex_fresh()
                and _opencode_fresh() and _catalog().cache_is_fresh())
    except Exception:
        return False
