"""OpenCode Go client adapter.

Wraps the OpenAI SDK to expose an Anthropic-SDK-compatible interface so the
rest of the harness (stream.py, render.py) needs zero changes.

Translates:
  Anthropic messages  →  OpenAI chat messages
  Anthropic tools     →  OpenAI function tools
  OpenAI response     →  Anthropic-style Message / content blocks
"""
from __future__ import annotations

import json
import queue
import threading
import time
import contextlib
from dataclasses import dataclass, field
from typing import Any, Generator, Optional

from .http_timeout import harness_http_timeout, http_read_timeout_seconds


def _get_usage_value(usage_obj, attr_name: str, default: int = 0) -> int:
    """Safely extract a token count from a usage object that may be a
    pydantic model (OpenAI SDK), a raw dict (some OpenCode providers),
    or None.  Handles all three transparently."""
    if usage_obj is None:
        return default
    if isinstance(usage_obj, dict):
        val = usage_obj.get(attr_name, default)
    else:
        val = getattr(usage_obj, attr_name, default)
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _repair_truncated_json(raw: str) -> Optional[dict]:
    """Best-effort recovery of a JSON object whose stream was cut mid-flight.

    Streaming tool-call arguments occasionally get truncated when a provider
    hits its output-token cap mid-string (typical when writing a large file).
    The accumulated buffer looks like::

        {"path": "index.html", "content": "<!DOCTYPE html><html...

    — a valid prefix with an unterminated string and unclosed braces. This
    function walks the buffer tracking string/brace state, then appends the
    minimum suffix needed to make it parseable. Returns the parsed dict if
    repair succeeds, or None otherwise.
    """
    if not raw or not raw.lstrip().startswith("{"):
        return None
    in_string = False
    escape = False
    stack: list[str] = []  # tracks '{' / '[' nesting
    for ch in raw:
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()
    repaired = raw
    if in_string:
        # Drop a trailing backslash that would re-open the escape state, then close the string.
        if repaired.endswith("\\"):
            repaired = repaired[:-1]
        repaired += '"'
    # Close any open arrays/objects, innermost first.
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    try:
        parsed = json.loads(repaired)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None

# _ContentBlock replaces the old @dataclass approach so blocks support
# both attribute access (render.py) and .get()/.model_dump() (trim.py / JSON).

from openai import OpenAI

from ..constants import OPENCODE_BASE_URL


OPENCODE_THINK_OFF_EFFORT = "none"
OPENCODE_ALLOWED_THINK_EFFORTS = {"xhigh", "high", "medium", "low", "minimal", "none"}
# Map Jarvis-internal effort labels to values the provider API actually accepts.
_EFFORT_API_MAP = {"xhigh": "high", "minimal": "low"}


_MINIMAX_MODEL_PREFIXES = ("minimax-", "minimax/", "miniomax-")


def _is_minimax_model(model: str) -> bool:
    """True when *model* is a MiniMax-powered model."""
    m = (model or "").strip().lower()
    return any(m.startswith(p) for p in _MINIMAX_MODEL_PREFIXES)


def _opencode_reasoning_options(model: str, thinking: dict | None) -> dict[str, Any]:
    """Translate Jarvis think mode to OpenCode-compatible reasoning options.

    Some providers (MiniMax) require ``reasoning_split: True`` in the body
    to properly separate thinking/reasoning from visible content in streaming
    responses — without it the reasoning leaks into ``content`` deltas and
    shows up inside the normal assistant message.
    """
    if not thinking:
        return {}

    mode = thinking.get("type")
    if mode == "enabled":
        effort = str(thinking.get("effort") or "high").lower()
        if effort not in OPENCODE_ALLOWED_THINK_EFFORTS or effort == "none":
            effort = "high"
        effort = _EFFORT_API_MAP.get(effort, effort)
        opts: dict[str, Any] = {"reasoning_effort": effort}
        # MiniMax models need reasoning_split to separate thinking from content
        # in streaming — without it the reasoning bleeds into text content.
        if _is_minimax_model(model):
            opts["extra_body"] = {"reasoning_split": True}
        return opts
    if mode == "disabled":
        return {"reasoning_effort": OPENCODE_THINK_OFF_EFFORT}
    return {}


