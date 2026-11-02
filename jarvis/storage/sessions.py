"""Persistent session storage backed by SQLite."""
import json, os, sqlite3, threading, time
from typing import Dict, Iterable, List, Optional

from ..constants import CONFIG_DIR, SESSIONS_DB, SESSION_TITLE_MAX_LENGTH, SESSIONS_LIST_LIMIT
from ..utils.serialize import _msg_to_json


# Columns added after the first release. ``_ensure_schema`` adds whichever an
# older sessions.db lacks (once per database per process):
#   cwd         — the project folder the chat belongs to ("" = not recorded:
#                 chats from before this column; see session_library.backfill)
#   cwd_guessed — 1 when ``cwd`` was worked out from the chat's file paths
#   pinned      — kept at the top of every list
#   archived    — hidden from the lists unless you look at Archived
_EXTRA_COLUMNS = (
    ("cwd", "TEXT NOT NULL DEFAULT ''"),
    ("cwd_guessed", "INTEGER NOT NULL DEFAULT 0"),
    ("pinned", "INTEGER NOT NULL DEFAULT 0"),
    ("archived", "INTEGER NOT NULL DEFAULT 0"),
)
_schema_ready: set = set()
_schema_lock = threading.Lock()


def _ensure_schema(c: sqlite3.Connection) -> None:
    key = str(SESSIONS_DB)
    if key in _schema_ready:
        return
    with _schema_lock:
        if key in _schema_ready:
            return
        _create_tables(c)
        have = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
        for name, decl in _EXTRA_COLUMNS:
            if name not in have:
                try:
                    c.execute(f"ALTER TABLE sessions ADD COLUMN {name} {decl}")
                except sqlite3.OperationalError:
                    pass  # another Jarvis added it a moment ago
        c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_cwd ON sessions(cwd, updated_at)")
        c.commit()
        _schema_ready.add(key)


def db_conn() -> sqlite3.Connection:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(SESSIONS_DB))
    c.row_factory = sqlite3.Row
    _ensure_schema(c)
    return c


def _create_tables(c: sqlite3.Connection) -> None:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            model TEXT,
            created_at REAL,
            updated_at REAL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            idx INTEGER,
            role TEXT,
            content_json TEXT,
            created_at REAL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, idx);
        """)


def db_init():
    with db_conn():
        pass  # db_conn creates / upgrades the tables


def _here() -> str:
    try:
        return os.getcwd()
    except OSError:  # the folder was deleted under us
        return ""


def db_create_session(model: str, cwd: Optional[str] = None) -> int:
    """A new, empty session in ``cwd`` (default: the folder Jarvis runs in)."""
    now = time.time()
    folder = _here() if cwd is None else str(cwd or "")
    with db_conn() as c:
        cur = c.execute(
            "INSERT INTO sessions (title, model, created_at, updated_at, cwd) VALUES (?,?,?,?,?)",
            (None, model, now, now, folder))
        return cur.lastrowid


def db_set_session_cwd(session_id, cwd: str, *, guessed: bool = False) -> bool:
    """Record the project folder a session belongs to (a chat that moved)."""
    if not session_id:
        return False
    with db_conn() as c:
        cur = c.execute("UPDATE sessions SET cwd=?, cwd_guessed=? WHERE id=?",
                        (str(cwd or ""), 1 if guessed else 0, int(session_id)))
        return cur.rowcount > 0


def _ids(session_ids: Iterable) -> List[int]:
    out = []
    for x in session_ids or ():
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in out:
            out.append(n)
    return out


def _set_flag(column: str, session_ids: Iterable, value: bool) -> int:
    ids = _ids(session_ids)
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    with db_conn() as c:
        cur = c.execute(f"UPDATE sessions SET {column}=? WHERE id IN ({marks})", (1 if value else 0, *ids))
        return cur.rowcount


def db_set_pinned(session_ids: Iterable, pinned: bool) -> int:
    """Pin / unpin sessions (kept at the top of every list). Returns how many changed."""
    return _set_flag("pinned", session_ids, pinned)


def db_set_archived(session_ids: Iterable, archived: bool) -> int:
    """Archive / restore sessions. An archived one is also unpinned."""
    n = _set_flag("archived", session_ids, archived)
    if archived:
        _set_flag("pinned", session_ids, False)
    return n


def db_session_info(session_id) -> Optional[Dict]:
    """One session's row (id, title, model, times, cwd, pinned, archived, msg_count)."""
    if not session_id:
        return None
    with db_conn() as c:
        row = c.execute(f"SELECT {_ROW_COLS} FROM sessions s WHERE s.id=?", (int(session_id),)).fetchone()
    return dict(row) if row else None


