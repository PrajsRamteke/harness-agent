"""Centered agent control modal — one place for every agent action.

User interaction:

* ↑/↓        navigate the agent list
* Enter      activate the highlighted agent (or pick "default" to deactivate)
* o          deactivate — base system prompt only
* g          toggle global scope (project-only ↔ project + global)
* s          toggle which scope `new` writes to (project ↔ global)
* n          create a new agent (opens a name-input sub-modal)
* e          open the highlighted agent's .md file in $EDITOR
* p          preview the highlighted agent's body in the transcript
* r          re-scan disk
* Esc        close

Designed to make ``/agent`` the only agent command a user ever needs.
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

from ..storage import agents as ag
from ..commands import agent as agent_cmd
from .. import state
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, ROW_NAME_WIDTH, _ellipsis
from .mouse_toggle import enable_mouse, disable_mouse


_OFF_ID = "__off__"


# ── new-agent sub-modal ──────────────────────────────────────────────────


class _NewAgentScreen(TuiModalScreen[tuple[str, str] | None]):
    """Tiny input modal that collects (name, description) for `/agent new`."""

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    _NewAgentScreen #modal {
        width: 60%;
        max-width: 80;
        max-height: 50%;
    }
    _NewAgentScreen #newagent_name_label,
    _NewAgentScreen #newagent_desc_label {
        color: #8b949e;
        padding: 0 1;
    }
    _NewAgentScreen #newagent_desc_label { margin-top: 1; }
    """
    )

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

    def __init__(self, scope: str) -> None:
        super().__init__()
        self._scope = scope

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static(
                    f"➕  New Agent   [#6e7681]writes to {self._scope} scope[/]",
                    id="modal_title",
                )
                yield Static(
                    "Name  [#6e7681](lowercase-kebab — e.g. python-debugger)[/]",
                    id="newagent_name_label",
                )
                yield Input(placeholder="agent-name", id="newagent_name")
                yield Static(
                    "Description  [#6e7681](one line)[/]",
                    id="newagent_desc_label",
                )
                yield Input(placeholder="what this agent does", id="newagent_desc")
                yield Static(
                    "[#f0b3ff]↵[/] on description to create   [#f0b3ff]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        self.query_one("#newagent_name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "newagent_name":
            self.query_one("#newagent_desc", Input).focus()
            return
        name = self.query_one("#newagent_name", Input).value.strip()
        desc = self.query_one("#newagent_desc", Input).value.strip()
        if not name:
            return
        self.dismiss((name, desc))

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── main agent control modal ─────────────────────────────────────────────


class AgentPickerScreen(TuiModalScreen[dict | str | None]):
    """One-stop control panel for agents.

    Dismiss values:
        - dict   → an agent record (caller activates it)
        - "off"  → user picked "default" / deactivate
        - None   → cancelled
    """

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    AgentPickerScreen #modal {
        width: 80%;
        max-width: 120;
        max-height: 85%;
    }
    AgentPickerScreen OptionList {
        height: 1fr;
        min-height: 12;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("g", "toggle_global", "Global", show=True),
        Binding("s", "toggle_new_scope", "Scope", show=True),
        Binding("n", "new_agent", "New", show=True),
        Binding("e", "edit_agent", "Edit", show=True),
        Binding("p", "preview_agent", "Preview", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("o", "deactivate", "Default", show=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("🎛  Agents", id="modal_title")
                yield Static("", id="modal_status")
                yield OptionList(id="agent_list")
                yield Static(
                    "[#f0b3ff]↑↓[/] nav   [#f0b3ff]↵[/] activate   [#f0b3ff]o[/] default   "
                    "[#f0b3ff]n[/] new   [#f0b3ff]e[/] edit   [#f0b3ff]p[/] preview   "
                    "[#f0b3ff]g[/] global   [#f0b3ff]s[/] scope   [#f0b3ff]r[/] refresh   "
                    "[#f0b3ff]esc[/] close",
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
        opts = self.query_one("#agent_list", OptionList)
        opts.clear_options()
        active = state.active_agent_name

        # "default" row (no agent → base system prompt)
        off_text = Text.assemble(
            ("● " if not active else "  ", "bold #3fb950"),
            ("    ", ""),  # icon slot — keeps alignment with rows that have one
            (f"{'default':<{ROW_NAME_WIDTH}s}",
             "bold #79c0ff" if not active else "#79c0ff"),
            ("  ", ""),
            ("base system prompt only", "#8b949e"),
        )
        opts.add_option(Option(off_text, id=_OFF_ID))

        agents = ag.discover_agents(force=True)
        if not agents:
            opts.add_option(Option(Text(" ", style="dim"), disabled=True))
            opts.add_option(Option(
                Text(
                    "  no agents found — press 'n' to create one, "
                    "or drop files in .harness/agents/",
                    style="italic #6e7681",
                ),
                disabled=True,
            ))
            opts.highlighted = 0
            opts.focus()
            self._refresh_status()
            return

        project = [a for a in agents if a.get("scope") == "project"]
        glob = [a for a in agents if a.get("scope") == "global"]

        if project:
            opts.add_option(Option(Text(" ", style="dim"), disabled=True))
            opts.add_option(Option(
                Text("  PROJECT  ·  .harness/agents/  .claude/agents/",
                     style="bold #6e7681"),
                disabled=True,
            ))
            for a in project:
                opts.add_option(Option(_format_agent_row(a, active), id=a["name"]))

        if glob:
            opts.add_option(Option(Text(" ", style="dim"), disabled=True))
            opts.add_option(Option(
                Text("  GLOBAL   ·  ~/.harness/agents/  ~/.claude/agents/",
                     style="bold #6e7681"),
                disabled=True,
            ))
            for a in glob:
                opts.add_option(Option(_format_agent_row(a, active), id=a["name"]))

        if not state.global_agents:
            gc = ag.global_count()
            if gc:
                opts.add_option(Option(Text(" ", style="dim"), disabled=True))
                opts.add_option(Option(
                    Text(f"  {gc} global agent{'s' if gc != 1 else ''} hidden — press 'g' to show",
                         style="italic #6e7681"),
                    disabled=True,
                ))

        self._highlight_active()
        opts.focus()
        self._refresh_status()

    def _refresh_status(self) -> None:
        scope = "project + global" if state.global_agents else "project-only"
        new_scope = agent_cmd.get_new_scope()
        count = len(ag.discover_agents())
        active = state.active_agent_name or "default"
        try:
            self.query_one("#modal_title", Static).update(
                f"🎛  Agents   [#6e7681]{count} available · scope: {scope}[/]"
            )
            self.query_one("#modal_status", Static).update(
                f"active: [bold #79c0ff]{active}[/]   ·   new writes to: [bold #e6edf3]{new_scope}[/]"
            )
        except Exception:
            pass

    def _highlight_active(self) -> None:
        opts = self.query_one("#agent_list", OptionList)
        target = state.active_agent_name or _OFF_ID
        for idx in range(opts.option_count):
            try:
                opt = opts.get_option_at_index(idx)
            except Exception:
                continue
            if getattr(opt, "id", None) == target:
                opts.highlighted = idx
                return
        opts.highlighted = 0

    def _current_agent_name(self) -> str | None:
        """Return the ID of the highlighted row (or None for placeholders)."""
        opts = self.query_one("#agent_list", OptionList)
        if opts.highlighted is None:
            return None
        try:
            opt = opts.get_option_at_index(opts.highlighted)
        except Exception:
            return None
        oid = getattr(opt, "id", None)
        if not oid or oid == _OFF_ID:
            return None
        return oid

    # ── bindings ───────────────────────────────────────────────────────────

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id
        if oid == _OFF_ID:
            self.dismiss("off")
            return
        if not oid:
            self.dismiss(None)
            return
        rec = ag.find_agent(oid)
        if rec is None:
            self.dismiss(None)
            return
        self.dismiss(rec)

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#agent_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#agent_list", OptionList).action_cursor_up()

    def action_toggle_global(self) -> None:
        state.global_agents = not state.global_agents
        state.save_agent_config()
        ag.invalidate_cache()
        self._populate()

    def action_refresh(self) -> None:
        ag.invalidate_cache()
        self._populate()

    def action_deactivate(self) -> None:
        self.dismiss("off")

    def action_toggle_new_scope(self) -> None:
        cur = agent_cmd.get_new_scope()
        agent_cmd.set_new_scope("global" if cur == "project" else "project")
        self._refresh_status()

    def action_new_agent(self) -> None:
        scope = agent_cmd.get_new_scope()

        def after(result: tuple[str, str] | None) -> None:
            if not result:
                return
            name, desc = result
            ok, msg = ag.scaffold_agent(name, scope=scope, description=desc)
            if not ok:
                self._notify(f"✗ {msg}", error=True)
                return
            ag.invalidate_cache()
            self._populate()
            # Try to open the new file in $EDITOR.
            editor = os.environ.get("EDITOR")
            if editor:
                try:
                    subprocess.Popen([editor, msg])
                except Exception:
                    pass
            self._notify(f"✓ created {msg}")

        self.app.push_screen(_NewAgentScreen(scope=scope), after)

    def action_edit_agent(self) -> None:
        name = self._current_agent_name()
        if not name:
            self._notify("(highlight an agent to edit)")
            return
        rec = ag.find_agent(name)
        if not rec:
            self._notify(f"agent '{name}' not found", error=True)
            return
        editor = os.environ.get("EDITOR")
        if not editor:
            self._notify(f"$EDITOR not set — file: {rec['path']}", error=True)
            return
        try:
            subprocess.Popen([editor, rec["path"]])
            self._notify(f"opened {rec['path']} in $EDITOR")
        except Exception as e:
            self._notify(f"could not launch $EDITOR: {e}", error=True)

    def action_preview_agent(self) -> None:
        name = self._current_agent_name()
        if not name:
            self._notify("(highlight an agent to preview)")
            return
        body = ag.load_agent_body(name) or ""
        try:
            from rich.panel import Panel
            from rich.markdown import Markdown
            log = self.app.query_one("#transcript")
            log.write(Panel(
                Markdown(body or "*(empty body)*"),
                title=f"🎛  Agent preview: {name}",
                border_style="cyan",
            ))
            self._notify(f"previewed '{name}' in transcript")
        except Exception as e:
            self._notify(f"preview failed: {e}", error=True)

    # ── helpers ────────────────────────────────────────────────────────────

    def _notify(self, msg: str, error: bool = False) -> None:
        try:
            color = "#f85149" if error else "#8b949e"
            self.query_one("#modal_status", Static).update(f"[{color}]{msg}[/]")
        except Exception:
            pass


def _format_agent_row(agent: dict, active_name: str) -> Text:
    is_active = agent["name"] == active_name
    icon = (agent.get("icon") or "").strip()
    color = (agent.get("color") or "").strip() or "#79c0ff"
    marker = "● " if is_active else "  "
    icon_part = f"{icon}  " if icon else "    "
    name_style = f"bold {color}" if is_active else color
    desc = agent.get("description", "")
    return Text.assemble(
        (marker, "bold #3fb950"),
        (icon_part, color),
        (f"{agent['name']:<{ROW_NAME_WIDTH}s}", name_style),
        ("  ", ""),
        (_ellipsis(desc), "#8b949e"),
    )