# ---------------------------------------------------------------------------
# Fake Anthropic-style data classes
# ---------------------------------------------------------------------------

@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class _ContentBlock:
    """A content block that supports both attribute access (for render.py) and
    model_dump() / dict serialisation (for trim.py / JSON encoding)."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def model_dump(self) -> dict:
        return dict(self.__dict__)

    # dict-like helpers so trim.py's isinstance(b, dict) fallback still works
    def get(self, key, default=None):
        return self.__dict__.get(key, default)

    def __getitem__(self, key):
        return self.__dict__[key]

    def __contains__(self, key):
        return key in self.__dict__


def _TextBlock(**kwargs) -> _ContentBlock:
    kwargs.setdefault("type", "text")
    return _ContentBlock(**kwargs)


def _ToolUseBlock(**kwargs) -> _ContentBlock:
    kwargs.setdefault("type", "tool_use")
    kwargs.setdefault("id", "")
    kwargs.setdefault("name", "")
    kwargs.setdefault("input", {})
    return _ContentBlock(**kwargs)


@dataclass
class _FakeMessage:
    content: list = field(default_factory=list)
    usage: _Usage = field(default_factory=_Usage)
    stop_reason: str = "end_turn"


# ---------------------------------------------------------------------------
# Format converters
# ---------------------------------------------------------------------------

def _anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    out = []
    for t in tools:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return out


def _block_as_dict(block) -> dict:
    """Normalise a content block to a plain dict (handles Anthropic SDK objects, our dataclasses, and raw dicts)."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    # Dataclass fallback
    return {k: v for k, v in block.__dict__.items()}


def _anthropic_messages_to_openai(messages: list[dict]) -> list[dict]:
    """Convert Anthropic-style messages list to OpenAI format."""
    out = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        # content is a list of blocks
        if role == "user":
            text_parts = []
            tool_results = []
            for raw_block in content:
                block = _block_as_dict(raw_block)
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        result_content = "\n".join(
                            b.get("text", "") if isinstance(b, dict) else str(b)
                            for b in result_content
                        )
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": str(result_content),
                    })
                else:
                    # plain string block
                    if isinstance(raw_block, str):
                        text_parts.append(raw_block)
            if text_parts:
                out.append({"role": "user", "content": "\n".join(text_parts)})
            out.extend(tool_results)

        elif role == "assistant":
            text_parts = []
            tool_calls = []
            reasoning_parts = []
            for raw_block in content:
                block = _block_as_dict(raw_block)
                btype = block.get("type", "")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "thinking":
                    reasoning_parts.append(block.get("thinking", ""))
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    })
                elif isinstance(raw_block, str):
                    text_parts.append(raw_block)
            msg_out: dict[str, Any] = {"role": "assistant"}
            joined = "\n".join(text_parts)
            if joined:
                msg_out["content"] = joined
            if reasoning_parts:
                msg_out["reasoning_content"] = "\n".join(reasoning_parts)
            elif tool_calls:
                # Kimi K2.6 (Moonshot) and other thinking-mode providers require
                # NON-EMPTY `reasoning_content` on assistant messages that
                # contain tool_calls.  Empty string `""` is treated as missing
                # and causes 400: "thinking is enabled but reasoning_content is
                # missing in assistant tool call message at index N".
                # Use a single space as placeholder when real thinking is absent.
                msg_out["reasoning_content"] = " "
            if tool_calls:
                msg_out["tool_calls"] = tool_calls
            if "content" not in msg_out and not tool_calls:
                msg_out["content"] = ""
            out.append(msg_out)

        else:
            out.append({"role": role, "content": str(content)})

    return out


