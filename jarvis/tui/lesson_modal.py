"""Centered lesson control modal — list / search / add / delete / clear.

User interaction:
    ↑/↓        navigate the lesson list
    /          focus the search input
    a          add a new lesson (sub-modal)
    d          delete the highlighted lesson
    c          wipe all (asks for 'yes' confirmation)
    r          refresh
    Esc        close (or unfocus search)
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from rich.text import Text

from ..storage import lessons as ls
from .modal_chrome import TUI_MODAL_CHROME_CSS, TuiModalScreen, _ellipsis
from .mouse_toggle import enable_mouse, disable_mouse


class _AddLessonScreen(TuiModalScreen[tuple[str, str, str] | None]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    _AddLessonScreen #modal { width: 72%; max-width: 110; max-height: 65%; }
    _AddLessonScreen #lab1, _AddLessonScreen #lab2, _AddLessonScreen #lab3 {
        color: #8b949e;
        padding: 0 1;
    }
    _AddLessonScreen #lab2, _AddLessonScreen #lab3 { margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("➕  New Lesson", id="modal_title")
                yield Static("Task  [#6e7681](what were you doing)[/]", id="lab1")
                yield Input(placeholder="e.g. wiring Redux Saga with TypeScript", id="add_task")
                yield Static("Lesson  [#6e7681](what you learned)[/]", id="lab2")
                yield Input(placeholder="e.g. always wrap dispatch in put()", id="add_lesson")
                yield Static("Tags  [#6e7681](comma-separated, optional)[/]", id="lab3")
                yield Input(placeholder="redux,saga,typescript", id="add_tags")
                yield Static(
                    "[#f0b3ff]↵[/] on tags to save   [#f0b3ff]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        self.query_one("#add_task", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "add_task":
            self.query_one("#add_lesson", Input).focus()
            return
        if event.input.id == "add_lesson":
            self.query_one("#add_tags", Input).focus()
            return
        task = self.query_one("#add_task", Input).value.strip()
        lesson = self.query_one("#add_lesson", Input).value.strip()
        tags = self.query_one("#add_tags", Input).value.strip()
        if not task or not lesson:
            return
        self.dismiss((task, lesson, tags))

    def action_cancel(self) -> None:
        self.dismiss(None)


class _ConfirmClearLessonsScreen(TuiModalScreen[bool]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    _ConfirmClearLessonsScreen #modal { width: 52%; max-width: 70; max-height: 32%; }
    _ConfirmClearLessonsScreen #confirm_prompt {
        padding: 0 1; color: #e6edf3; margin-bottom: 1;
    }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("⚠  Wipe All Lessons?", id="modal_title")
                yield Static(
                    "Type [bold #d29922]yes[/] to confirm.",
                    id="confirm_prompt",
                )
                yield Input(placeholder="yes", id="confirm_input")
                yield Static(
                    "[#f0b3ff]↵[/] confirm   [#f0b3ff]esc[/] cancel",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        self.query_one("#confirm_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss((event.value or "").strip().lower() == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


class LessonModalScreen(TuiModalScreen[None]):
    DEFAULT_CSS = TUI_MODAL_CHROME_CSS + """
    LessonModalScreen #modal { width: 82%; max-width: 130; max-height: 85%; }
    LessonModalScreen OptionList { height: 1fr; min-height: 12; }
    LessonModalScreen Input { margin-bottom: 1; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Close", show=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("a", "add", "Add", show=True),
        Binding("d", "delete", "Delete", show=True),
        Binding("delete", "delete", show=False),
        Binding("c", "clear", "Clear", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("slash", "focus_search", "Search", show=True),
    ]

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("≡  Lessons", id="modal_title")
                yield Static("", id="modal_status")
                yield Input(placeholder="search…  (esc clears, focuses list)", id="lesson_search")
                yield OptionList(id="lesson_list")
                yield Static(
                    "[#f0b3ff]↑↓[/] nav   [#f0b3ff]/[/] search   "
                    "[#f0b3ff]a[/] add   [#f0b3ff]d[/] delete   "
                    "[#f0b3ff]c[/] clear   [#f0b3ff]r[/] refresh   "
                    "[#f0b3ff]esc[/] close",
                    id="modal_hint",
                )

    def on_mount(self) -> None:
        enable_mouse()
        self._populate()

    def on_unmount(self) -> None:
        disable_mouse()

    def _populate(self, rows=None) -> None:
        opts = self.query_one("#lesson_list", OptionList)
        opts.clear_options()
        if rows is None:
            rows = ls.list_lessons()
        if not rows:
            opts.add_option(Option(
                Text("  no lessons yet — press 'a' to add one",
                     style="italic #6e7681"),
                disabled=True,
            ))
        else:
            for r in rows:
                tag_part = ""
                if r.get("tags"):
                    tag_part = f"   [{', '.join(r['tags'])}]"
                row = Text.assemble(
                    ("  ", ""),
                    (f"#{r['id']:<5d}", "#6e7681"),
                    (f"  ×{r.get('hits', 0):<3d}", "#8b949e"),
                    ("  ", ""),
                    (_ellipsis(r["task"], 28), "bold #e6edf3"),
                    ("  → ", "#6e7681"),
                    (_ellipsis(r["lesson"], 50), "#e6edf3"),
                    (_ellipsis(tag_part, 25), "#bc8cff"),
                )
                opts.add_option(Option(row, id=f"lesson:{r['id']}"))
        try:
            self.query_one("#modal_title", Static).update(
                f"≡  Lessons   [#6e7681]{len(rows)} entr{'ies' if len(rows) != 1 else 'y'}[/]"
            )
        except Exception:
            pass
        opts.focus()

    def _notify(self, msg: str, error: bool = False) -> None:
        try:
            color = "#f85149" if error else "#3fb950"
            self.query_one("#modal_status", Static).update(f"[{color}]{msg}[/]")
        except Exception:
            pass

    def _highlighted_id(self) -> int | None:
        opts = self.query_one("#lesson_list", OptionList)
        if opts.highlighted is None:
            return None
        try:
            opt = opts.get_option_at_index(opts.highlighted)
        except Exception:
            return None
        oid = getattr(opt, "id", None)
        if not oid or not oid.startswith("lesson:"):
            return None
        try:
            return int(oid.split(":", 1)[1])
        except ValueError:
            return None

    # ── events ─────────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "lesson_search":
            return
        q = (event.value or "").strip()
        if not q:
            self._populate()
        else:
            self._populate(rows=ls.search(q, limit=20))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "lesson_search":
            self.query_one("#lesson_list", OptionList).focus()

    # ── actions ────────────────────────────────────────────────────────────

    def action_dismiss_cancel(self) -> None:
        # If search has text, Esc clears it; otherwise close
        try:
            sb = self.query_one("#lesson_search", Input)
            if sb.value:
                sb.value = ""
                self._populate()
                self.query_one("#lesson_list", OptionList).focus()
                return
        except Exception:
            pass
        self.dismiss(None)

    def action_focus_search(self) -> None:
        self.query_one("#lesson_search", Input).focus()

    def action_cursor_down(self) -> None:
        self.query_one("#lesson_list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#lesson_list", OptionList).action_cursor_up()

    def action_add(self) -> None:
        def after(result):
            if not result:
                return
            task, lesson, tags = result
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
            s = ls.add_lesson(task, lesson, tag_list)
            self._populate()
            self._notify(f"✓ saved #{s['id']}: {task[:40]} → {lesson[:40]}")
        self.app.push_screen(_AddLessonScreen(), after)

    def action_delete(self) -> None:
        lid = self._highlighted_id()
        if lid is None:
            self._notify("(highlight a lesson to delete)")
            return
        if ls.delete_lesson(lid):
            self._populate()
            self._notify(f"✓ deleted #{lid}")
        else:
            self._notify(f"no lesson #{lid}", error=True)

    def action_clear(self) -> None:
        def after(ok: bool) -> None:
            if not ok:
                self._notify("clear cancelled")
                return
            n = ls.clear_all()
            self._populate()
            self._notify(f"✓ cleared {n} lesson(s)")
        self.app.push_screen(_ConfirmClearLessonsScreen(), after)

    def action_refresh(self) -> None:
        try:
            self.query_one("#lesson_search", Input).value = ""
        except Exception:
            pass
        self._populate()
        self._notify("re-read storage")
