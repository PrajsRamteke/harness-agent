"""Add-by-link dialogs for MCP servers and skills, plus their small helpers.

* ``McpAddScreen``  — paste a link / ``npx …`` / ``claude mcp add …`` / JSON / GitHub
  link / a name like ``linear``; live preview; Project or Global; result with the
  next step (Authenticate · enter keys) right there.
* ``SkillInstallScreen`` — paste a GitHub / SKILL.md / archive link or a folder;
  the skills found are listed (multi-select); Project or Global.
* ``McpSignInScreen`` — the browser sign-in for one server (open · copy · paste).
* ``KeysScreen`` — hidden inputs for the keys a server needs.
* ``ScopeToggle`` — the Project / Global switch (←/→, Space, or click).

Everything slow (network, connecting, unpacking) runs on a worker thread.
"""
from __future__ import annotations

import pathlib
from typing import Any

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from . import theme as ui
from .modal_chrome import (
    TUI_MODAL_CHROME_CSS,
    TuiModalScreen,
    empty_row,
    hint_line,
    picker_row,
)
from .mouse_toggle import disable_mouse, enable_mouse

_SCOPE_LABELS = {"global": "Global", "project": "This project"}


def short_path(p: str | pathlib.Path) -> str:
    """``~/…`` for the home folder, ``./…`` for something under the working folder."""
    s = str(p)
    try:
        cwd = str(pathlib.Path.cwd())
    except OSError:
        cwd = ""
    if cwd and (s == cwd or s.startswith(cwd + "/")):
        return "." + s[len(cwd):]
    home = str(pathlib.Path.home())
    return "~" + s[len(home):] if s.startswith(home) else s


# ── Project / Global switch ────────────────────────────────────────────────


