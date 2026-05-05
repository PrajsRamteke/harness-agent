"""Provider registry: Anthropic, OpenRouter, and OpenCode Go.

Each provider exposes a list of (model_id, description) tuples for the /model
picker. OpenRouter models are free-tier popular picks; users can also type any
OpenRouter slug manually at the prompt.
"""

# ── Provider identifiers ──────────────────────────────────────────────────────
PROVIDERS = ("anthropic", "openrouter", "opencode")
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_OPENCODE = "opencode"

# ── Auth mode identifiers ─────────────────────────────────────────────────────
AUTH_API_KEY = "api_key"
AUTH_OAUTH = "oauth"

PROVIDER_LABELS = {
    PROVIDER_ANTHROPIC: "Anthropic",
    PROVIDER_OPENROUTER: "OpenRouter",
    PROVIDER_OPENCODE: "OpenCode Go",
}

ANTHROPIC_MODELS = [
    ("claude-haiku-4-5", "Haiku 4.5 — fastest, cheapest"),
    ("claude-sonnet-4-6", "Sonnet 4.6 — balanced"),
    ("claude-opus-4-6", "Opus 4.6 — high capability"),
    ("claude-opus-4-7", "Opus 4.7 — most capable"),
]

# Top free models on OpenRouter (:free suffix). Users can enter any other slug
# manually — the picker accepts free-form input.
OPENROUTER_FREE_MODELS = [
    ("minimax/minimax-m2.5:free",                     "MiniMax M2.5 — default"),
    ("qwen/qwen3-coder:free",                         "Qwen3 Coder 480B — best for code"),
    ("openai/gpt-oss-120b:free",                      "GPT-OSS 120B — OpenAI open weights"),
    ("openai/gpt-oss-20b:free",                       "GPT-OSS 20B — smaller, faster"),
    ("meta-llama/llama-3.3-70b-instruct:free",        "Llama 3.3 70B Instruct"),
    ("qwen/qwen3-next-80b-a3b-instruct:free",         "Qwen3 Next 80B A3B Instruct"),
    ("nvidia/nemotron-3-super-120b-a12b:free",        "Nemotron 3 Super 120B"),
    ("z-ai/glm-4.5-air:free",                         "GLM 4.5 Air"),
    ("google/gemma-3-27b-it:free",                    "Gemma 3 27B Instruct"),
    ("nousresearch/hermes-3-llama-3.1-405b:free",     "Hermes 3 Llama 405B"),
    ("openrouter/owl-alpha",                          "Owl Alpha"),
]

OPENROUTER_DEFAULT_MODEL = "minimax/minimax-m2.5:free"

OPENROUTER_BASE_URL = "https://openrouter.ai/api"

OPENCODE_BASE_URL = "https://opencode.ai/zen/go/v1"

OPENCODE_DEFAULT_MODEL = "kimi-k2.6"

OPENCODE_MODELS = [
    ("glm-5.1",         "GLM-5.1 — latest GLM model"),
    ("glm-5",           "GLM-5 — high capability"),
    ("kimi-k2.6",       "Kimi K2.6 — Moonshot AI, most capable"),
    ("kimi-k2.5",       "Kimi K2.5 — Moonshot AI"),
    ("deepseek-v4-pro", "DeepSeek V4 Pro — strong reasoning"),
    ("deepseek-v4-flash","DeepSeek V4 Flash — fast & cheap"),
    ("mimo-v2.5-pro",   "MiMo V2.5 Pro"),
    ("mimo-v2.5",       "MiMo V2.5"),
    ("mimo-v2-pro",     "MiMo V2 Pro"),
    ("mimo-v2-omni",    "MiMo V2 Omni"),
    ("minimax-m2.7",    "MiniMax M2.7"),
    ("minimax-m2.5",    "MiniMax M2.5"),
    ("qwen3.6-plus",    "Qwen3.6 Plus"),
    ("qwen3.5-plus",    "Qwen3.5 Plus"),
]


def models_for(provider: str):
    if provider == PROVIDER_OPENROUTER:
        return OPENROUTER_FREE_MODELS
    if provider == PROVIDER_OPENCODE:
        return OPENCODE_MODELS
    return ANTHROPIC_MODELS
