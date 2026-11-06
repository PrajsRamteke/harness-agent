"""Terminals for the web page — real shells on a PTY, driven by the browser.

Each terminal is a login-less interactive shell (`$SHELL -i`) attached to a
pseudo-terminal, running in the project folder. Output is kept in a bounded
text buffer; the page long-polls ``read_many()`` (every shell in one request,
so eight shells never hold eight of the browser's ~6 connections per host)
with a cursor per shell, writes keystrokes with ``write()`` and follows the
xterm size with ``resize()``.

Replaying a trimmed buffer: full-screen programs (Claude Code, vim, htop …)
switch to the alternate screen once, paint it once and then send only the
cells that change. A page that starts reading after that (a reload, or one
that fell behind the trimmed window) would draw diffs onto a blank screen. So
the terminal tracks the DEC private modes in effect where the buffer starts
(``_Modes``) and replays them first, and when the program's alternate screen
began before that point it skips the stale diffs and sends the program
Ctrl+L, which every full-screen program takes as "repaint everything".

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
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid
from typing import Any

MAX_BUFFER = 2_000_000        # characters kept per terminal (a long Claude Code run)
READ_MAX = 524_288            # characters one read hands back; the page reads again
MAX_SESSIONS = 8
READ_WAIT_MAX = 25.0
READ_CHUNK = 65_536
WRITE_TIMEOUT = 5.0           # seconds a paste may wait for the shell to read
_SMALL_CHUNK = 4096           # small reads (keystroke echoes) are merged into one piece

_lock = threading.Lock()
_sessions: dict[str, "TerminalSession"] = {}
_order: list[str] = []
# Every terminal's output wakes this one condition, so one request can wait
# on all of a page's shells at once (``read_many``).
_activity = threading.Condition()


# ─── Terminal modes in effect at a point of the output ──────────────────────

_ALT_MODES = (1049, 1047, 47)
# DEC private modes a replay restores, with their power-on values: cursor keys,
# origin, autowrap, cursor shown, mouse reports, focus events, bracketed paste.
_TRACKED = {1: False, 6: False, 7: True, 25: True, 1000: False, 1002: False,
            1003: False, 1004: False, 1005: False, 1006: False, 1015: False,
            2004: False}
_MODE_RE = re.compile(r"\x1b\[\?([\d;]*)([hl])|\x1bc|\x1b([=>])")
_CSI_FINAL = re.compile(r"[@-~]")


def _unfinished(seq: str) -> bool:
    """Whether ``seq`` (starting with ESC) is cut before its last character."""
    if len(seq) < 2:
        return True
    kind = seq[1]
    if kind == "[":
        return not _CSI_FINAL.search(seq, 2)
    if kind in "]P_^":                 # OSC / DCS …: end with BEL or ESC \\
        return "\x07" not in seq and "\x1b\\" not in seq[1:]
    return False


class _Modes:
    """Follows the mode-setting sequences in a stream of output."""

    def __init__(self) -> None:
        self.modes = dict(_TRACKED)
        self.alt = False
        self.keypad = False
        self.alt_since: int | None = None   # absolute offset where the alt screen began
        self._carry = ""

    def feed(self, text: str, offset: int) -> None:
        """``text`` starts at absolute ``offset`` of the stream."""
        if self._carry:
            offset -= len(self._carry)
            text = self._carry + text
            self._carry = ""
        # A sequence cut by the end of this piece is finished by the next one.
        esc = text.rfind("\x1b", max(0, len(text) - 32))
        if esc >= 0 and _unfinished(text[esc:]):
            self._carry = text[esc:]
            text = text[:esc]
        if "\x1b" not in text:
            return
        for m in _MODE_RE.finditer(text):
            params, flag, keypad = m.group(1), m.group(2), m.group(3)
            if keypad:
                self.keypad = keypad == "="
                continue
            if flag is None:                       # ESC c: full reset
                self.modes = dict(_TRACKED)
                self.alt = self.keypad = False
                self.alt_since = None
                continue
            on = flag == "h"
            for part in params.split(";"):
                if not part.isdigit():
                    continue
                mode = int(part)
                if mode in _TRACKED:
                    self.modes[mode] = on
                elif mode in _ALT_MODES:
                    if on and not self.alt:
                        self.alt_since = offset + m.start()
                    self.alt = on
                    if not on:
                        self.alt_since = None

    def preamble(self) -> str:
        """Sequences that put a fresh terminal into these modes."""
        out = ["\x1b[?1049h"] if self.alt else []
        for mode, default in _TRACKED.items():
            if self.modes[mode] != default:
                out.append(f"\x1b[?{mode}{'h' if self.modes[mode] else 'l'}")
        if self.keypad:
            out.append("\x1b=")
        return "".join(out)


def _shell() -> str:
    for candidate in (os.environ.get("SHELL"), "/bin/zsh", "/bin/bash", "/bin/sh"):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "/bin/sh"


# The shell must own the PTY as its controlling terminal. Without one the
# kernel has no foreground process group to signal: Ctrl+C never interrupts a
# command (SIGINT) and a resize never reaches a program (SIGWINCH), so Claude
# Code kept drawing for the old width after a split. `start_new_session` makes
# the child a session leader; this stub then claims the PTY (TIOCSCTTY) and
# execs the shell — done after exec rather than in `preexec_fn`, which isn't
# safe to run in this threaded server.
_CLAIM_TTY = (
    "import fcntl, os, sys, termios\n"
    "try:\n"
    "    fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
    "except OSError:\n"
    "    pass\n"
    "os.execv(sys.argv[1], sys.argv[1:])\n"
)


def _shell_command() -> list[str]:
    shell = _shell()
    python = sys.executable
    if python and os.path.isfile(python) and hasattr(termios, "TIOCSCTTY"):
        return [python, "-I", "-S", "-c", _CLAIM_TTY, shell, "-i"]
    return [shell, "-i"]


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
        self._chunks: list[str] = []   # decoded output since `base`, in pieces
        self._size = 0            # characters held in _chunks
        self.base = 0             # absolute offset of the first held character
        self.end = 0              # absolute offset after the last character
        self.exited = False
        self.exit_code: int | None = None
        self.created = time.time()
        self.cond = _activity
        self._modes = _Modes()      # modes in effect at `end`
        self._base_modes = _Modes() # … and at `base` (fed what is trimmed)
        # The master fd is closed only by the reader thread, under this lock;
        # writers / resizes take it too, so none of them can ever touch a
        # closed (and possibly reused) fd number.
        self._fd_lock = threading.Lock()
        self._stop = False
        self._master, slave = pty.openpty()
        self._resize(cols, rows, fd=slave)
        self.proc = subprocess.Popen(                       # noqa: S603 - a user's own shell
            _shell_command(),
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

    @property
    def buffer(self) -> str:
        """Everything still held, as one string (tests, small terminals)."""
        with self.cond:
            return "".join(self._chunks)

    def _append(self, text: str) -> None:
        if not text:
            return
        with self.cond:
            self._modes.feed(text, self.end)
            if self._chunks and len(self._chunks[-1]) < _SMALL_CHUNK:
                self._chunks[-1] += text
            else:
                self._chunks.append(text)
            self._size += len(text)
            self.end += len(text)
            if self._size > MAX_BUFFER:
                self._trim(self._size - MAX_BUFFER)
            self.cond.notify_all()

    def _trim(self, overflow: int) -> None:
        """Drop at least ``overflow`` characters from the front, ending on a
        line break (or at least never inside an escape sequence)."""
        dropped: list[str] = []
        need = overflow
        while self._chunks and len(self._chunks[0]) <= need:
            piece = self._chunks.pop(0)
            dropped.append(piece)
            need -= len(piece)
        if need > 0 and self._chunks:
            head = self._chunks[0]
            cut = head.find("\n", need)
            if cut < 0 or cut - need > 4096:
                cut = need
                esc = head.rfind("\x1b", max(0, cut - 64), cut)
                if esc >= 0 and _unfinished(head[esc:cut]):
                    cut = esc
            else:
                cut += 1
            dropped.append(head[:cut])
            self._chunks[0] = head[cut:]
            if not self._chunks[0]:
                self._chunks.pop(0)
        gone = 0
        for piece in dropped:
            self._base_modes.feed(piece, self.base + gone)
            gone += len(piece)
        self._size -= gone
        self.base += gone

    def _slice(self, start: int, limit: int) -> str:
        """Held text from absolute ``start``, at most ``limit`` characters."""
        out: list[str] = []
        pos = self.base
        room = limit
        for piece in self._chunks:
            nxt = pos + len(piece)
            if nxt > start:
                part = piece[max(0, start - pos):]
                out.append(part[:room])
                room -= min(room, len(part))
                if room <= 0:
                    break
            pos = nxt
        return "".join(out)

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

    def has_news(self, cursor: int) -> bool:
        """Whether ``read(cursor)`` would hand back something right away."""
        return cursor < self.base or cursor < self.end

    def read(self, cursor: int, wait: float = 0.0, limit: int = READ_MAX) -> dict[str, Any]:
        """Output from ``cursor`` on. ``wait`` seconds only while nothing new.

        A cursor before the held window (a page attaching late, or one that
        fell behind) gets ``reset``: the page clears its screen, and the data
        starts with the modes in effect there (see the module docstring)."""
        redraw = False
        with self.cond:
            if cursor < self.base:
                reset = True
                modes = self._base_modes
                if self._modes.alt and (self._modes.alt_since is None or self._modes.alt_since < self.base):
                    # A full-screen program whose first paint is gone: what is
                    # held is diffs of a screen the page never saw. Skip them
                    # and have the program paint the whole screen again.
                    prefix = self._modes.preamble() + "\x1b[H\x1b[2J"
                    cursor = self.end
                    redraw = not self.exited
                else:
                    prefix = modes.preamble()
                    cursor = self.base
            else:
                reset = False
                prefix = ""
                cursor = min(cursor, self.end)
                deadline = time.monotonic() + max(0.0, wait)
                while self.end <= cursor and not self.exited:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    self.cond.wait(min(left, 1.0))
            data = self._slice(cursor, max(1, limit))
            result = {
                "ok": True,
                "id": self.id,
                "cursor": cursor + len(data),
                "data": prefix + data,
                "reset": reset,
                "more": cursor + len(data) < self.end,
                "exited": self.exited,
                "code": self.exit_code,
            }
        if redraw:
            self.write("\x0c")   # Ctrl+L: repaint (Claude Code, vim, htop, less …)
        return result

    def status(self) -> dict[str, Any]:
        """Where the shell is now and whether a program holds it.

        ``busy``: the terminal's foreground process group isn't the shell's own
        (a dev server, Claude Code, vim …) — typing a command there would feed
        that program, not the shell. ``cwd`` follows the shell's ``cd``s."""
        pid = self.proc.pid
        fg = -1
        with self._fd_lock:
            if self._master >= 0:
                try:
                    fg = os.tcgetpgrp(self._master)
                except OSError:
                    fg = -1
        busy = not self.exited and fg > 0 and fg != pid
        return {
            "id": self.id,
            "cwd": _live_cwd(pid) or self.cwd,
            "busy": busy,
            "program": _program_name(fg) if busy else "",
            "exited": self.exited,
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


# ─── What a shell is doing (TerminalSession.status) ─────────────────────

_PROBE_TIMEOUT = 1.5


def _live_cwd(pid: int) -> str:
    """The current folder of process ``pid`` ('' when the OS won't say)."""
    try:
        return os.readlink(f"/proc/{pid}/cwd")          # Linux
    except OSError:
        pass
    try:                                               # macOS: no /proc
        out = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        if line.startswith("n/"):
            return line[1:]
    return ""


def _program_name(pgid: int) -> str:
    """The command name of the process leading group ``pgid`` (`node`, `vim` …)."""
    try:
        out = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(pgid)],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return os.path.basename(out.splitlines()[0]) if out else ""


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


