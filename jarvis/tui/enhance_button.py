"""The composer's ``✦ enhance`` button (right edge of the input box).

Click (or ⌃G) fixes spelling and grammar of the prompt with the current model;
while that runs it spins, and afterwards it turns into ``↶ undo`` until the
prompt is edited or sent. The flow lives in ``mixins/enhance.py``.
"""
from __future__ import annotations

from rich.text import Text
from textual.widget import Widget

from . import theme as ui

IDLE, BUSY, UNDO = "idle", "busy", "undo"
_NARROW = 70  # below this terminal width the button is just its glyph


class EnhanceButton(Widget):
    DEFAULT_CSS = """
    EnhanceButton {
        width: auto;
        height: 1;
        margin: 1 0 1 2;
        background: transparent;
    }
    EnhanceButton.hidden {
        display: none;
    }
    """

    can_focus = False

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self.mode = IDLE
        self._hover = False
        self._frame = 0
        self._spin = None
        self._sync_tooltip()

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        if mode == BUSY and self._spin is None:
            self._spin = self.set_interval(1 / 12, self._tick)
        elif mode != BUSY and self._spin is not None:
            self._spin.stop()
            self._spin = None
        self._sync_tooltip()
        self.refresh(layout=True)

    def _tick(self) -> None:
        self._frame += 1
        self.refresh()

    def _sync_tooltip(self) -> None:
        self.tooltip = {
            IDLE: "Fix spelling & grammar with the current model (⌃G) — you still decide to send",
            BUSY: "Enhancing… esc cancels",
            UNDO: "Put your original prompt back (⌃G or ⌃Z)",
        }[self.mode]

    def _narrow(self) -> bool:
        return (self.app.size.width or 100) < _NARROW

    def _label(self) -> Text:
        narrow = self._narrow()
        out = Text(no_wrap=True)
        if self.mode == BUSY:
            frames = ui.SPINNER_FRAMES
            out.append(frames[self._frame % len(frames)], style=f"bold {ui.ACCENT}")
            if not narrow:
                out.append(" enhancing", style=ui.FG_MUTE)
            return out
        if self.mode == UNDO:
            glyph, word, color = "↶", " undo", ui.ACCENT_2
        else:
            glyph, word, color = "✦", " enhance", ui.ACCENT
        hot = self._hover
        out.append(glyph, style=f"bold {color}" if hot or self.mode == UNDO else color)
        if not narrow:
            word_style = color if hot else (ui.FG_MUTE if self.mode == UNDO else ui.FG_DIM)
            out.append(word, style=f"{word_style} underline" if hot else word_style)
        return out

    def get_content_width(self, container, viewport) -> int:
        return self._label().cell_len

    def render(self) -> Text:
        return self._label()

    def on_enter(self) -> None:
        self._hover = True
        self.refresh()

    def on_leave(self) -> None:
        self._hover = False
        self.refresh()

    async def on_click(self, event) -> None:
        event.stop()
        await self.app.run_action("enhance_prompt")
