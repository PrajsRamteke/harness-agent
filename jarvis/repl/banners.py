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
    asst = state.theme_colors["asst_border"]
    link = state.theme_colors["project_border"]
    if not compact:
        console.print(f"[{asst}]{WELCOME_ART}[/]")
    console.print(Panel(
        f"[{asst}]Jarvis[/] — chat, code, and control your Mac.\n"
        f"[dim][{link}]/help[/] commands · [{link}]F2[/]/[{link}]/verbose[/] internals · [{link}]/exit[/] quit[/]",
        border_style=asst, padding=(0, 2),
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
    pinned_flag = "[#d29922]pinned[/]" if state.pinned_context.strip() else "[dim]no pin[/]"
    asst = state.theme_colors["asst_border"]
    hl = state.theme_colors["project_border"]
    on_hl = f"[{hl}]on[/]"
    verbose = f"[{hl}]verbose[/]"
    auto_hl = f"[{hl}]auto[/]"
    off = "[dim]off[/]"
    quiet = "[dim]quiet[/]"
    ask = "[dim]ask[/]"
    if compact:
        flags = "  ".join([
            f"[{asst}]{state.MODEL}[/]",
            f"mode:{_mode_flag()}",
            f"think:{on_hl if state.think_mode else off}",
            f"tools:{verbose if state.show_internal else quiet}",
            f"[dim]{cwd_text}[/]",
        ])
        console.print(f"[dim]{flags}[/]")
        return
    flags = " • ".join([
        f"[{asst}]{state.MODEL}[/]",
        f"mode {_mode_flag()}",
        f"think {on_hl if state.think_mode else off}",
        f"bash {auto_hl if state.auto_approve else ask}",
        f"tools {verbose if state.show_internal else quiet}",
        f"pin {pinned_flag}",
        f"msgs {len(state.messages)}",
        f"[dim]{cwd_text}[/]",
        f"provider [dim]{state.provider}[/]",
        f"auth [dim]{state.auth_mode if state.provider == 'anthropic' else 'api_key'}[/]",
    ])
    console.print(Panel(flags, border_style=hl, padding=(0, 1)))
