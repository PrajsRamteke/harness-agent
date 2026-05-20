"""OpenAI Codex OAuth login modal — browser sign-in with localhost callback."""
from __future__ import annotations

import threading
import webbrowser
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Static

from rich.text import Text

from .. import state
from ..auth.codex_oauth_callback import (
    CodexOAuthCallbackError,
    pick_codex_callback_port,
    wait_for_codex_oauth_callback,
)
from ..auth.codex_oauth_tokens import (
    build_codex_authorize_url,
    exchange_codex_api_key,
    exchange_codex_oauth_code,
    persist_codex_oauth_bundle,
)
from ..auth.pkce import _pkce_pair
from ..constants import AUTH_MODE_FILE, AUTH_OAUTH, PROVIDER_FILE
from ..constants.codex_oauth import (
    CODEX_OAUTH_CALLBACK_PORT,
    CODEX_OAUTH_CLIENT_ID,
    CODEX_OAUTH_REDIRECT_URI,
)
from ..constants.providers import CODEX_DEFAULT_MODEL, CODEX_MODELS, PROVIDER_OPENAI_CODEX
from ..utils.io import _secure_write
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import disable_mouse, enable_mouse
from . import theme as ui


def _explain_codex_error(status: int, body: object) -> str:
    if isinstance(body, dict):
        desc = str(body.get("error_description") or body.get("error") or body.get("message") or "")
        if desc:
            return f"HTTP {status}: {desc}"
    if isinstance(body, str) and body:
        return f"HTTP {status}: {body}"
    return f"HTTP {status}: token exchange failed"


class CodexLoginModalScreen(TuiModalScreen[list[str] | None]):
    """OpenAI Codex / ChatGPT OAuth login. Dismisses model ids on success."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    CodexLoginModalScreen #modal {
        width: 80%;
        max-width: 110;
        max-height: 70%;
    }
    CodexLoginModalScreen #login_info,
    CodexLoginModalScreen #login_status {
        padding: 0 1;
        margin-top: 1;
        color: {ui.FG_MUTE};
    }
    CodexLoginModalScreen #login_status.ok  { color: {ui.OK}; }
    CodexLoginModalScreen #login_status.err { color: {ui.ERR}; }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+o", "open_browser", "Re-open browser", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._verifier = ""
        self._challenge = ""
        self._oauth_state = ""
        self._auth_url = ""
        self._busy = False

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(
                    f"⬟  Sign In   [{ui.FG_DIM}]OpenAI Codex · ChatGPT OAuth[/]",
                    id="modal_title",
                )
                yield Static("", id="login_info")
                yield Static("", id="login_status")
                yield Static(
                    f"[{ui.ACCENT_3}]ctrl+o[/] re-open browser   [{ui.ACCENT_3}]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._verifier, self._challenge, self._oauth_state = _pkce_pair()
        try:
            pick_codex_callback_port(CODEX_OAUTH_CALLBACK_PORT)
        except CodexOAuthCallbackError as e:
            self._set_status(str(e), ok=False)
            return
        self._auth_url = build_codex_authorize_url(
            client_id=CODEX_OAUTH_CLIENT_ID,
            redirect_uri=CODEX_OAUTH_REDIRECT_URI,
            code_challenge=self._challenge,
            state=self._oauth_state,
        )
        self.query_one("#login_info", Static).update(
            Text.from_markup(
                "Sign in with your [bold]ChatGPT / Codex[/] subscription. "
                "Your browser will open; after approving, you'll be redirected to "
                f"[{ui.ACCENT}]localhost:{CODEX_OAUTH_CALLBACK_PORT}[/] automatically.\n"
            )
        )
        self._start_flow()

    def on_unmount(self) -> None:
        disable_mouse()

    def _set_status(self, msg: str, *, ok: Optional[bool]) -> None:
        widget = self.query_one("#login_status", Static)
        widget.update(Text(msg, style="bold green" if ok is True else "bold red" if ok is False else "dim"))
        widget.set_class(ok is True, "ok")
        widget.set_class(ok is False, "err")

    def _start_flow(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._set_status("waiting for browser sign-in…", ok=None)
        try:
            webbrowser.open(self._auth_url)
        except Exception:
            self._set_status(
                f"open this URL manually:\n{self._auth_url}",
                ok=None,
            )

        def _worker() -> None:
            try:
                code, _state = wait_for_codex_oauth_callback(
                    expected_state=self._oauth_state,
                    port=CODEX_OAUTH_CALLBACK_PORT,
                    timeout=300.0,
                )
                status, body = exchange_codex_oauth_code(
                    code,
                    self._verifier,
                    redirect_uri=CODEX_OAUTH_REDIRECT_URI,
                )
                if status != 200 or not isinstance(body, dict) or "access_token" not in body:
                    self.app.call_from_thread(
                        self._on_done, None, _explain_codex_error(status, body)
                    )
                    return
                api_key = None
                id_token = body.get("id_token") or ""
                if id_token:
                    k_status, k_body = exchange_codex_api_key(id_token)
                    if k_status == 200 and isinstance(k_body, dict):
                        api_key = k_body.get("access_token")
                try:
                    persist_codex_oauth_bundle(body, api_key=api_key)
                except ValueError as e:
                    self.app.call_from_thread(self._on_done, None, str(e))
                    return
                self.app.call_from_thread(self._on_done, body, None)
            except CodexOAuthCallbackError as e:
                self.app.call_from_thread(self._on_done, None, str(e))
            except Exception as e:
                self.app.call_from_thread(self._on_done, None, str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, body: Optional[dict], err: Optional[str]) -> None:
        self._busy = False
        if err or not body:
            self._set_status(err or "sign-in failed", ok=False)
            return

        state.provider = PROVIDER_OPENAI_CODEX
        state.auth_mode = AUTH_OAUTH
        if not state.MODEL or state.MODEL.startswith("claude-"):
            state.MODEL = CODEX_DEFAULT_MODEL
        try:
            _secure_write(PROVIDER_FILE, state.provider)
            _secure_write(AUTH_MODE_FILE, state.auth_mode)
        except Exception:
            pass

        try:
            from ..auth.client import _build_codex_client
            state.client = _build_codex_client()
            if not state.client.validate():
                raise RuntimeError("client validation failed")
        except Exception as e:
            self._set_status(f"tokens saved but client build failed — {e}", ok=False)
            return

        model_ids = [m for m, _ in CODEX_MODELS]
        self._set_status(f"✓ signed in to OpenAI Codex — {len(model_ids)} models", ok=True)
        self.set_timer(0.6, lambda ids=model_ids: self._finish_login(ids))

    def _finish_login(self, model_ids: list[str]) -> None:
        self.dismiss(model_ids)

    def action_cancel(self) -> None:
        if self._busy:
            return
        self.dismiss(None)

    def action_open_browser(self) -> None:
        try:
            webbrowser.open(self._auth_url)
            self._set_status("browser reopened — complete sign-in there", ok=None)
        except Exception:
            pass
