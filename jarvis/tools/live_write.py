"""Write a ``write_file`` call's content to disk while the model is still streaming it.

A big ``write_file`` can stream for minutes. Until now the content only lived
in the in-flight tool call, so a dropped connection, Esc, or a reply cut off by
the output limit lost all of it. Now the content is decoded from the partial
tool-call JSON as it arrives and written straight away:

* a **new** file inside the project is written at its real path, so you can
  watch it fill in your editor;
* an **existing** file, a path outside the project, a subagent's write, or
  plan mode gets a recovery draft under ``CONFIG_DIR/drafts`` instead. The
  original file is only changed when the real ``write_file`` runs.

When the call completes, ``write_file`` runs as usual. It ``claim()``s the file
it created live and treats it as new, so the diff reads "create" rather than
"partial → full". A call that ends incomplete keeps what arrived, and the model
is told where the content is (``partial_note``) so it can continue from there
instead of starting over. A complete call that is refused before it runs (a
secret placeholder in the content, a tool that isn't available) has its live
file removed with ``discard()``.
"""
from __future__ import annotations

import itertools
import pathlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import IO, Callable

from ..constants import CONFIG_DIR
from ..path_resolve import project_scope_error, robust_resolve

DRAFTS_DIR = CONFIG_DIR / "drafts"
LIVE_TOOL = "write_file"
_PATH_KEYS = ("path", "file_path")
_CONTENT_KEY = "content"
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_PLAIN_RUN = re.compile(r'[^"\\]+')
_DRAFT_NAME_MAX = 80
_DRAFT_SEQ = itertools.count(1)

# Files a live write created at their real path: resolved path → (mtime_ns,
# size) right after the last chunk. A matching stat means nobody touched it
# since, so it is still ours to overwrite, claim or remove.
_owned: dict[str, tuple[int, int]] = {}
_owned_lock = threading.Lock()


