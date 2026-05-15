"""Centered Anthropic Pro/Max OAuth login modal.

Walks the user through the Claude Code subscription OAuth flow:

1. Generate a PKCE verifier/challenge pair.
2. Open ``https://claude.ai/oauth/authorize`` in the browser.
3. User signs in, approves access, and lands on a page showing
   ``<code>#<state>`` (or a callback URL containing both).
4. User pastes that string into the modal's input field — the parser accepts
   bare codes, ``code#state`` pairs, and full callback URLs.
5. Modal exchanges the code for tokens, persists them, switches the active
   provider to Anthropic, and rebuilds the API client.

Dismisses with ``True`` on success, ``False`` on cancel/error.
"""

from __future__ import annotations

import threading
import time
import urllib.parse
import webbrowser
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, Static

from rich.text import Text

from .. import state
from ..auth.oauth_tokens import save_oauth_tokens, clear_oauth_tokens
from ..auth.pkce import _pkce_pair
from ..constants import (
    OAUTH_CLIENT_ID, OAUTH_AUTHORIZE_URL, OAUTH_TOKEN_URL,
    OAUTH_REDIRECT_URI, OAUTH_SCOPES,
    AUTH_OAUTH, PROVIDER_ANTHROPIC, AUTH_MODE_FILE, PROVIDER_FILE,
)
from ..constants.models import OAUTH_DEFAULT_EXPIRY
from ..utils.http import _http_json
from ..utils.io import _secure_write
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse


def _parse_code_input(raw: str, fallback_state: str) -> tuple[str, str]:
    """Extract ``(code, state)`` from any of:

    * bare code             → ``"abc123"``
    * code-with-fragment    → ``"abc123#xyz789"``
    * full callback URL     → ``"https://…/callback?code=abc&state=xyz"``
    * URL fragment form     → ``"https://…/callback#code=abc&state=xyz"``

    Returns the parsed values, falling back to ``fallback_state`` (the PKCE
    verifier) when no ``state`` is present.
    """
    s = (raw or "").strip()
    if not s:
        return "", fallback_state

    # Full URL — pull from query or fragment.
    if s.startswith(("http://", "https://")):
        try:
            parsed = urllib.parse.urlparse(s)
            qs = urllib.parse.parse_qs(parsed.query)
            frag_qs = urllib.parse.parse_qs(parsed.fragment) if parsed.fragment else {}
            code = (qs.get("code") or frag_qs.get("code") or [""])[0]
            state_val = (qs.get("state") or frag_qs.get("state") or [fallback_state])[0]
            return code.strip(), state_val.strip()
        except Exception:
            pass  # fall through to the simple parsers below

    # "code#state" form (what Anthropic's OOB page shows).
    if "#" in s:
        code, state_val = s.split("#", 1)
        return code.strip(), state_val.strip()

    return s, fallback_state


def _explain_error(status: int, body: object) -> str:
    """Turn an OAuth token-exchange failure into a one-line user-facing hint."""
    err_type = ""
    err_msg = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            err_type = str(err.get("type") or err.get("error") or "")
            err_msg = str(err.get("message") or "")
        elif isinstance(err, str):
            err_type = err
        err_msg = err_msg or str(body.get("error_description") or "")

    if status == 429 or err_type in ("rate_limit_error", "too_many_requests"):
        return (
            "Anthropic OAuth rate-limited (429). Wait ~60s, then press "
            "Ctrl+R for a fresh code and try again."
        )
    if err_type in ("invalid_grant", "expired_token") or status == 400:
        return (
            "Code expired or already used. Press Ctrl+R for a fresh code, "
            "then paste the new one."
        )
    if err_type == "invalid_client" or status in (401, 403):
        return "OAuth client rejected — make sure you signed in to the same Anthropic account."
    if status == 0:
        return f"Network error — {err_msg or 'check your connection'}"
    return f"HTTP {status}: {err_msg or err_type or body}"


