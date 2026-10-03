"""Queued messages — rows above the composer while Jarvis works.

    ≡ Queued 2  · runs when Jarvis finishes · ↑ edits the last         clear all
    1  fix the failing auth test and check the…    ⚡ send now   ✎ edit   ✕
    ⚡ then update the docs                          next step   ↶ undo   ✕
    ✎ rename the helper        editing in the message box · ↵ save · esc cancel

``send now`` hands the message to the running turn at its next step (between
tool calls) instead of after the reply; ``edit`` moves it into the message box
(↵ puts it back in its place); ``✕`` drops it. Pure view: the queue lives in
``jarvis/prompt_queue.py`` and the app's ``queue_action`` does the work.
"""
from __future__ import annotations

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widget import Widget

from . import theme as ui

_NARROW = 76  # below this width the buttons are just their glyphs

# action → (wide label, narrow label)
_LABELS = {
    "send": ("⚡ send now", "⚡"),
    "unsteer": ("↶ undo", "↶"),
    "edit": ("✎ edit", "✎"),
    "remove": ("✕", "✕"),
    "clear": ("clear all", "clear"),
}


def _button(out: Text, action: str, *, hot: bool, narrow: bool) -> None:
    label = _LABELS[action][1 if narrow else 0]
    if hot:
        bg = ui.ERR if action in ("remove", "clear") else ui.ACCENT
        out.append(f" {label} ", style=f"bold {ui.BG_0} on {bg}")
        return
    if action == "send":
        color = ui.ACCENT
    elif action in ("remove", "clear"):
        color = ui.FG_DIM
    else:
        color = ui.FG_MUTE
    out.append(f" {label} ", style=f"{color} on {ui.BG_3}" if action != "clear" else color)


class _ClickRow(Widget):
    """One line with clickable buttons on the right (hit-tested by x)."""

    DEFAULT_CSS = """
    _ClickRow {
        height: 1;
        width: 100%;
        background: transparent;
    }
    """

    can_focus = False

    def __init__(self) -> None:
        super().__init__()
        self._spans: list[tuple[int, int, str]] = []
        self._hover: str = ""
        self._row_hover = False

    # subclasses: return [(action, …)] and build the left side
    def _actions(self) -> list[str]:
        return []

    def _left(self, width: int) -> Text:
        return Text()

    def _narrow(self) -> bool:
        return (self.app.size.width or 100) < _NARROW

    def render(self) -> Text:
        width = max(10, self.size.width or 80)
        narrow = self._narrow()
        right = Text(no_wrap=True)
        spans: list[tuple[int, int, str]] = []
        for i, action in enumerate(self._actions()):
            if i:
                right.append(" ")
            start = right.cell_len
            _button(right, action, hot=self._hover == action, narrow=narrow)
            spans.append((start, right.cell_len, action))
        room = max(4, width - right.cell_len - 2)
        left = self._left(room)
        left.truncate(room, overflow="ellipsis")
        gap = max(1, width - left.cell_len - right.cell_len)
        offset = left.cell_len + gap
        self._spans = [(offset + a, offset + b, act) for a, b, act in spans]
        out = Text(no_wrap=True, overflow="ellipsis")
        out.append_text(left)
        out.append(" " * gap)
        out.append_text(right)
        if self._row_hover:
            out.stylize(f"on {ui.BG_2}")
        return out

    def _action_at(self, x: int) -> str:
        for start, end, action in self._spans:
            if start <= x < end:
                return action
        return ""

    def on_mouse_move(self, event) -> None:
        action = self._action_at(event.x)
        if action != self._hover or not self._row_hover:
            self._hover = action
            self._row_hover = True
            self.refresh()

    def on_leave(self) -> None:
        if self._hover or self._row_hover:
            self._hover = ""
            self._row_hover = False
            self.refresh()

    def on_click(self, event) -> None:
        action = self._action_at(event.x)
        if action:
            event.stop()
            self._fire(action)

    def _fire(self, action: str) -> None:
        pass


