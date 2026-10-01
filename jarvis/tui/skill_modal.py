"""Centered skill browser modal — read-only catalog of available skills.

Skills are auto-invoked by the LLM (it sees their descriptions in the system
prompt and decides when to call `/skill load <name>` itself). This modal is
purely for human browsing/discovery — pressing Enter on a skill opens its
content in the transcript so the user can read what's in there, but it does
NOT change any persistent activation state.

* ↑/↓ to navigate
* Enter to preview the highlighted skill's body in the transcript
* a to install a skill from a link (GitHub, SKILL.md, archive, folder)
* d to remove (press twice) · m to move project ↔ global · u to update from its source
* i to import the highlighted global skill into this project
* e to export the highlighted project skill to your global config
* g to toggle global-scope visibility (re-scans + reloads list)
* r to refresh (re-scan disk)
* Esc to close
"""
from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Horizontal, Vertical
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option


from ..storage import skill_install as si
from ..storage import skills as sk
from .. import state
from .modal_chrome import (
    TUI_MODAL_CHROME_CSS, TuiModalScreen, ROW_NAME_WIDTH, empty_row, hint_line, picker_row, section_header,
)
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui
from ..utils.origins import tool_icon, tool_tag, tools_in


class SkillBrowserScreen(TuiModalScreen[str | None]):
    """Browse available skills. Returns the previewed skill name, or None."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    SkillBrowserScreen #modal {
        width: 78%;
        max-width: 120;
        max-height: 80%;
    }
    SkillBrowserScreen OptionList {
        height: 1fr;
        min-height: 12;
    }
    SkillBrowserScreen Input { margin-bottom: 1; }
    SkillBrowserScreen #skill_buttons { height: 1; margin-top: 1; }
    SkillBrowserScreen #skill_buttons Button { margin: 0 1 0 0; }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("g", "toggle_global", "Global", show=True),
        Binding("a", "add_skill", "Install", show=True),
        Binding("d", "remove_skill", "Remove", show=True),
        Binding("m", "move_skill", "Move", show=False),
        Binding("u", "update_skill", "Update", show=False),
        Binding("i", "import_to_project", "Import", show=True),
        Binding("e", "export_to_global", "Export", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("slash", "focus_search", "Search", show=True),
    ]

    def __init__(self, *, add: bool = False) -> None:
        super().__init__()
        self._open_add = add
        self._rows: dict[str, dict] = {}
        self._armed_remove: str | None = None

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("★  Skills", id="modal_title")
                yield Static("", id="modal_status")
                yield Input(placeholder="search name, description or tool (claude, cursor…)", id="skill_search")
                yield OptionList(id="skill_list")
                with Horizontal(id="skill_buttons"):
                    yield Button("+ Install skill", id="skill_add", variant="primary", compact=True)
                yield Static(
                    hint_line(("↵", "preview"), ("a", "install from link"), ("d", "remove"), ("m", "move"),
                              ("u", "update"), ("g", "global"), ("i/e", "import/export"), ("esc", "close")),
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
        if self._open_add:
            self.call_after_refresh(self.action_add_skill)

    def on_unmount(self) -> None:
        disable_mouse()
        if self._prev_scroll_y is not None:
            try:
                self.app.scroll_sensitivity_y = self._prev_scroll_y
            except AttributeError:
                pass

    # ── content ────────────────────────────────────────────────────────────

    def _populate(self) -> None:
        opts = self.query_one("#skill_list", OptionList)
        opts.clear_options()

        # Inline filter when the search box has text.
        try:
            q = (self.query_one("#skill_search", Input).value or "").strip().lower()
        except Exception:
            q = ""
        info = si.describe_installed(q)
        skills = info["skills"]
        self._rows = {r["name"]: r for r in skills}

        if not skills:
            opts.add_option(empty_row(
                "No skills yet — press a to install one from a link (or drop a SKILL.md into .harness/skills/<name>/)"
            ))
            opts.highlighted = 0
            opts.focus()
            self._refresh_title()
            return

        project = [s for s in skills if s.get("scope") == "project"]
        glob = [s for s in skills if s.get("scope") == "global"]

        if project:
            opts.add_option(section_header("Project", " · ".join(tools_in(project)), first=True))
            for s in project:
                opts.add_option(Option(_format_skill_row(s, q), id=s["name"]))

        if glob:
            note = " · ".join(tools_in(glob))
            if not state.global_skills:
                note += " — hidden from Jarvis: press g to turn global skills on"
            opts.add_option(section_header("Global", note, first=not project,
                                           note_style=None if state.global_skills else ui.WARN))
            for s in glob:
                opts.add_option(Option(_format_skill_row(s, q), id=s["name"]))

        opts.action_first()
        opts.focus()
        self._refresh_title()

    def _refresh_title(self) -> None:
        scope = "project + global" if state.global_skills else "project"
        count = len([r for r in self._rows.values() if r.get("active")])
        try:
            self.query_one("#modal_title", Static).update(
                f"★  Skills   [{ui.FG_DIM}]{count} available · scope: {scope} · LLM auto-invokes[/]"
            )
        except Exception:
            pass

    # ── bindings ───────────────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id
        if not oid:
            return
        # Preview the skill body in the transcript (does not "activate" it).
        try:
            from rich.panel import Panel
            from rich.markdown import Markdown
            content = sk.load_skill(oid) or "(empty)"
            log = self.app.query_one("#transcript")
            log.write(Panel(
                Markdown(content),
                title=f"⚙ Skill preview: {oid}",
                border_style="cyan",
            ), expand=True)
        except Exception:
            pass
        self.dismiss(oid)

    def action_dismiss_cancel(self) -> None:
        # If search has text, Esc clears it; otherwise close.
        try:
            sb = self.query_one("#skill_search", Input)
            if sb.value:
                sb.value = ""
                self._populate()
                self.query_one("#skill_list", OptionList).focus()
                return
        except Exception:
            pass
        self.dismiss(None)

    def action_focus_search(self) -> None:
        self.query_one("#skill_search", Input).focus()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if self._armed_remove and self._current_skill_name() != self._armed_remove:
            self._armed_remove = None

    def action_cursor_down(self) -> None:
        self.query_one("#skill_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#skill_list", OptionList).action_cursor_up()

    def _current_skill_name(self) -> str | None:
        opts = self.query_one("#skill_list", OptionList)
        if opts.highlighted is None:
            return None
        try:
            opt = opts.get_option_at_index(opts.highlighted)
        except Exception:
            return None
        oid = getattr(opt, "id", None)
        return oid if oid else None

    def action_toggle_global(self) -> None:
        state.global_skills = not state.global_skills
        state.save_skills_config()
        self._populate()
        self._notify(
            "◎ global skills shown" if state.global_skills else "▣ project-only skills"
        )

    def _selected_row(self) -> dict | None:
        name = self._current_skill_name()
        return self._rows.get(name) if name else None

    def action_add_skill(self) -> None:
        from .extension_modals import SkillInstallScreen

        def after(result: dict | None) -> None:
            sk.invalidate_cache()
            self._populate()
            if result and result.get("installed"):
                names = ", ".join(i["name"] for i in result["installed"][:4])
                self._notify(f"installed {names} → {result.get('scope', '')}")

        self.app.push_screen(SkillInstallScreen(), after)

    def action_remove_skill(self) -> None:
        row = self._selected_row()
        if not row:
            return
        if not row.get("managed"):
            self._notify(f"{row['name']} lives in {row['source_dir']} (another tool's folder) — remove it there",
                         error=True)
            return
        if self._armed_remove != row["name"]:
            self._armed_remove = row["name"]
            self._notify(f"press d again to remove {row['name']} ({row['scope']})", error=True)
            return
        self._armed_remove = None
        res = si.remove_skill(row["name"], scope=row["scope"])
        self._populate()
        self._notify(res.get("message") or res.get("error", "could not remove"), error=not res.get("ok"))

    def action_move_skill(self) -> None:
        row = self._selected_row()
        if not row:
            return
        res = si.move_skill(row["name"], "global" if row["scope"] == "project" else "project")
        self._populate()
        self._notify(res.get("message") or res.get("error", "could not move"), error=not res.get("ok"))

    def action_update_skill(self) -> None:
        row = self._selected_row()
        if not row:
            return
        self._notify(f"updating {row['name']} from {row.get('origin') or 'its source'}…")
        self._update_worker(row["name"])

    @work(thread=True)
    def _update_worker(self, name: str) -> None:
        try:
            res = si.update_skill(name)
        except Exception as exc:
            res = {"ok": False, "error": str(exc)}
        try:
            self.app.call_from_thread(self._updated, res)
        except Exception:
            pass

    def _updated(self, res: dict) -> None:
        self._populate()
        if res.get("ok"):
            names = ", ".join(i["name"] for i in res.get("installed", []))
            self._notify(f"updated {names}")
        else:
            self._notify(res.get("error", "could not update"), error=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "skill_add":
            self.action_add_skill()

    def action_import_to_project(self) -> None:
        name = self._current_skill_name()
        if not name:
            self._notify("(highlight a global skill to import)", error=True)
            return
        result = sk.import_skill_to_project(name)
        if result.get("error"):
            self._notify(str(result["error"]), error=True)
            return
        sk.invalidate_cache()
        self._populate()
        if result.get("added"):
            self._notify(f"imported {name} → {result['path']}")
        elif result.get("skipped"):
            self._notify(f"{name} already in project")
        else:
            self._notify("nothing to import", error=True)

    def action_export_to_global(self) -> None:
        name = self._current_skill_name()
        if not name:
            self._notify("(highlight a project skill to export)", error=True)
            return
        result = sk.export_skill_to_global(name)
        if result.get("error"):
            self._notify(str(result["error"]), error=True)
            return
        sk.invalidate_cache()
        self._populate()
        if result.get("added"):
            self._notify(f"exported {name} → {result['path']}")
        elif result.get("skipped"):
            self._notify(f"{name} already in global")
        else:
            self._notify("nothing to export", error=True)

    def action_refresh(self) -> None:
        try:
            self.query_one("#skill_search", Input).value = ""
        except Exception:
            pass
        self._populate()
        self._notify("re-scanned disk")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "skill_search":
            self._populate()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "skill_search":
            self.query_one("#skill_list", OptionList).focus()

    def _notify(self, msg: str, error: bool = False) -> None:
        try:
            color = ui.ERR if error else ui.OK
            self.query_one("#modal_status", Static).update(f"[{color}]{msg}[/]")
        except Exception:
            pass


def _format_skill_row(skill: dict, query: str = ""):
    active = skill.get("active", True)
    origin = skill.get("origin") or ""
    if origin.startswith(("/", "~", ".")):
        origin = origin.rstrip("/").rsplit("/", 1)[-1]  # a local folder: its name, not the whole path
    if len(origin) > 26:
        origin = origin[:25] + "…"
    tool = skill.get("tool") or "jarvis"
    right = f"{tool_icon(tool)} {tool_tag(tool, skill.get('also') or ())}"
    if origin:
        right += f" · from {origin}"
    if not active:
        right += " · hidden"
    return picker_row(
        skill.get("name", ""),
        detail=(skill.get("description") or "").replace("\n", " "),
        right=right,
        query=query,
        icon="✧",
        icon_style=ui.ACCENT_3 if active else ui.FG_DIM,
        title_style=None if active else ui.FG_DIM,
        detail_style=None if active else ui.FG_DIM,
        title_width=ROW_NAME_WIDTH,
    )
