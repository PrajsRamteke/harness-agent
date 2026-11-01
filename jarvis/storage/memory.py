"""Persistent personal memory — small JSON store of facts about the user.

Shape on disk:
    {"facts": [{"id": 1, "text": "name: Prajwal", "ts": 1710000000}, ...],
     "next_id": 2}
"""
import json, os, threading, time
from typing import List, Dict, Optional

from ..constants import MEMORY_FILE, CONFIG_DIR, FILE_PERMISSION

# Thread-level lock so concurrent tool calls (from ThreadPoolExecutor in
# render.py) don't race on read/write of the JSON file.
_file_lock = threading.Lock()


def _load() -> Dict:
    if not MEMORY_FILE.exists():
        return {"facts": [], "next_id": 1}
    try:
        data = json.loads(MEMORY_FILE.read_text())
        data.setdefault("facts", [])
        data.setdefault("next_id", 1)
        return data
    except (json.JSONDecodeError, OSError):
        return {"facts": [], "next_id": 1}


def _save(data: Dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    try: os.chmod(MEMORY_FILE, FILE_PERMISSION)
    except OSError: pass


def list_facts() -> List[Dict]:
    with _file_lock:
        return _load()["facts"]


def add_fact(text: str) -> Dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty fact")
    with _file_lock:
        data = _load()
        # dedupe on case-insensitive exact text
        for f in data["facts"]:
            if f["text"].lower() == text.lower():
                return f
        fact = {"id": data["next_id"], "text": text, "ts": int(time.time())}
        data["facts"].append(fact)
        data["next_id"] += 1
        _save(data)
    return fact


def update_fact(fact_id: int, text: str) -> Optional[Dict]:
    """Rewrite one fact's text. None if it is gone; ValueError if empty or a copy of another fact."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty fact")
    with _file_lock:
        data = _load()
        target = next((f for f in data["facts"] if f["id"] == fact_id), None)
        if target is None:
            return None
        if any(f["id"] != fact_id and f["text"].lower() == text.lower() for f in data["facts"]):
            raise ValueError("duplicate fact")
        target["text"] = text
        _save(data)
    return target


def restore_facts(facts: List[Dict]) -> int:
    """Put back deleted facts (undo), keeping their ids and dates. Skips ids / texts already there."""
    with _file_lock:
        data = _load()
        have_ids = {f["id"] for f in data["facts"]}
        have_text = {f["text"].lower() for f in data["facts"]}
        added = 0
        for f in facts:
            text = str(f.get("text") or "").strip()
            if not text or f.get("id") in have_ids or text.lower() in have_text:
                continue
            data["facts"].append({"id": f["id"], "text": text, "ts": int(f.get("ts") or time.time())})
            have_ids.add(f["id"])
            have_text.add(text.lower())
            added += 1
        if added:
            data["facts"].sort(key=lambda f: f["id"])
            data["next_id"] = max(data["next_id"], data["facts"][-1]["id"] + 1)
            _save(data)
    return added


def delete_fact(fact_id: int) -> bool:
    with _file_lock:
        data = _load()
        before = len(data["facts"])
        data["facts"] = [f for f in data["facts"] if f["id"] != fact_id]
        if len(data["facts"]) == before:
            return False
        _save(data)
    return True


def clear_all() -> int:
    with _file_lock:
        data = _load()
        n = len(data["facts"])
        data["facts"] = []
        _save(data)
    return n


def as_prompt_block() -> str:
    """Render current memory as a short block for injection into the system prompt.

    Only includes a count summary — not the full facts. The full facts are
    loaded on demand when the agent calls memory_list(). This saves ~2-4K
    chars per turn when many facts are stored.
    """
    facts = list_facts()
    if not facts:
        return ""
    n = len(facts)
    return (
        f"WHAT YOU REMEMBER ABOUT THE USER: {n} fact{'s' if n != 1 else ''} stored. "
        f"Use memory_list() to view all when relevant — summaries are not injected here to save tokens."
    )
