"""Provider registry: Anthropic, OpenRouter, OpenCode Go, and OpenCode Zen.

SINGLE source of truth for all model definitions. Each model is defined once in
MODEL_INFO with its description and pricing. The model lists (ANTHROPIC_MODELS,
OPENCODE_MODELS, etc.) are auto-generated from MODEL_INFO so you never need to
update more than one dict when adding or changing a model.
"""

import os

# ── Provider identifiers ──────────────────────────────────────────────────────
PROVIDERS = ("anthropic", "openrouter", "opencode", "opencode_zen", "openai_codex", "pollinations")
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_OPENCODE = "opencode"
PROVIDER_OPENCODE_ZEN = "opencode_zen"
PROVIDER_OPENAI_CODEX = "openai_codex"
PROVIDER_POLLINATIONS = "pollinations"
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
    PROVIDER_POLLINATIONS: "Pollinations",
}

MODEL_SOURCE_LABELS = {
    PROVIDER_HARNESS_AGENT: "Harness Agent",
    PROVIDER_POLLINATIONS: "Pollinations",
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
    PROVIDER_POLLINATIONS,
    PROVIDER_ANTHROPIC_API,
    PROVIDER_ANTHROPIC_AUTH,
    PROVIDER_OPENROUTER,
    PROVIDER_OPENCODE,
    PROVIDER_OPENCODE_ZEN,
    PROVIDER_OPENAI_CODEX_AUTH,
)

