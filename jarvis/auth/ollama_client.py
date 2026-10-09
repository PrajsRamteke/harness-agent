"""Ollama's native chat API behind the Anthropic-SDK shape the harness uses.

Why not Ollama's OpenAI endpoint: it has no way to set the context window,
and Ollama defaults to 4K tokens below 24 GB of VRAM — Jarvis's system prompt
and tool list alone are bigger, and Ollama cuts an over-long prompt silently.
``/api/chat`` takes ``options.num_ctx`` (and ``think``), so every request asks
for the window ``local_models.served_context`` worked out.

The conversion reuses the OpenAI-style client's pieces: Anthropic messages →
OpenAI messages (``_anthropic_messages_to_openai``) → Ollama's message shape,
and Ollama's NDJSON lines are re-shaped into OpenAI-style chunks for
``_OpenCodeStream``, which builds the final message (thinking, text,
tool_use blocks, usage) exactly as for every other OpenAI-style provider.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from types import SimpleNamespace as NS
from typing import Any, Callable

import httpx

from .opencode_client import (
    _OpenCodeStream,
    _anthropic_messages_to_openai,
    _anthropic_tools_to_openai,
)


class LocalServerError(Exception):
    """An error answer from a local server, shaped for ``repl/api_errors``
    (``status_code`` + an ``{"error": …}`` body)."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"Error code: {status_code} - {message}")
        self.status_code = status_code
        self.body = {"error": {"message": message}}
        self.message = message


def _system_text(system: Any) -> str:
    if not system:
        return ""
    if isinstance(system, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in system)
    return str(system)


def to_ollama_messages(messages: list[dict], system: Any = None) -> list[dict]:
    """Anthropic-style history → ``/api/chat`` messages.

    Images become ``images`` (bare base64), tool-call arguments objects (not
    JSON strings), and a tool result carries ``tool_name`` — Ollama matches
    results to calls by name.
    """
    out: list[dict] = []
    sys_text = _system_text(system)
    if sys_text:
        out.append({"role": "system", "content": sys_text})
    names: dict[str, str] = {}
    for m in _anthropic_messages_to_openai(messages):
        role = m.get("role", "user")
        content = m.get("content")
        msg: dict[str, Any] = {"role": role}
        images: list[str] = []
        if isinstance(content, list):
            texts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    texts.append(str(part.get("text") or ""))
                elif part.get("type") == "image_url":
                    url = str((part.get("image_url") or {}).get("url") or "")
                    if url.startswith("data:") and "," in url:
                        images.append(url.split(",", 1)[1])
            content = "\n".join(t for t in texts if t)
        msg["content"] = content if isinstance(content, str) else ""
        if images:
            msg["images"] = images
        if role == "assistant":
            calls = []
            for tc in m.get("tool_calls") or ():
                fn = tc.get("function") or {}
                raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except ValueError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                name = str(fn.get("name") or "")
                if tc.get("id"):
                    names[str(tc["id"])] = name
                calls.append({"id": tc.get("id") or "", "function": {"name": name, "arguments": args}})
            if calls:
                msg["tool_calls"] = calls
        elif role == "tool":
            cid = str(m.get("tool_call_id") or "")
            msg["tool_call_id"] = cid
            if names.get(cid):
                msg["tool_name"] = names[cid]
        out.append(msg)
    return out


class _Chunks:
    """Ollama NDJSON lines as OpenAI-style stream chunks (what
    ``_OpenCodeStream`` reads). ``close()`` drops the HTTP response."""

    def __init__(self, response: httpx.Response, client: httpx.Client):
        self.response = response
        self._client = client

    def __iter__(self):
        index = 0
        try:
            for line in self.response.iter_lines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get("error"):
                    raise LocalServerError(500, str(obj["error"]))
                msg = obj.get("message") or {}
                calls = []
                for tc in msg.get("tool_calls") or ():
                    fn = tc.get("function") or {}
                    args = fn.get("arguments")
                    calls.append(NS(
                        index=index,
                        id=tc.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                        function=NS(
                            name=str(fn.get("name") or ""),
                            arguments=args if isinstance(args, str) else json.dumps(args or {}),
                        ),
                    ))
                    index += 1
                delta = NS(
                    content=msg.get("content") or None,
                    reasoning=msg.get("thinking") or None,
                    reasoning_content=None,
                    tool_calls=calls or None,
                )
                usage = None
                if obj.get("done"):
                    pin, pout = int(obj.get("prompt_eval_count") or 0), int(obj.get("eval_count") or 0)
                    usage = {"prompt_tokens": pin, "completion_tokens": pout, "total_tokens": pin + pout}
                yield NS(choices=[NS(delta=delta, finish_reason=obj.get("done_reason"))], usage=usage)
        finally:
            self.close()

    def close(self) -> None:
        try:
            self.response.close()
        except Exception:
            pass


