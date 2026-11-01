"""Terminals for the web page — real shells on a PTY, driven by the browser.

Each terminal is a login-less interactive shell (`$SHELL -i`) attached to a
pseudo-terminal, running in the project folder. Output is kept in a bounded
text buffer; the page long-polls ``read()`` with a cursor, writes keystrokes
with ``write()`` and follows the xterm size with ``resize()``.

No WebSocket dependency: reads are HTTP long-poll and writes are plain POSTs,
which is all the page's existing ThreadingHTTPServer needs.

A terminal is a shell on the computer, outside the approval flow, so the
handler only exposes it to a direct visit (this computer / the LAN). A page
arriving through an Anywhere tunnel gets ``local_only`` instead — unless the
QR dialog's "Terminal on the link" switch was turned on from a direct visit
(``bridge.terminal_over_tunnel``), which the other instance in ``hub.py``
keeps its own copy of.
"""
from __future__ import annotations

import codecs
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import threading
import time
import uuid
from typing import Any

MAX_BUFFER = 300_000          # characters kept per terminal (~3 MB of text)
MAX_SESSIONS = 8
READ_WAIT_MAX = 25.0
READ_CHUNK = 65_536
WRITE_TIMEOUT = 5.0           # seconds a paste may wait for the shell to read

_lock = threading.Lock()
_sessions: dict[str, "TerminalSession"] = {}
_order: list[str] = []


def _shell() -> str:
    for candidate in (os.environ.get("SHELL"), "/bin/zsh", "/bin/bash", "/bin/sh"):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "/bin/sh"


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    # A web-only launch variable: the shell is not the remote, so drop it.
    env.pop("HARNESS_WEB_TUNNEL", None)
    return env


class TerminalSession:
    """One shell: PTY master side + a growing decoded output buffer."""

    def __init__(self, *, cols: int, rows: int, cwd: str | None = None) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.title = "Terminal"
        self.cwd = cwd or os.getcwd()
        self.buffer = ""          # decoded output since `base`
        self.base = 0             # absolute offset of buffer[0]
        self.end = 0              # absolute offset after the last character
        self.exited = False
        self.exit_code: int | None = None
        self.created = time.time()
        self.cond = threading.Condition()
        # The master fd is closed only by the reader thread, under this lock;
        # writers / resizes take it too, so none of them can ever touch a
        # closed (and possibly reused) fd number.
        self._fd_lock = threading.Lock()
        self._stop = False
        self._master, slave = pty.openpty()
        self._resize(cols, rows, fd=slave)
        self.proc = subprocess.Popen(                       # noqa: S603 - a user's own shell
            [_shell(), "-i"],
            stdin=slave, stdout=slave, stderr=slave,
            cwd=self.cwd, env=_child_env(),
            start_new_session=True, close_fds=True,
        )
        os.close(slave)
        # Non-blocking master: a shell that stops reading (a paste into a busy
        # program) must never wedge a request thread — or close() — on write.
        os.set_blocking(self._master, False)
        self._reader = threading.Thread(
            target=self._read_loop, name=f"terminal-{self.id}", daemon=True,
        )
        self._reader.start()

    # ─── PTY plumbing ────────────────────────────────────────────────
    def _resize(self, cols: int, rows: int, *, fd: int | None = None) -> None:
        cols = max(2, min(500, int(cols or 80)))
        rows = max(1, min(300, int(rows or 24)))
        packed = struct.pack("HHHH", rows, cols, 0, 0)
        if fd is not None:
            try:
                fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)
            except OSError:
                pass
            return
        with self._fd_lock:
            if self._master < 0:
                return
            try:
                fcntl.ioctl(self._master, termios.TIOCSWINSZ, packed)
            except OSError:
                pass

    def resize(self, cols: int, rows: int) -> None:
        self._resize(cols, rows)

    def write(self, data: str) -> bool:
        if not data or self.exited:
            return False
        payload = memoryview(data.encode("utf-8", "replace"))
        try:
            # A PTY takes a big paste in pieces: keep writing until all of it
            # went in (os.write may accept only part of it).
            deadline = time.monotonic() + WRITE_TIMEOUT
            while payload:
                with self._fd_lock:
                    fd = self._master
                    if fd < 0:
                        return False
                    try:
                        sent = os.write(fd, payload)
                    except BlockingIOError:
                        sent = 0
                payload = payload[sent:]
                if payload and not sent:
                    if time.monotonic() > deadline:
                        return False  # the program isn't reading its input
                    select.select([], [fd], [], 0.1)
            return True
        except (OSError, ValueError):
            self._finish(None)
            return False

    def _append(self, text: str) -> None:
        if not text:
            return
        with self.cond:
            self.buffer += text
            self.end += len(text)
            overflow = len(self.buffer) - MAX_BUFFER
            if overflow > 0:
                # Cut at a newline so the page never starts mid-escape.
                cut = self.buffer.find("\n", overflow)
                cut = overflow if cut < 0 or cut - overflow > 4096 else cut + 1
                self.buffer = self.buffer[cut:]
                self.base += cut
            self.cond.notify_all()

    def _read_loop(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        fd = self._master  # this thread is the only one that closes it
        try:
            while not self._stop:
                try:
                    ready, _, _ = select.select([fd], [], [], 0.5)
                except (OSError, ValueError):
                    break
                if fd in ready:
                    try:
                        chunk = os.read(fd, READ_CHUNK)
                    except BlockingIOError:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    self._append(decoder.decode(chunk))
                elif self.proc.poll() is not None:
                    # The child is gone and nothing is left to read.
                    break
        finally:
            try:
                leftover = decoder.decode(b"", final=True)
            except Exception:
                leftover = ""
            self._append(leftover)
            self._finish(self.proc.poll())
            with self._fd_lock:
                self._master = -1
            try:
                os.close(fd)
            except OSError:
                pass

    def _finish(self, code: int | None) -> None:
        already = self.exited
        self.exited = True
        if self.exit_code is None:
            self.exit_code = code
        if not already:
            self._append(f"\r\n\x1b[90m[process exited{'' if code is None else f' {code}'}]\x1b[0m\r\n")
        with self.cond:
            self.cond.notify_all()

    def read(self, cursor: int, wait: float) -> dict[str, Any]:
        """Output from ``cursor`` on. ``wait`` seconds only while nothing new."""
        with self.cond:
            if cursor < self.base:
                # The page fell behind the trimmed window: start from the top
                # of what is left and tell it to reset its screen.
                cursor = self.base
                reset = True
            else:
                reset = False
                cursor = min(cursor, self.end)
            deadline = time.monotonic() + max(0.0, wait)
            while self.end <= cursor and not self.exited:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(min(left, 1.0))
            start = max(0, cursor - self.base)
            data = self.buffer[start:]
            return {
                "ok": True,
                "id": self.id,
                "cursor": self.end,
                "data": data,
                "reset": reset,
                "exited": self.exited,
                "code": self.exit_code,
            }

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "cwd": self.cwd,
            "exited": self.exited,
            "code": self.exit_code,
            "cursor": self.end,
        }

    def close(self) -> None:
        self._stop = True  # the reader closes the fd on its next tick
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGHUP)
        except (OSError, ProcessLookupError):
            pass
        try:
            self.proc.terminate()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        self._finish(self.proc.poll())


