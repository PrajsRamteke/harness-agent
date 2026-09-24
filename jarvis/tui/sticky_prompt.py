"""Sticky prompt — the message you sent stays pinned above its reply.

Once the user box of the turn you're reading scrolls out of view, a compact
copy of it docks at the top of the transcript. It's an overlay, so nothing
underneath shifts when it appears, and it follows whichever turn owns the
top of the viewport — reading back through history works too::

    ┃ fix the stall retry in stream.py and add a test …   ⠹ 12s   ↑ 3/7 ↓   ⎘

* click the text — jump back to that prompt (it glows briefly)
* ↑ / ↓ — previous / next prompt (``alt+↑`` / ``alt+↓`` from anywhere)
* ⎘ — copy the prompt
* hover — expands to show the whole prompt
* the spinner + elapsed time show while that turn is still running

Settings: ``ui.sticky_prompt`` (default on).
"""
from __future__ import annotations

import time

from rich.text import Text
from textual import events
from textual.css.scalar import Scalar
from textual.widget import Widget

from . import theme as ui
from .transcript import UserBlock, _append_with_refs, wrap_lines

_EXPANDED_LINES = 6
_HOVER_DELAY = 0.35   # a pointer resting here while you wheel-scroll shouldn't pop it open
_NARROW = 56          # below this content width the counter is dropped


def _fmt_elapsed(s: float) -> str:
    if s < 60:
        return f"{int(s)}s"
    m, sec = divmod(int(s), 60)
    return f"{m}m {sec:02d}s"


