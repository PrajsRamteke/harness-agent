"""``/btw`` — a side question that never disturbs the running turn.

While Jarvis works on a long task the user can type ``/btw how far along are
you, and how long will it take?``. The answer comes back in its own small
window, where the conversation can go on (follow-ups), and the main chat keeps
running exactly as before.

How it stays out of the way
---------------------------
* **Nothing joins the chat.** No message is added to ``state.messages``, the
  session database, the prompt queue or the transcript. Side threads live apart
  from the chat (:data:`_threads`), so a question can never bend the turn.
* **Nothing is lost.** Closing a side window archives it (:data:`_archive`) and
  writes every side chat to :data:`CHATS_FILE`, so a closed or abandoned
  conversation can be opened again — also after Jarvis restarts. Only an
explicit `forget` erases one.
* **No tools.** The model only reads: it can't edit files, run commands or
  steer the main task, so a question can never change what the turn is doing.
* **Its own request, on its own thread.** The reply streams from a worker
  thread; the turn's worker, its stream and its queue are never touched.
* **The chat's own model.** The snapshot holds the conversation, so it goes to
  the model the chat already talks to — never to the (possibly different)
  "commit text" provider.

What the model sees on every question is a *fresh* snapshot of the chat
(recent messages, tool calls and results, clipped) plus the live status the
turn has right now (how long it has been working, the step it is on, queued
messages, background jobs, parallel agents, changed files). That is what lets
it say "I'm reading the tests, 6 of ~10 files done, a few minutes more".

UI-free: the web page (``POST /api/btw`` → ``btw`` events) and the terminal
(``tui/btw_modal.py``) both drive :func:`ask` and get :class:`Event` callbacks.
"""
from __future__ import annotations

import json
import pathlib
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_THREADS = 8               # side windows open at once (the oldest is archived)
ARCHIVE_LIMIT = 60            # closed side chats kept on disk
MAX_ARCHIVE_TITLE = 90        # chars of the first question used as a title
DETACHED_TAB = -1             # tab id of a side chat loaded from disk with no session
MAX_QUESTION = 4000           # chars of one question
MAX_PAIRS = 24                # question/answer pairs a side thread remembers
MAX_CONTEXT_CHARS = 30_000    # chars of chat history in one snapshot
TEXT_CHARS = 2_500            # clip for one older message's text
LAST_TEXT_CHARS = 6_000       # clip for the newest assistant message
TOOL_RESULT_CHARS = 300       # clip for one tool result
TIMEOUT_SECS = 180.0          # one answer may take this long
RETRIES = 2                   # re-asks when the model reaches for a tool

_SYSTEM = """You are the "/btw" side channel of Jarvis, an AI coding assistant. The user is talking to a main assistant that is working on a task right now; they opened this small window to ask you something on the side, without interrupting it.

You get a snapshot of the main conversation and the live status of the main task. Use them to answer the user's question.

- You are read-only. You cannot run tools, edit files or change what the main task does. If the user asks you to do something, say it must go to the main chat.
- Progress questions: say what has been done, what is happening now, and what is left, from the snapshot. Never invent progress the snapshot does not show.
- Time questions: give a rough range, not a promise, and say what it rests on (time spent so far, how many steps are done, what remains). If the snapshot is too thin to estimate, say so.
- Answer only what was asked, briefly and plainly. No preamble, no sign-off. Markdown is fine, and short lists or code spans where they help.
- The snapshot is a copy taken just now; the main task keeps moving, so say "as of now" when it matters."""

_ANSWER_TOOL = {
    "name": "reply",
    "description": "Give your answer to the user. Call it once, with the full answer text.",
    "input_schema": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The answer"}},
        "required": ["text"],
    },
}
_NO_TOOLS = "Tools are disabled here. Answer the user in plain text, from the snapshot."
_ONLY_REPLY_TOOL = "That tool is not available. Call reply with your answer."

