"""API key file/env specs — shared by key modal and connect flows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .paths import (
    KEY_FILE, OPENROUTER_KEY_FILE, OPENCODE_KEY_FILE, OPENCODE_ZEN_KEY_FILE, KIMCHI_KEY_FILE,
)
from .providers import (
    PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN, PROVIDER_KIMCHI,
)

API_KEY_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "anthropic_api",
        "provider": PROVIDER_ANTHROPIC,
        "label": "Anthropic API",
        "file_path": KEY_FILE,
        "env_var": "ANTHROPIC_API_KEY",
        "key_prefix": "sk-ant-",
    },
    {
        "id": "openrouter",
        "provider": PROVIDER_OPENROUTER,
        "label": "OpenRouter",
        "file_path": OPENROUTER_KEY_FILE,
        "env_var": "OPENROUTER_API_KEY",
        "key_prefix": "sk-or-",
    },
    {
        "id": "opencode",
        "provider": PROVIDER_OPENCODE,
        "label": "OpenCode Go",
        "file_path": OPENCODE_KEY_FILE,
        "env_var": "OPENCODE_API_KEY",
        "key_prefix": None,
    },
    {
        "id": "opencode_zen",
        "provider": PROVIDER_OPENCODE_ZEN,
        "label": "OpenCode Zen",
        "file_path": OPENCODE_ZEN_KEY_FILE,
        "env_var": "OPENCODE_ZEN_API_KEY",
        "key_prefix": None,
    },
    {
        "id": "kimchi",
        "provider": PROVIDER_KIMCHI,
        "label": "Kimchi",
        "file_path": KIMCHI_KEY_FILE,
        "env_var": "KIMCHI_API_KEY",
        "key_prefix": None,
    },
)

_API_KEY_BY_ID = {s["id"]: s for s in API_KEY_SPECS}
_API_KEY_BY_PROVIDER = {s["provider"]: s for s in API_KEY_SPECS}


def api_key_spec(spec_id: str) -> dict[str, Any] | None:
    return _API_KEY_BY_ID.get(spec_id)


def api_key_spec_for_provider(provider: str) -> dict[str, Any] | None:
    return _API_KEY_BY_PROVIDER.get(provider)