# ── SINGLE SOURCE OF TRUTH: all models + descriptions + pricing ───────────────
# Each entry: model_id -> (description, provider, (input_price_per_1M, output_price_per_1M))
# Provider must be one of PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, etc.
# Prices are in USD per 1M tokens, used only for /cost estimates.
# Add a new model HERE, and everything else (picker lists, pricing lookups) updates automatically.
MODEL_INFO: dict[str, tuple[str, str, tuple[float, float]]] = {
    # ── Anthropic (direct API) ────────────────────────────────────────────────
    "claude-haiku-4-5":  ("Haiku 4.5 — fastest, cheapest",     PROVIDER_ANTHROPIC, (1.0,   5.0)),
    "claude-sonnet-4-6": ("Sonnet 4.6 — balanced",             PROVIDER_ANTHROPIC, (3.0,  15.0)),
    "claude-opus-4-6":   ("Opus 4.6 — high capability",        PROVIDER_ANTHROPIC, (5.0, 25.0)),
    "claude-opus-4-7":   ("Opus 4.7 — most capable",           PROVIDER_ANTHROPIC, (5.0, 25.0)),

    # ── OpenRouter free-tier (all :free suffix → $0) ─────────────────────────
    "openai/gpt-oss-120b:free":               ("GPT-OSS 120B — default",            PROVIDER_OPENROUTER, (0.0, 0.0)),
    "minimax/minimax-m2.5:free":              ("MiniMax M2.5 (may be unavailable)", PROVIDER_OPENROUTER, (0.0, 0.0)),
    "qwen/qwen3-coder:free":                  ("Qwen3 Coder 480B — best for code",  PROVIDER_OPENROUTER, (0.0, 0.0)),
    "openai/gpt-oss-20b:free":                ("GPT-OSS 20B — smaller, faster",     PROVIDER_OPENROUTER, (0.0, 0.0)),
    "meta-llama/llama-3.3-70b-instruct:free": ("Llama 3.3 70B Instruct",            PROVIDER_OPENROUTER, (0.0, 0.0)),
    "qwen/qwen3-next-80b-a3b-instruct:free":  ("Qwen3 Next 80B A3B Instruct",       PROVIDER_OPENROUTER, (0.0, 0.0)),
    "nvidia/nemotron-3-super-120b-a12b:free": ("Nemotron 3 Super 120B",             PROVIDER_OPENROUTER, (0.0, 0.0)),
    "z-ai/glm-4.5-air:free":                  ("GLM 4.5 Air",                       PROVIDER_OPENROUTER, (0.0, 0.0)),
    "google/gemma-3-27b-it:free":             ("Gemma 3 27B Instruct",              PROVIDER_OPENROUTER, (0.0, 0.0)),
    "nousresearch/hermes-3-llama-3.1-405b:free": ("Hermes 3 Llama 405B",            PROVIDER_OPENROUTER, (0.0, 0.0)),
    "openrouter/owl-alpha":                   ("Owl Alpha",                         PROVIDER_OPENROUTER, (0.0, 0.0)),

    # ── OpenCode Go models (real pricing, help.apiyi) ──────────────────────────
    "glm-5.1":          ("GLM-5.1 — latest GLM model",                PROVIDER_OPENCODE, (1.40,   4.40)),
    "glm-5":            ("GLM-5 — high capability",                   PROVIDER_OPENCODE, (1.00,   3.20)),
    "kimi-k2.6":        ("Kimi K2.6 — Moonshot AI, most capable",     PROVIDER_OPENCODE, (0.32,   1.34)),
    "kimi-k2.5":        ("Kimi K2.5 — Moonshot AI",                   PROVIDER_OPENCODE, (0.60,   3.00)),
    "deepseek-v4-pro":  ("DeepSeek V4 Pro — strong reasoning",        PROVIDER_OPENCODE, (1.74,   3.48)),
    "deepseek-v4-flash":("DeepSeek V4 Flash — fast & cheap",          PROVIDER_OPENCODE, (0.14,   0.28)),
    "mimo-v2.5-pro":    ("MiMo V2.5 Pro",                              PROVIDER_OPENCODE, (1.00,   3.00)),
    "mimo-v2.5":        ("MiMo V2.5",                                  PROVIDER_OPENCODE, (0.40,   2.00)),
    "mimo-v2-pro":      ("MiMo V2 Pro",                                PROVIDER_OPENCODE, (1.00,   3.00)),
    "mimo-v2-omni":     ("MiMo V2 Omni",                               PROVIDER_OPENCODE, (0.40,   2.00)),
    "minimax-m2.7":     ("MiniMax M2.7",                               PROVIDER_OPENCODE, (0.30,   1.20)),
    "minimax-m2.5":     ("MiniMax M2.5",                               PROVIDER_OPENCODE, (0.30,   1.20)),
    "qwen3.6-plus":     ("Qwen3.6 Plus",                               PROVIDER_OPENCODE, (0.50,   3.00)),
    "qwen3.5-plus":     ("Qwen3.5 Plus",                               PROVIDER_OPENCODE, (0.20,   1.20)),

    # ── Harness Agent (free OpenCode Zen — no API key, /model only) ─────────
    "deepseek-v4-flash-free": ("DeepSeek V4 Flash Free — default", PROVIDER_HARNESS_AGENT, (0.0, 0.0)),
    "nemotron-3-super-free":  ("Nemotron 3 Super Free",            PROVIDER_HARNESS_AGENT, (0.0, 0.0)),
    "big-pickle":             ("Big Pickle",                       PROVIDER_HARNESS_AGENT, (0.0, 0.0)),

    # ── Pollinations (free, no API key — text.pollinations.ai) ───────────────
    "openai-fast":    ("GPT-OSS 20B — fast, free default",  PROVIDER_POLLINATIONS, (0.0, 0.0)),
    "openai":         ("GPT-5 mini",                        PROVIDER_POLLINATIONS, (0.0, 0.0)),
    "openai-large":   ("GPT-5.2 — most capable",            PROVIDER_POLLINATIONS, (0.0, 0.0)),
    "qwen-coder":     ("Qwen3 Coder 30B — best for code",   PROVIDER_POLLINATIONS, (0.0, 0.0)),
    "mistral":        ("Mistral Small 3.2 24B",             PROVIDER_POLLINATIONS, (0.0, 0.0)),
    "deepseek":       ("DeepSeek V3.2",                     PROVIDER_POLLINATIONS, (0.0, 0.0)),
    "grok":           ("Grok 4 Fast",                       PROVIDER_POLLINATIONS, (0.0, 0.0)),
    "claude-fast":    ("Claude Haiku 4.5",                  PROVIDER_POLLINATIONS, (0.0, 0.0)),
    "claude":         ("Claude Sonnet 4.6",                 PROVIDER_POLLINATIONS, (0.0, 0.0)),
    "claude-large":   ("Claude Opus 4.6",                   PROVIDER_POLLINATIONS, (0.0, 0.0)),
    "gemini":         ("Gemini 3 Flash",                    PROVIDER_POLLINATIONS, (0.0, 0.0)),

    # ── OpenCode Zen models (API key via /provider opencode_zen) ──────────────
    "minimax-m2.5-free":      ("MiniMax M2.5 Free — default",   PROVIDER_OPENCODE_ZEN, (0.0, 0.0)),
    "hy3-preview-free":       ("HY3 Preview Free",              PROVIDER_OPENCODE_ZEN, (0.0, 0.0)),
    "ring-2.6-1t-free":       ("Ring 2.6 1T Free",              PROVIDER_OPENCODE_ZEN, (0.0, 0.0)),

    "gpt-5.5":                ("GPT-5.5 — Codex recommended",  PROVIDER_OPENAI_CODEX, (0.0, 0.0)),
    "gpt-5.4":                ("GPT-5.4 — Codex fallback",       PROVIDER_OPENAI_CODEX, (0.0, 0.0)),
    "gpt-5.4-mini":           ("GPT-5.4 Mini — faster Codex",    PROVIDER_OPENAI_CODEX, (0.0, 0.0)),
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
POLLINATIONS_MODELS = [
    (mid, info[0])
    for mid, info in MODEL_INFO.items()
    if info[1] == PROVIDER_POLLINATIONS
]
HARNESS_AGENT_MODEL_IDS = frozenset(m for m, _ in HARNESS_AGENT_MODELS)
POLLINATIONS_MODEL_IDS = frozenset(m for m, _ in POLLINATIONS_MODELS)

# Static fallback so /model always lists Harness Agent even on partial/cached installs.
_HARNESS_AGENT_MODEL_FALLBACK: tuple[tuple[str, str], ...] = (
    ("deepseek-v4-flash-free", "DeepSeek V4 Flash Free — default"),
    ("nemotron-3-super-free", "Nemotron 3 Super Free"),
    ("big-pickle", "Big Pickle"),
)


def pollinations_models_for_picker() -> list[tuple[str, str]]:
    """Pollinations models — always shown in /model (no credentials required)."""
    return list(POLLINATIONS_MODELS)


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

# ── Default models per provider ───────────────────────────────────────────────
OPENROUTER_DEFAULT_MODEL = "openai/gpt-oss-120b:free"
OPENCODE_DEFAULT_MODEL = "kimi-k2.6"
OPENCODE_ZEN_DEFAULT_MODEL = "minimax-m2.5-free"
HARNESS_AGENT_DEFAULT_MODEL = "deepseek-v4-flash-free"
POLLINATIONS_DEFAULT_MODEL = "openai-fast"
CODEX_DEFAULT_MODEL = "gpt-5.5"
ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-4-6"

_PROVIDER_DEFAULT_MODEL = {
    PROVIDER_ANTHROPIC: ANTHROPIC_DEFAULT_MODEL,
    PROVIDER_OPENROUTER: OPENROUTER_DEFAULT_MODEL,
    PROVIDER_OPENCODE: OPENCODE_DEFAULT_MODEL,
    PROVIDER_OPENCODE_ZEN: OPENCODE_ZEN_DEFAULT_MODEL,
    PROVIDER_OPENAI_CODEX: CODEX_DEFAULT_MODEL,
    PROVIDER_POLLINATIONS: POLLINATIONS_DEFAULT_MODEL,
}

OPENROUTER_BASE_URL = "https://openrouter.ai/api"

OPENCODE_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"
POLLINATIONS_BASE_URL = "https://text.pollinations.ai/openai/v1"
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
    """Model-picker sources. Harness Agent and Pollinations are always first."""
    sources: list[str] = [PROVIDER_HARNESS_AGENT, PROVIDER_POLLINATIONS]
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
    # Free tiers first, then dedupe the rest.
    out: list[str] = [PROVIDER_HARNESS_AGENT, PROVIDER_POLLINATIONS]
    seen = set(out)
    for src in sources:
        if src in seen:
            continue
        seen.add(src)
        out.append(src)
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
    if source == PROVIDER_POLLINATIONS:
        return pollinations_models_for_picker()
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
    connected: set[str] = {PROVIDER_POLLINATIONS}

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


def models_for(provider: str):
    if provider == PROVIDER_POLLINATIONS:
        return POLLINATIONS_MODELS
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


def normalize_model_for_provider(model: str, provider: str) -> str:
    """Use ``model`` when valid for ``provider``; otherwise the provider default."""
    if model_belongs_to_provider(model, provider):
        return model.strip()
    if provider == PROVIDER_OPENCODE_ZEN:
        return OPENCODE_ZEN_DEFAULT_MODEL
    return _PROVIDER_DEFAULT_MODEL.get(provider, ANTHROPIC_DEFAULT_MODEL)
