"""Serialize Anthropic message blocks to JSON-ready dicts."""


def _msg_to_json(m):
    """Make assistant content blocks JSON-serializable for /save."""
    c = m["content"]
    if isinstance(c, str): return m
    out = []
    for b in c:
        if hasattr(b, "model_dump"): out.append(b.model_dump())
        else: out.append(b)
    return {"role": m["role"], "content": out}
