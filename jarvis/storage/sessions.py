"""Persistent session storage backed by SQLite."""
import json, sqlite3, threading, time
from typing import Dict, List, Optional

from ..constants import CONFIG_DIR, SESSIONS_DB, SESSION_TITLE_MAX_LENGTH, SESSIONS_LIST_LIMIT
from ..utils.serialize import _msg_to_json


def db_conn() -> sqlite3.Connection:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(SESSIONS_DB))
    c.row_factory = sqlite3.Row
    return c


def db_init():
    with db_conn() as c:
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


def db_create_session(model: str) -> int:
    now = time.time()
    with db_conn() as c:
        cur = c.execute(
            "INSERT INTO sessions (title, model, created_at, updated_at) VALUES (?,?,?,?)",
            (None, model, now, now))
        return cur.lastrowid


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


def db_list_sessions(limit: int = SESSIONS_LIST_LIMIT, offset: int = 0) -> List[sqlite3.Row]:
    with db_conn() as c:
        return c.execute("""
            SELECT s.id, s.title, s.model, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS msg_count
            FROM sessions s
            WHERE EXISTS (SELECT 1 FROM messages m WHERE m.session_id=s.id)
            ORDER BY s.updated_at DESC LIMIT ? OFFSET ?""", (limit, offset)).fetchall()


def db_count_sessions() -> int:
    """Total number of sessions with at least one message."""
    with db_conn() as c:
        row = c.execute("""
            SELECT COUNT(*) AS cnt FROM sessions s
            WHERE EXISTS (SELECT 1 FROM messages m WHERE m.session_id=s.id)
        """).fetchone()
        return row["cnt"] if row else 0


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
