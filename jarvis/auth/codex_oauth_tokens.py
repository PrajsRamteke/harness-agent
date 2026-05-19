"""OpenAI Codex OAuth token persistence, exchange, and refresh."""
from __future__ import annotations

import base64
import json
import time
from typing import Optional

from ..constants.codex_oauth import (
    CODEX_OAUTH_CLIENT_ID,
    CODEX_OAUTH_REDIRECT_URI,
    CODEX_OAUTH_REQUESTED_API_KEY_TOKEN,
    CODEX_OAUTH_TOKEN_URL,
    CODEX_OAUTH_USER_AGENT,
)
from ..constants.models import OAUTH_EXPIRY_BUFFER
from ..constants.paths import CODEX_OAUTH_FILE
from ..utils.http import _http_form, _http_json
from ..utils.io import _secure_write


def _jwt_exp_unix(token: str) -> int:
    try:
        payload = token.split(".", 2)[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return int(data.get("exp") or 0)
    except Exception:
        return 0


def load_codex_oauth_tokens() -> Optional[dict]:
    if not CODEX_OAUTH_FILE.exists():
        return None
    try:
        data = json.loads(CODEX_OAUTH_FILE.read_text())
        if not data.get("access_token") or not data.get("refresh_token"):
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def save_codex_oauth_tokens(data: dict) -> None:
    _secure_write(CODEX_OAUTH_FILE, json.dumps(data, indent=2))


def clear_codex_oauth_tokens() -> None:
    CODEX_OAUTH_FILE.unlink(missing_ok=True)


def build_codex_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
) -> str:
    import urllib.parse

    from ..constants.codex_oauth import (
        CODEX_OAUTH_AUTHORIZE_URL,
        CODEX_OAUTH_ORIGINATOR,
        CODEX_OAUTH_SCOPES,
    )

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": CODEX_OAUTH_SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": CODEX_OAUTH_ORIGINATOR,
    }
    return CODEX_OAUTH_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


def exchange_codex_oauth_code(
    code: str,
    verifier: str,
    *,
    redirect_uri: str = CODEX_OAUTH_REDIRECT_URI,
    client_id: str = CODEX_OAUTH_CLIENT_ID,
) -> tuple[int, object]:
    if not code or not verifier:
        return 400, {"error": "invalid_request", "error_description": "missing code or verifier"}
    return _http_form(
        CODEX_OAUTH_TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        user_agent=CODEX_OAUTH_USER_AGENT,
    )


def exchange_codex_api_key(id_token: str, *, client_id: str = CODEX_OAUTH_CLIENT_ID) -> tuple[int, object]:
    if not id_token:
        return 400, {"error": "invalid_request", "error_description": "missing id_token"}
    return _http_form(
        CODEX_OAUTH_TOKEN_URL,
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "client_id": client_id,
            "requested_token": CODEX_OAUTH_REQUESTED_API_KEY_TOKEN,
            "subject_token": id_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
        },
        user_agent=CODEX_OAUTH_USER_AGENT,
    )


def codex_oauth_refresh(tokens: dict) -> Optional[dict]:
    refresh_token = tokens.get("refresh_token") or ""
    if not refresh_token:
        return None
    status, body = _http_json(
        CODEX_OAUTH_TOKEN_URL,
        {
            "client_id": CODEX_OAUTH_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        user_agent=CODEX_OAUTH_USER_AGENT,
    )
    if status != 200 or not isinstance(body, dict):
        return None
    access_token = body.get("access_token") or tokens.get("access_token")
    id_token = body.get("id_token") or tokens.get("id_token")
    new_refresh = body.get("refresh_token") or refresh_token
    if not access_token:
        return None
    updated = dict(tokens)
    updated["access_token"] = access_token
    updated["id_token"] = id_token
    updated["refresh_token"] = new_refresh
    exp = _jwt_exp_unix(access_token)
    updated["expires_at"] = exp or int(time.time()) + 3600
    save_codex_oauth_tokens(updated)
    return updated


def get_fresh_codex_oauth_token() -> Optional[dict]:
    tokens = load_codex_oauth_tokens()
    if not tokens:
        return None
    expires_at = int(tokens.get("expires_at") or 0)
    if expires_at - time.time() < OAUTH_EXPIRY_BUFFER:
        refreshed = codex_oauth_refresh(tokens)
        if not refreshed:
            return None
        tokens = refreshed
    return tokens


def persist_codex_oauth_bundle(body: dict, *, api_key: str | None = None) -> dict:
    """Normalize a token-endpoint JSON body and persist it."""
    access_token = body.get("access_token") or ""
    refresh_token = body.get("refresh_token") or ""
    id_token = body.get("id_token") or ""
    if not access_token or not refresh_token:
        raise ValueError("token response missing access_token or refresh_token")
    exp = _jwt_exp_unix(access_token) or int(time.time()) + 3600
    bundle = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": id_token,
        "openai_api_key": api_key or body.get("openai_api_key") or "",
        "expires_at": exp,
    }
    save_codex_oauth_tokens(bundle)
    return bundle
