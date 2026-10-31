"""Claude through the user's own Claude Code CLI (Claude Agent SDK).

A drop-in for the Anthropic client (``client.messages.stream(...)``), like the
Codex / Antigravity clients — but the request is made by the user's unmodified
``claude`` binary, signed in by the user, never by Jarvis. This is the same
design as OpenCode's "Claude Agent" provider:

* Claude Code's built-in tools are off (``tools=[]``); Jarvis's tools are
  offered as an in-process MCP server ``jarvis`` (``mcp__jarvis__<name>``).
  Anything else is refused (``permission_mode="dontAsk"``).
* Claude Code owns the agent loop, Jarvis owns tools, approvals, history and
  the UI. When Claude calls a tool, the MCP handler *holds* the call; the
  stream given to Jarvis ends with ``stop_reason="tool_use"``; Jarvis runs the
  tool its usual way (approval prompts, transcript rows, diffs) and sends the
  next request, whose tool results release the held calls.
* No settings are inherited (``setting_sources=[]``, ``strict_mcp_config``):
  no ~/.claude settings, hooks, MCP servers, skills or CLAUDE.md.
* One long-running ``claude`` process per conversation. A request continues
  the process whose last reply is the request's last assistant message
  (``_Session.sig``); anything else (a new chat, history edited, another
  provider's replies) starts a process that gets the earlier history once, as
  a ``<conversation-checkpoint>`` message. When the system prompt, tools, model
  or effort change between turns the process restarts and resumes its saved
  Claude Code session.

Account safety: no credentials are read, stored or passed (the binary signs in
by itself), no headers are added, nothing sits between Claude Code and
Anthropic, the binary is run as published. See ``claude_agent.py``.
"""
from __future__ import annotations

import asyncio
import atexit
import hashlib
import itertools
import json
import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from types import SimpleNamespace as NS
from typing import Any, Callable, Generator

import httpx
from openai import APIStatusError

from . import claude_agent as cfg
from .opencode_client import _ContentBlock, _TextBlock, _ToolUseBlock

SERVER = "jarvis"
PREFIX = f"mcp__{SERVER}__"
CHECKPOINT_TAG = "conversation-checkpoint"
# A process idle this long is stopped; the next message resumes its saved
# Claude Code session.
IDLE_SECONDS = 300.0
# A process waiting on tool results nobody sends (the turn was abandoned).
STALE_TOOLS_SECONDS = 1800.0


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


# Requests in flight at once, across main chat, tabs and subagents. Claude
# plans assume ordinary individual use; Jarvis never fans out further.
MAX_PARALLEL = _int_env("HARNESS_CLAUDE_AGENT_MAX_PARALLEL", 4, 1, 16)
# Live `claude` processes kept (idle ones beyond this are stopped first).
MAX_PROCESSES = _int_env("HARNESS_CLAUDE_AGENT_MAX_PROCS", 8, 2, 32)


# ─── Errors ───────────────────────────────────────────────────────────────


class ClaudeAgentError(APIStatusError):
    """An error from Claude Code (status + message), shaped like the other
    OpenAI-style clients' errors so ``repl/stream.py`` and ``api_errors``
    handle it the same way."""


_ERROR_STATUS = {
    "authentication_failed": 401,
    "oauth_org_not_allowed": 403,
    "billing_error": 402,
    "rate_limit": 429,
    "invalid_request": 400,
    "server_error": 500,
    "max_output_tokens": 400,
    "unknown": 500,
}


def _error(status: int, message: str) -> ClaudeAgentError:
    resp = httpx.Response(status, request=httpx.Request("POST", "https://claude-code.local/v1/messages"),
                          json={"error": {"code": status, "message": message}})
    return ClaudeAgentError(f"Error code: {status} - {message}", response=resp,
                            body={"error": {"code": status, "message": message}})


def not_ready_error() -> ClaudeAgentError:
    s = cfg.status()
    code, msg, fix = s.problem()
    if code == "signed_out":
        return _error(401, f"{msg} Run `{fix}` in a terminal (Anthropic's own sign-in), then /provider claude.")
    return _error(424 if code else 500, f"{msg or 'Claude Code is not available.'} Fix: `{fix}`" if fix else msg)


# ─── Content helpers ──────────────────────────────────────────────────────


def _d(block: Any) -> dict:
    if isinstance(block, dict):
        return block
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:
            pass
    return dict(getattr(block, "__dict__", {}) or {})


