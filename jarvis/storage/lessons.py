"""Persistent lesson / experience memory.

Separate from user-facts memory. Stores short lessons the agent learned while
solving past tasks (pattern -> solution / gotcha / shortcut) so future similar
tasks can be answered faster with fewer tool calls.

Shape on disk:
    {"lessons": [{"id": 1,
                  "task": "short description of task pattern",
                  "lesson": "what worked / what to do",
                  "tags": ["git", "rebase"],
                  "ts": 1710000000,
                  "hits": 0}, ...],
     "next_id": 2}
"""
import json, os, threading, time, re
from typing import List, Dict

from ..constants import LESSONS_FILE, CONFIG_DIR, FILE_PERMISSION

# Thread-level lock to prevent concurrent read/write races on the JSON file
# when tool calls execute in parallel via ThreadPoolExecutor.
_file_lock = threading.Lock()

MAX_INJECT = 8          # how many lessons to show in system prompt at most
MAX_STORE = 200         # cap total entries; oldest low-hit pruned beyond this


def _load() -> Dict:
    if not LESSONS_FILE.exists():
        return {"lessons": [], "next_id": 1}
    try:
        data = json.loads(LESSONS_FILE.read_text())
        data.setdefault("lessons", [])
        data.setdefault("next_id", 1)
        return data
    except (json.JSONDecodeError, OSError):
        return {"lessons": [], "next_id": 1}


def _save(data: Dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LESSONS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    try: os.chmod(LESSONS_FILE, FILE_PERMISSION)
    except OSError: pass


def _prune(data: Dict) -> None:
    if len(data["lessons"]) <= MAX_STORE:
        return
    data["lessons"].sort(key=lambda s: (s.get("hits", 0), s.get("ts", 0)))
    data["lessons"] = data["lessons"][-MAX_STORE:]


def list_lessons() -> List[Dict]:
    with _file_lock:
        return _load()["lessons"]


def add_lesson(task: str, lesson: str, tags: List[str] | None = None) -> Dict:
    task = (task or "").strip()
    lesson = (lesson or "").strip()
    if not task or not lesson:
        raise ValueError("task and lesson are required")
    tags = [t.strip().lower() for t in (tags or []) if t and t.strip()]
    with _file_lock:
        data = _load()
        # dedupe on (task, lesson) case-insensitive
        for s in data["lessons"]:
            if s["task"].lower() == task.lower() and s["lesson"].lower() == lesson.lower():
                # merge tags, bump ts
                s["tags"] = sorted(set(s.get("tags", []) + tags))
                s["ts"] = int(time.time())
                _save(data)
                return s
        lesson_entry = {
            "id": data["next_id"], "task": task, "lesson": lesson,
            "tags": sorted(set(tags)), "ts": int(time.time()), "hits": 0,
        }
        data["lessons"].append(lesson_entry)
        data["next_id"] += 1
        _prune(data)
        _save(data)
    return lesson_entry


def delete_lesson(lesson_id: int) -> bool:
    with _file_lock:
        data = _load()
        before = len(data["lessons"])
        data["lessons"] = [s for s in data["lessons"] if s["id"] != lesson_id]
        if len(data["lessons"]) == before:
            return False
        _save(data)
    return True


def clear_all() -> int:
    with _file_lock:
        data = _load()
        n = len(data["lessons"])
        data["lessons"] = []
        _save(data)
    return n


_WORD = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> set:
    return set(_WORD.findall(text.lower()))


def search(query: str, limit: int = 15) -> List[Dict]:
    q = _tokens(query)
    if not q:
        return []
    scored = []
    with _file_lock:
        data = _load()
        for s in data["lessons"]:
            hay = _tokens(s["task"]) | _tokens(s["lesson"]) | set(s.get("tags", []))
            overlap = len(q & hay)
            if overlap:
                scored.append((overlap, s.get("hits", 0), s))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [s for _, _, s in scored[:limit]]


def bump_hits(lesson_id: int) -> None:
    with _file_lock:
        data = _load()
        for s in data["lessons"]:
            if s["id"] == lesson_id:
                s["hits"] = s.get("hits", 0) + 1
                _save(data)
                return


def as_prompt_block() -> str:
    """Render top lessons as a compact system-prompt block.

    Only includes a count + tag clouds — not the full lesson text. Full
    lessons are loaded on demand via lesson_search(). This saves ~3-5K
    chars per turn (the full text of top 8 lessons).
    """
    with _file_lock:
        data = _load()
        total = len(data["lessons"])
        top = sorted(
            data["lessons"],
            key=lambda s: (s.get("hits", 0), s.get("ts", 0)),
            reverse=True,
        )[:MAX_INJECT]
    if total == 0:
        return ""
    # Build a compact tag cloud from top lessons
    all_tags: set[str] = set()
    for s in top:
        all_tags.update(s.get("tags", []))
    tag_cloud = ", ".join(sorted(all_tags)) if all_tags else "general"
    return (
        f"PAST LESSONS: {total} total (topics: {tag_cloud}). "
        f"Use lesson_search('<topic>') only when prior experience could help the current task."
    )
