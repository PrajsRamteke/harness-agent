"""Activate / disconnect OAuth subscription connections."""
from __future__ import annotations

import os

from ...constants import AUTH_API_KEY, AUTH_MODE_FILE, AUTH_OAUTH, PROVIDER_FILE, PROVIDER_ANTHROPIC
from ...constants.oauth_providers import (
    OAUTH_ID_ANTHROPIC, OAUTH_ID_OPENAI_CODEX, OAuthProviderSpec,
)
from ...constants.providers import CODEX_DEFAULT_MODEL, PROVIDER_OPENAI_CODEX
from ...utils.io import _secure_write
from ... import state
from ..anthropic_models import sync_anthropic_model_ids
from ..client import _build_client_from_mode, _build_codex_client
from ..codex_oauth_tokens import clear_codex_oauth_tokens, load_codex_oauth_tokens
from ..oauth_tokens import clear_oauth_tokens, load_oauth_tokens
from .oauth_status import oauth_connection_status

OAuthActionResult = tuple[bool, str, list[str] | None]


def is_active_oauth(spec: OAuthProviderSpec) -> bool:
    st = oauth_connection_status(spec)
    if not st.connected:
        return False
    if spec.id == OAUTH_ID_ANTHROPIC:
        return (
            state.provider == PROVIDER_ANTHROPIC
            and state.auth_mode == AUTH_OAUTH
        )
    if spec.id == OAUTH_ID_OPENAI_CODEX:
        return state.provider == PROVIDER_OPENAI_CODEX and state.auth_mode == AUTH_OAUTH
    return False


def activate_oauth(spec: OAuthProviderSpec) -> OAuthActionResult:
    if not spec.available:
        return False, f"{spec.label} login is not available yet", None
    st = oauth_connection_status(spec)
    if not st.connected:
        return False, f"sign in to {spec.label} first", None

    if spec.id == OAUTH_ID_ANTHROPIC:
        state.provider = PROVIDER_ANTHROPIC
        state.auth_mode = AUTH_OAUTH
        _secure_write(PROVIDER_FILE, PROVIDER_ANTHROPIC)
        _secure_write(AUTH_MODE_FILE, AUTH_OAUTH)
        try:
            state.client = _build_client_from_mode(AUTH_OAUTH)
            model_ids = sync_anthropic_model_ids(state.client)
        except Exception as e:
            return False, f"failed to activate: {e}", None
        return True, f"✓ active: {spec.label} (OAuth)", model_ids

    if spec.id == OAUTH_ID_OPENAI_CODEX:
        state.provider = PROVIDER_OPENAI_CODEX
        state.auth_mode = AUTH_OAUTH
        if not state.MODEL or state.MODEL.startswith("claude-"):
            state.MODEL = CODEX_DEFAULT_MODEL
        _secure_write(PROVIDER_FILE, PROVIDER_OPENAI_CODEX)
        _secure_write(AUTH_MODE_FILE, AUTH_OAUTH)
        try:
            state.client = _build_codex_client()
        except Exception as e:
            return False, f"failed to activate: {e}", None
        from ...constants.providers import CODEX_MODELS
        model_ids = [m for m, _ in CODEX_MODELS]
        return True, f"✓ active: {spec.label} (OAuth)", model_ids

    return False, f"{spec.label} activation not implemented", None


def disconnect_oauth(spec: OAuthProviderSpec) -> OAuthActionResult:
    if spec.id == OAUTH_ID_ANTHROPIC:
        if not load_oauth_tokens():
            return False, "not signed in", None
        clear_oauth_tokens()
        if state.provider == PROVIDER_ANTHROPIC and state.auth_mode == AUTH_OAUTH:
            from ...constants.paths import KEY_FILE
            if KEY_FILE.exists() or os.getenv("ANTHROPIC_API_KEY"):
                state.auth_mode = AUTH_API_KEY
                _secure_write(AUTH_MODE_FILE, AUTH_API_KEY)
                try:
                    state.client = _build_client_from_mode(AUTH_API_KEY)
                except Exception:
                    state.client = None
            else:
                state.client = None
        return True, f"✓ signed out of {spec.label}", None

    if spec.id == OAUTH_ID_OPENAI_CODEX:
        if not load_codex_oauth_tokens():
            return False, "not signed in", None
        clear_codex_oauth_tokens()
        if state.provider == PROVIDER_OPENAI_CODEX and state.auth_mode == AUTH_OAUTH:
            state.client = None
        return True, f"✓ signed out of {spec.label}", None

    return False, f"{spec.label} logout not implemented yet", None
