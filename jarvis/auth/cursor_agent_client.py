"""Cursor models through the Cursor SDK (the user's own Cursor subscription).

A drop-in for the Anthropic client (``client.messages.stream(...)``), like the
Codex / Antigravity / Claude Code clients. Same design as
``claude_agent_client.py``:

* Cursor's built-in tools are off (``tools=[]``); Jarvis's tools are offered as
  local ``custom_tools``. Cursor owns the agent loop, Jarvis owns tools,
  approvals, history and the UI. When Cursor calls a tool, the tool's
  ``execute`` callback *holds* the call; the stream given to Jarvis ends with
  ``stop_reason="tool_use"``; Jarvis runs the tool its usual way (approval
  prompts, transcript rows, diffs) and sends the next request, whose tool
  results release the held calls.
* No settings are loaded (no ``setting_sources``): no Cursor rules, MCP
  servers, hooks or skills — Jarvis owns project context.
* One Cursor ``Agent`` per conversation. A request continues the agent whose
  last reply is the request's last assistant message (``_Session.sig``);
  anything else (a new chat, history edited, another provider's replies)
  starts an agent that gets the earlier history once, as a
  ``<conversation-checkpoint>``. The SDK has no system-prompt option, so
  Jarvis's instructions ride on the agent's first message.
* One SDK bridge (a Node subprocess the package ships) per working folder,
  shared by every agent.

The API key (``cursor_agent.api_key()``) is only handed to the SDK.
"""
from __future__ import annotations

import atexit
import concurrent.futures
import hashlib
import itertools
import json
import os
import queue
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace as NS
from typing import Any, Callable, Generator

import httpx
from openai import APIStatusError

from . import cursor_agent as cfg
from .claude_agent_client import (
    _append_extra, _d, _split_results, _system_text, _trim, _user_blocks, anchor, first_blocks, sig_of,
)
from .opencode_client import _ContentBlock, _TextBlock, _ToolUseBlock

# A held tool call gives up after this long (the turn was abandoned).
STALE_TOOLS_SECONDS = 1800.0
# A session idle this long is closed; the next message starts one from a checkpoint.
IDLE_SECONDS = 600.0
# Cursor calls tools one at a time or in parallel; calls arriving within this
# window of each other are handed to Jarvis as one batch.
BATCH_WINDOW = 0.4
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
STOPPED = "The tool call was not run (stopped in Jarvis)."


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


# Requests in flight at once (main chat, tabs, subagents) and live agents kept.
MAX_PARALLEL = _int_env("HARNESS_CURSOR_MAX_PARALLEL", 4, 1, 16)
MAX_SESSIONS = _int_env("HARNESS_CURSOR_MAX_SESSIONS", 8, 2, 32)


# ─── Errors ───────────────────────────────────────────────────────────────


class CursorAgentError(APIStatusError):
    """An error from Cursor (status + message), shaped like the other
    OpenAI-style clients' errors so ``repl/stream.py`` and ``api_errors``
    handle it the same way."""


def _error(status: int, message: str) -> CursorAgentError:
    resp = httpx.Response(status, request=httpx.Request("POST", "https://cursor.local/v1/messages"),
                          json={"error": {"code": status, "message": message}})
    return CursorAgentError(f"Error code: {status} - {message}", response=resp,
                            body={"error": {"code": status, "message": message}})


# SDK exception class name → HTTP-style status (matched by name so this module
# imports without the SDK).
_SDK_STATUS = {
    "AuthenticationError": 401, "PermissionDeniedError": 403, "RateLimitError": 429,
    "BadRequestError": 400, "NotFoundError": 404, "AgentNotFoundError": 404, "AgentBusyError": 409,
    "ConfigurationError": 400, "APITimeoutError": 504, "NetworkError": 503, "InternalServerError": 500,
}