def _openai_response_to_anthropic(response) -> _FakeMessage:
    choice = response.choices[0]
    msg = choice.message
    content: list = []

    reasoning_content = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
    if reasoning_content:
        content.append(_ContentBlock(type="thinking", thinking=reasoning_content))
    if msg.content:
        content.append(_TextBlock(type="text", text=msg.content))

    if msg.tool_calls:
        for tc in msg.tool_calls:
            raw_args = tc.function.arguments or ""
            if not raw_args.strip():
                args = {"__stream_error__": (
                    f"Provider returned the {tc.function.name or 'tool'} call with EMPTY "
                    f"arguments. Retry the same tool with all required arguments populated."
                )}
            else:
                try:
                    args = json.loads(raw_args)
                    if not isinstance(args, dict):
                        args = {"__stream_error__": (
                            f"Tool arguments parsed to {type(args).__name__}, not a JSON object. "
                            f"Raw: {raw_args[:300]}. Retry with a valid JSON object."
                        )}
                except json.JSONDecodeError as e:
                    repaired = _repair_truncated_json(raw_args)
                    if repaired is not None:
                        args = repaired
                    else:
                        args = {"__stream_error__": (
                            f"Provider returned invalid JSON arguments "
                            f"({e.msg} at pos {e.pos}); auto-repair also failed. "
                            f"Likely cause: response hit the output-token cap mid-write. "
                            f"For large files, write a skeleton first then edit_file to append."
                        )}
            content.append(_ToolUseBlock(
                type="tool_use",
                id=tc.id,
                name=tc.function.name,
                input=args,
            ))

    usage = _Usage(
        input_tokens=_get_usage_value(response.usage, "prompt_tokens"),
        output_tokens=_get_usage_value(response.usage, "completion_tokens"),
        total_tokens=_get_usage_value(response.usage, "total_tokens"),
    )
    stop_reason = "tool_use" if msg.tool_calls else "end_turn"
    return _FakeMessage(content=content, usage=usage, stop_reason=stop_reason)


# ---------------------------------------------------------------------------
# Stream adapter
# ---------------------------------------------------------------------------

_STREAM_END = object()


