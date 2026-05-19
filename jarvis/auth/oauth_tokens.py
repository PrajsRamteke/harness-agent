"""OAuth token persistence & refresh."""
import json, time, urllib.parse
from typing import Optional

from ..console import console
from ..constants import (
    OAUTH_FILE, OAUTH_TOKEN_URL, OAUTH_CLIENT_ID, OAUTH_SCOPES,
    OAUTH_REDIRECT_URI, OAUTH_TOKEN_USER_AGENT,
)
from ..constants.models import OAUTH_DEFAULT_EXPIRY, OAUTH_EXPIRY_BUFFER
from ..utils.io import _secure_write
from ..utils.http import _http_json


def parse_oauth_code(raw: str) -> str:
    """Extract the authorization code from paste input or a callback URL."""
    return parse_oauth_paste(raw)[0]


def parse_oauth_paste(raw: str, fallback_state: str = "") -> tuple[str, str]:
    """Return ``(code, state)`` from paste input or a callback URL."""
    s = (raw or "").strip()
    if not s:
        return "", fallback_state
    if s.startswith(("http://", "https://")):
        try:
            parsed = urllib.parse.urlparse(s)
            qs = urllib.parse.parse_qs(parsed.query)
            frag_qs = urllib.parse.parse_qs(parsed.fragment) if parsed.fragment else {}
            code = ((qs.get("code") or frag_qs.get("code") or [""])[0]).strip()
            oauth_state = (
                (qs.get("state") or frag_qs.get("state") or [fallback_state])[0]
            ).strip()
            return code, oauth_state or fallback_state
        except Exception:
            pass
    if "#" in s:
        code, oauth_state = s.split("#", 1)
        code = code.strip()
        oauth_state = oauth_state.strip() or fallback_state
        return code, oauth_state
    return s, fallback_state


def exchange_oauth_code(code: str, verifier: str, state: str) -> tuple[int, object]:
    """Exchange an authorization code for tokens. Returns (status, body)."""
    cleaned = parse_oauth_code(code) if ("#" in code or code.startswith("http")) else code.strip()
    if not cleaned or not state:
        return 400, {
            "error": "invalid_request",
            "error_description": "Missing authorization code or OAuth state",
        }
    return _http_json(
        OAUTH_TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": cleaned,
            "state": state,
            "client_id": OAUTH_CLIENT_ID,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "code_verifier": verifier,
        },
        user_agent=OAUTH_TOKEN_USER_AGENT,
    )


def oauth_client_headers() -> dict[str, str]:
    """Default HTTP headers for Anthropic API calls authenticated via OAuth."""
    from ..constants import OAUTH_BETA_HEADER, OAUTH_USER_AGENT

    return {
        "anthropic-beta": OAUTH_BETA_HEADER,
        "anthropic-dangerous-direct-browser-access": "true",
        "x-app": "cli",
        "User-Agent": OAUTH_USER_AGENT,
    }


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
    try:
        from .. import state
        state.anthropic_model_ids = None
    except Exception:
        pass


def oauth_refresh(tokens: dict) -> Optional[dict]:
    """Refresh access token using refresh_token. Returns new token dict or None on failure."""
    if not tokens.get("refresh_token"):
        return None
    status, body = _http_json(OAUTH_TOKEN_URL, {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": OAUTH_CLIENT_ID,
    }, user_agent=OAUTH_TOKEN_USER_AGENT)
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