def sdk_error(exc: BaseException, fallback: str = "Cursor failed") -> CursorAgentError:
    if isinstance(exc, CursorAgentError):
        return exc
    status = 500
    for cls in type(exc).__mro__:
        if cls.__name__ in _SDK_STATUS:
            status = _SDK_STATUS[cls.__name__]
            break
    text = str(exc) or type(exc).__name__
    if status == 401:
        text = f"{text} — check your Cursor API key (Cursor Dashboard → API Keys)."
    return _error(status, f"{fallback}: {_trim(text, 400)}" if status >= 500 else _trim(text, 400))


def not_ready_error() -> CursorAgentError:
    code, msg, fix = cfg.status().problem()
    if code == "no_sdk":
        return _error(424, f"{msg} Install it: `{fix}`")
    if code in ("no_key", "bad_key"):
        return _error(401, f"{msg}")
    return _error(500, msg or "Cursor isn't available.")


# ─── Request spec ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Spec:
    """What a Cursor agent is created with. Changing any of it means a new
    agent (tools and the model can't be swapped on a live one)."""

    model: str
    system: str
    tools: tuple  # ((name, description, json schema text), …)
    effort: str
    cwd: str

    @property
    def key(self) -> str:
        return hashlib.sha1(repr(self).encode("utf-8", "replace")).hexdigest()


NOTE = (
    "You are running inside Jarvis, a terminal and web coding assistant, through the "
    "Cursor SDK. Cursor's own built-in tools are not available here; use the tools "
    "Jarvis provides (Jarvis shows each call to the user and asks for approval where "
    "needed). Jarvis's instructions follow."
)


def build_spec(*, model: str, system: Any, tools: list[dict] | None, thinking: dict | None) -> Spec:
    rows = []
    for t in tools or []:
        t = _d(t)
        name = str(t.get("name") or "")
        if not name:
            continue
        schema = t.get("input_schema") or {"type": "object", "properties": {}}
        schema = {k: v for k, v in schema.items() if k != "cache_control"}
        rows.append((name, str(t.get("description") or ""), json.dumps(schema, sort_keys=True)))
    th = thinking or {}
    effort = "" if th.get("type") == "disabled" else str(th.get("effort") or "")
    if effort not in EFFORTS:
        effort = ""
    return Spec(model=model or cfg.DEFAULT_MODEL, system=_system_text(system), tools=tuple(rows),
                effort=effort, cwd=os.getcwd())


def instructions(spec: Spec) -> str:
    return f"<jarvis-instructions>\n{NOTE}\n\n{spec.system}\n</jarvis-instructions>".strip()


# ─── User message ─────────────────────────────────────────────────────────


def message_parts(blocks: list[dict]) -> tuple[str, list[tuple[str, str]]]:
    """Claude-style user blocks → ``(text, [(mime, base64), …])``. Documents
    can't ride along (Cursor takes text and images), so they're named."""
    text: list[str] = []
    images: list[tuple[str, str]] = []
    for b in blocks:
        t = b.get("type")
        if t == "text":
            text.append(str(b.get("text") or ""))
        elif t == "image":
            src = b.get("source") or {}
            if src.get("data"):
                images.append((str(src.get("media_type") or "image/png"), str(src["data"])))
        elif t == "document":
            text.append("[A document was attached, but Cursor can't read documents through Jarvis.]")
    return "\n\n".join(x for x in text if x.strip()), images


def _sdk_message(text: str, images: list[tuple[str, str]]) -> Any:
    from cursor_sdk import SDKImage, UserMessage

    return UserMessage(text=text, images=[SDKImage.from_data(data, mime) for mime, data in images] or None)


# ─── Bridges: one per working folder ──────────────────────────────────────

_bridges: dict[str, Any] = {}
_bridges_lock = threading.Lock()


def bridge(cwd: str) -> Any:
    """The shared SDK client (bridge subprocess) for ``cwd``."""
    with _bridges_lock:
        client = _bridges.get(cwd)
        if client is None:
            from cursor_sdk import CursorClient

            client = CursorClient.launch_bridge(workspace=cwd, timeout=cfg.PROBE_TIMEOUT)
            _bridges[cwd] = client
        return client


