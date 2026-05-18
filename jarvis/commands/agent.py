"""/agent slash command — list, activate, deactivate, create agents.

Syntax:
    /agent                       open picker (TUI) / list (REPL)
    /agent list                  list all available agents
    /agent <name>                activate by name
    /agent off | none            deactivate (clean base system prompt)
    /agent new <name>            scaffold .harness/agents/<name>.md
    /agent edit <name>           open the agent file in $EDITOR
    /agent show <name>           render the agent's body in the transcript
    /agent refresh               bust the cache and re-scan
    /agent global on|off         toggle global agent discovery (persisted)
    /agent scope project|global  default scope used by /agent new (session-only)
    /agent init                  scaffold a .harness/ tree in the current project
"""
from __future__ import annotations

import os
import pathlib
import subprocess

from ..console import console, Panel, Markdown
from ..constants import (
    PROJECT_HARNESS_DIRNAME,
    PROJECT_AGENTS_DIRNAME,
    PROJECT_SKILLS_DIRNAME,
)
from ..storage import agents as ag
from .. import state


# Session-level: which scope does `/agent new` write to. Not persisted —
# project is almost always the right answer; global is opt-in per call.
_NEW_SCOPE: str = "project"


def _render_agents(rows: list[dict]) -> None:
    if not rows:
        scope = "global" if state.global_agents else "project"
        console.print(Panel(
            f"(no {scope} agents found — create .harness/agents/<name>.md or run /agent new <name>)",
            title="⚙  Agents",
            border_style="cyan",
        ))
        return
    active = state.active_agent_name
    lines = []
    for r in rows:
        marker = " [bold green]●[/]" if r["name"] == active else "  "
        tag = " [dim][global][/]" if r.get("scope") == "global" else ""
        icon = (r.get("icon") or "").strip()
        icon_disp = f"{icon} " if icon else ""
        lines.append(f"{marker} [bold cyan]{icon_disp}{r['name']}[/]{tag}  [dim]{r['description']}[/]")
        lines.append(f"     [dim]▣ {r.get('source_tag') or r.get('source_dir', '')}[/]")
        lines.append("")
    scope_label = "◎ global" if state.global_agents else "▣ project"
    console.print(Panel(
        "\n".join(lines).rstrip(),
        title=f"⚙  Agents ({len(rows)}, {scope_label})",
        border_style="cyan",
    ))
    if not state.global_agents:
        gc = ag.global_count()
        if gc:
            console.print(f"  [dim]({gc} global agents hidden — /agent global on to show)[/]")


def _activate(name: str) -> bool:
    rec = ag.find_agent(name)
    if not rec:
        all_a = ag.list_agents()
        if all_a:
            avail = ", ".join(a["name"] for a in all_a[:12])
            console.print(f"[red]Agent '{name}' not found.[/] Available: {avail}")
        else:
            console.print(f"[red]Agent '{name}' not found.[/] No agents available.")
        return False
    state.set_active_agent(rec)
    icon = (rec.get("icon") or "").strip()
    icon_disp = f"{icon} " if icon else ""
    color = (rec.get("color") or "#3fb950").strip() or "#3fb950"
    console.print(
        f"[bold {color}]✓ agent → {icon_disp}{rec['name']}[/]  "
        f"[dim]({rec.get('description', '')})[/]"
    )
    return True


def _deactivate() -> None:
    state.set_active_agent(None)
    console.print("[dim]◉ agent → default (base system prompt only).[/]")


