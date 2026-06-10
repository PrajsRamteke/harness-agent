"""Multi-line prompt input widget for the Jarvis TUI.

Extracted from ``tui/app.py``. Talks to the host app purely through runtime
``self.app`` attribute access, so it carries no static dependency on the App
class and can be imported standalone (tests construct it directly).
"""
from __future__ import annotations

from textual.message import Message
from textual.widgets import TextArea


class PromptArea(TextArea):
    """Multi-line prompt input.

    - Enter submits.
    - Ctrl+J / Alt+Enter / Ctrl+Enter / Shift+Enter / Ctrl+N insert a newline.
    - Trailing backslash before Enter inserts a newline (bash-style).
    - Ctrl+D / Ctrl+C bubble up to the App.
    - @file mentions render with theme-colored background chips.
    - Dropped media/docs render as numbered chips like [image 1], [document 2].
    """

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def on_mount(self) -> None:
        from .prompt_highlight import PROMPT_THEME_NAME, build_prompt_text_area_theme

        self.register_theme(build_prompt_text_area_theme())
        self.theme = PROMPT_THEME_NAME
        self.language = None
        self.refresh_file_ref_highlights()

    def refresh_file_ref_highlights(self) -> None:
        from .prompt_highlight import build_prompt_highlights

        try:
            row, col = self.cursor_location
        except Exception:
            row, col = 0, 0
        self._highlights.clear()
        for line_no, spans in build_prompt_highlights(
            self.text or "",
            cursor_row=row,
            cursor_col=col,
        ).items():
            self._highlights[line_no] = spans
        self._line_cache.clear()
        self.refresh()

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        if event.text_area is not self:
            return
        self.refresh_file_ref_highlights()

    async def _on_paste(self, event) -> None:  # type: ignore[override]
        """Insert tokenized attachment chips instead of raw dropped paths."""
        if self.read_only:
            return
        # Textual dispatches _on_paste for every class in the MRO; suppress the
        # TextArea base handler so bracketed paste is not inserted twice.
        event.prevent_default()
        from ..prompt_attachments import tokenize_dropped_paths

        paste = event.text or ""
        tokenized, _, _ = tokenize_dropped_paths(paste)
        insert_text = tokenized if tokenized != paste else paste
        if result := self._replace_via_keyboard(insert_text, *self.selection):
            self.move_cursor(result.end_location)
            self.focus()
        try:
            self.app._last_input_value = self.text or ""
            self.refresh_file_ref_highlights()
        except Exception:
            pass

    async def _on_key(self, event):  # type: ignore[override]
        key = event.key
        if key == "escape":
            event.stop()
            event.prevent_default()
            try:
                if getattr(self.app, "file_ref_picker_active", False):
                    self.app.close_file_ref_picker()
                    return
                self.app.action_escape_action()
            except Exception:
                pass
            return
        if key in ("up", "down", "enter", "space", "escape"):
            try:
                if self.app._ask_user.handle_key(key):
                    event.stop()
                    event.prevent_default()
                    return
            except Exception:
                pass
        if key in ("up", "down", "tab"):
            try:
                if self.app.handle_prompt_key_for_file_ref(key):
                    event.stop()
                    event.prevent_default()
                    return
            except Exception:
                pass
        if key in ("shift+enter", "alt+enter", "ctrl+j", "ctrl+enter", "ctrl+n"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if key == "enter":
            event.stop()
            event.prevent_default()
            try:
                if self.app.try_accept_file_ref():
                    return
            except Exception:
                pass
            buf = self.text or ""
            n = 0
            for ch in reversed(buf):
                if ch == "\\":
                    n += 1
                else:
                    break
            if n % 2 == 1:
                self.text = buf[:-1] + "\n"
                try:
                    last_line = self.text.count("\n")
                    self.move_cursor((last_line, 0))
                except Exception:
                    pass
                return
            self.post_message(self.Submitted(buf))
            return
        if key == "ctrl+d":
            event.stop()
            event.prevent_default()
            self.app.exit()
            return
        if key == "ctrl+c":
            event.stop()
            event.prevent_default()
            try:
                self.app.action_cancel_or_quit()
            except Exception:
                self.app.exit()
            return
        if key == "ctrl+y":
            # Copy last reply (would otherwise be TextArea "redo", which is
            # near-useless in a chat composer; undo via ctrl+z still works).
            event.stop()
            event.prevent_default()
            try:
                self.app.action_copy_last_reply()
            except Exception:
                pass
            return
        if key == "ctrl+f":
            event.stop()
            event.prevent_default()
            try:
                self.app.action_open_tools_inspector()
            except Exception:
                pass
            return
        if key == "ctrl+t":
            event.stop()
            event.prevent_default()
            try:
                self.app.action_toggle_internal()
            except Exception:
                pass
            return
        if key in ("question_mark", "f1") and not (self.text or "").strip():
            event.stop()
            event.prevent_default()
            try:
                self.app.action_show_shortcuts()
            except Exception:
                pass
            return