def drop_bridge(cwd: str) -> None:
    """Forget (and stop) a bridge that stopped answering."""
    with _bridges_lock:
        client = _bridges.pop(cwd, None)
    if client is not None:
        try:
            client.close()
        except Exception:
            pass


def shutdown_bridges() -> None:
    with _bridges_lock:
        clients = list(_bridges.values())
        _bridges.clear()
    for c in clients:
        try:
            c.close()
        except Exception:
            pass


# ─── One Cursor agent ─────────────────────────────────────────────────────

_ids = itertools.count(1)
_END = object()


class _Session:
    """A Cursor agent and what Jarvis knows about its conversation.

    ``q`` carries what the current reply produces, from the pump thread and the
    tool callbacks to the reader: ``("text", s)``, ``("thinking", s)``,
    ``("tool", id, name, args)``, ``("usage", TokenUsage)``,
    ``("done", RunResult)``, ``("fail", exc)``.
    """

    def __init__(self, engine: "_Engine", spec: Spec, *, factory: Callable | None = None):
        self.n = next(_ids)
        self.engine = engine
        self.spec = spec
        self.factory = factory
        self.q: queue.Queue = queue.Queue()
        self.phase = "starting"      # starting → streaming ⇄ tools / idle → dead
        self.sig: tuple | None = None    # our last finished reply
        self.fed = 0                 # messages after `sig` already given to Cursor
        self.expect: list[str] = []  # tool ids Cursor waits on
        self.busy = False
        self.interrupted = False
        self.primed = False          # Jarvis's instructions already sent
        self.last_used = time.monotonic()
        self.agent: Any = None
        self.run: Any = None
        self.pump_done = threading.Event()
        self.pump_done.set()
        self._futures: dict[str, concurrent.futures.Future] = {}
        self._lock = threading.Lock()
        self._saw_delta = False

    # ── lifecycle ──

    def start(self) -> None:
        try:
            if self.factory is not None:
                self.agent = self.factory(self)
            else:
                self.agent = self._create_agent()
        except BaseException as e:  # noqa: BLE001
            self.phase = "dead"
            raise sdk_error(e, "Cursor didn't start")

    def _create_agent(self) -> Any:
        from cursor_sdk import Agent, AgentOptions, CustomTool, LocalAgentOptions

        tools = {
            name: CustomTool(execute=self.executor(name), description=desc, input_schema=json.loads(schema))
            for name, desc, schema in self.spec.tools
        }
        opts = AgentOptions(
            model=cfg.model_selection(self.spec.model, self.spec.effort),
            api_key=cfg.api_key(),
            name="Jarvis",
            local=LocalAgentOptions(cwd=self.spec.cwd, custom_tools=tools or None),
            tools=[],  # Cursor's built-in tools off: Jarvis's are the only ones
        )
        return Agent.create(opts, client=bridge(self.spec.cwd))

    def close(self) -> None:
        self.phase = "dead"
        self._release_all(STOPPED)
        agent, self.agent = self.agent, None
        if agent is not None:
            threading.Thread(target=self._close_agent, args=(agent,), daemon=True,
                             name="jarvis-cursor-close").start()

    @staticmethod
    def _close_agent(agent: Any) -> None:
        try:
            agent.close()
        except Exception:
            pass

    # ── tools (Cursor's callback threads) ──

    def executor(self, name: str) -> Callable[[Any, Any], Any]:
        def execute(args: Any, ctx: Any) -> Any:
            tid = str(getattr(ctx, "tool_call_id", "") or "") or f"cursor_{self.n}_{next(_ids)}"
            fut: concurrent.futures.Future = concurrent.futures.Future()
            with self._lock:
                self._futures[tid] = fut
            self.q.put(("tool", tid, name, dict(args or {})))
            try:
                return fut.result(timeout=STALE_TOOLS_SECONDS)
            except concurrent.futures.TimeoutError:
                return {"content": [{"type": "text", "text": "Jarvis didn't answer this tool call in time."}],
                        "isError": True}
            except concurrent.futures.CancelledError:
                return {"content": [{"type": "text", "text": STOPPED}], "isError": True}
            finally:
                with self._lock:
                    self._futures.pop(tid, None)
        return execute

    def give_results(self, results: dict[str, dict]) -> None:
        with self._lock:
            futs = dict(self._futures)
        for tid in list(self.expect):
            payload = results.get(tid) or {"content": [{"type": "text", "text": STOPPED}], "isError": True}
            fut = futs.get(tid)
            if fut is not None and not fut.done():
                fut.set_result(payload)
        self.expect = []
        self.phase = "streaming"
        self.last_used = time.monotonic()

    def _release_all(self, why: str) -> None:
        with self._lock:
            futs = list(self._futures.values())
        for fut in futs:
            if not fut.done():
                fut.set_result({"content": [{"type": "text", "text": why}], "isError": True})

    # ── a reply ──

    def query(self, text: str, images: list[tuple[str, str]]) -> None:
        """Send a user message and start streaming its reply into ``q``."""
        if self.phase == "dead" or self.agent is None:
            raise _error(503, "The Cursor agent has stopped.")
        self.q = queue.Queue()
        self._saw_delta = False
        self.pump_done.clear()
        self.phase = "streaming"
        self.last_used = time.monotonic()
        message = (text, images) if self.factory is not None else _sdk_message(text, images)
        threading.Thread(target=self._pump, args=(message,), daemon=True, name=f"jarvis-cursor-{self.n}").start()

    def _on_delta(self, update: Any) -> None:
        kind = getattr(update, "type", None) or (update.get("type") if isinstance(update, dict) else None)
        text = getattr(update, "text", None)
        if text is None and isinstance(update, dict):
            text = update.get("text")
        if kind == "text-delta" and text:
            self._saw_delta = True
            self.q.put(("text", str(text)))
        elif kind == "thinking-delta" and text:
            self.q.put(("thinking", str(text)))

    def _pump(self, message: Any) -> None:
        q = self.q
        try:
            if self.factory is not None:
                run = self.agent.send(message, on_delta=self._on_delta)
            else:
                from cursor_sdk import SendOptions

                run = self.agent.send(message, SendOptions(on_delta=self._on_delta))
            self.run = run
            for ev in run.events():
                sm = getattr(ev, "sdk_message", None)
                kind = getattr(sm, "type", "")
                if kind == "usage":
                    q.put(("usage", sm.usage))
                elif kind == "assistant" and not self._saw_delta:
                    # No streamed deltas for this reply: take the finished text.
                    for b in getattr(getattr(sm, "message", None), "content", ()) or ():
                        if getattr(b, "text", ""):
                            q.put(("text", b.text))
            q.put(("done", run.wait()))
        except BaseException as e:  # noqa: BLE001 — reported to the reader
            q.put(("fail", e))
        finally:
            self.pump_done.set()

    def interrupt(self) -> None:
        """Esc: stop the reply. Held tool calls are released as stopped."""
        if self.phase not in ("streaming", "tools"):
            return
        self.interrupted = True
        self._release_all(STOPPED)
        run = self.run
        if run is not None:
            threading.Thread(target=self._cancel, args=(run,), daemon=True, name="jarvis-cursor-cancel").start()

    @staticmethod
    def _cancel(run: Any) -> None:
        try:
            run.cancel()
        except Exception:
            pass  # already finished, or not cancellable now


