"""Centered skill browser modal — read-only catalog of available skills.

Skills are auto-invoked by the LLM (it sees their descriptions in the system
prompt and decides when to call `/skill load <name>` itself). This modal is
purely for human browsing/discovery — pressing Enter on a skill opens its
content in the transcript so the user can read what's in there, but it does
NOT change any persistent activation state.

* ↑/↓ to navigate
* Enter to preview the highlighted skill's body in the transcript
* g to toggle global-scope visibility (re-scans + reloads list)
* r to refresh (re-scan disk)
* Esc to close
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..storage import skills as sk
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen
from .mouse_toggle import enable_mouse, disable_mouse


class SkillBrowserScreen(TuiModalScreen[str | None]):
    """Browse available skills. Returns the previewed skill name, or None."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    SkillBrowserScreen #modal {
        width: 72%;
        max-width: 110;
        max-height: 80%;
        padding: 2 3;
    }
    SkillBrowserScreen OptionList {
        height: 1fr;
        min-height: 8;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("g", "toggle_global", "Global on/off", show=True),
        Binding("r", "refresh", "Refresh", show=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("🧰  skills (LLM auto-invokes by description)", id="modal_title")
                yield OptionList(id="skill_list")
                yield Static(
                    "↑/↓ navigate • Enter preview • g global • r refresh • Esc close",
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
                Text("── PROJECT  (.harness/skills/, .skills/, .claude/skills/, …) ──", style="dim bold"),
                disabled=True,
            ))
            for s in project:
                opts.add_option(Option(_format_skill_row(s), id=s["name"]))

        if glob:
            opts.add_option(Option(Text("", style="dim"), disabled=True))
            opts.add_option(Option(
                Text("── GLOBAL  (~/.harness/skills/, ~/.claude/skills/, …) ──", style="dim bold"),
                disabled=True,
            ))
            for s in glob:
                opts.add_option(Option(_format_skill_row(s), id=s["name"]))

        if not state.global_skills:
            gc = sk.global_count()
            if gc:
                opts.add_option(Option(Text("", style="dim"), disabled=True))
                opts.add_option(Option(
                    Text(f"  ({gc} global skills hidden — press 'g' to show)", style="dim italic"),
                    disabled=True,
                ))

        opts.highlighted = 1 if opts.option_count > 1 else 0
        opts.focus()
        self._refresh_title()

    def _refresh_title(self) -> None:
        scope = "🌍 project + global" if state.global_skills else "📁 project"
        count = len(sk.discover_skills())
        try:
            self.query_one("#modal_title", Static).update(
                f"🧰  skills  [dim]· {count} available · {scope} · LLM auto-invokes[/]"
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
                title=f"🧰 Skill preview: {oid}",
                border_style="cyan",
            ))
        except Exception:
            pass
        self.dismiss(oid)

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#skill_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#skill_list", OptionList).action_cursor_up()

    def action_toggle_global(self) -> None:
        state.global_skills = not state.global_skills
        state.save_skills_config()
        self._populate()

    def action_refresh(self) -> None:
        self._populate()


def _format_skill_row(skill: dict) -> Text:
    name = skill.get("name", "")
    desc = skill.get("description", "")
    return Text.assemble(
        ("  ", ""),
        (f"{name:<22s}", "bold cyan"),
        ("  ", ""),
        (desc[:100], "dim"),
    )
