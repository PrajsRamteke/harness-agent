"""A stand-in local model server for tests: Ollama's native API, or an
OpenAI-compatible server shaped like LM Studio.

    srv = FakeLocalServer("ollama").start()   # srv.url → http://127.0.0.1:<port>
    ...
    srv.stop()

Replies are scripted: a user message containing "use a tool" gets a call to
the first tool offered; a tool result gets "tool said: <result>"; anything
else gets "hello from <model> (ctx <num_ctx>)". Every chat request body is
kept in ``srv.requests``. ``srv.down()`` / ``srv.up()`` are not needed —
``stop()`` frees the port, so the server looks like it was quit.

Run directly to try Jarvis against it by hand:
    python tests/local_model_server.py ollama 11434
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OLLAMA_MODELS = [
    {"name": "qwen3:8b", "size": 5_200_000_000, "digest": "d-qwen3",
     "details": {"family": "qwen3", "parameter_size": "8.2B", "quantization_level": "Q4_K_M"},
     "caps": ["completion", "tools", "thinking"], "ctx": 40_960},
    {"name": "llava:7b", "size": 4_700_000_000, "digest": "d-llava",
     "details": {"family": "llama", "parameter_size": "7B", "quantization_level": "Q4_0"},
     "caps": ["completion", "vision"], "ctx": 4_096},
    {"name": "gpt-oss:20b", "size": 13_000_000_000, "digest": "d-gptoss",
     "details": {"family": "gptoss", "parameter_size": "20.9B", "quantization_level": "MXFP4"},
     "caps": ["completion", "tools", "thinking"], "ctx": 131_072},
    {"name": "nomic-embed-text:latest", "size": 274_000_000, "digest": "d-embed",
     "details": {"family": "nomic-bert", "parameter_size": "137M", "quantization_level": "F16"},
     "caps": ["embedding"], "ctx": 2_048},
]

LMSTUDIO_MODELS = [
    {"id": "qwen2.5-coder-14b-instruct", "type": "llm", "state": "loaded",
     "max_context_length": 32_768, "quantization": "Q4_K_M", "arch": "qwen2",
     "capabilities": ["tool_use"]},
    {"id": "gemma-3-12b", "type": "vlm", "state": "not-loaded",
     "max_context_length": 131_072, "quantization": "Q4_K_M", "arch": "gemma3"},
    {"id": "text-embedding-nomic-embed-text-v1.5", "type": "embeddings", "state": "not-loaded",
     "max_context_length": 2_048, "quantization": "Q4_K_M", "arch": "nomic-bert"},
]


def _reply_for(model: str, messages: list[dict], tools: list, ctx) -> tuple[str, dict | None]:
    last = messages[-1] if messages else {}
    if last.get("role") == "tool":
        return f"tool said: {last.get('content', '')}", None
    text = last.get("content") or ""
    if isinstance(text, list):
        text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))
    if "use a tool" in text and tools:
        fn = tools[0].get("function", tools[0])
        return "", {"name": fn.get("name", "tool"), "arguments": {"path": "README.md"}}
    return f"hello from {model} (ctx {ctx})", None


class FakeLocalServer:
    def __init__(self, kind: str = "ollama", *, api_key: str = "", fail_chat: int = 0,
                 no_tools: tuple[str, ...] = ("llava:7b",)):
        self.kind = kind
        self.api_key = api_key
        self.fail_chat = fail_chat      # answer chat with this HTTP status
        self.no_tools = set(no_tools)
        self.requests: list[dict] = []
        self.httpd: ThreadingHTTPServer | None = None

    @property
    def port(self) -> int:
        return self.httpd.server_address[1] if self.httpd else 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, port: int = 0) -> "FakeLocalServer":
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _json(self, code: int, body) -> None:
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _authed(self) -> bool:
                if not srv.api_key:
                    return True
                if self.headers.get("Authorization") == f"Bearer {srv.api_key}":
                    return True
                self._json(401, {"error": "unauthorized"})
                return False

            def _body(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    return json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return {}

            def do_GET(self):
                if not self._authed():
                    return
                p = self.path.split("?")[0]
                if srv.kind == "ollama":
                    if p == "/api/version":
                        return self._json(200, {"version": "0.12.3"})
                    if p == "/api/tags":
                        return self._json(200, {"models": [
                            {k: v for k, v in m.items() if k not in ("caps", "ctx")} | {"model": m["name"]}
                            for m in OLLAMA_MODELS]})
                    if p == "/api/ps":
                        return self._json(200, {"models": [{"name": "qwen3:8b", "context_length": 32768}]})
                    if p == "/v1/models":
                        return self._json(200, {"object": "list", "data": [
                            {"id": m["name"], "object": "model", "owned_by": "library"} for m in OLLAMA_MODELS]})
                else:
                    if p == "/v1/models":
                        return self._json(200, {"object": "list", "data": [
                            {"id": m["id"], "object": "model", "owned_by": "organization_owner"}
                            for m in LMSTUDIO_MODELS]})
                    if p == "/api/v0/models":
                        return self._json(200, {"object": "list", "data": LMSTUDIO_MODELS})
                self._json(404, {"error": "not found"})

            def do_POST(self):
                if not self._authed():
                    return
                p = self.path.split("?")[0]
                body = self._body()
                if srv.kind == "ollama" and p == "/api/show":
                    m = next((m for m in OLLAMA_MODELS if m["name"] == body.get("model")), None)
                    if m is None:
                        return self._json(404, {"error": "model not found"})
                    arch = m["details"]["family"]
                    return self._json(200, {"capabilities": m["caps"],
                                            "model_info": {"general.architecture": arch,
                                                           f"{arch}.context_length": m["ctx"]}})
                if srv.kind == "ollama" and p == "/api/chat":
                    return self._ollama_chat(body)
                if srv.kind != "ollama" and p == "/v1/chat/completions":
                    return self._openai_chat(body)
                self._json(404, {"error": "not found"})

            def _ollama_chat(self, body: dict) -> None:
                srv.requests.append(body)
                model = body.get("model", "")
                if srv.fail_chat:
                    return self._json(srv.fail_chat, {"error": "model requires more system memory"})
                if not any(m["name"] == model for m in OLLAMA_MODELS):
                    return self._json(404, {"error": f"model \"{model}\" not found, try pulling it first"})
                if body.get("tools") and model in srv.no_tools:
                    return self._json(400, {"error": f"registry.ollama.ai/library/{model} does not support tools"})
                text, call = _reply_for(model, body.get("messages") or [], body.get("tools") or [],
                                        (body.get("options") or {}).get("num_ctx"))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()

                def line(obj):
                    self.wfile.write((json.dumps(obj) + "\n").encode())
                    self.wfile.flush()

                if body.get("think"):
                    line({"model": model, "message": {"role": "assistant", "content": "", "thinking": "pondering"},
                          "done": False})
                if call:
                    line({"model": model, "message": {"role": "assistant", "content": "",
                                                      "tool_calls": [{"function": call}]}, "done": False})
                for word in text.split(" "):
                    line({"model": model, "message": {"role": "assistant", "content": word + " "}, "done": False})
                    time.sleep(0.001)
                line({"model": model, "message": {"role": "assistant", "content": ""}, "done": True,
                      "done_reason": "stop", "prompt_eval_count": 120, "eval_count": 7})

            def _openai_chat(self, body: dict) -> None:
                srv.requests.append(body)
                model = body.get("model", "")
                if srv.fail_chat:
                    return self._json(srv.fail_chat, {"error": {"message": "boom"}})
                text, call = _reply_for(model, body.get("messages") or [], body.get("tools") or [], None)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def chunk(delta, finish=None, usage=None):
                    obj = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": model,
                           "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    if usage:
                        obj["usage"] = usage
                    self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
                    self.wfile.flush()

                chunk({"role": "assistant", "content": ""})
                if call:
                    chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                           "function": {"name": call["name"], "arguments": ""}}]})
                    chunk({"tool_calls": [{"index": 0, "function": {"arguments": json.dumps(call["arguments"])}}]})
                for word in text.split(" ") if text else ():
                    chunk({"content": word + " "})
                chunk({}, finish="tool_calls" if call else "stop",
                      usage={"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "ollama"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else (11434 if kind == "ollama" else 1234)
    s = FakeLocalServer(kind).start(port)
    print(f"fake {kind} server on {s.url} — Ctrl+C to stop")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        s.stop()
