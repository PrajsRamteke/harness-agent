"""Persistent skill / experience memory.

Separate from user-facts memory. Stores short lessons the agent learned while
solving past tasks (pattern -> solution / gotcha / shortcut) so future similar
tasks can be answered faster with fewer tool calls.

Shape on disk:
    {"skills": [{"id": 1,
                 "task": "short description of task pattern",
                 "lesson": "what worked / what to do",
                 "tags": ["git", "rebase"],
                 "ts": 1710000000,
                 "hits": 0}, ...],
     "next_id": 2}
"""
import json, os, time, re
from typing import List, Dict

from ..constants import SKILLS_FILE, CONFIG_DIR, FILE_PERMISSION

MAX_INJECT = 8          # how many skills to show in system prompt at most
MAX_STORE = 200         # cap total entries; oldest low-hit pruned beyond this


def _load() -> Dict:
    if not SKILLS_FILE.exists():
        return {"skills": [], "next_id": 1}
    try:
        data = json.loads(SKILLS_FILE.read_text())
        data.setdefault("skills", [])
        data.setdefault("next_id", 1)
        return data
    except (json.JSONDecodeError, OSError):
        return {"skills": [], "next_id": 1}


def _save(data: Dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    try: os.chmod(SKILLS_FILE, FILE_PERMISSION)
    except OSError: pass


def _prune(data: Dict) -> None:
    if len(data["skills"]) <= MAX_STORE:
        return
    data["skills"].sort(key=lambda s: (s.get("hits", 0), s.get("ts", 0)))
    data["skills"] = data["skills"][-MAX_STORE:]


def list_skills() -> List[Dict]:
    return _load()["skills"]


def add_skill(task: str, lesson: str, tags: List[str] | None = None) -> Dict:
    task = (task or "").strip()
    lesson = (lesson or "").strip()
    if not task or not lesson:
        raise ValueError("task and lesson are required")
    tags = [t.strip().lower() for t in (tags or []) if t and t.strip()]
    data = _load()
    # dedupe on (task, lesson) case-insensitive
    for s in data["skills"]:
        if s["task"].lower() == task.lower() and s["lesson"].lower() == lesson.lower():
            # merge tags, bump ts
            s["tags"] = sorted(set(s.get("tags", []) + tags))
            s["ts"] = int(time.time())
            _save(data)
            return s
    skill = {
        "id": data["next_id"], "task": task, "lesson": lesson,
        "tags": sorted(set(tags)), "ts": int(time.time()), "hits": 0,
    }
    data["skills"].append(skill)
    data["next_id"] += 1
    _prune(data)
    _save(data)
    return skill


def delete_skill(skill_id: int) -> bool:
    data = _load()
    before = len(data["skills"])
    data["skills"] = [s for s in data["skills"] if s["id"] != skill_id]
    if len(data["skills"]) == before:
        return False
    _save(data)
    return True


def clear_all() -> int:
    data = _load()
    n = len(data["skills"])
    data["skills"] = []
    _save(data)
    return n


_WORD = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> set:
    return set(_WORD.findall(text.lower()))


def search(query: str, limit: int = 5) -> List[Dict]:
    q = _tokens(query)
    if not q:
        return []
    scored = []
    data = _load()
    for s in data["skills"]:
        hay = _tokens(s["task"]) | _tokens(s["lesson"]) | set(s.get("tags", []))
        overlap = len(q & hay)
        if overlap:
            scored.append((overlap, s.get("hits", 0), s))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [s for _, _, s in scored[:limit]]


def bump_hits(skill_id: int) -> None:
    data = _load()
    for s in data["skills"]:
        if s["id"] == skill_id:
            s["hits"] = s.get("hits", 0) + 1
            _save(data)
            return


def as_prompt_block() -> str:
    """Render top skills as a compact system-prompt block."""
    data = _load()
    skills = sorted(
        data["skills"],
        key=lambda s: (s.get("hits", 0), s.get("ts", 0)),
        reverse=True,
    )[:MAX_INJECT]
    if not skills:
        return ""
    lines = []
    for s in skills:
        tag_str = f" [{', '.join(s['tags'])}]" if s.get("tags") else ""
        lines.append(f"- #{s['id']}{tag_str} when: {s['task']} → {s['lesson']}")
    return (
        "PAST LESSONS (skills you've learned on earlier tasks — apply when relevant, "
        "call skill_search for more):\n" + "\n".join(lines)
    )