class ScopeToggle(Widget):
    """``[ Global ]  This project`` — focus it and press ←/→ (or click a side)."""

    DEFAULT_CSS = """
    ScopeToggle {
        width: auto;
        height: 1;
        padding: 0 1;
        background: $jv-bg-2;
    }
    ScopeToggle:focus {
        background: $jv-bg-3;
    }
    """
    can_focus = True
    BINDINGS = [
        Binding("left", "pick('global')", show=False),
        Binding("right", "pick('project')", show=False),
        Binding("space", "flip", show=False),
        Binding("g", "pick('global')", show=False),
        Binding("p", "pick('project')", show=False),
    ]

    class Changed(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, value: str = "global", *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.value = value if value in _SCOPE_LABELS else "global"
        self._spans: list[tuple[int, int, str]] = []

    def render(self) -> Text:
        t = Text(no_wrap=True)
        self._spans = []
        for i, key in enumerate(("global", "project")):
            if i:
                t.append("  ")
            label = f" {_SCOPE_LABELS[key]} "
            start = t.cell_len
            if key == self.value:
                t.append(label, style=f"bold {ui.BG_0} on {ui.ACCENT}")
            else:
                t.append(label, style=ui.FG_MUTE)
            self._spans.append((start, t.cell_len, key))
        if self.has_focus:
            t.append("  ←/→", style=ui.FG_DIM)
        return t

    def set_value(self, value: str, *, notify: bool = True) -> None:
        if value not in _SCOPE_LABELS or value == self.value:
            return
        self.value = value
        self.refresh()
        if notify:
            self.post_message(self.Changed(value))

    def action_pick(self, value: str) -> None:
        self.set_value(value)

    def action_flip(self) -> None:
        self.set_value("project" if self.value == "global" else "global")

    def on_click(self, event: Any) -> None:
        for start, end, key in self._spans:
            if start <= event.x < end:
                event.stop()
                self.set_value(key)
                self.focus()
                return


# ── shared bits ────────────────────────────────────────────────────────────


class _Busy:
    """A spinner glyph that a screen ticks while something runs in a worker."""

    def __init__(self, screen: TuiModalScreen, paint) -> None:
        self._screen = screen
        self._paint = paint
        self._timer = None
        self._i = 0
        self.label = ""

    def start(self, label: str) -> None:
        self.label = label
        if self._timer is None:
            self._timer = self._screen.set_interval(0.1, self._tick)
        self._tick()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.label = ""

    def _tick(self) -> None:
        self._i += 1
        frames = ui.SPINNER_FRAMES
        self._paint(Text.assemble((f"{frames[self._i % len(frames)]} ", ui.ACCENT), (self.label, ui.FG_MUTE)))


class _DismissMixin:
    """Timers await whatever their callback returns, and ``dismiss()`` returns an
    awaitable that must not be awaited from the screen's own handlers."""

    def _dismiss_with(self, result: Any) -> None:
        self.dismiss(result)  # type: ignore[attr-defined]


def _call(screen: TuiModalScreen, fn, *args) -> None:
    """Hop back to the UI thread from a worker (the screen may be gone by then)."""
    try:
        screen.app.call_from_thread(fn, *args)
    except Exception:
        pass


# ── keys for a server ──────────────────────────────────────────────────────


class KeysScreen(TuiModalScreen["dict[str, str] | None"]):
    """Hidden inputs for the keys a server needs. Dismisses with ``{VAR: value}`` (non-empty ones)."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    KeysScreen #modal { width: 76%; max-width: 100; }
    KeysScreen .key_label { color: {ui.FG_MUTE}; padding: 0 1; }
    """
    )
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "submit", "Save", show=True),
    ]

    def __init__(self, server: str, variables: list[str]) -> None:
        super().__init__()
        self._server = server
        self._vars = list(variables)

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(f"◈  Keys for {self._server}", id="modal_title")
                yield Static(
                    "Kept in ~/.config/harness-agent/mcp_secrets.json (private) — never written to the "
                    "server's config file, never shown to the model.",
                    id="modal_status",
                )
                for i, var in enumerate(self._vars):
                    yield Static(var, classes="key_label")
                    yield Input(password=True, placeholder="paste it here (hidden)", id=f"key_{i}")
                yield Static(hint_line(("↵", "next / save"), ("esc", "cancel")), id="modal_hint")

    def on_mount(self) -> None:
        enable_mouse()
        self.query_one("#key_0", Input).focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        idx = int((event.input.id or "key_0").split("_")[1])
        if idx + 1 < len(self._vars):
            self.query_one(f"#key_{idx + 1}", Input).focus()
        else:
            self.action_submit()

    def action_submit(self) -> None:
        values: dict[str, str] = {}
        for i, var in enumerate(self._vars):
            val = self.query_one(f"#key_{i}", Input).value.strip()
            if val:
                values[var] = val
        self.dismiss(values or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── guided setup for a marketplace server ─────────────────────────────────


def _open_url(url: str) -> bool:
    try:
        import webbrowser

        return bool(webbrowser.open(url))
    except Exception:
        return False


class ConnectSetupScreen(TuiModalScreen["dict[str, str] | None"]):
    """Everything a marketplace server needs before it can connect, on one screen.

    API-key servers (GitHub, Render…) get one field and a *Get a token* link;
    servers that only sign in through a registered app (Slack) get the numbered
    steps, a link that opens the vendor's page already filled in, the redirect
    URL to use, and the Client ID / Secret fields. Dismisses with
    ``{VAR: value}`` for the fields filled in, or ``None``.
    """

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ConnectSetupScreen #modal { width: 84%; max-width: 110; max-height: 94%; }
    ConnectSetupScreen #cs_scroll { height: auto; max-height: 100%; }
    ConnectSetupScreen #cs_why { color: {ui.FG_MUTE}; padding: 0 1; margin-bottom: 1; height: auto; }
    ConnectSetupScreen #cs_steps { padding: 0 1; height: auto; margin-bottom: 1; }
    ConnectSetupScreen #cs_link_row { height: 1; margin-bottom: 1; }
    ConnectSetupScreen #cs_link_row Button { margin: 0 1 0 0; }
    ConnectSetupScreen #cs_redirect { padding: 0 1; height: auto; margin-bottom: 1; }
    ConnectSetupScreen .cs_label { padding: 0 1; height: 1; }
    ConnectSetupScreen .cs_field { margin-bottom: 1; }
    ConnectSetupScreen #cs_note { color: {ui.WARN}; padding: 0 1; height: auto; }
    ConnectSetupScreen #cs_error { color: {ui.ERR}; padding: 0 1; height: auto; }
    ConnectSetupScreen #cs_buttons { height: 1; margin-top: 1; }
    ConnectSetupScreen #cs_buttons Button { margin: 0 1 0 0; }
    """
    )
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "submit", "Connect", show=False),
        Binding("ctrl+o", "open_link", "Open link", show=False),
    ]

    def __init__(self, item: dict[str, Any], variables: list[str] | None = None) -> None:
        super().__init__()
        self._item = item
        fields = item.get("fields") or {}
        self._vars = [v for v in (variables if variables is not None else (item.get("credentials") or list(fields))) if v]
        self._fields = {v: {"label": v, "hint": "", "secret": True, "placeholder": "", **fields.get(v, {})}
                        for v in self._vars}
        self._setup = item.get("setup") or {}
        self._link = str(self._setup.get("link") or "")

    def compose(self) -> ComposeResult:
        label = self._item.get("label") or self._item.get("id") or "server"
        app_mode = self._item.get("auth") == "app"
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(f"◈  Connect {label}", id="modal_title")
                title = self._setup.get("title") or (
                    f"{label} needs {self._fields[self._vars[0]]['label'].lower()}" if self._vars else f"Connect {label}"
                )
                why = self._setup.get("why") or (
                    "Stored privately in ~/.config/harness-agent/mcp_secrets.json — never written to a "
                    "config file, never shown to the model."
                )
                yield Static(Text.assemble((title + "\n", f"bold {ui.FG}"), (why, ui.FG_MUTE)), id="cs_why")
                steps = self._setup.get("steps") or []
                if steps:
                    from rich.table import Table

                    grid = Table.grid(padding=(0, 2, 0, 0))
                    grid.add_column(width=3, no_wrap=True)
                    grid.add_column(ratio=1)
                    for i, step in enumerate(steps, 1):
                        grid.add_row(Text(f" {i} ", style=f"bold {ui.ACCENT} reverse"), Text(step, style=ui.FG))
                    yield Static(grid, id="cs_steps")
                if self._link:
                    with Horizontal(id="cs_link_row"):
                        yield Button(f"{self._setup.get('link_label') or 'Open'} ↗", id="cs_open",
                                     variant="primary", compact=True)
                        yield Button("Copy link", id="cs_copy", compact=True)
                if app_mode and self._item.get("redirect_url"):
                    yield Static(
                        Text.assemble(("Redirect URL  ", ui.FG_DIM), (self._item["redirect_url"], ui.ACCENT),
                                      ("   already set in the pre-filled app", ui.FG_DIM)),
                        id="cs_redirect",
                    )
                for i, var in enumerate(self._vars):
                    meta = self._fields[var]
                    yield Static(
                        Text.assemble((meta["label"], f"bold {ui.FG}"),
                                      (f"   {meta['hint']}" if meta.get("hint") else "", ui.FG_DIM)),
                        classes="cs_label",
                    )
                    yield Input(
                        password=bool(meta.get("secret", True)),
                        placeholder=meta.get("placeholder") or ("paste it here (hidden)" if meta.get("secret", True) else "paste it here"),
                        id=f"cs_{i}",
                        classes="cs_field",
                    )
                if self._setup.get("note"):
                    yield Static(f"ⓘ  {self._setup['note']}", id="cs_note")
                yield Static("", id="cs_error")
                with Horizontal(id="cs_buttons"):
                    yield Button("Connect", id="cs_submit", variant="primary", compact=True)
                    yield Button("Cancel", id="cs_cancel", compact=True)
                pairs = [("↵", "next / connect")]
                if self._link:
                    pairs.append(("^o", "open link"))
                pairs.append(("esc", "cancel"))
                yield Static(hint_line(*pairs), id="modal_hint")

    def on_mount(self) -> None:
        enable_mouse()
        if self._vars:
            self.query_one("#cs_0", Input).focus()
        else:
            self.query_one("#cs_submit", Button).focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        {"cs_open": self.action_open_link, "cs_copy": self._copy_link,
         "cs_submit": self.action_submit, "cs_cancel": self.action_cancel}.get(event.button.id or "", lambda: None)()

    def action_open_link(self) -> None:
        if not self._link:
            return
        if not _open_url(self._link):
            self._copy_link()
            self.query_one("#cs_error", Static).update(
                Text("Couldn't open a browser here — the link is copied; open it on any device.", style=ui.WARN))
        self._focus_next_empty()

    def _focus_next_empty(self) -> None:
        """Back to the first field still to fill — ready for the paste."""
        for i in range(len(self._vars)):
            box = self.query_one(f"#cs_{i}", Input)
            if not box.value.strip():
                box.focus()
                return

    def _copy_link(self) -> None:
        copier = getattr(self.app, "_copy_text", None)
        if copier and self._link:
            copier(self._link)
            self.query_one("#cs_error", Static).update(Text("✓ link copied", style=ui.OK))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        idx = int((event.input.id or "cs_0").split("_")[1])
        if not event.input.value.strip():
            self.query_one("#cs_error", Static).update(
                Text(f"{self._fields[self._vars[idx]]['label']} is empty.", style=ui.ERR))
            return
        self.query_one("#cs_error", Static).update("")
        if idx + 1 < len(self._vars) and not self.query_one(f"#cs_{idx + 1}", Input).value.strip():
            self.query_one(f"#cs_{idx + 1}", Input).focus()
        else:
            self.action_submit()

    def action_submit(self) -> None:
        values: dict[str, str] = {}
        for i, var in enumerate(self._vars):
            val = self.query_one(f"#cs_{i}", Input).value.strip()
            if not val:
                self.query_one("#cs_error", Static).update(
                    Text(f"{self._fields[var]['label']} is empty.", style=ui.ERR))
                self.query_one(f"#cs_{i}", Input).focus()
                return
            values[var] = val
        self.dismiss(values)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── browser sign-in ────────────────────────────────────────────────────────


class McpSignInScreen(_DismissMixin, TuiModalScreen[bool]):
    """Sign in to one hosted MCP server. Closes itself when it connects.

    Esc closes the dialog but leaves the sign-in waiting (the bar above the
    composer carries on); *Cancel sign-in* really stops it.
    """

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    McpSignInScreen #modal { width: 78%; max-width: 104; }
    McpSignInScreen #si_link { color: {ui.ACCENT}; padding: 0 1; margin-bottom: 1; height: auto; }
    McpSignInScreen #si_buttons { height: 1; margin-top: 1; }
    McpSignInScreen #si_buttons Button { margin: 0 1 0 0; }
    """
    )
    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("o", "open_browser", "Open", show=False),
        Binding("c", "copy", "Copy", show=False),
        Binding("p", "paste", "Paste", show=False),
    ]

    def __init__(self, name: str) -> None:
        super().__init__()
        self._server = name
        self._url = ""
        self._busy: _Busy | None = None
        self._done = False

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(f"◈  Sign in to {self._server}", id="modal_title")
                yield Static("", id="modal_status")
                yield Static("", id="si_link")
                with Horizontal(id="si_buttons"):
                    yield Button("Open browser", id="si_open", variant="primary", compact=True)
                    yield Button("Copy link", id="si_copy", compact=True)
                    yield Button("Paste address", id="si_paste", compact=True)
                    yield Button("Cancel sign-in", id="si_cancel", compact=True)
                yield Static(
                    hint_line(("o", "open browser"), ("c", "copy link"), ("p", "paste address"),
                              ("esc", "close — keeps waiting")),
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._busy = _Busy(self, lambda t: self.query_one("#modal_status", Static).update(t))
        self._busy.start(f"contacting {self._server}…")
        for bid in ("#si_open", "#si_copy", "#si_paste"):
            self.query_one(bid, Button).disabled = True
        self._begin()
        self.set_interval(0.5, self._poll)

    def on_unmount(self) -> None:
        disable_mouse()
        if self._busy:
            self._busy.stop()

    @work(thread=True, group="mcp-signin")
    def _begin(self) -> None:
        from ..mcp.auth import coordinator
        from ..mcp.config import get_config
        from ..mcp.registry import mcp_registry

        cfg = get_config().get_server(self._server)
        if cfg is None:
            res: dict = {"ok": False, "error": f"no MCP server named '{self._server}'"}
        else:
            res = mcp_registry.authenticate(self._server, cfg)
            if res.get("ok") and not res.get("connected"):
                res["opened"] = coordinator.open_browser(self._server)
        _call(self, self._begun, res)

    def _begun(self, res: dict) -> None:
        if self._busy:
            self._busy.stop()
        status = self.query_one("#modal_status", Static)
        if res.get("connected"):
            self._finish(True)
            return
        if not res.get("ok"):
            status.update(Text(f"✗ {res.get('error', 'sign-in failed')}", style=ui.ERR))
            return
        self._url = res.get("url", "")
        self._remember()
        for bid in ("#si_open", "#si_copy", "#si_paste"):
            self.query_one(bid, Button).disabled = False
        if res.get("opened"):
            msg = "Approve access in the browser window that just opened. This closes when you're connected."
        else:
            msg = "Couldn't open a browser here — open the link below (or copy it) on any device."
        status.update(Text(msg, style=ui.FG))
        self.query_one("#si_link", Static).update(Text(self._url, style=ui.ACCENT))

    def _remember(self) -> None:
        opened = getattr(self.app, "_mcp_auth_opened", None)
        if opened is not None:
            opened.add(self._server)
            getattr(self.app, "_mcp_auth_flow", set()).add(self._server)

    def _poll(self) -> None:
        if self._done:
            return
        from ..mcp.auth import coordinator
        from ..mcp.registry import mcp_registry

        if mcp_registry.is_connected(self._server):
            self._finish(True)
            return
        req = coordinator.get(self._server)
        if req is not None and req.status == "error":
            self.query_one("#modal_status", Static).update(Text(f"✗ {req.message}", style=ui.ERR))
        elif req is not None and req.status == "working":
            self.query_one("#modal_status", Static).update(Text("Signing in…", style=ui.FG_MUTE))

    def _finish(self, ok: bool) -> None:
        if self._done:
            return
        self._done = True
        self.query_one("#modal_status", Static).update(Text(f"✓ Connected to {self._server}", style=ui.OK))
        self.set_timer(0.7, lambda: self._dismiss_with(ok))

    # ── actions ──────────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        {"si_open": self.action_open_browser, "si_copy": self.action_copy,
         "si_paste": self.action_paste, "si_cancel": self.action_cancel_sign_in}.get(
            event.button.id or "", lambda: None)()

    def action_open_browser(self) -> None:
        from ..mcp.auth import coordinator

        if not coordinator.open_browser(self._server):
            self.query_one("#modal_status", Static).update(
                Text("Couldn't open a browser here — copy the link instead.", style=ui.WARN)
            )

    def action_copy(self) -> None:
        if self._url:
            copier = getattr(self.app, "_copy_text", None)
            if copier:
                copier(self._url)
            self.query_one("#modal_status", Static).update(Text("✓ link copied", style=ui.OK))

    def action_paste(self) -> None:
        from .text_input_modal import TextInputScreen

        def after(text: str | None) -> None:
            if not text or not text.strip():
                return
            from ..mcp.auth import coordinator

            res = coordinator.submit(self._server, text)
            self.query_one("#modal_status", Static).update(
                Text("Signing in…", style=ui.FG_MUTE) if res.get("ok")
                else Text(f"✗ {res.get('error', 'that did not work')}", style=ui.ERR)
            )

        self.app.push_screen(
            TextInputScreen(
                title=f"Finish signing in to {self._server}",
                body="Signed in on another device? Paste the address the browser ended on (http://localhost…).",
                placeholder="http://localhost:33418/callback?code=…",
            ),
            after,
        )

    def action_cancel_sign_in(self) -> None:
        from ..mcp.auth import coordinator
        from ..mcp.registry import mcp_registry

        coordinator.cancel(self._server)
        mcp_registry.disconnect(self._server)
        opened = getattr(self.app, "_mcp_auth_opened", None)
        if opened is not None:
            opened.discard(self._server)
        self._done = True
        self.dismiss(False)

    def action_close(self) -> None:
        self._done = True
        self.dismiss(False)


