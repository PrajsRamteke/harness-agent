"""Google Antigravity login modal — browser sign-in with localhost callback."""
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
from ..auth.antigravity_oauth import build_antigravity_authorize_url, complete_antigravity_login
from ..auth.codex_oauth_callback import (
    CodexOAuthCallbackError,
    pick_codex_callback_port,
    wait_for_codex_oauth_callback,
)
from ..auth.pkce import _pkce_pair
from ..constants import AUTH_MODE_FILE, AUTH_OAUTH, PROVIDER_FILE
from ..constants.antigravity_oauth import (
    ANTIGRAVITY_OAUTH_CALLBACK_PATH,
    ANTIGRAVITY_OAUTH_CALLBACK_PORT,
)
from ..constants.providers import PROVIDER_ANTIGRAVITY, antigravity_models_for_picker
from ..utils.io import _secure_write
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import disable_mouse, enable_mouse
from . import theme as ui


class AntigravityLoginModalScreen(TuiModalScreen[list[str] | None]):
    """Google Antigravity OAuth login. Dismisses model ids on success."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    AntigravityLoginModalScreen #modal {
        width: 80%;
        max-width: 110;
        max-height: 70%;
    }
    AntigravityLoginModalScreen #login_info,
    AntigravityLoginModalScreen #login_status {
        padding: 0 1;
        margin-top: 1;
        color: {ui.FG_MUTE};
    }
    AntigravityLoginModalScreen #login_status.ok  { color: {ui.OK}; }
    AntigravityLoginModalScreen #login_status.err { color: {ui.ERR}; }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+o", "open_browser", "Re-open browser", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._verifier = ""
        self._oauth_state = ""
        self._auth_url = ""
        self._busy = False
        self._stop = threading.Event()
        self._exchanging = False  # past the browser: finishing can't be cancelled

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(
                    f"⊚  Sign In   [{ui.FG_DIM}]Google Antigravity · Google account[/]",
                    id="modal_title",
                )
                yield Static("", id="login_info")
                yield Static("", id="login_status")
                yield Static(
                    f"[bold {ui.FG_MUTE}]ctrl+o[/] re-open browser   [bold {ui.FG_MUTE}]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._verifier, challenge, self._oauth_state = _pkce_pair()
        try:
            pick_codex_callback_port(ANTIGRAVITY_OAUTH_CALLBACK_PORT, holder="Antigravity")
        except CodexOAuthCallbackError as e:
            self._set_status(str(e), ok=False)
            return
        try:
            self._auth_url = build_antigravity_authorize_url(
                code_challenge=challenge, state=self._oauth_state,
            )
        except RuntimeError as e:  # no OAuth client configured
            self._set_status(str(e), ok=False)
            return
        self.query_one("#login_info", Static).update(
            Text.from_markup(
                "Sign in with the [bold]Google account[/] you use for Antigravity — "
                "its Gemini and Claude models, on your Antigravity plan. Your browser will "
                "open; after approving you're sent back to "
                f"[{ui.ACCENT}]localhost:{ANTIGRAVITY_OAUTH_CALLBACK_PORT}[/] automatically.\n"
                f"[{ui.FG_DIM}]This uses Antigravity's own sign-in from outside the app — "
                "Google may not allow that for every account.[/]\n"
            )
        )
        self._start_flow()

    def on_unmount(self) -> None:
        self._stop.set()
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
            self._set_status(f"open this URL manually:\n{self._auth_url}", ok=None)

        def _worker() -> None:
            try:
                code, _state = wait_for_codex_oauth_callback(
                    expected_state=self._oauth_state,
                    port=ANTIGRAVITY_OAUTH_CALLBACK_PORT,
                    path=ANTIGRAVITY_OAUTH_CALLBACK_PATH,
                    timeout=300.0,
                    stop=self._stop,
                )
                self._exchanging = True
                self.app.call_from_thread(self._set_status, "signed in — setting up your project…", ok=None)
                bundle, err = complete_antigravity_login(code, self._verifier)
                if bundle is None:
                    self.app.call_from_thread(self._on_done, None, err)
                    return
                try:
                    from ..auth.antigravity_catalog import refresh_models
                    refresh_models()  # the account's own line-up, before the picker opens
                except Exception:
                    pass
                self.app.call_from_thread(self._on_done, bundle, None)
            except CodexOAuthCallbackError as e:
                if not self._stop.is_set():
                    self.app.call_from_thread(self._on_done, None, str(e))
            except Exception as e:
                self.app.call_from_thread(self._on_done, None, str(e))

        threading.Thread(target=_worker, daemon=True, name="jarvis-antigravity-login").start()

    def _on_done(self, bundle: Optional[dict], err: Optional[str]) -> None:
        self._busy = False
        self._exchanging = False
        if err or not bundle:
            self._set_status(err or "sign-in failed", ok=False)
            return

        state.provider = PROVIDER_ANTIGRAVITY
        state.auth_mode = AUTH_OAUTH
        try:
            _secure_write(PROVIDER_FILE, state.provider)
            _secure_write(AUTH_MODE_FILE, state.auth_mode)
        except Exception:
            pass
        try:
            from ..auth.client import _build_antigravity_client
            state.client = _build_antigravity_client()
            if state.client is None:
                raise RuntimeError("couldn't refresh the new sign-in")
        except Exception as e:
            self._set_status(f"tokens saved but client build failed — {e}", ok=False)
            return
        from ..auth.connect.oauth_actions import adopt_provider_model
        adopt_provider_model(PROVIDER_ANTIGRAVITY)

        model_ids = [m for m, _ in antigravity_models_for_picker()]
        who = f" as {bundle.get('email')}" if bundle.get("email") else ""
        self._set_status(f"✓ signed in to Google Antigravity{who} — {len(model_ids)} models", ok=True)
        self.set_timer(0.6, lambda ids=model_ids: self.dismiss(ids))

    def action_cancel(self) -> None:
        if self._exchanging:
            return
        self._stop.set()
        self.dismiss(None)

    def action_open_browser(self) -> None:
        if not self._auth_url:
            return
        try:
            webbrowser.open(self._auth_url)
            self._set_status("browser reopened — complete sign-in there", ok=None)
        except Exception:
            pass
