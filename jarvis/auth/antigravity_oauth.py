"""Google Antigravity sign-in: tokens, refresh, and the Cloud Code project.

Flow (same shape as the ChatGPT sign-in): PKCE authorize URL → Google redirects
to ``localhost:51121/oauth-callback`` → the code is exchanged for an access +
refresh token → ``loadCodeAssist`` names the account's managed Cloud Code
project (``onboardUser`` provisions one the first time) → everything is saved
to ``~/.config/harness-agent/antigravity_oauth.json`` (600).

Access tokens last an hour; :func:`get_fresh_antigravity_token` refreshes them
when they are about to expire. Only the user's own token is ever sent, and
only to Google.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse
from typing import Optional

import httpx

from ..constants.antigravity_oauth import (
    antigravity_client,
    ANTIGRAVITY_API_CLIENT,
    ANTIGRAVITY_DEFAULT_PROJECT_ID,
    ANTIGRAVITY_ENDPOINTS,
    ANTIGRAVITY_LOAD_ENDPOINTS,
    ANTIGRAVITY_LOAD_USER_AGENT,
    ANTIGRAVITY_OAUTH_AUTHORIZE_URL,
    ANTIGRAVITY_CLIENT_MISSING_MSG,
    ANTIGRAVITY_OAUTH_REDIRECT_URI,
    ANTIGRAVITY_OAUTH_SCOPES,
    ANTIGRAVITY_OAUTH_TOKEN_URL,
    ANTIGRAVITY_USERINFO_URL,
)
from ..constants.models import OAUTH_EXPIRY_BUFFER
from ..constants.paths import ANTIGRAVITY_OAUTH_FILE
from ..utils.io import _secure_write

HTTP_TIMEOUT = 15.0
_refresh_lock = threading.Lock()


def platform_name() -> str:
    """``Client-Metadata`` platform: Antigravity only ships for these two."""
    return "WINDOWS" if sys.platform.startswith("win") else "MACOS"


def client_metadata() -> dict:
    return {"ideType": "ANTIGRAVITY", "platform": platform_name(), "pluginType": "GEMINI"}


# ── token file ────────────────────────────────────────────────────────────────


def load_antigravity_tokens() -> Optional[dict]:
    try:
        data = json.loads(ANTIGRAVITY_OAUTH_FILE.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("refresh_token"):
        return None
    return data


def save_antigravity_tokens(data: dict) -> None:
    _secure_write(ANTIGRAVITY_OAUTH_FILE, json.dumps(data, indent=2))


def clear_antigravity_tokens() -> None:
    ANTIGRAVITY_OAUTH_FILE.unlink(missing_ok=True)


def account_email() -> str:
    tokens = load_antigravity_tokens() or {}
    return str(tokens.get("email") or "")


# ── authorize + exchange ──────────────────────────────────────────────────────


def build_antigravity_authorize_url(*, code_challenge: str, state: str,
                                    redirect_uri: str = ANTIGRAVITY_OAUTH_REDIRECT_URI) -> str:
    client_id, _ = antigravity_client()
    if not client_id:
        raise RuntimeError(ANTIGRAVITY_CLIENT_MISSING_MSG)
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": ANTIGRAVITY_OAUTH_SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        # A refresh token is only issued with offline access + a fresh consent.
        "access_type": "offline",
        "prompt": "consent",
    }
    return ANTIGRAVITY_OAUTH_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


def _post_form(url: str, fields: dict) -> tuple[int, object]:
    try:
        resp = httpx.post(
            url, data=fields, timeout=HTTP_TIMEOUT,
            headers={"Accept": "application/json", "User-Agent": ANTIGRAVITY_LOAD_USER_AGENT},
        )
    except httpx.HTTPError as e:
        return 0, f"network error: {e}"
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, resp.text


def exchange_antigravity_code(code: str, verifier: str, *,
                              redirect_uri: str = ANTIGRAVITY_OAUTH_REDIRECT_URI) -> tuple[int, object]:
    if not code or not verifier:
        return 400, {"error": "invalid_request", "error_description": "missing code or verifier"}
    client_id, client_secret = antigravity_client()
    if not (client_id and client_secret):
        return 400, {"error": "missing_client", "error_description": ANTIGRAVITY_CLIENT_MISSING_MSG}
    return _post_form(ANTIGRAVITY_OAUTH_TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    })


def explain_token_error(status: int, body: object) -> str:
    """One line for a failed token exchange / refresh."""
    if status == 0:
        return "Couldn't reach Google. Check the internet connection."
    desc = ""
    if isinstance(body, dict):
        desc = str(body.get("error_description") or body.get("error") or "")
    elif isinstance(body, str):
        desc = body[:200]
    if "OAuth client" in desc:
        return desc
    if "invalid_grant" in desc or "expired" in desc.lower():
        return "That sign-in expired or was already used. Start again."
    return f"Sign-in failed ({status}){': ' + desc if desc else ''}"


def _fetch_email(access_token: str) -> str:
    try:
        resp = httpx.get(
            ANTIGRAVITY_USERINFO_URL, timeout=HTTP_TIMEOUT,
            headers={"Authorization": f"Bearer {access_token}", "User-Agent": ANTIGRAVITY_LOAD_USER_AGENT},
        )
        if resp.status_code == 200:
            return str(resp.json().get("email") or "")
    except (httpx.HTTPError, ValueError):
        pass
    return ""


# ── Cloud Code project ────────────────────────────────────────────────────────


def _cc_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": ANTIGRAVITY_LOAD_USER_AGENT,
        "X-Goog-Api-Client": ANTIGRAVITY_API_CLIENT,
        "Client-Metadata": json.dumps(client_metadata(), separators=(",", ":")),
    }


def _project_id(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    proj = payload.get("cloudaicompanionProject")
    if isinstance(proj, str):
        return proj
    if isinstance(proj, dict) and isinstance(proj.get("id"), str):
        return proj["id"]
    return ""


def _default_tier(payload: object) -> str:
    tiers = payload.get("allowedTiers") if isinstance(payload, dict) else None
    if not isinstance(tiers, list) or not tiers:
        return "FREE"
    for t in tiers:
        if isinstance(t, dict) and t.get("isDefault") and t.get("id"):
            return str(t["id"])
    first = tiers[0]
    return str(first.get("id") or "FREE") if isinstance(first, dict) else "FREE"


def discover_project(access_token: str, *, onboard_tries: int = 6, onboard_wait: float = 3.0) -> str:
    """The account's managed Cloud Code project (provisioned on first use);
    the shared default when Google names none."""
    metadata = client_metadata()
    load: object = None
    for base in dict.fromkeys((*ANTIGRAVITY_LOAD_ENDPOINTS, *ANTIGRAVITY_ENDPOINTS)):
        try:
            resp = httpx.post(f"{base}/v1internal:loadCodeAssist", json={"metadata": metadata},
                              headers=_cc_headers(access_token), timeout=HTTP_TIMEOUT)
        except httpx.HTTPError:
            continue
        if resp.status_code != 200:
            continue
        try:
            load = resp.json()
        except ValueError:
            continue
        pid = _project_id(load)
        if pid:
            return pid
        break
    tier = _default_tier(load)
    for base in ANTIGRAVITY_ENDPOINTS:
        for _ in range(max(1, onboard_tries)):
            try:
                resp = httpx.post(f"{base}/v1internal:onboardUser",
                                  json={"tierId": tier, "metadata": metadata},
                                  headers=_cc_headers(access_token), timeout=HTTP_TIMEOUT)
            except httpx.HTTPError:
                break
            if resp.status_code != 200:
                break
            try:
                body = resp.json()
            except ValueError:
                break
            pid = _project_id((body or {}).get("response") if isinstance(body, dict) else None)
            if isinstance(body, dict) and body.get("done") and pid:
                return pid
            time.sleep(onboard_wait)
    return ANTIGRAVITY_DEFAULT_PROJECT_ID


def persist_antigravity_bundle(body: dict, *, project_id: str | None = None,
                               email: str | None = None) -> dict:
    """Save a token-endpoint answer (+ project and email, looked up if not given)."""
    access = str(body.get("access_token") or "")
    refresh = str(body.get("refresh_token") or "")
    if not access or not refresh:
        raise ValueError("Google didn't return a refresh token — sign in again")
    try:
        expires_in = int(body.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    bundle = {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": int(time.time()) + expires_in,
        "email": email if email is not None else _fetch_email(access),
        "project_id": project_id if project_id is not None else discover_project(access),
    }
    save_antigravity_tokens(bundle)
    return bundle


def complete_antigravity_login(code: str, verifier: str) -> tuple[dict | None, str]:
    """Exchange ``code`` and save everything. ``(bundle, "")`` or ``(None, why)``."""
    status, body = exchange_antigravity_code(code, verifier)
    if status != 200 or not isinstance(body, dict) or "access_token" not in body:
        return None, explain_token_error(status, body)
    try:
        return persist_antigravity_bundle(body), ""
    except ValueError as e:
        return None, str(e)


# ── refresh ───────────────────────────────────────────────────────────────────


def antigravity_refresh(tokens: dict) -> Optional[dict]:
    """New access token from the refresh token. None when Google refuses."""
    refresh = str(tokens.get("refresh_token") or "")
    if not refresh:
        return None
    client_id, client_secret = antigravity_client()
    if not (client_id and client_secret):
        return None
    status, body = _post_form(ANTIGRAVITY_OAUTH_TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    })
    if status != 200 or not isinstance(body, dict) or not body.get("access_token"):
        return None
    updated = dict(tokens)
    updated["access_token"] = body["access_token"]
    if body.get("refresh_token"):
        updated["refresh_token"] = body["refresh_token"]
    try:
        updated["expires_at"] = int(time.time()) + int(body.get("expires_in") or 3600)
    except (TypeError, ValueError):
        updated["expires_at"] = int(time.time()) + 3600
    if not updated.get("project_id"):
        updated["project_id"] = discover_project(updated["access_token"])
    save_antigravity_tokens(updated)
    return updated


def get_fresh_antigravity_token(*, force: bool = False) -> Optional[dict]:
    """Saved tokens with an access token good for a while (refreshed if not)."""
    with _refresh_lock:
        tokens = load_antigravity_tokens()
        if not tokens:
            return None
        expires_at = int(tokens.get("expires_at") or 0)
        if force or not tokens.get("access_token") or expires_at - time.time() < OAUTH_EXPIRY_BUFFER:
            return antigravity_refresh(tokens)
        return tokens
