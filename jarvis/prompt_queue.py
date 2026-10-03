"""Messages typed while Jarvis works — the queue above the composer (TUI + web).

Each entry of ``state.prompt_queue`` is a :class:`QueuedPrompt`: still the
tuple ``(text, attachment registry snapshot[, web upload ids])`` the turn loop
always read, plus

* ``id``     — stable, so a click in the terminal or the web page acts on the
  right message however the list shifts;
* ``steer``  — "send now": the running turn hands it to the model at its next
  step (between tool calls, ``take_steered``) instead of after the reply. If
  the reply ends first it simply runs next — it already sits at the front;
* ``held``   — being edited in the terminal's message box: skipped by
  ``pop_next`` / ``take_steered`` and shown read-only on the web.

A steered message joins the conversation inside the tool-result message as a
text block that starts with ``STEER_HEAD``; transcripts strip that line again
(``strip_steer``) and mark the message "sent while Jarvis worked".

Mutations come from the TUI main thread (clicks, web actions) and from the
turn's worker thread (``take_steered``), hence the lock.
"""
from __future__ import annotations

import itertools
import threading
from typing import Any

from . import state

STEER_HEAD = "[Sent by the user while you were working — read it now and adjust course:]"

_lock = threading.RLock()
_ids = itertools.count(1)


class QueuedPrompt(tuple):
    """``(text, registry[, files])`` with ``id`` / ``steer`` / ``held`` attributes."""

    id: str
    steer: bool
    held: bool

    def __new__(cls, text: str, registry: Any = None, files: list[str] | None = None, *,
                qid: str = "", steer: bool = False, held: bool = False) -> "QueuedPrompt":
        parts = (text, registry, list(files)) if files else (text, registry)
        obj = super().__new__(cls, parts)
        obj.id = qid or f"q{next(_ids)}"
        obj.steer = steer
        obj.held = held
        return obj

    @property
    def text(self) -> str:
        return str(self[0] or "")

    @property
    def files(self) -> list[str]:
        return list(self[2]) if len(self) > 2 and isinstance(self[2], list) else []

    @property
    def is_command(self) -> bool:
        return self.text.lstrip().startswith(("/", "!"))

    @property
    def steerable(self) -> bool:
        """Can join the running conversation (commands only run as a turn of their own)."""
        return not self.is_command and bool(self.text.strip() or self.files)

    def with_text(self, text: str, registry: Any = None) -> "QueuedPrompt":
        return QueuedPrompt(text, self[1] if registry is None else registry,
                            self.files or None, qid=self.id,
                            steer=self.steer and not text.lstrip().startswith(("/", "!")))


def _wrap(item: Any) -> QueuedPrompt:
    """Old-style entries (a bare string or tuple — tests, the legacy REPL) gain an id."""
    if isinstance(item, QueuedPrompt):
        return item
    if isinstance(item, tuple):
        text = str(item[0] or "") if item else ""
        registry = item[1] if len(item) > 1 else None
        files = item[2] if len(item) > 2 and isinstance(item[2], list) else None
        return QueuedPrompt(text, registry, files)
    return QueuedPrompt(str(item or ""), None)


def items() -> list[QueuedPrompt]:
    """The queue, in order (wrapping any old-style entries in place)."""
    with _lock:
        q = state.prompt_queue
        for i, item in enumerate(q):
            if not isinstance(item, QueuedPrompt):
                q[i] = _wrap(item)
        return list(q)


def _index(qid: str) -> int:
    for i, item in enumerate(items()):
        if item.id == qid:
            return i
    return -1


def find(qid: str) -> QueuedPrompt | None:
    i = _index(qid)
    return state.prompt_queue[i] if i >= 0 else None


def add(text: str, registry: Any = None, files: list[str] | None = None) -> QueuedPrompt:
    item = QueuedPrompt(text, registry, files)
    with _lock:
        state.prompt_queue.append(item)
    return item


def pending() -> int:
    """Messages that will run on their own (not one being edited in the terminal)."""
    return sum(1 for item in items() if not item.held)


def pop_next() -> QueuedPrompt | None:
    """The next message to run once the turn is over (steered ones first)."""
    with _lock:
        for i, item in enumerate(items()):
            if not item.held:
                return state.prompt_queue.pop(i)
    return None


def remove(qid: str) -> QueuedPrompt | None:
    with _lock:
        i = _index(qid)
        return state.prompt_queue.pop(i) if i >= 0 else None


def clear() -> int:
    """Drop every message (one being edited stays — the box still holds it)."""
    with _lock:
        keep = [item for item in items() if item.held]
        dropped = len(state.prompt_queue) - len(keep)
        state.prompt_queue[:] = keep
        return dropped


def edit(qid: str, text: str, *, registry: Any = None) -> QueuedPrompt | None:
    """New text for a message; empty text (and no files) removes it. ``None`` = not found.

    ``registry``: the composer's attachment snapshot after a terminal edit.
    """
    with _lock:
        i = _index(qid)
        if i < 0:
            return None
        item = state.prompt_queue[i]
        text = (text or "").strip()
        if not text and not item.files:
            state.prompt_queue.pop(i)
            return item
        new = item.with_text(text, registry)
        new.held = False
        state.prompt_queue[i] = new
        return new


