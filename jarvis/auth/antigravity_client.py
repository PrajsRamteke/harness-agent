"""Google Antigravity client — Gemini / Claude through Cloud Code Assist.

A drop-in for the Anthropic client (``.messages.stream``), like the Codex and
Ollama clients: the Anthropic-shaped history becomes Gemini ``contents``, the
request is wrapped the way the Antigravity app sends it
(``{project, model, request, requestType: "agent", …}``) and posted to
``/v1internal:streamGenerateContent?alt=sse``; the SSE answer is re-shaped
into OpenAI-style chunks for ``_OpenCodeStream``, so text, thinking, tool
calls and usage are built exactly as for every other provider.

Requests carry the signed-in user's own token (refreshed when it nears expiry,
and once more on a 401) and try the Cloud Code hosts in order when one is
busy or down. Errors are raised as ``openai.APIStatusError`` so
``repl/stream.py`` retries / reports them like any OpenAI-style provider.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from types import SimpleNamespace as NS
from typing import Any

import httpx
from openai import APIStatusError

from ..constants.antigravity_oauth import (
    ANTIGRAVITY_API_CLIENT,
    ANTIGRAVITY_ENDPOINTS,
    ANTIGRAVITY_SKIP_THOUGHT_SIGNATURE,
    ANTIGRAVITY_SYSTEM_PREAMBLE,
)
from ..utils.tool_images import split_tool_result
from . import antigravity_catalog as catalog
from .antigravity_oauth import client_metadata, get_fresh_antigravity_token, platform_name
from .http_timeout import http_read_timeout_seconds
from .opencode_client import _ContentBlock, _OpenCodeStream

CLAUDE_THINKING_MAX_OUTPUT = 64000
PLACEHOLDER = "_placeholder"


class AntigravityAPIError(APIStatusError):
    """An error answer from Cloud Code (status + Google's ``{"error": …}`` body)."""


def _error(resp: httpx.Response, body: Any = None, message: str = "") -> AntigravityAPIError:
    if body is None:
        try:
            resp.read()
            body = resp.json()
        except Exception:
            try:
                body = {"error": {"message": resp.text[:600]}}
            except Exception:
                body = None
    if not message:
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            message = str(err.get("message") or err.get("status") or "")
        message = message or f"HTTP {resp.status_code}"
    return AntigravityAPIError(f"Error code: {resp.status_code} - {message}", response=resp, body=body)


def _synthetic(status: int, message: str, url: str = "https://cloudcode-pa.googleapis.com") -> AntigravityAPIError:
    resp = httpx.Response(status, request=httpx.Request("POST", url),
                          json={"error": {"code": status, "message": message}})
    return _error(resp, message=message)


def request_headers(access_token: str, *, model: str = "") -> dict:
    """Headers the Antigravity app sends (its current version — the backend
    refuses outdated clients)."""
    arch = "arm64" if platform_name() == "MACOS" else "amd64"
    os_name = "darwin" if platform_name() == "MACOS" else "windows"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": f"antigravity/{catalog.version()} {os_name}/{arch}",
        "X-Goog-Api-Client": ANTIGRAVITY_API_CLIENT,
        "Client-Metadata": json.dumps(client_metadata(), separators=(",", ":")),
    }
    if "claude" in model and "thinking" in model:
        headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"
    return headers


# ── JSON schema → what Gemini / Claude (VALIDATED mode) accept ───────────────

_DROP_KEYS = {
    "$schema", "$defs", "definitions", "$ref", "$id", "$comment", "additionalProperties",
    "propertyNames", "title", "const", "examples", "default", "patternProperties",
    "unevaluatedProperties", "dependentRequired", "dependentSchemas", "if", "then", "else",
    "not", "contains", "minContains", "maxContains", "readOnly", "writeOnly", "deprecated",
}
_HINT_KEYS = ("minLength", "maxLength", "pattern", "minItems", "maxItems", "format",
              "exclusiveMinimum", "exclusiveMaximum", "uniqueItems", "multipleOf")


def _hint(schema: dict, text: str) -> None:
    desc = str(schema.get("description") or "")
    schema["description"] = f"{desc} ({text})" if desc else text


