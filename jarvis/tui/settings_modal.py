"""Centered settings control modal — view / set / reset / reload / edit.

User interaction:
    ↑/↓        navigate the settings list
    e          edit the highlighted key's value
    r          reset the highlighted key to its default
    R          reload settings.json from disk
    p          show the settings file path
    o          open settings.json in $EDITOR
    Esc        close
"""
from __future__ import annotations

import os
import subprocess
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..storage.settings import get_settings, SETTINGS_FILE, DEFAULTS
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, _ellipsis
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


_DESCRIPTIONS: dict[str, str] = {
    "model":         "Active model identifier",
    "theme":         "Visual theme — red or purple",
    "agent.active":  "Persisted active agent name (empty = default)",
    "agent.global":  "Include agents from ~/.harness, ~/.claude, ~/.opencode",
    "skills.global": "Include skills from ~/.config/*/skills (otherwise project-only)",
    "mcp.global":    "Include MCP servers from Claude/OpenCode/Cursor/Windsurf/VS Code",
    "think.mode":    "Enable extended thinking",
    "think.effort":  "Thinking effort — none / low / medium / high",
    "trace.on":      "Show thinking + tool panels in chat (^T trace)",
    "pin.enabled":   "Inject pinned.txt into every system prompt (/pin off to pause)",
}


