"""Smart repair layer for model tool inputs.

Validates model-generated tool arguments against the declared input schema
and auto-fixes common formatting mistakes so the model doesn't look "dumb"

Repair order (only runs if there are issues):
  1. fix_null_values        — strip null from optional fields
  2. fix_field_aliases      — rename common wrong param names (e.g. topic→task)
  3. fix_extra_fields       — strip fields not in the schema
  3. fix_stringified_arrays — parse ``"[\\"a\\",\\"b\\"]"`` → ``["a", "b"]``
  4. fix_wrong_container    — unwrap single-item list when object expected
  5. fix_path_cleanup       — unwrap ``["path"]`` → ``"path"``, trim whitespace
  6. fix_string_numbers     — ``"10"`` → ``10`` when int/number expected
  7. fix_boolean_strings    — ``"true"`` → ``True``, ``"false"`` → ``False``
  8. fix_coerce_to_string   — dict/list content serialised to JSON string
  9. fix_markdown_paths     — ``[file.md](...)`` → ``file.md``
 10. apply_smart_defaults   — ``limit`` without ``offset`` → ``offset=0``

Each fixer is idempotent: correct inputs pass through with zero changes
and no log entries, so the "try as-is" optimisation is implicit.
"""

from __future__ import annotations

import json
import re
from typing import Any

# ── schema lookup ────────────────────────────────────────────────────────


def _get_schema(name: str) -> dict[str, Any] | None:
    """Look up the *input_schema* for a named tool from the registry."""
    from ..tools import TOOL_NAME_TO_GROUP, TOOL_GROUPS

    group = TOOL_NAME_TO_GROUP.get(name)
    if group is None:
        return None
    for tool in TOOL_GROUPS.get(group, []):
        if tool["name"] == name:
            return tool.get("input_schema")
    return None


def _schema_properties(schema: dict[str, Any] | None) -> dict[str, Any]:
    return (schema or {}).get("properties", {})


def _schema_required(schema: dict[str, Any] | None) -> set[str]:
    return set((schema or {}).get("required", []))


# ── individual fixers ────────────────────────────────────────────────────


