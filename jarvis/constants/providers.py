"""Provider registry: Anthropic (default) and OpenRouter.

Each provider exposes a list of (model_id, description) tuples for the /model
picker. OpenRouter models are free-tier popular picks; users can also type any
OpenRouter slug manually at the prompt.
"""

PROVIDERS = ("anthropic", "openrouter")

PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "openrouter": "OpenRouter",
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
]

OPENROUTER_DEFAULT_MODEL = "minimax/minimax-m2.5:free"

OPENROUTER_BASE_URL = "https://openrouter.ai/api"


def models_for(provider: str):
    if provider == "openrouter":
        return OPENROUTER_FREE_MODELS
    return ANTHROPIC_MODELS