def _stat_key(p: pathlib.Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _owns(p: pathlib.Path) -> bool:
    key = str(p)
    with _owned_lock:
        stamp = _owned.get(key)
    return stamp is not None and stamp == _stat_key(p)


def claim(p: pathlib.Path) -> bool:
    """``write_file`` is about to write ``p``: True when a live write created it
    and nothing changed it since, so the write counts as a new file."""
    owned = _owns(p)
    with _owned_lock:
        _owned.pop(str(p), None)
    return owned


def discard(path: str) -> None:
    """A complete write_file call was refused: remove the file its live write
    created (only if it is still exactly what the live write left)."""
    if not path:
        return
    p = robust_resolve(path)
    if not _owns(p):
        return
    with _owned_lock:
        _owned.pop(str(p), None)
    try:
        p.unlink()
    except OSError:
        pass


# ── incremental JSON ─────────────────────────────────────────────────────────


def _decode_string(raw: str, i: int) -> tuple[str, int, bool]:
    """Decode a JSON string body from ``raw[i:]`` (just after its opening quote)
    as far as the data allows. Returns ``(text, next_index, closed)``; stops
    before an escape that is split across chunks."""
    out: list[str] = []
    n = len(raw)
    while i < n:
        m = _PLAIN_RUN.match(raw, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        ch = raw[i]
        if ch == '"':
            return "".join(out), i + 1, True
        # backslash escape
        if i + 1 >= n:
            break
        esc = raw[i + 1]
        if esc != "u":
            out.append(_ESCAPES.get(esc, esc))
            i += 2
            continue
        if i + 6 > n:
            break
        try:
            code = int(raw[i + 2:i + 6], 16)
        except ValueError:
            out.append(raw[i:i + 6])
            i += 6
            continue
        if 0xD800 <= code <= 0xDBFF:
            # High surrogate: combine with the low one when it follows.
            if i + 12 > n:
                break
            if raw[i + 6:i + 8] == "\\u":
                try:
                    low = int(raw[i + 8:i + 12], 16)
                except ValueError:
                    low = 0
                if 0xDC00 <= low <= 0xDFFF:
                    out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                    i += 12
                    continue
        out.append(chr(code))
        i += 6
    return "".join(out), i, False


class _ArgScanner:
    """Streams the top-level string fields of a tool call's JSON arguments.

    ``feed(delta)`` reports each string value's text through
    ``on_text(key, text)`` as soon as it arrives (``on_end(key)`` when it
    closes). Non-string values are skipped. ``done`` is True once the
    closing ``}`` has arrived.
    """

    def __init__(self, on_text: Callable[[str, str], None], on_end: Callable[[str], None]):
        self._raw = ""
        self._i = 0
        self._mode = "start"
        self._key = ""
        self._depth = 0
        self._in_str = False
        self._on_text = on_text
        self._on_end = on_end
        self.done = False

    def feed(self, delta: str) -> None:
        if self.done or not delta:
            return
        self._raw += delta
        self._scan()
        if self._i > 4096:  # keep the buffer small: drop what's been consumed
            self._raw = self._raw[self._i:]
            self._i = 0

    def _skip_ws(self) -> bool:
        raw, n = self._raw, len(self._raw)
        while self._i < n and raw[self._i] in " \t\r\n":
            self._i += 1
        return self._i < n

    def _scan(self) -> None:
        raw = self._raw
        while not self.done:
            mode = self._mode
            if mode in ("string", "key"):
                text, self._i, closed = _decode_string(raw, self._i)
                if mode == "key":
                    self._key += text
                elif text:
                    self._on_text(self._key, text)
                if not closed:
                    return
                if mode == "key":
                    self._mode = "colon"
                else:
                    self._on_end(self._key)
                    self._mode = "after"
                continue
            if mode == "other":
                if not self._skip_other():
                    return
                continue
            if not self._skip_ws():
                return
            ch = raw[self._i]
            if mode == "start":
                self._i += 1
                if ch == "{":
                    self._mode = "key_or_end"
                continue
            if mode == "key_or_end":
                self._i += 1
                if ch == '"':
                    self._key, self._mode = "", "key"
                elif ch == "}":
                    self.done = True
                continue
            if mode == "colon":
                self._i += 1
                if ch == ":":
                    self._mode = "value"
                continue
            if mode == "value":
                if ch == '"':
                    self._i += 1
                    self._mode = "string"
                else:
                    self._depth, self._in_str = 0, False
                    self._mode = "other"
                continue
            if mode == "after":
                self._i += 1
                if ch == ",":
                    self._mode = "key_or_end"
                elif ch == "}":
                    self.done = True
                continue

    def _skip_other(self) -> bool:
        """Skip a number / literal / nested value. True once it has ended."""
        raw, n = self._raw, len(self._raw)
        while self._i < n:
            ch = raw[self._i]
            if self._in_str:
                if ch == "\\":
                    if self._i + 1 >= n:
                        return False
                    self._i += 2
                    continue
                if ch == '"':
                    self._in_str = False
            elif ch == '"':
                self._in_str = True
            elif ch in "{[":
                self._depth += 1
            elif ch in "}]":
                if self._depth == 0:
                    self._mode = "after"
                    return True
                self._depth -= 1
            elif ch == "," and self._depth == 0:
                self._mode = "after"
                return True
            self._i += 1
        return False


# ── one write_file call ──────────────────────────────────────────────────────


@dataclass
class LiveResult:
    """What a live write left behind once the stream ended."""
    path: str              # the path the model asked for
    saved_to: str          # where the streamed content is on disk ("" = nowhere)
    chars: int
    lines: int
    complete: bool         # the content string (and call) fully arrived
    draft: bool            # saved_to is a recovery draft, not the real file


@dataclass
class _LiveWrite:
    drafts_only: bool
    path: str = ""
    path_buf: str = ""
    pending: list[str] = field(default_factory=list)
    content_done: bool = False
    target: pathlib.Path | None = None
    draft: bool = False
    off: bool = False
    fh: IO[str] | None = None
    chars: int = 0
    newlines: int = 0
    last_char: str = ""
    made_dirs: list[pathlib.Path] = field(default_factory=list)
    scanner: _ArgScanner | None = None

    def __post_init__(self) -> None:
        self.scanner = _ArgScanner(self._on_text, self._on_end)

    # scanner callbacks
    def _on_text(self, key: str, text: str) -> None:
        if key in _PATH_KEYS and not self.path:
            self.path_buf += text
        elif key == _CONTENT_KEY:
            self._write(text)

    def _on_end(self, key: str) -> None:
        if key in _PATH_KEYS and not self.path:
            self.path = self.path_buf
            if self.pending:
                self._write("")
        elif key == _CONTENT_KEY:
            self.content_done = True

    def feed(self, delta: str) -> None:
        if self.scanner is not None:
            self.scanner.feed(delta)

    @property
    def complete(self) -> bool:
        return self.content_done and bool(self.scanner and self.scanner.done)

    def _write(self, text: str) -> None:
        if text:
            self.chars += len(text)
            self.newlines += text.count("\n")
            self.last_char = text[-1]
        if self.off:
            return
        if self.fh is None:
            if text:
                self.pending.append(text)
            if not self.path:
                return
            self._open()
            if self.fh is None:
                self.pending = []
                return
            text = "".join(self.pending)
            self.pending = []
        if not text:
            return
        try:
            self.fh.write(text)
            self.fh.flush()
        except (OSError, UnicodeError):
            self._abandon()
            return
        self._remember()

    def _open(self) -> None:
        try:
            p = robust_resolve(self.path)
        except (OSError, RuntimeError, ValueError):
            self.off = True
            return
        if p.is_dir():
            self.off = True
            return
        from .. import state

        to_draft = (self.drafts_only or state.plan_mode
                    or project_scope_error(p, LIVE_TOOL) is not None
                    or (p.exists() and not _owns(p)))
        try:
            if to_draft:
                DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                name = f"{stamp}-{next(_DRAFT_SEQ)}-{p.name}"[:_DRAFT_NAME_MAX]
                target, self.draft = DRAFTS_DIR / name, True
            else:
                target = p
                missing = []
                parent = p.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                p.parent.mkdir(parents=True, exist_ok=True)
                self.made_dirs = missing  # deepest first
            self.fh = target.open("w", encoding="utf-8")
            self.target = target
        except OSError:
            self.off = True
            self.fh = None

    def _remember(self) -> None:
        if self.draft or self.target is None:
            return
        stamp = _stat_key(self.target)
        if stamp is not None:
            with _owned_lock:
                _owned[str(self.target)] = stamp
        try:
            from .files import _cache_invalidate

            _cache_invalidate(self.target)
        except Exception:  # noqa: BLE001 — cache upkeep must never break a write
            pass

    def _abandon(self) -> None:
        """Couldn't keep writing: stop and remove what this write created."""
        self.off = True
        if self.fh is not None:
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None
        if self.target is not None:
            with _owned_lock:
                _owned.pop(str(self.target), None)
            try:
                self.target.unlink()
            except OSError:
                pass
            for d in self.made_dirs:
                try:
                    d.rmdir()
                except OSError:
                    break
            self.target = None

    def close(self) -> LiveResult:
        if self.fh is not None:
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None
            self._remember()
        complete = self.complete
        if complete and self.draft and self.target is not None:
            # The whole content is in the tool call now: the draft isn't needed.
            try:
                self.target.unlink()
            except OSError:
                pass
            self.target = None
        lines = self.newlines + (1 if self.last_char and self.last_char != "\n" else 0)
        return LiveResult(
            path=self.path,
            saved_to=str(self.target) if self.target is not None else "",
            chars=self.chars,
            lines=lines,
            complete=complete,
            draft=self.draft,
        )


# ── one model reply ──────────────────────────────────────────────────────────


@dataclass
class _Call:
    name: str
    writer: _LiveWrite | None
    fed: bool = False


class LiveWrites:
    """Every write_file call streaming in one model reply.

    ``feed(key, name, delta)`` takes the ``("tool_input", …)`` deltas the
    provider streams yield; ``close()`` ends them all (always call it, in a
    ``finally``). ``drafts_only`` keeps every write in the drafts folder
    (subagents: their writes go through file claims first).
    """

    def __init__(self, drafts_only: bool = False):
        self._drafts_only = drafts_only
        self._calls: dict[object, _Call] = {}
        self._order: list[_LiveWrite] = []
        self.results: list[LiveResult] = []
        self._closed = False

    def feed(self, key: object, name: str, delta: str) -> None:
        """Providers differ: some send the name once with an empty delta, some
        repeat it on every delta, some stream the name itself in pieces, and
        some reuse a block index for the next message's calls."""
        if self._closed:
            return
        call = self._calls.get(key)
        if name and (call is None or call.name != name or (not delta and call.fed)):
            if call is not None and not call.fed and call.writer is not None:
                self._order.remove(call.writer)  # the name was still arriving
            writer = _LiveWrite(self._drafts_only) if name == LIVE_TOOL else None
            if writer is not None:
                self._order.append(writer)
            call = self._calls[key] = _Call(name, writer)
        if call is None or not delta:
            return
        call.fed = True
        if call.writer is not None:
            call.writer.feed(delta)

    def close(self) -> list[LiveResult]:
        if not self._closed:
            self._closed = True
            self.results = [w.close() for w in self._order]
        return self.results

    def incomplete(self) -> list[LiveResult]:
        return [r for r in self.close() if not r.complete and r.saved_to]

    def result_for(self, nth_write: int) -> LiveResult | None:
        """The live result for the ``nth_write``-th write_file call (0-based)."""
        results = self.close()
        return results[nth_write] if 0 <= nth_write < len(results) else None


def _size(chars: int) -> str:
    return f"{chars / 1024:.1f} KB" if chars >= 1024 else f"{chars} chars"


def partial_note(r: LiveResult) -> str:
    """Tells the model where the content that did arrive is, so it continues
    instead of regenerating the whole file."""
    if not r.saved_to or r.complete:
        return ""
    got = f"{r.lines} lines, {_size(r.chars)}"
    if r.draft:
        return (
            f" The part that did arrive ({got}) was saved as it streamed to the draft "
            f"{r.saved_to}; {r.path or 'the target file'} was NOT changed. Read the end of "
            "that draft and continue from where it stops instead of regenerating it all."
        )
    return (
        f" The part that did arrive ({got}) was already written to {r.path} as it "
        "streamed. Don't regenerate it: read the end of that file (read_file with an "
        "offset) and append only the rest with edit_file."
    )


def user_notice(r: LiveResult) -> str:
    """One line for the transcript when a reply stops mid-file."""
    where = f"draft {r.saved_to}" if r.draft else (r.path or r.saved_to)
    return f"Kept partial file: {where} ({r.lines} lines, {_size(r.chars)}) — the reply stopped before it was complete"

