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
    KEY_FILE, OPENROUTER_KEY_FILE, OPENCODE_ZEN_KEY_FILE, AUTH_MODE_FILE, PROVIDER_FILE,
    OPENROUTER_BASE_URL,
    OPENCODE_ZEN_BASE_URL, OPENCODE_ZEN_DEFAULT_MODEL,
    HARNESS_AGENT_DEFAULT_MODEL,
    PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN,
    PROVIDER_OPENAI_CODEX,
    is_harness_agent_model,
    AUTH_API_KEY, AUTH_OAUTH, DEFAULT_RETRIES, DEFAULT_BASH_TIMEOUT,
    normalize_model_for_provider,
)
from ..utils.io import _secure_write
from .. import state
from .api_key import load_key, prompt_for_key
from .openrouter import load_openrouter_key, prompt_for_openrouter_key
from .opencode import load_opencode_key, prompt_for_opencode_key
from .opencode_zen import load_opencode_zen_key, prompt_for_opencode_zen_key, has_opencode_zen_key
from .harness_agent import build_harness_agent_client, should_use_harness_agent_client
from .opencode_client import OpenCodeClient
from .oauth_tokens import (
    load_oauth_tokens, clear_oauth_tokens, oauth_refresh, get_fresh_oauth_token,
    oauth_client_headers,
)
from .anthropic_models import sync_anthropic_model_ids
from .codex_client import CodexClient
from .codex_oauth_tokens import get_fresh_codex_oauth_token, load_codex_oauth_tokens
from .oauth_flow import oauth_login
from .mode_picker import _choose_auth_mode


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


def _build_client_from_mode(mode: str, *, interactive: bool = True) -> Anthropic:
    """Construct a client. `mode` is 'openrouter' | 'oauth' | 'api_key'.

    If state.provider is openrouter, ignore `mode` and build an OpenRouter client.
    """
    if mode == PROVIDER_OPENROUTER or state.provider == PROVIDER_OPENROUTER:
        return _build_openrouter_client()
    if mode == AUTH_OAUTH:
        tokens = get_fresh_oauth_token()
        if not tokens:
            if not interactive:
                raise RuntimeError("Anthropic OAuth not configured")
            tokens = oauth_login()
        if not tokens:
            if not interactive:
                raise RuntimeError("Anthropic OAuth not configured")
            new_mode = _choose_auth_mode()
            state.auth_mode = new_mode
            _secure_write(AUTH_MODE_FILE, new_mode)
            return _build_client_from_mode(new_mode, interactive=interactive)
        return Anthropic(
            api_key=None,
            auth_token=tokens["access_token"],
            timeout=_http_timeout(openrouter=False),
            default_headers=oauth_client_headers(),
        )
    return Anthropic(api_key=load_key(), timeout=_http_timeout(openrouter=False))


def _has_openrouter_key() -> bool:
    if os.getenv("OPENROUTER_API_KEY"):
        return True
    try:
        return OPENROUTER_KEY_FILE.exists() and bool(OPENROUTER_KEY_FILE.read_text().strip())
    except OSError:
        return False


def _has_opencode_key() -> bool:
    from ..constants.paths import OPENCODE_KEY_FILE
    if os.getenv("OPENCODE_API_KEY"):
        return True
    try:
        return OPENCODE_KEY_FILE.exists() and bool(OPENCODE_KEY_FILE.read_text().strip())
    except OSError:
        return False


def _has_opencode_zen_key() -> bool:
    return has_opencode_zen_key()


def _has_usable_anthropic_auth() -> bool:
    if _has_anthropic_api_key() or load_oauth_tokens():
        return True
    if not AUTH_MODE_FILE.exists():
        return False
    try:
        stored = AUTH_MODE_FILE.read_text().strip()
    except OSError:
        return False
    if stored == AUTH_OAUTH and load_oauth_tokens():
        return True
    if stored == AUTH_API_KEY and _has_anthropic_api_key():
        return True
    return False


def _has_usable_provider_credentials() -> bool:
    """True when at least one paid/configured provider has working auth."""
    if (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or os.getenv("OPENCODE_API_KEY")
        or os.getenv("OPENCODE_ZEN_API_KEY")
    ):
        return True
    if _has_usable_anthropic_auth() or load_codex_oauth_tokens():
        return True
    if _has_openrouter_key() or _has_opencode_key() or _has_opencode_zen_key():
        return True
    return False


def _has_any_provider_credentials() -> bool:
    """Backward-compatible alias — only counts credentials that actually work."""
    return _has_usable_provider_credentials()


def _has_anthropic_api_key() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY")) or (
        KEY_FILE.exists() and bool(KEY_FILE.read_text().strip())
    )


