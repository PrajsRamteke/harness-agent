"""Memory facts and lessons for the web remote's Memory dialog (``static/js/memory.js``).

``GET /api/memory`` → ``public()``; ``POST /api/memory {op, kind, …}`` → ``apply()``,
then every open page gets a ``memory`` event carrying the fresh ``public()``.
Same files as the terminal's /memory and /lesson (``storage/memory.py``,
``storage/lessons.py``), which lock them themselves — so unlike pins this runs
on the HTTP thread, no terminal repaint is needed.

``kind`` is ``fact`` or ``lesson``. ops: ``add`` · ``update {id, …}`` ·
``delete {id}`` · ``clear`` · ``restore {items}`` (undo of a delete / clear).
fact fields: ``text``. lesson fields: ``task``, ``lesson``, ``tags`` (list or "a, b").
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ..constants import LESSONS_FILE, MEMORY_FILE
from ..storage import lessons as lesson_store
from ..storage import memory as memory_store

MAX_FACT_CHARS = 2_000
MAX_TASK_CHARS = 300
MAX_LESSON_CHARS = 4_000
MAX_TAGS = 12
MAX_TAG_CHARS = 32
MAX_RESTORE = 500
TOP_TAGS = 30

KINDS = ("fact", "lesson")


def public() -> dict[str, Any]:
    """Both stores: ``{memory: {items, count, chars, file}, lessons: {items, count, hits, limit, tags, file}}``."""
    facts = [{"id": f["id"], "text": f["text"], "ts": f.get("ts", 0)} for f in memory_store.list_facts()]
    lessons = [
        {"id": s["id"], "task": s["task"], "lesson": s["lesson"], "tags": list(s.get("tags", [])),
         "ts": s.get("ts", 0), "hits": s.get("hits", 0)}
        for s in lesson_store.list_lessons()
    ]
    tag_counts = Counter(t for s in lessons for t in s["tags"])
    return {
        "memory": {
            "items": facts,
            "count": len(facts),
            "chars": sum(len(f["text"]) for f in facts),
            "file": str(MEMORY_FILE),
        },
        "lessons": {
            "items": lessons,
            "count": len(lessons),
            "hits": sum(s["hits"] for s in lessons),
            "limit": lesson_store.MAX_STORE,
            "tags": [[t, n] for t, n in tag_counts.most_common(TOP_TAGS)],
            "file": str(LESSONS_FILE),
        },
    }


def _tags(raw: Any) -> list[str]:
    parts = raw.split(",") if isinstance(raw, str) else list(raw or [])
    out: list[str] = []
    for p in parts:
        tag = str(p).strip().lstrip("#").lower()[:MAX_TAG_CHARS]
        if tag and tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]


def _fact_text(data: dict[str, Any]) -> str:
    text = str(data.get("text") or "").strip()
    if not text:
        raise ValueError("Write something to remember.")
    if len(text) > MAX_FACT_CHARS:
        raise ValueError(f"Keep a fact under {MAX_FACT_CHARS:,} characters.")
    return text


def _lesson_fields(data: dict[str, Any]) -> tuple[str, str, list[str]]:
    task = str(data.get("task") or "").strip()
    lesson = str(data.get("lesson") or "").strip()
    if not task or not lesson:
        raise ValueError("A lesson needs both a task and what was learned.")
    if len(task) > MAX_TASK_CHARS:
        raise ValueError(f"Keep the task under {MAX_TASK_CHARS} characters.")
    if len(lesson) > MAX_LESSON_CHARS:
        raise ValueError(f"Keep a lesson under {MAX_LESSON_CHARS:,} characters.")
    return task, lesson, _tags(data.get("tags"))


def _item_id(data: dict[str, Any]) -> int:
    try:
        return int(data.get("id"))
    except (TypeError, ValueError):
        raise ValueError("Missing item id.") from None


def _add(kind: str, data: dict[str, Any]) -> int:
    if kind == "fact":
        text = _fact_text(data)
        if any(f["text"].lower() == text.lower() for f in memory_store.list_facts()):
            raise ValueError("Jarvis already remembers that.")
        return memory_store.add_fact(text)["id"]
    task, lesson, tags = _lesson_fields(data)
    return lesson_store.add_lesson(task, lesson, tags)["id"]


def _update(kind: str, data: dict[str, Any]) -> int:
    item_id = _item_id(data)
    try:
        if kind == "fact":
            found = memory_store.update_fact(item_id, _fact_text(data))
        else:
            found = lesson_store.update_lesson(item_id, *_lesson_fields(data))
    except ValueError as exc:
        if str(exc) == "duplicate fact":
            raise ValueError("Jarvis already remembers that.") from None
        raise
    if found is None:
        raise ValueError("That item is gone — the list is up to date now.")
    return item_id


def _delete(kind: str, data: dict[str, Any]) -> int:
    item_id = _item_id(data)
    done = memory_store.delete_fact(item_id) if kind == "fact" else lesson_store.delete_lesson(item_id)
    if not done:
        raise ValueError("That item was already removed.")
    return item_id


def _restore(kind: str, data: dict[str, Any]) -> int:
    raw = data.get("items")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Nothing to restore.")
    items = [i for i in raw[:MAX_RESTORE] if isinstance(i, dict) and isinstance(i.get("id"), int)]
    return memory_store.restore_facts(items) if kind == "fact" else lesson_store.restore_lessons(items)


def apply(data: dict[str, Any]) -> dict[str, Any]:
    """``{op, kind, …}`` → ``{ok, id?, error?, data}`` — ``data`` is the fresh ``public()`` either way."""
    op = str(data.get("op") or "").strip()
    kind = str(data.get("kind") or "").strip()
    if kind not in KINDS:
        return {"ok": False, "error": f"unknown kind: {kind or '(none)'}"}
    out: dict[str, Any] = {}
    try:
        if op == "add":
            out["id"] = _add(kind, data)
        elif op == "update":
            out["id"] = _update(kind, data)
        elif op == "delete":
            out["id"] = _delete(kind, data)
        elif op == "clear":
            out["removed"] = memory_store.clear_all() if kind == "fact" else lesson_store.clear_all()
        elif op == "restore":
            out["restored"] = _restore(kind, data)
        else:
            return {"ok": False, "error": f"unknown op: {op or '(none)'}"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "data": public()}
    return {"ok": True, **out, "data": public()}
