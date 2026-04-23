"""Welcome banner and status-header panel."""
from ..console import console, Panel
from ..constants import CWD
from .. import state

WELCOME_ART = r"""
  ██╗  ██╗ █████╗ ██████╗ ███╗   ██╗███████╗███████╗███████╗    █████╗  ██████╗ ███████╗███╗   ██╗████████╗
  ██║  ██║██╔══██╗██╔══██╗████╗  ██║██╔════╝██╔════╝██╔════╝   ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
  ███████║███████║██████╔╝██╔██╗ ██║█████╗  ███████╗███████╗   ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
  ██╔══██║██╔══██║██╔══██╗██║╚██╗██║██╔══╝  ╚════██║╚════██║   ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
  ██║  ██║██║  ██║██║  ██║██║ ╚████║███████╗███████║███████║   ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
  ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝╚══════╝   ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
"""


def welcome_banner():
    console.print(f"[bold magenta]{WELCOME_ART}[/]")
    console.print(Panel(
        "[bold]Jarvis-style macOS agent[/] — chat, code, and control your Mac.\n"
        "[dim]Type [cyan]/help[/] for commands • [cyan]/new[/] for a fresh chat • [cyan]/exit[/] to quit[/]",
        border_style="magenta", padding=(0, 2),
    ))


def header_panel():
    # Use live CWD from constants module (may have been mutated via os.chdir)
    import pathlib
    cwd = pathlib.Path.cwd()
    pinned_flag = "[yellow]pinned[/]" if state.pinned_context.strip() else "[dim]no pin[/]"
    flags = " • ".join([
        f"[bold magenta]{state.MODEL}[/]",
        f"🧠 {'[green]on[/]' if state.think_mode else '[dim]off[/]'}",
        f"⚡ {'[green]auto[/]' if state.auto_approve else '[dim]ask[/]'}",
        f"📌 {pinned_flag}",
        f"💬 {len(state.messages)}",
        f"📂 [dim]{cwd.name}[/]",
        f"🔐 [dim]{state.auth_mode}[/]",
    ])
    console.print(Panel(flags, border_style="green", padding=(0, 1)))