def set_held(qid: str, held: bool) -> QueuedPrompt | None:
    with _lock:
        item = find(qid)
        if item is not None:
            item.held = held
        return item


def steer(qid: str, on: bool = True) -> QueuedPrompt | None:
    """Mark "send now" (moves it to the front, after other steered ones) or undo it."""
    with _lock:
        i = _index(qid)
        if i < 0:
            return None
        item = state.prompt_queue[i]
        if on and not item.steerable:
            return item
        item.steer = bool(on)
        if on:
            state.prompt_queue.pop(i)
            at = 0
            while at < len(state.prompt_queue) and state.prompt_queue[at].steer:
                at += 1
            state.prompt_queue.insert(at, item)
        return item


def take_steered() -> list[QueuedPrompt]:
    """Remove and return the "send now" messages the running turn can take."""
    with _lock:
        taken = [item for item in items() if item.steer and item.steerable and not item.held]
        if taken:
            ids = {item.id for item in taken}
            state.prompt_queue[:] = [item for item in state.prompt_queue if item.id not in ids]
        return taken


def has_steered() -> bool:
    return any(item.steer and not item.held for item in items())


# ─── the steered message inside the conversation ─────────────────────────

def steer_text(text: str) -> str:
    """Text block a steered message joins the conversation as."""
    body = (text or "").strip()
    return f"{STEER_HEAD}\n{body}" if body else STEER_HEAD


def strip_steer(text: str) -> tuple[str, bool]:
    """``(visible text, was it steered)`` for a user message's text."""
    s = text or ""
    at = s.find(STEER_HEAD)
    if at < 0:
        return s, False
    return (s[:at] + s[at + len(STEER_HEAD):]).strip(), True


def steer_blocks(item: QueuedPrompt, text: str | None = None) -> list[dict[str, Any]]:
    """Content blocks for a steered message (``text`` = already expanded)."""
    body = item.text if text is None else text
    if item.files:
        from . import media

        metas = [m for m in (media.get(i) for i in item.files) if m]
        if metas:
            content = media.user_content(body, metas)
            if isinstance(content, str):
                return [{"type": "text", "text": steer_text(content)}]
            out = []
            for block in content:
                if block.get("type") == "text":
                    block = {**block, "text": steer_text(str(block.get("text") or ""))}
                out.append(block)
            return out
    return [{"type": "text", "text": steer_text(body)}]


def attach_to_last(blocks: list[dict[str, Any]]) -> bool:
    """Add ``blocks`` to the newest message — the tool results the model reads next.

    Returns False when that isn't a user message (nothing was added).
    """
    if not blocks or not state.messages:
        return False
    last = state.messages[-1]
    if last.get("role") != "user":
        return False
    content = last.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}] if content else []
    last["content"] = [*(content or []), *blocks]
    return True


# ─── the web page's view ─────────────────────────────────────────────────

def label(item: Any) -> str:
    from .media import queue_label

    return queue_label(item)


def public() -> list[dict[str, Any]]:
    """Rows for the web queue panel (``queue`` event / snapshot ``queue_items``)."""
    from . import media

    out: list[dict[str, Any]] = []
    for item in items():
        files = [media.public(m) for m in (media.get(i) for i in item.files) if m]
        out.append({
            "id": item.id,
            "text": item.text,
            "label": label(item),
            "files": files,
            "steer": bool(item.steer),
            "steerable": item.steerable,
            "command": item.is_command,
            "editing": bool(item.held),
        })
    return out


def labels() -> list[str]:
    return [t for t in (label(item) for item in items()) if t]


def apply_op(data: dict[str, Any]) -> dict[str, Any]:
    """A queue change from the web page: ``{op, id?, text?}``.

    ``op``: ``edit`` · ``remove`` · ``steer`` (send now) · ``unsteer`` · ``clear``.
    The caller refreshes the terminal's queue bar and tells every page.
    """
    op = str(data.get("op") or "").strip()
    qid = str(data.get("id") or "").strip()
    if op == "clear":
        return {"ok": True, "dropped": clear()}
    if not qid:
        return {"ok": False, "error": "missing id"}
    item = find(qid)
    if item is None:
        return {"ok": False, "code": "gone", "error": "That message already went to Jarvis."}
    if item.held and op != "remove":
        return {"ok": False, "code": "editing", "error": "It’s being edited in the terminal."}
    if op == "remove":
        remove(qid)
        return {"ok": True}
    if op == "edit":
        text = str(data.get("text") or "")
        if not text.strip() and not item.files:
            remove(qid)
            return {"ok": True, "removed": True}
        new = edit(qid, text)
        return {"ok": True, "steer": bool(new and new.steer)}
    if op == "steer":
        if not item.steerable:
            return {"ok": False, "code": "command", "error": "Commands run on their own once Jarvis is free."}
        steer(qid, True)
        return {"ok": True}
    if op == "unsteer":
        steer(qid, False)
        return {"ok": True}
    return {"ok": False, "error": f"unknown op: {op}"}
