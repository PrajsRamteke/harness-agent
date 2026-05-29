"""@file-mention picker for the Jarvis TUI prompt.

Handles the dropdown that appears when the user types ``@`` in the composer:
populating matches, navigating, accepting a path, and keeping it in sync with
the cursor. Mixed into ``JarvisTUI``.
"""
from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ..file_ref_picker import file_ref_option_label, filter_project_files
from ..mouse_toggle import disable_mouse, enable_mouse
from ..prompt_area import PromptArea
from ...prompt_refs import active_file_ref_at_cursor, replace_file_ref_at_cursor


class FileRefPickerMixin:
    """@file-mention picker behaviour for ``JarvisTUI``."""

    def _prompt_has_focus(self) -> bool:
        try:
            return self.query_one("#prompt", PromptArea).has_focus
        except Exception:
            return False

    def _try_file_ref_scroll(self, direction: str) -> bool:
        """Route ↑/↓/page keys to the @file picker when it is open."""
        if not self.file_ref_picker_active or not self._prompt_has_focus():
            return False
        delta_map = {"up": -1, "down": 1, "pageup": -5, "pagedown": 5}
        delta = delta_map.get(direction)
        if delta is None:
            return False
        return self._navigate_file_ref_picker(delta)

    def _navigate_file_ref_picker(self, delta: int) -> bool:
        if not self.file_ref_picker_active:
            return False
        opts = self.query_one("#file_ref_picker", OptionList)
        if opts.option_count == 0:
            return True
        cur = opts.highlighted if opts.highlighted is not None else 0
        nxt = max(0, min(opts.option_count - 1, cur + delta))
        opts.highlighted = nxt
        try:
            opts.scroll_to_highlight()
        except Exception:
            pass
        opts.refresh()
        return True

    @property
    def file_ref_picker_active(self) -> bool:
        try:
            panel = self.query_one("#file_ref_panel", Vertical)
            return not panel.has_class("hidden")
        except Exception:
            return False

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "prompt":
            return
        val = event.text_area.text or ""
        prev = self._last_input_value
        self._last_input_value = val
        if val == "/" and prev == "":
            event.text_area.clear()
            self._last_input_value = ""
            self._open_palette()
            return
        if not self._tokenizing_attachments:
            self._run_attachment_tokenize()
        self._sync_file_ref_picker()
        if isinstance(event.text_area, PromptArea):
            event.text_area.refresh_file_ref_highlights()

    def _prompt_cursor(self) -> tuple[int, int]:
        inp = self.query_one("#prompt", PromptArea)
        try:
            return inp.cursor_location
        except Exception:
            text = inp.text or ""
            lines = text.split("\n")
            return max(0, len(lines) - 1), len(lines[-1]) if lines else 0

    def _populate_file_ref_picker(self, query: str, *, keep_highlight_id: str | None = None) -> None:
        opts = self.query_one("#file_ref_picker", OptionList)
        status = self.query_one("#file_ref_hint", Static)
        paths = filter_project_files(query)
        opts.clear_options()
        if not paths:
            q = (query or "").strip()
            status.update(
                f"📎  no matches @{q or '…'} — root files/folders only until you type more"
                if not q
                else f"📎  no files match @{q} — keep typing in chat"
            )
            return
        q = (query or "").strip()
        scope = "project root" if not q else f"@{q}"
        status.update(
            f"📎  {scope} — {len(paths)} match(es) · type in chat · ↑↓ pick · tab/↵ insert · esc close"
        )
        for path in paths:
            opts.add_option(Option(file_ref_option_label(path), id=path))
        if not opts.option_count:
            return
        pick = 0
        if keep_highlight_id:
            for i in range(opts.option_count):
                opt = opts.get_option_at_index(i)
                if opt and opt.id == keep_highlight_id:
                    pick = i
                    break
        opts.highlighted = pick
        try:
            opts.scroll_to_highlight()
        except Exception:
            pass

    def _sync_file_ref_picker(self) -> None:
        inp = self.query_one("#prompt", PromptArea)
        text = inp.text or ""
        row, col = self._prompt_cursor()
        active = active_file_ref_at_cursor(text, row, col)
        panel = self.query_one("#file_ref_panel", Vertical)
        if not active:
            self.close_file_ref_picker()
            return
        self._file_ref_mention = active
        panel.remove_class("hidden")
        if not self._file_ref_mouse_on:
            enable_mouse()
            self._file_ref_mouse_on = True
        _row, _start, query = active
        opts = self.query_one("#file_ref_picker", OptionList)
        keep_id = None
        if opts.option_count and opts.highlighted is not None:
            try:
                opt = opts.get_option_at_index(opts.highlighted)
                if opt and opt.id:
                    keep_id = str(opt.id)
            except Exception:
                pass
        if self._file_ref_last_query != query:
            self._file_ref_last_query = query
            self._populate_file_ref_picker(query, keep_highlight_id=keep_id)
        elif opts.option_count == 0:
            self._populate_file_ref_picker(query)

    def close_file_ref_picker(self) -> None:
        try:
            panel = self.query_one("#file_ref_panel", Vertical)
            panel.add_class("hidden")
        except Exception:
            pass
        self._file_ref_mention = None
        self._file_ref_last_query = None
        if self._file_ref_mouse_on:
            disable_mouse()
            self._file_ref_mouse_on = False

    def _accept_file_ref(self, rel_path: str) -> None:
        inp = self.query_one("#prompt", PromptArea)
        text = inp.text or ""
        row, col = self._prompt_cursor()
        new_text, (new_row, new_col) = replace_file_ref_at_cursor(
            text, row, col, rel_path
        )
        inp.text = new_text
        try:
            inp.move_cursor((new_row, new_col))
        except Exception:
            pass
        self._last_input_value = new_text
        self.close_file_ref_picker()
        inp.refresh_file_ref_highlights()
        inp.focus()

    def try_accept_file_ref(self) -> bool:
        if not self.file_ref_picker_active:
            return False
        opts = self.query_one("#file_ref_picker", OptionList)
        if opts.option_count == 0 or opts.highlighted is None:
            return False
        opt = opts.get_option_at_index(opts.highlighted)
        if not opt or not opt.id:
            return False
        self._accept_file_ref(str(opt.id))
        return True

    def handle_prompt_key_for_file_ref(self, key: str) -> bool:
        if not self.file_ref_picker_active:
            return False
        if key == "up":
            return self._navigate_file_ref_picker(-1)
        if key == "down":
            return self._navigate_file_ref_picker(1)
        if key == "tab":
            return self.try_accept_file_ref()
        return False

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        if event.text_area.id == "prompt":
            self._sync_file_ref_picker()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "file_ref_picker" or not event.option.id:
            return
        self._accept_file_ref(str(event.option.id))