def _resolve_auth_mode(*, interactive: bool) -> str | None:
    """Pick Anthropic auth mode without prompting when ``interactive=False``."""
    if os.getenv("ANTHROPIC_API_KEY") or KEY_FILE.exists():
        return AUTH_API_KEY
    if load_oauth_tokens():
        return AUTH_OAUTH
    if AUTH_MODE_FILE.exists():
        stored = AUTH_MODE_FILE.read_text().strip()
        if stored == AUTH_OAUTH and load_oauth_tokens():
            return AUTH_OAUTH
        if stored == AUTH_API_KEY and _has_anthropic_api_key():
            return AUTH_API_KEY
        if stored == AUTH_OAUTH and not load_oauth_tokens():
            return None
        if stored == AUTH_API_KEY and not _has_anthropic_api_key():
            return None
    if not interactive:
        return None
    return _choose_auth_mode()


def _resolve_provider(*, interactive: bool = True) -> str:
    """Decide provider from env → stored → first-run Harness Agent default."""
    env_provider = os.getenv("HARNESS_PROVIDER", "").strip().lower()
    if env_provider in (PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN, PROVIDER_OPENAI_CODEX):
        return env_provider
    # Legacy: ANTHROPIC_API_KEY env var pins to Anthropic.
    if os.getenv("ANTHROPIC_API_KEY"):
        return PROVIDER_ANTHROPIC
    if os.getenv("OPENROUTER_API_KEY") and not KEY_FILE.exists() and not AUTH_MODE_FILE.exists():
        return PROVIDER_OPENROUTER
    if os.getenv("OPENCODE_API_KEY") and not KEY_FILE.exists() and not AUTH_MODE_FILE.exists():
        return PROVIDER_OPENCODE
    if os.getenv("OPENCODE_ZEN_API_KEY") and not KEY_FILE.exists() and not AUTH_MODE_FILE.exists():
        return PROVIDER_OPENCODE_ZEN
    if PROVIDER_FILE.exists():
        try:
            stored = PROVIDER_FILE.read_text().strip()
        except OSError:
            stored = ""
        if stored == PROVIDER_OPENAI_CODEX and load_codex_oauth_tokens():
            return PROVIDER_OPENAI_CODEX
        if stored == PROVIDER_ANTHROPIC and _has_usable_anthropic_auth():
            return PROVIDER_ANTHROPIC
        if stored == PROVIDER_OPENROUTER and _has_openrouter_key():
            return PROVIDER_OPENROUTER
        if stored == PROVIDER_OPENCODE and _has_opencode_key():
            return PROVIDER_OPENCODE
        if stored == PROVIDER_OPENCODE_ZEN:
            return PROVIDER_OPENCODE_ZEN
    if _has_usable_anthropic_auth():
        return PROVIDER_ANTHROPIC
    if load_codex_oauth_tokens():
        return PROVIDER_OPENAI_CODEX
    if _has_openrouter_key():
        return PROVIDER_OPENROUTER
    if _has_opencode_key():
        return PROVIDER_OPENCODE
    if _has_opencode_zen_key():
        return PROVIDER_OPENCODE_ZEN
    # First run — free Harness Agent (no API key, no provider prompt).
    return PROVIDER_OPENCODE_ZEN


def _build_opencode_client() -> OpenCodeClient:
    key = load_opencode_key()
    return OpenCodeClient(api_key=key)


def _build_opencode_zen_client() -> OpenCodeClient:
    key = load_opencode_zen_key()
    return OpenCodeClient(api_key=key, base_url=f"{OPENCODE_ZEN_BASE_URL}/")


def _build_opencode_zen_client_for_model(
    model: str | None = None,
    *,
    source: str = "",
) -> OpenCodeClient:
    use_free = should_use_harness_agent_client(model, source=source)
    state.harness_agent_free = use_free
    if use_free:
        return build_harness_agent_client()
    return _build_opencode_zen_client()


def _pick_fallback_provider(*, interactive: bool = True) -> str | None:
    """Choose another provider when Codex OAuth is unavailable."""
    if load_oauth_tokens() or _has_anthropic_api_key():
        return PROVIDER_ANTHROPIC
    if _has_openrouter_key():
        return PROVIDER_OPENROUTER
    from ..constants.paths import OPENCODE_KEY_FILE
    try:
        if OPENCODE_KEY_FILE.exists() and OPENCODE_KEY_FILE.read_text().strip():
            return PROVIDER_OPENCODE
        if OPENCODE_ZEN_KEY_FILE.exists() and OPENCODE_ZEN_KEY_FILE.read_text().strip():
            return PROVIDER_OPENCODE_ZEN
    except OSError:
        pass
    # Always fall back to free Harness Agent rather than blocking startup.
    return PROVIDER_OPENCODE_ZEN


def _build_codex_client() -> CodexClient | None:
    tokens = get_fresh_codex_oauth_token()
    if not tokens:
        return None
    return CodexClient(tokens["access_token"])


