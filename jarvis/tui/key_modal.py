"""API Key management modal — view all configured API keys, edit, delete, or add.

Opened via the :command:`/key` slash command in the TUI.

Layout
------
┌─────────────────────────────────────────────────────────────────┐
│ ⬟  API Keys                          214 providers · 3 configured │
│ [ Search providers…                                            ]  │
│  ● Anthropic      file  ~/.config/harness-agent/key    sk-ant-…abc123 │
│  ○ OpenRouter     env   OPENROUTER_API_KEY                 …def456  │
│  FROM MODELS.DEV  connected                                         │
│  ● DeepSeek       DEEPSEEK_API_KEY                saved key · …ghi7 │
│  MORE PROVIDERS   209 from models.dev                               │
│  ○ Groq           GROQ_API_KEY                       not configured │
│                                                                      │
│  ↑↓ nav  ↵/e edit  d delete  a add  / search  esc close             │
└─────────────────────────────────────────────────────────────────┘

The built-in providers come first; then every provider from models.dev
(:mod:`jarvis.auth.models_dev`) — the ones with a key, then the rest.

Key bindings
    ↑/↓               navigate the key list
    ↵  /  e            edit the highlighted key's value (adds one when unset)
    d                  delete the highlighted key (confirm first)
    a                  add a new key for the highlighted provider
    /                  search providers
    esc                close (clears the search first)
"""
from __future__ import annotations

import os
import pathlib
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from rich.markup import escape

from ..constants.api_keys import API_KEY_SPECS
from ..utils.io import _secure_write
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, empty_row, section_header
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui
from .text_input_modal import TextInputScreen


# ── key descriptor (from shared constants) ────────────────────────────────────

_KEY_DEFS: list[dict] = list(API_KEY_SPECS)

ID_PREFIX = "kp:"


def _catalog_defs() -> list[dict]:
    """One descriptor per models.dev provider (``catalog=True``), by name."""
    try:
        from ..auth import models_dev
    except Exception:
        return []
    out = []
    for pid, prov in models_dev.providers().items():
        out.append({
            "id": pid,
            "provider": pid,
            "label": prov.name,
            "file_path": None,
            "env_var": prov.key_vars[0] if prov.key_vars else "",
            "key_prefix": None,
            "catalog": True,
            "doc": prov.doc,
            "local": prov.local,
            "missing": models_dev.missing_vars(prov),
            "models": len(prov.models),
        })
    out.sort(key=lambda d: d["label"].lower())
    return out


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


def _suffix(value: str) -> str:
    return value[-6:] if len(value) > 6 else value


def _catalog_key_info(k: dict) -> dict[str, Any]:
    from ..auth import catalog_keys

    pid = k["provider"]
    source, value = catalog_keys.key_source(pid)
    env_var = catalog_keys.env_var_for(pid) if source == "env" else k.get("env_var", "")
    saved = catalog_keys.saved_value(pid)
    if source == "env":
        source_text = f"env  {env_var}"
    elif source == "file":
        source_text = f"saved  {saved}" if catalog_keys.is_reference(saved) else "saved key"
    else:
        source_text = "—  not configured"
    return {
        "provider": pid,
        "label": k["label"],
        "source": source,
        "source_text": source_text,
        "suffix": _suffix(value) if value and not catalog_keys.is_reference(saved) else "",
        "reference": saved if catalog_keys.is_reference(saved) else "",
        "prefix": "",
        "file_path": catalog_keys._file(),
        "env_var": env_var,
        "can_edit": source in ("file", "none"),
        "can_delete": source == "file",
        "can_add": source == "none",
        "catalog": True,
        "doc": k.get("doc", ""),
        "local": k.get("local", False),
        "missing": k.get("missing") or [],
        "models": k.get("models", 0),
    }


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
    if k.get("catalog"):
        return _catalog_key_info(k)
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


def _apply_key_change(provider: str, *, removed: bool = False) -> str:
    """Apply a saved/deleted key to the running session (no restart needed)."""
    try:
        from ..commands.control import apply_key_change

        return apply_key_change(provider, removed=removed)
    except Exception as e:
        return f"restart Jarvis to apply it ({e})"


def _key_id(provider: str) -> str:
    return f"{ID_PREFIX}{provider}"


def _provider_from_id(oid: str) -> str | None:
    if oid.startswith(ID_PREFIX):
        return oid[len(ID_PREFIX):]
    return None


