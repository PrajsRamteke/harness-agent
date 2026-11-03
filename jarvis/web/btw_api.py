"""Web API for ``/btw`` — the side-question window (``static/js/btw.js``).

The core is ``jarvis/btw.py``. Everything here runs on the HTTP handler thread:
a question is answered by a worker thread of its own, so the terminal's main
thread, the running turn and the prompt queue are never involved. Answers
stream to every open page as ``btw`` events::

    {"id": thread, "op": "start" | "delta" | "done" | "error" | "stopped" |
                         "closed" | "reopened", "turn": n, "text"?: …}

Routes (see ``handler.py``)::

    GET  /api/btw?tab=&session=       this chat's open side threads + its history (open and
                                      closed, ``open`` flagged). A side chat belongs to the
                                      session it was asked in; other sessions' stay hidden.
    POST /api/btw {op: "ask",    text, id?, tab?}   ask (a new thread unless ``id``)
    POST /api/btw {op: "stop",   id}                stop waiting for the running answer
    POST /api/btw {op: "close",  id}                close the window — the chat is kept
    POST /api/btw {op: "reopen", id}                open a closed chat again
    POST /api/btw {op: "forget", id}                delete a chat for good

Closing never loses a side chat: it moves to the history (on disk), where the
window's clock button lists it and a click brings it back.
"""
from __future__ import annotations

from typing import Any

from .. import btw
from .bridge import WebBridge


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def public(tab_id: int | None = None, session_id: int | None = None) -> dict[str, Any]:
    """The side chats of **one chat** — the session the page shows (``tab`` / ``session``),
    else the one on screen in the terminal. Other sessions' side chats stay out of view."""
    tab = btw.scoped_tab(tab_id, session_id)
    return {
        "ok": True,
        "tab": tab.id,
        "session": tab.session_id,
        "threads": [t.public() for t in btw.open_chats(tab)],
        "history": [t.public() for t in btw.all_chats(tab=tab)],
        "limit": btw.ARCHIVE_LIMIT,
    }


def run(data: dict[str, Any], bridge: WebBridge) -> dict[str, Any]:
    op = str(data.get("op") or "ask").strip()
    thread_id = str(data.get("id") or "").strip()

    def push(event: btw.Event) -> None:
        payload = event.public()
        thread = btw.get(event.thread)
        if thread is not None:        # which chat it is for: a page shows only its own
            payload["tab"], payload["session"] = thread.tab_id, thread.session_id
        bridge.emit("btw", payload)

    if op == "ask":
        try:
            thread = btw.ask(
                str(data.get("text") or ""),
                thread_id=thread_id,
                tab_id=_int(data.get("tab")),
                on_event=push,
            )
        except btw.BtwError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "id": thread.id, "thread": thread.public()}

    if op == "stop":
        ok = btw.stop(thread_id)
        if ok:
            bridge.emit("btw", {"id": thread_id, "op": "stopped"})
        return {"ok": ok}

    if op == "close":
        if not btw.close(thread_id):
            return {"ok": False, "error": "that side chat was already closed"}
        kept = btw.archived(thread_id)
        bridge.emit("btw", {"id": thread_id, "op": "closed",
                            "tab": kept.tab_id if kept else None,
                            "session": kept.session_id if kept else None,
                            "saved": kept.public() if kept else None})
        return {"ok": True, "saved": kept.public() if kept else None}

    if op == "reopen":
        try:
            thread = btw.reopen(thread_id)
        except btw.BtwError as exc:
            return {"ok": False, "error": str(exc)}
        bridge.emit("btw", {"id": thread.id, "op": "reopened", "tab": thread.tab_id,
                            "session": thread.session_id, "thread": thread.public()})
        return {"ok": True, "id": thread.id, "thread": thread.public()}

    if op == "forget":
        return {"ok": btw.forget(thread_id)}

    return {"ok": False, "error": f"unknown op: {op}"}
