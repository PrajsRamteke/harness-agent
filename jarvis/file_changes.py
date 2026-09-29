"""What changed on disk this session — the data behind the web "Changes" panel.

Tools that write files (``write_file`` / ``edit_file`` / ``multi_edit``, through
``repl.file_diffs.emit_file_diff``) and shell commands that touch files
(``run_bash``, through :func:`watch_shell`) report here. Every file keeps the
text it had **before Jarvis first touched it**; the diff on offer is always that
baseline against what is on disk *now*. So ten edits to one file read as one
change, a file created and removed again disappears, a file deleted from the
shell shows up as deleted, and ``mv`` reads as a rename.

Ledgers are kept per session id, so resuming a session in the same process
brings its changes back. UI-free: the web layer subscribes with
:func:`subscribe` and pushes ``change`` events to browsers.
"""
from __future__ import annotations

import difflib
import glob
import hashlib
import itertools
import os
import pathlib
import shlex
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from .constants import CWD

MAX_TEXT = 512 * 1024        # chars of one file kept for diffing; bigger → counts only
MAX_FILES = 400              # tracked files per session
MAX_LEDGER_CHARS = 24 * 1024 * 1024   # baseline + current text held per session; past it new files are counted, not diffed
MAX_STEPS = 40               # remembered edits per file
MAX_DIFF_LINES = 700         # diff rows sent to a browser per file
MAX_LINE = 400               # chars kept per diff row
MAX_SHELL_FILES = 60         # files snapshotted around one shell command
MAX_SHELL_BYTES = 8 * 1024 * 1024
_LEDGERS_KEPT = 8
# Persisted per-session change ledgers (SQLite): baselines + edit steps so the
# web Changes panel survives a restart and a session resume. Large texts are
# never persisted wholesale — see _persist_ledger().
_CHANGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_file_changes (
    session_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    existed INTEGER NOT NULL,
    baseline TEXT NOT NULL DEFAULT '',
    big INTEGER NOT NULL DEFAULT 0,
    first_ts REAL NOT NULL DEFAULT 0,
    last_ts REAL NOT NULL DEFAULT 0,
    steps_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (session_id, path)
);
CREATE INDEX IF NOT EXISTS idx_sfc_session ON session_file_changes(session_id);
"""

# Never look inside these when a shell command names a directory.
_SKIP_PARTS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", ".next",
}

_BIG = object()              # "exists, but binary or too large to diff"


@dataclass
class Step:
    ts: float
    action: str              # create | write | edit | delete
    added: int
    removed: int


@dataclass
class Tracked:
    path: str                # absolute, normalised
    existed: bool            # was there before Jarvis first touched it
    baseline: str            # its text then ("" when it did not exist)
    big: bool = False        # too large for a text diff — counts only
    current: Any = None      # last known text; None = deleted
    sig: tuple | None = None  # (mtime_ns, size) `current` was read at
    first_ts: float = 0.0
    last_ts: float = 0.0
    steps: list[Step] = field(default_factory=list)
    # net result vs baseline, cached until the file or its history changes
    net_key: Any = None
    status: str = ""
    added: int = 0
    removed: int = 0


_lock = threading.RLock()
_ledgers: "OrderedDict[int, dict[str, Tracked]]" = OrderedDict()
_listeners: list[Callable[[str], None]] = []
_persisted_sids: set[int] = set()   # sessions whose ledger was loaded from SQLite
_dirty_sids: set[int] = set()       # sessions with unflushed ledger writes
_persist_timer: threading.Timer | None = None


# ── helpers ────────────────────────────────────────────────────────────────

def _norm(path: str | os.PathLike) -> str:
    # realpath: the tools hand us resolved paths, the shell watcher builds them
    # from the project root — both must land on the same key.
    return os.path.realpath(os.path.expanduser(str(path)))


def file_id(path: str | os.PathLike) -> str:
    """Stable id of a file within a session (what the web UI keys rows by)."""
    return hashlib.sha1(_norm(path).encode("utf-8", "replace")).hexdigest()[:12]


def display_path(path: str) -> str:
    """Project-relative when inside the project, ``~/…`` under home, else absolute."""
    p = pathlib.Path(path)
    try:
        return p.relative_to(CWD).as_posix()
    except ValueError:
        pass
    try:
        return "~/" + p.relative_to(pathlib.Path.home()).as_posix()
    except ValueError:
        return p.as_posix()


def _lines(text: str) -> list[str]:
    if not text:
        return []
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _sig(path: str) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _read(path: str) -> Any:
    """Text of a file, ``None`` when missing, ``_BIG`` when binary / too large."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not os.path.isfile(path):
        return None
    if st.st_size > MAX_TEXT * 4:
        return _BIG
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return _BIG
    text = raw.decode("utf-8", errors="ignore")
    return _BIG if len(text) > MAX_TEXT else text