# ─── Routing requests to agents ───────────────────────────────────────────


class _Engine:
    def __init__(self, factory: Callable | None = None):
        self.lock = threading.Lock()
        self.sessions: list[_Session] = []
        self.slots = threading.BoundedSemaphore(MAX_PARALLEL)
        self.factory = factory
        self._janitor: threading.Thread | None = None

    def _match(self, messages: list[dict]) -> tuple[_Session | None, int]:
        last, want = anchor(messages)
        for s in reversed(self.sessions):
            if s.sig == want and not s.busy and s.phase not in ("starting", "dead"):
                return s, last
        return None, last

    def _evict(self) -> None:
        live = [s for s in self.sessions if s.phase != "dead"]
        if len(live) < MAX_SESSIONS:
            return
        idle = sorted((s for s in live if s.phase == "idle" and not s.busy), key=lambda s: s.last_used)
        for s in idle[: len(live) - MAX_SESSIONS + 1]:
            s.close()

    def _prune(self) -> None:
        for s in [s for s in self.sessions if s.phase == "dead" and not s.busy]:
            self.sessions.remove(s)

    def _ensure_janitor(self) -> None:
        if self._janitor is None or not self._janitor.is_alive():
            self._janitor = threading.Thread(target=self._tidy, daemon=True, name="jarvis-cursor-tidy")
            self._janitor.start()

    def _tidy(self) -> None:
        while True:
            time.sleep(30)
            now = time.monotonic()
            with self.lock:
                for s in self.sessions:
                    if s.busy or s.phase == "dead":
                        continue
                    if s.phase == "idle" and now - s.last_used > IDLE_SECONDS:
                        s.close()
                    elif s.phase == "tools" and now - s.last_used > STALE_TOOLS_SECONDS:
                        s.close()
                        s.sig = None

    def _spawn(self, spec: Spec) -> _Session:
        sess = _Session(self, spec, factory=self.factory)
        sess.busy = True
        with self.lock:
            self._evict()
            self._prune()
            self.sessions.append(sess)
        self._ensure_janitor()
        try:
            sess.start()
        except BaseException:
            self._release(sess, dead=True)
            raise
        return sess

    def open(self, spec: Spec, messages: list[dict]) -> "_Stream":
        if self.factory is None and not cfg.is_ready():
            raise not_ready_error()
        if not self.slots.acquire(timeout=600):
            raise _error(429, "Too many Cursor requests at once — try again in a moment.")
        try:
            return self._open(spec, messages)
        except BaseException:
            self.slots.release()
            raise

    def _open(self, spec: Spec, messages: list[dict]) -> "_Stream":
        with self.lock:
            sess, last = self._match(messages)
            if sess is not None:
                sess.busy = True
        if sess is not None:
            tail = messages[last + 1:][sess.fed:]
            try:
                return self._continue(sess, spec, tail, messages)
            except CursorAgentError:
                raise
            except BaseException:
                self._release(sess, dead=True)
                # The agent went away: start over with the history.
        return self._fresh(spec, messages)

    def _continue(self, sess: _Session, spec: Spec, tail: list[dict], messages: list[dict]) -> "_Stream":
        if sess.phase == "tools":
            results, extra = _split_results(tail)
            if extra:
                _append_extra(results, sess.expect, extra)
            # Cursor's tool results are {"content": [...], "isError": bool}.
            sess.give_results({tid: {"content": r["content"], "isError": r["is_error"]}
                               for tid, r in results.items()})
            sess.fed += len(tail)
            return _Stream(self, sess, spec, messages)
        if not _user_blocks(tail):
            raise RuntimeError("nothing new to send")
        if sess.interrupted and not sess.pump_done.wait(20):
            sess.close()  # the stopped run never finished: start over
        if sess.phase == "dead" or sess.spec.key != spec.key:
            raise RuntimeError("agent changed")  # → _fresh, with the history as a checkpoint
        blocks = _user_blocks(tail)
        if sess.interrupted:
            blocks.insert(0, {"type": "text", "text": "[The user stopped your previous reply.]"})
            sess.interrupted = False
        text, images = message_parts(blocks)
        sess.query(text, images)
        sess.fed += len(tail)
        return _Stream(self, sess, spec, messages)

    @staticmethod
    def _anchor_new(sess: _Session, messages: list[dict]) -> None:
        idx, sig = anchor(messages)
        sess.sig, sess.fed = sig, len(messages) - (idx + 1)

    def _fresh(self, spec: Spec, messages: list[dict]) -> "_Stream":
        sess = self._spawn(spec)
        self._anchor_new(sess, messages)
        try:
            text, images = message_parts(first_blocks(messages))
            sess.query(f"{instructions(spec)}\n\n{text}".strip(), images)
            sess.primed = True
        except BaseException:
            self._release(sess, dead=True)
            raise
        return _Stream(self, sess, spec, messages)

    def _release(self, sess: _Session, *, dead: bool = False) -> None:
        with self.lock:
            sess.busy = False
            sess.last_used = time.monotonic()
        if dead:
            sess.close()
            sess.sig = None

    def finish(self, sess: _Session) -> None:
        self._release(sess)
        self.slots.release()

    def close_all(self) -> None:
        with self.lock:
            sessions = list(self.sessions)
        for s in sessions:
            s.close()


