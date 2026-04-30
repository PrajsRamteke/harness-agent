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
    if not compact:
        console.print(f"[bold magenta]{WELCOME_ART}[/]")
    console.print(Panel(
        "[bold]Jarvis[/] — chat, code, and control your Mac.\n"
        "[dim][cyan]/help[/] commands • [cyan]F2[/]/[cyan]/verbose[/] internals • [cyan]/exit[/] quit[/]",
        border_style="magenta", padding=(0, 2),
    ))


def _mode_flag() -> str:
    """Compact rich-markup mode indicator for header panels."""
    lbl, col, style = state.MODE_LABELS.get(
        state.active_mode, (state.active_mode, "#ffffff", "")
    )
    return f"[{style} {col}]{lbl}[/]" if style else f"[{col}]{lbl}[/]"


def header_panel(compact: bool = False):
    # Use live cwd so /cd and launch location are reflected immediately.
    import pathlib
    cwd = pathlib.Path.cwd()
    cwd_text = escape(str(cwd))
    pinned_flag = "[yellow]pinned[/]" if state.pinned_context.strip() else "[dim]no pin[/]"
    if compact:
        flags = "  ".join([
            f"[bold magenta]{state.MODEL}[/]",
            f"mode:{_mode_flag()}",
            f"think:{'[green]on[/]' if state.think_mode else '[dim]off[/]'}",
            f"tools:{'[green]verbose[/]' if state.show_internal else '[dim]quiet[/]'}",
            f"project:[dim]{cwd_text}[/]",
        ])
        console.print(f"[dim]{flags}[/]")
        return
    flags = " • ".join([
        f"[bold magenta]{state.MODEL}[/]",
        f"mode {_mode_flag()}",
        f"think {'[green]on[/]' if state.think_mode else '[dim]off[/]'}",
        f"bash {'[green]auto[/]' if state.auto_approve else '[dim]ask[/]'}",
        f"tools {'[green]verbose[/]' if state.show_internal else '[dim]quiet[/]'}",
        f"pin {pinned_flag}",
        f"msgs {len(state.messages)}",
        f"project [dim]{cwd_text}[/]",
        f"provider [dim]{state.provider}[/]",
        f"auth [dim]{state.auth_mode if state.provider == 'anthropic' else 'api_key'}[/]",
    ])
    console.print(Panel(flags, border_style="green", padding=(0, 1)))
