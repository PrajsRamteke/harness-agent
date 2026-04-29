"""OAuth token persistence & refresh."""
import json, time
from typing import Optional

from ..console import console
from ..constants import OAUTH_FILE, OAUTH_TOKEN_URL, OAUTH_CLIENT_ID, OAUTH_SCOPES
from ..constants.models import OAUTH_DEFAULT_EXPIRY, OAUTH_EXPIRY_BUFFER
from ..utils.io import _secure_write
from ..utils.http import _http_json


def load_oauth_tokens() -> Optional[dict]:
    if not OAUTH_FILE.exists(): return None
    try:
        data = json.loads(OAUTH_FILE.read_text())
        if not data.get("access_token") or not data.get("refresh_token"):
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def save_oauth_tokens(data: dict):
    _secure_write(OAUTH_FILE, json.dumps(data, indent=2))


def clear_oauth_tokens():
    OAUTH_FILE.unlink(missing_ok=True)


def oauth_refresh(tokens: dict) -> Optional[dict]:
    """Refresh access token using refresh_token. Returns new token dict or None on failure."""
    if not tokens.get("refresh_token"):
        return None
    status, body = _http_json(OAUTH_TOKEN_URL, {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": OAUTH_CLIENT_ID,
    })
    if status != 200 or not isinstance(body, dict) or "access_token" not in body:
        return None
    expires_in = int(body.get("expires_in") or OAUTH_DEFAULT_EXPIRY)
    new_tokens = {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token") or tokens["refresh_token"],
        "expires_at": int(time.time()) + expires_in,
        "scopes": tokens.get("scopes", []),
    }
    save_oauth_tokens(new_tokens)
    return new_tokens


def get_fresh_oauth_token() -> Optional[dict]:
    """Load tokens; refresh if within expiry buffer of expiry. Returns None if unrecoverable."""
    tokens = load_oauth_tokens()
    if not tokens: return None
    if tokens.get("expires_at", 0) - time.time() < OAUTH_EXPIRY_BUFFER:
        refreshed = oauth_refresh(tokens)
        if not refreshed:
            console.print("[yellow]OAuth token refresh failed — please log in again.[/]")
            return None
        tokens = refreshed
    return tokens