class _OpenCodeStream:
    """Mimics Anthropic SDK MessageStream interface."""

    def __init__(self, response, *, read_timeout: float | None = None):
        self._response = response
        self._read_timeout = read_timeout if read_timeout is not None else http_read_timeout_seconds(openrouter=True)
        self._final: Optional[_FakeMessage] = None
        self._closed = False
        # Accumulated across streaming — written by _drain(), read by get_final_message()
        self._collected_text: list[str] = []
        self._collected_reasoning: list[str] = []
        self._collected_tool_calls: dict[int, dict] = {}
        self._usage_obj = None
        self._chunk_queue: queue.Queue = queue.Queue()
        self._reader_started = False
        self._reader_error: BaseException | None = None

    def _start_reader(self) -> None:
        if self._reader_started:
            return
        self._reader_started = True
        t = threading.Thread(target=self._read_chunks_into_queue, daemon=True)
        t.start()

    def _read_chunks_into_queue(self) -> None:
        """Daemon thread: push every chunk onto the queue, then end sentinel."""
        try:
            for chunk in self._response:
                if self._closed:
                    break
                self._chunk_queue.put(chunk)
        except Exception as exc:
            self._reader_error = exc
        finally:
            self._chunk_queue.put(_STREAM_END)

    def _take_next_chunk(self, *, last_progress: float) -> tuple[Any, float]:
        """Return the next queue item or raise on stall / reader failure."""
        while True:
            if self._closed:
                raise KeyboardInterrupt("stream cancelled")
            try:
                item = self._chunk_queue.get(timeout=0.5)
            except queue.Empty:
                idle = time.monotonic() - last_progress
                if idle >= self._read_timeout:
                    self.close()
                    raise TimeoutError(
                        f"Stream stalled for {int(idle)}s (limit {int(self._read_timeout)}s)"
                    )
                continue
            if item is _STREAM_END:
                if self._reader_error is not None:
                    raise self._reader_error
                return None, last_progress
            return item, time.monotonic()

    def _process_chunk(self, chunk) -> tuple[Optional[str], Optional[str]]:
        """Extract text/reasoning deltas and accumulate tool call fragments."""
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta is None:
            return None, None
        # Usage on streaming chunks lives at the root chunk level, NOT on delta.
        # Some providers emit usage on every chunk; others only on the final chunk.
        # Always overwrite so the LAST chunk with usage wins (final totals).
        if hasattr(chunk, "usage") and chunk.usage is not None:
            self._usage_obj = chunk.usage
        text = None
        reasoning = None
        reasoning_content = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if reasoning_content:
            self._collected_reasoning.append(reasoning_content)
            reasoning = reasoning_content
        if delta.content:
            self._collected_text.append(delta.content)
            text = delta.content
        if delta.tool_calls:
            for tc_chunk in delta.tool_calls:
                idx = tc_chunk.index
                if idx not in self._collected_tool_calls:
                    self._collected_tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                entry = self._collected_tool_calls[idx]
                if tc_chunk.id:
                    entry["id"] = tc_chunk.id
                if tc_chunk.function:
                    if tc_chunk.function.name:
                        entry["name"] += tc_chunk.function.name
                    if tc_chunk.function.arguments:
                        entry["arguments"] += tc_chunk.function.arguments
        return text, reasoning

    def _build_final(self) -> _FakeMessage:
        content: list = []
        if self._collected_reasoning:
            content.append(_ContentBlock(type="thinking", thinking="".join(self._collected_reasoning)))
        if self._collected_text:
            content.append(_TextBlock(type="text", text="".join(self._collected_text)))
        for idx in sorted(self._collected_tool_calls):
            tc = self._collected_tool_calls[idx]
            raw_args = tc["arguments"] or ""
            if not raw_args.strip():
                args = {"__stream_error__": (
                    f"Provider streamed the {tc.get('name') or 'tool'} call with EMPTY "
                    f"arguments — none of the required parameters arrived. Retry the "
                    f"same tool with all required arguments populated."
                )}
            else:
                try:
                    args = json.loads(raw_args)
                    if not isinstance(args, dict):
                        args = {"__stream_error__": (
                            f"Tool arguments parsed to {type(args).__name__}, not a JSON object. "
                            f"Raw: {raw_args[:300]}. Retry with a valid JSON object."
                        )}
                except json.JSONDecodeError as e:
                    repaired = _repair_truncated_json(raw_args)
                    if repaired is not None:
                        args = repaired
                    else:
                        args = {"__stream_error__": (
                            f"Provider streamed truncated/invalid JSON arguments "
                            f"({e.msg} at pos {e.pos}); auto-repair also failed. "
                            f"Likely cause: response hit the output-token cap mid-write. "
                            f"For large files, write in chunks: create a small skeleton "
                            f"first, then use edit_file to append sections."
                        )}
            content.append(_ToolUseBlock(type="tool_use", id=tc["id"], name=tc["name"], input=args))
        usage = _Usage(
            input_tokens=_get_usage_value(self._usage_obj, "prompt_tokens"),
            output_tokens=_get_usage_value(self._usage_obj, "completion_tokens"),
            total_tokens=_get_usage_value(self._usage_obj, "total_tokens"),
        )
        stop_reason = "tool_use" if any(b.get("type") == "tool_use" for b in content) else "end_turn"
        return _FakeMessage(content=content, usage=usage, stop_reason=stop_reason)

    @property
    def delta_stream(self) -> Generator[tuple[str, str], None, None]:
        """Yield ``(kind, chunk)`` pairs where *kind* is ``thinking`` or ``text``."""
        self._start_reader()
        last_progress = time.monotonic()
        while True:
            chunk, last_progress = self._take_next_chunk(last_progress=last_progress)
            if chunk is None:
                break
            text, reasoning = self._process_chunk(chunk)
            if reasoning:
                yield "thinking", reasoning
            if text:
                yield "text", text
        self._final = self._build_final()

    @property
    def text_stream(self) -> Generator[str, None, None]:
        """Yield text deltas. Reader runs in a daemon thread; _closed checked every 50ms.
        Raises KeyboardInterrupt when cancelled so the caller loop exits immediately."""
        for kind, chunk in self.delta_stream:
            if kind == "text":
                yield chunk

    def get_final_message(self) -> _FakeMessage:
        if self._final is not None:
            return self._final
        if self._closed:
            # Cancelled — return whatever was collected so far
            self._final = self._build_final()
            return self._final
        # Non-streaming path: drain the queue ourselves
        self._start_reader()
        last_progress = time.monotonic()
        while True:
            if self._closed:
                self._final = self._build_final()
                return self._final
            chunk, last_progress = self._take_next_chunk(last_progress=last_progress)
            if chunk is None:
                break
            self._process_chunk(chunk)
        self._final = self._build_final()
        return self._final

    def close(self):
        self._closed = True
        try:
            self._response.close()
        except Exception:
            pass
        # Also close the underlying httpx response if accessible, to unblock
        # any thread blocked inside `for chunk in self._response`.
        try:
            http_resp = getattr(self._response, "response", None)
            if http_resp is not None:
                http_resp.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ---------------------------------------------------------------------------
