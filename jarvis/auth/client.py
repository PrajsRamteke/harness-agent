"""Build an Anthropic-SDK client for the active provider; validate & retry.

Providers:
  - anthropic  → api.anthropic.com, auth via API key OR OAuth (existing flow).
  - openrouter → openrouter.ai/api/v1 (Anthropic-compatible /v1/messages),
                 auth via Bearer token using the Anthropic SDK's `auth_token=`.
"""
import os, sys

import httpx

from ..console import console, Anthropic, APIStatusError, APIConnectionError
from ..constants import (
    KEY_FILE, OPENROUTER_KEY_FILE, AUTH_MODE_FILE, PROVIDER_FILE,
    OAUTH_BETA_HEADER, OPENROUTER_BASE_URL, OPENROUTER_DEFAULT_MODEL,
    MODEL as _DEFAULT_ANTHROPIC_MODEL,
    PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENCODE,
    AUTH_API_KEY, AUTH_OAUTH, DEFAULT_RETRIES, DEFAULT_BASH_TIMEOUT,
)
from ..utils.io import _secure_write
from .. import state
from .api_key import load_key, prompt_for_key
from .openrouter import load_openrouter_key, prompt_for_openrouter_key
from .opencode import load_opencode_key, prompt_for_opencode_key
from .opencode_client import OpenCodeClient
from .oauth_tokens import (
    load_oauth_tokens, clear_oauth_tokens, oauth_refresh, get_fresh_oauth_token,
)
from .oauth_flow import oauth_login
from .mode_picker import _choose_auth_mode, _choose_provider


def _http_timeout(*, openrouter: bool) -> httpx.Timeout:
    """Limits how long we wait between bytes on streaming responses.

    OpenRouter (especially free models) often queues or stalls; without a bounded
    read timeout the UI can sit idle for many minutes while httpx waits. Anthropic
    direct keeps the SDK-style 10-minute read budget unless overridden.
    """
    env_read = os.getenv("HARNESS_HTTP_READ_TIMEOUT", "").strip()
    if env_read:
        read = float(env_read)
    else:
        read = 240.0 if openrouter else 600.0
    connect_default = 30
    c = float(os.getenv("HARNESS_HTTP_CONNECT_TIMEOUT", str(connect_default)).strip() or str(connect_default))
    return httpx.Timeout(connect=c, read=read, write=c, pool=c)


def _build_openrouter_client() -> Anthropic:
    key = load_openrouter_key()
    return Anthropic(
        api_key=None,
        auth_token=key,  # sends "Authorization: Bearer <key>"
        base_url=OPENROUTER_BASE_URL,
        timeout=_http_timeout(openrouter=True),
        default_headers={
            "HTTP-Referer": "https://github.com/harness-agent",
            "X-Title": "harness",
        },
    )


def _build_client_from_mode(mode: str) -> Anthropic:
    """Construct a client. `mode` is 'openrouter' | 'oauth' | 'api_key'.

    If state.provider is openrouter, ignore `mode` and build an OpenRouter client.
    """
    if mode == PROVIDER_OPENROUTER or state.provider == PROVIDER_OPENROUTER:
        return _build_openrouter_client()
    if mode == AUTH_OAUTH:
        tokens = get_fresh_oauth_token()
        if not tokens:
            tokens = oauth_login()
        if not tokens:
            new_mode = _choose_auth_mode()
            state.auth_mode = new_mode
            _secure_write(AUTH_MODE_FILE, new_mode)
            return _build_client_from_mode(new_mode)
        return Anthropic(
            api_key=None,
            auth_token=tokens["access_token"],
            timeout=_http_timeout(openrouter=False),
            default_headers={"anthropic-beta": OAUTH_BETA_HEADER},
        )
    return Anthropic(api_key=load_key(), timeout=_http_timeout(openrouter=False))


def _resolve_provider() -> str:
    """Decide provider from env → stored → prompt, preserving legacy behavior."""
    env_provider = os.getenv("HARNESS_PROVIDER", "").strip().lower()
    if env_provider in (PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENCODE):
        return env_provider
    # Legacy: ANTHROPIC_API_KEY env var pins to Anthropic.
    if os.getenv("ANTHROPIC_API_KEY"):
        return PROVIDER_ANTHROPIC
    if os.getenv("OPENROUTER_API_KEY") and not KEY_FILE.exists() and not AUTH_MODE_FILE.exists():
        return PROVIDER_OPENROUTER
    if os.getenv("OPENCODE_API_KEY") and not KEY_FILE.exists() and not AUTH_MODE_FILE.exists():
        return PROVIDER_OPENCODE
    if PROVIDER_FILE.exists():
        stored = PROVIDER_FILE.read_text().strip()
        if stored in (PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENCODE):
            return stored
    # Legacy: any existing Anthropic state means Anthropic (preserves old flow).
    if AUTH_MODE_FILE.exists() or KEY_FILE.exists() or load_oauth_tokens():
        return PROVIDER_ANTHROPIC
    return _choose_provider()


