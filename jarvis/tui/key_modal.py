"""API Key management modal — view all configured API keys, edit, delete, or add.

Opened via the :command:`/key` slash command in the TUI.

Layout
------
┌─────────────────────────────────────────────────────────────────┐
│ ⬟  API Keys                                       3 keys · 2 saved │
│                                                                      │
│  ● Anthropic      file  ~/.config/harness-agent/key    sk-ant-…abc123 │
│  ○ OpenRouter     env   OPENROUTER_API_KEY                 …def456  │
│  ○ OpenCode Go    —     not configured                              │
│  ○ OpenCode Zen   file  ~/.config/harness-agent/opencode_zen_key  …ghi789 │
│                                                                      │
│  ↑↓ nav  ↵/e edit  d delete  a add  esc close                       │
└─────────────────────────────────────────────────────────────────┘

Key bindings
    ↑/↓               navigate the key list
    ↵  /  e            edit the highlighted key's value
    d                  delete the highlighted key file (confirm first)
    a                  add a new key for the highlighted provider
    esc                close
"""
from __future__ import annotations

import os
import pathlib
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..constants.api_keys import API_KEY_SPECS
from ..utils.io import _secure_write
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui
from .text_input_modal import TextInputScreen


# ── key descriptor (from shared constants) ────────────────────────────────────

_KEY_DEFS: list[dict] = list(API_KEY_SPECS)

ID_PREFIX = "kp:"


# ── helpers ───────────────────────────────────────────────────────────────────


def _read_key_suffix(path: pathlib.Path) -> str:
    """Return the last 6 characters of the key in ``path``, or empty string."""
    try:
        raw = path.read_text().strip()
        if len(raw) > 6:
            return raw[-6:]
        return raw
    except Exception:
        return ""


def _get_key_info(k: dict) -> dict[str, Any]:
    """Return current status for a key descriptor.

    Returns a dict with:
        provider    — provider id
        label       — display name
        source      — ``"env"`` | ``"file"`` | ``"none"``
        source_text — human-readable source string
        suffix      — last 6 chars (masked), or empty
        can_edit    — True unless key only comes from env
        can_delete  — True when key comes from a file
        can_add     — True when not configured at all
    """
    file_path: pathlib.Path = k["file_path"]
    env_var: str = k["env_var"]
    has_env = bool(os.getenv(env_var))
    has_file = file_path.exists()

    suffix = ""
    source = "none"
    source_text = "—  not configured"

    if has_env:
        source = "env"
        env_val = os.getenv(env_var, "")
        suffix = env_val[-6:] if len(env_val) > 6 else env_val
        source_text = f"env  {env_var}"
        if has_file:
            source_text += f"  (+ file)"
    elif has_file:
        source = "file"
        suffix = _read_key_suffix(file_path)
        source_text = f"file  {file_path.name}"

    return {
        "provider": k["provider"],
        "label": k["label"],
        "source": source,
        "source_text": source_text,
        "suffix": suffix,
        "prefix": k.get("key_prefix", ""),
        "file_path": file_path,
        "env_var": env_var,
        "can_edit": source in ("file", "none"),
        "can_delete": source == "file",
        "can_add": source == "none",
    }


def _key_id(provider: str) -> str:
    return f"{ID_PREFIX}{provider}"


def _provider_from_id(oid: str) -> str | None:
    if oid.startswith(ID_PREFIX):
        return oid[len(ID_PREFIX):]
    return None


def _format_row(info: dict, is_current_provider: bool) -> Text:
    """Build a rich Text row for the key list."""
    marker = "● " if is_current_provider else "  "
    marker_style = f"bold {ui.OK}" if is_current_provider else ui.FG_DIM
    label_style = f"bold {ui.ACCENT}" if is_current_provider else ui.ACCENT

    source_text = info["source_text"]
    source_style = {
        "env": ui.WARN,
        "file": ui.ACCENT,
        "none": ui.FG_DIM,
    }.get(info["source"], ui.FG_DIM)

    suffix = info["suffix"]
    suffix_part = f"{'…' if suffix else ''}{suffix}" if suffix else "—"

    # Build the row text
    return Text.assemble(
        (marker, marker_style),
        (f"{info['label']:<22s}", label_style),
        (source_text, source_style),
        ("  ", ""),
        (suffix_part, ui.FG_DIM),
    )


