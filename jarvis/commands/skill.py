"""/skill slash command — list, load, and refresh project-base skills.

Syntax:
    /skill                  → list all available skill headers
    /skill list             → same as above
    /skill <name>           → load and show full skill content
    /skill refresh          → force re-scan for new/changed skills
    /skill load <name>      → show full skill content for <name>
    /skill global on|off    → toggle global skill visibility (persisted)
    /skill add <link>       → install from GitHub / SKILL.md / archive / folder
                              [--project|--global] [--skill name] [--all] [--overwrite]
    /skill inspect <link>   → list what a link contains
    /skill remove <name> · move <name> project|global · update <name>
"""
import re

from ..console import console, Panel, Markdown
from ..storage import skills as sk
from .. import state
from ..utils.origins import tool_tag


def _render_skills(rows, title):
    if not rows:
        scope = "global" if state.global_skills else "project"
        console.print(Panel(
            f"(no {scope} skills found — create .skills/<name>/SKILL.md)",
            title="☰ Skills",
            border_style="cyan",
        ))
        return
    lines = []
    for r in rows:
        where = tool_tag(r.get("tool") or "jarvis", r.get("also") or ())
        tag = f" [dim]\\[{'global · ' if r.get('scope') == 'global' else ''}{where}][/]"
        lines.append(f"[bold cyan]{r['name']}[/]{tag}  [dim]{r['description']}[/]")
        lines.append(f"  [dim]▣ {r['source_dir']}[/]")
        lines.append("")
    scope_label = "◎ global" if state.global_skills else "▣ project"
    console.print(Panel(
        "\n".join(lines),
        title=f"☰ Skills ({len(rows)}, {scope_label})",
        border_style="cyan",
    ))
    if not state.global_skills:
        gcount = sk.global_count()
        if gcount > 0:
            console.print(f"  [dim]({gcount} global skills hidden — /skills global on to show)[/]")


def _render_skill_content(name: str, content: str):
    console.print(Panel(
        Markdown(content),
        title=f"☰ Skill: {name}",
        border_style="cyan",
    ))


def handle_skill(cmd: str, arg: str):
    """Route /skill and /skills commands.

    Returns (handled, should_send) tuple matching dispatch convention.
    """
    if cmd not in ("/skill", "/skills"):
        return False, None

    parts = arg.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if sub in ("add", "install", "remove", "rm", "uninstall", "move", "update", "inspect", "preview"):
        _handle_manage(sub, rest)
        return True, None

    if sub == "" or sub == "list":
        # List all skills
        skills = sk.list_skills()
        _render_skills(skills, "available")
        return True, None

    elif sub == "refresh":
        # Force re-scan
        count = len(sk.discover_skills(force=True))
        scope = "global" if state.global_skills else "project"
        console.print(f"[green]✓[/] Re-scanned — [cyan]{count}[/] {scope} skills available")
        return True, None

    elif sub == "global" and rest in ("on", "off"):
        # Toggle global skills visibility — persisted + auto-reloaded
        old = state.global_skills
        state.global_skills = (rest == "on")
        if old != state.global_skills:
            # Persist to disk so it survives restarts
            state.save_skills_config()
        # Force re-discover with new scope — auto-reloads the list
        skills = sk.discover_skills(force=True)
        scope = "global" if state.global_skills else "project"
        count = len(skills)
        label = "◎ global (project + global)" if state.global_skills else "▣ project-only"
        console.print(f"[green]✓[/] Skills scope set to [bold]{label}[/] — [cyan]{count}[/] skills available")
        # Auto-show the updated list
        _render_skills(skills, "auto-reloaded")
        return True, None

    elif sub == "global":
        # Show current global status
        label = "◎ on" if state.global_skills else "▣ off"
        console.print(f"[cyan]Global skills:[/] {label}  (/skills global on|off to toggle)")
        return True, None

    elif sub == "load" and rest:
        # Load and show full skill content
        name = rest.strip().lower()
        content = sk.load_skill(name)
        if content:
            _render_skill_content(name, content)
        else:
            available = [s["name"] for s in sk.list_skills()]
            if available:
                console.print(f"[red]Skill '{name}' not found.[/] Available: {', '.join(available)}")
            else:
                console.print(f"[red]Skill '{name}' not found. No skills available.[/]")
        return True, None

    elif sub:
        # `sub` might be a skill name directly
        content = sk.load_skill(sub)
        if content:
            _render_skill_content(sub, content)
        else:
            # Check if it's a partial match
            all_skills = sk.list_skills()
            matches = [s for s in all_skills if sub in s["name"]]
            if matches:
                console.print(f"[yellow]'{sub}' didn't match any skill exactly.[/] Did you mean:")
                for m in matches:
                    tag = f" [dim][global][/]" if m.get("scope") == "global" else ""
                    console.print(f"  [cyan]{m['name']}[/]{tag} — {m['description']}")
                console.print("\n[d]Use /skill <name> to load, /skill list to see all[/]")
            else:
                console.print(f"[red]Unknown skill: '{sub}'[/]  (/skill list)")
        return True, None

    return True, None