class LoginModalScreen(TuiModalScreen[bool]):
    """Anthropic OAuth login. Dismisses ``True`` on success, ``False`` otherwise."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    LoginModalScreen #modal {
        width: 80%;
        max-width: 100;
        max-height: 80%;
        padding: 2 2;
    }
    LoginModalScreen #login_info {
        padding-bottom: 1;
    }
    LoginModalScreen #login_url {
        background: #0d1117;
        color: #58a6ff;
        padding: 0 1;
        border: tall #21262d;
    }
    LoginModalScreen #login_status {
        padding-top: 1;
        color: #8b949e;
    }
    LoginModalScreen #login_status.ok  { color: #3fb950; }
    LoginModalScreen #login_status.err { color: #f85149; }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "submit", "Submit", show=True),
        Binding("ctrl+o", "open_browser", "Re-open browser", show=True),
        Binding("ctrl+r", "fresh_code", "Fresh code", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._verifier: str = ""
        self._challenge: str = ""
        self._auth_url: str = ""
        self._busy: bool = False

    # ── lifecycle ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("🔐  Log in with Claude Pro / Max", id="modal_title")
                yield Static("", id="login_info")
                yield Static("", id="login_url")
                yield Static(
                    Text.from_markup(
                        "Paste the code shown after sign-in "
                        "[dim](looks like[/] [cyan]abc123#xyz789[/][dim])[/]:"
                    ),
                    id="login_prompt",
                )
                yield Input(
                    placeholder="<code>#<state>",
                    id="login_code",
                    password=False,
                )
                yield Static("", id="login_status")
                yield Static(
                    "Ctrl+S submit • Ctrl+O re-open browser • Ctrl+R fresh code • Esc cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._regenerate_pkce()

        self.query_one("#login_info", Static).update(
            Text.from_markup(
                "Use your Anthropic [bold]Claude Pro[/] or [bold]Max[/] subscription "
                "to drive Jarvis. Your browser will open the Anthropic sign-in page; "
                "after approving, copy the code Anthropic shows and paste it below.\n"
            )
        )
        self.query_one("#login_url", Static).update(Text(self._auth_url, style="blue"))

        # Auto-open the browser; user can re-open via Ctrl+O.
        try:
            webbrowser.open(self._auth_url)
            self._set_status("browser opened — sign in, then paste the code above", ok=None)
        except Exception:
            self._set_status(
                "couldn't open a browser — copy the URL above into one manually", ok=None
            )

        self.query_one("#login_code", Input).focus()

    def _regenerate_pkce(self) -> None:
        """Create a fresh PKCE pair + authorize URL (used on mount and Ctrl+R)."""
        self._verifier, self._challenge = _pkce_pair()
        params = {
            "code": "true",
            "client_id": OAUTH_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": OAUTH_REDIRECT_URI,
            "scope": OAUTH_SCOPES,
            "code_challenge": self._challenge,
            "code_challenge_method": "S256",
            "state": self._verifier,
        }
        self._auth_url = OAUTH_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)

    def on_unmount(self) -> None:
        disable_mouse()

    # ── helpers ──────────────────────────────────────────────────────────

    def _set_status(self, msg: str, *, ok: Optional[bool]) -> None:
        widget = self.query_one("#login_status", Static)
        widget.update(Text(msg, style="bold green" if ok is True else "bold red" if ok is False else "dim"))
        widget.set_class(ok is True, "ok")
        widget.set_class(ok is False, "err")

    # ── actions ──────────────────────────────────────────────────────────

    def action_cancel(self) -> None:
        if self._busy:
            return
        self.dismiss(False)

    def action_open_browser(self) -> None:
        try:
            webbrowser.open(self._auth_url)
        except Exception:
            pass

    def action_fresh_code(self) -> None:
        """Regenerate PKCE + URL and reopen the browser without leaving the modal."""
        if self._busy:
            return
        self._regenerate_pkce()
        self.query_one("#login_url", Static).update(Text(self._auth_url, style="blue"))
        self.query_one("#login_code", Input).value = ""
        try:
            webbrowser.open(self._auth_url)
            self._set_status(
                "fresh code requested — browser reopened, sign in again", ok=None
            )
        except Exception:
            self._set_status(
                "fresh code requested — copy the new URL above into a browser", ok=None
            )

    def action_submit(self) -> None:
        if self._busy:
            return
        raw = (self.query_one("#login_code", Input).value or "").strip()
        if not raw:
            self._set_status("paste the code first", ok=False)
            return

        code, returned_state = _parse_code_input(raw, fallback_state=self._verifier)
        if not code:
            self._set_status(
                "couldn't extract a code from that input — paste the value, "
                "not the page text",
                ok=False,
            )
            return

        self._busy = True
        self._set_status("exchanging code for tokens…", ok=None)

        def _worker() -> None:
            try:
                status, body = _http_json(OAUTH_TOKEN_URL, {
                    "grant_type": "authorization_code",
                    "code": code,
                    "state": returned_state,
                    "client_id": OAUTH_CLIENT_ID,
                    "redirect_uri": OAUTH_REDIRECT_URI,
                    "code_verifier": self._verifier,
                })
            except Exception as e:
                self.app.call_from_thread(self._on_exchange_done, None, 0, str(e))
                return
            if status != 200 or not isinstance(body, dict) or "access_token" not in body:
                self.app.call_from_thread(self._on_exchange_done, None, status, body)
                return
            self.app.call_from_thread(self._on_exchange_done, body, status, None)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_exchange_done(
        self, body: Optional[dict], status: int, err: object
    ) -> None:
        if err is not None or not body:
            self._busy = False
            hint = _explain_error(status, err)
            self._set_status(hint, ok=False)
            self.query_one("#login_code", Input).focus()
            return

        expires_in = int(body.get("expires_in") or OAUTH_DEFAULT_EXPIRY)
        raw_scope = body.get("scope", OAUTH_SCOPES)
        scopes = raw_scope.split() if isinstance(raw_scope, str) else (raw_scope or [])
        tokens = {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token", ""),
            "expires_at": int(time.time()) + expires_in,
            "scopes": scopes,
        }
        save_oauth_tokens(tokens)

        # Switch provider + auth mode + rebuild client.
        state.provider = PROVIDER_ANTHROPIC
        state.auth_mode = AUTH_OAUTH
        try:
            _secure_write(PROVIDER_FILE, state.provider)
            _secure_write(AUTH_MODE_FILE, state.auth_mode)
        except Exception:
            pass

        try:
            from ..auth.client import _build_client_from_mode
            state.client = _build_client_from_mode(AUTH_OAUTH)
            # Light validation — list models is cheap and confirms the token.
            state.client.models.list(limit=1)
        except Exception as e:
            self._busy = False
            self._set_status(f"tokens saved but client build failed — {e}", ok=False)
            return

        self._set_status(
            "✓ logged in — provider switched to Anthropic, OAuth client active",
            ok=True,
        )
        # Give the user a half-second to read the success line.
        self.set_timer(0.6, lambda: self.dismiss(True))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "login_code":
            self.action_submit()
