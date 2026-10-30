"""Chat tabs strip at the top of the TUI — like a browser's.

``◌ fix login bug ×  ⠹ refactor auth ×  ● new chat ×  +``

One row, one cell per tab: a status mark (spinner while its turn runs,
``●`` a reply finished while you looked elsewhere, ``◆`` it asks you
something), the session's title and a ``×``. Click a tab to show it, ``×``
(or a middle click) to close it, ``+`` for a new chat. The data comes from
``jarvis.tabs``; clicks go to ``app.tab_action``.
"""
from __future__ import annotations

from rich.text import Text
from textual.widget import Widget

from .. import tabs
from . import theme as ui

TITLE_MAX = 26
TITLE_MIN = 5


def _clip(text: str, width: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= width:
        return text
    return text[: max(1, width - 1)].rstrip() + "…"


class TabBar(Widget):
    DEFAULT_CSS = """
    TabBar {
        height: 1;
        width: 100%;
        padding: 0 1;
    }
    """

    can_focus = False

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        # (start, end, kind, tab id) — kind: "tab" | "close" | "new"
        self._spans: list[tuple[int, int, str, int]] = []
        self._hover: tuple[str, int] | None = None

    # ── layout ─────────────────────────────────────────────────────────
    def _status(self, tab: tabs.Tab) -> tuple[str, str]:
        if tab.busy:
            frame = getattr(self.app, "_frame", 0)
            return ui.SPINNER_FRAMES[(frame // 2) % len(ui.SPINNER_FRAMES)], ui.ACCENT
        if tab.needs_input:
            return "◆", ui.WARN
        if tab.unread:
            return "●", ui.ACCENT
        return "", ""

    def _title_width(self, rows: list[tabs.Tab], width: int) -> int:
        """Widest title that fits every tab (and the ``+``) in ``width`` cells."""
        fixed = 0
        for tab in rows:
            mark, _ = self._status(tab)
            fixed += 1 + (2 if mark else 0) + 3 + 1  # pad · mark · " × " · gap
        fixed += 4  # " + " and a cell to spare
        room = max(0, width - fixed)
        return max(TITLE_MIN, min(TITLE_MAX, room // max(1, len(rows))))

    def _build(self) -> Text:
        rows = tabs.all_tabs()
        width = max(20, (self.size.width or 100) - 2)
        tw = self._title_width(rows, width)
        out = Text(no_wrap=True, overflow="ellipsis")
        self._spans = []
        for tab in rows:
            active = tab.is_active()
            hover_tab = self._hover == ("tab", tab.id)
            hover_close = self._hover == ("close", tab.id)
            if active:
                bg = ui.BG_3
                fg = f"bold {ui.FG}"
            elif hover_tab or hover_close:
                bg = ui.BG_2
                fg = ui.FG
            else:
                bg = ui.BG_1
                fg = ui.FG_MUTE if tab.busy or tab.unread or tab.needs_input else ui.FG_DIM
            start = out.cell_len
            out.append(" ", style=f"on {bg}")
            mark, color = self._status(tab)
            if mark:
                out.append(f"{mark} ", style=f"{color} on {bg}")
            out.append(_clip(tab.title(), tw), style=f"{fg} on {bg}")
            out.append(" ", style=f"on {bg}")
            close_at = out.cell_len
            close_style = ui.ERR if hover_close else (ui.FG_MUTE if active else ui.FG_DIM)
            out.append("×", style=f"{close_style} on {bg}")
            out.append(" ", style=f"on {bg}")
            end = out.cell_len
            self._spans.append((start, close_at, "tab", tab.id))
            self._spans.append((close_at, end, "close", tab.id))
            out.append(" ")
        start = out.cell_len
        plus_style = f"bold {ui.ACCENT}" + (f" on {ui.BG_2}" if self._hover == ("new", 0) else "")
        out.append(" + ", style=plus_style)
        self._spans.append((start, out.cell_len, "new", 0))
        hint = "new chat" if len(rows) == 1 else ""
        if hint and out.cell_len + len(hint) + 2 <= width:
            out.append(hint, style=ui.FG_DIM)
        return out

    def render(self) -> Text:
        return self._build()

    # ── mouse ──────────────────────────────────────────────────────────
    def _hit(self, x: int) -> tuple[str, int] | None:
        x -= 1  # left padding
        for start, end, kind, tab_id in self._spans:
            if start <= x < end:
                return kind, tab_id
        return None

    def on_mouse_move(self, event) -> None:
        hit = self._hit(event.x)
        if hit != self._hover:
            self._hover = hit
            self.refresh()

    def on_leave(self, event) -> None:
        if self._hover is not None:
            self._hover = None
            self.refresh()

    def on_click(self, event) -> None:
        hit = self._hit(event.x)
        if hit is None:
            return
        event.stop()
        kind, tab_id = hit
        if kind == "tab" and getattr(event, "button", 1) == 2:
            kind = "close"  # middle click closes, like a browser
        fn = getattr(self.app, "tab_action", None)
        if callable(fn):
            fn(kind, tab_id)
