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
    ("claude-haiku-4-6", "Haiku 4.6 — fastest, cheapest"),
    ("claude-sonnet-4-6", "Sonnet 4.6 — balanced"),
    ("claude-opus-4-6", "Opus 4.6 — high capability"),
    ("claude-opus-4-7", "Opus 4.7 — most capable"),
]
MAX_TOOL_OUTPUT = 6000   # trimmed from 15000 — cuts tool-result token cost ~60%
MAX_FILE_READ = 200_000
MAX_PARALLEL_TOOLS = _env_int("HARNESS_MAX_PARALLEL_TOOLS", 64, 1, 64)

# Approx pricing per 1M tokens (USD) — used only for /cost estimates.
PRICING = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7":   (15.0, 75.0),
    "claude-haiku-4-5":  (1.0, 5.0),
}
