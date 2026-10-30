"""Chat tabs in the TUI: several sessions open at once, like browser tabs.

The tab strip (``tui/tab_bar.py``) sits above everything; ``+`` opens a new
chat without closing the one you were in. Every tab has its own transcript
widget, console and turn — a reply keeps streaming into its tab while you
read or type in another one (``jarvis/tabs.py`` binds the turn's threads to
it, so ``state.messages`` & co. are that tab's).

How the app stays per-tab without rewriting every call site:

* the turn bookkeeping the app always kept on ``self`` (``_busy``,
  ``_turn_id`` …) are properties reading ``tabs.current()``;
* ``self._tui_console`` is a router that forwards to the current tab's
  console (and its web mirror while the web remote runs);
* ``call_from_thread`` runs the callback bound to the calling worker's tab,
  so ``_turn_done`` & co. finish *that* tab's turn even when another is shown;
* things on screen that belong to the shown tab only — footer, sidebar,
  queue bar, placeholder, web busy flag — skip updates from a background tab
  (``_tab_visible``) and are repainted on every switch (``_show_active_tab``).

Mixed into ``JarvisTUI``.
"""
from __future__ import annotations

import inspect
import threading
import time

from rich.markup import escape as _rich_escape

from ... import state, tabs
from .. import theme as ui


def _tab_attr(name: str) -> property:
    def fget(self):
        return getattr(tabs.current(), name)

    def fset(self, value):
        setattr(tabs.current(), name, value)

    return property(fget, fset)


class TabConsoleRouter:
    """The one ``console`` every module holds: forwards to the current tab's.

    The current tab is the calling thread's (a turn's worker, its tool and
    subagent threads), else the one on screen. While the web remote runs a
    tab's console is wrapped by a ``WebMuxConsole`` that mirrors to the
    browser only while that tab is shown.
    """

    renders_tool_rows = True

    @staticmethod
    def _target():
        tab = tabs.current()
        return tab.mux or tab.console

    def __getattr__(self, name: str):
        target = self._target()
        if target is None:
            raise AttributeError(name)
        return getattr(target, name)


class _TabBridge:
    """A tab's view of the web bridge: its stream / tool events reach the
    browser only while it is the tab on screen (the page shows that one).
    Prompts (approvals, questions) always go through."""

    def __init__(self, bridge, tab: tabs.Tab) -> None:
        self._bridge = bridge
        self._tab = tab

    def emit(self, *args, **kwargs):
        if self._tab.is_active():
            return self._bridge.emit(*args, **kwargs)
        return None

    def __getattr__(self, name: str):
        return getattr(self._bridge, name)


