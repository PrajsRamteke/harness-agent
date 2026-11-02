"""Modal session picker — replaces the console.input-based /session flow."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import CenterMiddle, Vertical
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ..storage.sessions import db_count_sessions, db_list_sessions, db_delete_session, db_load_session
from ..repl.trim import count_tool_calls, estimate_session_tokens
from .. import state
from .modal_chrome import (
    TUI_MODAL_CHROME_CSS,
    TuiModalScreen,
    day_bucket,
    empty_row,
    hint_line,
    picker_row,
    relative_time,
    section_header,
)
from .mouse_toggle import enable_mouse, disable_mouse
from . import theme as ui


class SessionPickerScreen(TuiModalScreen[int | None]):
    """Saved conversations, newest first, grouped by day (pinned ones on top).

    Tab switches This project ↔ All projects; typing searches titles and every
    message; ↵ resume, ``^p`` pin, ``^a`` archive, ``^r`` rename, ``^d`` twice
    deletes. Returns the selected session id, or None if cancelled.
    """

    DEFAULT_CSS = (
        TUI_MODAL_CHROME_CSS
        + """
    SessionPickerScreen.tui-modal-screen #modal {
        width: 82%;
        max-width: 116;
        height: 80%;
        max-height: 40;
    }
    SessionPickerScreen OptionList {
        height: 1fr;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", show=True),
        # priority: the focused search Input would otherwise eat ^d
        Binding("ctrl+d", "delete", "Delete", show=False, priority=True),
        Binding("ctrl+r", "rename", "Rename", show=False, priority=True),
        Binding("ctrl+p", "pin", "Pin", show=False, priority=True),
        Binding("ctrl+a", "archive", "Archive", show=False, priority=True),
        Binding("tab", "toggle_scope", "All projects", show=False, priority=True),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("pageup", "page_up", show=False),
    ]

    PAGE_SIZE = 60
    SEARCH_LIMIT = 800

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            with Vertical(id="modal"):
                yield Static("▤  Sessions", id="modal_title")
                yield Static("", id="modal_status")
                yield Input(placeholder="Search conversations…", id="session_search")
                yield OptionList(id="session_list")
                yield Static(
                    hint_line(("↑↓", "navigate"), ("↵", "resume"), ("tab", "project/all"),
                              ("^p", "pin"), ("^a", "archive"), ("^r", "rename"),
                              ("^d", "delete"), ("esc", "close")),
                    id="modal_hint",
                )

    def on_mount(self):
        enable_mouse()
        self._prev_scroll_y = self.app.scroll_sensitivity_y
        self.app.scroll_sensitivity_y = 1.0
        self._rows: list = []
        self._offset = 0
        self._has_more = True
        self._query = ""
        self._pending_delete: int | None = None
        self._search_ids: list[int] | None = None  # message-text matches for the query
        self._search_seq = 0
        try:
            from ..storage.session_library import backfill_folders, here

            backfill_folders()
            self._here = here()
            # This project when it has chats, else everything.
            self._all = not self._here or db_count_sessions(cwd=self._here) == 0
        except Exception:
            self._here, self._all = "", True
        self._load_more()
        self._render_rows()
        self.query_one("#session_search", Input).focus()

    def on_unmount(self):
        disable_mouse()
        try:
            self.app.scroll_sensitivity_y = self._prev_scroll_y
        except AttributeError:
            pass

    # ── data ────────────────────────────────────────────────────────
    def _load_more(self, limit: int | None = None) -> None:
        if not self._has_more:
            return
        limit = limit or self.PAGE_SIZE
        try:
            if self._all:
                rows = db_list_sessions(limit=limit, offset=self._offset, pinned_first=True)
            else:
                rows = db_list_sessions(limit=limit, offset=self._offset, cwd=self._here, pinned_first=True)
        except Exception:
            rows = []
        seen = {r["id"] for r in self._rows}
        self._rows.extend(dict(r) for r in rows if r["id"] not in seen)  # dicts: a rename edits in place
        self._offset += len(rows)
        if len(rows) < limit:
            self._has_more = False

    def _reload(self, keep: str | None = None) -> None:
        """Start the list over (scope changed, a row was pinned / archived)."""
        self._rows, self._offset, self._has_more = [], 0, True
        self._load_more()
        if self._query.strip():
            self._search(self._query)
        self._render_rows(keep=keep)

    def _status(self) -> None:
        from ..storage.session_library import name_of

        try:
            total = db_count_sessions() if self._all else db_count_sessions(cwd=self._here)
        except Exception:
            total = len(self._rows)
        cur = state.current_session_id
        where = "all projects" if self._all else name_of(self._here)
        bits = [f"[bold {ui.ACCENT}]{where}[/] [{ui.FG_DIM}]{total} conversation{'s' if total != 1 else ''}[/]"]
        if cur is not None:
            bits.append(f"[{ui.FG_DIM}]current[/] [bold {ui.FG}]#{cur}[/]")
        if self._pending_delete is not None:
            bits.append(f"[{ui.WARN}]press ^d again to delete #{self._pending_delete}[/]")
        self.query_one("#modal_status", Static).update(f"   [{ui.FG_DIM}]·[/]   ".join(bits))

    # ── rendering ──────────────────────────────────────────────────
    def _render_rows(self, keep: str | None = None) -> None:
        opts = self.query_one("#session_list", OptionList)
        opts.clear_options()
        q = self._query.strip().lower()
        # Titles match at once; chats whose messages match join when the
        # background search (session_library) answers.
        found = set(self._search_ids or ()) if q else set()
        rows = [r for r in self._rows if not q or r["id"] in found or q in (r["title"] or "").lower()
                or q in (r["model"] or "").lower() or q == f"#{r['id']}"]
        if not rows:
            opts.add_option(empty_row(
                f"No conversations match “{self._query.strip()}”" if q else "No saved conversations yet"
            ))
            self._status()
            return
        options = []
        bucket = None
        for r in rows:
            b = "PINNED" if r.get("pinned") and not q else day_bucket(r["updated_at"])
            if b != bucket:
                options.append(section_header(b, first=bucket is None))
                bucket = b
            sid = r["id"]
            title = " ".join((r["title"] or "Untitled").split())
            n = r["msg_count"] or 0
            model = (r["model"] or "").rsplit("/", 1)[-1]
            right = f"{n} msg{'s' if n != 1 else ''} · {relative_time(r['updated_at'])}"
            detail = model if model else ""
            cwd = r.get("cwd") or ""
            if self._all and cwd != self._here:
                from ..storage.session_library import name_of

                detail = f"▸ {name_of(cwd) if cwd else 'no folder'}" + (f" · {model}" if model else "")
            if r.get("pinned"):
                title = f"◆ {title}"
            if sid == self._pending_delete:
                right = "^d again to delete"
            options.append(Option(
                picker_row(
                    title,
                    detail=detail,
                    right=right,
                    active=sid == state.current_session_id,
                    query=q,
                    right_style=ui.WARN if sid == self._pending_delete else None,
                ),
                id=str(sid),
            ))
        opts.add_options(options)
        try:
            opts.highlighted = opts.get_option_index(keep) if keep else None
        except Exception:
            opts.highlighted = None
        if opts.highlighted is None:
            opts.action_first()
        self._status()

    def _current_id(self) -> int | None:
        opts = self.query_one("#session_list", OptionList)
        if opts.option_count == 0 or opts.highlighted is None:
            return None
        opt = opts.get_option_at_index(opts.highlighted)
        if not opt.id or opt.id == "__none__":
            return None
        try:
            return int(opt.id)
        except ValueError:
            return None

    # ── events ─────────────────────────────────────────────────────
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "session_search":
            return
        self._query = event.value or ""
        self._pending_delete = None
        self._search_ids = None
        if self._query.strip() and self._has_more:
            self._load_more(self.SEARCH_LIMIT)  # search across older sessions too
        self._render_rows()  # titles at once; message matches follow
        if len(self._query.strip()) >= 2:
            self._search(self._query)

    def _search(self, query: str) -> None:
        self._search_seq += 1
        seq = self._search_seq
        cwd = None if self._all else self._here

        def work() -> None:
            from ..storage.sessions import db_search_session_ids

            try:
                ids = db_search_session_ids(query, cwd=cwd, limit=self.SEARCH_LIMIT)
            except Exception:
                return
            try:
                self.app.call_from_thread(done, ids)
            except Exception:
                pass

        def done(ids: list[int]) -> None:
            if seq != self._search_seq or query != self._query or not self.is_attached:
                return
            have = {r["id"] for r in self._rows}
            missing = [i for i in ids if i not in have]
            if missing:  # older matches past the loaded pages
                try:
                    extra = db_list_sessions(limit=len(missing), ids=missing, archived=None)
                    self._rows.extend(dict(r) for r in extra)
                except Exception:
                    pass
            self._search_ids = ids
            self._render_rows(keep=str(self._current_id()) if self._current_id() else None)

        self.run_worker(work, thread=True, exclusive=True, group="session-search")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "session_search":
            sid = self._current_id()
            if sid is not None:
                self.dismiss(sid)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if self._pending_delete is not None and self._current_id() != self._pending_delete:
            self._pending_delete = None
            self._render_rows(keep=str(self._current_id()) if self._current_id() else None)
            return
        opts = self.query_one("#session_list", OptionList)
        try:
            idx = int(event.option_index)
        except (TypeError, ValueError):
            return
        if self._has_more and idx >= opts.option_count - 6 and not self._query.strip():
            keep = event.option.id
            self._load_more()
            self._render_rows(keep=keep)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        try:
            self.dismiss(int(event.option.id))
        except (TypeError, ValueError):
            self.dismiss(None)

    # ── bindings ───────────────────────────────────────────────────
    def action_dismiss_cancel(self):
        inp = self.query_one("#session_search", Input)
        if inp.value:
            inp.value = ""
            return
        self.dismiss(None)

    def action_select(self):
        self.dismiss(self._current_id())

    def action_cursor_down(self):
        self.query_one("#session_list", OptionList).action_cursor_down()

    def action_cursor_up(self):
        self.query_one("#session_list", OptionList).action_cursor_up()

    def action_page_down(self):
        self.query_one("#session_list", OptionList).action_page_down()

    def action_page_up(self):
        self.query_one("#session_list", OptionList).action_page_up()

    def action_toggle_scope(self):
        """Tab: this project's chats ↔ every project's."""
        if not self._here:
            return
        self._all = not self._all
        self._pending_delete = None
        self._reload()

    def _row(self, sid: int) -> dict | None:
        return next((r for r in self._rows if r["id"] == sid), None)

    def action_pin(self):
        from ..storage.sessions import db_set_pinned

        sid = self._current_id()
        row = sid is not None and self._row(sid)
        if not row:
            return
        on = not row.get("pinned")
        db_set_pinned([sid], on)
        self.app.notify(("Pinned" if on else "Unpinned") + f" “{(row['title'] or '')[:40]}”", timeout=2)
        self._reload(keep=str(sid))

    def action_archive(self):
        from ..storage.sessions import db_set_archived

        sid = self._current_id()
        row = sid is not None and self._row(sid)
        if not row:
            return
        if sid == state.current_session_id:
            self.app.notify("That's the chat you're in — start /new first", severity="warning", timeout=3)
            return
        db_set_archived([sid], True)
        self.app.notify(f"Archived #{sid} — the web Sessions dialog lists archived chats", timeout=3)
        self._reload()

    def action_rename(self):
        """Edit the highlighted session's title (the app's rename dialog)."""
        sid = self._current_id()
        if sid is None:
            return
        self._pending_delete = None

        def after() -> None:
            from ..storage.sessions import db_get_title

            title = db_get_title(sid)
            for r in self._rows:
                if r["id"] == sid and title:
                    r["title"] = title
            self._render_rows(keep=str(sid))
            try:
                self.query_one("#session_search", Input).focus()
            except Exception:
                pass

        self.app.open_rename_dialog(sid, after=after)

    def action_delete(self):
        """Two-step delete: first ^d arms, second ^d on the same row deletes."""
        sid = self._current_id()
        if sid is None:
            return
        if sid == state.current_session_id:
            # Same rule as the web remote: the live chat keeps writing to this
            # id, so deleting it would orphan every message that follows.
            self.app.notify("Can't delete the session you're in — start /new first",
                            severity="warning", timeout=3)
            return
        if self._pending_delete != sid:
            self._pending_delete = sid
            self._render_rows(keep=str(sid))
            return
        self._pending_delete = None
        opts = self.query_one("#session_list", OptionList)
        idx = opts.highlighted or 0
        try:
            deleted = db_delete_session(sid)
        except Exception as e:
            self._render_rows(keep=str(sid))
            self.app.notify(f"Couldn't delete session #{sid}: {e}", severity="error", timeout=4)
            return
        self._rows = [r for r in self._rows if r["id"] != sid]
        if deleted:
            self._offset = max(0, self._offset - 1)  # later DB rows shift up by one
        self._render_rows()
        try:
            enabled = [i for i in range(opts.option_count) if not opts.get_option_at_index(i).disabled]
            if enabled:
                opts.highlighted = min(enabled, key=lambda i: abs(i - idx))
        except Exception:
            pass
        self.app.notify(f"Deleted session #{sid}", timeout=2)


