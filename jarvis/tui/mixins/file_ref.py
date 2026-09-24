"""Inline completion popup above the composer: ``/commands`` and ``@files``.

Typing ``/`` at the start of the prompt lists slash commands (prefix
matches first, then substring matches on name/description); typing ``@``
anywhere lists project files. ↑/↓ move, Tab completes, Enter completes (and
runs a command that takes no arguments), Esc closes. Mixed into
``JarvisTUI``.
"""
from __future__ import annotations

from rich.text import Text
from textual.containers import Vertical
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from .. import theme as ui
from ..file_ref_picker import file_ref_option_label, filter_project_files
from ..prompt_area import PromptArea
from ...prompt_refs import active_file_ref_at_cursor, replace_file_ref_at_cursor


def slash_matches(text: str, limit: int = 60) -> list[tuple[str, str]]:
    """Commands for the typed ``/prefix`` — prefix hits, then fuzzy-ish hits."""
    from ..commands_catalog import filter_commands

    q = (text or "").strip().lower()
    catalog = filter_commands("/")
    if q in ("", "/"):
        return catalog[:limit]
    starts = [(c, d) for c, d in catalog if c.lower().startswith(q)]
    # An exact name (e.g. a custom "/pr") beats longer built-ins ("/provider").
    starts.sort(key=lambda cd: cd[0].strip().lower() != q)
    seen = {c for c, _ in starts}
    bare = q.lstrip("/")
    # Short queries only match names — descriptions are too noisy for "/m".
    inside = [(c, d) for c, d in catalog if c not in seen and bare in c.lower()] if len(bare) >= 2 else []
    seen.update(c for c, _ in inside)
    desc = [(c, d) for c, d in catalog if c not in seen and bare in d.lower()] if len(bare) >= 3 else []
    return (starts + inside + desc)[:limit]