class TabsMixin:
    # Turn bookkeeping: per tab (see module docstring).
    _busy = _tab_attr("busy")
    _turn_id = _tab_attr("turn_id")
    _turn_threads = _tab_attr("turn_threads")
    _turn_cancelled = _tab_attr("turn_cancelled")
    _turn_is_llm = _tab_attr("turn_is_llm")
    _reply_before_turn = _tab_attr("reply_before_turn")
    _turn_t0 = _tab_attr("turn_t0")
    _activity_label = _tab_attr("activity_label")
    _activity_t0 = _tab_attr("activity_t0")
    _last_footer_source = _tab_attr("last_footer_source")

    def _tabs_init(self) -> None:
        tabs.adopt()
        self._console_router = TabConsoleRouter()
        self._turn_seq = 0  # turn ids are unique across tabs
        self._turn_tabs: dict[int, tabs.Tab] = {}  # turn id → its tab, until the worker starts
        tabs.add_listener(self._on_tabs_changed)

    @property
    def _tui_console(self):
        return self._console_router

    @property
    def _web_mux(self):
        return tabs.current().mux

    # ── context ──────────────────────────────────────────────────────
    @staticmethod
    def _tab_visible() -> bool:
        """Is the code running now working for the tab on screen?"""
        return tabs.current() is tabs.active()

    def _next_turn_id(self) -> int:
        self._turn_seq += 1
        return self._turn_seq

    def call_from_thread(self, callback, *args, **kwargs):
        """Run ``callback`` on the UI thread for the calling worker's tab."""
        tab = tabs.tab_of_thread(threading.get_ident())
        if tab is None:
            return super().call_from_thread(callback, *args, **kwargs)

        async def run():
            with tabs.bound(tab):
                result = callback(*args, **kwargs)
            # Awaited unbound: other UI work interleaves on this thread meanwhile.
            if inspect.isawaitable(result):
                result = await result
            return result

        return super().call_from_thread(run)

    # ── widgets ──────────────────────────────────────────────────────
    def _attach_tab_ui(self, tab: tabs.Tab, transcript=None) -> None:
        """Give ``tab`` its transcript (mounted, hidden until shown) and console."""
        from ..console_shim import TUIConsole
        from ..transcript import Transcript
        from ..mixins.activity import ActivityLine

        if transcript is None:
            transcript = Transcript(id=f"transcript-{tab.id}")
            transcript.display = False
            self.query_one("#body").mount(transcript, before=self.query_one("#sticky_prompt"))
        transcript.set_class(not state.show_internal, "-trace-off")
        tab.transcript = transcript
        tab.console = TUIConsole(self, transcript, self.query_one("#activity", ActivityLine))
        self._wrap_tab_for_web(tab)

    def _wrap_tab_for_web(self, tab: tabs.Tab) -> None:
        bridge = getattr(self, "_web_bridge", None)
        if bridge is None or tab.console is None:
            tab.mux = None
            return
        from ...web.console_mux import WebMuxConsole

        tab.mux = WebMuxConsole(tab.console, _TabBridge(bridge, tab))

    def _refresh_tab_bar(self) -> None:
        try:
            self.query_one("#tabbar").refresh()
        except Exception:
            pass

    def _on_tabs_changed(self) -> None:
        if threading.current_thread() is threading.main_thread():
            self._refresh_tab_bar()
        else:
            try:
                super().call_from_thread(self._refresh_tab_bar)
            except Exception:
                pass

    def _show_active_tab(self) -> None:
        """Show the active tab's transcript and repaint what belongs to it."""
        active = tabs.active()
        for tab in tabs.all_tabs():
            if tab.transcript is not None:
                tab.transcript.display = tab is active
        try:
            from ..prompt_area import PromptArea

            prompt = self.query_one("#prompt", PromptArea)
            prompt.text = active.draft
            lines = active.draft.split("\n")
            prompt.move_cursor((len(lines) - 1, len(lines[-1])))
            prompt.focus()
        except Exception:
            pass
        self._last_input_value = active.draft
        self._sync_composer_mode()
        if active.busy:
            from ..app import _BUSY_PLACEHOLDER

            self._set_placeholder(_BUSY_PLACEHOLDER)
            self._status_msg = "thinking…"
        else:
            from ..app import _PLACEHOLDER

            self._set_placeholder(_PLACEHOLDER)
            self._status_msg = "ready"
        active.needs_input = False
        self._sync_composer_busy()
        self._refresh_queue_bar()
        self._render_footer()
        self._refresh_sidebar()
        self._refresh_activity_widgets()
        self._refresh_tab_bar()
        if active.transcript is not None:
            self.call_after_refresh(active.transcript.follow)
        self.call_after_refresh(self._sync_sticky_prompt)
        self._web_tab_switched()

    def _web_tab_switched(self) -> None:
        """Pages show the active tab: send its transcript now, then its live reply."""
        bridge = getattr(self, "_web_bridge", None)
        if bridge is None:
            return
        try:
            from ...web.state_api import snapshot_from_state, state_fields

            snap = snapshot_from_state(busy=bool(self._busy))
            bridge.emit("snapshot", snap)
            watcher = getattr(getattr(self, "_web_server", None), "state_watcher", None)
            if watcher is not None:
                watcher.mark_synced(state_fields(busy=bool(self._busy)))
            mux = tabs.active().mux
            if mux is not None:
                mux.replay_live()
        except Exception:
            pass
        self._sync_web_busy()

    # ── open / switch / close ────────────────────────────────────────
    def _new_tab(self, *, quiet: bool = False) -> tabs.Tab | None:
        """``+``: a new chat in a new tab; the current one keeps going."""
        from ...storage.sessions import db_create_session

        self._save_draft()
        try:
            tab = tabs.create()  # at the end, like a browser's +
        except ValueError as exc:
            self.notify(str(exc).capitalize(), severity="warning", timeout=4)
            return None
        self._attach_tab_ui(tab)
        with tabs.bound(tab):
            state.current_session_id = db_create_session(state.MODEL)
            self._mount_welcome()
        self._show_active_tab()
        if not quiet:
            self._set_status(f"new chat · tab {tabs.index_of(tab) + 1}")
        return tab

    def _save_draft(self) -> None:
        try:
            from ..prompt_area import PromptArea

            tabs.active().draft = self.query_one("#prompt", PromptArea).text
        except Exception:
            pass

    def _switch_tab(self, tab: tabs.Tab | None) -> bool:
        if tab is None:
            return False
        if tab.is_active():
            tab.unread = False
            self._refresh_tab_bar()
            return True
        if getattr(self, "_queue_editing", None):
            self._queue_edit_cancel()
        self._save_draft()
        if not tabs.activate(tab):
            return False
        self._show_active_tab()
        return True

    def _close_tab(self, tab: tabs.Tab | None) -> None:
        """Close ``tab`` (its session stays in /session). A running reply stops."""
        if tab is None:
            return
        note = ""
        if tab.busy:
            with tabs.bound(tab):
                note = self._stop_turn_for_session_change()
        if tabs.count() <= 1:
            # The last tab: like /new — a fresh chat in it.
            with tabs.bound(tab):
                self._reset_tab_session()
            self._show_active_tab()
            self._set_status("new chat" + (f" · {note}" if note else ""))
            return
        title = tab.title()
        if tab.console is not None:
            tab.console.cancel_pending_prompts()
        nxt = tabs.close(tab)
        if tab.transcript is not None:
            tab.transcript.remove()
        tab.transcript = tab.console = tab.mux = None
        if nxt is not None:
            self._show_active_tab()
        sid = tab.session_id
        msg = f"closed “{title}”"
        if note:
            msg += f" · {note}"
        if sid and tab.session.get("messages"):
            msg += f" · /session #{sid} reopens it"
        self.notify(msg, timeout=4)

    def _reset_tab_session(self) -> None:
        """A fresh, empty session in the current tab (``/new`` semantics)."""
        from ...storage.sessions import db_create_session

        tab = tabs.current()
        tab.session.clear()
        tab.session.update(tabs._session_defaults())
        state.current_session_id = db_create_session(state.MODEL)
        tab.unread = tab.needs_input = False
        transcript = self._transcript()
        transcript.clear()
        self._tui_console.forget_tools()
        self._mount_welcome()

    def _focus_tab_of_console(self, console) -> None:
        """A tab asks the user something (approval, question): show it."""
        for tab in tabs.all_tabs():
            if tab.console is console or tab.mux is console:
                if not tab.is_active():
                    title = tab.title()
                    self._switch_tab(tab)
                    self.notify(f"“{title}” needs your answer", severity="warning", timeout=5)
                return

    def _tab_finished_in_background(self, tab: tabs.Tab) -> None:
        tab.unread = True
        self._refresh_tab_bar()
        title = tab.title()
        mark = "⏹" if tab.turn_cancelled else "✓"
        self.notify(f"{mark} “{title}” {'stopped' if tab.turn_cancelled else 'finished'} "
                    f"· tab {tabs.index_of(tab) + 1}", timeout=4)

    def tab_action(self, kind: str, tab_id: int = 0) -> None:
        """Clicks on the tab strip (and the web's)."""
        if kind == "new":
            self._new_tab()
        elif kind == "tab":
            self._switch_tab(tabs.get(tab_id))
        elif kind == "close":
            self._close_tab(tabs.get(tab_id))

    # ── keys ─────────────────────────────────────────────────────────
    def action_new_tab(self) -> None:
        self._new_tab()

    def action_close_tab(self) -> None:
        self._close_tab(tabs.active())

    def action_next_tab(self) -> None:
        self._switch_tab(tabs.move(tabs.active(), 1))

    def action_prev_tab(self) -> None:
        self._switch_tab(tabs.move(tabs.active(), -1))

    def action_goto_tab(self, n: int) -> None:
        rows = tabs.all_tabs()
        if 1 <= n <= len(rows):
            self._switch_tab(rows[n - 1])

    # ── /tab ─────────────────────────────────────────────────────────
    def _try_tab_command(self, text: str) -> bool:
        """``/tab`` · ``/tab new`` · ``/tab close [n]`` · ``/tab <n>`` · ``/tab next|prev``."""
        head, _, rest = text.strip().partition(" ")
        if head.lower() not in ("/tab", "/tabs"):
            return False
        arg = rest.strip().lower()
        rows = tabs.all_tabs()
        if arg in ("new", "+", "add", "open"):
            self._new_tab()
        elif arg in ("next", "n", ">"):
            self.action_next_tab()
        elif arg in ("prev", "previous", "p", "<"):
            self.action_prev_tab()
        elif arg.startswith(("close", "x")):
            num = arg.partition(" ")[2].strip()
            if num.isdigit() and 1 <= int(num) <= len(rows):
                self._close_tab(rows[int(num) - 1])
            else:
                self._close_tab(tabs.active())
        elif arg.isdigit():
            if 1 <= int(arg) <= len(rows):
                self._switch_tab(rows[int(arg) - 1])
            else:
                self._set_status(f"no tab {arg} — {len(rows)} open")
        else:
            self._print_tabs()
        return True

    def _print_tabs(self) -> None:
        con = self._tui_console
        con.print(f"[{ui.ACCENT}]▤[/] [{ui.FG_MUTE}]{tabs.count()} chat tab(s)[/]")
        now = time.time()
        for i, tab in enumerate(tabs.all_tabs(), 1):
            mark = "▸" if tab.is_active() else " "
            bits = []
            if tab.busy:
                bits.append(f"working {int(now - (tab.busy_since or now))}s")
            if tab.unread:
                bits.append("new reply")
            if tab.session_id:
                bits.append(f"session #{tab.session_id}")
            meta = f" [{ui.FG_DIM}]· {' · '.join(bits)}[/]" if bits else ""
            con.print(f"  [{ui.ACCENT}]{mark}[/] {i}  {_rich_escape(tab.title())}{meta}")
        con.print(f"[{ui.FG_DIM}]  + in the tab strip or /tab new · /tab <n> · /tab close · "
                  f"alt+t new · alt+w close · ctrl+pgup/pgdn switch[/]")

    # ── web ──────────────────────────────────────────────────────────
    def _handle_web_tabs(self, data: dict) -> dict:
        """``POST /api/tabs {op: new|switch|close, id}`` on the UI thread."""
        op = str(data.get("op") or "")
        try:
            tab_id = int(data.get("id") or 0)
        except (TypeError, ValueError):
            tab_id = 0
        if op == "new":
            tab = self._new_tab(quiet=True)
            if tab is None:
                return {"ok": False, "error": f"{tabs.MAX_TABS} tabs are open — close one first"}
        elif op == "switch":
            if not self._switch_tab(tabs.get(tab_id)):
                return {"ok": False, "error": "that tab was closed"}
        elif op == "close":
            tab = tabs.get(tab_id)
            if tab is None:
                return {"ok": False, "error": "that tab was closed"}
            self._close_tab(tab)
        else:
            return {"ok": False, "error": f"unknown op {op!r}"}
        return {"ok": True, "tabs": tabs.public()}