def clean_schema(schema: Any, *, placeholder: bool = False) -> Any:
    """A tool's JSON schema in the subset Cloud Code takes: no ``$ref`` /
    ``anyOf`` / ``const`` / type lists; constraints become description hints;
    ``placeholder`` gives an empty object one property (Claude needs one)."""
    if isinstance(schema, list):
        return [clean_schema(s, placeholder=placeholder) for s in schema]
    if not isinstance(schema, dict):
        return schema
    s = dict(schema)
    if "const" in s and "enum" not in s:
        s["enum"] = [s["const"]]
    if "$ref" in s:
        _hint(s, f"see {str(s['$ref']).split('/')[-1]}")
        s.setdefault("type", "object")
    for key in ("allOf",):
        if isinstance(s.get(key), list):
            merged: dict = {}
            for part in s.pop(key):
                if isinstance(part, dict):
                    merged.setdefault("properties", {}).update(part.get("properties") or {})
                    merged.setdefault("required", []).extend(part.get("required") or [])
                    if part.get("type"):
                        merged.setdefault("type", part["type"])
            for k, v in merged.items():
                if k == "properties":
                    s.setdefault("properties", {}).update(v)
                elif k == "required":
                    s["required"] = list(dict.fromkeys([*(s.get("required") or []), *v]))
                else:
                    s.setdefault(k, v)
    for key in ("anyOf", "oneOf"):
        options = s.pop(key, None)
        if isinstance(options, list):
            real = [o for o in options if isinstance(o, dict) and o.get("type") != "null"]
            pick = real[0] if real else {"type": "string"}
            if len(real) > 1:
                kinds = [str(o.get("type") or "object") for o in real]
                _hint(s, "accepts: " + " | ".join(dict.fromkeys(kinds)))
            for k, v in pick.items():
                if k == "description" and s.get("description"):
                    continue
                s.setdefault(k, v)
    if isinstance(s.get("type"), list):
        types = [t for t in s["type"] if t and t != "null"]
        if len(types) > 1:
            _hint(s, "accepts: " + " | ".join(types))
        s["type"] = types[0] if types else "string"
    for key in _HINT_KEYS:
        if key in s and not isinstance(s[key], (dict, list)):
            _hint(s, f"{key}: {s[key]}")
            s.pop(key, None)
    for key in _DROP_KEYS:
        s.pop(key, None)
    if isinstance(s.get("enum"), list):
        s["enum"] = [str(v) for v in s["enum"] if v is not None]
        s.setdefault("type", "string")
        if s["type"] != "string":
            s["type"] = "string"
    if isinstance(s.get("properties"), dict):
        s["properties"] = {k: clean_schema(v, placeholder=placeholder) for k, v in s["properties"].items()}
    if "items" in s:
        s["items"] = clean_schema(s["items"], placeholder=placeholder)
    if isinstance(s.get("required"), list):
        props = s.get("properties") or {}
        req = [r for r in s["required"] if r in props]
        if req:
            s["required"] = req
        else:
            s.pop("required")
    if s.get("type") == "object" and placeholder and not s.get("properties"):
        s["properties"] = {PLACEHOLDER: {"type": "boolean", "description": "Placeholder. Always pass true."}}
        s["required"] = [PLACEHOLDER]
    return s


def to_tools(tools: list[dict] | None, *, claude: bool) -> list[dict]:
    decls = []
    for t in tools or []:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        params = t.get("input_schema") or {"type": "object", "properties": {}}
        decls.append({
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": clean_schema(params, placeholder=claude),
        })
    return [{"functionDeclarations": decls}] if decls else []


# ── Anthropic history → Gemini contents ──────────────────────────────────────


def _as_dict(block: Any) -> dict:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return dict(getattr(block, "__dict__", {}) or {})


def _inline(block: dict) -> dict | None:
    src = block.get("source") or {}
    if src.get("type") != "base64" or not src.get("data"):
        return None
    default = "application/pdf" if block.get("type") == "document" else "image/png"
    return {"inlineData": {"mimeType": src.get("media_type") or default, "data": src["data"]}}


def system_text(system: Any) -> str:
    if not system:
        return ""
    if isinstance(system, str):
        return system
    return "\n".join(
        str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in system
    ).strip()