def db_append_message(session_id: int, idx: int, msg: Dict):
    serialized = _msg_to_json(msg)
    content = serialized["content"]
    content_json = json.dumps(content) if not isinstance(content, str) else json.dumps(content)
    with db_conn() as c:
        c.execute(
            "INSERT INTO messages (session_id, idx, role, content_json, created_at) VALUES (?,?,?,?,?)",
            (session_id, idx, msg["role"], content_json, time.time()))
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))


def db_replace_session_messages(session_id: int, msgs: List[Dict]):
    """Rewrite all messages for a session."""
    with db_conn() as c:
        c.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        now = time.time()
        for i, m in enumerate(msgs):
            s = _msg_to_json(m)
            content_json = json.dumps(s["content"])
            c.execute(
                "INSERT INTO messages (session_id, idx, role, content_json, created_at) VALUES (?,?,?,?,?)",
                (session_id, i, m["role"], content_json, now))
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))


def clean_title(title: str) -> str:
    """One line, collapsed spaces, at most ``SESSION_TITLE_MAX_LENGTH`` chars."""
    return " ".join((title or "").split())[:SESSION_TITLE_MAX_LENGTH].strip()


# session id → (title, read at). The sidebar, the web state poll and the
# project switcher all ask every second or two; a rename in this process
# updates it at once, one from another Jarvis shows within ``_TITLE_TTL``.
_TITLE_TTL = 3.0
_title_cache: Dict[tuple, tuple] = {}  # keyed by database too (tests swap it)
_title_lock = threading.Lock()


def _title_key(session_id) -> tuple:
    return (str(SESSIONS_DB), int(session_id))


def _remember_title(session_id: int, title: str) -> None:
    with _title_lock:
        _title_cache[_title_key(session_id)] = (title, time.monotonic())


def db_get_title(session_id) -> str:
    """The stored title of a session ("" when it has none yet)."""
    if not session_id:
        return ""
    sid = int(session_id)
    with _title_lock:
        hit = _title_cache.get(_title_key(sid))
    if hit and time.monotonic() - hit[1] < _TITLE_TTL:
        return hit[0]
    try:
        with db_conn() as c:
            row = c.execute("SELECT title FROM sessions WHERE id=?", (sid,)).fetchone()
    except sqlite3.Error:
        return hit[0] if hit else ""
    title = str((row["title"] if row else "") or "").strip()
    _remember_title(sid, title)
    return title