def _format_row(info: dict, is_current_provider: bool, query: str = ""):
    """One provider row: status dot, name, where the key comes from."""
    from .modal_chrome import picker_row

    source = info["source"]
    suffix = info["suffix"]
    if source == "none":
        if info.get("missing"):
            right = f"needs {', '.join(info['missing'])}"
        elif info.get("local"):
            right = "local server"
        else:
            right = "not configured"
        right_style, dot, dot_style = ui.FG_DIM, "○", ui.FG_DIM
    else:
        where = "env var" if source == "env" else "saved key"
        if info.get("reference"):
            right = f"uses {info['reference']}"
        else:
            right = f"{where} · …{suffix}" if suffix else where
        right_style = ui.WARN if source == "env" else ui.FG_MUTE
        dot, dot_style = "●", ui.OK
    return picker_row(
        info["label"],
        detail=info.get("env_var") or "",
        right=right,
        right_style=right_style,
        active=is_current_provider,
        icon=dot,
        icon_style=dot_style,
        title_width=22,
        query=query,
    )


def _matches(info: dict, q: str) -> bool:
    if not q:
        return True
    hay = " ".join((info["label"], info["provider"], info.get("env_var") or "")).lower()
    return all(part in hay for part in q.split())


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
                    f"[bold {ui.FG_MUTE}]y[/] yes   [bold {ui.FG_MUTE}]n[/] no   [bold {ui.FG_MUTE}]esc[/] cancel",
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

    ``focus`` (a provider id) highlights that provider on open, and with
    ``add=True`` goes straight to the paste-a-key prompt for it — what the
    model picker's "Connect <provider>" rows use.
    """

    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    KeyModalScreen #modal { width: 84%; max-width: 120; height: 85%; max-height: 44; }
    KeyModalScreen OptionList { height: 1fr; min-height: 10; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("e", "edit", "Edit", show=True),
        Binding("d", "delete", "Delete", show=True),
        Binding("a", "add", "Add", show=True),
        Binding("slash", "focus_search", "Search", show=True),
    ]

    def __init__(self, focus: str = "", *, add: bool = False) -> None:
        super().__init__()
        self._focus = focus
        self._add_on_open = add
        self._catalog: list[dict] = []

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⬟  API Keys", id="modal_title")
                yield Static("", id="modal_status")
                yield Input(value="", placeholder="Search providers…  (/)", id="key_search")
                yield OptionList(id="key_list")
                yield Static(
                    f"[bold {ui.FG_MUTE}]↑↓[/] nav   [bold {ui.FG_MUTE}]↵/e[/] edit   "
                    f"[bold {ui.FG_MUTE}]d[/] delete   [bold {ui.FG_MUTE}]a[/] add   "
                    f"[bold {ui.FG_MUTE}]/[/] search   [bold {ui.FG_MUTE}]esc[/] close   "
                    f"[{ui.FG_DIM}]providers via models.dev[/]",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._catalog = _catalog_defs()
        self._populate(keep=_key_id(self._focus) if self._focus else None)
        self.query_one("#key_list", OptionList).focus()
        if self._focus and self._add_on_open:
            self.call_after_refresh(self.action_edit)

    def on_unmount(self) -> None:
        disable_mouse()

    # ── content ────────────────────────────────────────────────────────────

    def _query(self) -> str:
        try:
            return (self.query_one("#key_search", Input).value or "").strip().lower()
        except Exception:
            return ""

    def _populate(self, keep: str | None = None) -> None:
        opts = self.query_one("#key_list", OptionList)
        keep = keep or self._highlighted_option_id()
        opts.clear_options()
        q = self._query()

        configured = 0
        options: list[Option] = []
        for k_def in _KEY_DEFS:
            info = _get_key_info(k_def)
            if info["source"] != "none":
                configured += 1
            if not _matches(info, q):
                continue
            row = _format_row(info, info["provider"] == state.provider, q)
            options.append(Option(row, id=_key_id(info["provider"])))

        connected: list[Option] = []
        more: list[Option] = []
        for k_def in self._catalog:
            info = _get_key_info(k_def)
            if info["source"] != "none":
                configured += 1
            if not _matches(info, q):
                continue
            opt = Option(
                _format_row(info, info["provider"] == state.provider, q),
                id=_key_id(info["provider"]),
            )
            (connected if info["source"] != "none" else more).append(opt)
        if connected:
            options.append(section_header("From models.dev", "connected", first=not options))
            options.extend(connected)
        if more:
            note = f"{len(more)} from models.dev" if not q else f"{len(more)} match"
            options.append(section_header("More providers", note, first=not options))
            options.extend(more)
        if not options:
            options.append(empty_row(f"No providers match “{q}”"))
        opts.add_options(options)

        self._update_title(configured)
        try:
            opts.highlighted = opts.get_option_index(keep) if keep else None
        except Exception:
            opts.highlighted = None
        if opts.highlighted is None:
            opts.action_first()
        opts.scroll_to_highlight()
        self._clear_status()

    def _update_title(self, configured: int = 0) -> None:
        total = len(_KEY_DEFS) + len(self._catalog)
        try:
            self.query_one("#modal_title", Static).update(
                f"⬟  API Keys   [{ui.FG_DIM}]{total} providers · "
                f"{configured} configured[/]"
            )
        except Exception:
            pass

    def _highlighted_option_id(self) -> str | None:
        try:
            opts = self.query_one("#key_list", OptionList)
            if opts.highlighted is None:
                return None
            return opts.get_option_at_index(opts.highlighted).id
        except Exception:
            return None

    def _highlighted_provider(self) -> str | None:
        oid = self._highlighted_option_id()
        if not oid:
            return None
        return _provider_from_id(oid)

    def _key_def_for_provider(self, provider: str) -> dict | None:
        for k in _KEY_DEFS:
            if k["provider"] == provider:
                return k
        for k in self._catalog:
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

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "key_search":
            self._populate()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "key_search":
            event.stop()
            self.action_edit()  # Enter in the search box acts on the highlighted row

    def action_dismiss_cancel(self) -> None:
        try:
            search = self.query_one("#key_search", Input)
            if search.value:
                search.value = ""
                self.query_one("#key_list", OptionList).focus()
                return
        except Exception:
            pass
        self.dismiss(None)

    def action_focus_search(self) -> None:
        self.query_one("#key_search", Input).focus()

    def action_cursor_down(self) -> None:
        self.query_one("#key_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#key_list", OptionList).action_cursor_up()

    def action_page_down(self) -> None:
        self.query_one("#key_list", OptionList).action_page_down()

    def action_page_up(self) -> None:
        self.query_one("#key_list", OptionList).action_page_up()

    def _prompt_body(self, info: dict, verb: str) -> str:
        if info.get("catalog"):
            lines = [f"{verb} your {escape(info['label'])} API key."]
            if info.get("doc"):
                lines.append(f"Get one: [{ui.ACCENT}]{escape(info['doc'])}[/]")
            if info.get("local"):
                lines.append(f"[{ui.FG_DIM}]A local server — any value works if it doesn't check keys.[/]")
            if info.get("missing"):
                lines.append(
                    f"[{ui.WARN}]Also set {', '.join(info['missing'])} in your environment — "
                    "its address needs it.[/]"
                )
            lines.append(
                f"Saved to: [{ui.ACCENT}]{info['file_path']}[/] (chmod 600)\n"
                f"[{ui.FG_DIM}]Tip: type ${info.get('env_var') or 'VAR'} to use an environment "
                "variable instead of a copy.[/]"
            )
            return "\n".join(lines)
        return (
            f"{verb} your {info['label']} API key.\n"
            f"Saved to: [{ui.ACCENT}]{info['file_path']}[/] (chmod 600)"
        )

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

        verb = "Paste" if info["source"] == "none" else "Paste your new"
        title = f"✎  Edit {info['label']} API Key" if info["source"] == "file" else f"➕  Add {info['label']} API Key"
        self.app.push_screen(
            TextInputScreen(title=title, body=self._prompt_body(info, verb), placeholder="Paste API key here…"),
            lambda val: self._on_edit_result(k_def, val),
        )

    def _on_edit_result(self, k_def: dict, new_val: str | None) -> None:
        if not new_val or not new_val.strip():
            self._notify("edit cancelled", error=True)
            return
        new_val = new_val.strip()
        if k_def.get("catalog"):
            from ..auth import catalog_keys

            try:
                catalog_keys.save(k_def["provider"], new_val)
            except Exception as e:
                self._notify(f"✗ failed to save: {e}", error=True)
                return
            self._populate(keep=_key_id(k_def["provider"]))
            if catalog_keys.is_reference(new_val) and not catalog_keys.get_key(k_def["provider"]):
                self._notify(f"saved — but {new_val} is empty in this shell", error=True)
                return
            note = _apply_key_change(k_def["provider"])
            self._notify(f"✓ {k_def['label']} key saved" + (f" — {note}" if note else ""))
            return
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
        except Exception as e:
            self._notify(f"✗ failed to save: {e}", error=True)
            return
        note = _apply_key_change(k_def["provider"])
        self._notify(f"✓ {k_def['label']} key saved" + (f" — {note}" if note else ""))

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
        file_name = (
            f"{label}'s entry in {info['file_path']}" if info.get("catalog") else str(info["file_path"])
        )

        def after(confirmed: bool) -> None:
            if not confirmed:
                self._notify("delete cancelled")
                return
            try:
                if info.get("catalog"):
                    from ..auth import catalog_keys

                    catalog_keys.delete(provider)
                else:
                    info["file_path"].unlink(missing_ok=True)
                self._populate()
            except Exception as e:
                self._notify(f"✗ failed to delete: {e}", error=True)
                return
            note = _apply_key_change(k_def["provider"], removed=True)
            self._notify(f"✓ {label} key deleted" + (f" — {note}" if note else ""))

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
        self.app.push_screen(
            TextInputScreen(title=title, body=self._prompt_body(info, "Paste"), placeholder="Paste API key here…"),
            lambda val: self._on_edit_result(k_def, val),
        )
