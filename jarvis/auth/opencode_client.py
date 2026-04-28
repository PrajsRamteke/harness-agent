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
import contextlib
from dataclasses import dataclass, field
from typing import Any, Generator, Optional

# _ContentBlock replaces the old @dataclass approach so blocks support
# both attribute access (render.py) and .get()/.model_dump() (trim.py / JSON).

from openai import OpenAI

from ..constants import OPENCODE_BASE_URL


# ---------------------------------------------------------------------------
# Fake Anthropic-style data classes
# ---------------------------------------------------------------------------

@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0


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
                # DeepSeek thinking mode requires reasoning_content for
                # assistant messages that participated in a tool-call turn.
                # Old sessions (pre-reasoning-support) may be missing the
                # thinking block — provide empty string to satisfy the API.
                msg_out["reasoning_content"] = ""
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

    reasoning_content = getattr(msg, "reasoning_content", None)
    if reasoning_content:
        content.append(_ContentBlock(type="thinking", thinking=reasoning_content))
    if msg.content:
        content.append(_TextBlock(type="text", text=msg.content))

    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            content.append(_ToolUseBlock(
                type="tool_use",
                id=tc.id,
                name=tc.function.name,
                input=args,
            ))

    usage = _Usage(
        input_tokens=getattr(response.usage, "prompt_tokens", 0),
        output_tokens=getattr(response.usage, "completion_tokens", 0),
    )
    stop_reason = "tool_use" if msg.tool_calls else "end_turn"
    return _FakeMessage(content=content, usage=usage, stop_reason=stop_reason)


# ---------------------------------------------------------------------------
# Stream adapter
# ---------------------------------------------------------------------------

class _OpenCodeStream:
    """Mimics Anthropic SDK MessageStream interface."""

    def __init__(self, response):
        self._response = response
        self._final: Optional[_FakeMessage] = None
        self._closed = False
        # Accumulated across streaming — written by _drain(), read by get_final_message()
        self._collected_text: list[str] = []
        self._collected_reasoning: list[str] = []
        self._collected_tool_calls: dict[int, dict] = {}
        self._usage_obj = None
        self._chunk_queue: queue.Queue = queue.Queue()
        self._reader_started = False

    def _start_reader(self) -> None:
        if self._reader_started:
            return
        self._reader_started = True
        t = threading.Thread(target=self._read_chunks_into_queue, daemon=True)
        t.start()

    def _read_chunks_into_queue(self) -> None:
        """Daemon thread: push every chunk onto the queue, then None sentinel."""
        try:
            for chunk in self._response:
                self._chunk_queue.put(chunk)
        except Exception:
            pass
        finally:
            self._chunk_queue.put(None)

    def _process_chunk(self, chunk) -> Optional[str]:
        """Extract text delta and accumulate tool call fragments. Returns text or None."""
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta is None:
            return None
        if hasattr(chunk, "usage") and chunk.usage:
            self._usage_obj = chunk.usage
        text = None
        reasoning_content = getattr(delta, "reasoning_content", None)
        if reasoning_content:
            self._collected_reasoning.append(reasoning_content)
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
        return text

    def _build_final(self) -> _FakeMessage:
        content: list = []
        if self._collected_reasoning:
            content.append(_ContentBlock(type="thinking", thinking="".join(self._collected_reasoning)))
        if self._collected_text:
            content.append(_TextBlock(type="text", text="".join(self._collected_text)))
        for idx in sorted(self._collected_tool_calls):
            tc = self._collected_tool_calls[idx]
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            content.append(_ToolUseBlock(type="tool_use", id=tc["id"], name=tc["name"], input=args))
        usage = _Usage(
            input_tokens=getattr(self._usage_obj, "prompt_tokens", 0) if self._usage_obj else 0,
            output_tokens=getattr(self._usage_obj, "completion_tokens", 0) if self._usage_obj else 0,
        )
        stop_reason = "tool_use" if any(b.get("type") == "tool_use" for b in content) else "end_turn"
        return _FakeMessage(content=content, usage=usage, stop_reason=stop_reason)

    @property
    def text_stream(self) -> Generator[str, None, None]:
        """Yield text deltas. Reader runs in a daemon thread; _closed checked every 50ms.
        Raises KeyboardInterrupt when cancelled so the caller loop exits immediately."""
        self._start_reader()
        while True:
            if self._closed:
                raise KeyboardInterrupt("stream cancelled")
            try:
                chunk = self._chunk_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if chunk is None:
                break
            text = self._process_chunk(chunk)
            if text:
                yield text
        self._final = self._build_final()

    def get_final_message(self) -> _FakeMessage:
        if self._final is not None:
            return self._final
        if self._closed:
            # Cancelled — return whatever was collected so far
            self._final = self._build_final()
            return self._final
        # Non-streaming path: drain the queue ourselves
        self._start_reader()
        while True:
            if self._closed:
                self._final = self._build_final()
                return self._final
            try:
                chunk = self._chunk_queue.get(timeout=0.05)
            except queue.Empty:
                continue
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
    def __init__(self, oai_client: OpenAI):
        self._client = oai_client

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

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        # Pass thinking/reasoning mode (used by DeepSeek V4, etc.)
        if thinking and thinking.get("type") == "enabled":
            kwargs["reasoning_effort"] = "max"  # agent-grade reasoning
            kwargs.setdefault("extra_body", {})
            kwargs["extra_body"]["thinking"] = {"type": "enabled"}

        oai_tools = _anthropic_tools_to_openai(tools) if tools else []
        if oai_tools:
            kwargs["tools"] = oai_tools

        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            err = str(e)
            if oai_tools and ("invalid_request" in err or "tool" in err.lower() or "function" in err.lower()):
                # Model doesn't support tool use — retry without tools
                kwargs.pop("tools", None)
                response = self._client.chat.completions.create(**kwargs)
            else:
                raise

        stream = _OpenCodeStream(response)
        try:
            yield stream
        finally:
            stream.close()


# ---------------------------------------------------------------------------
# Top-level client (mimics Anthropic client)
# ---------------------------------------------------------------------------

class OpenCodeClient:
    """Drop-in Anthropic client replacement for OpenCode Go provider."""

    def __init__(self, api_key: str):
        self._oai = OpenAI(
            api_key=api_key,
            base_url=f"{OPENCODE_BASE_URL}/",
        )
        self.messages = _OpenCodeMessages(self._oai)

    def validate(self) -> bool:
        """Light validation — just ensure the key is non-empty."""
        return True