def _matcher(a: list[str], b: list[str]) -> difflib.SequenceMatcher:
    # autojunk would treat common lines (blank, "}") as noise on long files
    # and give odd diffs; it only pays off on very large inputs.
    return difflib.SequenceMatcher(None, a, b, autojunk=len(a) + len(b) > 6000)


def _count(before: str, after: str) -> tuple[int, int]:
    """Lines added / removed going from ``before`` to ``after``."""
    a, b = _lines(before), _lines(after)
    if not a:
        return len(b), 0
    if not b:
        return 0, len(a)
    added = removed = 0
    for tag, i1, i2, j1, j2 in _matcher(a, b).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def _clip(line: str) -> str:
    if line.endswith("\r"):
        line = line[:-1]          # CRLF files: the row shouldn't end in a stray CR
    return line if len(line) <= MAX_LINE else line[:MAX_LINE] + "…"


def build_hunks(before: str, after: str, context: int = 3, limit: int | None = None) -> tuple[list[dict], int]:
    """Numbered diff hunks ``before → after`` and how many rows were cut.

    A row is ``[kind, old_no, new_no, text]`` with kind ``" "`` (context),
    ``"+"`` or ``"-"``.
    """
    limit = MAX_DIFF_LINES if limit is None else limit
    a, b = _lines(before), _lines(after)
    hunks: list[dict] = []
    shown = hidden = 0
    for group in _matcher(a, b).get_grouped_opcodes(context):
        rows: list[list] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                rows += [[" ", i + 1, j + 1, a[i]] for i, j in zip(range(i1, i2), range(j1, j2))]
                continue
            if tag in ("replace", "delete"):
                rows += [["-", i + 1, None, a[i]] for i in range(i1, i2)]
            if tag in ("replace", "insert"):
                rows += [["+", None, j + 1, b[j]] for j in range(j1, j2)]
        if shown >= limit:
            hidden += sum(1 for r in rows if r[0] != " ")
            continue
        room = limit - shown
        if len(rows) > room:
            hidden += sum(1 for r in rows[room:] if r[0] != " ")
            rows = rows[:room]
        shown += len(rows)
        for r in rows:
            r[3] = _clip(r[3])
        first, last = group[0], group[-1]
        old_len, new_len = last[2] - first[1], last[4] - first[3]
        hunks.append({
            # git's convention: an empty range starts at the line before it
            "old_start": first[1] + 1 if old_len else first[1], "old_len": old_len,
            "new_start": first[3] + 1 if new_len else first[3], "new_len": new_len,
            "rows": rows,
        })
    return hunks, hidden


# ── the ledger ─────────────────────────────────────────────────────────────

def _session_key() -> int:
    from . import state

    try:
        return int(state.current_session_id or 0)
    except (TypeError, ValueError):
        return 0


def _ledger() -> dict[str, Tracked]:
    sid = _session_key()
    led = _ledgers.get(sid)
    if led is None:
        led = _ledgers[sid] = {}
        evicted: list[int] = []
        while len(_ledgers) > _LEDGERS_KEPT:
            old, _ = _ledgers.popitem(last=False)
            evicted.append(old)
        for old in evicted:
            # Flushed synchronously — the debounced timer may not have run yet,
            # and a later flush must not see a missing ledger as "netted out".
            _persist_ledger(old)
            with _lock:
                _persisted_sids.discard(old)
        _restore_session(sid, led)
    else:
        _ledgers.move_to_end(sid)
    return led


