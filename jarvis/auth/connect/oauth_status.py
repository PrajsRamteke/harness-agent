"""OAuth subscription connection status."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ...constants.oauth_providers import (
    OAUTH_ID_ANTHROPIC, OAUTH_ID_ANTIGRAVITY, OAUTH_ID_OPENAI_CODEX, OAuthProviderSpec,
)
from ..antigravity_oauth import load_antigravity_tokens
from ..oauth_tokens import load_oauth_tokens
from ..codex_oauth_tokens import load_codex_oauth_tokens

OAuthConnectionSource = Literal["oauth", "none"]


@dataclass(frozen=True)
class OAuthConnectionStatus:
    connected: bool
    source: OAuthConnectionSource
    detail: str


def oauth_connection_status(spec: OAuthProviderSpec) -> OAuthConnectionStatus:
    if spec.id == OAUTH_ID_ANTHROPIC:
        if load_oauth_tokens():
            return OAuthConnectionStatus(True, "oauth", "signed in")
        return OAuthConnectionStatus(False, "none", "not signed in")
    if spec.id == OAUTH_ID_OPENAI_CODEX:
        if load_codex_oauth_tokens():
            return OAuthConnectionStatus(True, "oauth", "signed in")
        return OAuthConnectionStatus(False, "none", "not signed in")
    if spec.id == OAUTH_ID_ANTIGRAVITY:
        tokens = load_antigravity_tokens()
        if tokens:
            email = str(tokens.get("email") or "")
            return OAuthConnectionStatus(True, "oauth", f"signed in as {email}" if email else "signed in")
        return OAuthConnectionStatus(False, "none", "not signed in")
    if not spec.available:
        return OAuthConnectionStatus(False, "none", "coming soon")
    return OAuthConnectionStatus(False, "none", "not signed in")