def _error_text(resp: httpx.Response) -> str:
    try:
        resp.read()
        body = resp.json()
        if isinstance(body, dict) and body.get("error"):
            return str(body["error"])
    except Exception:
        pass
    try:
        return resp.text[:400] or f"HTTP {resp.status_code}"
    except Exception:
        return f"HTTP {resp.status_code}"


class _OllamaMessages:
    def __init__(self, owner: "OllamaClient"):
        self._owner = owner

    def _send(self, payload: dict) -> httpx.Response:
        o = self._owner
        req = o._http.build_request("POST", f"{o.base_url}/api/chat", json=payload)
        resp = o._http.send(req, stream=True)
        if resp.status_code != 200:
            text = _error_text(resp)
            resp.close()
            raise LocalServerError(resp.status_code, text)
        return resp

    @contextlib.contextmanager
    def stream(self, *, model: str, messages: list[dict], tools: list[dict] | None = None,
               system: str | list | None = None, max_tokens: int = 8192,
               thinking: dict | None = None, **_kwargs):
        o = self._owner
        options: dict[str, Any] = {}
        ctx = o.num_ctx(model)
        if ctx:
            options["num_ctx"] = int(ctx)
        predict = o.num_predict(model)
        options["num_predict"] = int(min(max_tokens, predict) if predict else max_tokens)
        payload: dict[str, Any] = {
            "model": model,
            "messages": to_ollama_messages(messages, system),
            "stream": True,
            "options": options,
            "keep_alive": o.keep_alive,
        }
        if tools and model not in o.no_tools:
            payload["tools"] = _anthropic_tools_to_openai(tools)
        thinks, levels, _can_off = o.think(model)
        if thinks and model not in o.no_think and thinking:
            if thinking.get("type") == "enabled":
                effort = str(thinking.get("effort") or "").lower()
                payload["think"] = (effort if effort in levels else (levels[-1] if levels else True)) \
                    if levels else True
            elif thinking.get("type") == "disabled" and not levels:
                payload["think"] = False
        try:
            resp = self._send(payload)
        except LocalServerError as e:
            low = e.message.lower()
            if e.status_code == 400 and "tools" in payload and "does not support tools" in low:
                # A chat-only model: answer without tools rather than fail
                # every turn (it's labelled "no tool use" in /model).
                o.no_tools.add(model)
                payload.pop("tools")
                resp = self._send(payload)
            elif e.status_code == 400 and "think" in payload and "thinking" in low:
                o.no_think.add(model)
                payload.pop("think")
                resp = self._send(payload)
            else:
                raise
        stream = _OpenCodeStream(_Chunks(resp, o._http), read_timeout=o.read_timeout)
        try:
            yield stream
        finally:
            stream.close()


class OllamaClient:
    """Drop-in for the Anthropic client (``.messages.stream``) on ``/api/chat``.

    ``num_ctx`` / ``num_predict`` / ``think`` are per-model callables so a
    model switch, a context change in the UI or a fresh scan applies to the
    next request without rebuilding the client.
    """

    def __init__(
        self,
        base_url: str,
        *,
        key: str = "",
        num_ctx: Callable[[str], int | None] = lambda _m: None,
        num_predict: Callable[[str], int | None] = lambda _m: None,
        think: Callable[[str], tuple] = lambda _m: (None, (), True),
        read_timeout: float = 900.0,
        keep_alive: str = "30m",
        label: str = "Ollama",
    ):
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.think = think
        self.read_timeout = read_timeout
        self.keep_alive = keep_alive
        self.label = label
        self.no_tools: set[str] = set()
        self.no_think: set[str] = set()
        headers = {"User-Agent": "harness-agent/1.0"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._http = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=read_timeout, write=60.0, pool=5.0),
            headers=headers,
        )
        self.messages = _OllamaMessages(self)

    def validate(self) -> bool:
        return True

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:
            pass
