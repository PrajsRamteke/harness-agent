"""Lesson-memory tools exposed to the model.

Purpose: let the agent save lessons learned while solving a task, and recall
them quickly on similar future tasks — so repeat work costs fewer tokens and
tool calls.
"""
from ..storage import lessons as ls


def lesson_save(task: str, lesson: str, tags: list | None = None) -> str:
    s = ls.add_lesson(task, lesson, tags or [])
    tag_str = f" [{', '.join(s['tags'])}]" if s.get("tags") else ""
    return f"saved lesson #{s['id']}{tag_str}: {s['task']} → {s['lesson']}"


def lesson_search(query: str, limit: int = 5) -> str:
    hits = ls.search(query, limit=int(limit) if limit else 5)
    if not hits:
        return "(no matching lessons)"
    for h in hits:
        ls.bump_hits(h["id"])
    return "\n".join(
        f"#{h['id']} [{', '.join(h.get('tags', []))}] {h['task']} → {h['lesson']}"
        for h in hits
    )


def lesson_list() -> str:
    rows = ls.list_lessons()
    if not rows:
        return "(no lessons saved)"
    return "\n".join(
        f"#{r['id']} hits={r.get('hits',0)} [{', '.join(r.get('tags', []))}] "
        f"{r['task']} → {r['lesson']}"
        for r in rows
    )


def lesson_delete(id: int) -> str:
    ok = ls.delete_lesson(int(id))
    return f"deleted lesson #{id}" if ok else f"no lesson #{id}"


LESSON_TOOLS = [
    {
        "name": "lesson_save",
        "description": (
            "Save a durable LESSON you learned solving the current task, so future "
            "similar tasks cost less. Use when: you discovered a non-obvious "
            "solution, a gotcha, a shortcut, a working command, or a reusable "
            "pattern. Do NOT save ephemeral details (specific file names, one-off "
            "values). Keep `task` as a short pattern ('rebase branch onto main "
            "with conflicts'), `lesson` as the actionable takeaway ('git rebase -i "
            "HEAD~N then resolve, never --no-verify'). Separate from personal "
            "user memory — this is for YOUR know-how."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Short pattern describing the kind of task."},
                "lesson": {"type": "string", "description": "The actionable takeaway / solution / gotcha."},
                "tags": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Short keywords to help future retrieval, e.g. ['git','rebase']."
                },
            },
            "required": ["task", "lesson"],
        },
    },
    {
        "name": "lesson_search",
        "description": (
            "Search your saved lessons for lessons relevant to the current task "
            "BEFORE diving in. Call this at the start of any non-trivial task to "
            "see if you've solved something similar before."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords describing the current task."},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "lesson_list",
        "description": "List every saved lesson with hit counts. Rarely needed — prefer lesson_search.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "lesson_delete",
        "description": "Delete a saved lesson by id (e.g. when it's wrong or outdated).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
]
