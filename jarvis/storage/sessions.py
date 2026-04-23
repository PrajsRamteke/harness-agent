"""Persistent session storage backed by SQLite."""
import json, sqlite3, time
from typing import Dict, List, Optional

from ..constants import CONFIG_DIR, SESSIONS_DB
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
    """Rewrite all messages for a session (used after /retry etc.)."""
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


def db_set_title_if_empty(session_id: int, title: str):
    title = (title or "").strip().replace("\n", " ")[:80]
    if not title:
        return
    with db_conn() as c:
        row = c.execute("SELECT title FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row and not row["title"]:
            c.execute("UPDATE sessions SET title=? WHERE id=?", (title, session_id))


def db_list_sessions(limit: int = 50) -> List[sqlite3.Row]:
    with db_conn() as c:
        return c.execute("""
            SELECT s.id, s.title, s.model, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS msg_count
            FROM sessions s
            WHERE EXISTS (SELECT 1 FROM messages m WHERE m.session_id=s.id)
            ORDER BY s.updated_at DESC LIMIT ?""", (limit,)).fetchall()


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


def db_delete_session(session_id: int) -> bool:
    with db_conn() as c:
        cur = c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        c.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        return cur.rowcount > 0