def _build_opencode_client() -> OpenCodeClient:
    key = load_opencode_key()
    return OpenCodeClient(api_key=key)


def make_client():
    """Resolve provider + auth, build client, validate; handle 401 with refresh/re-auth."""
    state.provider = _resolve_provider()
    _secure_write(PROVIDER_FILE, state.provider)

    if state.provider == PROVIDER_OPENCODE:
        from ..constants import OPENCODE_DEFAULT_MODEL
        if not state.MODEL or state.MODEL.startswith("claude-") or "/" in state.MODEL:
            state.MODEL = OPENCODE_DEFAULT_MODEL
        for attempt in range(DEFAULT_RETRIES):
            try:
                c = _build_opencode_client()
                return c
            except Exception as e:
                if "401" in str(e) or "unauthorized" in str(e).lower():
                    from ..constants import OPENCODE_KEY_FILE
                    OPENCODE_KEY_FILE.unlink(missing_ok=True)
                    prompt_for_opencode_key(
                        reason="Stored OpenCode key rejected. Please re-enter."
                    )
                    continue
                raise
        console.print("[red]Too many OpenCode auth failures[/]"); sys.exit(1)

    if state.provider == PROVIDER_OPENROUTER:
        if "/" not in state.MODEL:
            state.MODEL = OPENROUTER_DEFAULT_MODEL
        for attempt in range(DEFAULT_RETRIES):
            try:
                c = _build_openrouter_client()
                # Skip cheap validation: OpenRouter's /v1/models schema differs
                # from Anthropic's and would break client.models.list(). The
                # first real /v1/messages call will surface any 401.
                return c
            except APIStatusError as e:
                if e.status_code == 401:
                    OPENROUTER_KEY_FILE.unlink(missing_ok=True)
                    prompt_for_openrouter_key(
                        reason="Stored OpenRouter key rejected (401). Please re-enter."
                    )
                    continue
                raise
            except APIConnectionError as e:
                console.print(f"[red]Network error: {e}[/]"); sys.exit(1)
        console.print("[red]Too many OpenRouter auth failures[/]"); sys.exit(1)

    # ── Anthropic path (preserved from original flow) ──
    if os.getenv("ANTHROPIC_API_KEY"):
        state.auth_mode = AUTH_API_KEY
    elif AUTH_MODE_FILE.exists():
        stored = AUTH_MODE_FILE.read_text().strip()
        if stored in (AUTH_API_KEY, AUTH_OAUTH):
            state.auth_mode = stored
            if state.auth_mode == AUTH_OAUTH and not load_oauth_tokens():
                pass
            elif state.auth_mode == AUTH_API_KEY and not KEY_FILE.exists() and not os.getenv("ANTHROPIC_API_KEY"):
                state.auth_mode = _choose_auth_mode()
        else:
            state.auth_mode = _choose_auth_mode()
    elif KEY_FILE.exists():
        state.auth_mode = AUTH_API_KEY
    elif load_oauth_tokens():
        state.auth_mode = AUTH_OAUTH
    else:
        state.auth_mode = _choose_auth_mode()

    _secure_write(AUTH_MODE_FILE, state.auth_mode)

    # If returning from OpenRouter, restore a valid Anthropic default model.
    if "/" in state.MODEL:
        state.MODEL = _DEFAULT_ANTHROPIC_MODEL

    for attempt in range(DEFAULT_RETRIES):
        try:
            c = _build_client_from_mode(state.auth_mode)
            c.models.list(limit=1)  # cheap validation
            return c
        except APIStatusError as e:
            if e.status_code == 401:
                if state.auth_mode == AUTH_OAUTH:
                    tokens = load_oauth_tokens()
                    if tokens and oauth_refresh(tokens):
                        console.print("[dim]refreshed OAuth token, retrying…[/]")
                        continue
                    console.print("[yellow]OAuth session invalid — re-login required.[/]")
                    clear_oauth_tokens()
                    oauth_login()
                    continue
                else:
                    KEY_FILE.unlink(missing_ok=True)
                    prompt_for_key(reason="Stored Anthropic key rejected (401). Please re-enter.")
                    continue
            raise
        except APIConnectionError as e:
            console.print(f"[red]Network error: {e}[/]"); sys.exit(1)
    console.print("[red]Too many auth failures[/]"); sys.exit(1)
