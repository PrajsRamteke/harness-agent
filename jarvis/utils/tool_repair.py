"""Smart repair layer for model tool inputs.

Validates model-generated tool arguments against the declared input schema
and auto-fixes common formatting mistakes so the model doesn't look "dumb"

Repair order (each step no-ops unless the model sent bad data):
  0. parse stringified args     — whole input sent as a JSON string → dict
  1. fix_wrapped_arguments      — unwrap ``{"arguments": {...}}`` envelopes
  2. fix_field_aliases          — rename common wrong param names
                                  (``file_path``→``path``, ``old_string``→``old_str``)
  3. fix_null_values            — strip null from optional fields
  4. fix_extra_fields           — strip fields not in the schema
  5. fix_stringified_arrays     — parse ``"[\\"a\\",\\"b\\"]"`` → ``["a", "b"]``
  6. fix_stringified_objects    — parse ``"{...}"`` → ``{...}``
  7. fix_wrong_container        — unwrap ``[{...}]`` → ``{...}``; wrap bare
                                  scalar/object → ``[...]`` when array expected
  8. fix_path_cleanup           — unwrap ``["path"]`` → ``"path"``, trim whitespace
  9. fix_string_numbers         — ``"10"`` → ``10`` when int/number expected
 10. fix_boolean_strings        — ``"true"``/``"yes"``/``"1"`` → ``True``
 11. fix_coerce_to_string       — list of lines joined; dict serialised to JSON
 12. fix_markdown_paths         — ``[file.md](...)`` → ``file.md`` (full match only)
 13. fix_nested_array_objects   — repair each object item of array params
                                  (``multi_edit.edits``, ``ask_user_question.questions``)

Each fixer is idempotent: correct inputs pass through with zero changes
and no log entries.  When nothing was fixed, the original *raw* dict is
returned unchanged (no copy).
"""

from __future__ import annotations

import json
import re
from typing import Any

# Marker appended to tool output when input was auto-repaired. The UI layers
# (dock rows, transcript lines) detect it to show a small ⚒ indicator.
REPAIR_NOTE_MARKER = "[repair note:"


def has_repair_note(text: Any) -> bool:
    return REPAIR_NOTE_MARKER in str(text or "")


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

# Envelope keys some providers/models wrap the real arguments in:
# {"arguments": {"path": "x.py"}} instead of {"path": "x.py"}.
_WRAPPER_KEYS = {"arguments", "args", "input", "inputs", "params", "parameters", "tool_input"}


