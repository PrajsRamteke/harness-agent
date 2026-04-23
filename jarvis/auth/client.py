"""Build an Anthropic client using the current auth mode; validate & retry."""
import os, sys

from ..console import console, Anthropic, APIStatusError, APIConnectionError
from ..constants import KEY_FILE, AUTH_MODE_FILE, OAUTH_BETA_HEADER
from ..utils.io import _secure_write
from .. import state
from .api_key import load_key, prompt_for_key
from .oauth_tokens import (
    load_oauth_tokens, clear_oauth_tokens, oauth_refresh, get_fresh_oauth_token,
)
from .oauth_flow import oauth_login
from .mode_picker import _choose_auth_mode


def _build_client_from_mode(mode: str) -> Anthropic:
    if mode == "oauth":
        tokens = get_fresh_oauth_token()
        if not tokens:
            tokens = oauth_login()
        if not tokens:
            # User backed out or OAuth failed — fall back to the picker so they
            # can switch to API key without restarting the program.
            new_mode = _choose_auth_mode()
            state.auth_mode = new_mode
            _secure_write(AUTH_MODE_FILE, new_mode)
            return _build_client_from_mode(new_mode)
        return Anthropic(
            api_key=None,
            auth_token=tokens["access_token"],
            default_headers={"anthropic-beta": OAUTH_BETA_HEADER},
        )
    return Anthropic(api_key=load_key())


def make_client() -> Anthropic:
    """Resolve auth mode, build client, validate; handle 401 with refresh/re-auth."""
    # Priority: env var API key → stored auth mode → pick interactively
    if os.getenv("ANTHROPIC_API_KEY"):
        state.auth_mode = "api_key"
    elif AUTH_MODE_FILE.exists():
        stored = AUTH_MODE_FILE.read_text().strip()
        if stored in ("api_key", "oauth"):
            state.auth_mode = stored
            if state.auth_mode == "oauth" and not load_oauth_tokens():
                pass
            elif state.auth_mode == "api_key" and not KEY_FILE.exists() and not os.getenv("ANTHROPIC_API_KEY"):
                state.auth_mode = _choose_auth_mode()
        else:
            state.auth_mode = _choose_auth_mode()
    elif KEY_FILE.exists():
        state.auth_mode = "api_key"
    elif load_oauth_tokens():
        state.auth_mode = "oauth"
    else:
        state.auth_mode = _choose_auth_mode()

    _secure_write(AUTH_MODE_FILE, state.auth_mode)

    for attempt in range(3):
        try:
            c = _build_client_from_mode(state.auth_mode)
            c.models.list(limit=1)  # cheap validation
            return c
        except APIStatusError as e:
            if e.status_code == 401:
                if state.auth_mode == "oauth":
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
                    prompt_for_key(reason="Stored key rejected (401). Please re-enter.")
                    continue
            raise
        except APIConnectionError as e:
            console.print(f"[red]Network error: {e}[/]"); sys.exit(1)
    console.print("[red]Too many auth failures[/]"); sys.exit(1)
