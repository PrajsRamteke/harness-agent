"""Workspace project list (``/project``): switch, add, remove, link, reorder.

Rows: colour dot · name · path · status (★ here · ● running elsewhere · ◌ stopped ·
missing) · link badge. Returns the id of the project to switch to (``None``: closed).
The other actions edit ``storage/workspace.py`` in place and repaint.

Keys: ↑↓ move · ↵ switch · a add · d remove · l links · w write policy · r rename ·
J/K (or shift+↓/↑) reorder · s settings · esc close.
"""
from __future__ import annotations

import os
import shlex

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ..commands import project as pc
from ..storage import workspace
from ..storage.workspace import WorkspaceError
from . import theme as ui
from .modal_chrome import (
    TUI_MODAL_CHROME_CSS,
    TuiModalScreen,
    empty_row,
    hint_line,
    picker_row,
)
from .mouse_toggle import disable_mouse, enable_mouse
from .text_input_modal import TextInputScreen

_STATUS = {
    "current": ("★ here", ui.WARN),
    "running": ("● running elsewhere", ui.OK),
    "stopped": ("◌ stopped", ui.FG_DIM),
    "missing": ("✗ folder missing", ui.ERR),
}
_WRITE_CYCLE = ("ask", "allow", "deny")


def complete_path(text: str) -> tuple[str, list[str]]:
    """Tab-completion for a folder path: ``(completed text, candidates)``.

    Understands ``~``; only folders are offered. A single match completes with a
    trailing ``/``; several complete their common prefix.
    """
    raw = text or ""
    expanded = os.path.expanduser(raw) if raw else os.getcwd() + os.sep
    base, _, stem = expanded.rpartition(os.sep)
    folder = (base or os.sep) if expanded.startswith(os.sep) else (base or ".")
    try:
        names = sorted(
            n for n in os.listdir(folder)
            if os.path.isdir(os.path.join(folder, n)) and n.lower().startswith(stem.lower())
            and (stem.startswith(".") or not n.startswith("."))
        )
    except OSError:
        return raw, []
    if not names:
        return raw, []
    keep = raw.rpartition(os.sep)[0] + os.sep if os.sep in raw else ""
    if len(names) == 1:
        return keep + names[0] + os.sep, names
    common = os.path.commonprefix(names)
    return (keep + common if len(common) > len(stem) else raw), names


class PathInputScreen(TuiModalScreen[str | None]):
    """A folder path with ``~`` and Tab completion. Dismisses with the text, or ``None``."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    PathInputScreen #modal { width: 78%; max-width: 110; }
    PathInputScreen #modal_body { color: {ui.FG}; padding: 0 1; margin-bottom: 1; }
    PathInputScreen #path_hints { color: {ui.FG_DIM}; padding: 0 1; height: auto; max-height: 4; }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("tab", "complete", "Complete", show=False, priority=True),
    ]

    def __init__(self, title: str, body: str = "", value: str = "") -> None:
        super().__init__()
        self._title, self._body, self._value = title, body, value

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(self._title, id="modal_title")
                if self._body:
                    yield Static(self._body, id="modal_body")
                yield Input(value=self._value, placeholder="~/code/my-app", id="path_input")
                yield Static("", id="path_hints")
                yield Static(hint_line(("↵", "add"), ("tab", "complete"), ("esc", "cancel")), id="modal_hint")

    def on_mount(self) -> None:
        enable_mouse()
        inp = self.query_one("#path_input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def on_unmount(self) -> None:
        disable_mouse()

    def action_complete(self) -> None:
        inp = self.query_one("#path_input", Input)
        done, names = complete_path(inp.value)
        inp.value = done
        inp.cursor_position = len(done)
        hints = self.query_one("#path_hints", Static)
        hints.update("  ".join(names[:8]) + (" …" if len(names) > 8 else "") if len(names) > 1 else "")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class _SelectScreen(TuiModalScreen[list[str] | None]):
    """Multi-select of projects: space toggles, ↵ saves, esc cancels."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    _SelectScreen #modal { width: 70%; max-width: 90; height: auto; max-height: 70%; }
    _SelectScreen OptionList { height: auto; max-height: 20; }
    _SelectScreen #modal_body { color: {ui.FG_DIM}; padding: 0 1; margin-bottom: 1; }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("space", "toggle", "Toggle", show=False),
        Binding("enter", "save", "Save", show=False, priority=True),
    ]

    def __init__(self, title: str, body: str, options: list[tuple[str, str, str]], chosen: set[str]) -> None:
        super().__init__()
        self._title, self._body = title, body
        self._options = options  # (id, label, colour)
        self._chosen = set(chosen)

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(self._title, id="modal_title")
                yield Static(self._body, id="modal_body")
                yield OptionList(id="select_list")
                yield Static(hint_line(("space", "toggle"), ("↵", "save"), ("esc", "cancel")), id="modal_hint")

    def on_mount(self) -> None:
        enable_mouse()
        self._fill()
        self.query_one("#select_list", OptionList).focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def _fill(self, keep: int | None = None) -> None:
        opts = self.query_one("#select_list", OptionList)
        opts.clear_options()
        for pid, label, color in self._options:
            on = pid in self._chosen
            t = Text(no_wrap=True)
            t.append("[x] " if on else "[ ] ", style=f"bold {ui.ACCENT}" if on else ui.FG_DIM)
            t.append("■ ", style=color)
            t.append(label, style=ui.FG)
            opts.add_option(Option(t, id=pid))
        if self._options:
            opts.highlighted = keep if keep is not None else 0

    def action_toggle(self) -> None:
        opts = self.query_one("#select_list", OptionList)
        idx = opts.highlighted
        if idx is None:
            return
        pid = self._options[idx][0]
        self._chosen.symmetric_difference_update({pid})
        self._fill(keep=idx)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.action_toggle()

    def action_save(self) -> None:
        self.dismiss([pid for pid, _l, _c in self._options if pid in self._chosen])

    def action_cancel(self) -> None:
        self.dismiss(None)