# ── add an MCP server ──────────────────────────────────────────────────────


class McpAddScreen(_DismissMixin, TuiModalScreen["dict | None"]):
    """Add MCP servers from a link, command, JSON or name. Dismisses with the ``add_mcp`` result."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    McpAddScreen #modal { width: 86%; max-width: 124; max-height: 92%; }
    McpAddScreen #add_help { color: {ui.FG_MUTE}; padding: 0 1; margin-bottom: 1; height: auto; }
    McpAddScreen #add_preview { padding: 0 1; height: auto; min-height: 2; margin-bottom: 1; }
    McpAddScreen #scope_row { height: 1; margin-bottom: 1; }
    McpAddScreen #scope_row Static { width: auto; padding: 0 1; color: {ui.FG_MUTE}; }
    McpAddScreen #add_path { color: {ui.FG_DIM}; }
    McpAddScreen #add_pop_title { color: {ui.FG_DIM}; padding: 0 1; }
    McpAddScreen OptionList { height: 7; }
    McpAddScreen #add_result { padding: 0 1; height: auto; margin-top: 1; }
    McpAddScreen #add_buttons { height: 1; margin-top: 1; }
    McpAddScreen #add_buttons Button { margin: 0 1 0 0; }
    """
    )
    BINDINGS = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("ctrl+l", "flip_scope", "Scope", show=False),
    ]

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self._initial = initial
        self._gen = 0
        self._preview: dict | None = None
        self._scope_touched = False
        self._busy: _Busy | None = None
        self._adding = False
        self._result: dict | None = None
        self._debounce = None

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("▶  Add MCP server", id="modal_title")
                yield Static(
                    "Paste a link, an install command (npx … / uvx …), a `claude mcp add …` line, a JSON config, "
                    "a GitHub link — or just a name like linear.",
                    id="add_help",
                )
                yield Input(placeholder="https://mcp.linear.app/mcp   ·   npx -y @scope/server   ·   notion",
                            id="add_source")
                yield Static("", id="add_preview")
                with Horizontal(id="scope_row"):
                    yield Static("Add to")
                    yield ScopeToggle("global", id="add_scope")
                    yield Static("", id="add_path")
                yield Static("POPULAR  (↵ to fill)", id="add_pop_title")
                opts = OptionList(id="add_catalog")
                yield opts
                yield Static("", id="add_result")
                with Horizontal(id="add_buttons"):
                    yield Button("Add", id="add_go", variant="primary", compact=True)
                    yield Button("Authenticate", id="add_sign", compact=True)
                    yield Button("Enter keys", id="add_keys", compact=True)
                    yield Button("Close", id="add_close", compact=True)
                yield Static(
                    hint_line(("↵", "add"), ("tab", "scope / list"), ("←→", "project·global"), ("esc", "close")),
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        from ..mcp.catalog import CATALOG

        opts = self.query_one("#add_catalog", OptionList)
        for c in CATALOG:
            opts.add_option(Option(
                picker_row(c["label"], detail=c["desc"], right="hosted · sign-in" if c.get("url") else "runs locally",
                           icon="◈", icon_style=ui.ACCENT_3, title_width=12),
                id=c["id"],
            ))
        self._busy = _Busy(self, lambda t: self.query_one("#add_result", Static).update(t))
        for bid in ("#add_sign", "#add_keys"):
            self.query_one(bid, Button).display = False
        self._paint_path()
        inp = self.query_one("#add_source", Input)
        inp.compact = True
        if self._initial:
            inp.value = self._initial
        inp.focus()

    def on_unmount(self) -> None:
        disable_mouse()
        if self._busy:
            self._busy.stop()

    # ── scope ────────────────────────────────────────────────────────────

    def _scope(self) -> str:
        return self.query_one("#add_scope", ScopeToggle).value

    def _paint_path(self) -> None:
        from ..mcp.config import scope_path

        self.query_one("#add_path", Static).update(short_path(scope_path(self._scope())))

    def on_scope_toggle_changed(self, event: ScopeToggle.Changed) -> None:
        self._scope_touched = True
        self._paint_path()

    def action_flip_scope(self) -> None:
        self.query_one("#add_scope", ScopeToggle).action_flip()

    # ── live preview ─────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "add_source":
            return
        if self._debounce is not None:
            self._debounce.stop()
        text = event.value.strip()
        self._preview = None
        self._reset_result()
        if not text:
            self.query_one("#add_preview", Static).update("")
            return
        self._debounce = self.set_timer(0.25, lambda: self._preview_worker(text))

    @work(thread=True, exclusive=True, group="mcp-preview")
    def _preview_worker(self, text: str) -> None:
        from ..mcp.install import describe_source

        res = describe_source(text)
        _call(self, self._show_preview, text, res)

    def _show_preview(self, text: str, res: dict) -> None:
        if text != self.query_one("#add_source", Input).value.strip():
            return  # typed on since
        self._preview = res
        w = self.query_one("#add_preview", Static)
        if not res.get("ok"):
            w.update(Text(res.get("error", "not recognised"), style=ui.FG_DIM))
            return
        lines: list[Text] = []
        for s in res["servers"]:
            t = Text(no_wrap=False)
            t.append("● ", style=ui.OK)
            t.append(s["name"], style=f"bold {ui.FG}")
            t.append(f"  {s['transport']}  ", style=ui.ACCENT_3)
            t.append(s["endpoint"], style=ui.FG_MUTE)
            if s["credentials"]:
                t.append(f"   needs {', '.join(s['credentials'])}", style=ui.WARN)
            elif not s["local"]:
                t.append("   browser sign-in if it asks", style=ui.FG_DIM)
            if s["exists"]:
                t.append(f"   already in {' + '.join(s['exists'])}", style=ui.WARN)
            lines.append(t)
            for n in s["notes"]:
                lines.append(Text(f"  {n}", style=ui.FG_DIM))
        w.update(Text("\n").join(lines))
        hint = next((s["scope_hint"] for s in res["servers"] if s.get("scope_hint")), "")
        if hint == "project" and not self._scope_touched:
            self.query_one("#add_scope", ScopeToggle).set_value("project", notify=False)
            self._paint_path()

    # ── catalog ──────────────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "add_catalog" and event.option.id:
            event.stop()
            inp = self.query_one("#add_source", Input)
            inp.value = str(event.option.id)
            inp.cursor_position = len(inp.value)
            inp.focus()

    # ── add ──────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "add_source":
            event.stop()
            self.action_add()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "add_go":
            self.action_add()
        elif bid == "add_close":
            self.action_cancel()
        elif bid == "add_sign":
            self._sign_in()
        elif bid == "add_keys":
            self._enter_keys()

    def _reset_result(self) -> None:
        self._result = None
        try:
            self.query_one("#add_result", Static).update("")
            for bid in ("#add_sign", "#add_keys"):
                self.query_one(bid, Button).display = False
        except Exception:
            pass

    def action_add(self) -> None:
        text = self.query_one("#add_source", Input).value.strip()
        if not text:
            self.query_one("#add_result", Static).update(
                Text("Paste something first — or pick one from the list below.", style=ui.WARN)
            )
            return
        if self._adding:
            return
        self._adding = True
        self.query_one("#add_go", Button).disabled = True
        self._reset_result()
        assert self._busy is not None
        self._busy.start("adding and connecting…")
        self._add_worker(text, self._scope())

    @work(thread=True, group="mcp-add")
    def _add_worker(self, text: str, scope: str) -> None:
        from ..mcp.install import add_mcp

        try:
            res = add_mcp(text, scope=scope)
        except Exception as exc:  # never leave the dialog spinning
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "servers": []}
        _call(self, self._added, res)

    def _added(self, res: dict) -> None:
        self._adding = False
        if self._busy:
            self._busy.stop()
        self.query_one("#add_go", Button).disabled = False
        self._show_result(res)

    def _show_result(self, res: dict) -> None:
        self._result = res
        out = self.query_one("#add_result", Static)
        servers = res.get("servers") or []
        if not servers:
            out.update(Text(f"✗ {res.get('error', 'nothing to add')}", style=ui.ERR))
            return
        lines: list[Text] = []
        want_sign = want_keys = False
        for r in servers:
            st, nm, sc = r.get("status"), r.get("name"), r.get("scope", "")
            t = Text()
            if st == "connected":
                t.append("✓ ", style=ui.OK)
                t.append(f"{nm} ", style=f"bold {ui.FG}")
                t.append(f"({sc}) connected — {r.get('tool_count', 0)} tools", style=ui.FG_MUTE)
            elif st == "auth_required":
                want_sign = True
                t.append("◐ ", style=ui.WARN)
                t.append(f"{nm} ", style=f"bold {ui.FG}")
                t.append(f"({sc}) added — sign in to finish", style=ui.FG_MUTE)
            elif st == "needs_credentials":
                want_keys = True
                t.append("◐ ", style=ui.WARN)
                t.append(f"{nm} ", style=f"bold {ui.FG}")
                t.append(f"({sc}) added — needs {', '.join(r.get('missing', []))}", style=ui.FG_MUTE)
            elif st in ("added", "exists"):
                t.append("✓ ", style=ui.OK)
                t.append(f"{nm} ", style=f"bold {ui.FG}")
                t.append(r.get("message") or f"({sc}) added", style=ui.FG_MUTE)
            elif st == "denied":
                t.append("✗ ", style=ui.ERR)
                t.append(str(r.get("message", "declined")), style=ui.ERR)
            else:
                t.append("✗ ", style=ui.ERR)
                t.append(f"{nm} ", style=f"bold {ui.FG}")
                t.append(str(r.get("error", "failed")), style=ui.ERR)
                t.append("\n  Saved to the config — fix it and retry, or remove it in /mcp.", style=ui.FG_DIM)
            lines.append(t)
            if r.get("scope_note"):
                lines.append(Text(f"  {r['scope_note']}", style=ui.WARN))
        out.update(Text("\n").join(lines))
        sign, keys = self.query_one("#add_sign", Button), self.query_one("#add_keys", Button)
        sign.display, keys.display = want_sign, want_keys
        if want_sign:
            sign.focus()
        elif want_keys:
            keys.focus()
        elif all(r.get("status") in ("connected", "added", "exists") for r in servers):
            self.set_timer(1.0, lambda: self._dismiss_with(res))

    def _pending(self, status: str) -> list[dict]:
        return [r for r in (self._result or {}).get("servers", []) if r.get("status") == status]

    def _sign_in(self) -> None:
        pend = self._pending("auth_required")
        if not pend:
            return
        name = pend[0]["name"]

        def after(ok: bool | None) -> None:
            if ok:
                self.dismiss(self._result)

        self.app.push_screen(McpSignInScreen(name), after)

    def _enter_keys(self) -> None:
        pend = self._pending("needs_credentials")
        if not pend:
            return
        name, missing = pend[0]["name"], list(pend[0].get("missing", []))

        def after(values: dict | None) -> None:
            if not values:
                return
            assert self._busy is not None
            self._busy.start(f"saving keys and connecting {name}…")
            self._keys_worker(name, values)

        self.app.push_screen(KeysScreen(name, missing), after)

    @work(thread=True, group="mcp-keys")
    def _keys_worker(self, name: str, values: dict) -> None:
        from ..mcp.install import set_credentials

        try:
            res = set_credentials(name, values)
        except Exception as exc:
            res = {"ok": False, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        _call(self, self._keys_done, name, res)

    def _keys_done(self, name: str, res: dict) -> None:
        if self._busy:
            self._busy.stop()
        prev = (self._result or {}).get("servers", [])
        merged = []
        for r in prev:
            if r.get("name") == name:
                r = {**r, **{k: v for k, v in res.items() if k != "ok"}, "name": name}
            merged.append(r)
        self._show_result({**(self._result or {}), "servers": merged})

    def action_cancel(self) -> None:
        self.dismiss(self._result if self._result and self._result.get("servers") else None)


# ── install skills ─────────────────────────────────────────────────────────


class SkillInstallScreen(_DismissMixin, TuiModalScreen["dict | None"]):
    """Install skills from a link. Dismisses with the ``install_skills`` result (or None)."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    SkillInstallScreen #modal { width: 88%; max-width: 128; max-height: 92%; }
    SkillInstallScreen #sk_help { color: {ui.FG_MUTE}; padding: 0 1; margin-bottom: 1; height: auto; }
    SkillInstallScreen #sk_quick { height: 1; margin-bottom: 1; }
    SkillInstallScreen #sk_quick Static { width: auto; padding: 0 1; color: {ui.FG_DIM}; }
    SkillInstallScreen #sk_scope_row { height: 1; margin-bottom: 1; }
    SkillInstallScreen #sk_scope_row Static { width: auto; padding: 0 1; color: {ui.FG_MUTE}; }
    SkillInstallScreen #sk_path { color: {ui.FG_DIM}; }
    SkillInstallScreen #sk_found { height: 10; }
    SkillInstallScreen #sk_result { padding: 0 1; height: auto; margin-top: 1; }
    SkillInstallScreen #sk_buttons { height: 1; margin-top: 1; }
    SkillInstallScreen #sk_buttons Button { margin: 0 1 0 0; }
    """
    )
    BINDINGS = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("ctrl+l", "flip_scope", "Scope", show=False),
        Binding("space", "toggle_row", "Select", show=False),
        Binding("A", "toggle_all", "All", show=False),
    ]

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self._initial = initial
        self._info: dict | None = None
        self._text_for_info = ""
        self._selected: set[str] = set()
        self._busy: _Busy | None = None
        self._installing = False
        self._debounce = None

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("★  Install a skill", id="modal_title")
                yield Static(
                    "Paste a GitHub link (repo or folder), owner/repo, a link to a SKILL.md, a .zip/.tar.gz, or a folder path.",
                    id="sk_help",
                )
                yield Input(placeholder="https://github.com/anthropics/skills   ·   owner/repo@skill   ·   ~/my-skills",
                            id="sk_source")
                with Horizontal(id="sk_quick"):
                    yield Static("Try")
                    yield Button("anthropics/skills", id="sk_try_anthropic", compact=True)
                with Horizontal(id="sk_scope_row"):
                    yield Static("Install to")
                    yield ScopeToggle("global", id="sk_scope")
                    yield Static("", id="sk_path")
                yield OptionList(id="sk_found")
                yield Static("", id="sk_result")
                with Horizontal(id="sk_buttons"):
                    yield Button("Install", id="sk_go", variant="primary", compact=True)
                    yield Button("Close", id="sk_close", compact=True)
                yield Static(
                    hint_line(("↵", "look / install"), ("space", "select"), ("A", "all"),
                              ("←→", "project·global"), ("esc", "close")),
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._busy = _Busy(self, lambda t: self.query_one("#sk_result", Static).update(t))
        self._paint_path()
        self._paint_list()
        inp = self.query_one("#sk_source", Input)
        inp.compact = True
        if self._initial:
            inp.value = self._initial
        inp.focus()

    def on_unmount(self) -> None:
        disable_mouse()
        if self._busy:
            self._busy.stop()

    # ── scope ────────────────────────────────────────────────────────────

    def _scope(self) -> str:
        return self.query_one("#sk_scope", ScopeToggle).value

    def _paint_path(self) -> None:
        from ..storage import skill_install as si

        self.query_one("#sk_path", Static).update(short_path(si.scope_root(self._scope())))

    def on_scope_toggle_changed(self, event: ScopeToggle.Changed) -> None:
        self._paint_path()

    def action_flip_scope(self) -> None:
        self.query_one("#sk_scope", ScopeToggle).action_flip()

    # ── looking inside the link ──────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "sk_source":
            return
        if self._debounce is not None:
            self._debounce.stop()
        text = event.value.strip()
        if text != self._text_for_info:
            self._info, self._selected = None, set()
            self._paint_list()
            self.query_one("#sk_result", Static).update("")
        if len(text) >= 8 and ("/" in text or text.startswith(("~", "."))):
            self._debounce = self.set_timer(0.8, lambda: self._inspect(text))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "sk_source":
            return
        event.stop()
        text = event.value.strip()
        if not text:
            return
        if self._info and self._info.get("ok") and self._text_for_info == text and self._selected:
            self.action_install()
        else:
            self._inspect(text, then_install=True)

    def _inspect(self, text: str, then_install: bool = False) -> None:
        if self._installing or (self._info is not None and self._text_for_info == text and not then_install):
            return
        assert self._busy is not None
        self._busy.start("looking inside…")
        self._inspect_worker(text, then_install)

    @work(thread=True, exclusive=True, group="skill-inspect")
    def _inspect_worker(self, text: str, then_install: bool) -> None:
        from ..storage import skill_install as si

        try:
            res = si.inspect_source(text)
        except Exception as exc:
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _call(self, self._inspected, text, res, then_install)

    def _inspected(self, text: str, res: dict, then_install: bool) -> None:
        if text != self.query_one("#sk_source", Input).value.strip():
            return
        if self._busy:
            self._busy.stop()
        self._info, self._text_for_info = res, text
        out = self.query_one("#sk_result", Static)
        if not res.get("ok"):
            self._selected = set()
            self._paint_list()
            out.update(Text(f"✗ {res.get('error', 'could not read that')}", style=ui.ERR))
            return
        usable = [s for s in res["skills"] if s.get("usable")]
        # One skill (or one named in the link) is ready to go; several: pick.
        self._selected = {s["name"] for s in usable} if len(usable) == 1 else set()
        self._paint_list()
        n = len(res["skills"])
        out.update(Text(
            f"{res['label']} — {n} skill{'s' if n != 1 else ''}"
            + ("" if n == 1 else "  ·  space selects, A selects all, ↵ installs"),
            style=ui.FG_MUTE,
        ))
        if then_install and self._selected:
            self.action_install()
        elif n > 1:
            self.query_one("#sk_found", OptionList).focus()

    # ── list ─────────────────────────────────────────────────────────────

    def _row(self, s: dict) -> Option:
        mark = "☑" if s["name"] in self._selected else "☐"
        right = f"installed · {s['installed']}" if s.get("installed") else (
            f"{s['files']} files" + (" · ⚠ " + s["problems"][0] if s.get("problems") else "")
        )
        return Option(
            picker_row(s["name"], detail=s.get("description", "").replace("\n", " "), right=right,
                       icon=mark, icon_style=ui.ACCENT if s["name"] in self._selected else ui.FG_DIM,
                       title_width=22, right_style=ui.WARN if s.get("installed") else ui.FG_DIM),
            id=s["name"], disabled=not s.get("usable", True),
        )

    def _paint_list(self) -> None:
        opts = self.query_one("#sk_found", OptionList)
        keep = opts.highlighted
        opts.clear_options()
        if not self._info or not self._info.get("ok"):
            opts.add_option(empty_row("Skills found at the link show up here."))
            return
        for s in self._info["skills"]:
            opts.add_option(self._row(s))
        if keep is not None and keep < opts.option_count:
            opts.highlighted = keep
        elif opts.option_count:
            opts.highlighted = 0

    def _current(self) -> str | None:
        opts = self.query_one("#sk_found", OptionList)
        if opts.highlighted is None or not opts.option_count:
            return None
        return getattr(opts.get_option_at_index(opts.highlighted), "id", None)

    def action_toggle_row(self) -> None:
        if not isinstance(self.focused, OptionList) or self.focused.id != "sk_found":
            return
        name = self._current()
        if not name:
            return
        self._selected.symmetric_difference_update({name})
        self._paint_list()

    def action_toggle_all(self) -> None:
        if not isinstance(self.focused, OptionList) or self.focused.id != "sk_found" or not self._info:
            return
        names = {s["name"] for s in self._info.get("skills", []) if s.get("usable")}
        self._selected = set() if self._selected >= names else names
        self._paint_list()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if not self._selected and event.option.id:
            self._selected = {str(event.option.id)}
            self._paint_list()
        self.action_install()

    # ── install ──────────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "sk_go":
            if self._info and self._info.get("ok") and self._selected:
                self.action_install()
            else:
                self.on_input_submitted(Input.Submitted(self.query_one("#sk_source", Input),
                                                        self.query_one("#sk_source", Input).value))
        elif bid == "sk_close":
            self.action_cancel()
        elif bid == "sk_try_anthropic":
            inp = self.query_one("#sk_source", Input)
            inp.value = "https://github.com/anthropics/skills"
            inp.focus()
            self._inspect(inp.value, then_install=False)

    def action_install(self) -> None:
        if self._installing or not self._info or not self._info.get("ok"):
            return
        names = sorted(self._selected)
        if not names:
            self.query_one("#sk_result", Static).update(Text("Select at least one skill (space).", style=ui.WARN))
            return
        self._installing = True
        self.query_one("#sk_go", Button).disabled = True
        assert self._busy is not None
        self._busy.start(f"installing {', '.join(names[:3])}{'…' if len(names) > 3 else ''}")
        self._install_worker(self._text_for_info, self._scope(), names)

    @work(thread=True, group="skill-install")
    def _install_worker(self, text: str, scope: str, names: list[str]) -> None:
        from ..storage import skill_install as si

        try:
            res = si.install_skills(text, scope=scope, names=names, overwrite=False)
        except Exception as exc:
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _call(self, self._installed, res)

    def _installed(self, res: dict) -> None:
        self._installing = False
        if self._busy:
            self._busy.stop()
        self.query_one("#sk_go", Button).disabled = False
        out = self.query_one("#sk_result", Static)
        lines: list[Text] = []
        for i in res.get("installed", []):
            t = Text()
            t.append("✓ ", style=ui.OK)
            t.append(i["name"], style=f"bold {ui.FG}")
            t.append(f"  → {i['scope']}", style=ui.FG_MUTE)
            lines.append(t)
        for s in res.get("skipped", []):
            lines.append(Text(f"– {s['name']}: {s['reason']}", style=ui.WARN))
        if not res.get("ok") and not lines:
            lines.append(Text(f"✗ {res.get('error', 'could not install')}", style=ui.ERR))
        if res.get("scope_note"):
            lines.append(Text(res["scope_note"], style=ui.WARN))
        out.update(Text("\n").join(lines))
        if res.get("ok") and not res.get("skipped"):
            self.set_timer(1.1, lambda: self._dismiss_with(res))
        elif res.get("ok"):
            self._result = res

    _result: dict | None = None

    def action_cancel(self) -> None:
        self.dismiss(self._result)