class StickyPrompt(Widget):
    """Docked (overlay) copy of the prompt whose reply you're reading."""

    DEFAULT_CSS = """
    StickyPrompt {
        layer: overlay;
        dock: top;
        height: auto;
        width: 1fr;
        margin: 0 0 0 3;
        padding: 1 2;  /* same box as UserBlock — it looks like the prompt stuck */
        background: $jv-user-bg;
        border-left: heavy $jv-accent;
        color: $jv-fg;
    }
    StickyPrompt.-hidden {
        display: none;
    }
    StickyPrompt.-shell {
        border-left: heavy $jv-warn;
    }
    """

    can_focus = False

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="-hidden")
        self.block: UserBlock | None = None
        self.index = 0
        self.total = 0
        self.running = False
        self.expanded = False
        self._frame = 0
        self._key: tuple | None = None
        self._copied_at = 0.0
        self._hover_timer = None
        # (start, end, action) click targets on the first row, content coords.
        self._zones: list[tuple[int, int, str]] = []

    @property
    def shown(self) -> bool:
        return self.block is not None and not self.has_class("-hidden")

    # ── state ────────────────────────────────────────────────────────
    def sync(self, target: tuple[UserBlock, int, int] | None, *, running: bool = False,
             frame: int = 0, width: int = 0) -> None:
        """Show ``(block, index, total)``, or hide on ``None``. Cheap when
        nothing changed; the spinner repaints only this one row.

        ``width`` is the transcript's content width: a docked ``1fr`` ignores
        the right margin, so the bar is sized to sit exactly over the user
        boxes (and off the scrollbar)."""
        if target is None:
            if self.block is not None:
                self.block = None
                self._key = None
                self._collapse()
                self.add_class("-hidden")
            return
        block, index, total = target
        key = (id(block), index, total, running)
        changed = key != self._key or self.has_class("-hidden")
        self._key = key
        self.block, self.index, self.total, self.running = block, index, total, running
        self._frame = frame
        if width > 0 and self.styles.width != Scalar.parse(str(width)):
            self.styles.width = width
            changed = True
        if changed:
            self.set_class(block.has_class("-shell"), "-shell")
            self.remove_class("-hidden")
            self.refresh(layout=True)
        elif running:
            self.refresh()

    # ── mouse ────────────────────────────────────────────────────────
    def on_enter(self, event: events.Enter) -> None:
        if self._hover_timer is not None:
            self._hover_timer.stop()
        self._hover_timer = self.set_timer(_HOVER_DELAY, self._expand)

    def on_leave(self, event: events.Leave) -> None:
        self._collapse()

    def _expand(self) -> None:
        self._hover_timer = None
        if self.shown and not self.expanded:
            self.expanded = True
            self.refresh(layout=True)

    def _collapse(self) -> None:
        if self._hover_timer is not None:
            self._hover_timer.stop()
            self._hover_timer = None
        if self.expanded:
            self.expanded = False
            self.refresh(layout=True)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._scroll_transcript(event, -1)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._scroll_transcript(event, 1)

    def _scroll_transcript(self, event: events.MouseEvent, sign: int) -> None:
        # The bar floats over the transcript (a sibling), so the wheel event
        # would otherwise bubble past it and do nothing.
        event.stop()
        self._collapse()
        try:
            t = self.app.query_one("#transcript")
            t.scroll_relative(y=sign * max(1, int(self.app.scroll_sensitivity_y)), animate=False)
        except Exception:
            pass

    def on_click(self, event: events.Click) -> None:
        event.stop()
        if self.block is None:
            return
        action = "jump"
        # The ↑ ↓ ⎘ targets extend into the padding rows around them.
        off = event.get_content_offset_capture(self)
        if -1 <= off.y <= 1:
            for start, end, name in self._zones:
                if start <= off.x < end:
                    action = name
                    break
        if action == "copy":
            self._copy()
            return
        nav = getattr(self.app, "prompt_nav", None)
        if callable(nav):
            self._collapse()
            nav(action, self.block)

    def _copy(self) -> None:
        if self.block is None:
            return
        text = self.block.text.strip()
        copy = getattr(self.app, "_copy_text", None)
        if callable(copy):
            copy(text)
        self._copied_at = time.monotonic()
        self.refresh()
        self.set_timer(1.6, self.refresh)
        self.app.notify(f"Copied prompt ({len(text):,} chars)", timeout=1.8)

    # ── render ───────────────────────────────────────────────────────
    def _meta(self, width: int) -> tuple[Text, list[tuple[int, int, str]]]:
        out = Text(no_wrap=True)
        zones: list[tuple[int, int, str]] = []

        def seg(s: str, style: str = "", action: str | None = None) -> None:
            start = out.cell_len
            out.append(s, style=style)
            if action:
                zones.append((start, out.cell_len, action))

        live = ui.ACCENT if self.expanded else ui.FG_MUTE
        off = ui.blend(ui.FG_DIM, ui.BG_0, 0.45)
        if self.running:
            sp = ui.SPINNER_FRAMES[self._frame % len(ui.SPINNER_FRAMES)]
            seg(sp, ui.ACCENT)
            t0 = getattr(self.app, "_turn_t0", 0.0)
            if t0:
                seg(f" {_fmt_elapsed(max(0.0, time.monotonic() - t0))}", ui.FG_DIM)
            seg("  ")
        seg(" ↑ ", live if self.index > 0 else off, "prev")
        if width >= _NARROW:
            seg(f"{self.index + 1}/{self.total}", ui.FG_DIM, "jump")
        seg(" ↓ ", live, "next")
        seg(" ")
        if time.monotonic() - self._copied_at < 1.5:
            seg(" ✓ ", ui.OK, "copy")
        else:
            seg(" ⎘ ", live, "copy")
        return out, zones

    def _prompt_line(self, room: int) -> Text:
        """The whole prompt on one row (whitespace folded), ellipsized."""
        text = " ".join((self.block.text if self.block else "").split())
        out = Text(no_wrap=True)
        _append_with_refs(out, text)
        if out.cell_len > room:
            out.truncate(max(1, room - 1), overflow="crop")
            out.append("…", style=ui.FG_DIM)
        return out

    def _build(self, width: int) -> tuple[Text, list[tuple[int, int, str]]]:
        width = max(12, width)
        meta, zones = self._meta(width)
        room = max(4, width - meta.cell_len - 2)
        body: list[Text]
        more = 0
        if self.expanded and self.block is not None:
            wrapped = Text()
            for n, line in enumerate(self.block.text.strip("\n").split("\n")):
                if n:
                    wrapped.append("\n")
                _append_with_refs(wrapped, line)
            body = wrap_lines(wrapped, room)
            if len(body) > _EXPANDED_LINES:
                more = len(body) - _EXPANDED_LINES
                body = body[:_EXPANDED_LINES]
        else:
            body = [self._prompt_line(room)]
        out = Text(no_wrap=True, overflow="crop", end="")
        first = body[0]
        out.append_text(first)
        pad = max(2, width - first.cell_len - meta.cell_len)
        out.append(" " * pad)
        shift = out.cell_len
        out.append_text(meta)
        for line in body[1:]:
            out.append("\n")
            out.append_text(line)
        if self.expanded:
            hint = f"… +{more} more lines · " if more else ""
            out.append(f"\n\n{hint}click to jump back · alt+↑ alt+↓ step through prompts",
                       style=ui.FG_DIM)
        return out, [(a + shift, b + shift, name) for a, b, name in zones]

    def get_content_height(self, container, viewport, width: int) -> int:
        if self.block is None:
            return 1
        return self._build(width)[0].plain.count("\n") + 1

    def render(self) -> Text:
        if self.block is None:
            return Text("")
        text, self._zones = self._build(self.content_region.width or self.size.width)
        return text

    def plain_text(self) -> str:
        return self.render().plain if self.block is not None else ""