class _ConfirmScreen(TuiModalScreen[bool]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    _ConfirmScreen #modal { width: 60%; max-width: 80; height: auto; }
    _ConfirmScreen #confirm_prompt { padding: 0 1; color: {ui.FG}; margin-bottom: 1; }
    """

    BINDINGS = [
        Binding("y", "yes", "Yes", show=False),
        Binding("enter", "yes", "Yes", show=False),
        Binding("n", "no", "No", show=False),
        Binding("escape", "no", "Cancel", show=True),
    ]

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self._title, self._message = title, message

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(self._title, id="modal_title")
                yield Static(self._message, id="confirm_prompt")
                yield Static(hint_line(("y/↵", "confirm"), ("n/esc", "cancel")), id="modal_hint")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class _SettingsScreen(TuiModalScreen[None]):
    """Workspace-wide settings: ↵ toggles a switch / edits a number."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    _SettingsScreen #modal { width: 70%; max-width: 90; height: auto; max-height: 70%; }
    _SettingsScreen OptionList { height: auto; max-height: 12; }
    """
    )

    BINDINGS = [Binding("escape", "close", "Close", show=True)]

    NOTES = {
        "warm_limit": "projects kept running in the background while idle",
        "idle_stop_minutes": "stop an idle background project after N minutes (0 = never)",
        "link_all": "every project may read every other",
        "delegation": "the agent may hand a task to another project's Jarvis",
    }

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⚙  Workspace settings", id="modal_title")
                yield OptionList(id="settings_list")
                yield Static(hint_line(("↵", "change"), ("esc", "close")), id="modal_hint")

    def on_mount(self) -> None:
        enable_mouse()
        self._fill()
        self.query_one("#settings_list", OptionList).focus()

    def on_unmount(self) -> None:
        disable_mouse()

    def _fill(self, keep: int = 0) -> None:
        opts = self.query_one("#settings_list", OptionList)
        opts.clear_options()
        settings = workspace.load()["settings"]
        for key in workspace.DEFAULT_SETTINGS:
            val = settings[key]
            shown = ("on" if val else "off") if isinstance(val, bool) else str(val)
            opts.add_option(Option(picker_row(key, detail=self.NOTES.get(key, ""), right=shown), id=key))
        opts.highlighted = keep

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        key = str(event.option.id)
        idx = event.option_index
        current = workspace.load()["settings"][key]
        if isinstance(current, bool):
            self._save(key, not current, idx)
            return

        def done(value: str | None) -> None:
            if value is None or not value.strip():
                return
            if not value.strip().isdigit():
                self.app.notify(f"{key} must be a whole number", severity="warning", timeout=3)
                return
            self._save(key, int(value.strip()), idx)

        self.app.push_screen(TextInputScreen(f"⚙  {key}", self.NOTES.get(key, ""), value=str(current)), done)

    def _save(self, key: str, value: object, idx: int) -> None:
        try:
            workspace.update_settings(**{key: value})
        except WorkspaceError as exc:
            self.app.notify(str(exc), severity="warning", timeout=3)
        self._fill(keep=idx)

    def action_close(self) -> None:
        self.dismiss(None)


class ProjectPickerScreen(TuiModalScreen[str | None]):
    """The workspace list. Dismisses with the project id to switch to, or ``None``."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    ProjectPickerScreen.tui-modal-screen #modal {
        width: 84%;
        max-width: 120;
        height: 70%;
        max-height: 34;
    }
    ProjectPickerScreen OptionList { height: 1fr; }
    """
    )

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("a", "add", "Add", show=False),
        Binding("d", "remove", "Remove", show=False),
        Binding("l", "links", "Links", show=False),
        Binding("w", "write_policy", "Write policy", show=False),
        Binding("r", "rename", "Rename", show=False),
        Binding("s", "settings", "Settings", show=False),
        Binding("J", "move(1)", "Move down", show=False),
        Binding("K", "move(-1)", "Move up", show=False),
        Binding("shift+down", "move(1)", "Move down", show=False, priority=True),
        Binding("shift+up", "move(-1)", "Move up", show=False, priority=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("▣  Projects", id="modal_title")
                yield Static("", id="modal_status")
                yield OptionList(id="project_list")
                yield Static(
                    hint_line(("↑↓", "navigate"), ("↵", "switch"), ("a", "add"), ("d", "remove"),
                              ("l", "links"), ("w", "writes"), ("r", "rename"), ("J/K", "move"),
                              ("s", "settings"), ("esc", "close")),
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._rows: list[dict] = []
        self._repaint(keep=pc_current_id())
        self.query_one("#project_list", OptionList).focus()

    def on_unmount(self) -> None:
        disable_mouse()

    # ── data / painting ────────────────────────────────────────────
    def _repaint(self, keep: str | None = None) -> None:
        opts = self.query_one("#project_list", OptionList)
        self._rows = pc.project_rows()
        opts.clear_options()
        if not self._rows:
            opts.add_option(empty_row("No projects yet — press a to add this folder"))
            self._status_line()
            return
        width = min(24, max(len(r["name"]) for r in self._rows))
        for row in self._rows:
            label, color = _STATUS[row["status"]]
            right = label
            if row["links"]:
                right += f"   ⇄ {', '.join(row['link_ids'][:3])}{' …' if len(row['links']) > 3 else ''} ({row['link_write']})"
            opts.add_option(Option(
                picker_row(
                    row["name"], detail=row["display"], right=right,
                    active=row["status"] == "current", icon="●", icon_style=row["color"],
                    right_style=color, title_width=width,
                ),
                id=row["id"],
            ))
        try:
            opts.highlighted = opts.get_option_index(keep) if keep else None
        except Exception:
            opts.highlighted = None
        if opts.highlighted is None:
            opts.action_first()
        self._status_line()

    def _status_line(self) -> None:
        cur = workspace.current()
        n = len(self._rows)
        bits = [f"[bold {ui.ACCENT}]{n} project{'s' if n != 1 else ''}[/]"]
        if cur:
            bits.append(f"[{ui.FG_DIM}]here[/] [bold {ui.FG}]{escape(cur['name'])}[/]")
        else:
            bits.append(f"[{ui.WARN}]this folder isn't in the workspace — a adds it[/]")
        self.query_one("#modal_status", Static).update(f"   [{ui.FG_DIM}]·[/]   ".join(bits))

    def _highlighted(self) -> dict | None:
        opts = self.query_one("#project_list", OptionList)
        if opts.option_count == 0 or opts.highlighted is None:
            return None
        pid = opts.get_option_at_index(opts.highlighted).id
        return next((r for r in self._rows if r["id"] == pid), None)

    def _refocus(self) -> None:
        try:
            self.query_one("#project_list", OptionList).focus()
        except Exception:
            pass

    def _fail(self, exc: Exception) -> None:
        self.app.notify(str(exc), severity="warning", timeout=4)

    # ── events / actions ───────────────────────────────────────────
    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        row = next((r for r in self._rows if r["id"] == event.option.id), None)
        if row is None:
            return
        if row["status"] == "missing":
            self.app.notify(f"{row['display']} no longer exists — d removes it", severity="warning", timeout=4)
            return
        self.dismiss(row["id"])

    def action_close(self) -> None:
        self.dismiss(None)

    def action_add(self) -> None:
        def done(value: str | None) -> None:
            self._refocus()
            if value is None:
                return
            try:
                paths = shlex.split(value) if value.strip() else []
            except ValueError:
                paths = value.split()
            out = workspace.add_many([os.path.abspath(os.path.expanduser(p)) for p in paths or [os.getcwd()]])
            for proj in out["added"]:
                self.app.notify(f"Added {proj['name']}", timeout=2.5)
            for err in out["errors"]:
                self.app.notify(f"{pc.display(err['path'])}: {err['error']}", severity="warning", timeout=5)
            keep = out["added"][-1]["id"] if out["added"] else None
            self._repaint(keep=keep)

        self.app.push_screen(
            PathInputScreen("▣  Add project", "Folder to add (~ works, tab completes). Enter keeps this folder.",
                            value=pc.display(os.getcwd())),
            done,
        )

    def action_remove(self) -> None:
        row = self._highlighted()
        if row is None:
            return

        def done(yes: bool | None) -> None:
            self._refocus()
            if not yes:
                return
            try:
                workspace.remove(row["id"])
            except WorkspaceError as exc:
                self._fail(exc)
            self._repaint()

        self.app.push_screen(
            _ConfirmScreen("⚠  Remove project?",
                           f"Drop [bold]{escape(row['name'])}[/] from the workspace? The folder itself is untouched."),
            done,
        )

    def action_links(self) -> None:
        row = self._highlighted()
        if row is None:
            return
        others = [(r["id"], f"{r['name']}  {r['display']}", r["color"]) for r in self._rows if r["id"] != row["id"]]
        if not others:
            self.app.notify("Add another project first", timeout=3)
            return

        def done(chosen: list[str] | None) -> None:
            self._refocus()
            if chosen is None:
                return
            try:
                workspace.update(row["id"], links=chosen)
            except WorkspaceError as exc:
                self._fail(exc)
            self._repaint(keep=row["id"])

        self.app.push_screen(
            _SelectScreen(f"⇄  Links from {row['name']}", "Projects this one's agent may read (writes: "
                          f"{row['link_write']} — w cycles it).", others, set(row["link_ids"])),
            done,
        )

    def action_write_policy(self) -> None:
        row = self._highlighted()
        if row is None:
            return
        nxt = _WRITE_CYCLE[(_WRITE_CYCLE.index(row["link_write"]) + 1) % len(_WRITE_CYCLE)]
        try:
            workspace.update(row["id"], link_write=nxt)
        except WorkspaceError as exc:
            self._fail(exc)
            return
        self.app.notify(f"{row['name']}: writes into linked projects → {nxt}", timeout=2.5)
        self._repaint(keep=row["id"])

    def action_rename(self) -> None:
        row = self._highlighted()
        if row is None:
            return

        def done(value: str | None) -> None:
            self._refocus()
            if value is None or not value.strip() or value.strip() == row["name"]:
                return
            try:
                workspace.update(row["id"], name=value.strip())
            except WorkspaceError as exc:
                self._fail(exc)
            self._repaint(keep=row["id"])

        self.app.push_screen(TextInputScreen("✎  Rename project", f"New name for {row['name']}", value=row["name"]), done)

    def action_settings(self) -> None:
        self.app.push_screen(_SettingsScreen(), lambda _r: self._refocus())

    def action_move(self, delta: int) -> None:
        row = self._highlighted()
        if row is None:
            return
        ids = [r["id"] for r in self._rows]
        i = ids.index(row["id"])
        j = i + int(delta)
        if not 0 <= j < len(ids):
            return
        ids[i], ids[j] = ids[j], ids[i]
        workspace.reorder(ids)
        self._repaint(keep=row["id"])


def pc_current_id() -> str | None:
    cur = workspace.current()
    return cur["id"] if cur else None
