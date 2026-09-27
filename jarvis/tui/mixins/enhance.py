"""One-click prompt enhance in the composer (mixed into ``JarvisTUI``).

``✦ enhance`` (or ⌃G) sends the prompt to the current model on a worker
thread (``jarvis/prompt_enhance.py``) and swaps the corrected text into the
input box as a single undoable edit. Nothing is sent: the user reads it,
edits it, and presses ↵ when happy. The button then offers ``↶ undo``
(⌃G again or ⌃Z) until the prompt is edited or sent. Esc cancels a running
enhancement; the prompt is read-only while one runs.
"""
from __future__ import annotations

import threading

from textual.screen import ModalScreen

from ... import prompt_enhance
from ... import state
from ..enhance_button import BUSY, IDLE, UNDO, EnhanceButton
from ..prompt_area import PromptArea


class EnhanceMixin:
    def _enhance_init(self) -> None:
        self._enhance_gen = 0              # bumps on every start / cancel
        self._enhance_running = False
        self._enhance_original: str | None = None   # what ↶ undo restores
        self._enhance_result: str | None = None     # the text we put in

    # ── widgets ──────────────────────────────────────────────────────
    def _enhance_button(self) -> EnhanceButton | None:
        try:
            return self.query_one("#enhance", EnhanceButton)
        except Exception:
            return None

    def _enhance_prompt_area(self) -> PromptArea | None:
        try:
            return self.query_one("#prompt", PromptArea)
        except Exception:
            return None

    def _enhance_sync(self) -> None:
        """Keep the button in step with the prompt (called on every edit)."""
        btn = self._enhance_button()
        area = self._enhance_prompt_area()
        if btn is None or area is None:
            return
        text = area.text or ""
        if self._enhance_result is not None and text != self._enhance_result:
            # Edited (or ⌃Z'd) since the enhance — the undo offer is over.
            self._enhance_original = self._enhance_result = None
        if self._enhance_running:
            mode = BUSY
        elif self._enhance_result is not None:
            mode = UNDO
        else:
            mode = IDLE
        btn.set_mode(mode)
        btn.set_class(mode == IDLE and not prompt_enhance.can_enhance(text), "hidden")

    # ── actions ──────────────────────────────────────────────────────
    def action_enhance_prompt(self) -> None:
        """Button click / ⌃G: enhance, or undo the enhance just made."""
        if isinstance(self.screen, ModalScreen) or self._enhance_running:
            return
        area = self._enhance_prompt_area()
        if area is None:
            return
        text = area.text or ""
        if self._enhance_result is not None and text == self._enhance_result:
            self._enhance_undo()
            return
        reason = prompt_enhance.skip_reason(text)
        if reason:
            self._set_status(f"✦ {reason}")
            return
        if state.client is None:
            self._set_status("✦ no model connected — run /provider")
            return
        self._enhance_gen += 1
        gen = self._enhance_gen
        self._enhance_running = True
        area.read_only = True
        self.close_file_ref_picker()  # its Enter would edit the locked prompt
        self._enhance_sync()
        model = state.MODEL.rsplit("/", 1)[-1]
        self._set_status(f"✦ enhancing your prompt with {model}… · esc cancels")
        threading.Thread(
            target=self._enhance_worker, args=(gen, text),
            name="prompt-enhance-ui", daemon=True,
        ).start()

    def _enhance_worker(self, gen: int, text: str) -> None:
        result = error = None
        try:
            result = prompt_enhance.enhance_prompt(text)
        except prompt_enhance.EnhanceError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 — never kill the UI over this
            error = str(exc) or type(exc).__name__
        try:
            self.call_from_thread(self._enhance_done, gen, text, result, error)
        except Exception:
            pass  # app is shutting down

    def _enhance_done(self, gen: int, original: str, result, error: str | None) -> None:
        if gen != self._enhance_gen:
            return  # cancelled
        self._enhance_finish()
        area = self._enhance_prompt_area()
        if area is None:
            return
        if (area.text or "") != original:
            self._set_status("✦ prompt changed meanwhile — enhancement discarded")
            return
        if error:
            self._set_status(f"✦ couldn't enhance: {error}")
            return
        if result is None or not result.changed:
            self._set_status("✦ looks good — nothing to fix")
            return
        self._enhance_replace(area, result.text)
        self._enhance_original, self._enhance_result = original, result.text
        self._enhance_sync()
        self._set_status("✦ prompt enhanced — ↵ send · ⌃G undo")

    def _enhance_finish(self) -> None:
        self._enhance_running = False
        area = self._enhance_prompt_area()
        if area is not None:
            area.read_only = False
        self._enhance_sync()

    def _enhance_cancel(self) -> bool:
        """Esc / ⌃C while enhancing: stop waiting (the reply is dropped)."""
        if not self._enhance_running:
            return False
        self._enhance_gen += 1
        self._enhance_finish()
        self._set_status("✦ enhance cancelled")
        return True

    def _enhance_undo(self) -> None:
        area = self._enhance_prompt_area()
        original = self._enhance_original
        if area is None or original is None:
            return
        self._enhance_original = self._enhance_result = None
        self._enhance_replace(area, original)
        self._enhance_sync()
        self._set_status("✦ original prompt restored")

    def _enhance_forget(self) -> None:
        """The prompt was sent or cleared — no undo, no pending result."""
        if self._enhance_running:
            self._enhance_gen += 1
            self._enhance_running = False
            area = self._enhance_prompt_area()
            if area is not None:
                area.read_only = False
        self._enhance_original = self._enhance_result = None
        self._enhance_sync()

    @staticmethod
    def _enhance_replace(area: PromptArea, text: str) -> None:
        """Swap the whole prompt as one undo step (⌃Z brings the old one back)."""
        area.history.checkpoint()
        area.replace(text, (0, 0), area.document.end, maintain_selection_offset=False)
        area.history.checkpoint()
        lines = text.split("\n")
        area.move_cursor((len(lines) - 1, len(lines[-1])))
        area.focus()