def handle_agent(cmd: str, arg: str):
    """Route /agent commands. Returns (handled, should_send) like skill handler."""
    if cmd not in ("/agent", "/agents"):
        return False, None

    parts = arg.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    # /agent              → list (TUI intercepts bare form to open modal)
    if sub == "" or sub == "list":
        _render_agents(ag.list_agents())
        return True, None

    if sub == "refresh":
        ag.invalidate_cache()
        rows = ag.discover_agents(force=True)
        # If the active agent disappeared from disk, drop it.
        if state.active_agent_name and not ag.find_agent(state.active_agent_name):
            console.print(
                f"[yellow]active agent '{state.active_agent_name}' no longer found — deactivating[/]"
            )
            state.set_active_agent(None)
        else:
            state.resolve_active_agent()
        scope = "global" if state.global_agents else "project"
        console.print(
            f"[green]✓[/] Re-scanned — [cyan]{len(rows)}[/] {scope} agents available"
        )
        return True, None

    if sub in ("off", "none", "clear"):
        _deactivate()
        return True, None

    if sub == "global":
        if rest.lower() in ("on", "off"):
            old = state.global_agents
            state.global_agents = (rest.lower() == "on")
            if old != state.global_agents:
                state.save_agent_config()
            ag.invalidate_cache()
            rows = ag.discover_agents(force=True)
            label = "◎ global (project + global)" if state.global_agents else "▣ project-only"
            console.print(
                f"[green]✓[/] Agent scope set to [bold]{label}[/] — "
                f"[cyan]{len(rows)}[/] agents visible"
            )
            _render_agents(rows)
            return True, None
        label = "◎ on" if state.global_agents else "▣ off"
        console.print(f"[cyan]Global agents:[/] {label}  (/agent global on|off)")
        return True, None

    if sub == "scope":
        global _NEW_SCOPE
        if rest.lower() in ("project", "global"):
            _NEW_SCOPE = rest.lower()
            console.print(f"[green]✓[/] /agent new will now write to [bold]{_NEW_SCOPE}[/] scope")
        else:
            console.print(f"[cyan]/agent new[/] scope: [bold]{_NEW_SCOPE}[/]  (/agent scope project|global)")
        return True, None

    if sub == "new":
        if not rest:
            console.print("[red]usage:[/] /agent new <name> [description]")
            return True, None
        bits = rest.split(maxsplit=1)
        name = bits[0]
        desc = bits[1] if len(bits) > 1 else ""
        ok, msg = ag.scaffold_agent(name, scope=_NEW_SCOPE, description=desc)
        if ok:
            console.print(f"[green]✓[/] Created [cyan]{msg}[/]  [dim](/agent {name} to activate)[/]")
            editor = os.environ.get("EDITOR")
            if editor:
                console.print(f"  [dim]edit with: $EDITOR ({editor}) {msg}[/]")
        else:
            console.print(f"[red]✗ {msg}[/]")
        return True, None

    if sub == "edit":
        if not rest:
            console.print("[red]usage:[/] /agent edit <name>")
            return True, None
        rec = ag.find_agent(rest)
        if not rec:
            console.print(f"[red]agent '{rest}' not found[/]")
            return True, None
        path = rec["path"]
        editor = os.environ.get("EDITOR")
        if editor:
            try:
                subprocess.Popen([editor, path])
                console.print(f"[green]✓[/] opened in $EDITOR: [cyan]{path}[/]")
            except Exception as e:
                console.print(f"[yellow]could not launch $EDITOR ({editor}): {e}[/]")
                console.print(f"  [dim]path: {path}[/]")
        else:
            console.print(f"[cyan]{path}[/]  [dim](\\$EDITOR not set)[/]")
        return True, None

    if sub == "init":
        ok, msg = _scaffold_project_tree(force=(rest.lower() in ("force", "--force")))
        if ok:
            console.print(f"[green]✓[/] {msg}")
        else:
            console.print(f"[yellow]{msg}[/]")
        return True, None

    if sub == "show":
        if not rest:
            console.print("[red]usage:[/] /agent show <name>")
            return True, None
        rec = ag.find_agent(rest)
        if not rec:
            console.print(f"[red]agent '{rest}' not found[/]")
            return True, None
        body = ag.load_agent_body(rec["name"]) or ""
        title = f"⚙  Agent: {rec['name']}  [dim]({rec.get('scope')})[/]"
        console.print(Panel(Markdown(body or "*(empty body)*"), title=title, border_style="cyan"))
        return True, None

    # bare name → activate
    _activate(sub)
    return True, None


# Re-exported helper for the TUI to learn the current /agent new scope.
def get_new_scope() -> str:
    return _NEW_SCOPE


def set_new_scope(scope: str) -> None:
    global _NEW_SCOPE
    if scope in ("project", "global"):
        _NEW_SCOPE = scope


# ── /agent init scaffolder ───────────────────────────────────────────────


_GITKEEP = ""
_README_BODY = (
    "# .harness/\n\n"
    "Per-project Harness configuration:\n\n"
    "- `agents/<name>.md` — project-local agents (frontmatter + body markdown).\n"
    "- `skills/<name>/SKILL.md` — instruction packs the LLM auto-invokes when\n"
    "  their `description:` matches the task.\n"
    "- `settings.json` — overrides for this project (merged over the global\n"
    "  `~/.config/harness-agent/settings.json`).\n\n"
    "See `/agent` and `/skill` for activation and `~/.harness/` for the\n"
    "user-global counterpart.\n"
)


def _scaffold_project_tree(force: bool = False) -> tuple[bool, str]:
    """Create a `.harness/` tree in the current project root.

    Creates ``.harness/agents/``, ``.harness/skills/``, and a README. Never
    overwrites existing files unless ``force=True``.
    """
    try:
        root = pathlib.Path.cwd().resolve()
        # Walk up to git root if possible — matches discover_agents().
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=root, capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0:
                root = pathlib.Path(r.stdout.strip()).resolve()
        except Exception:
            pass

        harness = root / PROJECT_HARNESS_DIRNAME
        agents_dir = root / PROJECT_AGENTS_DIRNAME
        skills_dir = root / PROJECT_SKILLS_DIRNAME

        created: list[str] = []
        for d in (harness, agents_dir, skills_dir):
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                created.append(str(d.relative_to(root)))

        readme = harness / "README.md"
        if not readme.exists() or force:
            readme.write_text(_README_BODY, encoding="utf-8")
            created.append(str(readme.relative_to(root)))

        # .gitkeep so empty agents/skills survive git
        for d in (agents_dir, skills_dir):
            keep = d / ".gitkeep"
            if not keep.exists() and not any(d.iterdir()):
                keep.write_text(_GITKEEP, encoding="utf-8")

        if not created:
            return True, f".harness/ already initialized at {harness}"
        ag.invalidate_cache()
        bits = ", ".join(created)
        return True, f"initialized .harness/ at {harness}  (created: {bits})"
    except Exception as e:
        return False, f"scaffold failed: {e}"
