"""Numeric and behavior constants (model-agnostic).

Model-specific constants (MODEL_INFO, PRICING, model lists) live in
jarvis/constants/providers.py. Add new models there.
"""
import os


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


VERSION = "0.1.0"
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_TOOL_OUTPUT = 6000   # trimmed from 15000 — cuts tool-result token cost ~60%
MAX_FILE_READ = 200_000
MAX_PARALLEL_TOOLS = _env_int("HARNESS_MAX_PARALLEL_TOOLS", 64, 1, 64)

# ── Numeric / behavior constants ───────────────────────────────────────────────
FILE_PERMISSION = 0o600
PANEL_PREVIEW_CHARS = 400
# Tool output shown inline in the transcript (RichLog scrolls). F3 opens full viewer.
TOOL_UI_PREVIEW_LINES = _env_int("HARNESS_TOOL_UI_PREVIEW_LINES", 24, 4, 200)
TOOL_UI_PREVIEW_CHARS = _env_int("HARNESS_TOOL_UI_PREVIEW_CHARS", 4000, 500, 100_000)
TOOL_UI_VIEWER_MAX_CHARS = _env_int("HARNESS_TOOL_UI_VIEWER_MAX_CHARS", 500_000, 10_000, 2_000_000)
TOOL_UI_HISTORY_SIZE = 32
OAUTH_EXPIRY_BUFFER = 60      # seconds before expiry to trigger refresh
OAUTH_DEFAULT_EXPIRY = 3600   # fallback when expires_in missing
DEFAULT_BASH_TIMEOUT = 60
DEFAULT_RETRIES = 3
# Hard cap on a single API response. The old 8192 default routinely truncated
# tool-call arguments mid-stream when the model wrote large files (HTML, JSON,
# big code), producing "Unterminated string" / "missing required argument"
# errors. Claude 4.x supports up to 64K output tokens; most OpenAI-compatible
# providers accept 32K. Override with HARNESS_API_MAX_TOKENS if a specific
# provider rejects this value.
API_MAX_TOKENS = _env_int("HARNESS_API_MAX_TOKENS", 32000, 1024, 200_000)
THINKING_BUDGET_TOKENS = 4000
THINK_EFFORTS = ("xhigh", "high", "medium", "low", "minimal", "none")
DEFAULT_THINK_EFFORT = "high"
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

# Connected Context Pack — max chars for the resolve_context / read_bundle output
CONTEXT_BUNDLE_MAX_CHARS = _env_int("HARNESS_BUNDLE_MAX_CHARS", 120_000, 8_000, 500_000)
CONTEXT_BUNDLE_PER_FILE_MAX = _env_int("HARNESS_BUNDLE_PER_FILE_MAX", 20_000, 500, 100_000)
# full | skeleton | manifest — used when the tool omits an explicit mode
BUNDLE_DEFAULT_MODE = (os.getenv("HARNESS_BUNDLE_MODE", "skeleton") or "skeleton").strip().lower()
BUNDLE_DEFAULT_MODE_READ = (os.getenv("HARNESS_BUNDLE_MODE_READ", "full") or "full").strip().lower()
