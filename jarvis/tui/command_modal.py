"""Centered custom-command manager modal — one place for every /command action.

User interaction:

* ↑/↓        navigate the command list
* Enter      insert `/<name> ` into the prompt box — type args, edit, send
* t          insert the command's full template text into the prompt box
* n          create a new command (in-app editor: name + description + template)
* e          edit the highlighted command in the same in-app editor — works
             for project AND global files, saves back where the file lives
* p          preview the highlighted command's template in the transcript
* d          delete the highlighted command (press twice to confirm)
* i          import the highlighted global command into this project
* x          export the highlighted project command to ~/.harness/commands/
* g          toggle global scope (project-only ↔ project + global)
* s          toggle which scope `new` writes to (project ↔ global)
* r          re-scan disk
* /          focus the search box
* Esc        close (clears search first if it has text)

Dismiss value: a string to place into the prompt input box, or None.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from rich.text import Text

from ..storage import commands as cc
from ..commands import command as command_cmd
from .. import state
from .modal_chrome import (
    TUI_MODAL_CHROME_CSS,
    TuiModalScreen,
    ROW_NAME_WIDTH,
    _ellipsis,
    modal_key,
    primary_style,
)
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


# ── command editor sub-modal (create + edit) ─────────────────────────────


class _CommandEditorScreen(TuiModalScreen[str | None]):
    """Full in-app editor: name + description inputs and a template text area.

    Used for both creating a new command and editing an existing one — the
    save goes back to wherever the file lives (project or global). Performs
    the write itself so a validation error keeps the user's input on screen.

    Dismiss value: the saved file path, or None when cancelled.
    """

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    _CommandEditorScreen #modal {
        width: 80%;
        max-width: 110;
        max-height: 90%;
    }
    _CommandEditorScreen .field_label {
        padding: 0 1;
        margin-top: 1;
    }
    _CommandEditorScreen #cmded_body {
        height: 1fr;
        min-height: 8;
        margin: 0 1;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    def __init__(self, scope: str, record: dict | None = None) -> None:
        super().__init__()
        self._scope = scope
        self._record = record  # None → create mode

    def compose(self) -> ComposeResult:
        rec = self._record or {}
        if rec:
            where = rec.get("path", "")
            title = f"✎  Edit Command /{rec.get('name', '')}   [{ui.FG_DIM}]{where}[/]"
        else:
            title = f"➕  New Command   [{ui.FG_DIM}]writes to {self._scope} scope[/]"
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(title, id="modal_title")
                yield Static("", id="modal_status")
                yield Static(
                    f"Name  [{ui.FG_DIM}](lowercase-kebab — triggered as /<name>)[/]",
                    classes="field_label",
                )
                yield Input(
                    value=rec.get("name", ""),
                    placeholder="command-name (e.g. pr-description)",
                    id="cmded_name",
                )
                yield Static(
                    f"Description  [{ui.FG_DIM}](one line, shown in lists and the palette)[/]",
                    classes="field_label",
                )
                yield Input(
                    value=rec.get("description", ""),
                    placeholder="what this command does",
                    id="cmded_desc",
                )
                yield Static(
                    f"Template  [{ui.FG_DIM}](the prompt that is sent — $ARGUMENTS and $1…$9 are filled from your input)[/]",
                    classes="field_label",
                )
                yield TextArea(rec.get("_body", "").strip(), id="cmded_body")
                yield Static(
                    f"{modal_key('^s')} save   {modal_key('tab')} next field   {modal_key('esc')} cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        # Creating → start at the name; editing → jump straight to the body.
        if self._record:
            self.query_one("#cmded_body", TextArea).focus()
        else:
            self.query_one("#cmded_name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "cmded_name":
            self.query_one("#cmded_desc", Input).focus()
        elif event.input.id == "cmded_desc":
            self.query_one("#cmded_body", TextArea).focus()

    def action_save(self) -> None:
        name = self.query_one("#cmded_name", Input).value.strip()
        desc = self.query_one("#cmded_desc", Input).value.strip()
        body = self.query_one("#cmded_body", TextArea).text
        if not name:
            self._notify("name is required", error=True)
            self.query_one("#cmded_name", Input).focus()
            return
        ok, msg = cc.write_command(
            name, desc, body,
            scope=self._scope,
            existing_path=(self._record or {}).get("path"),
        )
        if not ok:
            self._notify(f"✗ {msg}", error=True)
            return
        self.dismiss(msg)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _notify(self, msg: str, error: bool = False) -> None:
        try:
            color = ui.ERR if error else ui.OK
            self.query_one("#modal_status", Static).update(f"[{color}]{msg}[/]")
        except Exception:
            pass


# ── main command manager modal ───────────────────────────────────────────


class CommandManagerScreen(TuiModalScreen[str | None]):
    """One-stop control panel for custom slash commands.

    Dismiss values:
        - "/name " → caller inserts the invocation into the prompt box
        - template text → caller inserts it for direct editing
        - None → cancelled
    """

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    CommandManagerScreen #modal {
        width: 80%;
        max-width: 120;
        max-height: 85%;
    }
    CommandManagerScreen OptionList {
        height: 1fr;
        min-height: 12;
    }
    CommandManagerScreen Input { margin-bottom: 1; }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("t", "insert_template", "Template", show=True),
        Binding("n", "new_command", "New", show=True),
        Binding("e", "edit_command", "Edit", show=True),
        Binding("p", "preview_command", "Preview", show=True),
        Binding("d", "delete_command", "Delete", show=True),
        Binding("i", "import_to_project", "Import", show=True),
        Binding("x", "export_to_global", "Export", show=True),
        Binding("g", "toggle_global", "Global", show=True),
        Binding("s", "toggle_new_scope", "Scope", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("slash", "focus_search", "Search", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._pending_delete: str | None = None

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⌘  Commands", id="modal_title")
                yield Static("", id="modal_status")
                yield Input(placeholder="search name or description…", id="cmd_search")
                yield OptionList(id="cmd_list")
                yield Static(
                    f"{modal_key('↑↓')} nav · {modal_key('↵')} to input · {modal_key('t')} template   "
                    f"{modal_key('n')} new · {modal_key('e')} edit · {modal_key('p')} preview · {modal_key('d')} delete   "
                    f"{modal_key('i')} import · {modal_key('x')} export · {modal_key('g')} global · {modal_key('s')} scope   "
                    f"{modal_key('r')} refresh · {modal_key('esc')} close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        try:
            self._prev_scroll_y = self.app.scroll_sensitivity_y
            self.app.scroll_sensitivity_y = 1.0
        except AttributeError:
            self._prev_scroll_y = None
        self._populate()

    def on_unmount(self) -> None:
        disable_mouse()
        if self._prev_scroll_y is not None:
            try:
                self.app.scroll_sensitivity_y = self._prev_scroll_y
            except AttributeError:
                pass

    # ── content ────────────────────────────────────────────────────────────

    def _populate(self) -> None:
        opts = self.query_one("#cmd_list", OptionList)
        opts.clear_options()
        self._pending_delete = None

        cmds = cc.discover_commands(force=True)
        try:
            q = (self.query_one("#cmd_search", Input).value or "").strip().lower()
        except Exception:
            q = ""
        if q:
            cmds = [
                c for c in cmds
                if q in c["name"].lower() or q in c.get("description", "").lower()
            ]

        if not cmds:
            opts.add_option(Option(
                Text("  no commands found — press 'n' to create one, "
                     "or drop .md prompts into .harness/commands/",
                     style=f"italic {ui.FG_DIM}"),
                disabled=True,
            ))
            opts.highlighted = 0
            opts.focus()
            self._refresh_title()
            return

        project = [c for c in cmds if c.get("scope") == "project"]
        glob = [c for c in cmds if c.get("scope") == "global"]

        if project:
            opts.add_option(Option(
                Text("  PROJECT  ·  .harness/commands/  .claude/commands/",
                     style=f"bold {ui.FG_DIM}"),
                disabled=True,
            ))
            for c in project:
                opts.add_option(Option(_format_command_row(c), id=c["name"]))

        if glob:
            opts.add_option(Option(Text(" ", style="dim"), disabled=True))
            opts.add_option(Option(
                Text("  GLOBAL   ·  ~/.harness/commands/  ~/.claude/commands/",
                     style=f"bold {ui.FG_DIM}"),
                disabled=True,
            ))
            for c in glob:
                opts.add_option(Option(_format_command_row(c), id=c["name"]))

        if not state.global_commands:
            gc = cc.global_count()
            if gc:
                opts.add_option(Option(Text(" ", style="dim"), disabled=True))
                opts.add_option(Option(
                    Text(f"  {gc} global command{'s' if gc != 1 else ''} hidden — press 'g' to show",
                         style=f"italic {ui.FG_DIM}"),
                    disabled=True,
                ))

        opts.highlighted = 1 if opts.option_count > 1 else 0
        opts.focus()
        self._refresh_title()

    def _refresh_title(self) -> None:
        scope = "project + global" if state.global_commands else "project-only"
        new_scope = command_cmd.get_new_scope()
        count = len(cc.discover_commands())
        try:
            self.query_one("#modal_title", Static).update(
                f"⌘  Commands   [{ui.FG_DIM}]{count} available · scope: {scope} · trigger as /<name> \\[args][/]"
            )
            self.query_one("#modal_status", Static).update(
                f"[{ui.FG_DIM}]new writes to: [bold {ui.FG}]{new_scope}[/][/]"
            )
        except Exception:
            pass

    def _current_command_name(self) -> str | None:
        opts = self.query_one("#cmd_list", OptionList)
        if opts.highlighted is None:
            return None
        try:
            opt = opts.get_option_at_index(opts.highlighted)
        except Exception:
            return None
        oid = getattr(opt, "id", None)
        return oid if oid else None

    # ── bindings ───────────────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id
        if not oid:
            return
        # Hand the invocation to the prompt box — the user finishes it there.
        self.dismiss(f"/{oid} ")

    def action_insert_template(self) -> None:
        name = self._current_command_name()
        if not name:
            self._notify("(highlight a command first)", error=True)
            return
        rec = cc.find_command(name)
        body = (rec or {}).get("_body", "").strip()
        if not body:
            self._notify(f"'{name}' has an empty template", error=True)
            return
        self.dismiss(body)

    def action_dismiss_cancel(self) -> None:
        try:
            sb = self.query_one("#cmd_search", Input)
            if sb.value:
                sb.value = ""
                self._populate()
                self.query_one("#cmd_list", OptionList).focus()
                return
        except Exception:
            pass
        self.dismiss(None)

    def action_focus_search(self) -> None:
        self.query_one("#cmd_search", Input).focus()

    def action_cursor_down(self) -> None:
        self._pending_delete = None
        self.query_one("#cmd_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self._pending_delete = None
        self.query_one("#cmd_list", OptionList).action_cursor_up()

    def action_toggle_global(self) -> None:
        state.global_commands = not state.global_commands
        state.save_commands_config()
        cc.invalidate_cache()
        self._populate()
        self._notify(
            "◎ global commands shown" if state.global_commands else "▣ project-only commands"
        )

    def action_toggle_new_scope(self) -> None:
        cur = command_cmd.get_new_scope()
        command_cmd.set_new_scope("global" if cur == "project" else "project")
        self._refresh_title()

    def action_new_command(self) -> None:
        scope = command_cmd.get_new_scope()

        def after(saved_path: str | None) -> None:
            if not saved_path:
                return
            cc.invalidate_cache()
            self._populate()
            self._notify(f"✓ created {saved_path}")

        self.app.push_screen(_CommandEditorScreen(scope=scope), after)

    def action_edit_command(self) -> None:
        name = self._current_command_name()
        if not name:
            self._notify("(highlight a command to edit)", error=True)
            return
        rec = cc.find_command(name)
        if not rec:
            self._notify(f"command '{name}' not found", error=True)
            return

        def after(saved_path: str | None) -> None:
            if not saved_path:
                return
            cc.invalidate_cache()
            self._populate()
            self._notify(f"✓ saved {saved_path}")

        self.app.push_screen(
            _CommandEditorScreen(scope=rec.get("scope", "project"), record=rec), after
        )

    def action_preview_command(self) -> None:
        name = self._current_command_name()
        if not name:
            self._notify("(highlight a command to preview)", error=True)
            return
        rec = cc.find_command(name)
        body = (rec or {}).get("_body", "")
        try:
            from rich.panel import Panel
            from rich.markdown import Markdown
            log = self.app.query_one("#transcript")
            log.write(Panel(
                Markdown(body or "*(empty template)*"),
                title=f"⌘  Command preview: /{name}",
                border_style="cyan",
            ), expand=True)
            self._notify(f"previewed '/{name}' in transcript")
        except Exception as e:
            self._notify(f"preview failed: {e}", error=True)

    def action_delete_command(self) -> None:
        name = self._current_command_name()
        if not name:
            self._notify("(highlight a command to delete)", error=True)
            return
        if self._pending_delete != name:
            self._pending_delete = name
            self._notify(f"press 'd' again to delete /{name}", error=True)
            return
        self._pending_delete = None
        ok, msg = cc.delete_command(name)
        if ok:
            self._populate()
            self._notify(f"✓ deleted {msg}")
        else:
            self._notify(f"✗ {msg}", error=True)

    def action_import_to_project(self) -> None:
        name = self._current_command_name()
        if not name:
            self._notify("(highlight a global command to import)", error=True)
            return
        result = cc.import_command_to_project(name)
        if result.get("error"):
            self._notify(str(result["error"]), error=True)
            return
        cc.invalidate_cache()
        self._populate()
        if result.get("added"):
            self._notify(f"imported {name} → {result['path']}")
        elif result.get("skipped"):
            self._notify(f"{name} already in project")
        else:
            self._notify("nothing to import", error=True)

    def action_export_to_global(self) -> None:
        name = self._current_command_name()
        if not name:
            self._notify("(highlight a project command to export)", error=True)
            return
        result = cc.export_command_to_global(name)
        if result.get("error"):
            self._notify(str(result["error"]), error=True)
            return
        cc.invalidate_cache()
        self._populate()
        if result.get("added"):
            self._notify(f"exported {name} → {result['path']}")
        elif result.get("skipped"):
            self._notify(f"{name} already in global")
        else:
            self._notify("nothing to export", error=True)

    def action_refresh(self) -> None:
        try:
            self.query_one("#cmd_search", Input).value = ""
        except Exception:
            pass
        cc.invalidate_cache()
        self._populate()
        self._notify("re-scanned disk")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "cmd_search":
            self._populate()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "cmd_search":
            self.query_one("#cmd_list", OptionList).focus()

    def _notify(self, msg: str, error: bool = False) -> None:
        try:
            color = ui.ERR if error else ui.OK
            self.query_one("#modal_status", Static).update(f"[{color}]{msg}[/]")
        except Exception:
            pass


def _format_command_row(cmd: dict) -> Text:
    name = "/" + cmd.get("name", "")
    desc = cmd.get("description", "")
    hint = cmd.get("argument_hint", "")
    if hint:
        desc = f"{hint}  ·  {desc}" if desc else hint
    return Text.assemble(
        ("  ", ""),
        (f"{name:<{ROW_NAME_WIDTH}s}", primary_style(True)),
        ("  ", ""),
        (_ellipsis(desc), ui.FG_MUTE),
    )
