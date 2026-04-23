"""Model identifiers, pricing, and output caps."""
import os

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
AVAILABLE_MODELS = [
    ("claude-haiku-4-6", "Haiku 4.6 — fastest, cheapest"),
    ("claude-sonnet-4-6", "Sonnet 4.6 — balanced"),
    ("claude-opus-4-6", "Opus 4.6 — high capability"),
    ("claude-opus-4-7", "Opus 4.7 — most capable"),
]
MAX_TOOL_OUTPUT = 15000
MAX_FILE_READ = 200_000

# Approx pricing per 1M tokens (USD) — used only for /cost estimates.
PRICING = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7":   (15.0, 75.0),
    "claude-haiku-4-5":  (1.0, 5.0),
}