def to_contents(messages: list[dict], *, model: str) -> list[dict]:
    """Gemini ``contents`` for an Anthropic-shaped history.

    Claude's signed thinking blocks are replayed as thought parts (it needs
    them in a tool loop); other thinking is dropped. Gemini 3 function calls carry the
    "skip validation" signature, since history keeps none of Google's."""
    gemini = not ("claude" in model)
    names: dict[str, str] = {}
    out: list[dict] = []

    def push(role: str, parts: list[dict]) -> None:
        if not parts:
            return
        if out and out[-1]["role"] == role:
            out[-1]["parts"].extend(parts)
        else:
            out.append({"role": role, "parts": parts})

    for msg in messages or []:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            if content:
                push("model" if role == "assistant" else "user", [{"text": content}])
            continue
        if role == "assistant":
            parts: list[dict] = []
            for raw in content or []:
                b = _as_dict(raw)
                btype = b.get("type")
                if btype == "thinking" and not gemini and b.get("signature"):
                    parts.append({"text": b.get("thinking") or "", "thought": True,
                                  "thoughtSignature": b["signature"]})
                elif btype == "text" and b.get("text"):
                    parts.append({"text": b["text"]})
                elif btype == "tool_use":
                    tid = str(b.get("id") or "")
                    names[tid] = str(b.get("name") or "")
                    call: dict = {"name": b.get("name") or "", "args": b.get("input") or {}}
                    if tid:
                        call["id"] = tid
                    part: dict = {"functionCall": call}
                    if gemini:
                        part["thoughtSignature"] = ANTIGRAVITY_SKIP_THOUGHT_SIGNATURE
                    parts.append(part)
            push("model", parts)
            continue
        parts = []
        after: list[dict] = []  # images from tool results go after every response
        for raw in content or []:
            b = _as_dict(raw)
            btype = b.get("type")
            if btype == "text" and b.get("text"):
                parts.append({"text": b["text"]})
            elif btype in ("image", "document"):
                inline = _inline(b)
                if inline:
                    parts.append(inline)
            elif btype == "tool_result":
                tid = str(b.get("tool_use_id") or "")
                text, images = split_tool_result(b.get("content", ""))
                if b.get("is_error"):
                    text = f"Error: {text}" if text else "Error"
                resp: dict = {"name": names.get(tid) or "tool",
                              "response": {"output": text or ("(image below)" if images else "")}}
                if tid:
                    resp["id"] = tid
                parts.append({"functionResponse": resp})
                for img in images:
                    inline = _inline(img)
                    if inline:
                        after.append(inline)
        if after:
            parts.append({"text": "Image(s) returned by the tool call above:"})
            parts.extend(after)
        push("user", parts)
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "parts": [{"text": "(continuing)"}]})
    return out


def _tool_loop_signed(contents: list[dict]) -> bool:
    """Claude with thinking needs the last model turn of a tool loop to start
    with a signed thought; False when history can't give it one."""
    for c in reversed(contents):
        if c["role"] != "model":
            continue
        has_call = any("functionCall" in p for p in c["parts"])
        if not has_call:
            return True
        return bool(c["parts"]) and bool(c["parts"][0].get("thought")) and bool(
            c["parts"][0].get("thoughtSignature"))
    return True


def _in_tool_loop(contents: list[dict]) -> bool:
    return bool(contents) and contents[-1]["role"] == "user" and any(
        "functionResponse" in p for p in contents[-1]["parts"])


def _nearest(want: str, levels: tuple[str, ...]) -> str:
    order = ("minimal", "low", "medium", "high", "xhigh", "max")
    if want in levels:
        return want
    rank = order.index(want) if want in order else order.index("high")
    below = [lv for lv in levels if lv in order and order.index(lv) <= rank]
    return below[-1] if below else levels[0]


def generation_config(model: str, *, max_tokens: int, thinking: dict | None,
                      contents: list[dict]) -> dict:
    """``generationConfig`` with this model's thinking form."""
    cfg: dict[str, Any] = {}
    info = catalog.get_model(model)
    limit = info.max_output if info and info.max_output else 0
    cfg["maxOutputTokens"] = int(min(max_tokens, limit) if limit else max_tokens)
    kind, levels, can_off = catalog.think_facts(model)
    on = bool(thinking) and thinking.get("type") == "enabled"
    want = str((thinking or {}).get("effort") or "high").lower()
    if kind == "gemini3":
        fixed = catalog.gemini3_fixed_level(model)
        if fixed:
            cfg["thinkingConfig"] = {"includeThoughts": True, "thinkingLevel": fixed}
        elif levels:
            level = _nearest(want, levels) if on else levels[0]
            cfg["thinkingConfig"] = {"includeThoughts": on, "thinkingLevel": level}
    elif kind == "claude" and on:
        if not _in_tool_loop(contents) or _tool_loop_signed(contents):
            budget = catalog.CLAUDE_BUDGETS[_nearest(want, tuple(catalog.CLAUDE_BUDGETS))]
            cfg["thinkingConfig"] = {"include_thoughts": True, "thinking_budget": budget}
            if cfg["maxOutputTokens"] <= budget:
                cfg["maxOutputTokens"] = CLAUDE_THINKING_MAX_OUTPUT
    elif kind == "budget":
        budgets = {"low": 4096, "medium": 8192, "high": 16384}
        if on:
            cfg["thinkingConfig"] = {"includeThoughts": True,
                                     "thinkingBudget": budgets[_nearest(want, tuple(budgets))]}
        elif can_off:
            cfg["thinkingConfig"] = {"includeThoughts": False, "thinkingBudget": 0}
    return cfg


