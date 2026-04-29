"""Interactive PKCE OAuth login flow."""
import time, urllib.parse, webbrowser
from typing import Optional

from ..console import console, Panel
from ..constants import (
    OAUTH_CLIENT_ID, OAUTH_AUTHORIZE_URL, OAUTH_TOKEN_URL,
    OAUTH_REDIRECT_URI, OAUTH_SCOPES,
)
from ..constants.models import OAUTH_DEFAULT_EXPIRY
from ..utils.http import _http_json
from .pkce import _pkce_pair
from .oauth_tokens import save_oauth_tokens


def oauth_login() -> Optional[dict]:
    """Run the PKCE authorize→paste→exchange flow. Returns token dict, or None if
    the user wants to go back / cancels / exchange fails (so the caller can offer
    a different auth mode instead of exiting)."""
    verifier, challenge = _pkce_pair()
    params = {
        "code": "true",
        "client_id": OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    url = OAUTH_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    console.print(Panel(
        "[bold]Log in with your Anthropic (Pro/Max) account[/]\n\n"
        "1. A browser window will open. Sign in and approve access.\n"
        "2. You'll land on a page showing a code like [cyan]abc123#xyz789[/].\n"
        "3. Copy the ENTIRE code (including the [cyan]#[/]) and paste it back here.\n\n"
        f"If the browser doesn't open, visit this URL manually:\n[dim]{url}[/]\n\n"
        "[dim]Type [cyan]b[/dim][dim] to go back to the auth method picker.[/]",
        title="🔐 Anthropic OAuth login", border_style="cyan",
    ))
    try: webbrowser.open(url)
    except Exception: pass

    try:
        pasted = console.input("Paste the code here (or 'b' to go back): ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]login cancelled — returning to auth picker[/]")
        return None
    if pasted.lower() in ("b", "back"):
        return None
    if not pasted:
        console.print("[yellow]no code pasted — returning to auth picker[/]")
        return None

    # The callback page shows "<code>#<state>". Split on "#" if present.
    if "#" in pasted:
        code, returned_state = pasted.split("#", 1)
    else:
        code, returned_state = pasted, verifier  # tolerate either form
    code = code.strip(); returned_state = returned_state.strip()

    status, body = _http_json(OAUTH_TOKEN_URL, {
        "grant_type": "authorization_code",
        "code": code,
        "state": returned_state,
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "code_verifier": verifier,
    })
    if status != 200 or not isinstance(body, dict) or "access_token" not in body:
        console.print(f"[red]Token exchange failed (HTTP {status}): {body}[/]")
        console.print("[yellow]Returning to auth picker — you can try API key instead.[/]")
        return None

    expires_in = int(body.get("expires_in") or OAUTH_DEFAULT_EXPIRY)
    tokens = {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": int(time.time()) + expires_in,
        "scopes": body.get("scope", OAUTH_SCOPES).split() if isinstance(body.get("scope"), str) else body.get("scope", []),
    }
    save_oauth_tokens(tokens)
    console.print("[green]✓ Logged in with Anthropic[/]")
    return tokens