def reset() -> None:
    """Forget every ledger (tests).

    Clears in-memory ledgers AND persisted rows so tests never leak into
    each other through the sessions DB (every test uses session id 1).
    """
    with _lock:
        sids = list(_ledgers.keys()) or ([_session_key()] if _session_key() else [])
        _ledgers.clear()
        _persisted_sids.clear()
        _dirty_sids.clear()
    for sid in sids:
        try:
            conn = _changes_conn()
            try:
                with conn:
                    conn.execute("DELETE FROM session_file_changes WHERE session_id = ?", (sid,))
            finally:
                conn.close()
        except Exception:
            pass


def forget_session(sid: int) -> None:
    """Drop one session's in-memory ledger (called when it is deleted)."""
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        return
    with _lock:
        _ledgers.pop(sid, None)
        _persisted_sids.discard(sid)
        _dirty_sids.discard(sid)
    try:
        from .storage.sessions import db_conn

        conn = db_conn()
        try:
            with conn:
                conn.execute("DELETE FROM session_file_changes WHERE session_id = ?", (sid,))
        finally:
            conn.close()
    except Exception:
        pass


# ── persistence (SQLite) ─────────────────────────────────────────────────
# The ledger is in-memory while a session runs; each write is flushed to the
# sessions DB (debounced) so the web Changes panel survives a restart and a
# resume. Only baselines + aggregate edit steps are stored — never the current
# file text — so the table stays small and resume re-diffs against disk.

_PERSIST_DEBOUNCE_SECS = 2.0
_PERSIST_BASELINE_CHARS = 200_000   # bigger baselines → counts only, like _BIG
_PERSIST_STEPS_KEPT = 40


def _changes_conn():
    from .storage.sessions import db_conn

    conn = db_conn()
    try:
        conn.executescript(_CHANGES_SCHEMA)
    except Exception:
        pass
    return conn


def _schedule_persist(sid: int) -> None:
    if not sid:
        return
    with _lock:
        _dirty_sids.add(sid)
        global _persist_timer
        if _persist_timer is not None:
            return
        _persist_timer = threading.Timer(_PERSIST_DEBOUNCE_SECS, _flush_dirty)
        _persist_timer.daemon = True
        _persist_timer.start()


def _flush_dirty() -> None:
    with _lock:
        global _persist_timer
        _persist_timer = None
        sids = sorted(_dirty_sids)
        _dirty_sids.clear()
    for sid in sids:
        _persist_ledger(sid)


def flush() -> None:
    """Write pending ledgers to SQLite now (restart / tests)."""
    _flush_dirty()


def _persist_ledger(sid: int) -> None:
    try:
        sid = int(sid or 0)
    except (TypeError, ValueError):
        return
    if not sid:
        return
    with _lock:
        led = _ledgers.get(sid)
        rows = [] if led is None else list(led.values())
    if not rows:
        # A session whose files all netted out to zero: clear stale rows.
        try:
            conn = _changes_conn()
            try:
                with conn:
                    conn.execute("DELETE FROM session_file_changes WHERE session_id = ?", (sid,))
            finally:
                conn.close()
        except Exception:
            pass
        return
    payload: list[tuple] = []
    for tf in rows:
        baseline = tf.baseline if isinstance(tf.baseline, str) else ""
        big = 1 if (tf.big or len(baseline) > _PERSIST_BASELINE_CHARS) else 0
        if big and len(baseline) > _PERSIST_BASELINE_CHARS:
            baseline = ""
        steps = [
            {"ts": s.ts, "action": s.action, "added": s.added, "removed": s.removed}
            for s in (tf.steps or [])[-_PERSIST_STEPS_KEPT:]
        ]
        payload.append((
            sid, _norm(tf.path), 1 if tf.existed else 0, baseline, big,
            float(tf.first_ts or 0), float(tf.last_ts or 0),
            __import__("json").dumps(steps),
        ))
    try:
        conn = _changes_conn()
        try:
            with conn:
                paths = [p[1] for p in payload]
                if paths:
                    q = ",".join("?" for _ in paths)
                    conn.execute(
                        f"DELETE FROM session_file_changes WHERE session_id = ? AND path NOT IN ({q})",
                        (sid, *paths),
                    )
                else:
                    conn.execute("DELETE FROM session_file_changes WHERE session_id = ?", (sid,))
                conn.executemany(
                    "INSERT OR REPLACE INTO session_file_changes"
                    " (session_id, path, existed, baseline, big, first_ts, last_ts, steps_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    payload,
                )
        finally:
            conn.close()
    except Exception:
        pass