class QueueHeader(_ClickRow):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0
        self.steered = 0

    def _actions(self) -> list[str]:
        return ["clear"] if self.count > 1 else []

    def _left(self, width: int) -> Text:
        out = Text(no_wrap=True)
        out.append("≡ ", style=ui.FG_DIM)
        out.append("Queued ", style=f"bold {ui.FG_MUTE}")
        out.append(str(self.count), style=f"bold {ui.ACCENT}")
        if self.steered:
            note = " · ⚡ goes in at Jarvis's next step"
        else:
            note = " · runs when Jarvis finishes"
        if not self._narrow():
            note += " · ↑ edits the last"
        out.append(note, style=ui.FG_DIM)
        return out

    def _fire(self, action: str) -> None:
        self.app.queue_action(action)


class QueueRow(_ClickRow):
    def __init__(self, item, n: int) -> None:
        super().__init__()
        self.item = item
        self.n = n

    def set_item(self, item, n: int) -> None:
        self.item, self.n = item, n
        self.refresh()

    def _actions(self) -> list[str]:
        item = self.item
        if item.held:
            return []
        if item.steer:
            return ["unsteer", "remove"]
        if item.steerable:
            return ["send", "edit", "remove"]
        return ["edit", "remove"]

    def _left(self, width: int) -> Text:
        from ..media import queue_label

        item = self.item
        text = " ".join(queue_label(item).split())
        out = Text(no_wrap=True)
        if item.held:
            out.append("✎ ", style=f"bold {ui.ACCENT_2}")
            note = "  editing in the message box · ↵ save · esc cancel"
            room = max(4, width - 2 - len(note))
            body = Text(text, style=ui.FG_MUTE)
            body.truncate(room, overflow="ellipsis")
            out.append_text(body)
            out.append(note, style=f"italic {ui.ACCENT_2}")
            return out
        if item.steer:
            out.append("⚡ ", style=f"bold {ui.ACCENT}")
            note = "  next step" if not self._narrow() else ""
            room = max(4, width - 2 - len(note))
            body = Text(text, style=ui.FG)
            body.truncate(room, overflow="ellipsis")
            out.append_text(body)
            out.append(note, style=f"italic {ui.ACCENT}")
            return out
        out.append(f"{self.n:<2} ", style=ui.FG_DIM)
        out.append(text, style=ui.FG if self._row_hover else ui.FG_MUTE)
        return out

    def _fire(self, action: str) -> None:
        self.app.queue_action(action, self.item.id)


class QueueBar(VerticalScroll):
    """The queue panel: a header row + one row per queued message."""

    # Look: ``#queuebar`` in theme.py (app CSS — it outranks DEFAULT_CSS).
    DEFAULT_CSS = """
    QueueBar.hidden {
        display: none;
    }
    """

    can_focus = False

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=(classes + " hidden") if classes else "hidden")
        self._leaving: set = set()  # rows being removed (removal is async)

    def compose(self):
        yield QueueHeader()

    def show_items(self, items: list) -> None:
        """Render ``items`` (``QueuedPrompt``s); hide when there are none."""
        self.set_class(not items, "hidden")
        self._leaving = {r for r in self._leaving if r.is_attached}
        rows = [r for r in self.query(QueueRow) if r.is_attached and r not in self._leaving]
        for row in rows[len(items):]:
            self._leaving.add(row)
            row.remove()
        for n, (row, item) in enumerate(zip(rows, items), 1):
            row.set_item(item, n)
        extra = [QueueRow(item, n) for n, item in enumerate(items, 1)][len(rows):]
        if extra:
            self.mount(*extra)
        try:
            header = self.query_one(QueueHeader)
        except Exception:
            return
        header.count = len(items)
        header.steered = sum(1 for it in items if it.steer)
        header.refresh()

    def plain_rows(self) -> list[str]:
        """Rendered text of each row (tests)."""
        return [str(w.render().plain).rstrip() for w in self.children
                if isinstance(w, _ClickRow) and w not in self._leaving]

    def button_x(self, qid: str, action: str) -> int | None:
        """Where ``action``'s button sits on ``qid``'s row (tests)."""
        for row in self.query(QueueRow):
            if row.item.id == qid:
                row.render()
                for start, _end, act in row._spans:
                    if act == action:
                        return start + 1
        return None
