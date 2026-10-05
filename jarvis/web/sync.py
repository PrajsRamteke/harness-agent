"""Keep web clients in sync with changes made anywhere else.

Most session state is changed by the TUI directly (``/model`` in the
terminal, Tab to cycle agents, ``/new``, token counters ticking up…), and none
of that goes through the web bridge. ``StateWatcher`` polls the cheap
``state_fields`` once a second while a browser is connected and broadcasts
what changed:

* ``state`` — the new fields (no transcript), for small changes;
* ``snapshot`` — the whole transcript when the conversation itself was
  swapped out (another session, ``/new`` / ``/reset``, trace toggled);
* ``projects`` — the running Jarvis list (``hub.project_rows``) changed: one
  opened or closed, got busy, finished, or is waiting for an approval.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .bridge import WebBridge
from .state_api import snapshot_from_state, state_fields

# Changing any of these means the rendered transcript is stale.
_REPLAY_KEYS = ("session_id", "show_internal")


class StateWatcher:
    def __init__(
        self,
        bridge: WebBridge,
        busy: Callable[[], bool],
        *,
        interval: float = 1.0,
        projects: Callable[[], Any] | None = None,
    ) -> None:
        self._bridge = bridge
        self._busy = busy
        self._projects = projects
        self._last_projects: Any = None
        self._interval = interval
        self._stop = threading.Event()
        self._last: dict[str, Any] | None = None
        self._title_key: tuple[Any, int] | None = None
        self._title: str = ""
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-web-sync")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.tick()
            except Exception:
                # Never let a transient read (state mid-mutation) kill syncing.
                pass

    def _current(self) -> dict[str, Any]:
        from .. import state

        # The title only changes when messages arrive or the session changes;
        # skip the sqlite read otherwise.
        key = (state.current_session_id, len(state.messages))
        title = self._title if key == self._title_key else None
        fields = state_fields(busy=bool(self._busy()), session_title=title)
        self._title_key = key
        self._title = fields["session_title"]
        return fields

    def _tick_projects(self) -> None:
        if self._projects is None:
            return
        try:
            rows = self._projects()
        except Exception:
            return
        # The page fetched the list when it loaded: only changes are news.
        if self._last_projects is not None and rows != self._last_projects:
            self._bridge.emit("projects", {"projects": rows})
        self._last_projects = rows

    def tick(self) -> str | None:
        """One poll. Returns the event type broadcast (for tests), if any."""
        if not self._bridge.has_subscribers():
            # Clients get a full snapshot when they connect; start fresh then.
            self._last = None
            self._last_projects = None
            return None
        self._tick_projects()
        cur = self._current()
        last = self._last
        self._last = cur
        if last is None or cur == last:
            return None
        replay = any(cur[k] != last[k] for k in _REPLAY_KEYS) or (
            cur["message_count"] < last["message_count"]
        )
        if replay:
            self._bridge.emit("snapshot", snapshot_from_state(busy=cur["busy"]))
            return "snapshot"
        self._bridge.emit("state", cur)
        return "state"
