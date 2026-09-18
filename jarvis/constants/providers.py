"""Provider registry: Anthropic, OpenRouter, OpenCode Go, and OpenCode Zen.

SINGLE source of truth for all model definitions.

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
from dataclasses import dataclass

# ── Provider identifiers ──────────────────────────────────────────────────────
PROVIDERS = ("anthropic", "openrouter", "opencode", "opencode_zen", "openai_codex", "kimchi")
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_OPENCODE = "opencode"
PROVIDER_OPENCODE_ZEN = "opencode_zen"
PROVIDER_OPENAI_CODEX = "openai_codex"
PROVIDER_KIMCHI = "kimchi"
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
    PROVIDER_KIMCHI: "Kimchi",
}

MODEL_SOURCE_LABELS = {
    PROVIDER_HARNESS_AGENT: "Harness Agent",
    PROVIDER_ANTHROPIC_API: "Anthropic API",
    PROVIDER_ANTHROPIC_AUTH: "Anthropic Auth",
    PROVIDER_OPENROUTER: "OpenRouter",
    PROVIDER_OPENCODE: "OpenCode Go",
    PROVIDER_OPENCODE_ZEN: "OpenCode Zen",
    PROVIDER_OPENAI_CODEX_AUTH: "OpenAI Codex Auth",
    PROVIDER_KIMCHI: "Kimchi",
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
    PROVIDER_KIMCHI,
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
    # ── Anthropic (direct API) ────────────────────────────────────────────────
    ModelSpec("claude-haiku-4-5",  "Haiku 4.5 — fastest, cheapest",  PROVIDER_ANTHROPIC, 1.0,  5.0, supports_images=True),
    ModelSpec("claude-sonnet-4-6", "Sonnet 4.6 — balanced",          PROVIDER_ANTHROPIC, 3.0, 15.0, default=True, supports_images=True),
    ModelSpec("claude-opus-4-6",   "Opus 4.6 — high capability",     PROVIDER_ANTHROPIC, 5.0, 25.0, supports_images=True),
    ModelSpec("claude-opus-4-7",   "Opus 4.7 — high capability",     PROVIDER_ANTHROPIC, 5.0, 25.0, supports_images=True),
    ModelSpec("claude-opus-4-8",   "Opus 4.8 — most capable",        PROVIDER_ANTHROPIC, 5.0, 25.0, supports_images=True),

    # ── OpenRouter ────────────────────────────────────────────────────────────
    # The free tier is discovered LIVE from https://openrouter.ai/api/v1/models
    # (see jarvis/auth/openrouter_catalog.py) — every $0 model OpenRouter serves
    # shows up in /model on its own, and retired ones disappear. The entries
    # below are only the offline seed list, used before the first refresh or
    # when the network is unavailable. Don't curate free models here by hand.
    ModelSpec("deepseek/deepseek-v4-flash-0731:free",   "DeepSeek V4 Flash — 1M ctx, free",  PROVIDER_OPENROUTER, default=True),
    ModelSpec("nvidia/nemotron-3-ultra-550b-a55b:free", "Nemotron 3 Ultra — 1M ctx, free",   PROVIDER_OPENROUTER),
    ModelSpec("nvidia/nemotron-3.5-lightning:free",     "Nemotron 3.5 Lightning — 1M, free", PROVIDER_OPENROUTER),
    ModelSpec("thinkingmachines/inkling:free",          "Inkling — 1M ctx, free",            PROVIDER_OPENROUTER, supports_images=True),
    ModelSpec("nvidia/nemotron-3-super-120b-a12b:free", "Nemotron 3 Super 120B — free",      PROVIDER_OPENROUTER),
    ModelSpec("qwen/qwen3.8-27b:free",                  "Qwen3.8 27B — free",                PROVIDER_OPENROUTER, supports_images=True),
    ModelSpec("google/gemma-4-31b-it:free",             "Gemma 4 31B — free",                PROVIDER_OPENROUTER, supports_images=True),
    ModelSpec("cohere/north-mini-code:free",            "North Mini Code — free",            PROVIDER_OPENROUTER),
    ModelSpec("openrouter/free",                        "OpenRouter Free — auto-routed",     PROVIDER_OPENROUTER),
    ModelSpec("deepseek/deepseek-v4.1-flash",           "DeepSeek V4.1 Flash — fast & cheap", PROVIDER_OPENROUTER, 0.15, 1.20, supports_images=True),

    # ── OpenCode Go models (real pricing, help.apiyi) ──────────────────────────
    ModelSpec("glm-5.1",           "GLM-5.1 — latest GLM model",            PROVIDER_OPENCODE, 1.40, 4.40, supports_images=True),
    ModelSpec("glm-5",             "GLM-5 — high capability",               PROVIDER_OPENCODE, 1.00, 3.20, supports_images=True),
    ModelSpec("kimi-k2.6",         "Kimi K2.6 — Moonshot AI, most capable", PROVIDER_OPENCODE, 0.32, 1.34, default=True, supports_images=True),
    ModelSpec("kimi-k2.5",         "Kimi K2.5 — Moonshot AI",               PROVIDER_OPENCODE, 0.60, 3.00, supports_images=True),
    ModelSpec("deepseek-v4-pro",   "DeepSeek V4 Pro — strong reasoning",    PROVIDER_OPENCODE, 1.74, 3.48),
    ModelSpec("deepseek-v4-flash", "DeepSeek V4 Flash — fast & cheap",      PROVIDER_OPENCODE, 0.14, 0.28),
    ModelSpec("mimo-v2.5-pro",     "MiMo V2.5 Pro",                         PROVIDER_OPENCODE, 1.00, 3.00),
    ModelSpec("mimo-v2.5",         "MiMo V2.5",                             PROVIDER_OPENCODE, 0.40, 2.00),
    ModelSpec("mimo-v2-pro",       "MiMo V2 Pro",                           PROVIDER_OPENCODE, 1.00, 3.00),
    ModelSpec("mimo-v2-omni",      "MiMo V2 Omni",                          PROVIDER_OPENCODE, 0.40, 2.00, supports_images=True),
    ModelSpec("minimax-m2.7",      "MiniMax M2.7",                          PROVIDER_OPENCODE, 0.30, 1.20),
    ModelSpec("minimax-m2.5",      "MiniMax M2.5",                          PROVIDER_OPENCODE, 0.30, 1.20),
    ModelSpec("qwen3.6-plus",      "Qwen3.6 Plus",                          PROVIDER_OPENCODE, 0.50, 3.00),
    ModelSpec("qwen3.5-plus",      "Qwen3.5 Plus",                          PROVIDER_OPENCODE, 0.20, 1.20),

    # ── Harness Agent (free OpenCode Zen — no API key, /model only) ─────────
    # NOTE: hy3-free / x-preview-f-free were retired by the gateway (401 "Model
    # not supported"). Kept in sync with the free ids the live /zen/v1/models
    # endpoint returns (run: opencode-free-api.sh models).
    ModelSpec("nemotron-3-ultra-free",           "Nemotron 3 Ultra Free — default",  PROVIDER_HARNESS_AGENT, default=True),
    ModelSpec("mimo-v2.5-free",                  "MiMo V2.5 Free",                   PROVIDER_HARNESS_AGENT),
    ModelSpec("big-pickle",                      "Big Pickle",                       PROVIDER_HARNESS_AGENT),
    ModelSpec("union-alpha",                     "Union Alpha Free",                 PROVIDER_HARNESS_AGENT),
    ModelSpec("nemotron-3.5-lightning-free",     "Nemotron 3.5 Lightning Free",      PROVIDER_HARNESS_AGENT),
    ModelSpec("muse-spark-1.2-contributor-free", "Muse Spark 1.2 Free",              PROVIDER_HARNESS_AGENT),
    ModelSpec("muse-spark-1.3-contributor-free", "Muse Spark 1.3 Free",              PROVIDER_HARNESS_AGENT),
    ModelSpec("ling-3.0-flash-fin-free",         "Ling 3.0 Flash Fin Free",          PROVIDER_HARNESS_AGENT),
    ModelSpec("deepseek-v4-flash-free",          "DeepSeek V4 Flash Free",           PROVIDER_HARNESS_AGENT),

    # Paid OpenCode Zen picker reuses these slugs; exclusive free IDs have expired.

    # ── OpenAI Codex (ChatGPT subscription / OAuth) ───────────────────────────
    ModelSpec("gpt-5.5",      "GPT-5.5 — Codex recommended", PROVIDER_OPENAI_CODEX, default=True, supports_images=True),
    ModelSpec("gpt-5.4",      "GPT-5.4 — Codex fallback",    PROVIDER_OPENAI_CODEX, supports_images=True),
    ModelSpec("gpt-5.4-mini", "GPT-5.4 Mini — faster Codex", PROVIDER_OPENAI_CODEX, supports_images=True),

    # ── Kimchi (llm.kimchi.dev — OpenAI-compatible, BYO API key) ───────────────
    ModelSpec("glm-5.2-fp8",       "GLM-5.2 FP8 — latest GLM model",       PROVIDER_KIMCHI, supports_images=True),
    ModelSpec("kimi-k2.6",         "Kimi K2.6 — reasoning, most capable",  PROVIDER_KIMCHI, default=True, supports_images=True),
    ModelSpec("kimi-k2.7",         "Kimi K2.7 — reasoning, most capable",  PROVIDER_KIMCHI, default=True, supports_images=True),
    ModelSpec("minimax-m2.7",      "MiniMax M2.7",                          PROVIDER_KIMCHI),
    ModelSpec("minimax-m3",        "MiniMax M3",                            PROVIDER_KIMCHI),
    ModelSpec("nemotron-3-ultra-fp4", "Nemotron 3 Ultra FP4",               PROVIDER_KIMCHI),
    ModelSpec("nemotron-3-super-fp4", "Nemotron 3 Super FP4",               PROVIDER_KIMCHI),
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


def model_supports_images(model_id: str) -> bool:
    """Return True if the given model ID can natively process image inputs."""
    return model_id in IMAGE_SUPPORTING_MODELS


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

# ── Auto-generated model lists from MODEL_INFO ─────────────────────────────────
ANTHROPIC_MODELS = [
    (mid, info[0])
    for mid, info in MODEL_INFO.items()
    if info[1] == PROVIDER_ANTHROPIC
]

# OAuth / Pro-Max subscription catalog (newest first). Live API ids are merged in
# at runtime when OAuth connects successfully.
ANTHROPIC_AUTH_MODEL_IDS = (
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
)
OPENROUTER_FREE_MODELS = [
    (mid, info[0])
    for mid, info in MODEL_INFO.items()
    if info[1] == PROVIDER_OPENROUTER
]
_OPENROUTER_SEED_MODELS: tuple[tuple[str, str], ...] = tuple(OPENROUTER_FREE_MODELS)


def openrouter_models_for_picker(live: bool = False) -> list[tuple[str, str]]:
    """OpenRouter rows: every free model it serves right now, then the paid seeds.

    The free tier is read from the public catalog (no API key needed) so the
    user gets all of it without a code change — see
    :mod:`jarvis.auth.openrouter_catalog`. ``live=False`` reads the on-disk
    cache only and never blocks; ``live=True`` refreshes over the network.
    """
    try:
        from ..auth.openrouter_catalog import free_models

        discovered = free_models(live=live)
    except Exception:
        discovered = []

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in discovered:
        register_dynamic_model(
            m.id, m.label, PROVIDER_OPENROUTER, supports_images=m.supports_images
        )
        if m.id not in seen:
            seen.add(m.id)
            out.append((m.id, m.label))
    for mid, desc in _OPENROUTER_SEED_MODELS:
        if mid not in seen:
            seen.add(mid)
            out.append((mid, desc))
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
    return OPENROUTER_DEFAULT_MODEL
OPENCODE_MODELS = [
    (mid, info[0])
    for mid, info in MODEL_INFO.items()
    if info[1] == PROVIDER_OPENCODE
]
HARNESS_AGENT_MODELS = [
    (mid, info[0])
    for mid, info in MODEL_INFO.items()
    if info[1] == PROVIDER_HARNESS_AGENT
]
HARNESS_AGENT_MODEL_IDS = frozenset(m for m, _ in HARNESS_AGENT_MODELS)
KIMCHI_MODELS = [
    (mid, info[0])
    for mid, info in MODEL_INFO.items()
    if info[1] == PROVIDER_KIMCHI
]
KIMCHI_MODEL_IDS = frozenset(m for m, _ in KIMCHI_MODELS)

# Static fallback so /model always lists Harness Agent even on partial/cached
# installs. Derived from MODELS — no separate copy to keep in sync.
_HARNESS_AGENT_MODEL_FALLBACK: tuple[tuple[str, str], ...] = tuple(HARNESS_AGENT_MODELS)


def harness_agent_models_for_picker(
    live: bool = False, cached: bool = False
) -> list[tuple[str, str]]:
    """Harness Agent models — always shown in /model (no credentials required).

    With ``live=True`` the list is refreshed from the public OpenCode catalog
    (see :mod:`jarvis.auth.zen_catalog`) so newly added free models appear — and
    retired ones disappear — without a code change. Falls back to the static
    list when the network is unavailable.

    With ``cached=True`` the same discovery is read from the on-disk cache
    instead — instant, no network — and unioned with the static list so the
    picker is never thinner than the built-in set. This is what UI threads use.
    """
    order = [m for m, _ in _HARNESS_AGENT_MODEL_FALLBACK]
    merged: dict[str, str] = {m: d for m, d in _HARNESS_AGENT_MODEL_FALLBACK}
    for mid, desc in HARNESS_AGENT_MODELS:
        if mid not in merged:
            order.append(mid)
        merged[mid] = desc
    static = [(m, merged[m]) for m in order]

    if not live:
        if not cached:
            return static
        try:
            from ..auth.zen_catalog import cached_free_models

            discovered = cached_free_models()
        except Exception:
            discovered = []
        if not discovered:
            return static
        labels = dict(static)
        labels.update(discovered)
        ids = [m for m, _ in discovered]
        ids += [m for m, _ in static if m not in set(ids)]
        if HARNESS_AGENT_DEFAULT_MODEL in labels:
            ids = [HARNESS_AGENT_DEFAULT_MODEL] + [
                m for m in ids if m != HARNESS_AGENT_DEFAULT_MODEL
            ]
        return [(m, labels[m]) for m in ids]

    try:
        from ..auth.zen_catalog import refresh_free_models

        dynamic = refresh_free_models()
    except Exception:
        dynamic = None
    if not dynamic:
        return static

    labels = dict(dynamic)
    ordered = [m for m, _ in dynamic if m != HARNESS_AGENT_DEFAULT_MODEL]
    if HARNESS_AGENT_DEFAULT_MODEL in labels:
        ordered.insert(0, HARNESS_AGENT_DEFAULT_MODEL)
    return [(m, labels[m]) for m in ordered]


def opencode_zen_models_for_picker() -> list[tuple[str, str]]:
    """OpenCode Zen picker list: zen-exclusive models + shared Harness Agent slugs."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for mid, info in MODEL_INFO.items():
        if info[1] != PROVIDER_OPENCODE_ZEN:
            continue
        if mid not in seen:
            seen.add(mid)
            out.append((mid, info[0]))
    for mid, desc in HARNESS_AGENT_MODELS:
        if mid not in seen:
            seen.add(mid)
            out.append((mid, desc))
    return out