def _restore_session(sid: int, led: dict[str, Tracked]) -> None:
    """Load a persisted ledger into memory (once per process per session).

    Baselines are replayed as-is; the current text is re-read from disk, so
    files edited outside Jarvis (or after a restart) still diff correctly.
    """
    try:
        sid = int(sid or 0)
    except (TypeError, ValueError):
        return
    if not sid or sid in _persisted_sids:
        return
    _persisted_sids.add(sid)
    try:
        conn = _changes_conn()
        try:
            db_rows = conn.execute(
                "SELECT path, existed, baseline, big, first_ts, last_ts, steps_json"
                " FROM session_file_changes WHERE session_id = ?",
                (sid,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return
    if not db_rows:
        return
    import json as _json

    for r in db_rows:
        try:
            path = _norm(str(r[0] or ""))
            if not path or path in led:
                continue
            baseline = str(r[2] or "")
            big = bool(r[3])
            try:
                steps_raw = _json.loads(r[6] or "[]")
            except Exception:
                steps_raw = []
            steps = [
                Step(ts=float(s.get("ts") or 0), action=str(s.get("action") or "edit"),
                     added=int(s.get("added") or 0), removed=int(s.get("removed") or 0))
                for s in steps_raw if isinstance(s, dict)
            ][:_PERSIST_STEPS_KEPT]
            tf = Tracked(
                path=path,
                existed=bool(r[1]),
                baseline="" if big else baseline,
                big=big,
                first_ts=float(r[4] or 0),
                last_ts=float(r[5] or 0),
                steps=steps,
            )
            _settle(tf)
            # A file whose content now matches its baseline nets out — but its
            # edit history is still worth showing, so keep the row (the same
            # rule _visible() applies to live files).
            tf.current = _text_of(tf)
            if tf.current is not None and not tf.big and tf.baseline == tf.current and not steps:
                continue
            led[path] = tf
        except Exception:
            continue


def subscribe(fn: Callable[[str], None]) -> None:
    """``fn(file_id)`` runs (on the writer's thread) after a file is recorded."""
    if fn not in _listeners:
        _listeners.append(fn)


def unsubscribe(fn: Callable[[str], None]) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def record(path: str | os.PathLike, before: str | None, after: str | None, action: str = "edit") -> None:
    """Report one change. ``before`` is ``None`` when the file did not exist,
    ``after`` is ``None`` when it was deleted. Never raises."""
    try:
        fid = _record(path, before, after, action)
    except Exception:
        return
    if not fid:
        return
    for fn in list(_listeners):
        try:
            fn(fid)
        except Exception:
            pass


def _record(path, before, after, action) -> str | None:
    if before is None and after is None:
        return None
    if before is not None and before == after:
        return None
    key = _norm(path)
    now = time.time()
    big = any(t is not None and len(t) > MAX_TEXT for t in (before, after))
    with _lock:
        led = _ledger()
        tf = led.get(key)
        if tf is None:
            if len(led) >= MAX_FILES:
                return None
            if not big and _held(led) + len(before or "") + len(after or "") > MAX_LEDGER_CHARS:
                big = True
            tf = led[key] = Tracked(
                path=key, existed=before is not None, baseline="" if big else (before or ""),
                first_ts=now,
            )
        elif not big and _held(led) + len(after or "") > MAX_LEDGER_CHARS:
            big = True
        tf.big = tf.big or big
        added, removed = _count(before or "", after or "") if not tf.big else (0, 0)
        tf.steps.append(Step(now, action, added, removed))
        del tf.steps[:-MAX_STEPS]
        tf.current = after if not tf.big or after is None else ""
        if tf.big:
            tf.baseline = ""                    # counts only from here on: let the text go
        tf.sig = _sig(key)
        tf.last_ts = now
        tf.net_key = None
        sid = _session_key()
    _schedule_persist(sid)
    return file_id(key)


def _held(led: dict[str, Tracked]) -> int:
    """Characters of file text a ledger is holding on to."""
    return sum(len(t.baseline) + (len(t.current) if isinstance(t.current, str) else 0) for t in led.values())


def _settle(tf: Tracked) -> None:
    """Bring a file's net status up to date with what is on disk right now."""
    sig = _sig(tf.path)
    if sig != tf.sig:
        # Changed behind our back (a later shell command, the user's editor).
        cur = _read(tf.path)
        tf.sig = sig
        if cur is _BIG:
            tf.big, tf.current, tf.baseline = True, "", ""
        else:
            tf.current = cur
        tf.net_key = None
    key = (tf.sig, len(tf.steps), tf.last_ts)
    if tf.net_key == key:
        return
    tf.net_key = key
    cur = tf.current
    if tf.big:
        tf.added = sum(s.added for s in tf.steps)
        tf.removed = sum(s.removed for s in tf.steps)
        if cur is None:
            tf.status = "deleted" if tf.existed else "reverted"
        else:
            tf.status = "modified" if tf.existed else "added"
        return
    if cur is None:
        if not tf.existed:
            tf.status, tf.added, tf.removed = "reverted", 0, 0
        else:
            tf.status, tf.added, tf.removed = "deleted", 0, len(_lines(tf.baseline))
    elif not tf.existed:
        tf.status, tf.added, tf.removed = "added", len(_lines(cur)), 0
    elif cur == tf.baseline:
        tf.status, tf.added, tf.removed = "reverted", 0, 0
    else:
        tf.status = "modified"
        tf.added, tf.removed = _count(tf.baseline, cur)


def _text_of(tf: Tracked) -> str:
    return tf.current if isinstance(tf.current, str) else ""


def _visible(led: dict[str, Tracked]) -> list[tuple[Tracked, str | None]]:
    """Files with a net change, newest first, renames folded together.

    Returns ``[(file, renamed_from)]``; a deleted file whose text is exactly an
    added file's text is shown once, as a rename of the added file.
    """
    live = []
    for tf in led.values():
        _settle(tf)
        if tf.status != "reverted":
            live.append(tf)
    live.sort(key=lambda t: t.last_ts, reverse=True)

    deleted: dict[str, Tracked] = {}
    for tf in live:
        if tf.status == "deleted" and not tf.big and tf.baseline.strip():
            deleted.setdefault(tf.baseline, tf)

    rows: list[tuple[Tracked, str | None]] = []
    folded: set[str] = set()
    for tf in live:
        src = deleted.get(tf.current) if tf.status == "added" and not tf.big and tf.current else None
        if src is not None and src.path not in folded:
            folded.add(src.path)
            rows.append((tf, src.path))
        else:
            rows.append((tf, None))
    return [(tf, src) for tf, src in rows if tf.path not in folded]


def _row(tf: Tracked, renamed_from: str | None) -> dict[str, Any]:
    row = {
        "id": file_id(tf.path),
        "path": display_path(tf.path),
        "status": "renamed" if renamed_from else tf.status,
        "added": 0 if renamed_from else tf.added,
        "removed": 0 if renamed_from else tf.removed,
        "edits": len(tf.steps),
        "first": round(tf.first_ts, 2),
        "last": round(tf.last_ts, 2),
    }
    if renamed_from:
        row["from"] = display_path(renamed_from)
    if tf.big:
        row["big"] = True
    return row


def summaries() -> dict[str, Any]:
    """Every changed file (no diffs) plus totals — the Changes list."""
    with _lock:
        rows = _visible(_ledger())
        files = [_row(tf, src) for tf, src in rows]
    by_status: dict[str, int] = {}
    for f in files:
        by_status[f["status"]] = by_status.get(f["status"], 0) + 1
    return {
        "now": round(time.time(), 2),
        "files": files,
        "totals": {
            "files": len(files),
            "added": sum(f["added"] for f in files),
            "removed": sum(f["removed"] for f in files),
            "by_status": by_status,
        },
    }


def detail(fid: str) -> dict[str, Any] | None:
    """One file's row plus its numbered diff and edit history."""
    with _lock:
        rows = _visible(_ledger())
        for tf, src in rows:
            if file_id(tf.path) != fid:
                continue
            out = _row(tf, src)
            out["steps"] = [
                {"ts": round(s.ts, 2), "action": s.action, "added": s.added, "removed": s.removed}
                for s in tf.steps
            ]
            if tf.big:
                out.update({"hunks": [], "hidden": 0})
            elif src:
                out.update({"hunks": [], "hidden": 0})
            else:
                hunks, hidden = build_hunks(tf.baseline, _text_of(tf))
                out.update({"hunks": hunks, "hidden": hidden})
            return out
    return None


def change_event(fid: str) -> dict[str, Any]:
    """Payload of the ``change`` event: the whole list (small) and the fresh
    diff of the file that just changed."""
    return {"changes": summaries(), "focus": fid, "detail": detail(fid)}


def patch_text(fid: str | None = None) -> str:
    """The changes as one ``git apply``-able unified diff (one file with ``fid``)."""
    chunks: list[str] = []
    with _lock:
        rows = _visible(_ledger())
        for tf, src in rows:
            if fid and file_id(tf.path) != fid:
                continue
            name = display_path(tf.path)
            if tf.big:
                chunks.append(f"# {name}: too large to include\n")
                continue
            if src:
                old = display_path(src)
                chunks.append(
                    f"diff --git a/{old} b/{name}\nsimilarity index 100%\n"
                    f"rename from {old}\nrename to {name}\n"
                )
                continue
            head = [f"diff --git a/{name} b/{name}"]
            a_name, b_name = f"a/{name}", f"b/{name}"
            if not tf.existed:
                head.append("new file mode 100644")
                a_name = "/dev/null"
            elif tf.current is None:
                head.append("deleted file mode 100644")
                b_name = "/dev/null"
            body = list(difflib.unified_diff(
                _lines(tf.baseline), _lines(_text_of(tf)), a_name, b_name, lineterm="", n=3,
            ))
            if not body and not tf.existed:  # empty new file: header only
                chunks.append("\n".join(head) + "\n")
                continue
            chunks.append("\n".join(head + body) + "\n")
    return "".join(chunks)


# ── shell commands ─────────────────────────────────────────────────────────

_OPS = {";", "&&", "||", "|", "&", "(", ")", "|&"}
_REDIRECTS = {">", ">>", "<", ">&", "&>", "&>>", ">|", "<<", "<<<"}
_GLOB_CHARS = set("*?[")
_JUNK_CHARS = set(" \t\n'\"$`\\<>|&;(){}=,")


def _tokens(cmd: str) -> list[str]:
    try:
        lex = shlex.shlex(cmd, posix=os.name != "nt", punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return cmd.split()


def _inside_project(p: pathlib.Path) -> bool:
    try:
        rel = p.relative_to(CWD)
    except ValueError:
        return False
    return not (set(rel.parts) & _SKIP_PARTS)


def _walk_files(root: pathlib.Path, budget: int) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_PARTS]
        for n in names:
            out.append(pathlib.Path(base) / n)
            if len(out) >= budget:
                return out
    return out


def _shell_targets(cmd: str) -> list[str]:
    """Paths a shell command names that it might create, change or delete."""
    found: dict[str, None] = {}
    cur = pathlib.Path(CWD)
    seg: list[str] = []
    redirect_next = False

    def add(p: pathlib.Path) -> None:
        if len(found) < MAX_SHELL_FILES and _inside_project(p):
            found[str(p)] = None

    def consider(tok: str, walk: bool) -> None:
        if not tok or tok.startswith("-") or "://" in tok or len(tok) > 260 or "\x00" in tok:
            return
        raw = os.path.expanduser(tok)
        base = pathlib.Path(raw) if os.path.isabs(raw) else cur / raw
        if _GLOB_CHARS & set(tok):
            # Only expand patterns that start inside the project: `find /usr/*/*/*`
            # must not make us list half the disk.
            text = str(base)
            prefix = text[:min(text.index(c) for c in _GLOB_CHARS if c in text)]
            # "dir/" is the directory itself; "dir/te" is a partial name inside dir.
            root = pathlib.Path(prefix if prefix.endswith(os.sep) else os.path.dirname(prefix))
            if not _inside_project(root):
                return
            found_globs = itertools.islice(glob.iglob(text), MAX_SHELL_FILES * 3)
            matches = [pathlib.Path(m) for m in sorted(found_globs)[:MAX_SHELL_FILES]]
        else:
            matches = [base]
        for m in matches:
            try:
                m = pathlib.Path(os.path.normpath(m))
                if m.is_file():
                    add(m)
                elif m.is_dir():
                    if walk and _inside_project(m):
                        for f in _walk_files(m, MAX_SHELL_FILES - len(found)):
                            add(f)
                elif not _JUNK_CHARS & set(tok) and m.parent.is_dir():
                    add(m)          # might be created (mv/cp target, redirect)
            except OSError:
                continue

    def flush() -> None:
        nonlocal cur
        words, seg[:] = list(seg), []
        if not words:
            return
        head = os.path.basename(words[0])
        if head == "cd" and len(words) > 1:
            nxt = pathlib.Path(os.path.expanduser(words[1]))
            nxt = nxt if nxt.is_absolute() else cur / nxt
            if nxt.is_dir():
                cur = pathlib.Path(os.path.normpath(nxt))
            return
        flags = [w for w in words[1:] if w.startswith("-") and not w.startswith("--")]
        recursive = any(("r" in f or "R" in f) for f in flags) or "--recursive" in words
        sub = words[1] if head == "git" and len(words) > 1 else ""
        walk = head in {"mv", "move"} or (head in {"rm", "cp", "rmdir", "rd", "del", "copy"} and recursive) \
            or sub in {"mv", "rm", "checkout", "restore"}
        for w in words[1:]:
            consider(w, walk)

    for tok in _tokens(cmd):
        if tok in _OPS:
            flush()
            redirect_next = False
        elif tok in _REDIRECTS:
            redirect_next = True
        elif redirect_next:
            redirect_next = False
            consider(tok, False)
        else:
            seg.append(tok)
    flush()
    return list(found)


def watch_shell(cmd: str) -> Callable[[], None]:
    """Call before running a shell command; call the result after it.

    Snapshots the files the command line names (and paths it could create),
    then records whatever really changed — deletions (``rm``), renames (``mv``),
    in-place edits (``sed -i``), copies, redirects. Nothing is recorded for
    files that did not change. Never raises.
    """
    try:
        before: dict[str, Any] = {}
        budget = MAX_SHELL_BYTES
        for path in _shell_targets(cmd):
            text = _read(path)
            if text is _BIG:
                continue
            budget -= len(text or "")
            if budget < 0:
                break
            before[path] = text
    except Exception:
        return lambda: None
    if not before:
        return lambda: None

    def finish() -> None:
        try:
            for path, old in before.items():
                new = _read(path)
                if old is None and new is None and os.path.isdir(path):
                    # A directory appeared (cp -r, mv dir, a scaffolder): its files are new.
                    for f in _walk_files(pathlib.Path(path), MAX_SHELL_FILES):
                        text = _read(str(f))
                        if isinstance(text, str) and _inside_project(f):
                            record(f, None, text, "create")
                    continue
                if new is _BIG or new == old:
                    continue
                if old is None:
                    record(path, None, new, "create")
                elif new is None:
                    record(path, old, None, "delete")
                else:
                    record(path, old, new, "edit")
        except Exception:
            pass

    return finish
