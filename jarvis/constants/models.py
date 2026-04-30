"""Model identifiers, pricing, and output caps."""
import os


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
AVAILABLE_MODELS = [
    ("claude-haiku-4-5", "Haiku 4.5 — fastest, cheapest"),
    ("claude-sonnet-4-6", "Sonnet 4.6 — balanced"),
    ("claude-opus-4-6", "Opus 4.6 — high capability"),
    ("claude-opus-4-7", "Opus 4.7 — most capable"),
]
MAX_TOOL_OUTPUT = 6000   # trimmed from 15000 — cuts tool-result token cost ~60%
MAX_FILE_READ = 200_000
MAX_PARALLEL_TOOLS = _env_int("HARNESS_MAX_PARALLEL_TOOLS", 64, 1, 64)

# Approx pricing per 1M tokens (USD) — used only for /cost estimates.
# (input_price, output_price). Update these if provider prices change.
PRICING = {
    # ── Anthropic (direct API) ────────────────────────────────────────────────
    "claude-haiku-4-5":  (1.0,   5.0),
    "claude-sonnet-4-6": (3.0,  15.0),
    "claude-opus-4-6":   (15.0, 75.0),
    "claude-opus-4-7":   (15.0, 75.0),

    # ── OpenRouter free-tier (all :free suffix → $0) ─────────────────────────
    "minimax/minimax-m2.5:free":                     (0.0, 0.0),
    "qwen/qwen3-coder:free":                         (0.0, 0.0),
    "openai/gpt-oss-120b:free":                      (0.0, 0.0),
    "openai/gpt-oss-20b:free":                       (0.0, 0.0),
    "meta-llama/llama-3.3-70b-instruct:free":        (0.0, 0.0),
    "qwen/qwen3-next-80b-a3b-instruct:free":         (0.0, 0.0),
    "nvidia/nemotron-3-super-120b-a12b:free":        (0.0, 0.0),
    "z-ai/glm-4.5-air:free":                         (0.0, 0.0),
    "google/gemma-3-27b-it:free":                    (0.0, 0.0),
    "nousresearch/hermes-3-llama-3.1-405b:free":     (0.0, 0.0),

    # ── OpenCode Go models (estimates — update as needed) ────────────────────
    "glm-5.1":          (2.0,   8.0),    # Zhipu GLM-5 series
    "glm-5":            (2.0,   8.0),
    "kimi-k2.6":        (1.0,   4.0),    # Moonshot Kimi K2
    "kimi-k2.5":        (1.0,   4.0),
    "deepseek-v4-pro":  (1.74,  3.48),   # official DeepSeek API
    "deepseek-v4-flash":(0.14,  0.28),   # official DeepSeek API
    "mimo-v2.5-pro":    (1.0,   4.0),    # MiMo series
    "mimo-v2.5":        (0.5,   2.0),
    "mimo-v2-pro":      (1.0,   4.0),
    "mimo-v2-omni":     (0.5,   2.0),
    "minimax-m2.7":     (1.5,   6.0),    # MiniMax
    "minimax-m2.5":     (1.0,   5.0),
    "qwen3.6-plus":     (0.5,   2.0),    # Alibaba Qwen
    "qwen3.5-plus":     (0.5,   2.0),
}

# ── Numeric / behavior constants ───────────────────────────────────────────────
FILE_PERMISSION = 0o600
PANEL_PREVIEW_CHARS = 400
OAUTH_EXPIRY_BUFFER = 60      # seconds before expiry to trigger refresh
OAUTH_DEFAULT_EXPIRY = 3600   # fallback when expires_in missing
DEFAULT_BASH_TIMEOUT = 60
DEFAULT_RETRIES = 3
API_MAX_TOKENS = 8192
THINKING_BUDGET_TOKENS = 4000
SPECK_MAX_CHARS = 8000
CLICK_WAIT_ATTEMPTS = 20
CLICK_WAIT_DELAY = 0.2
SETTLE_WAIT = 0.5

# File reading limits
MAX_FILE_SIZE_BYTES = 2_000_000
MAX_FILE_CHUNK_BYTES = 4_096

# Document reader defaults
DOC_MAX_FILES_DEFAULT = 32
DOC_MAX_FILES_CAP = 80
DOC_MAX_CHARS_PER_FILE_DEFAULT = 48_000
DOC_CSV_MAX_ROWS_DEFAULT = 200

# OCR defaults
OCR_MAX_FILES_DEFAULT = 80
OCR_MAX_FILES_CAP = 200
OCR_CHARS_PER_IMAGE = 800
OCR_CHARS_PER_IMAGE_CAP = 4000
OCR_SCAN_CHARS = 12_000
OCR_WORKER_MIN = 1

# Git log default
GIT_LOG_DEFAULT_COUNT = 10

# Search default
SEARCH_DEFAULT_MAX_RESULTS = 8
SEARCH_MATCH_CAP = 50

# ── Mode identifiers ──────────────────────────────────────────────────────────
MODE_DEFAULT = "default"
MODE_CODING = "coding"
