"""Startup defaults — Harness Agent works with zero API keys on first run."""
from __future__ import annotations

import os


def _has_any_credentials_fast() -> bool:
    """Cheap credential probe — avoids importing the full auth client stack."""
    if any(
        os.getenv(k)
        for k in (
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "OPENCODE_API_KEY",
            "OPENCODE_ZEN_API_KEY",
        )
    ):
        return True

    from .constants import (
        KEY_FILE,
        OPENROUTER_KEY_FILE,
        OPENCODE_KEY_FILE,
        OPENCODE_ZEN_KEY_FILE,
        OAUTH_FILE,
    )
    from .constants.paths import CODEX_OAUTH_FILE

    for path in (
        KEY_FILE,
        OPENROUTER_KEY_FILE,
        OPENCODE_KEY_FILE,
        OPENCODE_ZEN_KEY_FILE,
        OAUTH_FILE,
        CODEX_OAUTH_FILE,
    ):
        try:
            if path.exists() and path.read_text().strip():
                return True
        except OSError:
            pass
    return False


def ensure_harness_agent_defaults() -> None:
    """Pin free Harness Agent when no paid provider credentials exist."""
    from . import state
    from .constants.providers import HARNESS_AGENT_DEFAULT_MODEL, PROVIDER_OPENCODE_ZEN

    if _has_any_credentials_fast():
        return
    state.provider = PROVIDER_OPENCODE_ZEN
    state.MODEL = HARNESS_AGENT_DEFAULT_MODEL
    state.harness_agent_free = True
