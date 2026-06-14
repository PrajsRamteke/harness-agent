"""OAuth login modal — subscription sign-in only (/login, /logout).

API-key providers (Anthropic API, OpenRouter, OpenCode) use ``/key`` instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..auth.connect.oauth_actions import (
    activate_oauth, disconnect_oauth, is_active_oauth,
)
from ..auth.connect.oauth_status import oauth_connection_status
from ..constants.oauth_providers import OAUTH_PROVIDERS, OAuthProviderSpec, oauth_provider
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui

ID_PREFIX = "oauth:"


@dataclass
class OAuthConnectResult:
    action: Literal["connected", "disconnected", "activated", "cancelled"]
    spec_id: str = ""
    message: str = ""
    model_ids: list[str] | None = None


def _option_id(spec_id: str) -> str:
    return f"{ID_PREFIX}{spec_id}"


def _spec_from_option_id(option_id: str) -> OAuthProviderSpec | None:
    if option_id.startswith(ID_PREFIX):
        return oauth_provider(option_id[len(ID_PREFIX):])
    return None


class OAuthConnectModalScreen(TuiModalScreen[OAuthConnectResult | None]):
    """Pick an OAuth subscription provider to sign in, sign out, or activate."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    OAuthConnectModalScreen #modal {
        width: 84%;
        max-width: 110;
        max-height: 78%;
    }
    OAuthConnectModalScreen OptionList {
        height: 1fr;
        min-height: 6;
        margin-top: 1;
    }
    OAuthConnectModalScreen #oauth_status {
        padding: 0 1;
        margin-top: 1;
        color: {ui.FG_MUTE};
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("d", "disconnect", "Sign out", show=True),
        Binding("a", "activate", "Activate", show=True),
    ]

    def __init__(self, *, title: str = "Sign in") -> None:
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("", id="modal_title")
                yield Static(
                    Text.from_markup(
                        "[bold]OAuth login[/] — subscription accounts only.\n"
                        f"[dim]API keys (Anthropic billing, OpenRouter, OpenCode, Kimchi) → [/][{ui.ACCENT}]/key[/]"
                    ),
                    id="oauth_info",
                )
                yield OptionList(id="oauth_list")
                yield Static("", id="oauth_status")
                yield Static(
                    f"[{ui.ACCENT_3}]↵[/] sign in   [{ui.ACCENT_3}]d[/] sign out   "
                    f"[{ui.ACCENT_3}]a[/] activate   [{ui.ACCENT_3}]esc[/] close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._populate()
        self.query_one("#oauth_list", OptionList).focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def _highlighted(self) -> OAuthProviderSpec | None:
        opts = self.query_one("#oauth_list", OptionList)
        if opts.highlighted is None:
            return None
        try:
            opt = opts.get_option_at_index(opts.highlighted)
        except Exception:
            return None
        oid = getattr(opt, "id", None)
        return _spec_from_option_id(str(oid)) if oid else None

    def _set_status(self, msg: str, *, error: bool = False) -> None:
        color = ui.ERR if error else ui.OK if msg.startswith("✓") else ui.FG_MUTE
        self.query_one("#oauth_status", Static).update(Text(msg, style=color))

    def _populate(self) -> None:
        opts = self.query_one("#oauth_list", OptionList)
        opts.clear_options()
        signed_in = 0
        for spec in sorted(OAUTH_PROVIDERS, key=lambda p: p.sort_order):
            st = oauth_connection_status(spec)
            if st.connected:
                signed_in += 1
            active = is_active_oauth(spec)
            if not spec.available:
                marker = "  "
                status_text = "coming soon"
                status_style = ui.FG_DIM
            else:
                marker = "● " if active else ("◉ " if st.connected else "  ")
                status_style = ui.OK if st.connected else ui.FG_DIM
                status_text = st.detail
            marker_style = f"bold {ui.OK}" if active else (ui.ACCENT if st.connected else ui.FG_DIM)
            row = Text.assemble(
                (marker, marker_style),
                (f"{spec.label:<16s}", f"bold {ui.ACCENT}" if active else ui.ACCENT),
                ("OAuth login     ", ui.ACCENT_2),
                (status_text[:32], status_style),
            )
            opts.add_option(Option(row, id=_option_id(spec.id)))
        if opts.option_count:
            opts.highlighted = 0
            opts.focus()
        self.query_one("#modal_title", Static).update(
            f"⬟  {self._title}   [{ui.FG_DIM}]{signed_in} signed in[/]"
        )

    def _handle_spec(self, spec: OAuthProviderSpec) -> None:
        if not spec.available:
            self._set_status(f"{spec.label} OAuth login coming soon", error=True)
            return
        st = oauth_connection_status(spec)
        if st.connected:
            self._do_activate(spec)
        else:
            self._start_login(spec)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter (or click) signs in / activates the highlighted provider.

        The focused ``OptionList`` consumes Enter and posts this message, so the
        primary action must live here — a screen-level ``enter`` binding would be
        shadowed and only appear to "work once".
        """
        event.stop()
        oid = event.option.id
        if not oid:
            return
        spec = _spec_from_option_id(str(oid))
        if spec:
            self._handle_spec(spec)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#oauth_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#oauth_list", OptionList).action_cursor_up()

    def action_activate(self) -> None:
        spec = self._highlighted()
        if spec:
            self._do_activate(spec)

    def action_disconnect(self) -> None:
        spec = self._highlighted()
        if not spec:
            return
        ok, msg, _ = disconnect_oauth(spec)
        self._set_status(msg, error=not ok)
        if ok:
            self._populate()

    def _do_activate(self, spec: OAuthProviderSpec) -> None:
        ok, msg, model_ids = activate_oauth(spec)
        self._set_status(msg, error=not ok)
        if ok:
            self._populate()
            self.dismiss(OAuthConnectResult("activated", spec.id, msg, model_ids))

    def _start_login(self, spec: OAuthProviderSpec) -> None:
        from ..constants.oauth_providers import OAUTH_ID_ANTHROPIC, OAUTH_ID_OPENAI_CODEX

        if spec.id == OAUTH_ID_OPENAI_CODEX:
            from .codex_login_modal import CodexLoginModalScreen
            login_screen = CodexLoginModalScreen()
        elif spec.id == OAUTH_ID_ANTHROPIC:
            from .login_modal import LoginModalScreen
            login_screen = LoginModalScreen()
        else:
            self._set_status(f"{spec.label} login not implemented yet", error=True)
            return

        def after(model_ids: list[str] | None) -> None:
            if model_ids is None:
                self._set_status("sign in cancelled", error=True)
                return
            self._populate()
            self.dismiss(
                OAuthConnectResult(
                    "connected",
                    spec.id,
                    f"✓ signed in to {spec.label}",
                    model_ids,
                )
            )

        self.app.push_screen(login_screen, after)
