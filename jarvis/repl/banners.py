"""Welcome banner and status-header panel."""
from rich.markup import escape

from ..console import console, Panel
from .. import state

WELCOME_ART = r"""
  ██╗  ██╗ █████╗ ██████╗ ███╗   ██╗███████╗███████╗███████╗    █████╗  ██████╗ ███████╗███╗   ██╗████████╗
  ██║  ██║██╔══██╗██╔══██╗████╗  ██║██╔════╝██╔════╝██╔════╝   ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
  ███████║███████║██████╔╝██╔██╗ ██║█████╗  ███████╗███████╗   ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
  ██╔══██║██╔══██║██╔══██╗██║╚██╗██║██╔══╝  ╚════██║╚════██║   ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
  ██║  ██║██║  ██║██║  ██║██║ ╚████║███████╗███████║███████║   ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
  ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝╚══════╝   ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
"""


def welcome_banner(compact: bool = False):
    from ..constants import VERSION

    asst = state.theme_colors["asst_border"]
    link = state.theme_colors["project_border"]
    if not compact:
        console.print(f"[{asst}]{WELCOME_ART}[/]")
    console.print(Panel(
        f"[{asst}]Jarvis[/] [dim]v{VERSION}[/] — chat, code, and control your Mac.\n"
        f"[dim][{link}]/help[/] commands · [{link}]F2[/]/[{link}]/verbose[/] internals · [{link}]/exit[/] quit[/]",
        border_style=asst, padding=(0, 2),
    ))

    if state.update_result:
        info = state.update_result
        count = info.get("count", 0)
        commits = info.get("commits", [])
        noun = "commit" if count == 1 else "commits"
        lines = [f"[bold {link}]✓ {count} new {noun} pulled[/]"]
        for c in commits[:5]:
            lines.append(f"[dim]  · {escape(c)}[/]")
        if len(commits) > 5:
            lines.append(f"[dim]  … and {len(commits) - 5} more[/]")
        lines.append(f"\n[dim]Restart Jarvis to run the updated code.[/]")
        console.print(Panel(
            "\n".join(lines),
            title="[bold]Updated[/]",
            title_align="left",
            border_style=link,
            padding=(0, 2),
        ))


def _agent_flag() -> str:
    """Compact rich-markup indicator for the active agent (header panels).

    Falls back to a dim "default" badge when no agent is active so the
    header always tells the user which prompt is in effect.
    """
    rec = state.active_agent
    if rec is None and state.active_agent_name:
        rec = state.resolve_active_agent()
    if not rec:
        return "[dim]default[/]"
    icon = (rec.get("icon") or "").strip()
    color = (rec.get("color") or "").strip() or "#3fb950"
    label = f"{icon} {rec['name']}".strip() if icon else rec["name"]
    return f"[bold {color}]{label}[/]"


def header_panel(compact: bool = False):
    # Use live cwd so /cd and launch location are reflected immediately.
    import pathlib
    from ..constants import VERSION

    cwd = pathlib.Path.cwd()
    cwd_text = escape(str(cwd))
    pinned_flag = "[#d29922]pinned[/]" if state.pinned_context.strip() else "[dim]no pin[/]"
    asst = state.theme_colors["asst_border"]
    hl = state.theme_colors["project_border"]
    think_hl = f"[{hl}]{state.think_effort}[/]"
    verbose = f"[{hl}]verbose[/]"
    auto_hl = f"[{hl}]auto[/]"
    off = "[dim]off[/]"
    quiet = "[dim]quiet[/]"
    ask = "[dim]ask[/]"
    if compact:
        flags = "  ".join([
            f"[{asst}]{state.MODEL}[/]",
            f"agent:{_agent_flag()}",
            f"think:{think_hl if state.think_mode else off}",
            f"tools:{verbose if state.show_internal else quiet}",
            f"v{VERSION}",
            f"[dim]{cwd_text}[/]",
        ])
        console.print(f"[dim]{flags}[/]")
        return
    flags = " • ".join([
        f"[{asst}]{state.MODEL}[/]",
        f"v{VERSION}",
        f"agent {_agent_flag()}",
        f"think {think_hl if state.think_mode else off}",
        f"bash {auto_hl if state.auto_approve else ask}",
        f"tools {verbose if state.show_internal else quiet}",
        f"pin {pinned_flag}",
        f"msgs {len(state.messages)}",
        f"[dim]{cwd_text}[/]",
        f"provider [dim]{state.provider}[/]",
        f"auth [dim]{state.auth_mode if state.provider == 'anthropic' else 'api_key'}[/]",
    ])
    console.print(Panel(flags, border_style=hl, padding=(0, 1)))