def resume_session_into_state(sid: int, console_print, preview: bool = True, *, quiet: bool = False) -> bool:
    """Shared helper: load session into state, render a short tail preview."""
    loaded = db_load_session(sid)
    if loaded is None:
        if not quiet:
            console_print(f"[red]session {sid} not found[/]")
        return False
    state.messages = loaded
    state.current_session_id = sid
    try:  # an old chat whose folder wasn't recorded now lives here
        from ..storage.sessions import db_session_info, db_set_session_cwd
        from ..storage.session_library import here

        info = db_session_info(sid) or {}
        if not info.get("cwd") or info.get("cwd_guessed"):
            db_set_session_cwd(sid, here())
    except Exception:
        pass
    # Recounted from the history, like the tokens — resetting to 0 made a
    # resumed session show "0 tool calls" in the sidebars and /stats.
    state.tool_calls_count = count_tool_calls(loaded)
    state.total_in, state.total_out, state.total_tokens = estimate_session_tokens(loaded)
    from ..storage.sessions import restore_session_stats

    restore_session_stats(state, sid)  # Claude Code CLI cost + exact context size
    if not quiet:
        console_print(f"[green]▶ resumed session #{sid} ({len(state.messages)} messages)[/]")
    if not preview:
        return True
    tail = state.messages[-6:]
    for m in tail:
        cn = m["content"]
        if isinstance(cn, str):
            preview = cn[:200]
        else:
            texts = [b.get("text", "") for b in cn
                     if isinstance(b, dict) and b.get("type") == "text"]
            preview = (" ".join(texts))[:200] or "[tool blocks]"
        console_print(f"  [dim]{m['role']}:[/] {preview}")
    return True