def _blocks(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        return [_d(b) for b in content]
    return []


def strip_prefix(name: str) -> str:
    return name[len(PREFIX):] if name.startswith(PREFIX) else name


def sig_of(msg: dict) -> tuple:
    """What identifies an assistant reply we produced: its tool call ids, or
    the hash of its text."""
    blocks = _blocks(msg.get("content"))
    ids = tuple(str(b.get("id") or "") for b in blocks if b.get("type") == "tool_use")
    if ids:
        return ("tools", ids)
    text = "".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text").strip()
    return ("text", hashlib.sha1(text.encode("utf-8", "replace")).hexdigest())


def anchor(messages: list[dict]) -> tuple[int, tuple]:
    """``(index, sig)`` of what a process's knowledge of the conversation hangs
    off: the last assistant message, or — before any reply — the opening
    message (``index`` -1)."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            return i, sig_of(messages[i])
    first = json.dumps(_blocks(messages[0].get("content")) if messages else [], sort_keys=True, default=str)
    return -1, ("start", hashlib.sha1(first.encode("utf-8", "replace")).hexdigest())


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n…[{len(text) - limit} more characters]"


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in _blocks(content):
        if b.get("type") == "text":
            parts.append(str(b.get("text") or ""))
        elif b.get("type") == "image":
            parts.append("[image]")
    return "\n".join(p for p in parts if p)


def render_checkpoint(messages: list[dict]) -> str:
    """Earlier history as one text block Claude Code can read (it can't load a
    history it didn't write). Thinking is left out, long tool output clipped."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role")
        for b in _blocks(m.get("content")):
            t = b.get("type")
            if t == "text" and str(b.get("text") or "").strip():
                lines.append(f"{'User' if role == 'user' else 'Assistant'}: {b['text'].strip()}")
            elif t == "tool_use":
                args = json.dumps(b.get("input") or {}, ensure_ascii=False)
                lines.append(f"Assistant called {strip_prefix(str(b.get('name') or ''))}({_trim(args, 4000)})")
            elif t == "tool_result":
                status = " (error)" if b.get("is_error") else ""
                lines.append(f"Tool result{status}: {_trim(_result_text(b.get('content')), 12000)}")
            elif t == "image":
                lines.append(f"{'User' if role == 'user' else 'Assistant'}: [image]")
            elif t == "document":
                lines.append("User: [document]")
    body = "\n\n".join(lines)
    return (f"<{CHECKPOINT_TAG}>\nThe conversation so far (it continued in another session — "
            f"read it as context; the tools named are the ones you have now):\n\n{body}\n</{CHECKPOINT_TAG}>")


def _user_blocks(messages: list[dict]) -> list[dict]:
    """Content blocks for a Claude Code user message from Jarvis user
    messages: text and images pass through, anything tool-shaped becomes text."""
    out: list[dict] = []
    for m in messages:
        if m.get("role") != "user":
            continue
        for b in _blocks(m.get("content")):
            t = b.get("type")
            if t == "text":
                if str(b.get("text") or "").strip():
                    out.append({"type": "text", "text": b["text"]})
            elif t == "image" and (b.get("source") or {}).get("type") == "base64":
                out.append({"type": "image", "source": b["source"]})
            elif t == "document" and (b.get("source") or {}).get("type") == "base64":
                out.append({"type": "document", "source": b["source"]})
            elif t == "tool_result":
                out.append({"type": "text", "text": f"Tool result: {_trim(_result_text(b.get('content')), 12000)}"})
    return out


def _mcp_content(content: Any) -> list[dict]:
    """A Jarvis tool_result's content as MCP result content."""
    if isinstance(content, str):
        return [{"type": "text", "text": content or "(no output)"}]
    out: list[dict] = []
    for b in _blocks(content):
        t = b.get("type")
        if t == "text":
            out.append({"type": "text", "text": str(b.get("text") or "")})
        elif t == "image":
            src = b.get("source") or {}
            if src.get("type") == "base64" and src.get("data"):
                out.append({"type": "image", "data": src["data"], "mimeType": src.get("media_type") or "image/png"})
            else:
                out.append({"type": "text", "text": "[image not available]"})
    return out or [{"type": "text", "text": "(no output)"}]


# ─── Request spec ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Spec:
    """What a `claude` process is started with. Changing any of it means a
    restart (the SDK can't swap tools or the system prompt on a live one)."""

    model: str
    system: str
    tools: tuple  # ((name, description, json schema text), …)
    effort: str
    thinking: str  # "adaptive" | "disabled" | ""
    cwd: str

    @property
    def key(self) -> str:
        return hashlib.sha1(repr(self).encode("utf-8", "replace")).hexdigest()

    @property
    def tool_names(self) -> list[str]:
        return [t[0] for t in self.tools]


NOTE = (
    "You are running inside Jarvis, a terminal and web coding assistant, through "
    "Claude Code. Claude Code's own tools are not available here; use the "
    f"`{PREFIX}*` tools Jarvis provides (Jarvis shows each call to the user and "
    "asks for approval where needed). Jarvis's instructions follow."
)


def _system_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    parts = []
    for b in system or []:
        b = _d(b)
        if b.get("type") == "text":
            parts.append(str(b.get("text") or ""))
    return "\n\n".join(p for p in parts if p)


def build_spec(*, model: str, system: Any, tools: list[dict] | None, thinking: dict | None) -> Spec:
    tool_rows = []
    for t in tools or []:
        t = _d(t)
        name = str(t.get("name") or "")
        if not name:
            continue
        schema = t.get("input_schema") or {"type": "object", "properties": {}}
        schema = {k: v for k, v in schema.items() if k != "cache_control"}
        tool_rows.append((name, str(t.get("description") or ""), json.dumps(schema, sort_keys=True)))
    effort, mode = "", ""
    th = thinking or {}
    if th.get("type") == "disabled":
        mode = "disabled"
    elif th:
        mode = "adaptive"
        effort = str(th.get("effort") or "")
    if effort not in ("low", "medium", "high", "xhigh", "max"):
        effort = ""
    return Spec(model=model or cfg.DEFAULT_MODEL, system=_system_text(system), tools=tuple(tool_rows),
                effort=effort, thinking=mode, cwd=os.getcwd())


# ─── The event loop all sessions share ────────────────────────────────────


class _Loop:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def get(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self.loop is None or self.loop.is_closed():
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, daemon=True, name="jarvis-claude-agent").start()
                self.loop = loop
            return self.loop

    def call(self, fn: Callable, *args: Any) -> None:
        self.get().call_soon_threadsafe(fn, *args)

    def run(self, coro, timeout: float | None = None) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.get()).result(timeout)


_LOOP = _Loop()
_END = object()


class _Failure:
    def __init__(self, exc: BaseException):
        self.exc = exc


# ─── One `claude` process ─────────────────────────────────────────────────

_ids = itertools.count(1)


class _Session:
    """A `claude` process and what Jarvis knows about its conversation."""

    def __init__(self, engine: "_Engine", spec: Spec, *, resume: str = "", factory: Callable | None = None):
        self.n = next(_ids)
        self.engine = engine
        self.spec = spec
        self.resume = resume
        self.factory = factory
        self.q: queue.Queue = queue.Queue()
        self.claude_session_id = resume
        self.phase = "starting"     # starting → streaming ⇄ tools / idle → dead
        self.sig: tuple | None = None   # our last finished reply
        self.fed = 0                # messages after `sig` already given to Claude
        self.expect: list[str] = []  # tool ids Claude waits on
        self.busy = False
        self.interrupted = False
        self.last_used = time.monotonic()
        self.stderr: deque[str] = deque(maxlen=40)
        self.saw_init = False
        # Loop side (only touched on the event loop).
        self._announced: list[tuple[str, str, Any]] = []
        self._claimed: set[str] = set()
        self._results: dict[str, asyncio.Future] = {}
        self._announce_evt: asyncio.Event | None = None
        self._cmds: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self.ready = threading.Event()
        self.start_error: BaseException | None = None
        self.alive = False

    # ── lifecycle (loop side) ──

    def _options(self):
        from claude_agent_sdk import ClaudeAgentOptions, SdkMcpTool, create_sdk_mcp_server

        tools = []
        for name, desc, schema in self.spec.tools:
            tools.append(SdkMcpTool(name=name, description=desc, input_schema=json.loads(schema),
                                    handler=self._handler(name)))
        server = create_sdk_mcp_server(SERVER, tools=tools)

        env = dict(cfg.child_env())
        for k in cfg.STRIPPED_ENV:
            if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT") and os.environ.get(k):
                env[k] = ""
        thinking = None
        if self.spec.thinking == "adaptive":
            thinking = {"type": "adaptive", "display": "summarized"}
        elif self.spec.thinking == "disabled":
            thinking = {"type": "disabled"}
        kw: dict[str, Any] = dict(
            cli_path=cfg.status().cli_path or cfg.find_cli(),
            cwd=self.spec.cwd,
            model=self.spec.model if self.spec.model != cfg.DEFAULT_MODEL else None,
            tools=[],
            mcp_servers={SERVER: server},
            strict_mcp_config=True,
            allowed_tools=[PREFIX + n for n in self.spec.tool_names],
            # Only Jarvis's tools are pre-approved; anything else is refused
            # without asking (Jarvis asks the user itself, per call).
            permission_mode="dontAsk",
            setting_sources=[],
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": f"{NOTE}\n\n{self.spec.system}".strip()},
            include_partial_messages=True,
            verbatim_prompts=True,
            env=env,
            stderr=self.stderr.append,
            max_buffer_size=64 * 1024 * 1024,
        )
        if self.spec.effort:
            kw["effort"] = self.spec.effort
        if thinking:
            kw["thinking"] = thinking
        if self.resume:
            kw["resume"] = self.resume
        return ClaudeAgentOptions(**kw)

    def _make_client(self):
        if self.factory is not None:
            return self.factory(self)
        from claude_agent_sdk import ClaudeSDKClient

        return ClaudeSDKClient(self._options())

    async def main(self) -> None:
        """The whole life of the process, in one task (the SDK's anyio scopes
        must be entered and left by the same task)."""
        self._cmds = asyncio.Queue()
        self._announce_evt = asyncio.Event()
        client = None
        reader = None
        try:
            client = self._make_client()
            await client.connect()
            self.alive = True
        except BaseException as e:  # noqa: BLE001 — reported to the caller
            self.start_error = e
            self.phase = "dead"
            self.ready.set()
            self.q.put(_Failure(e))
            self.q.put(_END)
            return
        self.ready.set()

        async def read() -> None:
            try:
                async for m in client.receive_messages():
                    self._on_message(m)
                    self.q.put(m)
            except asyncio.CancelledError:
                raise
            except BaseException as e:  # noqa: BLE001
                self.q.put(_Failure(e))
            finally:
                self.q.put(_END)

        reader = asyncio.ensure_future(read())
        try:
            while True:
                cmd, arg, done = await self._cmds.get()
                if cmd == "close":
                    done.set_result(None)
                    break
                try:
                    if cmd == "query":
                        await client.query(_one(arg))
                    elif cmd == "interrupt":
                        await client.interrupt()
                    done.set_result(None)
                except BaseException as e:  # noqa: BLE001
                    done.set_exception(e)
                    if cmd == "query":
                        break
        finally:
            self.alive = False
            self.phase = "dead"
            for fut in self._results.values():
                if not fut.done():
                    fut.cancel()
            reader.cancel()
            try:
                await asyncio.wait_for(client.disconnect(), 8)
            except BaseException:  # noqa: BLE001
                pass

    def _on_message(self, m: Any) -> None:
        """Loop side: learn the session id and which tool calls exist."""
        name = type(m).__name__
        if name == "SystemMessage" and getattr(m, "subtype", "") == "init":
            self.saw_init = True
            sid = (getattr(m, "data", None) or {}).get("session_id")
            if sid:
                self.claude_session_id = sid
        elif name == "ResultMessage":
            sid = getattr(m, "session_id", "")
            if sid:
                self.claude_session_id = sid
        elif name == "AssistantMessage" and not getattr(m, "parent_tool_use_id", None):
            for b in getattr(m, "content", None) or []:
                if type(b).__name__ == "ToolUseBlock" and str(b.name).startswith(PREFIX):
                    if not any(a[0] == b.id for a in self._announced):
                        self._announced.append((b.id, strip_prefix(b.name), b.input))
                        self._announce_evt.set()

    def _future(self, tid: str) -> asyncio.Future:
        fut = self._results.get(tid)
        if fut is None:
            fut = asyncio.get_running_loop().create_future()
            self._results[tid] = fut
        return fut

    def _handler(self, name: str):
        async def handler(args: dict) -> dict:
            tid = await self._match(name, args)
            if not tid:
                return {"content": [{"type": "text", "text": "Jarvis couldn't match this tool call."}],
                        "is_error": True}
            return await self._future(tid)
        return handler

    async def _match(self, name: str, args: Any) -> str:
        """The announced call this handler invocation belongs to: same name
        and arguments, oldest first (Claude Code calls our tools one by one)."""
        deadline = time.monotonic() + 30
        while True:
            for tid, n, a in self._announced:
                if tid not in self._claimed and n == name and a == args:
                    self._claimed.add(tid)
                    return tid
            for tid, n, _a in self._announced:  # args re-encoded differently
                if tid not in self._claimed and n == name:
                    self._claimed.add(tid)
                    return tid
            left = deadline - time.monotonic()
            if left <= 0:
                return ""
            self._announce_evt.clear()
            try:
                await asyncio.wait_for(self._announce_evt.wait(), left)
            except asyncio.TimeoutError:
                pass

    def _resolve(self, tid: str, payload: dict) -> None:
        fut = self._future(tid)
        if not fut.done():
            fut.set_result(payload)

    async def _send(self, cmd: str, arg: Any = None) -> None:
        done = asyncio.get_running_loop().create_future()
        await self._cmds.put((cmd, arg, done))
        await done

    # ── thread side ──

    def start(self, timeout: float = 90.0) -> None:
        loop = _LOOP.get()
        loop.call_soon_threadsafe(lambda: setattr(self, "_task", loop.create_task(self.main())))
        if not self.ready.wait(timeout):
            self.close()
            raise _error(504, "Claude Code didn't start in time.")
        if self.start_error is not None:
            raise _start_failure(self.start_error, self)

    def command(self, cmd: str, arg: Any = None, timeout: float = 60.0) -> None:
        if self.phase == "dead" or not self.alive:
            raise _error(503, "The Claude Code process has stopped.")
        _LOOP.run(self._send(cmd, arg), timeout)

    def query(self, blocks: list[dict]) -> None:
        self.command("query", blocks)
        self.phase = "streaming"
        self.last_used = time.monotonic()

    def give_results(self, results: dict[str, dict]) -> None:
        for tid in list(self.expect):
            payload = results.get(tid) or {
                "content": [{"type": "text", "text": "The tool call was not run (stopped in Jarvis)."}],
                "is_error": True}
            _LOOP.call(self._resolve, tid, payload)
        self.expect = []
        self.phase = "streaming"
        self.last_used = time.monotonic()

    def _post(self, cmd: str) -> None:
        def go() -> None:
            if self.alive and self._cmds is not None:
                asyncio.ensure_future(self._send(cmd))
        try:
            _LOOP.call(go)
        except Exception:
            pass

    def interrupt(self) -> None:
        if self.phase in ("streaming", "starting") and self.alive:
            self.interrupted = True
            self._post("interrupt")

    def drain_interrupted(self, timeout: float = 20.0) -> bool:
        """After Esc: read what's left of the stopped reply (up to its result)
        so the next request starts clean. False when the process is gone."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                item = self.q.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is _END or isinstance(item, _Failure):
                return False
            if type(item).__name__ == "ResultMessage":
                self.phase = "idle"
                return True
        return False

    def close(self) -> None:
        self.phase = "dead"
        self._post("close")

    def stderr_tail(self) -> str:
        return "\n".join(list(self.stderr)[-6:]).strip()


async def _one(blocks: list[dict]):
    yield {"type": "user", "message": {"role": "user", "content": blocks}, "parent_tool_use_id": None}


def _start_failure(e: BaseException, sess: _Session | None = None) -> ClaudeAgentError:
    text = str(e) or type(e).__name__
    tail = sess.stderr_tail() if sess else ""
    low = (text + " " + tail).lower()
    if "not found" in low and ("cli" in low or "claude" in low) and "conversation" not in low:
        return _error(424, f"Claude Code wasn't found. Install it: `{cfg.CLI_INSTALL_CMD}`")
    if "login" in low or "not logged in" in low or "authenticat" in low:
        return _error(401, f"Claude Code isn't signed in. Run `{cfg.LOGIN_CMD}` in a terminal.")
    return _error(500, f"Claude Code didn't start: {_trim(text, 300)}" + (f"\n{tail}" if tail else ""))


# ─── Routing requests to processes ────────────────────────────────────────


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
            if s.sig == want and not s.busy and s.phase != "starting":
                return s, last
        return None, last

    def _evict(self) -> None:
        live = [s for s in self.sessions if s.phase != "dead"]
        if len(live) < MAX_PROCESSES:
            return
        idle = sorted((s for s in live if s.phase == "idle" and not s.busy), key=lambda s: s.last_used)
        for s in idle[: len(live) - MAX_PROCESSES + 1]:
            s.close()  # dormant: resumable through its saved session

    def _prune(self) -> None:
        # Dead sessions are kept (for resume) but not forever.
        dead = [s for s in self.sessions if s.phase == "dead" and not s.busy]
        for s in dead[:-32]:
            self.sessions.remove(s)

    def _ensure_janitor(self) -> None:
        if self._janitor is None or not self._janitor.is_alive():
            self._janitor = threading.Thread(target=self._tidy, daemon=True, name="jarvis-claude-agent-tidy")
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
                        s.sig = None  # waiting on calls nobody answers: don't resume it

    def _spawn(self, spec: Spec, *, resume: str = "") -> _Session:
        sess = _Session(self, spec, resume=resume, factory=self.factory)
        sess.busy = True
        with self.lock:
            self._evict()
            self.sessions.append(sess)
            self._prune()
        self._ensure_janitor()
        try:
            sess.start()
        except BaseException:
            self._release(sess, dead=True)
            raise
        return sess

    def open(self, spec: Spec, messages: list[dict]) -> "_Stream":
        if self.factory is None and not cfg.is_usable():
            raise not_ready_error()
        if not self.slots.acquire(timeout=600):
            raise _error(429, "Too many Claude Code requests at once — try again in a moment.")
        try:
            stream = self._open(spec, messages)
        except BaseException:
            self.slots.release()
            raise
        return stream

    def _open(self, spec: Spec, messages: list[dict]) -> "_Stream":
        with self.lock:
            sess, last = self._match(messages)
            if sess is not None:
                sess.busy = True
        if sess is not None:
            tail = messages[last + 1:][sess.fed:]
            try:
                return self._continue(sess, spec, tail, messages)
            except ClaudeAgentError:
                raise
            except BaseException:
                self._release(sess, dead=True)
                # The process went away: start over with the history.
        return self._fresh(spec, messages)

    def _continue(self, sess: _Session, spec: Spec, tail: list[dict], messages: list[dict]) -> "_Stream":
        if sess.phase == "tools":
            results, extra = _split_results(tail)
            if extra:
                _append_extra(results, sess.expect, extra)
            sess.give_results(results)
            sess.fed += len(tail)
            return _Stream(self, sess, spec, messages)
        if not _user_blocks(tail):
            raise RuntimeError("nothing new to send")
        if sess.interrupted and sess.phase != "dead" and not sess.drain_interrupted():
            sess.close()
        if sess.phase == "dead" or sess.spec.key != spec.key:
            # Restart with the new prompt / tools / model and resume Claude
            # Code's saved session (falls back to a checkpoint if it's gone).
            old = sess
            old.close()
            if not old.claude_session_id:
                raise RuntimeError("no session to resume")
            with self.lock:
                old.busy = False
            sess = self._spawn(spec, resume=old.claude_session_id)
            sess.sig, sess.fed, sess.interrupted = old.sig, old.fed, old.interrupted
            old.sig = None  # the new process carries the conversation on
        blocks = _user_blocks(tail)
        if sess.interrupted:
            blocks.insert(0, {"type": "text", "text": "[The user stopped your previous reply.]"})
            sess.interrupted = False
        sess.query(blocks)
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
            sess.query(first_blocks(messages))
        except BaseException:
            self._release(sess, dead=True)
            raise
        return _Stream(self, sess, spec, messages, fresh=True)

    def restart_fresh(self, old: _Session, spec: Spec, messages: list[dict]) -> _Session:
        """The process died before saying anything: once more, from scratch."""
        self._release(old, dead=True)
        sess = self._spawn(spec)
        self._anchor_new(sess, messages)
        sess.query(first_blocks(messages))
        return sess

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


def first_blocks(messages: list[dict]) -> list[dict]:
    """What a new process gets: the trailing user messages as they are, and
    everything before them as one checkpoint."""
    i = len(messages)
    while i > 0 and messages[i - 1].get("role") == "user" and not any(
            b.get("type") == "tool_result" for b in _blocks(messages[i - 1].get("content"))):
        i -= 1
    head, tail = messages[:i], messages[i:]
    blocks = _user_blocks(tail)
    if head:
        cp = render_checkpoint(head)
        if not blocks:
            blocks = [{"type": "text", "text": "Continue from where the conversation left off."}]
        blocks.insert(0, {"type": "text", "text": cp})
    return blocks or [{"type": "text", "text": "Continue."}]


def _split_results(tail: list[dict]) -> tuple[dict[str, dict], list[str]]:
    results: dict[str, dict] = {}
    extra: list[str] = []
    for m in tail:
        if m.get("role") != "user":
            continue
        for b in _blocks(m.get("content")):
            if b.get("type") == "tool_result":
                results[str(b.get("tool_use_id") or "")] = {
                    "content": _mcp_content(b.get("content")), "is_error": bool(b.get("is_error"))}
            elif b.get("type") == "text" and str(b.get("text") or "").strip():
                extra.append(b["text"])
    return results, extra


def _append_extra(results: dict[str, dict], expect: list[str], extra: list[str]) -> None:
    """Text sent alongside tool results ("send now" messages) can't be its own
    message mid-loop: it rides on the last result Claude reads."""
    target = next((t for t in reversed(expect) if t in results), None)
    if target is None and expect:
        target = expect[-1]
        results[target] = {"content": [], "is_error": False}
    if target is None:
        return
    results[target]["content"] = list(results[target]["content"]) + [
        {"type": "text", "text": "\n\n" + "\n\n".join(extra)}]


# ─── The stream Jarvis reads ──────────────────────────────────────────────


class _Stream:
    """Mimics the Anthropic SDK's MessageStream (``delta_stream``,
    ``text_stream``, ``get_final_message``, ``close``)."""

    def __init__(self, engine: _Engine, sess: _Session, spec: Spec, messages: list[dict], *, fresh: bool = False):
        self.engine = engine
        self.sess = sess
        self.spec = spec
        self.messages = messages
        self.fresh = fresh
        self._final = None
        self._closed = False
        self._finished = False
        self._yielded = False
        self._restarted = False
        self._content: list[_ContentBlock] = []
        self._blocks: dict[int, dict] = {}
        self._msg_tools: list[str] = []
        self._stop = ""
        self._usage = NS(input_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                         output_tokens=0, total_tokens=0)
        self._out_total = 0
        self._error: ClaudeAgentError | None = None
        self._rate_note = ""
        self._timeout = float(os.getenv("HARNESS_CLAUDE_AGENT_TIMEOUT", "0") or 0) or 900.0
        self._once = threading.Lock()  # Esc (UI thread) can race the worker finishing

    def _take_finish(self) -> bool:
        """True exactly once: whoever gets it releases the session and slot."""
        with self._once:
            if self._finished:
                return False
            self._finished = True
            return True

    # ── events ──

    def _on_event(self, e: dict) -> Generator[tuple[str, Any], None, bool]:
        t = e.get("type")
        if t == "message_start":
            self._blocks = {}
            self._msg_tools = []
            self._stop = ""
            u = (e.get("message") or {}).get("usage") or {}
            self._set_usage(u, final=False)
        elif t == "content_block_start":
            idx = int(e.get("index") or 0)
            cb = dict(e.get("content_block") or {})
            cb["_json"] = ""
            self._blocks[idx] = cb
            if cb.get("type") == "tool_use" and str(cb.get("name") or "").startswith(PREFIX):
                yield "tool_input", (idx, strip_prefix(cb["name"]), "")
        elif t == "content_block_delta":
            idx = int(e.get("index") or 0)
            cb = self._blocks.get(idx)
            d = e.get("delta") or {}
            dt = d.get("type")
            if dt == "text_delta" and d.get("text"):
                if cb is not None:
                    cb["text"] = str(cb.get("text") or "") + d["text"]
                yield "text", d["text"]
            elif dt == "thinking_delta" and d.get("thinking"):
                if cb is not None:
                    cb["thinking"] = str(cb.get("thinking") or "") + d["thinking"]
                yield "thinking", d["thinking"]
            elif dt == "input_json_delta" and cb is not None:
                cb["_json"] += d.get("partial_json") or ""
                name = str(cb.get("name") or "")
                if name.startswith(PREFIX):
                    yield "tool_input", (idx, strip_prefix(name), d.get("partial_json") or "")
        elif t == "content_block_stop":
            idx = int(e.get("index") or 0)
            cb = self._blocks.get(idx)
            if cb is not None:
                self._close_block(cb)
        elif t == "message_delta":
            self._stop = str((e.get("delta") or {}).get("stop_reason") or "")
            self._set_usage(e.get("usage") or {}, final=True)
        elif t == "message_stop":
            if self._stop == "tool_use" and self._msg_tools:
                self._end("tool_use")
                return True
        return False

    def _close_block(self, cb: dict) -> None:
        typ = cb.get("type")
        if typ == "text" and str(cb.get("text") or ""):
            self._content.append(_TextBlock(type="text", text=cb["text"]))
        elif typ == "thinking" and str(cb.get("thinking") or "").strip():
            self._content.append(_ContentBlock(type="thinking", thinking=cb["thinking"]))
        elif typ == "tool_use":
            name = str(cb.get("name") or "")
            if not name.startswith(PREFIX):
                return  # Claude Code answers unknown tools itself
            raw = cb.get("_json") or ""
            try:
                args = json.loads(raw) if raw.strip() else dict(cb.get("input") or {})
            except ValueError:
                args = {"__stream_error__": "Claude streamed invalid JSON arguments for this tool. Retry the call."}
            if not isinstance(args, dict):
                args = {"__stream_error__": "Tool arguments were not a JSON object. Retry the call."}
            self._content.append(_ToolUseBlock(type="tool_use", id=str(cb.get("id") or ""),
                                               name=strip_prefix(name), input=args))
            self._msg_tools.append(str(cb.get("id") or ""))

    def _set_usage(self, u: dict, *, final: bool) -> None:
        if not u:
            return
        self._usage.input_tokens = int(u.get("input_tokens") or 0)
        self._usage.cache_read_input_tokens = int(u.get("cache_read_input_tokens") or 0)
        self._usage.cache_creation_input_tokens = int(u.get("cache_creation_input_tokens") or 0)
        if final:
            self._out_total += int(u.get("output_tokens") or 0)
        self._usage.output_tokens = self._out_total
        self._usage.total_tokens = (self._usage.input_tokens + self._usage.cache_read_input_tokens
                                    + self._usage.cache_creation_input_tokens + self._out_total)

    def _end(self, stop: str) -> None:
        if not self._take_finish():
            return
        sess = self.sess
        if stop == "tool_use":
            sess.expect = list(self._msg_tools)
            sess.phase = "tools"
        else:
            sess.phase = "idle"
        final = NS(content=list(self._content), usage=self._usage, stop_reason=stop or "end_turn",
                   model=self.spec.model)
        sess.sig = sig_of({"content": final.content})
        sess.fed = 0
        self._final = final
        self.engine.finish(sess)

    def _fail(self, err: ClaudeAgentError) -> None:
        if self._take_finish():
            self.engine._release(self.sess, dead=True)
            self.engine.slots.release()
        raise err

    def _assistant_error(self, m: Any) -> None:
        kind = str(getattr(m, "error", "") or "")
        if not kind:
            return
        text = "".join(getattr(b, "text", "") or "" for b in (getattr(m, "content", None) or [])).strip()
        status = _ERROR_STATUS.get(kind, 500)
        if status == 401:
            text = (text or "Claude Code isn't signed in.") + f" Run `{cfg.LOGIN_CMD}` in a terminal."
        if status == 429 and self._rate_note:
            text = f"{text} {self._rate_note}".strip()
        self._error = _error(status, text or kind.replace("_", " "))

    def _on_result(self, m: Any) -> None:
        if getattr(m, "is_error", False) and not self._content:
            if self._error is None:
                errors = getattr(m, "errors", None) or []
                text = "; ".join(str(x) for x in errors) or str(getattr(m, "result", "") or "") or str(
                    getattr(m, "subtype", "error"))
                status = int(getattr(m, "api_error_status", 0) or 0) or 500
                low = text.lower()
                if "prompt is too long" in low or "context" in low and "limit" in low:
                    status = 400
                self._error = _error(status, text)
            self._fail(self._error)
        stop = str(getattr(m, "stop_reason", "") or "") or self._stop or "end_turn"
        if stop not in ("end_turn", "max_tokens", "stop_sequence", "refusal", "pause_turn"):
            stop = "end_turn"
        self._end(stop)

    # ── reading ──

    def _next(self, deadline: float) -> Any:
        while True:
            if self._closed:
                raise KeyboardInterrupt("stream cancelled")
            try:
                return self.sess.q.get(timeout=0.25)
            except queue.Empty:
                if time.monotonic() > deadline:
                    self.sess.interrupt()
                    self._fail(_error(504, f"Claude Code sent nothing for {int(self._timeout)}s."))

    @property
    def delta_stream(self) -> Generator[tuple[str, Any], None, None]:
        deadline = time.monotonic() + self._timeout
        while not self._finished:
            item = self._next(deadline)
            deadline = time.monotonic() + self._timeout
            if item is _END or isinstance(item, _Failure):
                if self._finished:
                    break
                if not self._yielded and not self._restarted and not self.fresh:
                    # The process ended before answering (it was resumed from a
                    # session Claude Code no longer has, or crashed): once more
                    # from scratch with the whole history.
                    self._restarted = True
                    try:
                        self.sess = self.engine.restart_fresh(self.sess, self.spec, self.messages)
                    except ClaudeAgentError as e:
                        self._fail(e)
                    except Exception as e:  # noqa: BLE001
                        self._fail(_start_failure(e, self.sess))
                    continue
                if self._error is not None:
                    self._fail(self._error)
                exc = item.exc if isinstance(item, _Failure) else None
                self._fail(_start_failure(exc, self.sess) if exc is not None else
                           _error(502, "Claude Code stopped before finishing the reply."
                                  + (f"\n{self.sess.stderr_tail()}" if self.sess.stderr_tail() else "")))
            name = type(item).__name__
            if name == "StreamEvent":
                if getattr(item, "parent_tool_use_id", None):
                    continue
                gen = self._on_event(item.event or {})
                try:
                    while True:
                        kind, val = next(gen)
                        self._yielded = True
                        yield kind, val
                except StopIteration as stop:
                    if stop.value:
                        break
            elif name == "AssistantMessage":
                self._assistant_error(item)
            elif name == "RateLimitEvent":
                info = getattr(item, "rate_limit_info", None)
                resets = getattr(info, "resets_at", None) if info is not None else None
                if resets:
                    try:
                        self._rate_note = "Resets " + time.strftime("%H:%M", time.localtime(float(resets))) + "."
                    except (TypeError, ValueError):
                        pass
            elif name == "ResultMessage":
                self._on_result(item)
                break
        if self._final is None and not self._closed:
            self._end(self._stop or "end_turn")

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
            return NS(content=list(self._content), usage=self._usage, stop_reason="end_turn", model=self.spec.model)
        return self._final

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._take_finish():
            # Esc: stop Claude Code mid-reply; it keeps the partial reply and
            # the next request continues the same session.
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


atexit.register(shutdown)


class _Messages:
    def __init__(self, owner: "ClaudeAgentClient"):
        self._owner = owner

    def stream(self, *, model: str, messages: list[dict], tools: list[dict] | None = None,
               system: Any = None, thinking: dict | None = None, **_ignored: Any) -> _Stream:
        spec = build_spec(model=model, system=system, tools=tools, thinking=thinking)
        return self._owner.engine.open(spec, [_d(m) for m in messages])


class ClaudeAgentClient:
    """``state.client`` for the ``claude_agent`` provider."""

    def __init__(self, *, engine_: _Engine | None = None):
        self.engine = engine_ or engine()
        self.messages = _Messages(self)

    def validate(self) -> bool:
        return cfg.is_ready()

    def close(self) -> None:
        pass  # processes are shared by every client; they stop when idle


# ─── Model list ───────────────────────────────────────────────────────────


def fetch_server_info(timeout: float = 60.0) -> dict | None:
    """Claude Code's initialize answer (commands, models …) — starts the
    binary once and stops it. No model request is made."""
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    env = dict(cfg.child_env())

    async def run() -> dict | None:
        opts = ClaudeAgentOptions(cli_path=cfg.status().cli_path or cfg.find_cli(), tools=[],
                                  setting_sources=[], strict_mcp_config=True, env=env,
                                  permission_mode="dontAsk")
        client = ClaudeSDKClient(opts)
        await client.connect()
        try:
            return await client.get_server_info()
        finally:
            await asyncio.wait_for(client.disconnect(), 8)

    return _LOOP.run(run(), timeout)
