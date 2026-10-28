"""Bun-backed httpx transport for the OpenCode Zen free tier (internal).

The ``/inference/v1`` gateway serves the free models only to requests that
carry the real OpenCode client's fingerprint. Part of that fingerprint lives
below the HTTP layer: a Python/OpenSSL client is rejected with a synthetic
``429 FreeUsageLimitError`` even when the body, headers and session id are
byte-identical to a request the CLI just made successfully from the same
machine at the same second.

OpenCode ships as a Bun binary, and ``BUN_BE_BUN=1`` turns that same binary
into a Bun runtime. This module uses it as a dumb HTTP pipe: every request is
handed to a tiny Bun script over stdin, and the raw response is streamed back
over stdout. Nothing else about the request changes — the OpenAI SDK still
builds the payload, parses the SSE stream and raises its usual exceptions, so
Jarvis keeps its own tool loop.

Falls back to ``None`` when no Bun-capable binary is available; callers then
keep the plain transport.
"""
from __future__ import annotations

import base64
import json
import os
import select
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import httpx

# Default seconds a stream may stay silent before the transport gives up. The
# REPL has its own stall detection, but it lives in the consumer thread and
# cannot fire while this transport is blocked, so enforce it here too.
_READ_TIMEOUT = float(os.environ.get("HARNESS_BUN_READ_TIMEOUT", "300"))

# Chunk size for reads from the child's stdout.
_CHUNK = 65536

_SHIM_JS = r"""
const job = JSON.parse(await Bun.stdin.text());
const out = Bun.stdout.writer();
const enc = new TextEncoder();
const fail = async (msg) => {
  await out.write(enc.encode(JSON.stringify({status: 599, headers: [], error: String(msg)}) + "\n"));
  await out.flush();
  process.exit(1);
};
try {
  const body = job.body ? Uint8Array.from(atob(job.body), c => c.charCodeAt(0)) : undefined;
  const res = await fetch(job.url, {
    method: job.method,
    headers: job.headers,
    body,
    redirect: "manual",
  });
  await out.write(enc.encode(JSON.stringify({status: res.status, headers: [...res.headers]}) + "\n"));
  await out.flush();
  if (res.body) {
    const reader = res.body.getReader();
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      await out.write(value);
      await out.flush();
    }
  }
  process.exit(0);
} catch (e) {
  await fail(e);
}
"""

# Headers that describe the *connection*, not the request: httpx/urllib set
# their own, and forwarding them makes Bun reject or mis-frame the request.
_DROP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "accept-encoding",
    "transfer-encoding",
}


@lru_cache(maxsize=1)
def _shim_path() -> Path:
    path = Path(tempfile.gettempdir()) / "jarvis_zen_bun_shim.js"
    try:
        if not path.exists() or path.read_text(encoding="utf-8") != _SHIM_JS:
            path.write_text(_SHIM_JS, encoding="utf-8")
    except OSError:
        pass
    return path


@lru_cache(maxsize=1)
def _bun_argv() -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]] | None:
    """Return ``(argv, extra_env)`` for a Bun runtime, or ``None``.

    Order: an explicit ``HARNESS_BUN_BIN``, then ``bun`` on PATH, then the
    OpenCode binary re-used as a Bun runtime via ``BUN_BE_BUN``.
    """
    explicit = os.environ.get("HARNESS_BUN_BIN", "").strip()
    if explicit:
        return ((explicit,), ())

    on_path = shutil.which("bun")
    if on_path:
        return ((on_path,), ())

    opencode = os.environ.get("HARNESS_OPENCODE_BIN", "").strip()
    candidates = [opencode] if opencode else []
    candidates.append(str(Path.home() / ".opencode" / "bin" / "opencode"))
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return ((candidate,), (("BUN_BE_BUN", "1"),))
    return None


def bun_available() -> bool:
    return _bun_argv() is not None


class _BunByteStream(httpx.SyncByteStream):
    """Stream the child process's stdout as an httpx response body."""

    def __init__(self, proc: subprocess.Popen, timeout: float = _READ_TIMEOUT):
        self._proc = proc
        self._timeout = timeout
        self._closed = False

    def __iter__(self):
        try:
            last = time.monotonic()
            while True:
                if self._closed:
                    return
                ready, _, _ = select.select([self._proc.stdout], [], [], 1.0)
                if not ready:
                    if time.monotonic() - last >= self._timeout:
                        raise httpx.ReadTimeout(
                            f"Bun transport stalled for {int(self._timeout)}s",
                            request=None,
                        )
                    continue
                chunk = self._proc.stdout.read1(_CHUNK)
                if not chunk:
                    return
                last = time.monotonic()
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        except Exception:
            pass
        for pipe in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:
                pass


class BunTransport(httpx.BaseTransport):
    """An httpx transport that performs requests with Bun's fetch."""

    def __init__(self, timeout: float = _READ_TIMEOUT):
        resolved = _bun_argv()
        if resolved is None:
            raise RuntimeError("no Bun runtime available")
        argv, extra_env = resolved
        self._argv = [*argv, str(_shim_path())]
        env = dict(os.environ)
        env.update(dict(extra_env))
        self._env = env
        self._timeout = timeout

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        headers = [
            (k, v)
            for k, v in request.headers.items()
            if k.lower() not in _DROP_HEADERS
        ]
        body = request.content
        job = {
            "method": request.method,
            "url": str(request.url),
            "headers": headers,
            "body": base64.b64encode(body).decode("ascii") if body else "",
        }
        try:
            proc = subprocess.Popen(
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env,
            )
        except OSError as e:
            raise httpx.ConnectError(f"could not start Bun transport: {e}", request=request)

        try:
            proc.stdin.write(json.dumps(job).encode("utf-8"))
            proc.stdin.close()
        except (BrokenPipeError, OSError) as e:
            stream = _BunByteStream(proc, self._timeout)
            stream.close()
            raise httpx.ConnectError(f"Bun transport closed early: {e}", request=request)

        head = proc.stdout.readline()
        if not head:
            stream = _BunByteStream(proc, self._timeout)
            detail = b""
            try:
                detail = proc.stderr.read() or b""
            finally:
                stream.close()
            raise httpx.ConnectError(
                "Bun transport produced no response"
                + (f": {detail.decode('utf-8', 'replace').strip()[:300]}" if detail else ""),
                request=request,
            )

        try:
            meta = json.loads(head)
        except json.JSONDecodeError:
            stream = _BunByteStream(proc, self._timeout)
            stream.close()
            raise httpx.ConnectError(
                f"Bun transport returned a malformed header: {head[:200]!r}",
                request=request,
            )

        if meta.get("status") == 599:
            stream = _BunByteStream(proc, self._timeout)
            stream.close()
            raise httpx.ConnectError(
                f"Bun fetch failed: {meta.get('error', 'unknown error')}",
                request=request,
            )

        return httpx.Response(
            int(meta.get("status") or 0),
            headers=[tuple(h) for h in meta.get("headers") or []],
            stream=_BunByteStream(proc, self._timeout),
            request=request,
        )

    def close(self) -> None:
        return None


def bun_http_client(timeout: float = _READ_TIMEOUT) -> httpx.Client | None:
    """An httpx client that routes every request through Bun, or ``None``."""
    if not bun_available():
        return None
    try:
        return httpx.Client(transport=BunTransport(timeout=timeout))
    except Exception:
        return None