def db_set_title_if_empty(session_id: int, title: str):
    title = clean_title(title)
    if not title:
        return
    with db_conn() as c:
        row = c.execute("SELECT title FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row and not row["title"]:
            c.execute("UPDATE sessions SET title=? WHERE id=?", (title, session_id))
            _remember_title(session_id, title)


def db_rename_session(session_id: int, title: str) -> bool:
    """Give a session a new title. False when the session doesn't exist.

    Raises ``ValueError`` for a blank title. A renamed session keeps its name:
    the first message only fills in a title that is still empty.
    """
    title = clean_title(title)
    if not title:
        raise ValueError("title can't be empty")
    with db_conn() as c:
        cur = c.execute("UPDATE sessions SET title=? WHERE id=?", (title, int(session_id)))
        found = cur.rowcount > 0
    if found:
        _remember_title(session_id, title)
    return found


_ROW_COLS = """s.id, s.title, s.model, s.created_at, s.updated_at,
       s.cwd, s.cwd_guessed, s.pinned, s.archived,
       (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS msg_count"""

_SORTS = {
    "recent": "s.updated_at DESC",
    "oldest": "s.updated_at ASC",
    "created": "s.created_at DESC",
    "longest": "msg_count DESC, s.updated_at DESC",
}


def _where(cwd: Optional[str], archived: Optional[bool], pinned: Optional[bool],
           ids: Optional[List[int]] = None) -> tuple:
    """WHERE clause shared by list and count. ``None`` = don't filter on it."""
    parts = ["EXISTS (SELECT 1 FROM messages m WHERE m.session_id=s.id)"]
    args: list = []
    if cwd is not None:
        parts.append("s.cwd=?")
        args.append(str(cwd))
    if archived is not None:
        parts.append("s.archived=?")
        args.append(1 if archived else 0)
    if pinned is not None:
        parts.append("s.pinned=?")
        args.append(1 if pinned else 0)
    if ids is not None:
        parts.append(f"s.id IN ({','.join('?' * len(ids)) or 'NULL'})")
        args.extend(ids)
    return " AND ".join(parts), args


def db_list_sessions(limit: int = SESSIONS_LIST_LIMIT, offset: int = 0, *,
                     cwd: Optional[str] = None, archived: Optional[bool] = False,
                     pinned: Optional[bool] = None, sort: str = "recent",
                     pinned_first: bool = False, ids: Optional[List[int]] = None) -> List[sqlite3.Row]:
    """Sessions with at least one message, newest first.

    ``cwd`` keeps one project folder ("" = chats whose folder isn't recorded);
    ``archived`` False (default) hides archived ones, True lists only those,
    None both; ``pinned_first`` puts pinned ones on top.
    """
    where, args = _where(cwd, archived, pinned, ids)
    order = _SORTS.get(sort, _SORTS["recent"])
    if pinned_first:
        order = "s.pinned DESC, " + order
    with db_conn() as c:
        return c.execute(
            f"SELECT {_ROW_COLS} FROM sessions s WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            (*args, int(limit), int(offset))).fetchall()


def db_count_sessions(*, cwd: Optional[str] = None, archived: Optional[bool] = False,
                      pinned: Optional[bool] = None) -> int:
    """How many sessions with at least one message match (same filters as the list)."""
    where, args = _where(cwd, archived, pinned)
    with db_conn() as c:
        row = c.execute(f"SELECT COUNT(*) AS cnt FROM sessions s WHERE {where}", args).fetchone()
        return row["cnt"] if row else 0


def db_session_folders() -> List[Dict]:
    """Every project folder that has chats: cwd, count, archived, pinned, last."""
    with db_conn() as c:
        rows = c.execute("""
            SELECT s.cwd AS cwd,
                   SUM(CASE WHEN s.archived=0 THEN 1 ELSE 0 END) AS count,
                   SUM(CASE WHEN s.archived=1 THEN 1 ELSE 0 END) AS archived,
                   SUM(CASE WHEN s.pinned=1 AND s.archived=0 THEN 1 ELSE 0 END) AS pinned,
                   MAX(s.updated_at) AS last
            FROM sessions s
            WHERE EXISTS (SELECT 1 FROM messages m WHERE m.session_id=s.id)
            GROUP BY s.cwd ORDER BY last DESC""").fetchall()
    return [dict(r) for r in rows]


def db_search_session_ids(query: str, *, cwd: Optional[str] = None,
                          archived: Optional[bool] = False, limit: int = 200) -> List[int]:
    """Sessions whose title or any message contains ``query`` (case-insensitive
    for ASCII), newest first. Message text is matched in its stored JSON, so a
    query is tried both as typed and JSON-escaped (quotes, non-ASCII)."""
    q = (query or "").strip()
    if not q:
        return []
    escaped = json.dumps(q)[1:-1]

    def like(x: str) -> str:
        return "%" + x.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    where, args = _where(cwd, archived, None)
    pats = [like(q)] if escaped == q else [like(q), like(escaped)]
    content = " OR ".join("m.content_json LIKE ? ESCAPE '\\'" for _ in pats)
    with db_conn() as c:
        rows = c.execute(
            f"""SELECT s.id FROM sessions s WHERE {where} AND (
                    s.title LIKE ? ESCAPE '\\' OR CAST(s.id AS TEXT)=? OR s.model LIKE ? ESCAPE '\\'
                    OR EXISTS (SELECT 1 FROM messages m WHERE m.session_id=s.id AND ({content})))
                ORDER BY s.pinned DESC, s.updated_at DESC LIMIT ?""",
            (*args, like(q), q.lstrip("#"), like(q), *pats, int(limit))).fetchall()
    return [int(r["id"]) for r in rows]


def db_first_matches(session_id: int, query: str, limit: int = 6) -> List[sqlite3.Row]:
    """The first few messages of a session whose JSON contains ``query`` (for a snippet)."""
    q = (query or "").strip()
    if not q:
        return []
    escaped = json.dumps(q)[1:-1]
    with db_conn() as c:
        return c.execute(
            """SELECT idx, role, content_json FROM messages WHERE session_id=?
               AND (instr(lower(content_json), lower(?)) > 0 OR instr(lower(content_json), lower(?)) > 0)
               ORDER BY idx ASC LIMIT ?""", (int(session_id), q, escaped, int(limit))).fetchall()


def db_load_session(session_id: int) -> Optional[List[Dict]]:
    with db_conn() as c:
        row = c.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row: return None
        rows = c.execute(
            "SELECT role, content_json FROM messages WHERE session_id=? ORDER BY idx ASC",
            (session_id,)).fetchall()
    out = []
    for r in rows:
        content = json.loads(r["content_json"])
        out.append({"role": r["role"], "content": content})
    return out


# Per-chat numbers that aren't in the history: the Claude Code CLI's cost
# estimate and the exact context size of the last request. Kept beside the
# messages so leaving a chat and coming back shows them again.
_STATS_SQL = """CREATE TABLE IF NOT EXISTS session_stats (
    session_id INTEGER PRIMARY KEY,
    claude_cost_usd REAL NOT NULL DEFAULT 0,
    context_tokens INTEGER NOT NULL DEFAULT 0,
    updated REAL NOT NULL DEFAULT 0
)"""


def db_save_session_stats(session_id, *, claude_cost_usd: float, context_tokens: int) -> None:
    if not session_id:
        return
    c = db_conn()
    try:
        with c:
            c.execute(_STATS_SQL)
            c.execute(
                "INSERT INTO session_stats (session_id, claude_cost_usd, context_tokens, updated) "
                "VALUES (?, ?, ?, strftime('%s','now')) ON CONFLICT(session_id) DO UPDATE SET "
                "claude_cost_usd=excluded.claude_cost_usd, context_tokens=excluded.context_tokens, "
                "updated=excluded.updated",
                (int(session_id), float(claude_cost_usd or 0), int(context_tokens or 0)),
            )
    finally:
        c.close()


def db_load_session_stats(session_id) -> Optional[Dict]:
    if not session_id:
        return None
    c = db_conn()
    try:
        c.execute(_STATS_SQL)
        row = c.execute("SELECT claude_cost_usd, context_tokens FROM session_stats WHERE session_id=?",
                        (int(session_id),)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        c.close()
    if row is None:
        return None
    return {"claude_cost_usd": float(row[0] or 0), "context_tokens": int(row[1] or 0)}


def restore_session_stats(state, session_id) -> None:
    """After resuming ``session_id`` into ``state``: its saved cost and the
    exact context size (better than the chars/4 estimate) come back."""
    state.claude_cost_usd = 0.0
    try:
        stats = db_load_session_stats(session_id)
    except Exception:
        stats = None
    if not stats:
        return
    state.claude_cost_usd = stats["claude_cost_usd"]
    if stats["context_tokens"]:
        state.total_in = stats["context_tokens"]


def db_delete_session(session_id: int) -> bool:
    c = db_conn()
    try:
        with c:
            cur = c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            c.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            for table in ("session_file_changes", "session_stats"):
                try:
                    c.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
                except sqlite3.OperationalError:
                    pass  # table not created yet — nothing ever recorded
        deleted = cur.rowcount > 0
    finally:
        c.close()
    with _title_lock:
        _title_cache.pop(_title_key(session_id), None)
    # Only after the commit: forget_session opens its own connection, and doing
    # that inside the transaction above blocked on our own write lock for
    # sqlite's 5 s busy timeout — every /session delete froze the UI.
    try:
        from .. import file_changes

        file_changes.forget_session(session_id)
    except Exception:
        pass
    return deleted
