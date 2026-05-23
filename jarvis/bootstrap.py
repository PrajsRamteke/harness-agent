"""Startup defaults — Harness Agent works with zero API keys on first run."""
from __future__ import annotations


def ensure_harness_agent_defaults() -> None:
    """Pin free Harness Agent when no paid provider credentials exist."""
    from . import state
    from .auth.client import _has_usable_provider_credentials
    from .constants.providers import HARNESS_AGENT_DEFAULT_MODEL, PROVIDER_OPENCODE_ZEN

    if _has_usable_provider_credentials():
        return
    state.provider = PROVIDER_OPENCODE_ZEN
    state.MODEL = HARNESS_AGENT_DEFAULT_MODEL
    state.harness_agent_free = True
