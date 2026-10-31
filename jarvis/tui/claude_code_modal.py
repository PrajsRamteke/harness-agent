"""``/claude-code`` — Claude through the user's own Claude Code CLI.

A checklist (installed · new enough · SDK · signed in), what it means for the
account, and the one thing to do next. Signing in hands this terminal to the
official ``claude auth login`` (``App.suspend``): the user talks to Anthropic's
own sign-in directly and Jarvis never sees any of it.

Dismisses ``"use"`` when the user picks it, else ``None``.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import webbrowser

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Horizontal, Vertical
from textual.widgets import Button, Static

from .. import state
from ..auth import claude_agent as ca
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, hint_line
from .mouse_toggle import disable_mouse, enable_mouse
from . import theme as ui

USE = "use"


def _check(ok: bool | None) -> str:
    if ok is None:
        return f"[{ui.FG_DIM}]·[/]"
    return f"[{ui.OK}]✓[/]" if ok else f"[{ui.WARN}]✗[/]"


class ClaudeCodeScreen(TuiModalScreen[str | None]):
    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ClaudeCodeScreen #modal { width: 80%; max-width: 104; max-height: 86%; }
    ClaudeCodeScreen #cc_body { padding: 0 1; height: auto; }
    ClaudeCodeScreen #cc_status { padding: 0 1; height: auto; margin-top: 1; color: {ui.FG_MUTE}; }
    ClaudeCodeScreen #cc_buttons { height: 1; margin-top: 1; padding: 0 1; }
    ClaudeCodeScreen #cc_buttons Button { margin: 0 1 0 0; }
    """
    )
    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("enter", "primary", "Use", show=False),
        Binding("l", "login", "Sign in", show=False),
        Binding("r", "recheck", "Recheck", show=False),
        Binding("i", "install_sdk", "Install SDK", show=False),
        Binding("o", "docs", "Docs", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._busy = False

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(f"◆  Claude Code CLI   [{ui.FG_DIM}]your own signed-in claude[/]", id="modal_title")
                yield Static("", id="cc_body")
                yield Static("", id="cc_status")
                with Horizontal(id="cc_buttons"):
                    yield Button("Use Claude Code", id="cc_use", variant="primary", compact=True)
                    yield Button("Sign in", id="cc_login", compact=True)
                    yield Button("Install SDK", id="cc_sdk", compact=True)
                    yield Button("Recheck", id="cc_recheck", compact=True)
                    yield Button("Setup guide ↗", id="cc_docs", compact=True)
                yield Static(
                    hint_line(("↵", "use"), ("l", "sign in"), ("r", "recheck"), ("i", "install SDK"),
                              ("o", "guide"), ("esc", "close")),
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._paint(ca.status())
        self._recheck(quiet=True)

    def on_unmount(self) -> None:
        disable_mouse()

    # ── painting ──

    def _paint(self, s: ca.Status) -> None:
        code, msg, fix = s.problem()
        checked = bool(s.checked_at)
        rows = [
            f"{_check(bool(s.cli_path) and (s.cli_ok if s.cli_version else None))} "
            f"[bold]Claude Code[/] {escape(s.cli_version or '')}  "
            f"[{ui.FG_DIM}]{escape(s.cli_path or 'not found')}[/]",
            f"{_check(bool(s.sdk_version))} [bold]Claude Agent SDK[/] {escape(s.sdk_version or '')}  "
            f"[{ui.FG_DIM}]Python package Jarvis drives it with[/]",
            f"{_check(s.logged_in if checked else None)} [bold]Signed in[/]  "
            f"[{ui.FG_DIM}]{escape(s.account_line() or ('not checked yet' if not checked else 'no'))}[/]",
        ]
        if s.ready:
            names = " · ".join(m["id"] for m in ca.models()[:8])
            rows.append(f"\n[{ui.FG_DIM}]Models:[/] {escape(names)}")
        if code:
            rows.append(f"\n[{ui.WARN}]{escape(msg)}[/]")
            if fix:
                rows.append(f"Run: [bold {ui.ACCENT}]{escape(fix)}[/]"
                            + (f"  [{ui.FG_DIM}]— or press l to do it here[/]" if code == "signed_out" else ""))
        rows.append(
            f"\n[{ui.FG_DIM}]Claude Code itself sends every request to Anthropic, signed in with your "
            "account (Pro / Max / Team plan or API key). Jarvis never reads or stores that login, adds "
            "no headers and doesn't modify the binary — it only provides its tools and shows the replies. "
            "Usage counts against your plan like normal Claude Code use.[/]"
        )
        if state.provider == "claude_agent":
            rows.append(f"[{ui.OK}]● In use[/] [{ui.FG_DIM}]· model {escape(state.MODEL)}[/]")
        self.query_one("#cc_body", Static).update("\n".join(rows))
        use = self.query_one("#cc_use", Button)
        use.disabled = not s.ready or state.provider == "claude_agent"
        use.label = "In use" if state.provider == "claude_agent" else "Use Claude Code"
        self.query_one("#cc_login", Button).display = bool(s.cli_ok) and not s.logged_in
        self.query_one("#cc_sdk", Button).display = not s.sdk_version

    def _say(self, text: str, *, err: bool = False) -> None:
        color = ui.ERR if err else ui.FG_MUTE
        self.query_one("#cc_status", Static).update(f"[{color}]{escape(text)}[/]" if text else "")

    # ── actions ──

    def _recheck(self, *, quiet: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        if not quiet:
            self._say("Asking Claude Code…")

        def work() -> None:
            try:
                s = ca.refresh()
                if s.ready and not ca.models_are_live():
                    ca.refresh_models()
            except Exception as e:  # noqa: BLE001
                self.app.call_from_thread(self._done_check, None, str(e))
                return
            self.app.call_from_thread(self._done_check, s, "")

        threading.Thread(target=work, daemon=True, name="jarvis-claude-code-check").start()

    def _done_check(self, s: ca.Status | None, err: str) -> None:
        self._busy = False
        if s is None:
            self._say(err or "Couldn't ask Claude Code.", err=True)
            return
        self._paint(s)
        self._say("Ready." if s.ready else "")

    def action_primary(self) -> None:
        s = ca.status()
        if s.ready and state.provider != "claude_agent":
            self.dismiss(USE)
        elif s.cli_ok and not s.logged_in:
            self.action_login()
        elif not s.sdk_version:
            self.action_install_sdk()

    def action_login(self) -> None:
        s = ca.status()
        if not s.cli_path:
            self._say(f"Install Claude Code first: {ca.CLI_INSTALL_CMD}", err=True)
            return
        if getattr(state, "headless", False):
            self._say(f"Run `{ca.LOGIN_CMD}` in a terminal on this computer, then Recheck.")
            return
        cmd = [s.cli_path, "auth", "login"]
        try:
            # The official sign-in, in this terminal: Jarvis steps aside and
            # never sees what's typed or shown.
            with self.app.suspend():
                print(f"\n── Claude Code sign-in (Anthropic's own flow; Jarvis is paused) ──\n", flush=True)
                subprocess.run(cmd, env=ca._probe_env())
                print("\nBack to Jarvis…", flush=True)
        except Exception as e:  # noqa: BLE001 — e.g. suspend unsupported here
            self._say(f"Couldn't start it here ({e}). Run `{ca.LOGIN_CMD}` in a terminal, then Recheck.",
                      err=True)
            return
        self._recheck()

    def action_install_sdk(self) -> None:
        if ca.sdk_version() or self._busy:
            self._recheck()
            return
        self._busy = True
        self._say(f"Installing the SDK: {ca.SDK_INSTALL_CMD} …")

        def work() -> None:
            try:
                p = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--no-binary", ca.SDK_PACKAGE, ca.SDK_PACKAGE],
                    capture_output=True, text=True, timeout=600,
                )
                ok, out = p.returncode == 0, (p.stderr or p.stdout or "").strip().splitlines()[-1:] or [""]
            except Exception as e:  # noqa: BLE001
                ok, out = False, [str(e)]
            self.app.call_from_thread(self._installed, ok, out[0])

        threading.Thread(target=work, daemon=True, name="jarvis-claude-sdk-install").start()

    def _installed(self, ok: bool, line: str) -> None:
        self._busy = False
        if not ok:
            self._say(f"Install failed: {line}", err=True)
            return
        self._say("SDK installed.")
        self._recheck(quiet=True)

    def action_recheck(self) -> None:
        self._recheck()

    def action_docs(self) -> None:
        try:
            webbrowser.open(ca.CLI_DOCS_URL)
        except Exception:
            self._say(ca.CLI_DOCS_URL)

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        handlers = {
            "cc_use": lambda: self.dismiss(USE),
            "cc_login": self.action_login,
            "cc_sdk": self.action_install_sdk,
            "cc_recheck": self.action_recheck,
            "cc_docs": self.action_docs,
        }
        fn = handlers.get(event.button.id or "")
        if fn:
            fn()
