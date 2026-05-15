"""JSON-schema sanitisation for Anthropic tool inputs.

Anthropic's Messages API rejects ``oneOf`` / ``anyOf`` / ``allOf`` at the
**top level** of a tool's ``input_schema``. Real-world MCP servers (ClickUp,
TestSprite, GitHub, …) ship variant-style schemas that trigger a 400
``input_schema does not support oneOf, allOf, or anyOf at the top level``.

This module flattens any top-level combinator into a plain object schema by
merging every variant's ``properties`` into one dict.

The functions are idempotent and safe to apply to any tool schema right
before sending it to the API.
"""

from __future__ import annotations

from typing import Any


_TOP_LEVEL_COMBINATORS = ("oneOf", "anyOf", "allOf")


def _flatten_top_level_combinator(schema: dict[str, Any]) -> dict[str, Any]:
    """Merge ``oneOf``/``anyOf``/``allOf`` variants into a single object schema.

    * ``oneOf`` / ``anyOf``: union every variant's ``properties``; ``required``
      is cleared (we can't know which variant the caller chose).
    * ``allOf``: union properties **and** ``required`` lists.
    * Other top-level fields (description, title, …) are preserved.
    * Existing top-level ``properties`` win on name collisions.
    """
    out = dict(schema)
    merged_props: dict[str, Any] = {}
    if isinstance(out.get("properties"), dict):
        merged_props.update(out["properties"])

    all_of_required: list[str] = []
    saw_one_or_any = False

    for key in _TOP_LEVEL_COMBINATORS:
        variants = out.pop(key, None)
        if not isinstance(variants, list):
            continue
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            v_props = variant.get("properties")
            if isinstance(v_props, dict):
                for k, v in v_props.items():
                    merged_props.setdefault(k, v)
            if key == "allOf":
                v_req = variant.get("required") or []
                if isinstance(v_req, list):
                    for r in v_req:
                        if isinstance(r, str) and r not in all_of_required:
                            all_of_required.append(r)
            else:
                saw_one_or_any = True

    out["type"] = "object"
    out["properties"] = merged_props
    if saw_one_or_any:
        out["required"] = []
    elif all_of_required:
        existing = out.get("required") or []
        if not isinstance(existing, list):
            existing = []
        for r in all_of_required:
            if r not in existing:
                existing.append(r)
        out["required"] = existing
    return out


def normalize_input_schema(schema: Any) -> dict[str, Any]:
    """Ensure ``schema`` is a plain ``{"type":"object","properties":{...}}`` dict.

    * Flattens any top-level ``oneOf``/``anyOf``/``allOf``.
    * Fills in missing ``type``/``properties``/``required``.
    * Returns a fresh dict — never mutates the input.
    * Idempotent.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": []}

    if any(k in schema for k in _TOP_LEVEL_COMBINATORS):
        schema = _flatten_top_level_combinator(schema)
    else:
        schema = dict(schema)

    if "type" not in schema:
        schema["type"] = "object"
    if schema.get("type") == "object" and "properties" not in schema:
        schema["properties"] = {}
    if "required" not in schema:
        schema["required"] = []
    return schema


def sanitize_tool(tool: Any) -> dict[str, Any]:
    """Return a copy of ``tool`` with its ``input_schema`` normalized.

    Tolerates dict-like inputs as well as objects exposing ``model_dump()``.
    """
    if hasattr(tool, "model_dump"):
        tool = tool.model_dump()
    if not isinstance(tool, dict):
        return tool  # nothing we can do — let the API reject it loudly
    out = dict(tool)
    if "input_schema" in out:
        out["input_schema"] = normalize_input_schema(out["input_schema"])
    return out


def sanitize_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """Apply :func:`sanitize_tool` across a list — for use at the API boundary."""
    return [sanitize_tool(t) for t in tools]