class FileRefPickerMixin:
    """Slash-command + @file completion popup for ``JarvisTUI``."""

    _popup_mode: str | None = None  # "file" | "slash" | None

    # ── state ───────────────────────────────────────────────────────
    def _prompt_has_focus(self) -> bool:
        try:
            return self.query_one("#prompt", PromptArea).has_focus
        except Exception:
            return False

    @property
    def file_ref_picker_active(self) -> bool:
        try:
            return not self.query_one("#popup", Vertical).has_class("hidden")
        except Exception:
            return False

    @property
    def completion_active(self) -> bool:
        return self.file_ref_picker_active

    def _try_file_ref_scroll(self, direction: str) -> bool:
        """Route page keys to the popup when it is open."""
        if not self.file_ref_picker_active or not self._prompt_has_focus():
            return False
        delta = {"up": -1, "down": 1, "pageup": -5, "pagedown": 5}.get(direction)
        if delta is None:
            return False
        return self._navigate_file_ref_picker(delta)

    def _navigate_file_ref_picker(self, delta: int) -> bool:
        if not self.file_ref_picker_active:
            return False
        opts = self.query_one("#popup_list", OptionList)
        if opts.option_count == 0:
            return True
        cur = opts.highlighted if opts.highlighted is not None else 0
        opts.highlighted = (cur + delta) % opts.option_count if abs(delta) == 1 else max(
            0, min(opts.option_count - 1, cur + delta)
        )
        try:
            opts.scroll_to_highlight()
        except Exception:
            pass
        return True

    # ── text changes ───────────────────────────────────────────────
    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "prompt":
            return
        val = event.text_area.text or ""
        self._last_input_value = val
        hist = getattr(self, "_history", None)
        if hist is not None and hist.browsing and val not in hist.entries:
            hist.reset()
        if not self._tokenizing_attachments:
            self._run_attachment_tokenize()
        self._sync_file_ref_picker()
        self._sync_composer_mode()
        if isinstance(event.text_area, PromptArea):
            event.text_area.refresh_file_ref_highlights()

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        if event.text_area.id == "prompt":
            self._sync_file_ref_picker()

    def _sync_composer_mode(self) -> None:
        try:
            comp = self.query_one("#composer")
            prefix = self.query_one("#prompt_prefix", Static)
        except Exception:
            return
        shell = (self._last_input_value or "").startswith("!")
        comp.set_class(shell, "-shell")
        prefix.update("!" if shell else ui.ARROW)

    def _prompt_cursor(self) -> tuple[int, int]:
        inp = self.query_one("#prompt", PromptArea)
        try:
            return inp.cursor_location
        except Exception:
            text = inp.text or ""
            lines = text.split("\n")
            return max(0, len(lines) - 1), len(lines[-1]) if lines else 0

    def _slash_query(self) -> str | None:
        text = self.query_one("#prompt", PromptArea).text or ""
        if not text.startswith("/") or any(ch.isspace() for ch in text):
            return None
        return text

    def _sync_file_ref_picker(self) -> None:
        try:
            inp = self.query_one("#prompt", PromptArea)
        except Exception:
            return
        # Text placed by history recall / a completion keeps the popup closed
        # until the user edits it (so ↑/↓ keep walking history).
        quiet = getattr(self, "_popup_suppressed_for", None)
        if quiet is not None:
            if (inp.text or "") == quiet:
                self.close_file_ref_picker()
                return
            self._popup_suppressed_for = None
        slash = self._slash_query()
        if slash is not None:
            self._show_slash_popup(slash)
            return
        text = inp.text or ""
        row, col = self._prompt_cursor()
        active = active_file_ref_at_cursor(text, row, col)
        if not active:
            self.close_file_ref_picker()
            return
        self._file_ref_mention = active
        _row, _start, query = active
        if self._popup_mode != "file" or self._file_ref_last_query != query:
            self._file_ref_last_query = query
            self._show_file_popup(query)

    # ── popup rendering ────────────────────────────────────────────
    def _open_popup(self, mode: str) -> None:
        self._popup_mode = mode
        self.query_one("#popup", Vertical).remove_class("hidden")

    def _show_slash_popup(self, query: str) -> None:
        if self._popup_mode == "slash" and getattr(self, "_slash_last_query", None) == query:
            return
        opts = self.query_one("#popup_list", OptionList)
        hint = self.query_one("#popup_hint", Static)
        matches = slash_matches(query)
        self._slash_last_query = query
        opts.clear_options()
        if not matches:
            self.close_file_ref_picker()
            return
        width = max(12, min(22, max(len(c.strip()) for c, _ in matches) + 2))
        typed = query.strip().lower()
        for cmd, desc in matches:
            name = cmd.strip()
            label = Text(no_wrap=True, overflow="ellipsis")
            if len(typed) > 1 and name.lower().startswith(typed):
                label.append(name[:len(typed)], style=f"bold {ui.ACCENT}")
                label.append(name[len(typed):], style=f"bold {ui.FG}")
            else:
                label.append(name, style=f"bold {ui.FG}")
            label.append(" " * max(1, width - len(name)))
            label.append(desc, style=ui.FG_DIM)
            opts.add_option(Option(label, id=cmd))
        opts.highlighted = 0  # best match first on every keystroke
        hint.update(Text.assemble(
            ("commands", f"bold {ui.FG_MUTE}"),
            (f"  {len(matches)}", ui.FG_DIM),
            ("   ↑↓ select · tab complete · ↵ run · esc close", ui.FG_DIM),
        ))
        self._open_popup("slash")

    def _show_file_popup(self, query: str) -> None:
        opts = self.query_one("#popup_list", OptionList)
        hint = self.query_one("#popup_hint", Static)
        paths = filter_project_files(query)
        opts.clear_options()
        q = (query or "").strip()
        if not paths:
            hint.update(Text.assemble(
                ("files", f"bold {ui.FG_MUTE}"),
                (f"   no match for @{q}" if q else "   type to search", ui.FG_DIM),
            ))
            self._open_popup("file")
            return
        for path in paths:
            opts.add_option(Option(file_ref_option_label(path), id=path))
        opts.highlighted = 0
        hint.update(Text.assemble(
            ("files", f"bold {ui.FG_MUTE}"),
            (f"  {len(paths)}{'+' if len(paths) >= 60 else ''}", ui.FG_DIM),
            ("   ↑↓ select · tab/↵ insert · esc close", ui.FG_DIM),
        ))
        self._open_popup("file")

    def close_file_ref_picker(self) -> None:
        try:
            self.query_one("#popup", Vertical).add_class("hidden")
        except Exception:
            pass
        self._popup_mode = None
        self._slash_last_query = None
        self._file_ref_mention = None
        self._file_ref_last_query = None

    # ── accepting ──────────────────────────────────────────────────
    def _accept_file_ref(self, rel_path: str) -> None:
        inp = self.query_one("#prompt", PromptArea)
        text = inp.text or ""
        row, col = self._prompt_cursor()
        new_text, (new_row, new_col) = replace_file_ref_at_cursor(text, row, col, rel_path)
        self._popup_suppressed_for = new_text
        inp.text = new_text
        try:
            inp.move_cursor((new_row, new_col))
        except Exception:
            pass
        self._last_input_value = new_text
        self.close_file_ref_picker()
        inp.refresh_file_ref_highlights()
        inp.focus()

    def _highlighted_id(self) -> str | None:
        opts = self.query_one("#popup_list", OptionList)
        if opts.option_count == 0 or opts.highlighted is None:
            return None
        opt = opts.get_option_at_index(opts.highlighted)
        return str(opt.id) if opt and opt.id else None

    def _accept_slash(self, cmd: str, *, run: bool) -> None:
        inp = self.query_one("#prompt", PromptArea)
        needs_args = cmd.endswith(" ")
        self.close_file_ref_picker()
        if run and not needs_args:
            inp.text = cmd.strip()
            self._last_input_value = inp.text
            self.close_file_ref_picker()
            inp.post_message(PromptArea.Submitted(inp.text))
            return
        text = cmd if needs_args else cmd.strip()
        self._popup_suppressed_for = text
        inp.text = text
        inp.move_cursor((0, len(text)))
        self._last_input_value = text
        self.close_file_ref_picker()
        inp.focus()

    def try_accept_file_ref(self, *, run: bool = True) -> bool:
        """Accept the highlighted popup row. Returns True if consumed."""
        if not self.file_ref_picker_active:
            return False
        oid = self._highlighted_id()
        if oid is None:
            if self._popup_mode == "file":
                self.close_file_ref_picker()
            return False
        if self._popup_mode == "slash":
            self._accept_slash(oid, run=run)
            return True
        self._accept_file_ref(oid)
        return True

    def handle_prompt_key_for_file_ref(self, key: str) -> bool:
        if not self.file_ref_picker_active:
            return False
        if key == "up":
            return self._navigate_file_ref_picker(-1)
        if key == "down":
            return self._navigate_file_ref_picker(1)
        if key == "tab":
            return self.try_accept_file_ref(run=False)
        return False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "popup_list" or not event.option.id:
            return
        if self._popup_mode == "slash":
            self._accept_slash(str(event.option.id), run=True)
        else:
            self._accept_file_ref(str(event.option.id))