OPENCODE_ZEN_MODELS = opencode_zen_models_for_picker()
OPENCODE_ZEN_MODEL_IDS = frozenset(m for m, _ in OPENCODE_ZEN_MODELS)
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

OPENROUTER_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER[PROVIDER_OPENROUTER]
OPENCODE_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER[PROVIDER_OPENCODE]
HARNESS_AGENT_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER[PROVIDER_HARNESS_AGENT]
OPENCODE_ZEN_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER.get(PROVIDER_OPENCODE_ZEN, HARNESS_AGENT_DEFAULT_MODEL)
CODEX_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER[PROVIDER_OPENAI_CODEX]
ANTHROPIC_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER[PROVIDER_ANTHROPIC]
KIMCHI_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER[PROVIDER_KIMCHI]

_PROVIDER_DEFAULT_MODEL = {
    PROVIDER_ANTHROPIC: ANTHROPIC_DEFAULT_MODEL,
    PROVIDER_OPENROUTER: OPENROUTER_DEFAULT_MODEL,
    PROVIDER_OPENCODE: OPENCODE_DEFAULT_MODEL,
    PROVIDER_OPENCODE_ZEN: OPENCODE_ZEN_DEFAULT_MODEL,
    PROVIDER_OPENAI_CODEX: CODEX_DEFAULT_MODEL,
    PROVIDER_KIMCHI: KIMCHI_DEFAULT_MODEL,
}