# Messages namespace (mimics client.messages)
# ---------------------------------------------------------------------------

class _OpenCodeMessages:
    def __init__(self, oai_client: OpenAI, owner: "OpenCodeClient | None" = None):
        self._client = oai_client
        self._owner = owner

    def _create_completion(self, **kwargs):
        extra = self._owner.next_request_headers() if self._owner else None
        if extra:
            return self._client.chat.completions.create(**kwargs, extra_headers=extra)
        return self._client.chat.completions.create(**kwargs)

    @contextlib.contextmanager
    def stream(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | list | None = None,
        max_tokens: int = 8192,
        thinking: dict | None = None,
        **_kwargs,
    ):
        oai_messages = []
        # Convert system prompt
        if system:
            if isinstance(system, list):
                sys_text = "\n".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in system
                )
            else:
                sys_text = str(system)
            oai_messages.append({"role": "system", "content": sys_text})
        oai_messages.extend(_anthropic_messages_to_openai(messages))

        # DeepSeek, Kimi (Moonshot), and other thinking-mode providers require
        # `reasoning_content` on EVERY assistant message in the conversation.
        # Kimi (Moonshot) specifically requires NON-EMPTY `reasoning_content`
        # on assistant messages that have tool_calls — empty string `""` is
        # treated as missing.  Some models enable thinking by default at the
        # API level regardless of whether we request it, so this guard runs
        # unconditionally.  Without this, the API returns 400:
        #   "thinking is enabled but reasoning_content is missing in assistant
        #    tool call message at index N"
        for msg in oai_messages:
            if msg["role"] == "assistant" and "reasoning_content" not in msg:
                # Must be non-empty for tool-call messages (Kimi K2.6).
                msg["reasoning_content"] = " " if msg.get("tool_calls") else ""

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        # Pass thinking/reasoning mode using values accepted by OpenCode Go/Zen:
        # xhigh, high, medium, low, minimal, none.
        reasoning_options = _opencode_reasoning_options(model, thinking)
        if reasoning_options:
            kwargs.update(reasoning_options)

        oai_tools = _anthropic_tools_to_openai(tools) if tools else []
        if oai_tools:
            kwargs["tools"] = oai_tools

        try:
            response = self._create_completion(**kwargs)
        except Exception as e:
            err = str(e)
            # Don't silently swallow reasoning_content errors — they need
            # the above fix, not a tool-removal retry.
            if oai_tools and ("reasoning_content" not in err.lower() and
                              ("invalid_request" in err or
                               "tool" in err.lower() or
                               "function" in err.lower())):
                # Model doesn't support tool use — retry without tools
                kwargs.pop("tools", None)
                response = self._create_completion(**kwargs)
            else:
                raise

        stream = _OpenCodeStream(
            response,
            read_timeout=http_read_timeout_seconds(openrouter=True),
        )
        try:
            yield stream
        finally:
            stream.close()


# ---------------------------------------------------------------------------
# Top-level client (mimics Anthropic client)
# ---------------------------------------------------------------------------

class OpenCodeClient:
    """Drop-in Anthropic client replacement for OpenAI-compatible providers.

    Supports both OpenCode Go (default) and OpenCode Zen (via base_url override).
    Optional ``default_headers`` and per-request ``request_id_header`` for Zen.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
        *,
        request_id_header: str | None = None,
        request_id_prefix: str = "msg_",
    ):
        self._request_id_header = request_id_header
        self._request_id_prefix = request_id_prefix
        self._request_seq = 0
        hdrs = dict(default_headers or {})
        self._oai = OpenAI(
            api_key=api_key,
            base_url=base_url or f"{OPENCODE_BASE_URL}/",
            default_headers=hdrs or None,
            timeout=harness_http_timeout(openrouter=True),
        )
        self.messages = _OpenCodeMessages(self._oai, owner=self)

    def next_request_headers(self) -> dict[str, str] | None:
        if not self._request_id_header:
            return None
        self._request_seq += 1
        return {self._request_id_header: f"{self._request_id_prefix}{self._request_seq}"}

    def validate(self) -> bool:
        """Light validation — just ensure the key is non-empty."""
        return True
