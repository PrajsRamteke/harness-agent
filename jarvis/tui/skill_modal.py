"""Centered skill browser modal — read-only catalog of available skills.

Skills are auto-invoked by the LLM (it sees their descriptions in the system
prompt and decides when to call `/skill load <name>` itself). This modal is
purely for human browsing/discovery — pressing Enter on a skill opens its
content in the transcript so the user can read what's in there, but it does
NOT change any persistent activation state.

* ↑/↓ to navigate
* Enter to preview the highlighted skill's body in the transcript
* i to import the highlighted global skill into this project
* g to toggle global-scope visibility (re-scans + reloads list)
* r to refresh (re-scan disk)
* Esc to close
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..storage import skills as sk
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, ROW_NAME_WIDTH, _ellipsis, modal_key, primary_style
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


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
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("g", "toggle_global", "Global", show=True),
        Binding("i", "import_to_project", "Import", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("slash", "focus_search", "Search", show=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("★  Skills", id="modal_title")
                yield Static("", id="modal_status")
                yield Input(placeholder="search name or description…", id="skill_search")
                yield OptionList(id="skill_list")
                yield Static(
                    f"{modal_key('↑↓')} navigate   {modal_key('↵')} preview   {modal_key('i')} import   "
                    f"{modal_key('/')} search   {modal_key('g')} global   "
                    f"{modal_key('r')} refresh   {modal_key('esc')} close",
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
        opts = self.query_one("#skill_list", OptionList)
        opts.clear_options()

        skills = sk.discover_skills(force=True)
        # Apply inline filter when the search box has text.
        try:
            q = (self.query_one("#skill_search", Input).value or "").strip().lower()
        except Exception:
            q = ""
        if q:
            skills = [
                s for s in skills
                if q in s["name"].lower() or q in s.get("description", "").lower()
            ]

        if not skills:
            opts.add_option(Option(
                Text("(no skills found — drop SKILL.md files into .harness/skills/<name>/)", style="dim"),
                disabled=True,
            ))
            opts.highlighted = 0
            opts.focus()
            self._refresh_title()
            return

        project = [s for s in skills if s.get("scope") == "project"]
        glob = [s for s in skills if s.get("scope") == "global"]

        if project:
            opts.add_option(Option(
                Text("  PROJECT  ·  .harness/skills/  .skills/  .claude/skills/",
                     style=f"bold {ui.FG_DIM}"),
                disabled=True,
            ))
            for s in project:
                opts.add_option(Option(_format_skill_row(s), id=s["name"]))

        if glob:
            opts.add_option(Option(Text(" ", style="dim"), disabled=True))
            opts.add_option(Option(
                Text("  GLOBAL   ·  ~/.harness/skills/  ~/.claude/skills/",
                     style=f"bold {ui.FG_DIM}"),
                disabled=True,
            ))
            for s in glob:
                opts.add_option(Option(_format_skill_row(s), id=s["name"]))

        if not state.global_skills:
            gc = sk.global_count()
            if gc:
                opts.add_option(Option(Text(" ", style="dim"), disabled=True))
                opts.add_option(Option(
                    Text(f"  {gc} global skill{'s' if gc != 1 else ''} hidden — press 'g' to show",
                         style=f"italic {ui.FG_DIM}"),
                    disabled=True,
                ))

        opts.highlighted = 1 if opts.option_count > 1 else 0
        opts.focus()
        self._refresh_title()

    def _refresh_title(self) -> None:
        scope = "project + global" if state.global_skills else "project"
        count = len(sk.discover_skills())
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
            ))
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


def _format_skill_row(skill: dict) -> Text:
    name = skill.get("name", "")
    desc = skill.get("description", "")
    return Text.assemble(
        ("  ", ""),
        (f"{name:<{ROW_NAME_WIDTH}s}", primary_style(True)),
        ("  ", ""),
        (_ellipsis(desc), ui.FG_MUTE),
    )
