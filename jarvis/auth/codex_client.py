"""OpenAI Codex client — ChatGPT subscription backend via Responses API."""
from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from typing import Any, Generator, Optional

from openai import OpenAI

from ..constants.providers import CODEX_BASE_URL


def _block_as_dict(block) -> dict:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return {k: v for k, v in block.__dict__.items()}


def _system_text(system: str | list | None) -> str:
    if not system:
        return ""
    if isinstance(system, str):
        return system
    parts = []
    for block in system:
        if isinstance(block, dict):
            parts.append(block.get("text", ""))
        else:
            parts.append(str(block))
    return "\n".join(p for p in parts if p)


def _anthropic_tools_to_responses(tools: list[dict]) -> list[dict]:
    out = []
    for t in tools or []:
        out.append({
            "type": "function",
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
        })
    return out


def _anthropic_messages_to_responses_input(messages: list[dict]) -> list[dict]:
    """Best-effort Anthropic message list → Responses API ``input`` items."""
    items: list[dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            if content:
                items.append({"role": role, "content": content})
            continue
        if role == "user":
            text_parts: list[str] = []
            for raw in content:
                block = _block_as_dict(raw)
                btype = block.get("type", "")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        result_content = "\n".join(
                            b.get("text", "") if isinstance(b, dict) else str(b)
                            for b in result_content
                        )
                    items.append({
                        "type": "function_call_output",
                        "call_id": block.get("tool_use_id", ""),
                        "output": str(result_content),
                    })
            joined = "\n".join(p for p in text_parts if p)
            if joined:
                items.append({"role": "user", "content": joined})
        elif role == "assistant":
            text_parts = []
            for raw in content:
                block = _block_as_dict(raw)
                btype = block.get("type", "")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    items.append({
                        "type": "function_call",
                        "call_id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    })
            joined = "\n".join(p for p in text_parts if p)
            if joined:
                items.append({"role": "assistant", "content": joined})
    return items


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class _ContentBlock:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def model_dump(self) -> dict:
        if self.type == "text":
            return {"type": "text", "text": self.text}
        if self.type == "tool_use":
            return {
                "type": "tool_use",
                "id": self.id,
                "name": self.name,
                "input": self.input,
            }
        return {"type": self.type, "text": self.text}


@dataclass
class _FakeMessage:
    id: str = "codex-response"
    role: str = "assistant"
    type: str = "message"
    model: str = ""
    stop_reason: str = "end_turn"
    content: list = field(default_factory=list)
    usage: _Usage = field(default_factory=_Usage)


class _CodexStream:
    def __init__(self, response_iter, model: str):
        self._iter = response_iter
        self._model = model
        self._text_parts: list[str] = []
        self._tool_calls: dict[str, dict] = {}
        self._usage = _Usage()
        self._final: Optional[_FakeMessage] = None
        self._closed = False

    @property
    def text_stream(self) -> Generator[str, None, None]:
        for event in self._iter:
            etype = getattr(event, "type", "")
            if etype == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    self._text_parts.append(delta)
                    yield delta
            elif etype == "response.function_call_arguments.delta":
                item_id = getattr(event, "item_id", "") or ""
                delta = getattr(event, "delta", "") or ""
                slot = self._tool_calls.setdefault(
                    item_id,
                    {"id": item_id, "name": "", "arguments": ""},
                )
                slot["arguments"] += delta
            elif etype == "response.output_item.done":
                item = getattr(event, "item", None)
                if item is not None:
                    item_type = getattr(item, "type", "")
                    if item_type == "function_call":
                        item_id = getattr(item, "id", "") or getattr(item, "call_id", "")
                        self._tool_calls[item_id] = {
                            "id": item_id,
                            "name": getattr(item, "name", ""),
                            "arguments": getattr(item, "arguments", "") or "",
                        }
            elif etype == "response.completed":
                resp = getattr(event, "response", None)
                usage = getattr(resp, "usage", None) if resp is not None else None
                if usage is not None:
                    self._usage.input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
                    self._usage.output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

    def get_final_message(self) -> _FakeMessage:
        if self._final is not None:
            return self._final
        blocks: list[_ContentBlock] = []
        text = "".join(self._text_parts).strip()
        if text:
            blocks.append(_ContentBlock(type="text", text=text))
        for tc in self._tool_calls.values():
            args_raw = tc.get("arguments") or "{}"
            try:
                parsed = json.loads(args_raw)
            except json.JSONDecodeError:
                parsed = {}
            blocks.append(_ContentBlock(
                type="tool_use",
                id=tc.get("id") or "",
                name=tc.get("name") or "",
                input=parsed if isinstance(parsed, dict) else {},
            ))
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        self._final = _FakeMessage(
            model=self._model,
            stop_reason=stop,
            content=blocks,
            usage=self._usage,
        )
        return self._final

    def close(self) -> None:
        self._closed = True
        try:
            close = getattr(self._iter, "close", None)
            if callable(close):
                close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class _CodexMessages:
    def __init__(self, client: OpenAI):
        self._client = client

    @contextlib.contextmanager
    def stream(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | list | None = None,
        _max_tokens: int = 8192,
        thinking: dict | None = None,
        **_kwargs,
    ):
        instructions = _system_text(system)
        # Codex backend allowlist: no max_output_tokens, temperature, top_p, etc.
        payload: dict[str, Any] = {
            "model": model,
            "input": _anthropic_messages_to_responses_input(messages),
            "stream": True,
            "store": False,
        }
        if instructions:
            payload["instructions"] = instructions
        resp_tools = _anthropic_tools_to_responses(tools or [])
        if resp_tools:
            payload["tools"] = resp_tools
        if thinking and thinking.get("type") == "enabled":
            effort = str(thinking.get("effort") or "medium")
            payload["reasoning"] = {"effort": effort}

        response = self._client.responses.create(**payload)
        stream = _CodexStream(response, model)
        try:
            yield stream
        finally:
            stream.close()


class CodexClient:
    """Drop-in Anthropic client replacement for OpenAI Codex (ChatGPT OAuth)."""

    def __init__(self, access_token: str, *, base_url: str = CODEX_BASE_URL):
        self._access_token = access_token
        # OpenAI SDK uses api_key for Authorization: Bearer (not auth_token).
        self._oai = OpenAI(
            api_key=access_token,
            base_url=f"{base_url.rstrip('/')}/",
        )
        self.messages = _CodexMessages(self._oai)

    def validate(self) -> bool:
        return bool(self._access_token)
