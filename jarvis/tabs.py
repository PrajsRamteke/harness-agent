"""Chat tabs: several sessions open at once in one Jarvis (UI-free).

Each ``Tab`` owns what belongs to one conversation — the messages, the
session id, token counters, the prompt queue, plan mode … (``SESSION_FIELDS``).
``jarvis.state`` serves those names from the tab of the *current thread*:

* a turn's worker is bound to the tab it started in (``bind_thread``), and so
  are the threads it starts — parallel tool calls (``carry``) and subagents
  (``state.link_thread``) — so a reply keeps landing in its own chat while
  the user reads or types in another tab;
* every other thread (the UI, the web server, the legacy REPL) sees the
  *active* tab, the one on screen.

``bound(tab)`` binds the calling thread for a block — the UI uses it to act
on a tab that isn't shown (a reply finishing in the background, closing a
busy tab). With one tab nothing changes: everything reads the default tab.

The UI hangs its own per-tab objects on a tab (``transcript``, ``console``,
``mux``) and keeps the turn bookkeeping there (``busy``, ``turn_id`` …).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from .constants import TOOL_UI_HISTORY_SIZE

MAX_TABS = 12


def _session_defaults() -> dict[str, Any]:
    return {
        "messages": [],
        "current_session_id": None,
        "backups": [],
        "total_in": 0,
        "total_out": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "plan_mode": False,
        "session_start": time.time(),
        "tool_calls_count": 0,
        "last_assistant_text": "",
        "last_api_error": None,
        "web_tool_used_this_turn": False,
        "prompt_queue": [],
        "tool_output_history": deque(maxlen=TOOL_UI_HISTORY_SIZE),
    }


# Names ``jarvis.state`` serves from the current tab.
SESSION_FIELDS: tuple[str, ...] = tuple(_session_defaults())


class Tab:
    def __init__(self, tab_id: int) -> None:
        self.id = tab_id
        self.session: dict[str, Any] = _session_defaults()
        self.created = time.time()
        # Turn bookkeeping (the TUI reads and writes these through the app).
        self.busy = False
        self.turn_id = 0
        self.turn_threads: dict[int, int] = {}
        self.turn_cancelled = False
        self.turn_is_llm = False
        self.reply_before_turn: str | None = None
        self.turn_t0 = 0.0
        self.busy_since = 0.0
        self.activity_label = ""
        self.activity_t0 = 0.0
        self.last_footer_source: tuple | None = None
        # Badges: a reply finished while the tab wasn't shown / it asks the user something.
        self.unread = False
        self.needs_input = False
        self.draft = ""  # unsent text in the composer while another tab is shown
        # UI objects (TUI: Transcript, TUIConsole, WebMuxConsole).
        self.transcript: Any = None
        self.console: Any = None
        self.mux: Any = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Tab {self.id} session={self.session.get('current_session_id')} busy={self.busy}>"

    @property
    def session_id(self) -> int | None:
        return self.session.get("current_session_id")

    def is_active(self) -> bool:
        return _active is self

    def title(self) -> str:
        """What the tab strip shows: the session's title, else its first prompt, else "New chat"."""
        sid = self.session_id
        if sid:
            try:
                from .storage.sessions import db_get_title

                t = (db_get_title(sid) or "").strip()
                if t:
                    return t
            except Exception:
                pass
        for msg in self.session.get("messages") or []:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return " ".join(content.split())[:80]
            break
        return "New chat"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title(),
            "session_id": self.session_id,
            "active": self.is_active(),
            "busy": bool(self.busy),
            "busy_since": self.busy_since or None,
            "unread": bool(self.unread),
            "needs_input": bool(self.needs_input),
            "messages": len(self.session.get("messages") or []),
        }


_lock = threading.RLock()
_tabs: list[Tab] = []
_next_id = 1
_active: Tab | None = None
# thread ident → stack of tabs it works for (``bound`` nests).
_bindings: dict[int, list[Tab]] = {}
_listeners: list[Callable[[], None]] = []


def _new_tab() -> Tab:
    global _next_id
    tab = Tab(_next_id)
    _next_id += 1
    return tab


def _ensure_default() -> None:
    global _active
    if not _tabs:
        tab = _new_tab()
        _tabs.append(tab)
        _active = tab


_ensure_default()


# ─── lookups ─────────────────────────────────────────────────────────────


def all_tabs() -> list[Tab]:
    with _lock:
        return list(_tabs)


def active() -> Tab:
    tab = _active
    if tab is None:  # only during a reset in tests
        with _lock:
            _ensure_default()
            return _active  # type: ignore[return-value]
    return tab


def current() -> Tab:
    """The tab the calling thread works for (bound), else the active one."""
    stack = _bindings.get(threading.get_ident())
    if stack:
        return stack[-1]
    return active()


