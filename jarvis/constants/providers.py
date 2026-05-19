"""Provider registry: Anthropic, OpenRouter, OpenCode Go, and OpenCode Zen.

SINGLE source of truth for all model definitions. Each model is defined once in
MODEL_INFO with its description and pricing. The model lists (ANTHROPIC_MODELS,
OPENCODE_MODELS, etc.) are auto-generated from MODEL_INFO so you never need to
update more than one dict when adding or changing a model.
"""

import os

# ── Provider identifiers ──────────────────────────────────────────────────────
PROVIDERS = ("anthropic", "openrouter", "opencode", "opencode_zen")
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_OPENCODE = "opencode"
PROVIDER_OPENCODE_ZEN = "opencode_zen"

# Model-picker sources (Anthropic splits API key vs OAuth subscription).
PROVIDER_ANTHROPIC_API = "anthropic_api"
PROVIDER_ANTHROPIC_AUTH = "anthropic_auth"

# ── Auth mode identifiers ─────────────────────────────────────────────────────
AUTH_API_KEY = "api_key"
AUTH_OAUTH = "oauth"

PROVIDER_LABELS = {
    PROVIDER_ANTHROPIC: "Anthropic",
    PROVIDER_OPENROUTER: "OpenRouter",
    PROVIDER_OPENCODE: "OpenCode Go",
    PROVIDER_OPENCODE_ZEN: "OpenCode Zen",
}

MODEL_SOURCE_LABELS = {
    PROVIDER_ANTHROPIC_API: "Anthropic API",
    PROVIDER_ANTHROPIC_AUTH: "Anthropic Auth",
    PROVIDER_OPENROUTER: "OpenRouter",
    PROVIDER_OPENCODE: "OpenCode Go",
    PROVIDER_OPENCODE_ZEN: "OpenCode Zen",
}

# Picker display order (Anthropic API + Auth first when configured).
MODEL_SOURCES = (
    PROVIDER_ANTHROPIC_API,
    PROVIDER_ANTHROPIC_AUTH,
    PROVIDER_OPENROUTER,
    PROVIDER_OPENCODE,
    PROVIDER_OPENCODE_ZEN,
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
    "minimax/minimax-m2.5:free":              ("MiniMax M2.5 — default",            PROVIDER_OPENROUTER, (0.0, 0.0)),
    "qwen/qwen3-coder:free":                  ("Qwen3 Coder 480B — best for code",  PROVIDER_OPENROUTER, (0.0, 0.0)),
    "openai/gpt-oss-120b:free":               ("GPT-OSS 120B — OpenAI open weights", PROVIDER_OPENROUTER, (0.0, 0.0)),
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

    # ── OpenCode Zen models (free tier) ─────────────────────────────────────────
    "minimax-m2.5-free":      ("MiniMax M2.5 Free — default",   PROVIDER_OPENCODE_ZEN, (0.0, 0.0)),
    "hy3-preview-free":       ("HY3 Preview Free",              PROVIDER_OPENCODE_ZEN, (0.0, 0.0)),
    "nemotron-3-super-free":  ("Nemotron 3 Super Free",         PROVIDER_OPENCODE_ZEN, (0.0, 0.0)),
    # "ring-2.6-1t-free":       ("Ring 2.6 1T Free",              PROVIDER_OPENCODE_ZEN, (0.0, 0.0)),
    "deepseek-v4-flash-free": ("DeepSeek V4 Flash Free",       PROVIDER_OPENCODE_ZEN, (0.0, 0.0)),
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
OPENCODE_ZEN_MODELS = [
    (mid, info[0])
    for mid, info in MODEL_INFO.items()
    if info[1] == PROVIDER_OPENCODE_ZEN
]

# ── Pricing dict (auto-generated from MODEL_INFO) ─────────────────────────────
PRICING: dict[str, tuple[float, float]] = {
    mid: info[2]
    for mid, info in MODEL_INFO.items()
}

# ── Default models per provider ───────────────────────────────────────────────
OPENROUTER_DEFAULT_MODEL = "minimax/minimax-m2.5:free"
OPENCODE_DEFAULT_MODEL = "kimi-k2.6"
OPENCODE_ZEN_DEFAULT_MODEL = "minimax-m2.5-free"

OPENROUTER_BASE_URL = "https://openrouter.ai/api"

OPENCODE_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"


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


def connected_model_sources() -> list[str]:
    """Model-picker sources that have credentials configured."""
    sources: list[str] = []
    if _has_anthropic_api():
        sources.append(PROVIDER_ANTHROPIC_API)
    if _has_anthropic_oauth():
        sources.append(PROVIDER_ANTHROPIC_AUTH)
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
    if not sources:
        return list(MODEL_SOURCES)
    return sources


def model_option_id(source: str, model_id: str) -> str:
    return f"{source}::{model_id}"


def parse_model_option_id(option_id: str) -> tuple[str, str]:
    if "::" in option_id:
        source, model_id = option_id.split("::", 1)
        return source, model_id
    return "", option_id


def models_for_source(source: str):
    if source == PROVIDER_ANTHROPIC_API:
        return list(ANTHROPIC_MODELS)
    if source == PROVIDER_ANTHROPIC_AUTH:
        from ..auth.anthropic_models import anthropic_auth_models_for_picker
        return anthropic_auth_models_for_picker()
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
    if provider == PROVIDER_OPENROUTER:
        return OPENROUTER_FREE_MODELS
    if provider == PROVIDER_OPENCODE:
        return OPENCODE_MODELS
    if provider == PROVIDER_OPENCODE_ZEN:
        return OPENCODE_ZEN_MODELS
    return list(ANTHROPIC_MODELS)