_CMD_RE = re.compile(r"^/btw(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)


class BtwError(Exception):
    """The question couldn't be answered; the message is shown to the user."""


def parse(text: str) -> str | None:
    """The question in ``/btw <question>``; ``""`` for a bare ``/btw``; ``None``
    when ``text`` isn't a /btw command."""
    m = _CMD_RE.match((text or "").strip())
    if m is None:
        return None
    return (m.group(1) or "").strip()


def is_btw(text: str) -> bool:
    return parse(text) is not None


# ─── events ──────────────────────────────────────────────────────────────

@dataclass
class Event:
    """One step of an answer. ``op``: ``start`` · ``delta`` · ``done`` · ``error`` · ``stopped``."""

    thread: str
    op: str
    turn: int = 0                   # which question of the thread (0-based)
    text: str = ""                  # delta: the new text · done: the whole answer · error: why
    question: str = ""              # start: what was asked
    extra: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        out = {"id": self.thread, "op": self.op, "turn": self.turn}
        if self.text:
            out["text"] = self.text
        if self.question:
            out["question"] = self.question
        out.update(self.extra)
        return out


EventFn = Callable[[Event], None]


# ─── snapshot of the chat ────────────────────────────────────────────────

def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    return f"{text[:head].rstrip()}\n… ({len(text) - limit:,} chars left out) …\n{text[-tail:].lstrip()}"


def _block(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        try:
            return raw.model_dump()
        except Exception:  # noqa: BLE001
            pass
    return {k: getattr(raw, k) for k in ("type", "text", "name", "input", "content", "is_error")
            if hasattr(raw, k)}


def _result_text(block: dict[str, Any]) -> str:
    body = block.get("content", "")
    if isinstance(body, list):
        parts = []
        for item in body:
            item = _block(item)
            parts.append(str(item.get("text") or ""))
        return "\n".join(p for p in parts if p).strip()
    return str(body or "").strip()


def render_message(msg: dict[str, Any], *, text_limit: int = TEXT_CHARS) -> str:
    """One chat message as a few readable lines (tool calls and results shortened)."""
    role = msg.get("role")
    content = msg.get("content")
    who = "User" if role == "user" else "Assistant"
    if isinstance(content, str):
        text = _clip(content, text_limit)
        return f"{who}: {text}" if text else ""
    if not isinstance(content, list):
        return ""
    from .repl.tool_activity import describe_tool_activity

    lines: list[str] = []
    for raw in content:
        block = _block(raw)
        kind = block.get("type")
        if kind == "text":
            text = _clip(str(block.get("text") or ""), text_limit)
            if text:
                lines.append(f"{who}: {text}")
        elif kind == "tool_use":
            try:
                label = describe_tool_activity(str(block.get("name") or "tool"), block.get("input"))
            except Exception:  # noqa: BLE001 — a label is nice to have, never required
                label = str(block.get("name") or "tool")
            lines.append(f"  → {label}")
        elif kind == "tool_result":
            body = _clip(_result_text(block), TOOL_RESULT_CHARS)
            flag = " (failed)" if block.get("is_error") else ""
            lines.append(f"  ← result{flag}: {body}" if body else f"  ← result{flag}")
        elif kind == "image":
            lines.append(f"{who}: [image]")
    return "\n".join(lines)


def render_conversation(messages: list[dict[str, Any]], *, budget: int = MAX_CONTEXT_CHARS) -> str:
    """The newest messages that fit ``budget`` chars, oldest first."""
    picked: list[str] = []
    used = 0
    newest_assistant = True
    omitted = 0
    for msg in reversed(list(messages)):
        limit = TEXT_CHARS
        if newest_assistant and msg.get("role") == "assistant":
            limit = LAST_TEXT_CHARS  # what the main assistant said last matters most
            newest_assistant = False
        text = render_message(msg, text_limit=limit)
        if not text:
            continue
        if used + len(text) > budget and picked:
            omitted += 1
            continue
        picked.append(text)
        used += len(text)
    picked.reverse()
    head = "(earlier messages left out)\n\n" if omitted else ""
    return head + "\n\n".join(picked)


def _fmt_secs(secs: float) -> str:
    secs = max(0, int(secs))
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def live_status(tab: Any, *, now: float | None = None) -> str:
    """What the main task is doing right now — the part a transcript can't tell."""
    from . import tabs

    now = now or time.time()
    lines: list[str] = []
    if getattr(tab, "busy", False):
        since = float(getattr(tab, "busy_since", 0) or 0)
        spent = f" for {_fmt_secs(now - since)}" if since else ""
        lines.append(f"Main task: working{spent}.")
        label = (getattr(tab, "activity_label", "") or "").strip()
        if label:
            lines.append(f"Current step: {label}")
    else:
        lines.append("Main task: idle — Jarvis is not working on anything right now.")

    try:
        with tabs.bound(tab):
            from . import file_changes, prompt_queue

            queued = prompt_queue.labels()
            if queued:
                lines.append("Messages waiting in the queue: " + "; ".join(_clip(q, 120) for q in queued[:5]))
            changes = file_changes.summaries()
        totals = changes.get("totals") or {}
        if totals.get("files"):
            names = [str(f.get("path") or f.get("name") or "") for f in (changes.get("files") or [])[:8]]
            lines.append(
                f"Files changed so far: {totals['files']} (+{totals.get('added', 0)} −{totals.get('removed', 0)})"
                + (": " + ", ".join(n for n in names if n) if any(names) else "")
            )
    except Exception:  # noqa: BLE001 — status is best effort
        pass

    calls = int((getattr(tab, "session", {}) or {}).get("tool_calls_count") or 0)
    if calls:
        lines.append(f"Tool calls in this chat so far: {calls}")

    try:
        from .tools import background

        jobs = background.jobs()[-6:]
        if jobs:
            rows = []
            for job in jobs:
                state_ = "running" if job.running else ("killed" if job.killed else f"finished (exit {job.code})")
                rows.append(f"#{job.id} {' '.join(job.cmd.split())[:80]} — {state_}, {_fmt_secs(job.elapsed)}")
            lines.append("Background jobs: " + "; ".join(rows))
    except Exception:  # noqa: BLE001
        pass

    try:
        from .subagents import team

        for t in team.running_teams():
            snap = t.snapshot(reports=False)
            c = snap.get("counts") or {}
            lines.append(
                f"Parallel agents running ({_clip(str(snap.get('goal') or ''), 100)}): "
                f"{snap.get('total', 0)} agents, {_fmt_secs(snap.get('elapsed', 0))} so far, counts {c}"
            )
            for a in snap.get("agents", [])[:8]:
                lines.append(f"  - {a['name']}: {a['status']}, {a['steps']} steps — {_clip(str(a['activity']), 80)}")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def snapshot(tab: Any) -> str:
    """The chat as it is right now: live status, then the recent conversation."""
    messages = list((getattr(tab, "session", {}) or {}).get("messages") or [])
    convo = render_conversation(messages) or "(the conversation has no messages yet)"
    return (
        "<live_status>\n" + live_status(tab) + "\n</live_status>\n\n"
        "<conversation_snapshot>\n" + convo + "\n</conversation_snapshot>"
    )


# ─── side threads ────────────────────────────────────────────────────────

@dataclass
class Thread:
    id: str
    tab_id: int
    session_id: int | None = None   # the chat (session) it belongs to — set once the chat has one
    created: float = field(default_factory=time.time)
    pairs: list[tuple[str, str]] = field(default_factory=list)   # answered (question, answer)
    running: bool = False
    gen: int = 0            # bumps on every ask / stop, so a stale worker stays quiet
    pending: str = ""       # the question being answered
    partial: str = ""       # what has streamed so far
    title: str = ""         # first question — the name in the history list
    closed_at: float = 0.0  # 0 while the window is open
    updated: float = 0.0    # last question or answer — orders the history

    def active_at(self) -> float:
        return max(self.updated, self.closed_at, self.created)

    def turns(self) -> list[dict[str, str]]:
        """The conversation as the window shows it, including an unfinished answer."""
        out = [{"q": q, "a": a, "status": "done"} for q, a in self.pairs]
        if self.running:
            out.append({"q": self.pending, "a": self.partial,
                        "status": "live" if self.partial else "wait"})
        elif self.partial:                       # stopped mid-answer: keep what was written
            out.append({"q": self.pending, "a": self.partial, "status": "stopped"})
        return out

    def preview(self) -> str:
        answer = self.pairs[-1][1] if self.pairs else self.partial
        return _clip(" ".join(answer.split()), 160)

    def public(self) -> dict[str, Any]:
        preview = self.preview()
        return {
            "id": self.id,
            "tab": self.tab_id,
            "session": self.session_id,
            "created": self.created,
            "closed_at": self.closed_at,
            "updated": self.active_at(),
            "open": not self.closed_at,
            "title": self.title or (self.pairs[0][0] if self.pairs else self.pending),
            "count": len(self.pairs) + (1 if self.partial else 0),
            "preview": preview,
            "running": self.running,
            "pending": self.pending,
            "partial": self.partial,
            "turns": self.turns(),
            "pairs": [{"q": q, "a": a} for q, a in self.pairs],
        }

    def saved(self) -> dict[str, Any]:
        return {
            "id": self.id, "tab": self.tab_id, "session": self.session_id, "created": self.created,
            "closed_at": self.closed_at, "updated": self.updated, "title": self.title,
            "pairs": [{"q": q, "a": a} for q, a in self.pairs],
            "pending": self.pending, "partial": self.partial,
        }


_lock = threading.RLock()
_save_lock = threading.Lock()         # one writer of the history file at a time
_threads: dict[str, Thread] = {}      # open side windows
_archive: dict[str, Thread] = {}      # closed ones — kept, never dropped until forgotten
_loaded = False                       # the archive on disk is read once
CHATS_FILE: pathlib.Path | None = None  # where side chats live (tests point this at a temp dir)


def get(thread_id: str) -> Thread | None:
    """An *open* side thread (closed ones are in :func:`history`)."""
    with _lock:
        return _threads.get(thread_id)


def threads() -> list[Thread]:
    with _lock:
        return list(_threads.values())


def archived(thread_id: str) -> Thread | None:
    _ensure_loaded()
    with _lock:
        return _archive.get(thread_id)


def history(limit: int = ARCHIVE_LIMIT) -> list[Thread]:
    """Closed side chats, newest first."""
    _ensure_loaded()
    with _lock:
        items = sorted(_archive.values(), key=lambda t: (t.closed_at or t.created, t.created), reverse=True)
    return items[:limit]


def belongs(thread: Thread, tab_id: int | None, session_id: int | None) -> bool:
    """Is ``thread`` a side chat of the chat in tab ``tab_id`` / session ``session_id``?

    A side chat is linked to the chat it was asked in, never to the page: switch
    to another session and it is gone from view; switch back and it returns.
    One asked before its chat had a session joins it as soon as it gets one.
    """
    with _lock:
        if thread.session_id:
            return bool(session_id) and thread.session_id == session_id
        if tab_id is not None and thread.tab_id == tab_id:
            if session_id:
                thread.session_id = session_id
            return True
    return False


def _scope(tab: Any) -> tuple[int | None, int | None]:
    return (getattr(tab, "id", None), getattr(tab, "session_id", None) or None)


def scoped_tab(tab_id: int | None = None, session_id: int | None = None) -> Any:
    """The chat a request is about: its session's tab, else the tab, else the one on screen."""
    from . import tabs

    return ((tabs.find_session(session_id) if session_id else None)
            or (tabs.get(tab_id) if tab_id is not None else None)
            or tabs.active())


def all_chats(limit: int = ARCHIVE_LIMIT, *, tab: Any = None) -> list[Thread]:
    """Every side chat worth listing — the open windows *and* the closed ones —
    newest activity first. A chat you used and left open is part of the history too.

    With ``tab``, only the side chats of that chat (its session)."""
    _ensure_loaded()
    with _lock:
        rows = [t for t in _threads.values() if t.pairs or t.pending or t.partial]
        rows += list(_archive.values())
    if tab is not None:
        tab_id, session_id = _scope(tab)
        rows = [t for t in rows if belongs(t, tab_id, session_id)]
    rows.sort(key=lambda t: t.active_at(), reverse=True)
    return rows[:limit]


def open_chats(tab: Any = None) -> list[Thread]:
    """The open windows (all of them, or just those of ``tab``'s chat)."""
    rows = threads()
    if tab is None:
        return rows
    tab_id, session_id = _scope(tab)
    return [t for t in rows if belongs(t, tab_id, session_id)]


def new_thread(tab_id: int) -> Thread:
    with _lock:
        while len(_threads) >= MAX_THREADS:      # the oldest open one steps aside, archived
            _archive_thread(_threads.pop(next(iter(_threads))))
        thread = Thread(id=uuid.uuid4().hex[:12], tab_id=tab_id)
        _threads[thread.id] = thread
        return thread


def close(thread_id: str) -> bool:
    """Close a side window — the chat is **kept** in the history, partial answer included."""
    _ensure_loaded()
    with _lock:
        thread = _threads.pop(thread_id, None)
        if thread is None:
            if thread_id in _archive:
                return False                       # already closed (and already saved)
            return False
        thread.gen += 1
        thread.running = False
        _archive_thread(thread)
    save()
    return True


def reopen(thread_id: str) -> Thread:
    """Bring a closed side chat back as an open window (same id, same conversation)."""
    _ensure_loaded()
    with _lock:
        thread = _archive.pop(thread_id, None)
        if thread is None:
            if thread_id in _threads:
                raise BtwError("that side chat is already open")
            raise BtwError("that side chat is gone — it was deleted")
        while len(_threads) >= MAX_THREADS:
            _archive_thread(_threads.pop(next(iter(_threads))))
        thread.closed_at = 0.0
        thread.gen += 1
        thread.running = False
        _threads[thread.id] = thread
    save()
    return thread


def forget(thread_id: str) -> bool:
    """Erase a side chat for good — the only way one is ever thrown away."""
    _ensure_loaded()
    with _lock:
        gone = _archive.pop(thread_id, None) is not None
        live = _threads.pop(thread_id, None)
        if live is not None:
            live.gen += 1
            live.running = False
            gone = True
    if gone:
        save()
    return gone


def stop(thread_id: str) -> bool:
    """Stop waiting for the running answer (what streamed so far is kept)."""
    with _lock:
        thread = _threads.get(thread_id)
        if thread is None or not thread.running:
            return False
        thread.gen += 1
        thread.running = False
    return True


def reset() -> None:
    """Forget every side thread in memory (tests). Nothing is read from or written to disk."""
    global _loaded
    with _lock:
        for thread in list(_threads.values()) + list(_archive.values()):
            thread.gen += 1
            thread.running = False
        _threads.clear()
        _archive.clear()
        _loaded = True


# ─── the history on disk ─────────────────────────────────────────────────

def chats_file() -> pathlib.Path:
    global CHATS_FILE
    if CHATS_FILE is None:
        from .constants.paths import BTW_CHATS_FILE

        CHATS_FILE = BTW_CHATS_FILE
    return CHATS_FILE


def _archive_thread(thread: Thread) -> Thread:
    """Move a thread to the history (call with the lock held)."""
    thread.closed_at = thread.closed_at or time.time()
    if not thread.title:
        first = thread.pairs[0][0] if thread.pairs else thread.pending
        thread.title = _clip(" ".join(first.split()), MAX_ARCHIVE_TITLE) or "(empty side chat)"
    thread.gen += 1
    thread.running = False
    _archive.pop(thread.id, None)
    _archive[thread.id] = thread
    while len(_archive) > ARCHIVE_LIMIT:        # the oldest goes, never the newest
        oldest = min(_archive.values(), key=lambda t: (t.closed_at or t.created, t.created))
        _archive.pop(oldest.id, None)
    return thread


def _thread_from(data: Any) -> Thread | None:
    try:
        session = data.get("session")
        session_id = int(session) if session else None
        return Thread(
            id=str(data["id"]),
            # Tab ids don't outlive a run: a chat that never got a session is not
            # claimed by whichever tab happens to reuse its number.
            tab_id=int(data.get("tab") or 0) if session_id else DETACHED_TAB,
            session_id=session_id,
            created=float(data.get("created") or time.time()),
            pairs=[(str(p.get("q") or ""), str(p.get("a") or "")) for p in (data.get("pairs") or [])],
            title=str(data.get("title") or ""),
            closed_at=float(data.get("closed_at") or 0),
            updated=float(data.get("updated") or 0),
            pending=str(data.get("pending") or ""),
            partial=str(data.get("partial") or ""),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _ensure_loaded() -> None:
    """Read the saved side chats once — after a restart they are all history."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        raw = json.loads(chats_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    items = raw.get("chats") if isinstance(raw, dict) else None
    with _lock:
        for item in items or []:
            thread = _thread_from(item)
            if thread is None or not thread.id or thread.id in _archive:
                continue
            thread.closed_at = thread.closed_at or thread.created
            if not thread.title and (thread.pairs or thread.pending):
                first = thread.pairs[0][0] if thread.pairs else thread.pending
                thread.title = _clip(" ".join(first.split()), MAX_ARCHIVE_TITLE)
            _archive[thread.id] = thread
        while len(_archive) > ARCHIVE_LIMIT:
            oldest = min(_archive.values(), key=lambda t: (t.closed_at or t.created, t.created))
            _archive.pop(oldest.id, None)


def save() -> None:
    """Write every open and closed side chat to disk. Failure is never fatal."""
    _ensure_loaded()
    with _lock:
        rows = [t.saved() for t in _threads.values() if t.pairs or t.pending or t.partial]
        rows += [t.saved() for t in _archive.values()]
    path = chats_file()
    try:
        with _save_lock:            # an answer and a close can land at the same moment
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"version": 1, "chats": rows}, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    except OSError:
        pass


# ─── the model call ──────────────────────────────────────────────────────

def _target(tab: Any) -> tuple[Any, str, str, str]:
    """``(client, model, provider, auth_mode)`` — the chat's own, read off its tab."""
    from . import state

    config = getattr(tab, "config", {}) or {}
    client = config.get("client", state.client)
    model = config.get("MODEL") or state.MODEL
    provider = config.get("provider") or state.provider
    auth_mode = config.get("auth_mode", state.auth_mode)
    return client, model, provider, auth_mode


def _system_blocks(snap: str, auth_mode: Any, client: Any) -> Any:
    from .constants import AUTH_OAUTH, OAUTH_IDENTITY

    text = _SYSTEM
    if getattr(client, "gate_tools", None):
        text += "\n- Give your answer by calling reply once with the full text."
    else:
        text += "\n- Do not call tools. Reply with the answer as plain text."
    text += "\n\n" + snap
    if auth_mode == AUTH_OAUTH:
        # Anthropic OAuth requires this exact first block.
        return [{"type": "text", "text": OAUTH_IDENTITY}, {"type": "text", "text": text}]
    return text


def _messages(thread: Thread, question: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for q, a in thread.pairs[-MAX_PAIRS:]:
        out.append({"role": "user", "content": q})
        out.append({"role": "assistant", "content": a})
    out.append({"role": "user", "content": question})
    return out


def _request_kwargs(thread: Thread, question: str, tab: Any) -> tuple[Any, dict[str, Any]]:
    from .constants import PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN
    from .constants.models import API_MAX_TOKENS

    client, model, provider, auth_mode = _target(tab)
    if client is None:
        raise BtwError("no model connected — run /provider")
    kwargs: dict[str, Any] = dict(
        model=model,
        # Room for a reasoning model to think before it writes a short answer.
        max_tokens=min(API_MAX_TOKENS, 4096),
        system=_system_blocks(snapshot(tab), auth_mode, client),
        messages=_messages(thread, question),
    )
    if provider in (PROVIDER_OPENCODE, PROVIDER_OPENCODE_ZEN):
        kwargs["thinking"] = {"type": "disabled"}
    if getattr(client, "gate_tools", None):
        kwargs["tools"] = [_ANSWER_TOOL]
    return client, kwargs


def short_error(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return "the provider refused the request — check /provider"
    if status == 429:
        return "rate limited — try again in a moment"
    msg = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return msg if len(msg) <= 160 else msg[:157] + "…"


def _blocks(final: Any) -> list[dict[str, Any]]:
    return [_block(b) for b in (getattr(final, "content", None) or [])]


_JSON_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_TEXT_KEY_RE = re.compile(r'"text"\s*:\s*"')
_HIGH_SURROGATES = range(0xD800, 0xDC00)
_LOW_SURROGATES = range(0xDC00, 0xE000)


class _ReplyText:
    """Reads the ``text`` argument of a ``reply`` call out of its JSON while it is still
    arriving, so a gateway that forces tools streams the answer like any other."""

    def __init__(self) -> None:
        self._raw = ""
        self._pos: int | None = None    # just past the opening quote of the value
        self._done = False

    def feed(self, chunk: str) -> str:
        """The newly readable part of the text after ``chunk`` arrived."""
        if self._done or not chunk:
            return ""
        self._raw += chunk
        raw = self._raw
        if self._pos is None:
            found = _TEXT_KEY_RE.search(raw)
            if found is None:
                return ""
            self._pos = found.end()
        out: list[str] = []
        i = self._pos
        while i < len(raw):
            ch = raw[i]
            if ch == '"':
                self._done = True
                break
            if ch != "\\":
                out.append(ch)
                i += 1
                continue
            if i + 1 >= len(raw):
                break                     # the escape is cut off: wait for the rest
            kind = raw[i + 1]
            if kind != "u":
                out.append(_JSON_ESCAPES.get(kind, kind))
                i += 2
                continue
            if i + 6 > len(raw):
                break
            try:
                code = int(raw[i + 2:i + 6], 16)
            except ValueError:
                out.append("\ufffd")
                i += 6
                continue
            if code in _HIGH_SURROGATES:  # an emoji: needs its second half too
                if i + 12 > len(raw):
                    break
                try:
                    low = int(raw[i + 8:i + 12], 16) if raw[i + 6:i + 8] == "\\u" else 0
                except ValueError:
                    low = 0
                if low in _LOW_SURROGATES:
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                    i += 6
                else:
                    code = 0xFFFD
            out.append(chr(code) if code not in _LOW_SURROGATES else "\ufffd")
            i += 6
        self._pos = i
        return "".join(out)


def _pieces(stream: Any, gated: bool) -> Any:
    """The answer's text as it arrives. Plain text normally; through a gateway that
    forces tools it is the ``reply`` call's text argument (other text is chatter)."""
    if gated:
        if not hasattr(stream, "delta_stream"):
            return
        readers: dict[Any, _ReplyText] = {}
        names: dict[Any, str] = {}
        for kind, chunk in stream.delta_stream:
            if kind != "tool_input":
                continue
            key, name, args = chunk
            names[key] = name or names.get(key, "")
            if names[key] == _ANSWER_TOOL["name"]:
                piece = readers.setdefault(key, _ReplyText()).feed(args)
                if piece:
                    yield piece
    elif hasattr(stream, "text_stream"):
        yield from stream.text_stream


def _stream_answer(client: Any, kwargs: dict[str, Any], alive: Callable[[], bool],
                   on_text: Callable[[str], None], deadline: float) -> str:
    """Ask the model; ``on_text`` gets each new piece. Returns the whole answer."""
    gated = bool(getattr(client, "gate_tools", None))
    for _ in range(RETRIES + 1):
        streamed = ""
        with client.messages.stream(**kwargs) as stream:
            for piece in _pieces(stream, gated):
                if not alive():
                    return streamed
                if time.monotonic() > deadline:
                    raise BtwError(f"the model took longer than {int(TIMEOUT_SECS)}s — try again")
                if piece:
                    streamed += piece
                    on_text(piece)
            if not alive():
                return streamed
            blocks = _blocks(stream.get_final_message())
        calls = [b for b in blocks if b.get("type") == "tool_use"]
        for call in calls:
            if call.get("name") == _ANSWER_TOOL["name"]:
                text = str((call.get("input") or {}).get("text") or "")
                if text and not streamed:
                    on_text(text)
                return text
        text = streamed or "".join(b.get("text") or "" for b in blocks if b.get("type") == "text")
        if text.strip():
            if not streamed:
                on_text(text)
            return text
        if not calls:
            return ""
        # It reached for a tool: refuse and ask again.
        refusal = _ONLY_REPLY_TOOL if gated else _NO_TOOLS
        kwargs["messages"] = kwargs["messages"] + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": b.get("id"), "name": b.get("name"), "input": b.get("input") or {}}
                for b in calls
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.get("id"), "content": refusal, "is_error": True}
                for b in calls
            ]},
        ]
    raise BtwError("the model kept trying to run tools — try another model")


def ask(question: str, *, thread_id: str = "", tab_id: int | None = None,
        on_event: EventFn | None = None) -> Thread:
    """Ask ``question`` on a side thread (a new one when ``thread_id`` is empty).

    Returns the thread at once; the answer arrives as :class:`Event` calls on a
    worker thread (``start`` → ``delta``… → ``done`` | ``error``). Raises
    :class:`BtwError` for a question that can't be asked at all.
    """
    from . import tabs

    question = (question or "").strip()
    if not question:
        raise BtwError("type a question after /btw")
    if len(question) > MAX_QUESTION:
        raise BtwError(f"keep the question under {MAX_QUESTION:,} characters")
    emit = on_event or (lambda _e: None)

    with _lock:
        thread = _threads.get(thread_id) if thread_id else None
        if thread_id and thread is None:
            raise BtwError("that side window was closed — start a new /btw")
        if thread is not None and thread.running:
            raise BtwError("still answering — wait for it, or stop it first")
        if thread is None:
            tab = (tabs.get(tab_id) if tab_id is not None else None) or tabs.active()
            thread = new_thread(tab.id)
            thread.session_id = tab.session_id or None   # linked to this chat from the start
        thread.running = True
        thread.gen += 1
        gen = thread.gen
        thread.pending, thread.partial = question, ""
        thread.updated = time.time()
        if not thread.title:
            thread.title = _clip(" ".join(question.split()), MAX_ARCHIVE_TITLE)
        turn = len(thread.pairs)

    def alive() -> bool:
        with _lock:
            return thread.gen == gen and thread.running

    def say(op: str, text: str = "", **extra: Any) -> None:
        try:
            emit(Event(thread.id, op, turn=turn, text=text, extra=extra))
        except Exception:  # noqa: BLE001 — a broken listener must not kill the answer
            pass

    def work() -> None:
        try:
            # Always the chat the side chat belongs to, even when its tab number changed.
            tab = scoped_tab(thread.tab_id, thread.session_id)
            client, kwargs = _request_kwargs(thread, question, tab)

            def on_text(piece: str) -> None:
                if alive():
                    with _lock:
                        thread.partial += piece
                    say("delta", piece)

            answer = _stream_answer(client, kwargs, alive, on_text,
                                    time.monotonic() + TIMEOUT_SECS).strip()
            if not alive():
                return
            if not answer:
                raise BtwError("the model returned nothing — try again")
            with _lock:
                if thread.gen != gen:
                    return
                thread.pairs.append((question, answer))
                thread.running = False
                thread.pending = thread.partial = ""
                thread.updated = time.time()
            save()                      # an answer is never lost, even to a crash
            say("done", answer)
        except BtwError as exc:
            _fail(str(exc))
        except Exception as exc:  # noqa: BLE001 — shown to the user, never raised into the UI
            _fail(short_error(exc))

    def _fail(why: str) -> None:
        # The question stays with whatever streamed before the failure — a
        # reopened chat shows what was asked, never a blank window.
        with _lock:
            if thread.gen != gen:
                return
            thread.running = False
        save()
        say("error", why)

    say("start", question=question)
    threading.Thread(target=work, name="btw", daemon=True).start()
    return thread
