"""Shared backdrop, frame, and OptionList styling for centered TUI modals.

The actual CSS now lives in :mod:`jarvis.tui.theme` so every widget,
modal, and chat panel pulls colors from a single source. This module
re-exports the modal CSS plus a few row-formatting helpers.

The chrome uses ``$jv-*`` theme variables, so it is one constant string for
every palette; :class:`TuiModalScreen` applies it to every dialog.
"""
from __future__ import annotations

from typing import TypeVar

from textual.screen import ModalScreen

from . import theme as _theme

TDismiss = TypeVar("TDismiss")


# Standard column widths shared by every list-style modal.
ROW_NAME_WIDTH = 22
ROW_DESC_MAX_WIDTH = 55


def _ellipsis(s: str, max_len: int = ROW_DESC_MAX_WIDTH) -> str:
    """Truncate ``s`` to ``max_len`` characters, appending ``…`` if cut."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


# Backwards-compatible alias — every modal imports this name and prefixes it
# to its DEFAULT_CSS; TuiModalScreen strips that prefix again.
TUI_MODAL_CHROME_CSS: str = _theme.MODAL_CSS


def get_modal_chrome_css() -> str:
    """Return the modal chrome CSS string (same for every theme)."""
    return _theme.MODAL_CSS


def _render_theme_placeholders(css: str) -> str:
    """Replace ``{ui.TOKEN}`` placeholders in modal-specific CSS suffixes."""
    for name in (
        "BG_0", "BG_1", "BG_2", "BG_3", "BG_4",
        "BORDER", "BORDER_FC", "FG", "FG_MUTE", "FG_DIM", "SEP",
        "OK", "WARN", "ERR", "ACCENT", "ACCENT_2", "ACCENT_3",
    ):
        css = css.replace(f"{{ui.{name}}}", getattr(_theme, name))
    return css


def reload_chrome_css() -> None:
    """Back-compat no-op in practice: the chrome no longer varies by theme."""
    global TUI_MODAL_CHROME_CSS
    TUI_MODAL_CHROME_CSS = _theme.MODAL_CSS


class TuiModalScreen(ModalScreen[TDismiss]):
    """Adds ``tui-modal-screen`` and owns the shared dialog chrome.

    The chrome is this class's own ``DEFAULT_CSS`` and is *unscoped*:
    Textual scopes a class's default CSS by prefixing its type name as an
    ancestor, so ``.tui-modal-screen #modal`` in a subclass would become
    ``XScreen .tui-modal-screen #modal`` and never match the screen itself.
    Subclasses still prefix ``TUI_MODAL_CHROME_CSS`` (legacy); it is stripped
    here, leaving only their scoped, dialog-specific rules.
    """

    DEFAULT_CSS = _theme.MODAL_CSS
    SCOPED_CSS = False
    __modal_css_suffix__: str | None = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        default_css = cls.__dict__.get("DEFAULT_CSS", "")
        if isinstance(default_css, str) and default_css.startswith(TUI_MODAL_CHROME_CSS):
            default_css = default_css[len(TUI_MODAL_CHROME_CSS):]
            cls.DEFAULT_CSS = default_css
        cls.__modal_css_suffix__ = default_css if isinstance(default_css, str) else None

    def __init__(self, *args: object, **kwargs: object) -> None:
        # Dialog-specific rules may carry ``{ui.TOKEN}`` placeholders; fill
        # them from the active palette each time a dialog opens.
        suffix = type(self).__dict__.get("__modal_css_suffix__")
        if suffix and "{ui." in suffix:
            type(self).DEFAULT_CSS = _render_theme_placeholders(suffix)
        super().__init__(*args, **kwargs)
        self.add_class("tui-modal-screen")

    def on_mount(self) -> None:
        """Shared polish for every dialog: ``esc`` hint on the title row and
        a quick fade + rise on open. (Textual runs this in addition to each
        subclass's own ``on_mount``.)"""
        try:
            from rich.table import Table
            from rich.text import Text
            from textual.widgets import Static

            title = self.query_one("#modal_title", Static)
            content = title.content
            left = Text.from_markup(content) if isinstance(content, str) else content
            grid = Table.grid(expand=True)
            grid.add_column(ratio=1)
            grid.add_column(justify="right")
            grid.add_row(left, Text("esc", style=_theme.FG_DIM))
            title.update(grid)
        except Exception:
            pass
        try:
            # Rise into place. (No opacity fade: Textual's opacity blending
            # leaves children composited against the backdrop.)
            from textual.css.scalar import ScalarOffset
            from textual.geometry import Offset

            frame = self.query_one("#modal")
            frame.styles.offset = (0, 2)
            # The target must be a ScalarOffset — a plain tuple can't be
            # interpolated and would leave the dialog stuck 2 rows low.
            frame.styles.animate(
                "offset", ScalarOffset.from_offset(Offset(0, 0)),
                duration=0.18, easing="out_cubic",
                on_complete=lambda: frame.styles.clear_rule("offset"),
            )
        except Exception:
            try:
                self.query_one("#modal").styles.clear_rule("offset")
            except Exception:
                pass
        try:
            from textual.widgets import Input

            # Single-row, borderless search/entry fields in every dialog.
            for inp in self.query(Input):
                inp.compact = True
        except Exception:
            pass


# ── Shared row formatters ─────────────────────────────────────────────────
#
# Every list dialog uses the same visual grammar (opencode-style):
#   * names in the normal foreground, details dim, meta right-aligned;
#   * the current/active item marked with an accent ``●``;
#   * groups introduced by small-caps section headers (disabled rows, so the
#     cursor skips them);
#   * one muted hint line: bold keys, dim labels.


def modal_key(text: str) -> str:
    """Rich markup for a shortcut key inside a hint line."""
    return f"[bold {_theme.FG_MUTE}]{text}[/]"


def hint_line(*pairs: tuple[str, str]) -> str:
    """``hint_line(("↑↓", "navigate"), ("↵", "select"))`` → markup."""
    return "   ".join(f"{modal_key(k)} [{_theme.FG_DIM}]{label}[/]" for k, label in pairs)


def active_marker(active: bool) -> tuple[str, str]:
    """Return (text, style) for active row markers."""
    if active:
        return ("● ", f"bold {_theme.ACCENT}")
    return ("  ", "")


def primary_style(active: bool = False) -> str:
    """Row name style — neutral, bold when it's the current item."""
    return f"bold {_theme.FG}" if active else _theme.FG


def secondary_style(active: bool = False) -> str:
    """Secondary column style (provider, scope, …)."""
    return _theme.FG_MUTE if active else _theme.FG_DIM


def marker_for(active: bool) -> tuple[str, str]:
    """Return (text, style) for the active-row indicator.

    ``●`` for active vs. two-space placeholder so every row keeps the same
    two-column gutter regardless of state.
    """
    return active_marker(active)


def highlight_match(text: str, query: str, style: str, match_style: str | None = None):
    """``Text`` of ``text`` with case-insensitive ``query`` matches accented."""
    from rich.text import Text

    out = Text(text, style=style)
    q = (query or "").strip()
    if q:
        out.highlight_words([q], style=match_style or f"bold {_theme.ACCENT}", case_sensitive=False)
    return out


def picker_row(
    title: str,
    *,
    detail: str = "",
    right: str = "",
    active: bool = False,
    query: str = "",
    title_style: str | None = None,
    detail_style: str | None = None,
    right_style: str | None = None,
    icon: str = "",
    icon_style: str | None = None,
    title_width: int = 0,
):
    """One list row: ``● title  detail ·········· right``.

    ``right`` is right-aligned; title/detail truncate with an ellipsis so a
    row never wraps. Matches of ``query`` are accented.
    """
    from rich.table import Table
    from rich.text import Text

    marker, marker_style = active_marker(active)
    left = Text(no_wrap=True, overflow="ellipsis")
    if icon:
        left.append(f"{icon} ", style=icon_style or _theme.FG_DIM)
    left.append_text(highlight_match(title, query, title_style or primary_style(active)))
    if title_width and len(title) < title_width:
        left.append(" " * (title_width - len(title)))
    if detail:
        left.append("  ")
        left.append_text(highlight_match(detail, query, detail_style or _theme.FG_DIM))
    grid = Table.grid(expand=True, padding=0)
    grid.add_column(width=2, no_wrap=True)
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    grid.add_column(no_wrap=True, justify="right")
    right_text = Text(f"  {right}" if right else "", style=right_style or _theme.FG_DIM,
                      no_wrap=True)
    grid.add_row(Text(marker, style=marker_style), left, right_text)
    return grid


def section_header(label: str, note: str = "", *, first: bool = False, note_style: str | None = None):
    """A non-selectable group header row for an ``OptionList``."""
    from rich.text import Text
    from textual.widgets.option_list import Option

    t = Text(no_wrap=True, overflow="ellipsis")
    if not first:
        t.append("\n")
    t.append(label.upper(), style=f"bold {_theme.ACCENT}")
    if note:
        t.append(f"  {note}", style=note_style or _theme.FG_DIM)
    return Option(t, disabled=True)


def empty_row(message: str, oid: str = "__none__"):
    """A dim, non-selectable placeholder row (empty list / no matches)."""
    from rich.text import Text
    from textual.widgets.option_list import Option

    return Option(Text(f"  {message}", style=f"italic {_theme.FG_DIM}"), id=oid, disabled=True)


def relative_time(ts: float | None) -> str:
    """``just now`` · ``12m ago`` · ``3h ago`` · ``yesterday`` · ``Mar 4``."""
    import time as _time
    from datetime import datetime

    if not ts:
        return ""
    try:
        delta = max(0.0, _time.time() - float(ts))
    except (TypeError, ValueError):
        return ""
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    then = datetime.fromtimestamp(float(ts))
    now = datetime.now()
    if then.date() == now.date():
        return f"{int(delta // 3600)}h ago"
    days = (now.date() - then.date()).days
    if days == 1:
        return "yesterday"
    if days < 7:
        return then.strftime("%A").lower()
    if then.year == now.year:
        return then.strftime("%b %-d")
    return then.strftime("%b %-d %Y")


def day_bucket(ts: float | None) -> str:
    """Group label for a timestamp: Today · Yesterday · This week · Older."""
    from datetime import datetime

    if not ts:
        return "Older"
    try:
        then = datetime.fromtimestamp(float(ts)).date()
    except (TypeError, ValueError, OSError):
        return "Older"
    days = (datetime.now().date() - then).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return "This week"
    if days < 31:
        return "This month"
    return "Older"
