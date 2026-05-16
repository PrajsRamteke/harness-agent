"""Welcome banner and status header.

In the TUI the persistent info (model / agent / session) lives in the
status bar, so this module emits only a lean welcome card into the
transcript. The legacy Rich REPL path still calls ``header_panel``.

Theme-awareness
---------------
Theme colors are resolved *live* inside each function so they pick up
runtime ``set_theme()`` calls.  We never capture ``_ui.ACCENT`` at module
level — that would freeze one theme for the lifetime of the import.
"""
from rich.markup import escape

from ..console import console, Panel
from .. import state


def _theme_colors():
    """Return current theme color tokens — works in TUI and Rich REPL."""
    try:
        from ..tui import theme as _ui
        return {
            "accent": _ui.ACCENT,
            "accent_2": _ui.ACCENT_2,
            "accent_3": _ui.ACCENT_3,
            "fg_mute": _ui.FG_MUTE,
            "fg_dim": _ui.FG_DIM,
            "ok": _ui.OK,
            "border": _ui.BORDER,
            "border_fc": _ui.BORDER_FC,
            "warn": _ui.WARN,
            "err": _ui.ERR,
            "sep": _ui.SEP,
        }
    except Exception:  # pragma: no cover — Rich REPL without TUI module
        return {
            "accent": "#79c0ff",
            "accent_2": "#c084fc",
            "accent_3": "#f0b3ff",
            "fg_mute": "#9aa4b1",
            "fg_dim": "#6b7684",
            "ok": "#56d364",
            "border": "#2a323d",
            "border_fc": "#4d8df6",
            "warn": "#e3b341",
            "err": "#f85149",
            "sep": "#1f2630",
        }


WELCOME_ART = r"""
  ██╗  ██╗ █████╗ ██████╗ ███╗   ██╗███████╗███████╗███████╗    █████╗  ██████╗ ███████╗███╗   ██╗████████╗
  ██║  ██║██╔══██╗██╔══██╗████╗  ██║██╔════╝██╔════╝██╔════╝   ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
  ███████║███████║██████╔╝██╔██╗ ██║█████╗  ███████╗███████╗   ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
  ██╔══██║██╔══██║██╔══██╗██║╚██╗██║██╔══╝  ╚════██║╚════██║   ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
  ██║  ██║██║  ██║██║  ██║██║ ╚████║███████╗███████║███████║   ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
  ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝╚══════╝╚══════╝   ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
"""


def welcome_banner(compact: bool = False):
    """Render the welcome card into the active console (transcript in TUI).

    Colors are resolved live from the current theme so the art and panel
    shift when the user switches themes mid-session.
    """
    c = _theme_colors()
    from ..constants import VERSION

    # Art uses accent_3 (the "highlight" token) — varies per theme:
    #   red       → #ffa198 (coral)
    #   blue      → #79f0ff (cyan)
    #   purple    → #f0b3ff (pink)
    #   green     → #a3f0bf (mint)
    #   orange    → #fec77d (golden)
    #   yellow    → #f0d272 (pale gold)
    #   rose      → #ffb3c6 (soft pink)
    #   slate     → #d0d7de (light gray)
    #   ocean     → #93c5fd (sky blue)
    #   cyberpunk → #f0aaff (neon pink)
    #   monochrome→ #808080 (gray)
    #   forest    → #aed581 (light green)
    #   dracula   → #8be9fd (cyan)
    #   sunset    → #f7c08a (peach)
    #   dark      → #ce9178 (warm orange)
    console.print(f"[{c['accent_3']}]{WELCOME_ART}[/]")

    title = f"[bold {c['accent']}]JARVIS v{VERSION}[/]"
    tagline = f"[{c['fg_mute']}]Chat, code, and control your Mac.[/]"
    hints = (
        f"[{c['accent_2']}]/[/] commands   "
        f"[{c['accent_2']}]/agent[/] pick agent   "
        f"[{c['accent_2']}]/model[/] switch model   "
        f"[{c['accent_2']}]/help[/] full reference   "
        f"[{c['accent_2']}]F2[/] toggle internals"
    )

    body = f"{title}    {tagline}\n{hints}"
    console.print(Panel(body, border_style=c['border'], padding=(0, 2)))

    if state.update_result:
        info = state.update_result
        count = info.get("count", 0)
        commits = info.get("commits", [])
        noun = "commit" if count == 1 else "commits"
        lines = [f"[bold {c['ok']}]✓ {count} new {noun} pulled[/]"]
        for commit in commits[:5]:
            lines.append(f"[{c['fg_dim']}]  · {escape(commit)}[/]")
        if len(commits) > 5:
            lines.append(f"[{c['fg_dim']}]  … and {len(commits) - 5} more[/]")
        lines.append(f"\n[{c['fg_dim']}]Restart Jarvis to run the updated code.[/]")
        console.print(Panel(
            "\n".join(lines),
            title="[bold]Updated[/]",
            title_align="left",
            border_style=c['accent'],
            padding=(0, 2),
        ))


def _agent_flag(c: dict | None = None) -> str:
    """Compact indicator for the active agent — used in the Rich REPL header.

    The TUI uses ``jarvis.tui.app._agent_badge_markup`` instead.
    """
    if c is None:
        c = _theme_colors()
    rec = state.active_agent
    if rec is None and state.active_agent_name:
        rec = state.resolve_active_agent()
    if not rec:
        return f"[{c['fg_dim']}]default[/]"
    icon = (rec.get("icon") or "").strip()
    color = (rec.get("color") or "").strip() or c['ok']
    label = f"{icon} {rec['name']}".strip() if icon else rec["name"]
    return f"[bold {color}]{label}[/]"


def header_panel(compact: bool = False):
    """Rich-REPL only header strip. The TUI shows status bar instead."""
    c = _theme_colors()
    import pathlib
    from ..constants import VERSION

    cwd = pathlib.Path.cwd()
    cwd_text = escape(str(cwd))
    pinned_flag = (
        f"[{c['accent']}]pinned[/]" if state.pinned_context.strip()
        else f"[{c['fg_dim']}]no pin[/]"
    )
    think_hl = f"[{c['accent']}]{state.think_effort}[/]"
    off = f"[{c['fg_dim']}]off[/]"
    if compact:
        flags = "  ".join([
            f"[{c['accent']}]{state.MODEL}[/]",
            f"agent:{_agent_flag(c)}",
            f"think:{think_hl if state.think_mode else off}",
            f"v{VERSION}",
            f"[{c['fg_dim']}]{cwd_text}[/]",
        ])
        console.print(f"[{c['fg_mute']}]{flags}[/]")
        return
    flags = " • ".join([
        f"[{c['accent']}]{state.MODEL}[/]",
        f"v{VERSION}",
        f"agent {_agent_flag(c)}",
        f"think {think_hl if state.think_mode else off}",
        f"bash {c['accent'] if state.auto_approve else c['fg_dim']}",
        f"pin {pinned_flag}",
        f"msgs {len(state.messages)}",
        f"[{c['fg_dim']}]{cwd_text}[/]",
        f"provider [{c['fg_dim']}]{state.provider}[/]",
        f"auth [{c['fg_dim']}]{state.auth_mode if state.provider == 'anthropic' else 'api_key'}[/]",
    ])
    console.print(Panel(flags, border_style=c['border'], padding=(0, 1)))