# ─── The stream Jarvis reads ──────────────────────────────────────────────


class _Stream:
    """Mimics the Anthropic SDK's MessageStream (``delta_stream``,
    ``text_stream``, ``get_final_message``, ``close``)."""

    def __init__(self, engine: _Engine, sess: _Session, spec: Spec, messages: list[dict]):
        self.engine = engine
        self.sess = sess
        self.spec = spec
        self.messages = messages
        self._final = None
        self._closed = False
        self._finished = False
        self._content: list[_ContentBlock] = []
        self._text = ""
        self._thinking = ""
        self._batch: list[tuple[str, str, dict]] = []
        self._batch_at = 0.0
        self._usage = NS(input_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                         output_tokens=0, total_tokens=0)
        self._timeout = float(os.getenv("HARNESS_CURSOR_TIMEOUT", "0") or 0) or 900.0
        self._once = threading.Lock()  # Esc (UI thread) can race the worker finishing

    def _take_finish(self) -> bool:
        """True exactly once: whoever gets it releases the session and slot."""
        with self._once:
            if self._finished:
                return False
            self._finished = True
            return True

    # ── events ──

    def _flush_text(self) -> None:
        if self._thinking.strip():
            self._content.append(_ContentBlock(type="thinking", thinking=self._thinking))
        if self._text:
            self._content.append(_TextBlock(type="text", text=self._text))
        self._thinking = self._text = ""

    def _add_usage(self, u: Any) -> None:
        # Cursor's input_tokens already include cache reads/writes; the output
        # of every step of the reply adds up, the input is the latest context.
        self._usage.input_tokens = int(getattr(u, "input_tokens", 0) or 0)
        self._usage.output_tokens += int(getattr(u, "output_tokens", 0) or 0)
        self._usage.total_tokens = self._usage.input_tokens + self._usage.output_tokens

    def _end(self, stop: str) -> None:
        if not self._take_finish():
            return
        sess = self.sess
        self._flush_text()
        if stop == "tool_use":
            for tid, name, args in self._batch:
                self._content.append(_ToolUseBlock(type="tool_use", id=tid, name=name, input=args))
            sess.expect = [tid for tid, _n, _a in self._batch]
            sess.phase = "tools"
        else:
            sess.phase = "idle"
        sess.sig = sig_of({"content": self._content})
        sess.fed = 0
        cfg.clear_limit(self.spec.model)  # it answered: this pool has room
        self._final = NS(content=list(self._content), usage=self._usage, stop_reason=stop or "end_turn",
                         model=self.spec.model, cost_usd=0.0)
        self.engine.finish(sess)

    def _fail(self, err: CursorAgentError) -> None:
        if err.status_code == 429:
            cfg.note_limit(self.spec.model, str(err))  # which pool is out, and until when
        if self._take_finish():
            self.engine._release(self.sess, dead=True)
            self.engine.slots.release()
        raise err

    def _result_error(self, res: Any) -> CursorAgentError | None:
        status = str(getattr(res, "status", "") or "").lower()
        if status != "error":
            return None
        code = str(getattr(res, "error_code", "") or "")
        msg = str(getattr(res, "error_message", "") or "") or "Cursor stopped with an error."
        low = (code + " " + msg).lower()
        if "auth" in low or "api key" in low or "unauthor" in low:
            return _error(401, f"{msg} — check your Cursor API key.")
        if "context" in low and ("long" in low or "limit" in low or "exceed" in low):
            return _error(400, msg)
        if "rate" in low or "quota" in low or "limit" in low:
            return _error(429, msg)
        return _error(500, msg)

    # ── reading ──

    def _next(self, deadline: float) -> Any:
        """The next queue item, ``None`` when the batch window just closed."""
        while True:
            if self._closed:
                raise KeyboardInterrupt("stream cancelled")
            wait = 0.25
            if self._batch:
                wait = max(0.01, min(wait, self._batch_at + BATCH_WINDOW - time.monotonic()))
            try:
                return self.sess.q.get(timeout=wait)
            except queue.Empty:
                if self._batch and time.monotonic() >= self._batch_at + BATCH_WINDOW:
                    return None
                if time.monotonic() > deadline:
                    self.sess.interrupt()
                    self._fail(_error(504, f"Cursor sent nothing for {int(self._timeout)}s."))

    @property
    def delta_stream(self) -> Generator[tuple[str, Any], None, None]:
        deadline = time.monotonic() + self._timeout
        while not self._finished:
            item = self._next(deadline)
            deadline = time.monotonic() + self._timeout
            if item is None:  # the batch window closed: hand the tool calls to Jarvis
                self._end("tool_use")
                break
            kind = item[0]
            if kind == "text":
                self._text += item[1]
                yield "text", item[1]
            elif kind == "thinking":
                self._thinking += item[1]
                yield "thinking", item[1]
            elif kind == "tool":
                _k, tid, name, args = item
                self._batch.append((tid, name, args))
                self._batch_at = time.monotonic()
                yield "tool_input", (len(self._batch) - 1, name, json.dumps(args, ensure_ascii=False))
            elif kind == "usage":
                self._add_usage(item[1])
            elif kind == "fail":
                self._fail(sdk_error(item[1]))
            elif kind == "done":
                err = self._result_error(item[1])
                if err is not None and not self._text and not self._content:
                    self._fail(err)
                if not self._usage.input_tokens:
                    self._add_usage(getattr(item[1], "usage", None))
                self._end("end_turn")
                break
        if self._final is None and not self._closed:
            self._end("end_turn")

    @property
    def text_stream(self) -> Generator[str, None, None]:
        for kind, chunk in self.delta_stream:
            if kind == "text":
                yield chunk

    def get_final_message(self):
        if self._final is None and not self._closed:
            for _ in self.delta_stream:
                pass
        if self._final is None:
            self._flush_text()
            return NS(content=list(self._content), usage=self._usage, stop_reason="end_turn",
                      model=self.spec.model, cost_usd=0.0)
        return self._final

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._take_finish():
            # Esc: stop Cursor mid-reply; the agent keeps the partial reply and
            # the next request continues the same conversation.
            self.sess.interrupt()
            self.engine.finish(self.sess)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ─── Public client ────────────────────────────────────────────────────────

_engine: _Engine | None = None
_engine_lock = threading.Lock()


def engine() -> _Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = _Engine()
        return _engine


def shutdown() -> None:
    with _engine_lock:
        e = _engine
    if e is not None:
        e.close_all()
    shutdown_bridges()


atexit.register(shutdown)


class _Messages:
    def __init__(self, owner: "CursorAgentClient"):
        self._owner = owner

    def stream(self, *, model: str, messages: list[dict], tools: list[dict] | None = None,
               system: Any = None, thinking: dict | None = None, **_ignored: Any) -> _Stream:
        spec = build_spec(model=model, system=system, tools=tools, thinking=thinking)
        return self._owner.engine.open(spec, [_d(m) for m in messages])


class CursorAgentClient:
    """``state.client`` for the ``cursor`` provider."""

    def __init__(self, *, engine_: _Engine | None = None):
        self.engine = engine_ or engine()
        self.messages = _Messages(self)

    def validate(self) -> bool:
        return cfg.is_ready()

    def close(self) -> None:
        pass  # agents and bridges are shared by every client; they stop when idle