def make_client(*, interactive: bool = True, _retried: bool = False):
    """Resolve provider + auth, build client, validate; handle 401 with refresh/re-auth.

    When ``interactive=False`` (TUI default), never opens Rich console login prompts.
    Returns ``None`` if credentials are missing — use ``/login`` or ``/key`` in the TUI.
    """
    state.provider = _resolve_provider(interactive=interactive)
    _secure_write(PROVIDER_FILE, state.provider)

    prev_model = state.MODEL
    if state.provider == PROVIDER_OPENCODE_ZEN and not _has_usable_provider_credentials():
        state.MODEL = HARNESS_AGENT_DEFAULT_MODEL
        state.harness_agent_free = True
    else:
        state.MODEL = normalize_model_for_provider(state.MODEL, state.provider)
        if state.provider == PROVIDER_OPENCODE_ZEN:
            state.harness_agent_free = should_use_harness_agent_client(state.MODEL)
    if state.MODEL != prev_model:
        try:
            from ..storage.prefs import save_last_model
            save_last_model()
        except Exception:
            pass

    if state.provider == PROVIDER_OPENCODE:
        if not interactive and not _has_opencode_key():
            return None
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

    if state.provider == PROVIDER_OPENCODE_ZEN:
        use_free = state.harness_agent_free or should_use_harness_agent_client(state.MODEL)
        if not interactive and not use_free and not _has_opencode_zen_key():
            return None
        for attempt in range(DEFAULT_RETRIES):
            try:
                c = _build_opencode_zen_client_for_model(state.MODEL)
                return c
            except Exception as e:
                if not use_free and ("401" in str(e) or "unauthorized" in str(e).lower()):
                    OPENCODE_ZEN_KEY_FILE.unlink(missing_ok=True)
                    prompt_for_opencode_zen_key(
                        reason="Stored OpenCode Zen key rejected. Please re-enter."
                    )
                    continue
                raise
        if use_free:
            console.print("[red]Harness Agent connection failed[/]"); sys.exit(1)
        console.print("[red]Too many OpenCode Zen auth failures[/]"); sys.exit(1)

    if state.provider == PROVIDER_OPENAI_CODEX:
        state.auth_mode = AUTH_OAUTH
        _secure_write(AUTH_MODE_FILE, AUTH_OAUTH)
        c = _build_codex_client()
        if c is None:
            if not interactive:
                return None
            console.print(
                "[yellow]OpenAI Codex OAuth not configured — run /login to sign in[/]"
            )
            if not _retried:
                fallback = _pick_fallback_provider(interactive=interactive)
                if fallback:
                    state.provider = fallback
                    _secure_write(PROVIDER_FILE, state.provider)
                    return make_client(interactive=interactive, _retried=True)
            console.print("[red]No fallback auth configured[/]")
            sys.exit(1)
        for attempt in range(DEFAULT_RETRIES):
            try:
                return c
            except Exception as e:
                if "401" in str(e) or "unauthorized" in str(e).lower():
                    from .codex_oauth_tokens import clear_codex_oauth_tokens
                    clear_codex_oauth_tokens()
                    console.print("[yellow]Codex OAuth session invalid — run /login[/]")
                    c = _build_codex_client()
                    if c is None:
                        break
                    continue
                raise
        console.print("[red]Too many OpenAI Codex auth failures[/]"); sys.exit(1)

    if state.provider == PROVIDER_OPENROUTER:
        if not interactive and not _has_openrouter_key():
            return None
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
    mode = _resolve_auth_mode(interactive=interactive)
    if mode is None:
        return None
    state.auth_mode = mode
    _secure_write(AUTH_MODE_FILE, state.auth_mode)

    for attempt in range(DEFAULT_RETRIES):
        try:
            c = _build_client_from_mode(state.auth_mode, interactive=interactive)
            c.models.list(limit=1)  # cheap validation
            if state.provider == PROVIDER_ANTHROPIC:
                sync_anthropic_model_ids(c)
            return c
        except RuntimeError:
            if not interactive:
                return None
            raise
        except APIStatusError as e:
            if e.status_code == 401:
                if state.auth_mode == AUTH_OAUTH:
                    tokens = load_oauth_tokens()
                    if tokens and oauth_refresh(tokens):
                        console.print("[dim]refreshed OAuth token, retrying…[/]")
                        continue
                    console.print("[yellow]OAuth session invalid — re-login required.[/]")
                    clear_oauth_tokens()
                    if not interactive:
                        return None
                    oauth_login()
                    continue
                else:
                    KEY_FILE.unlink(missing_ok=True)
                    if not interactive:
                        return None
                    prompt_for_key(reason="Stored Anthropic key rejected (401). Please re-enter.")
                    continue
            raise
        except APIConnectionError as e:
            console.print(f"[red]Network error: {e}[/]"); sys.exit(1)
    if not interactive:
        return None
    console.print("[red]Too many auth failures[/]"); sys.exit(1)