OPENROUTER_BASE_URL = "https://openrouter.ai/api"

OPENCODE_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
KIMCHI_BASE_URL = "https://llm.kimchi.dev/openai/v1"
# Kimchi rejects requests without this header (returns misleading 402 "exhausted credits").
KIMCHI_USER_AGENT = "kimchi/0.1.20"


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
    """True when ``model`` is a free Harness Agent (OpenCode Zen public) model."""
    m = (model or "").strip()
    if m in HARNESS_AGENT_MODEL_IDS:
        return True
    return m in {mid for mid, _ in _HARNESS_AGENT_MODEL_FALLBACK}


def connected_model_sources() -> list[str]:
    """Model-picker sources. Harness Agent is always first and always included."""
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
    if os.getenv("KIMCHI_API_KEY"):
        sources.append(PROVIDER_KIMCHI)
    else:
        from .paths import KIMCHI_KEY_FILE
        try:
            if KIMCHI_KEY_FILE.exists() and KIMCHI_KEY_FILE.read_text().strip():
                sources.append(PROVIDER_KIMCHI)
        except OSError:
            pass
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
        rows = [
            (PROVIDER_HARNESS_AGENT, mid, desc)
            for mid, desc in _HARNESS_AGENT_MODEL_FALLBACK
        ]
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
        return list(ANTHROPIC_MODELS)
    if source == PROVIDER_ANTHROPIC_AUTH:
        from ..auth.anthropic_models import anthropic_auth_models_for_picker
        return anthropic_auth_models_for_picker()
    if source == PROVIDER_OPENAI_CODEX_AUTH:
        return list(CODEX_MODELS)
    if source == PROVIDER_KIMCHI:
        return list(KIMCHI_MODELS)
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
    if os.getenv("KIMCHI_API_KEY"):
        connected.add(PROVIDER_KIMCHI)

    # ── Key files on disk ──────────────────────────────────────────────────
    # Lazy import to avoid circular dependency (paths → no providers imports)
    from .paths import (
        KEY_FILE, OPENROUTER_KEY_FILE,
        OPENCODE_KEY_FILE, OPENCODE_ZEN_KEY_FILE,
        KIMCHI_KEY_FILE,
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
    if _has_content(KIMCHI_KEY_FILE):
        connected.add(PROVIDER_KIMCHI)

    # First run — no keys at all → show everything so user can see options
    if not connected:
        return set(PROVIDERS)
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
        return OPENCODE_MODELS
    if provider == PROVIDER_OPENCODE_ZEN:
        return opencode_zen_models_for_picker()
    if provider == PROVIDER_OPENAI_CODEX:
        return CODEX_MODELS
    if provider == PROVIDER_KIMCHI:
        return KIMCHI_MODELS
    return list(ANTHROPIC_MODELS)


def model_belongs_to_provider(model: str, provider: str) -> bool:
    """Return True when ``model`` can be sent on ``provider``."""
    m = (model or "").strip()
    if not m:
        return False
    if provider == PROVIDER_OPENCODE_ZEN and is_harness_agent_model(m):
        return True
    info = MODEL_INFO.get(m)
    if info:
        return info[1] == provider
    if provider == PROVIDER_OPENROUTER:
        return "/" in m
    if provider == PROVIDER_ANTHROPIC:
        return m.startswith("claude-")
    if provider == PROVIDER_KIMCHI:
        return m in KIMCHI_MODEL_IDS
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
    if model_belongs_to_provider(model, provider):
        return model.strip()
    if provider == PROVIDER_OPENCODE_ZEN:
        return OPENCODE_ZEN_DEFAULT_MODEL
    if provider == PROVIDER_OPENROUTER:
        return openrouter_default_model()
    return _PROVIDER_DEFAULT_MODEL.get(provider, ANTHROPIC_DEFAULT_MODEL)


def refresh_model_catalogs(retry_blocked: bool = False) -> bool:
    """Refresh every live model catalog into the on-disk cache.

    Safe to call from a background thread — it only performs public, unauthenticated
    GETs. Returns True when at least one catalog came back, so a caller that is
    showing cached rows knows whether re-rendering is worthwhile.

    ``retry_blocked=True`` (an explicit ``/model refresh``) also clears the
    record of models that previously refused this account.
    """
    ok = False
    try:
        from ..auth.zen_catalog import refresh_free_models as _zen_refresh

        ok = bool(_zen_refresh()) or ok
    except Exception:
        pass
    try:
        from ..auth.openrouter_catalog import refresh_free_models as _or_refresh

        ok = bool(_or_refresh(retry_blocked=retry_blocked)) or ok
    except Exception:
        pass
    return ok


def model_catalogs_are_fresh() -> bool:
    """True when every live catalog cache is within its TTL (no refresh needed)."""
    try:
        from ..auth.zen_catalog import cache_is_fresh as _zen_fresh
        from ..auth.openrouter_catalog import cache_is_fresh as _or_fresh

        return _zen_fresh() and _or_fresh()
    except Exception:
        return False