def build_request(*, model: str, messages: list[dict], tools: list[dict] | None,
                  system: Any, max_tokens: int, thinking: dict | None,
                  project: str, session_id: str) -> dict:
    claude = "claude" in model
    contents = to_contents(messages, model=model)
    sys_text = system_text(system)
    request: dict[str, Any] = {
        "contents": contents,
        "systemInstruction": {
            "role": "user",
            "parts": [{"text": ANTIGRAVITY_SYSTEM_PREAMBLE + ("\n\n" + sys_text if sys_text else "")}],
        },
        "generationConfig": generation_config(model, max_tokens=max_tokens, thinking=thinking,
                                              contents=contents),
        "sessionId": session_id,
    }
    decls = to_tools(tools, claude=claude)
    if decls:
        request["tools"] = decls
        if claude:
            request["toolConfig"] = {"functionCallingConfig": {"mode": "VALIDATED"}}
    return {
        "project": project,
        "model": model,
        "request": request,
        "requestType": "agent",
        "userAgent": "antigravity",
        "requestId": f"agent-{uuid.uuid4()}",
    }


# ── SSE → OpenAI-style chunks ────────────────────────────────────────────────


class _Chunks:
    """``data: {"response": {candidates, usageMetadata}}`` lines as the chunks
    ``_OpenCodeStream`` reads. ``close()`` drops the HTTP response."""

    def __init__(self, response: httpx.Response, *, keep_signature: bool = False):
        self.response = response
        self._keep_signature = keep_signature

    def __iter__(self):
        index = 0
        try:
            for line in self.response.iter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if isinstance(obj, list):  # a JSON-array stream: one object per item
                    obj = obj[0] if obj else {}
                if isinstance(obj, dict) and obj.get("error"):
                    err = obj["error"] if isinstance(obj["error"], dict) else {"message": str(obj["error"])}
                    status = int(err.get("code") or 500) if str(err.get("code") or "").isdigit() else 500
                    fake = httpx.Response(status, request=self.response.request, json={"error": err})
                    raise _error(fake, {"error": err}, str(err.get("message") or "stream error"))
                resp = obj.get("response", obj) if isinstance(obj, dict) else {}
                cands = resp.get("candidates") or []
                parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
                text, thought, sig, calls = [], [], "", []
                for p in parts:
                    if not isinstance(p, dict):
                        continue
                    if self._keep_signature and p.get("thoughtSignature") and (
                            p.get("thought") or not p.get("text")) and "functionCall" not in p:
                        sig = str(p["thoughtSignature"])
                    if p.get("thought"):
                        thought.append(str(p.get("text") or ""))
                    elif "functionCall" in p:
                        fc = p["functionCall"] or {}
                        args = fc.get("args") or {}
                        if isinstance(args, dict):
                            args.pop(PLACEHOLDER, None)
                        calls.append(NS(index=index, id=str(fc.get("id") or f"call_{uuid.uuid4().hex[:16]}"),
                                        function=NS(name=str(fc.get("name") or ""),
                                                    arguments=json.dumps(args))))
                        index += 1
                    elif p.get("text"):
                        text.append(str(p["text"]))
                usage = None
                meta = resp.get("usageMetadata")
                if isinstance(meta, dict):
                    pin = int(meta.get("promptTokenCount") or 0)
                    pout = int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0)
                    usage = {"prompt_tokens": pin, "completion_tokens": pout,
                             "total_tokens": int(meta.get("totalTokenCount") or pin + pout)}
                delta = NS(content="".join(text) or None, reasoning="".join(thought) or None,
                           reasoning_content=None, tool_calls=calls or None, signature=sig or None)
                finish = cands[0].get("finishReason") if cands else None
                yield NS(choices=[NS(delta=delta, finish_reason=finish)], usage=usage)
        finally:
            self.close()

    def close(self) -> None:
        try:
            self.response.close()
        except Exception:
            pass


