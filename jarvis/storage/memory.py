"""Persistent personal memory — small JSON store of facts about the user.

Shape on disk:
    {"facts": [{"id": 1, "text": "name: Prajwal", "ts": 1710000000}, ...],
     "next_id": 2}
"""
import json, os, time
from typing import List, Dict, Optional

from ..constants import MEMORY_FILE, CONFIG_DIR, FILE_PERMISSION


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
    return _load()["facts"]


def add_fact(text: str) -> Dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty fact")
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


def delete_fact(fact_id: int) -> bool:
    data = _load()
    before = len(data["facts"])
    data["facts"] = [f for f in data["facts"] if f["id"] != fact_id]
    if len(data["facts"]) == before:
        return False
    _save(data)
    return True


def clear_all() -> int:
    data = _load()
    n = len(data["facts"])
    data["facts"] = []
    _save(data)
    return n


def as_prompt_block() -> str:
    """Render current memory as a short block for injection into the system prompt."""
    facts = list_facts()
    if not facts:
        return ""
    lines = [f"- {f['text']}" for f in facts]
    return "WHAT YOU REMEMBER ABOUT THE USER (use when relevant, do not recite unless asked):\n" + "\n".join(lines)
