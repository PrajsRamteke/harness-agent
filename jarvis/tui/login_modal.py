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
from ..auth.oauth_tokens import save_oauth_tokens, clear_oauth_tokens, exchange_oauth_code, parse_oauth_paste
from ..auth.anthropic_models import sync_anthropic_model_ids
from ..auth.pkce import _pkce_pair
from ..constants import (
    OAUTH_CLIENT_ID, OAUTH_AUTHORIZE_URL,
    OAUTH_REDIRECT_URI, OAUTH_SCOPES,
    AUTH_OAUTH, PROVIDER_ANTHROPIC, AUTH_MODE_FILE, PROVIDER_FILE,
)
from ..constants.models import OAUTH_DEFAULT_EXPIRY
from ..utils.io import _secure_write
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


def _parse_code_input(raw: str, fallback_state: str) -> tuple[str, str]:
    """Extract ``(code, state)`` from paste input or a callback URL."""
    return parse_oauth_paste(raw, fallback_state)


def _explain_error(status: int, body: object) -> str:
    """Turn an OAuth token-exchange failure into a one-line user-facing hint."""
    err_type = ""
    err_msg = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            err_type = str(err.get("type") or err.get("error") or "")
            err_msg = str(err.get("message") or err.get("error_description") or "")
        elif isinstance(err, str):
            err_type = err
        err_msg = err_msg or str(body.get("error_description") or body.get("message") or "")

    if status == 429 or err_type in ("rate_limit_error", "too_many_requests"):
        return (
            "Anthropic OAuth rate-limited (429). Wait ~60s, then press "
            "Ctrl+R for a fresh code and try again."
        )
    if err_type in ("invalid_grant", "expired_token"):
        return (
            "Code expired or already used. Press Ctrl+R for a fresh code, "
            "then paste the new one."
        )
    if err_type in ("invalid_request", "invalid_request_error") or (
        status == 400 and "invalid request" in err_msg.lower()
    ):
        return (
            "OAuth request rejected — press Ctrl+R, sign in again, and paste the "
            "full code#state string."
        )
    if status == 400:
        return f"HTTP 400: {err_msg or err_type or body}"
    if err_type == "invalid_client" or status in (401, 403):
        return "OAuth client rejected — make sure you signed in to the same Anthropic account."
    if status == 0:
        return f"Network error — {err_msg or 'check your connection'}"
    return f"HTTP {status}: {err_msg or err_type or body}"


class LoginModalScreen(TuiModalScreen[list[str] | None]):
    """Anthropic OAuth login. Dismisses model ids on success, ``None`` on cancel/error."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    LoginModalScreen #modal {
        width: 80%;
        max-width: 110;
        max-height: 82%;
    }
    LoginModalScreen #login_info {
        padding: 0 1;
        margin-bottom: 1;
    }
    LoginModalScreen #login_url {
        background: {ui.BG_1};
        color: {ui.ACCENT};
        padding: 0 1;
        border: round {ui.SEP};
        margin-bottom: 1;
    }
    LoginModalScreen #login_prompt {
        padding: 0 1;
        margin-bottom: 1;
    }
    LoginModalScreen #login_status {
        padding: 0 1;
        margin-top: 1;
        color: {ui.FG_MUTE};
    }
    LoginModalScreen #login_status.ok  { color: {ui.OK}; }
    LoginModalScreen #login_status.err { color: {ui.ERR}; }
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
        self._oauth_state: str = ""
        self._auth_url: str = ""
        self._busy: bool = False

    # ── lifecycle ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(f"⬟  Log In   [{ui.FG_DIM}]Claude Pro / Max OAuth[/]", id="modal_title")
                yield Static("", id="login_info")
                yield Static("", id="login_url")
                yield Static(
                    Text.from_markup(
                        "Paste the code shown after sign-in "
                        f"[dim](looks like[/] [{ui.ACCENT}]abc123#xyz789[/][dim])[/]:"
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
                    f"[{ui.ACCENT_3}]ctrl+s[/] submit   [{ui.ACCENT_3}]ctrl+o[/] re-open browser   "
                    f"[{ui.ACCENT_3}]ctrl+r[/] fresh code   [{ui.ACCENT_3}]esc[/] cancel",
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
        self._verifier, self._challenge, self._oauth_state = _pkce_pair()
        params = {
            "code": "true",
            "client_id": OAUTH_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": OAUTH_REDIRECT_URI,
            "scope": OAUTH_SCOPES,
            "code_challenge": self._challenge,
            "code_challenge_method": "S256",
            "state": self._oauth_state,
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
        self.dismiss(None)

    def action_open_browser(self) -> None:
        """Re-open the authorize URL for the *current* PKCE session (same code)."""
        try:
            webbrowser.open(self._auth_url)
            self._set_status(
                "browser reopened — paste the code from this session (Ctrl+R for a new one)",
                ok=None,
            )
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

        code, pasted_state = _parse_code_input(raw, self._oauth_state)
        if not code:
            self._set_status(
                "couldn't extract a code from that input — paste the value, "
                "not the page text",
                ok=False,
            )
            return
        if pasted_state != self._oauth_state:
            self._set_status(
                "state mismatch — press Ctrl+R, sign in again, paste the new code#state",
                ok=False,
            )
            return

        self._busy = True
        self._set_status("exchanging code for tokens…", ok=None)

        def _worker() -> None:
            try:
                status, body = exchange_oauth_code(code, self._verifier, self._oauth_state)
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
            model_ids = sync_anthropic_model_ids(state.client)
            if not model_ids:
                state.client.models.list(limit=1)
        except Exception as e:
            self._busy = False
            self._set_status(f"tokens saved but client build failed — {e}", ok=False)
            return

        self._busy = False
        if model_ids:
            preview = ", ".join(model_ids)
            self._set_status(
                f"✓ logged in — {len(model_ids)} models: {preview}",
                ok=True,
            )
        else:
            self._set_status(
                "✓ logged in — provider switched to Anthropic, OAuth client active",
                ok=True,
            )
        # Give the user a half-second to read the success line.
        self.set_timer(0.6, lambda ids=model_ids: self._finish_login(ids))

    def _finish_login(self, model_ids: list[str]) -> None:
        self.dismiss(model_ids)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "login_code":
            self.action_submit()