# ── confirmation sub-modal ────────────────────────────────────────────────────


class _ConfirmDeleteScreen(TuiModalScreen[bool]):
    """Tiny yes/no confirmation before deleting a key file."""

    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    _ConfirmDeleteScreen #modal { width: 50%; max-width: 70; max-height: 35%; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("y", "confirm", "Yes", show=True),
        Binding("n", "cancel", "No", show=True),
        Binding("enter", "confirm", "Yes", show=False),
    ]

    def __init__(self, label: str, file_name: str) -> None:
        super().__init__()
        self._label = label
        self._file_name = file_name

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(
                    f"✕  Delete {self._label} key?",
                    id="modal_title",
                )
                yield Static(
                    f"[{ui.FG_MUTE}]This will remove:[/]\n"
                    f"[{ui.FG}]{self._file_name}[/]\n\n"
                    f"[{ui.ACCENT_3}]y[/] yes   [{ui.ACCENT_3}]n[/] no   [{ui.ACCENT_3}]esc[/] cancel",
                    id="modal_hint",
                )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# ── main key management modal ─────────────────────────────────────────────────


class KeyModalScreen(TuiModalScreen[None]):
    """Modal that lists all API keys with edit, delete, and add actions.

    Dismisses with ``None`` — all side effects (edit, delete, add) are
    applied immediately within the modal's lifecycle.
    """

    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    KeyModalScreen #modal { width: 84%; max-width: 120; max-height: 80%; }
    KeyModalScreen OptionList { height: 1fr; min-height: 10; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("e", "edit", "Edit", show=True),
        Binding("d", "delete", "Delete", show=True),
        Binding("a", "add", "Add", show=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⬟  API Keys", id="modal_title")
                yield Static("", id="modal_status")
                yield OptionList(id="key_list")
                yield Static(
                    f"[{ui.ACCENT_3}]↑↓[/] nav   [{ui.ACCENT_3}]↵/e[/] edit   "
                    f"[{ui.ACCENT_3}]d[/] delete   [{ui.ACCENT_3}]a[/] add   "
                    f"[{ui.ACCENT_3}]esc[/] close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._populate()

    def on_unmount(self) -> None:
        disable_mouse()

    # ── content ────────────────────────────────────────────────────────────

    def _populate(self) -> None:
        opts = self.query_one("#key_list", OptionList)
        opts.clear_options()

        configured = 0
        for k_def in _KEY_DEFS:
            info = _get_key_info(k_def)
            is_current = info["provider"] == state.provider
            row = _format_row(info, is_current)
            opts.add_option(Option(row, id=_key_id(info["provider"])))
            if info["source"] != "none":
                configured += 1

        self._update_title(configured)
        opts.focus()
        self._clear_status()

    def _update_title(self, configured: int = 0) -> None:
        total = len(_KEY_DEFS)
        try:
            self.query_one("#modal_title", Static).update(
                f"⬟  API Keys   [{ui.FG_DIM}]{total} providers · "
                f"{configured} configured[/]"
            )
        except Exception:
            pass

    def _highlighted_provider(self) -> str | None:
        opts = self.query_one("#key_list", OptionList)
        if opts.highlighted is None:
            return None
        try:
            opt = opts.get_option_at_index(opts.highlighted)
        except Exception:
            return None
        oid = getattr(opt, "id", None)
        if not oid:
            return None
        return _provider_from_id(oid)

    def _key_def_for_provider(self, provider: str) -> dict | None:
        for k in _KEY_DEFS:
            if k["provider"] == provider:
                return k
        return None

    def _notify(self, msg: str, error: bool = False) -> None:
        try:
            color = ui.ERR if error else ui.OK
            self.query_one("#modal_status", Static).update(f"[{color}]{msg}[/]")
        except Exception:
            pass

    def _clear_status(self) -> None:
        try:
            self.query_one("#modal_status", Static).update("")
        except Exception:
            pass

    # ── bindings / actions ─────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter (or click) on a key row edits it — fires every time, not just once.

        The focused ``OptionList`` consumes Enter and posts this message, which
        shadows any screen-level ``enter`` binding, so the primary action must
        live here.
        """
        event.stop()
        self.action_edit()

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#key_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#key_list", OptionList).action_cursor_up()

    def action_edit(self) -> None:
        provider = self._highlighted_provider()
        if not provider:
            self._notify("(highlight a key to edit)", error=True)
            return
        k_def = self._key_def_for_provider(provider)
        if not k_def:
            return
        info = _get_key_info(k_def)

        if info["source"] == "env":
            self._notify(f"{info['label']} key is set via env ${info['env_var']} — unset the env var to use a file key instead", error=True)
            return
        if info["source"] not in ("file", "none"):
            self._notify(f"cannot edit {info['label']} key (source: {info['source']})", error=True)
            return

        title = f"✎  Edit {info['label']} API Key"
        body = (
            f"Paste your new {info['label']} API key.\n"
            f"Saved to: [{ui.ACCENT}]{info['file_path']}[/] (chmod 600)"
        )
        # Pre-fill with current value if it exists
        current = ""
        if info["source"] == "file":
            try:
                current = info["file_path"].read_text().strip()
            except Exception:
                pass

        self.app.push_screen(
            TextInputScreen(title=title, body=body, placeholder="Paste API key here…"),
            lambda val: self._on_edit_result(k_def, val),
        )

    def _on_edit_result(self, k_def: dict, new_val: str | None) -> None:
        if not new_val or not new_val.strip():
            self._notify("edit cancelled", error=True)
            return
        new_val = new_val.strip()
        prefix = k_def.get("key_prefix")
        if prefix and not new_val.startswith(prefix):
            self._notify(
                f"Key must start with [{prefix}] — got [{new_val[:20]}…]",
                error=True,
            )
            return
        try:
            file_path: pathlib.Path = k_def["file_path"]
            _secure_write(file_path, new_val)
            self._populate()
            self._notify(f"✓ {k_def['label']} key saved to {file_path.name}")
        except Exception as e:
            self._notify(f"✗ failed to save: {e}", error=True)

    def action_delete(self) -> None:
        provider = self._highlighted_provider()
        if not provider:
            self._notify("(highlight a key to delete)", error=True)
            return
        k_def = self._key_def_for_provider(provider)
        if not k_def:
            return
        info = _get_key_info(k_def)

        if info["source"] == "env":
            self._notify(
                f"{info['label']} key is from env ${info['env_var']} — "
                f"unset the environment variable to remove it",
                error=True,
            )
            return
        if info["source"] != "file":
            self._notify(f"no {info['label']} key file to delete", error=True)
            return

        label = info["label"]
        file_name = str(info["file_path"])

        def after(confirmed: bool) -> None:
            if not confirmed:
                self._notify("delete cancelled")
                return
            try:
                info["file_path"].unlink(missing_ok=True)
                self._populate()
                self._notify(f"✓ {label} key file deleted")
            except Exception as e:
                self._notify(f"✗ failed to delete: {e}", error=True)

        self.app.push_screen(_ConfirmDeleteScreen(label, file_name), after)

    def action_add(self) -> None:
        """Add a key for a provider that has none configured."""
        provider = self._highlighted_provider()
        if not provider:
            self._notify("(highlight a provider to add a key for)", error=True)
            return
        k_def = self._key_def_for_provider(provider)
        if not k_def:
            return
        info = _get_key_info(k_def)

        if info["source"] != "none":
            self._notify(f"{info['label']} already configured — use Edit to replace", error=True)
            return

        title = f"➕  Add {info['label']} API Key"
        body = (
            f"Paste your {info['label']} API key.\n"
            f"Saved to: [{ui.ACCENT}]{info['file_path']}[/] (chmod 600)"
        )

        self.app.push_screen(
            TextInputScreen(title=title, body=body, placeholder="Paste API key here…"),
            lambda val: self._on_edit_result(k_def, val),
        )