def get(tab_id: int) -> Tab | None:
    with _lock:
        for tab in _tabs:
            if tab.id == tab_id:
                return tab
    return None


def index_of(tab: Tab) -> int:
    with _lock:
        try:
            return _tabs.index(tab)
        except ValueError:
            return -1


def find_session(session_id: int | None) -> Tab | None:
    """The tab that has session ``session_id`` open (a session is never open twice)."""
    if not session_id:
        return None
    with _lock:
        for tab in _tabs:
            if tab.session_id == session_id:
                return tab
    return None


def count() -> int:
    with _lock:
        return len(_tabs)


def public() -> list[dict[str, Any]]:
    return [t.public() for t in all_tabs()]


# ─── changes ─────────────────────────────────────────────────────────────


def add_listener(fn: Callable[[], None]) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn: Callable[[], None]) -> None:
    try:
        _listeners.remove(fn)
    except ValueError:
        pass


def notify() -> None:
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


def create(*, activate: bool = True, after: Tab | None = None) -> Tab:
    """A new, empty tab — right after ``after`` (default: at the end)."""
    global _active
    with _lock:
        if len(_tabs) >= MAX_TABS:
            raise ValueError(f"{MAX_TABS} tabs are open — close one first")
        tab = _new_tab()
        pos = index_of(after) + 1 if after is not None and after in _tabs else len(_tabs)
        _tabs.insert(pos, tab)
        if activate:
            _active = tab
            tab.unread = False
    notify()
    return tab


def activate(tab: Tab) -> bool:
    """Show ``tab``. False when it isn't open (closed meanwhile)."""
    global _active
    with _lock:
        if tab not in _tabs:
            return False
        _active = tab
        tab.unread = False
    notify()
    return True


def close(tab: Tab) -> Tab | None:
    """Drop ``tab``; returns the tab to show next (the neighbour, like a browser).

    The last tab is never closed — the caller starts a fresh chat in it instead.
    """
    global _active
    with _lock:
        if tab not in _tabs or len(_tabs) <= 1:
            return None
        i = _tabs.index(tab)
        _tabs.remove(tab)
        if _active is tab:
            _active = _tabs[min(i, len(_tabs) - 1)]
            _active.unread = False
        nxt = _active
    notify()
    return nxt


def move(tab: Tab, step: int) -> Tab:
    """The tab ``step`` places from ``tab``, wrapping around."""
    with _lock:
        if tab not in _tabs:
            return active()
        return _tabs[(_tabs.index(tab) + step) % len(_tabs)]


def adopt() -> Tab:
    """A UI starts (once per process; once per app in tests): one tab, holding
    the conversation that was current, with no UI objects or turn marks from
    an earlier UI."""
    global _active
    with _lock:
        session = active().session
        tab = _new_tab()
        tab.session = session
        _tabs[:] = [tab]
        _bindings.clear()
        _active = tab
    notify()
    return tab


def reset() -> None:
    """Back to one empty tab (tests)."""
    global _active, _next_id
    with _lock:
        _tabs.clear()
        _bindings.clear()
        _next_id = 1
        _active = None
        _ensure_default()


# ─── thread binding ──────────────────────────────────────────────────────


def bind_thread(ident: int | None, tab: Tab | None) -> None:
    if not ident or tab is None:
        return
    with _lock:
        _bindings.setdefault(ident, []).append(tab)


def unbind_thread(ident: int | None) -> None:
    """Drop the innermost binding of ``ident`` (all of a finished thread's, when it was bound once)."""
    if not ident:
        return
    with _lock:
        stack = _bindings.get(ident)
        if stack:
            stack.pop()
            if not stack:
                _bindings.pop(ident, None)


def tab_of_thread(ident: int | None) -> Tab | None:
    stack = _bindings.get(ident or 0)
    return stack[-1] if stack else None


@contextmanager
def bound(tab: Tab | None) -> Iterator[Tab]:
    """Work for ``tab`` on this thread for the duration of the block."""
    if tab is None:
        yield current()
        return
    me = threading.get_ident()
    bind_thread(me, tab)
    try:
        yield tab
    finally:
        unbind_thread(me)


def carry(fn: Callable) -> Callable:
    """Wrap ``fn`` so a pool thread runs it for the caller's tab — and stops
    with the caller's turn (``state.link_thread``)."""
    tab = current()
    parent = threading.get_ident()

    def run(*args, **kwargs):
        from . import state

        me = threading.get_ident()
        bind_thread(me, tab)
        state.link_thread(me, parent)
        try:
            return fn(*args, **kwargs)
        finally:
            state.unlink_thread(me)
            unbind_thread(me)

    return run