# ─── Registry ────────────────────────────────────────────────────────────

def _unique_title() -> str:
    used = {s.title for s in _sessions.values()}
    n = 1
    while f"Terminal {n}" in used:
        n += 1
    return f"Terminal {n}"


def open_terminal(*, cols: int = 80, rows: int = 24, cwd: str | None = None) -> dict[str, Any]:
    with _lock:
        if len(_sessions) >= MAX_SESSIONS:
            oldest = next((i for i in _order if i in _sessions), None)
            if oldest is None:
                return {"ok": False, "error": f"Too many terminals open (max {MAX_SESSIONS})."}
            _close_locked(oldest)
        session = TerminalSession(cols=cols, rows=rows, cwd=cwd)
        session.title = _unique_title()
        _sessions[session.id] = session
        _order.append(session.id)
    return {"ok": True, "terminal": session.public()}


def get_terminal(tid: str) -> TerminalSession | None:
    with _lock:
        return _sessions.get(tid)


def list_terminals() -> list[dict[str, Any]]:
    with _lock:
        sessions = [_sessions[i] for i in _order if i in _sessions]
    return [s.public() for s in sessions]


def _close_locked(tid: str) -> bool:
    session = _sessions.pop(tid, None)
    try:
        _order.remove(tid)
    except ValueError:
        pass
    if session is not None:
        session.close()
        return True
    return False


def close_terminal(tid: str) -> bool:
    with _lock:
        return _close_locked(tid)


def close_all() -> None:
    with _lock:
        ids = list(_sessions)
    for tid in ids:
        close_terminal(tid)


def cwd_for_shell(path: str) -> str:
    """A folder the shell may start in (the project root by default)."""
    if path:
        try:
            real = os.path.realpath(os.path.expanduser(path))
            if os.path.isdir(real):
                return real
        except OSError:
            pass
    return os.getcwd()


__all__ = [
    "open_terminal", "get_terminal", "list_terminals", "close_terminal",
    "close_all", "cwd_for_shell", "READ_WAIT_MAX",
]
