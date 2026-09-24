"""Sticky prompt + prompt-to-prompt navigation (mixed into ``JarvisTUI``).

The bar itself is ``sticky_prompt.StickyPrompt``; this keeps it in step
with the transcript's scroll position and the turn lifecycle, and binds
``alt+↑`` / ``alt+↓`` to jump between prompts.
"""
from __future__ import annotations

from textual.actions import SkipAction
from textual.screen import ModalScreen

from ..sticky_prompt import StickyPrompt
from ..transcript import Transcript, UserBlock


class PromptNavMixin:
    _sticky_on: bool = True

    def _load_sticky_pref(self) -> None:
        try:
            from ...storage.settings import get_settings

            self._sticky_on = bool(get_settings().get("ui.sticky_prompt", True))
        except Exception:
            self._sticky_on = True

    def _sync_sticky_prompt(self) -> None:
        """Show the prompt owning the top of the viewport (or hide the bar).
        Called on scroll, on every activity tick and after layout changes."""
        try:
            bar = self.query_one("#sticky_prompt", StickyPrompt)
            transcript = self.query_one("#transcript", Transcript)
        except Exception:
            return
        target = transcript.sticky_prompt() if self._sticky_on else None
        running = bool(target and self._busy and target[1] == target[2] - 1)
        width = transcript.scrollable_content_region.width if target else 0
        bar.sync(target, running=running, frame=self._frame, width=width)

    def prompt_nav(self, action: str, block: UserBlock | None = None) -> None:
        """Sticky-bar clicks: ``jump`` back to ``block``, or ``prev`` / ``next``
        relative to it (next past the last prompt re-follows the live end)."""
        try:
            transcript = self.query_one("#transcript", Transcript)
        except Exception:
            return
        if block is None:
            return
        if action == "jump":
            transcript.jump_to_prompt(block)
            return
        blocks = [b for b, _y, _bottom in transcript.prompt_spans()]
        if block not in blocks:
            return
        i = blocks.index(block) + (-1 if action == "prev" else 1)
        if 0 <= i < len(blocks):
            transcript.jump_to_prompt(blocks[i])
        elif i >= len(blocks):
            transcript.follow()

    def action_step_prompt(self, delta: int) -> None:
        if isinstance(self.screen, ModalScreen):
            raise SkipAction
        try:
            transcript = self.query_one("#transcript", Transcript)
        except Exception:
            return
        if not transcript.step_prompt(delta):
            self._set_status("no earlier prompt" if delta < 0 else "already at the latest output")
