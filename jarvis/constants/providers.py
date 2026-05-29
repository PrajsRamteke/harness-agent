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
    """
    id: str
    label: str
    provider: str
    input_price: float = 0.0
    output_price: float = 0.0
    default: bool = False


MODELS: list[ModelSpec] = [
    # ── Anthropic (direct API) ────────────────────────────────────────────────
    ModelSpec("claude-haiku-4-5",  "Haiku 4.5 — fastest, cheapest",  PROVIDER_ANTHROPIC, 1.0,  5.0),
    ModelSpec("claude-sonnet-4-6", "Sonnet 4.6 — balanced",          PROVIDER_ANTHROPIC, 3.0, 15.0, default=True),
    ModelSpec("claude-opus-4-6",   "Opus 4.6 — high capability",     PROVIDER_ANTHROPIC, 5.0, 25.0),
    ModelSpec("claude-opus-4-7",   "Opus 4.7 — high capability",     PROVIDER_ANTHROPIC, 5.0, 25.0),
    ModelSpec("claude-opus-4-8",   "Opus 4.8 — most capable",        PROVIDER_ANTHROPIC, 5.0, 25.0),

    # ── OpenRouter free-tier (all :free suffix → $0) ─────────────────────────
    ModelSpec("openai/gpt-oss-120b:free",               "GPT-OSS 120B — default",            PROVIDER_OPENROUTER, default=True),
    ModelSpec("minimax/minimax-m2.5:free",              "MiniMax M2.5 (may be unavailable)", PROVIDER_OPENROUTER),
    ModelSpec("qwen/qwen3-coder:free",                  "Qwen3 Coder 480B — best for code",  PROVIDER_OPENROUTER),
    ModelSpec("openai/gpt-oss-20b:free",                "GPT-OSS 20B — smaller, faster",     PROVIDER_OPENROUTER),
    ModelSpec("meta-llama/llama-3.3-70b-instruct:free", "Llama 3.3 70B Instruct",            PROVIDER_OPENROUTER),
    ModelSpec("qwen/qwen3-next-80b-a3b-instruct:free",  "Qwen3 Next 80B A3B Instruct",       PROVIDER_OPENROUTER),
    ModelSpec("nvidia/nemotron-3-super-120b-a12b:free", "Nemotron 3 Super 120B",             PROVIDER_OPENROUTER),
    ModelSpec("z-ai/glm-4.5-air:free",                  "GLM 4.5 Air",                       PROVIDER_OPENROUTER),
    ModelSpec("google/gemma-3-27b-it:free",             "Gemma 3 27B Instruct",              PROVIDER_OPENROUTER),
    ModelSpec("nousresearch/hermes-3-llama-3.1-405b:free", "Hermes 3 Llama 405B",            PROVIDER_OPENROUTER),
    ModelSpec("openrouter/owl-alpha",                   "Owl Alpha",                         PROVIDER_OPENROUTER),

    # ── OpenCode Go models (real pricing, help.apiyi) ──────────────────────────
    ModelSpec("glm-5.1",           "GLM-5.1 — latest GLM model",            PROVIDER_OPENCODE, 1.40, 4.40),
    ModelSpec("glm-5",             "GLM-5 — high capability",               PROVIDER_OPENCODE, 1.00, 3.20),
    ModelSpec("kimi-k2.6",         "Kimi K2.6 — Moonshot AI, most capable", PROVIDER_OPENCODE, 0.32, 1.34, default=True),
    ModelSpec("kimi-k2.5",         "Kimi K2.5 — Moonshot AI",               PROVIDER_OPENCODE, 0.60, 3.00),
    ModelSpec("deepseek-v4-pro",   "DeepSeek V4 Pro — strong reasoning",    PROVIDER_OPENCODE, 1.74, 3.48),
    ModelSpec("deepseek-v4-flash", "DeepSeek V4 Flash — fast & cheap",      PROVIDER_OPENCODE, 0.14, 0.28),
    ModelSpec("mimo-v2.5-pro",     "MiMo V2.5 Pro",                         PROVIDER_OPENCODE, 1.00, 3.00),
    ModelSpec("mimo-v2.5",         "MiMo V2.5",                             PROVIDER_OPENCODE, 0.40, 2.00),
    ModelSpec("mimo-v2-pro",       "MiMo V2 Pro",                           PROVIDER_OPENCODE, 1.00, 3.00),
    ModelSpec("mimo-v2-omni",      "MiMo V2 Omni",                          PROVIDER_OPENCODE, 0.40, 2.00),
    ModelSpec("minimax-m2.7",      "MiniMax M2.7",                          PROVIDER_OPENCODE, 0.30, 1.20),
    ModelSpec("minimax-m2.5",      "MiniMax M2.5",                          PROVIDER_OPENCODE, 0.30, 1.20),
    ModelSpec("qwen3.6-plus",      "Qwen3.6 Plus",                          PROVIDER_OPENCODE, 0.50, 3.00),
    ModelSpec("qwen3.5-plus",      "Qwen3.5 Plus",                          PROVIDER_OPENCODE, 0.20, 1.20),

    # ── Harness Agent (free OpenCode Zen — no API key, /model only) ─────────
    ModelSpec("deepseek-v4-flash-free", "DeepSeek V4 Flash Free — default", PROVIDER_HARNESS_AGENT, default=True),
    ModelSpec("nemotron-3-super-free",  "Nemotron 3 Super Free",            PROVIDER_HARNESS_AGENT),
    ModelSpec("mimo-v2.5-free",         "MiMo V2.5 Free — Xiaomi",          PROVIDER_HARNESS_AGENT),
    ModelSpec("big-pickle",             "Big Pickle",                       PROVIDER_HARNESS_AGENT),

    # ── OpenCode Zen models (API key via /provider opencode_zen) ──────────────
    ModelSpec("minimax-m2.5-free", "MiniMax M2.5 Free — default", PROVIDER_OPENCODE_ZEN, default=True),
    ModelSpec("hy3-preview-free",  "HY3 Preview Free",            PROVIDER_OPENCODE_ZEN),
    ModelSpec("ring-2.6-1t-free",  "Ring 2.6 1T Free",            PROVIDER_OPENCODE_ZEN),

    # ── OpenAI Codex (ChatGPT subscription / OAuth) ───────────────────────────
    ModelSpec("gpt-5.5",      "GPT-5.5 — Codex recommended", PROVIDER_OPENAI_CODEX, default=True),
    ModelSpec("gpt-5.4",      "GPT-5.4 — Codex fallback",    PROVIDER_OPENAI_CODEX),
    ModelSpec("gpt-5.4-mini", "GPT-5.4 Mini — faster Codex", PROVIDER_OPENAI_CODEX),
]

# model_id -> (description, provider, (input_price_per_1M, output_price_per_1M))
# Derived from MODELS; kept for back-compat with code that reads MODEL_INFO directly.
MODEL_INFO: dict[str, tuple[str, str, tuple[float, float]]] = {
    m.id: (m.label, m.provider, (m.input_price, m.output_price))
    for m in MODELS
}

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

# Static fallback so /model always lists Harness Agent even on partial/cached
# installs. Derived from MODELS — no separate copy to keep in sync.
_HARNESS_AGENT_MODEL_FALLBACK: tuple[tuple[str, str], ...] = tuple(HARNESS_AGENT_MODELS)


def harness_agent_models_for_picker() -> list[tuple[str, str]]:
    """Harness Agent models — always shown in /model (no credentials required)."""
    order = [m for m, _ in _HARNESS_AGENT_MODEL_FALLBACK]
    merged: dict[str, str] = {m: d for m, d in _HARNESS_AGENT_MODEL_FALLBACK}
    for mid, desc in HARNESS_AGENT_MODELS:
        if mid not in merged:
            order.append(mid)
        merged[mid] = desc
    return [(m, merged[m]) for m in order]


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
OPENCODE_ZEN_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER[PROVIDER_OPENCODE_ZEN]
HARNESS_AGENT_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER[PROVIDER_HARNESS_AGENT]
CODEX_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER[PROVIDER_OPENAI_CODEX]
ANTHROPIC_DEFAULT_MODEL = _DEFAULT_BY_PROVIDER[PROVIDER_ANTHROPIC]

_PROVIDER_DEFAULT_MODEL = {
    PROVIDER_ANTHROPIC: ANTHROPIC_DEFAULT_MODEL,
    PROVIDER_OPENROUTER: OPENROUTER_DEFAULT_MODEL,
    PROVIDER_OPENCODE: OPENCODE_DEFAULT_MODEL,
    PROVIDER_OPENCODE_ZEN: OPENCODE_ZEN_DEFAULT_MODEL,
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


def all_model_picker_rows() -> list[tuple[str, str, str]]:
    """All /model rows as (source, model_id, description). Harness Agent always first."""
    rows: list[tuple[str, str, str]] = [
        (PROVIDER_HARNESS_AGENT, mid, desc)
        for mid, desc in harness_agent_models_for_picker()
    ]
    try:
        for src in connected_model_sources():
            if src == PROVIDER_HARNESS_AGENT:
                continue
            rows.extend((src, mid, desc) for mid, desc in models_for_source(src))
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


def models_for_source(source: str):
    if source == PROVIDER_HARNESS_AGENT:
        return harness_agent_models_for_picker()
    if source == PROVIDER_ANTHROPIC_API:
        return list(ANTHROPIC_MODELS)
    if source == PROVIDER_ANTHROPIC_AUTH:
        from ..auth.anthropic_models import anthropic_auth_models_for_picker
        return anthropic_auth_models_for_picker()
    if source == PROVIDER_OPENAI_CODEX_AUTH:
        return list(CODEX_MODELS)
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
        return OPENROUTER_FREE_MODELS
    if provider == PROVIDER_OPENCODE:
        return OPENCODE_MODELS
    if provider == PROVIDER_OPENCODE_ZEN:
        return opencode_zen_models_for_picker()
    if provider == PROVIDER_OPENAI_CODEX:
        return CODEX_MODELS
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
    return _PROVIDER_DEFAULT_MODEL.get(provider, ANTHROPIC_DEFAULT_MODEL)
