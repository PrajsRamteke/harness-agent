"""OAuth / subscription login providers — separate from API-key billing providers.

Only browser-login subscriptions live here (``/login``, ``/logout``).
API keys are managed via ``/key`` and ``jarvis/constants/api_keys.py``.
"""
from __future__ import annotations

from dataclasses import dataclass

from .providers import PROVIDER_ANTHROPIC, PROVIDER_OPENAI_CODEX

OAUTH_ID_ANTHROPIC = "anthropic"
OAUTH_ID_OPENAI_CODEX = "openai_codex"


@dataclass(frozen=True)
class OAuthProviderSpec:
    id: str
    label: str
    description: str
    runtime_provider: str
    available: bool = True
    sort_order: int = 0


OAUTH_PROVIDERS: tuple[OAuthProviderSpec, ...] = (
    OAuthProviderSpec(
        id=OAUTH_ID_ANTHROPIC,
        label="Anthropic",
        description="Claude Pro / Max — sign in with your subscription",
        runtime_provider=PROVIDER_ANTHROPIC,
        available=True,
        sort_order=10,
    ),
    OAuthProviderSpec(
        id=OAUTH_ID_OPENAI_CODEX,
        label="OpenAI Codex",
        description="ChatGPT / Codex subscription — browser login",
        runtime_provider=PROVIDER_OPENAI_CODEX,
        available=True,
        sort_order=20,
    ),
)

_OAUTH_BY_ID = {p.id: p for p in OAUTH_PROVIDERS}


def oauth_provider(spec_id: str) -> OAuthProviderSpec | None:
    return _OAUTH_BY_ID.get(spec_id)