def read_many(cursors: dict[str, int], wait: float, limit: int = READ_MAX) -> dict[str, Any]:
    """Wait up to ``wait`` seconds for output on any of these terminals and
    return what each one has. A terminal that is gone answers ``gone``."""
    deadline = time.monotonic() + max(0.0, min(wait, READ_WAIT_MAX))
    per = max(4096, limit // max(1, len(cursors)))
    while True:
        # Look the sessions up before taking _activity: closing one holds
        # _lock while its last output takes _activity.
        with _lock:
            sessions = {tid: _sessions.get(tid) for tid in cursors}
        out: dict[str, Any] = {}
        with _activity:
            for tid, session in sessions.items():
                cursor = cursors[tid]
                if session is None:
                    out[tid] = {"ok": False, "code": "gone", "id": tid}
                elif session.has_news(cursor) or session.exited:
                    out[tid] = session
            if not out:
                left = deadline - time.monotonic()
                if left <= 0:
                    return {"ok": True, "terminals": {}}
                _activity.wait(min(left, 1.0))
                continue
        for tid, value in out.items():
            if isinstance(value, TerminalSession):
                out[tid] = value.read(cursors[tid], 0.0, per)
        return {"ok": True, "terminals": out}


def get_terminal(tid: str) -> TerminalSession | None:
    with _lock:
        return _sessions.get(tid)


def list_terminals() -> list[dict[str, Any]]:
    with _lock:
        sessions = [_sessions[i] for i in _order if i in _sessions]
    return [s.public() for s in sessions]


def live_count() -> int:
    """Shells still running (the project switcher shows other pages this count)."""
    with _lock:
        return sum(1 for s in _sessions.values() if not s.exited)


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
    "open_terminal", "get_terminal", "read_many", "list_terminals", "close_terminal",
    "close_all", "cwd_for_shell", "READ_WAIT_MAX",
]