def _flatten(d: dict, prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            rows.extend(_flatten(v, path))
        else:
            rows.append((path, v))
    return rows


def _fmt_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v if v else "(unset)"
    if v is None:
        return "null"
    return str(v)


# ── edit value sub-modal ──────────────────────────────────────────────────


class _EditValueScreen(TuiModalScreen[str | None]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    _EditValueScreen #modal { width: 62%; max-width: 90; max-height: 42%; }
    _EditValueScreen #edit_hint { padding: 0 1; color: {ui.FG_MUTE}; margin-bottom: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

    def __init__(self, key: str, current: Any) -> None:
        super().__init__()
        self._key = key
        self._current = current

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(
                    f"✎  Set   [bold {ui.ACCENT}]{self._key}[/]",
                    id="modal_title",
                )
                hint = _DESCRIPTIONS.get(self._key, "")
                if hint:
                    yield Static(f"[{ui.FG_MUTE}]{hint}[/]", id="edit_hint")
                yield Input(value=_fmt_value(self._current), id="value_input")
                yield Static(
                    f"[{ui.ACCENT_3}]↵[/] save   [{ui.ACCENT_3}]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        inp = self.query_one("#value_input", Input)
        inp.focus()
        try:
            inp.cursor_position = len(inp.value)
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── main settings modal ───────────────────────────────────────────────────


class SettingsModalScreen(TuiModalScreen[None]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    SettingsModalScreen #modal { width: 84%; max-width: 130; max-height: 85%; }
    SettingsModalScreen OptionList { height: 1fr; min-height: 12; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("enter", "edit", show=False),
        Binding("e", "edit", "Edit", show=True),
        Binding("r", "reset", "Reset", show=True),
        Binding("R", "reload", "Reload", show=True),
        Binding("p", "show_path", "Path", show=True),
        Binding("o", "open_editor", "Open in $EDITOR", show=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⚙  Settings", id="modal_title")
                yield Static("", id="modal_status")
                yield OptionList(id="settings_list")
                yield Static(
                    f"[{ui.ACCENT_3}]↑↓[/] nav   [{ui.ACCENT_3}]↵/e[/] edit   "
                    f"[{ui.ACCENT_3}]r[/] reset   [{ui.ACCENT_3}]R[/] reload   "
                    f"[{ui.ACCENT_3}]p[/] path   [{ui.ACCENT_3}]o[/] $EDITOR   "
                    f"[{ui.ACCENT_3}]esc[/] close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._populate()

    def on_unmount(self) -> None:
        disable_mouse()

    def _populate(self) -> None:
        opts = self.query_one("#settings_list", OptionList)
        opts.clear_options()
        settings = get_settings()
        merged = settings.all()
        overrides = dict(_flatten(settings.overrides()))
        defaults_flat = dict(_flatten(DEFAULTS))
        rows = _flatten(merged)
        for path, value in rows:
            is_override = path in overrides
            default_val = defaults_flat.get(path, "")
            same_as_default = value == default_val
            key_style = f"bold {ui.ACCENT}" if is_override else ui.ACCENT
            value_style = (
                f"bold {ui.OK}" if isinstance(value, bool) and value
                else f"bold {ui.ERR}" if isinstance(value, bool)
                else f"italic {ui.FG_DIM}" if (isinstance(value, str) and not value)
                else ui.FG
            )
            tail = (
                f"  [{ui.FG_DIM}](default)[/]" if same_as_default
                else f"  [{ui.FG_DIM}]· default {_fmt_value(default_val)}[/]"
            )
            desc = _DESCRIPTIONS.get(path, "")
            row = Text.from_markup(
                f"  [{key_style}]{path:<22}[/]  "
                f"[{value_style}]{_ellipsis(_fmt_value(value), 28):<28}[/]"
                f"{_ellipsis(tail, 30)}  [{ui.FG_DIM}]{_ellipsis(desc, 45)}[/]"
            )
            opts.add_option(Option(row, id=f"k:{path}"))
        try:
            n_over = len(overrides)
            n_keys = len(rows)
            self.query_one("#modal_title", Static).update(
                f"⚙  Settings   [{ui.FG_DIM}]{n_keys} keys · {n_over} override{'s' if n_over != 1 else ''}[/]"
            )
        except Exception:
            pass
        opts.focus()

    def _notify(self, msg: str, error: bool = False) -> None:
        try:
            color = ui.ERR if error else ui.OK
            self.query_one("#modal_status", Static).update(f"[{color}]{msg}[/]")
        except Exception:
            pass

    def _highlighted_key(self) -> str | None:
        opts = self.query_one("#settings_list", OptionList)
        if opts.highlighted is None:
            return None
        try:
            opt = opts.get_option_at_index(opts.highlighted)
        except Exception:
            return None
        oid = getattr(opt, "id", None)
        if not oid or not oid.startswith("k:"):
            return None
        return oid.split(":", 1)[1]

    # ── actions ────────────────────────────────────────────────────────────

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#settings_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#settings_list", OptionList).action_cursor_up()

    def action_edit(self) -> None:
        key = self._highlighted_key()
        if not key:
            self._notify("(highlight a key to edit)")
            return
        current = get_settings().get(key)

        def after(new_val: str | None) -> None:
            if new_val is None:
                return
            try:
                get_settings().set(key, new_val)
                try:
                    state.apply_settings_to_state()
                except Exception:
                    pass
                self._populate()
                self._notify(f"✓ {key} = {new_val}")
            except Exception as e:
                self._notify(f"✗ {e}", error=True)

        self.app.push_screen(_EditValueScreen(key, current), after)

    def action_reset(self) -> None:
        key = self._highlighted_key()
        if not key:
            self._notify("(highlight a key to reset)")
            return
        try:
            get_settings().reset(key)
            try:
                state.apply_settings_to_state()
            except Exception:
                pass
            self._populate()
            self._notify(f"✓ reset {key} to default")
        except Exception as e:
            self._notify(f"✗ {e}", error=True)

    def action_reload(self) -> None:
        try:
            get_settings().reload()
            try:
                state.apply_settings_to_state()
            except Exception:
                pass
            self._populate()
            self._notify("✓ re-read settings.json from disk")
        except Exception as e:
            self._notify(f"✗ {e}", error=True)

    def action_show_path(self) -> None:
        self._notify(str(SETTINGS_FILE))

    def action_open_editor(self) -> None:
        editor = os.environ.get("EDITOR")
        if not editor:
            self._notify(f"$EDITOR not set — path: {SETTINGS_FILE}", error=True)
            return
        try:
            subprocess.Popen([editor, str(SETTINGS_FILE)])
            self._notify(f"opened {SETTINGS_FILE} in $EDITOR")
        except Exception as e:
            self._notify(f"could not launch $EDITOR: {e}", error=True)