def _fix_wrapped_arguments(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Unwrap a single-key argument envelope.

    Only fires when the envelope key is NOT a real schema property, so tools
    that legitimately take an ``input`` param are unaffected.
    """
    if len(data) != 1:
        return data, []
    key = next(iter(data))
    value = data[key]
    if key in props or key not in _WRAPPER_KEYS or not isinstance(value, dict):
        return data, []
    return dict(value), [f"unwrapped '{key}' argument envelope"]


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
    "edit_file": {
        "old": "old_str",
        "new": "new_str",
        "search": "old_str",
        "replace": "new_str",
        "replacement": "new_str",
        "content": "new_str",
    },
    "write_file": {
        "data": "content",
        "body": "content",
        "file_content": "content",
        "contents": "content",
    },
    "run_bash": {
        "script": "cmd",
        "shell_command": "cmd",
        "bash": "cmd",
        "code": "cmd",
    },
    "search_code": {
        "query": "pattern",
        "regex": "pattern",
        "search": "pattern",
        "term": "pattern",
    },
    "glob_files": {
        "glob": "pattern",
        "query": "pattern",
    },
}

# Generic wrong-name → candidate schema names (first match wins).
_GENERIC_FIELD_ALIASES: dict[str, list[str]] = {
    "content": ["text", "lesson", "body", "message", "cmd", "command"],
    "topic": ["task", "title", "name", "subject", "query"],
    "text": ["content", "lesson", "message"],
    "search": ["query"],
    "keyword": ["query"],
    "q": ["query", "pattern"],
    # Claude-Code / OpenAI style param names → our schema names. These are
    # the #1 cause of "missing required argument" failures: the wrong name
    # used to be silently stripped, leaving the tool without its path/str.
    "file_path": ["path"],
    "filepath": ["path"],
    "file_name": ["path", "name"],
    "filename": ["path", "name"],
    "file": ["path"],
    "folder": ["directory", "path"],
    "dir": ["directory", "path"],
    "old_string": ["old_str"],
    "new_string": ["new_str"],
    "old_text": ["old_str"],
    "new_text": ["new_str"],
    "command": ["cmd"],
}


def _alias_value_compatible(value: Any, target_prop: dict[str, Any]) -> bool:
    """Don't rename when the value clearly can't fit the target field
    (e.g. ``replace=true`` must not become ``new_str=True``)."""
    if isinstance(value, bool) and target_prop.get("type") == "string":
        return False
    return True


def _fix_field_aliases(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
    tool_name: str,
) -> tuple[dict[str, Any], list[str]]:
    """Rename common hallucinated parameter names to schema field names.

    Models often use intuitive but wrong names (``file_path`` for ``path``,
    ``old_string`` for ``old_str``).  Renaming happens *before* unknown-field
    stripping so required args are not lost.
    """
    log: list[str] = []

    for wrong, correct in _TOOL_FIELD_ALIASES.get(tool_name, {}).items():
        if wrong not in data or wrong in props or correct not in props or correct in data:
            continue
        if not _alias_value_compatible(data[wrong], props[correct]):
            continue
        data[correct] = data.pop(wrong)
        log.append(f"renamed '{wrong}' → '{correct}'")

    for wrong in list(data.keys()):
        if wrong in props:
            continue
        for correct in _GENERIC_FIELD_ALIASES.get(wrong, []):
            if correct in props and correct not in data and _alias_value_compatible(
                data[wrong], props[correct]
            ):
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


def _fix_stringified_objects(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Detect string values that look like JSON objects and parse them.

    Mirror of :func:`_fix_stringified_arrays` for ``object``-typed fields:
    ``"config": "{\\\"k\\\": 1}"`` → ``"config": {"k": 1}``
    """
    log: list[str] = []
    for key, value in list(data.items()):
        prop = props.get(key)
        if prop is None:
            continue
        if prop.get("type") != "object" or not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                data[key] = parsed
                log.append(f"parsed stringified object for '{key}'")
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
      * Schema expects ``array``, model sends a bare object — wrap it
        (``edits: {...}`` → ``edits: [{...}]``).
      * Schema expects ``array`` of strings, model sends a bare string —
        wrap it (``paths: "a.py"`` → ``paths: ["a.py"]``).
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
            continue
        if expected == "array" and not isinstance(value, list):
            items_type = (prop.get("items") or {}).get("type")
            if isinstance(value, dict) and items_type in (None, "object"):
                data[key] = [value]
                log.append(f"wrapped bare object into array for '{key}'")
            elif isinstance(value, str) and items_type in (None, "string"):
                stripped = value.strip()
                # strings starting with '[' are malformed JSON arrays — wrapping
                # them would hide the real problem, so leave them alone.
                if stripped and not stripped.startswith("["):
                    data[key] = [value]
                    log.append(f"wrapped bare string into array for '{key}'")
    return data, log


def _fix_string_numbers(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Coerce string values to int/float when the schema expects a number.

    Includes float → int coercion for clean values (``"5.0"`` and ``5.0``
    both become ``5`` for integer fields).
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
                if "." in stripped or "e" in stripped.lower():
                    num: int | float = float(stripped)
                else:
                    num = int(stripped)
            except (ValueError, TypeError):
                continue
            if isinstance(num, float) and expected == "integer" and num.is_integer():
                num = int(num)
            data[key] = num
            log.append(f"coerced string '{key}' to {expected}")
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


_TRUE_STRINGS = {"true", "yes", "y", "on", "1"}
_FALSE_STRINGS = {"false", "no", "n", "off", "0"}


def _fix_boolean_strings(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Coerce string ``"true"``/``"false"``/``"yes"``/``"1"`` to Python bool.

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
            if stripped in _TRUE_STRINGS:
                data[key] = True
                log.append(f"coerced string '{key}' to boolean")
            elif stripped in _FALSE_STRINGS:
                data[key] = False
                log.append(f"coerced string '{key}' to boolean")
        elif isinstance(value, (int, float)):
            data[key] = bool(value)
            log.append(f"coerced number '{key}' to boolean")
    return data, log


# String fields where a list of strings almost always means "lines of text"
# — join them instead of serialising to a JSON array string.
_JOINABLE_STRING_KEYS = {
    "content", "text", "body", "message", "lesson", "code", "data",
    "old_str", "new_str", "notes",
}


def _fix_coerce_to_string(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Convert non-string values to string when schema expects string.

    Models often send structured data (dict, list) for ``content``,
    ``old_str``, ``new_str`` fields.  Repairs, most-specific first:
      * single-element list of one string → unwrapped to that string
        (``cmd: ["ls -la"]`` → ``cmd: "ls -la"``)
      * list of strings on a content-like field → joined with newlines
        (``content: ["a", "b"]`` → ``content: "a\\nb"``)
      * anything else structured → pretty-printed JSON
      * primitives → ``str()`` (bools as ``"true"``/``"false"``)
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

        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
            data[key] = value[0]
            log.append(f"unwrapped single-element list for '{key}'")
        elif (
            isinstance(value, list)
            and value
            and all(isinstance(item, str) for item in value)
            and key.lower() in _JOINABLE_STRING_KEYS
        ):
            data[key] = "\n".join(value)
            log.append(f"joined {len(value)}-element list for '{key}' with newlines")
        elif isinstance(value, (dict, list)):
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

    Only fires when the ENTIRE value is a single markdown link — a value
    that merely starts with ``[x](y)`` is real content (or a real path with
    brackets) and must not be truncated.
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
        m = _MD_LINK_RE.fullmatch(value.strip())
        if m:
            data[key] = m.group(1)
            log.append(f"stripped markdown link from '{key}'")
    return data, log


def _fix_nested_array_objects(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
) -> tuple[dict[str, Any], list[str]]:
    """Recursively repair object items inside array parameters.

    ``multi_edit`` edits and ``ask_user_question`` questions are arrays of
    objects with their own schemas — a model that sends ``old_string`` inside
    an edit item needs the same alias/coercion treatment as top-level args.
    """
    log: list[str] = []
    for key, value in list(data.items()):
        prop = props.get(key)
        if prop is None or prop.get("type") != "array" or not isinstance(value, list):
            continue
        items_schema = prop.get("items") or {}
        if items_schema.get("type") != "object":
            continue
        item_props = items_schema.get("properties", {})
        item_required = set(items_schema.get("required", []))
        if not item_props and not item_required:
            continue  # dynamic item schema — leave untouched

        new_items = list(value)
        changed = False
        for idx, item in enumerate(new_items):
            if not isinstance(item, dict):
                continue
            fixed, sub = _repair_object(dict(item), item_props, item_required, "")
            if sub:
                new_items[idx] = fixed
                changed = True
                log.extend(f"{key}[{idx}]: {entry}" for entry in sub)
        if changed:
            data[key] = new_items
    return data, log


# ── orchestration ────────────────────────────────────────────────────────

_FIXERS = [
    ("null values", _fix_null_values),
    ("extra fields", _fix_extra_fields),
    ("stringified arrays", _fix_stringified_arrays),
    ("stringified objects", _fix_stringified_objects),
    ("container types", _fix_wrong_container_types),
    ("path cleanup", _fix_path_cleanup),
    ("string numbers", _fix_string_numbers),
    ("boolean strings", _fix_boolean_strings),
    ("coerce to string", _fix_coerce_to_string),
    ("markdown paths", _fix_markdown_paths),
    ("nested array objects", _fix_nested_array_objects),
]


def _repair_object(
    data: dict[str, Any],
    props: dict[str, Any],
    required: set[str],
    tool_name: str,
) -> tuple[dict[str, Any], list[str]]:
    """Run the full fixer pipeline on one object (top-level args or a nested
    array item).  Mutates and returns *data*; callers pass a copy."""
    repairs: list[str] = []

    data, more = _fix_wrapped_arguments(data, props, required)
    repairs.extend(more)

    data, more = _fix_field_aliases(data, props, required, tool_name)
    repairs.extend(more)

    for _label, fixer in _FIXERS:
        data, more = fixer(data, props, required)
        repairs.extend(more)
    return data, repairs


def repair_tool_input(
    name: str,
    raw: dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Validate and repair a model's tool arguments against the declared schema.

    Args:
        name: Tool name (e.g. ``"read_file"``, ``"run_bash"``).
        raw: Raw input dict from the model.  A JSON-encoded string of a dict
            (a common streaming-provider quirk) is parsed and repaired too.
        schema: Explicit schema.  If ``None``, looked up from the tool registry.

    Returns:
        ``(repaired_dict, repair_log)`` where *repair_log* is a list of
        human-readable strings describing what was fixed.  Empty = pristine.
    """
    repairs: list[str] = []

    # Whole input arrived as a JSON string instead of an object.
    if isinstance(raw, str):
        stripped = raw.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            return raw, []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return raw, []
        if not isinstance(parsed, dict):
            return raw, []
        raw = parsed
        repairs.append("parsed stringified JSON arguments")

    if schema is None:
        schema = _get_schema(name)

    # No schema → nothing further to validate or repair
    if not schema or not isinstance(raw, dict):
        return raw, repairs

    props = _schema_properties(schema)
    required = _schema_required(schema)

    data, more = _repair_object(dict(raw), props, required, name)
    repairs.extend(more)

    if not repairs:
        return raw, []
    return data, repairs
