"""Activate / disconnect OAuth subscription connections."""
from __future__ import annotations

import os

from ...constants import AUTH_API_KEY, AUTH_MODE_FILE, AUTH_OAUTH, PROVIDER_FILE, PROVIDER_ANTHROPIC
from ...constants.oauth_providers import (
    OAUTH_ID_ANTHROPIC, OAUTH_ID_ANTIGRAVITY, OAUTH_ID_OPENAI_CODEX, OAuthProviderSpec,
)
from ...constants.providers import (
    PROVIDER_ANTIGRAVITY, PROVIDER_OPENAI_CODEX, normalize_model_for_provider,
)
from ...storage.prefs import save_last_model
from ...utils.io import _secure_write
from ... import state
from ..anthropic_models import sync_anthropic_model_ids
from ..antigravity_oauth import clear_antigravity_tokens, load_antigravity_tokens
from ..client import _build_antigravity_client, _build_client_from_mode, _build_codex_client
from ..codex_oauth_tokens import clear_codex_oauth_tokens, load_codex_oauth_tokens
from ..oauth_tokens import clear_oauth_tokens, load_oauth_tokens
from .oauth_status import oauth_connection_status

OAuthActionResult = tuple[bool, str, list[str] | None]


def adopt_provider_model(provider: str) -> None:
    """After a sign-in switched to ``provider``, move the model there too.

    Swapping only the client left e.g. a Harness Agent model id selected, so
    the next request asked Anthropic / Codex for a model they don't serve and
    only a restart (which re-resolves the model) made chat work again.
    """
    state.harness_agent_free = False
    state.MODEL = normalize_model_for_provider(state.MODEL, provider)
    save_last_model()


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
    if spec.id == OAUTH_ID_ANTIGRAVITY:
        return state.provider == PROVIDER_ANTIGRAVITY
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
            state.client = _build_client_from_mode(AUTH_OAUTH, interactive=False)
            model_ids = sync_anthropic_model_ids(state.client)
        except Exception as e:
            return False, f"failed to activate: {e}", None
        adopt_provider_model(PROVIDER_ANTHROPIC)
        return True, f"✓ active: {spec.label} (OAuth)", model_ids

    if spec.id == OAUTH_ID_OPENAI_CODEX:
        state.provider = PROVIDER_OPENAI_CODEX
        state.auth_mode = AUTH_OAUTH
        _secure_write(PROVIDER_FILE, PROVIDER_OPENAI_CODEX)
        _secure_write(AUTH_MODE_FILE, AUTH_OAUTH)
        try:
            state.client = _build_codex_client()
        except Exception as e:
            return False, f"failed to activate: {e}", None
        adopt_provider_model(PROVIDER_OPENAI_CODEX)
        from ...constants.providers import codex_models_for_picker
        model_ids = [m for m, _ in codex_models_for_picker()]
        return True, f"✓ active: {spec.label} (OAuth)", model_ids

    if spec.id == OAUTH_ID_ANTIGRAVITY:
        state.provider = PROVIDER_ANTIGRAVITY
        state.auth_mode = AUTH_OAUTH
        _secure_write(PROVIDER_FILE, PROVIDER_ANTIGRAVITY)
        _secure_write(AUTH_MODE_FILE, AUTH_OAUTH)
        try:
            state.client = _build_antigravity_client()
            if state.client is None:
                raise RuntimeError("Antigravity sign-in couldn't be refreshed — sign in again")
        except Exception as e:
            return False, f"failed to activate: {e}", None
        adopt_provider_model(PROVIDER_ANTIGRAVITY)
        from ...constants.providers import antigravity_models_for_picker
        model_ids = [m for m, _ in antigravity_models_for_picker()]
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

    if spec.id == OAUTH_ID_ANTIGRAVITY:
        if not load_antigravity_tokens():
            return False, "not signed in", None
        clear_antigravity_tokens()
        if state.provider == PROVIDER_ANTIGRAVITY:
            state.client = None
        return True, f"✓ signed out of {spec.label}", None

    return False, f"{spec.label} logout not implemented yet", None
