"""Memory tools exposed to the model: save / list / delete personal facts."""
from ..storage import memory as mem


def memory_save(text: str | None = None, fact: str | None = None) -> str:
    """Save a durable user fact.

    ``text`` is the canonical tool argument. ``fact`` is accepted as a
    compatibility alias because some models try that name despite the schema.
    """
    saved = mem.add_fact(text if text is not None else fact)
    return f"saved #{saved['id']}: {saved['text']}"


def memory_list() -> str:
    facts = mem.list_facts()
    if not facts:
        return "(memory is empty)"
    lines = []
    for f in facts:
        fid = f.get("id", "?")
        lines.append(f"#{fid}: {f['text']}")
    return "\n".join(lines)


def memory_delete(id: int) -> str:
    ok = mem.delete_fact(int(id))
    return f"deleted #{id}" if ok else f"no fact with id #{id}"


MEMORY_TOOLS = [
    {
        "name": "memory_save",
        "description": (
            "Save a personal fact about the user to long-term memory "
            "(name, role, preferences, recurring context, wishes, needs, likes, dislikes). "
            "Call this proactively WITHOUT asking the user first when personal info becomes evident "
            "in conversation. The user should NEVER have to repeat themselves across sessions. "
            "Do NOT save ephemeral task details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "One short fact to save. Use this parameter name, not 'fact'. Example: 'name: Prajwal' or 'prefers concise replies'.",
                },
                "fact": {
                    "type": "string",
                    "description": "Backward-compatible alias for text. Prefer text for new calls.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "memory_list",
        "description": "List every stored personal fact about the user. Call this at the start of any conversation to refresh what you know about the user. The system prompt only shows the count — use this tool to see the actual facts.",
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