def _fix_null_values(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Remove ``null`` entries for optional fields.

    The model often sends ``null`` instead of omitting an optional param.
    We strip it and log the repair.
    """
    log: list[str] = []
    for key in list(data.keys()):
        if data[key] is None and key not in required:
            del data[key]
            log.append(f"removed null value for optional field '{key}'")
    return data, log


# Per-tool wrong-name → schema-name mappings. Applied only when the target
# property exists in the schema and is not already populated.
_TOOL_FIELD_ALIASES: dict[str, dict[str, str]] = {
    "lesson_save": {
        "topic": "task",
        "subject": "task",
        "title": "task",
        "content": "lesson",
        "text": "lesson",
        "description": "lesson",
        "body": "lesson",
    },
    "lesson_search": {
        "topic": "query",
        "search": "query",
        "keyword": "query",
        "keywords": "query",
        "q": "query",
    },
}

# Generic wrong-name → candidate schema names (first match wins).
_GENERIC_FIELD_ALIASES: dict[str, list[str]] = {
    "content": ["text", "lesson", "body", "message", "cmd", "command"],
    "topic": ["task", "title", "name", "subject", "query"],
    "text": ["content", "lesson", "message"],
    "search": ["query"],
    "keyword": ["query"],
}


def _fix_field_aliases(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
    tool_name: str,
) -> tuple[dict[str, Any], list[str]]:
    """Rename common hallucinated parameter names to schema field names.

    Models often use intuitive but wrong names (``topic``/``content`` for
    ``lesson_save``).  Renaming happens *before* unknown-field stripping so
    required args are not lost.
    """
    log: list[str] = []

    for wrong, correct in _TOOL_FIELD_ALIASES.get(tool_name, {}).items():
        if wrong not in data or correct not in props or correct in data:
            continue
        data[correct] = data.pop(wrong)
        log.append(f"renamed '{wrong}' → '{correct}'")

    for wrong in list(data.keys()):
        if wrong in props:
            continue
        for correct in _GENERIC_FIELD_ALIASES.get(wrong, []):
            if correct in props and correct not in data:
                data[correct] = data.pop(wrong)
                log.append(f"renamed '{wrong}' → '{correct}'")
                break

    return data, log


def _fix_extra_fields(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Strip fields not declared in the schema.

    Models hallucinate extra kwargs (``language``, ``description``, …).
    Stripping them lets the tool run instead of throwing TypeError.

    If the schema has **no** properties and **no** required fields (e.g.
    some MCP tools with fully dynamic schemas), we skip stripping so we
    don't clobber valid untyped params.
    """
    if not props and not required:
        return data, []  # dynamic / untyped schema — keep everything

    log: list[str] = []
    for key in list(data.keys()):
        if key not in props and key not in required:
            del data[key]
            log.append(f"removed unknown field '{key}'")
    return data, log


_ARRAY_STR_RE = re.compile(r"^\s*\[.*\]\s*$", re.DOTALL)


def _fix_stringified_arrays(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Detect string values that look like JSON arrays and parse them.

    Open-source models sometimes serialise lists as strings:
    ``"paths": "[\\\"a\\\",\\\"b\\\"]"`` → ``"paths": ["a", "b"]``
    """
    log: list[str] = []
    for key, value in list(data.items()):
        prop = props.get(key)
        if prop is None:
            continue
        expected = prop.get("type")
        if expected != "array":
            continue
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    data[key] = parsed
                    log.append(f"parsed stringified array for '{key}'")
            except json.JSONDecodeError:
                pass
    return data, log


def _fix_wrong_container_types(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Fix container type mismatches.

    Cases handled:
      * Schema expects ``object``, model sends ``[{...}]`` — unwrap the
        single-item list.
      * Schema expects ``array``, model sends ``{...}`` — not fixable
        in general, so skipped.
    """
    log: list[str] = []
    for key, value in list(data.items()):
        prop = props.get(key)
        if prop is None:
            continue
        expected = prop.get("type")
        # object expected, got a single-item list → unwrap
        if expected == "object" and isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
            data[key] = value[0]
            log.append(f"unwrapped single-element list for '{key}'")
    return data, log


def _fix_string_numbers(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Coerce string values to int/float when the schema expects a number.

    Includes float → int coercion for clean values.
    """
    log: list[str] = []
    for key, value in list(data.items()):
        prop = props.get(key)
        if prop is None:
            continue
        expected = prop.get("type")
        if expected not in ("integer", "number"):
            continue
        # bool is a subclass of int in Python — don't coerce True/False
        if isinstance(value, bool):
            continue
        if isinstance(value, str):
            stripped = value.strip()
            try:
                if "." in stripped:
                    data[key] = float(stripped)
                else:
                    data[key] = int(stripped)
                log.append(f"coerced string '{key}' to {expected}")
            except (ValueError, TypeError):
                pass
        elif isinstance(value, float) and expected == "integer":
            if value == int(value) and not (value != value):  # also skip NaN
                data[key] = int(value)
                log.append(f"converted float '{key}' to int")
    return data, log


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]*)\)")
_PATH_HINTS = {"path", "file", "name", "directory", "url", "dir"}


def _fix_path_cleanup(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Clean up path fields: unwrap single-element arrays, trim whitespace.

    The model sometimes wraps a single path in a list:
    ``"path": ["main.py"]`` → ``"path": "main.py"``

    Also trims leading/trailing whitespace from path strings.
    Only applies to fields whose name or description hints at a path.
    """
    log: list[str] = []
    for key, value in list(data.items()):
        prop = props.get(key)
        if prop is None:
            continue
        expected = prop.get("type")
        if expected != "string":
            continue

        # Check if this field looks like a path
        key_lower = key.lower()
        desc = (prop.get("description") or "").lower()
        is_path_field = any(hint in key_lower for hint in _PATH_HINTS) or any(
            hint in desc for hint in _PATH_HINTS
        )
        if not is_path_field:
            continue

        # Unwrap single-element list → bare string
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
            data[key] = value[0]
            log.append(f"unwrapped single-element list for '{key}'")
            value = value[0]  # update local ref for next check

        # Trim leading/trailing whitespace
        if isinstance(value, str) and value != value.strip():
            data[key] = value.strip()
            log.append(f"trimmed whitespace for '{key}'")

    return data, log


def _fix_boolean_strings(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Coerce string ``"true"``/``"false"`` to Python bool.

    ``force="false"`` is truthy in Python — this can bypass safety guards
    silently.  We normalise it to ``force=False``.
    """
    log: list[str] = []
    for key, value in list(data.items()):
        prop = props.get(key)
        if prop is None:
            continue
        expected = prop.get("type")
        if expected != "boolean":
            continue
        if isinstance(value, bool):
            continue  # already correct
        if isinstance(value, str):
            stripped = value.strip().lower()
            if stripped == "true":
                data[key] = True
                log.append(f"coerced string '{key}' to boolean")
            elif stripped == "false":
                data[key] = False
                log.append(f"coerced string '{key}' to boolean")
        elif isinstance(value, (int, float)):
            data[key] = bool(value)
            log.append(f"coerced number '{key}' to boolean")
    return data, log


def _fix_coerce_to_string(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Convert non-string values to string when schema expects string.

    Models often send structured data (dict, list) for ``content``,
    ``old_str``, ``new_str`` fields — e.g. writing a JSON config as a
    dict instead of a string.  Dicts/lists are serialised as pretty-printed
    JSON; primitives get ``str()``.
    """
    log: list[str] = []
    for key, value in list(data.items()):
        prop = props.get(key)
        if prop is None:
            continue
        expected = prop.get("type")
        if expected != "string":
            continue
        if isinstance(value, str):
            continue  # already correct
        if value is None:
            continue  # handled by _fix_null_values

        if isinstance(value, (dict, list)):
            data[key] = json.dumps(value, indent=2, ensure_ascii=False)
            log.append(f"serialised {type(value).__name__} '{key}' to string")
        elif isinstance(value, bool):
            data[key] = "true" if value else "false"
            log.append(f"coerced bool '{key}' to string")
        elif isinstance(value, (int, float)):
            data[key] = str(value)
            log.append(f"coerced number '{key}' to string")
    return data, log


def _fix_markdown_paths(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Strip markdown link syntax from string path values.

    Models sometimes wrap file paths in markdown links because they're
    thinking in chat terms: ``"[notes.md](some/path)"`` → ``"notes.md"``.
    Applies when the field description *or* the field name hints at a
    path/filename — catches schemas where properties lack descriptions.
    """
    log: list[str] = []
    for key, value in list(data.items()):
        prop = props.get(key)
        if prop is None:
            continue
        expected = prop.get("type")
        if expected != "string":
            continue
        if not isinstance(value, str):
            continue
        desc = (prop.get("description") or "").lower()
        # Check both description and key name for path hints
        key_lower = key.lower()
        if not any(hint in desc for hint in _PATH_HINTS) and not any(
            hint in key_lower for hint in _PATH_HINTS
        ):
            continue
        m = _MD_LINK_RE.match(value.strip())
        if m:
            data[key] = m.group(1)
            log.append(f"stripped markdown link from '{key}'")
    return data, log


def _apply_smart_defaults(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Apply default values for closely related fields.

    Known patterns:
      * ``limit`` without ``offset`` → auto-set ``offset=0``
    """
    log: list[str] = []
    if "limit" in data and "offset" not in data and "offset" in props:
        data["offset"] = 0
        log.append("added default offset=0 (limit provided without offset)")
    return data, log


# ── orchestration ────────────────────────────────────────────────────────

_FIXERS = [
    ("null values", _fix_null_values),
    ("extra fields", _fix_extra_fields),
    ("stringified arrays", _fix_stringified_arrays),
    ("container types", _fix_wrong_container_types),
    ("path cleanup", _fix_path_cleanup),
    ("string numbers", _fix_string_numbers),
    ("boolean strings", _fix_boolean_strings),
    ("coerce to string", _fix_coerce_to_string),
    ("markdown paths", _fix_markdown_paths),
    ("smart defaults", _apply_smart_defaults),
]


def repair_tool_input(
    name: str,
    raw: dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Validate and repair a model's tool arguments against the declared schema.

    Args:
        name: Tool name (e.g. ``"read_file"``, ``"run_bash"``).
        raw: Raw input dict from the model.
        schema: Explicit schema.  If ``None``, looked up from the tool registry.

    Returns:
        ``(repaired_dict, repair_log)`` where *repair_log* is a list of
        human-readable strings describing what was fixed.  Empty = pristine.
    """
    if schema is None:
        schema = _get_schema(name)

    # Fast path: no schema → nothing to validate or repair
    if not schema:
        return raw, []

    data = dict(raw)  # work on a copy — never mutate the caller's dict
    props = _schema_properties(schema)
    required = _schema_required(schema)
    repairs: list[str] = []

    data, more = _fix_field_aliases(data, props, required, name)
    repairs.extend(more)

    for _label, fixer in _FIXERS:
        data, more = fixer(data, props, required)
        repairs.extend(more)

    return data, repairs
