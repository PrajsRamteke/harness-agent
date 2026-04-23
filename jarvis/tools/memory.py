"""Memory tools exposed to the model: save / list / delete personal facts."""
from ..storage import memory as mem


def memory_save(text: str) -> str:
    fact = mem.add_fact(text)
    return f"saved #{fact['id']}: {fact['text']}"


def memory_list() -> str:
    facts = mem.list_facts()
    if not facts:
        return "(memory is empty)"
    return "\n".join(f"#{f['id']}: {f['text']}" for f in facts)


def memory_delete(id: int) -> str:
    ok = mem.delete_fact(int(id))
    return f"deleted #{id}" if ok else f"no fact with id #{id}"


MEMORY_TOOLS = [
    {
        "name": "memory_save",
        "description": (
            "Save a personal fact about the user to long-term memory "
            "(name, role, preferences, recurring context). Use only for durable "
            "facts the user stated explicitly or that are clearly useful across "
            "sessions. Do NOT save ephemeral task details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "One short fact, e.g. 'name: Prajwal' or 'prefers concise replies'."}},
            "required": ["text"],
        },
    },
    {
        "name": "memory_list",
        "description": "List every stored personal fact. Use when you need to recall what you know about the user.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory_delete",
        "description": "Delete a stored fact by its numeric id.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
]