class _AntigravityStream(_OpenCodeStream):
    """``_OpenCodeStream`` that also keeps the thought signature (Claude needs
    it back on the thinking block of a tool loop)."""

    def __init__(self, response, *, read_timeout: float | None = None):
        super().__init__(response, read_timeout=read_timeout)
        self._signature = ""

    def _process_chunk(self, chunk):
        delta = chunk.choices[0].delta if getattr(chunk, "choices", None) else None
        sig = getattr(delta, "signature", None) if delta is not None else None
        if sig:
            self._signature = sig
        return super()._process_chunk(chunk)

    def _build_final(self):
        final = super()._build_final()
        if self._signature:
            for b in final.content:
                if b.get("type") == "thinking":
                    b.signature = self._signature
                    break
            else:
                # A signature with no thought text (Claude may send it alone).
                final.content.insert(0, _ContentBlock(type="thinking", thinking="",
                                                      signature=self._signature))
        return final


# ── client ───────────────────────────────────────────────────────────────────


def _session_key() -> str:
    from .. import state

    sid = getattr(state, "current_session_id", None)
    return f"jarvis-{_PROCESS_TAG}-{sid if sid is not None else 0}"


_PROCESS_TAG = uuid.uuid4().hex[:12]

# Answers worth trying the next Cloud Code host for.
_NEXT_HOST = {404, 429, 500, 502, 503, 504, 529}


class _AntigravityMessages:
    def __init__(self, owner: "AntigravityClient"):
        self._owner = owner

    def _send(self, payload: dict, model: str) -> httpx.Response:
        o = self._owner
        tokens = get_fresh_antigravity_token()
        if not tokens:
            raise _synthetic(401, "Not signed in to Antigravity — sign in again with /login")
        refreshed = False
        last: Exception | None = None
        hosts = list(o.endpoints)
        i = 0
        while i < len(hosts):
            base = hosts[i]
            url = f"{base}/v1internal:streamGenerateContent?alt=sse"
            headers = request_headers(str(tokens.get("access_token") or ""), model=model)
            headers["Accept"] = "text/event-stream"
            try:
                resp = o._http.send(o._http.build_request("POST", url, json=payload, headers=headers),
                                    stream=True)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last = e
                i += 1
                continue
            if resp.status_code == 200:
                return resp
            if resp.status_code == 401 and not refreshed:
                resp.close()
                refreshed = True
                tokens = get_fresh_antigravity_token(force=True)
                if not tokens:
                    raise _synthetic(401, "Your Antigravity sign-in expired — sign in again with /login")
                continue
            err = _error(resp)
            resp.close()
            last = err
            if resp.status_code in _NEXT_HOST and i + 1 < len(hosts):
                i += 1
                continue
            raise err
        if isinstance(last, Exception):
            raise last
        raise _synthetic(503, "No Antigravity host answered")

    @contextlib.contextmanager
    def stream(self, *, model: str, messages: list[dict], tools: list[dict] | None = None,
               system: Any = None, max_tokens: int = 8192, thinking: dict | None = None,
               **_kwargs):
        tokens = get_fresh_antigravity_token() or {}
        payload = build_request(
            model=model, messages=messages, tools=tools, system=system,
            max_tokens=max_tokens, thinking=thinking,
            project=str(tokens.get("project_id") or ""), session_id=_session_key(),
        )
        resp = self._send(payload, model)
        chunks = _Chunks(resp, keep_signature="claude" in model)
        stream = _AntigravityStream(chunks, read_timeout=self._owner.read_timeout)
        try:
            yield stream
        finally:
            stream.close()


class AntigravityClient:
    """Drop-in Anthropic client replacement for Google Antigravity (OAuth)."""

    def __init__(self, *, endpoints: tuple[str, ...] = ANTIGRAVITY_ENDPOINTS,
                 read_timeout: float | None = None):
        self.endpoints = endpoints
        self.read_timeout = read_timeout if read_timeout is not None else http_read_timeout_seconds(openrouter=False)
        self._http = httpx.Client(timeout=httpx.Timeout(connect=20.0, read=self.read_timeout,
                                                        write=60.0, pool=10.0))
        self.messages = _AntigravityMessages(self)

    def validate(self) -> bool:
        return get_fresh_antigravity_token() is not None

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:
            pass