# ── add / remove / move / update ─────────────────────────────────────────────

def _split_flags(rest: str) -> tuple[str, dict]:
    """Pull ``--project|--global|--skill x|--all|--overwrite`` out of the text."""
    opts: dict = {"scope": None, "skill": None, "all": False, "overwrite": False}
    text = " " + rest.strip() + " "
    m = re.search(r"\s--skill[= ]\s*([\w.-]+)", text)
    if m:
        opts["skill"] = m.group(1)
        text = text.replace(m.group(0), " ", 1)
    for flag, key, val in (("--project", "scope", "project"), ("-p", "scope", "project"),
                           ("--global", "scope", "global"), ("-g", "scope", "global"),
                           ("--all", "all", True), ("--overwrite", "overwrite", True)):
        pat = re.compile(rf"\s{re.escape(flag)}(?=\s)")
        if pat.search(text):
            opts[key] = val
            text = pat.sub(" ", text)
    return text.strip(), opts


def _handle_manage(sub: str, rest: str) -> None:
    from ..storage import skill_install as si

    text, opts = _split_flags(rest)
    if sub in ("inspect", "preview"):
        res = si.inspect_source(text)
        if not res.get("ok"):
            console.print(f"[red]✗[/] {res.get('error')}")
            return
        console.print(f"[bold cyan]{res['label']}[/] — {len(res['skills'])} skill(s):")
        for s in res["skills"]:
            mark = f" [dim](installed: {s['installed']})[/]" if s.get("installed") else ""
            console.print(f"  [cyan]{s['name']}[/]{mark}  [dim]{s['description'][:100]}[/]")
        return
    if sub in ("add", "install"):
        if not text:
            console.print("[red]Usage:[/] [cyan]/skill add <github link | owner/repo | SKILL.md link | folder>[/] "
                          "[dim][--project|--global] [--skill name] [--all][/]")
            return
        res = si.install_skills(text, scope=opts["scope"] or "global",
                                names=[opts["skill"]] if opts["skill"] else None,
                                install_all=opts["all"], overwrite=opts["overwrite"])
        if res.get("needs_choice"):
            console.print(f"[yellow]{res['label']} has {len(res['skills'])} skills.[/] Pick one with [cyan]--skill <name>[/] or take all with [cyan]--all[/]:")
            for s in res["skills"]:
                console.print(f"  [cyan]{s['name']}[/]  [dim]{s['description'][:100]}[/]")
            return
        if not res.get("ok"):
            console.print(f"[red]✗[/] {res.get('error')}")
            return
        for i in res["installed"]:
            console.print(f"[green]✓[/] installed [bold]{i['name']}[/] [dim]({i['scope']}) {i['path']}[/]")
        for s in res.get("skipped", []):
            console.print(f"[yellow]–[/] skipped {s['name']}: {s['reason']}")
        if res.get("scope_note"):
            console.print(f"[yellow]{res['scope_note']}[/]")
        return
    if sub in ("remove", "rm", "uninstall"):
        res = si.remove_skill(text, scope=opts["scope"])
        console.print(f"[green]✓[/] {res['message']}" if res.get("ok") else f"[red]✗[/] {res.get('error')}")
        return
    if sub == "move":
        name, _, to = text.partition(" ")
        to = (to or ("project" if opts["scope"] == "project" else "global")).strip()
        res = si.move_skill(name, to)
        console.print(f"[green]✓[/] {res['message']}" if res.get("ok") else f"[red]✗[/] {res.get('error')}")
        return
    if sub == "update":
        res = si.update_skill(text)
        console.print(f"[green]✓[/] {res['message']}" if res.get("ok") else f"[red]✗[/] {res.get('error')}")
