"""Tests for the smart tool-input repair layer."""

import json
import unittest
from typing import Any

from jarvis.utils.tool_repair import (
    repair_tool_input,
    _get_schema,
    _fix_null_values,
    _fix_field_aliases,
    _fix_extra_fields,
    _fix_stringified_arrays,
    _fix_wrong_container_types,
    _fix_string_numbers,
    _fix_markdown_paths,
    _fix_path_cleanup,
    _fix_boolean_strings,
    _fix_coerce_to_string,
)
from jarvis.tools import FUNC, TOOL_GROUPS, TOOL_NAME_TO_GROUP

# ── fixtures ─────────────────────────────────────────────────────────────

_READ_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "file path to read"},
        "offset": {"type": "integer", "description": "0-indexed starting line"},
        "limit": {"type": "integer", "description": "number of lines"},
        "force": {"type": "boolean", "description": "bypass guards"},
    },
    "required": ["path"],
}

_RUN_BASH_SCHEMA = {
    "type": "object",
    "properties": {
        "cmd": {"type": "string", "description": "shell command"},
        "timeout": {"type": "integer", "description": "timeout in seconds"},
    },
    "required": ["cmd"],
}

_READ_DOC_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "One file to read"},
        "paths": {"type": "array", "items": {"type": "string"}, "description": "Multiple paths"},
        "directory": {"type": "string", "description": "folder to scan"},
        "max_files": {"type": "integer"},
    },
}

_CLICK_MENU_SCHEMA = {
    "type": "object",
    "properties": {
        "app": {"type": "string", "description": "app name"},
        "path": {
            "type": "array",
            "items": {"type": "string"},
            "description": "menu path items",
        },
    },
    "required": ["app", "path"],
}

_WRITE_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "file path to write"},
        "content": {"type": "string", "description": "file content"},
    },
    "required": ["path", "content"],
}

_LESSON_SAVE_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "description": "Short pattern describing the kind of task."},
        "lesson": {"type": "string", "description": "The actionable takeaway."},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["task", "lesson"],
}


# ── unit tests for individual fixers ─────────────────────────────────────


class TestFixNullValues(unittest.TestCase):
    def test_removes_null_on_optional(self):
        data, log = _fix_null_values(
            {"path": "x.py", "offset": None, "limit": None},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"path": "x.py"})
        self.assertEqual(len(log), 2)
        self.assertIn("offset", log[0])

    def test_keeps_null_on_required(self):
        data, log = _fix_null_values(
            {"path": None},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"path": None})  # left for Python error handling
        self.assertEqual(log, [])

    def test_no_op_when_no_nulls(self):
        data, log = _fix_null_values(
            {"path": "x.py", "offset": 10},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"path": "x.py", "offset": 10})
        self.assertEqual(log, [])

    def test_removes_none_on_optional_only(self):
        data, log = _fix_null_values(
            {"path": "x.py", "limit": None},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"path": "x.py"})
        self.assertEqual(len(log), 1)


class TestFixFieldAliases(unittest.TestCase):
    def test_lesson_save_topic_content_renamed(self):
        data, log = _fix_field_aliases(
            {
                "topic": "casual conversation tone",
                "content": "respond naturally in Hinglish",
            },
            _LESSON_SAVE_SCHEMA["properties"],
            {"task", "lesson"},
            "lesson_save",
        )
        self.assertEqual(
            data,
            {
                "task": "casual conversation tone",
                "lesson": "respond naturally in Hinglish",
            },
        )
        self.assertEqual(len(log), 2)
        self.assertIn("topic", log[0])
        self.assertIn("content", log[1])

    def test_does_not_overwrite_existing_canonical_fields(self):
        data, log = _fix_field_aliases(
            {"task": "keep me", "topic": "ignore me", "content": "also ignore"},
            _LESSON_SAVE_SCHEMA["properties"],
            {"task", "lesson"},
            "lesson_save",
        )
        # topic skipped because task already set; content → lesson
        self.assertEqual(data, {"task": "keep me", "topic": "ignore me", "lesson": "also ignore"})
        self.assertEqual(len(log), 1)
        self.assertIn("content", log[0])

        repaired, full_log = repair_tool_input(
            "lesson_save",
            {"task": "keep me", "topic": "ignore me", "content": "also ignore"},
        )
        self.assertEqual(repaired, {"task": "keep me", "lesson": "also ignore"})
        self.assertTrue(any("removed unknown field 'topic'" in entry for entry in full_log))

    def test_generic_content_to_text_when_lesson_absent(self):
        schema = {"text": {"type": "string"}}
        data, log = _fix_field_aliases(
            {"content": "hello"},
            schema,
            set(),
            "memory_save",
        )
        self.assertEqual(data, {"text": "hello"})
        self.assertIn("content", log[0])

    def test_leaves_valid_schema_fields_untouched(self):
        data, log = _fix_field_aliases(
            {"path": "main.py", "content": "print('hi')"},
            _WRITE_FILE_SCHEMA["properties"],
            {"path", "content"},
            "write_file",
        )
        self.assertEqual(data, {"path": "main.py", "content": "print('hi')"})
        self.assertEqual(log, [])


class TestFixExtraFields(unittest.TestCase):
    def test_strips_unknown_fields(self):
        data, log = _fix_extra_fields(
            {"cmd": "ls", "timeout": 10, "language": "python", "description": "list files"},
            _RUN_BASH_SCHEMA["properties"],
            {"cmd"},
        )
        self.assertEqual(data, {"cmd": "ls", "timeout": 10})
        self.assertEqual(len(log), 2)

    def test_skips_dynamic_schema(self):
        """Empty props + empty required = dynamic schema (MCP)."""
        data, log = _fix_extra_fields(
            {"whatever": 1, "anything": "goes"},
            {},
            set(),
        )
        self.assertEqual(data, {"whatever": 1, "anything": "goes"})
        self.assertEqual(log, [])

    def test_no_op_when_no_extra(self):
        data, log = _fix_extra_fields(
            {"cmd": "ls"},
            _RUN_BASH_SCHEMA["properties"],
            {"cmd"},
        )
        self.assertEqual(data, {"cmd": "ls"})
        self.assertEqual(log, [])


class TestFixStringifiedArrays(unittest.TestCase):
    def test_parses_json_array_string(self):
        data, log = _fix_stringified_arrays(
            {"app": "Safari", "path": '["File", "New Window"]'},
            _CLICK_MENU_SCHEMA["properties"],
            {"app", "path"},
        )
        self.assertEqual(data, {"app": "Safari", "path": ["File", "New Window"]})
        self.assertEqual(len(log), 1)

    def test_parses_single_element_array(self):
        data, log = _fix_stringified_arrays(
            {"paths": '["a.py"]'},
            _READ_DOC_SCHEMA["properties"],
            set(),
        )
        self.assertEqual(data, {"paths": ["a.py"]})
        self.assertEqual(len(log), 1)

    def test_ignores_non_array_string(self):
        data, log = _fix_stringified_arrays(
            {"path": "not an array"},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"path": "not an array"})
        self.assertEqual(log, [])

    def test_ignores_non_string_values(self):
        data, log = _fix_stringified_arrays(
            {"paths": ["already", "a", "list"]},
            _READ_DOC_SCHEMA["properties"],
            set(),
        )
        self.assertEqual(data, {"paths": ["already", "a", "list"]})
        self.assertEqual(log, [])

    def test_invalid_json_string_is_ignored(self):
        data, log = _fix_stringified_arrays(
            {"paths": "[broken without closing"},
            _READ_DOC_SCHEMA["properties"],
            set(),
        )
        self.assertEqual(data, {"paths": "[broken without closing"})
        self.assertEqual(log, [])


class TestFixWrongContainerTypes(unittest.TestCase):
    def test_unwraps_single_item_list_for_object(self):
        data, log = _fix_wrong_container_types(
            {"config": [{"key": "value"}]},
            {"config": {"type": "object", "properties": {}}},
            set(),
        )
        self.assertEqual(data, {"config": {"key": "value"}})
        self.assertEqual(len(log), 1)

    def test_does_not_unwrap_multi_item_list(self):
        data, log = _fix_wrong_container_types(
            {"config": [{"a": 1}, {"b": 2}]},
            {"config": {"type": "object", "properties": {}}},
            set(),
        )
        self.assertEqual(data, {"config": [{"a": 1}, {"b": 2}]})
        self.assertEqual(log, [])

    def test_no_op_for_correct_types(self):
        data, log = _fix_wrong_container_types(
            {"path": ["a.py", "b.py"]},
            {"path": {"type": "array", "items": {"type": "string"}}},
            set(),
        )
        self.assertEqual(data, {"path": ["a.py", "b.py"]})
        self.assertEqual(log, [])


class TestFixStringNumbers(unittest.TestCase):
    def test_coerces_string_to_int(self):
        data, log = _fix_string_numbers(
            {"timeout": "30"},
            _RUN_BASH_SCHEMA["properties"],
            {"cmd"},
        )
        self.assertEqual(data, {"timeout": 30})
        self.assertEqual(len(log), 1)

    def test_coerces_string_to_float(self):
        data, log = _fix_string_numbers(
            {"seconds": "2.5"},
            {"seconds": {"type": "number"}},
            set(),
        )
        self.assertEqual(data, {"seconds": 2.5})
        self.assertEqual(len(log), 1)

    def test_converts_float_to_int(self):
        data, log = _fix_string_numbers(
            {"limit": 10.0},
            {"limit": {"type": "integer"}},
            set(),
        )
        self.assertEqual(data, {"limit": 10})
        self.assertEqual(len(log), 1)

    def test_does_not_coerce_bool(self):
        """bool is a subclass of int in Python; make sure we don't coerce it."""
        data, log = _fix_string_numbers(
            {"force": True, "limit": False},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"force": True, "limit": False})
        self.assertEqual(log, [])

    def test_skips_non_numeric_strings(self):
        data, log = _fix_string_numbers(
            {"timeout": "not-a-number"},
            _RUN_BASH_SCHEMA["properties"],
            {"cmd"},
        )
        self.assertEqual(data, {"timeout": "not-a-number"})
        self.assertEqual(log, [])

    def test_skips_correct_int(self):
        data, log = _fix_string_numbers(
            {"timeout": 30},
            _RUN_BASH_SCHEMA["properties"],
            {"cmd"},
        )
        self.assertEqual(data, {"timeout": 30})
        self.assertEqual(log, [])


class TestFixMarkdownPaths(unittest.TestCase):
    def test_strips_full_markdown_link(self):
        data, log = _fix_markdown_paths(
            {"path": "[notes.md](some/url)"},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"path": "notes.md"})
        self.assertEqual(len(log), 1)

    def test_no_op_on_plain_string(self):
        data, log = _fix_markdown_paths(
            {"path": "notes.md"},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"path": "notes.md"})
        self.assertEqual(log, [])

    def test_skips_non_path_field_with_markdown(self):
        schema_props = {"text": {"type": "string", "description": "message text"}}
        data, log = _fix_markdown_paths(
            {"text": "[hello](world)"},
            schema_props,
            set(),
        )
        self.assertEqual(data, {"text": "[hello](world)"})  # no path hint in description
        self.assertEqual(log, [])


class TestFixPathCleanup(unittest.TestCase):
    def test_unwraps_single_element_list_for_path(self):
        from jarvis.utils.tool_repair import _fix_path_cleanup

        data, log = _fix_path_cleanup(
            {"path": ["main.py"]},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"path": "main.py"})
        self.assertEqual(len(log), 1)
        self.assertIn("unwrapped", log[0])

    def test_trims_whitespace_from_path(self):
        from jarvis.utils.tool_repair import _fix_path_cleanup

        data, log = _fix_path_cleanup(
            {"path": "  main.py  "},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"path": "main.py"})
        self.assertEqual(len(log), 1)
        self.assertIn("whitespace", log[0])

    def test_does_not_unwrap_multi_element_list(self):
        from jarvis.utils.tool_repair import _fix_path_cleanup

        data, log = _fix_path_cleanup(
            {"path": ["a.py", "b.py"]},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"path": ["a.py", "b.py"]})
        self.assertEqual(log, [])

    def test_skips_non_path_field(self):
        from jarvis.utils.tool_repair import _fix_path_cleanup

        data, log = _fix_path_cleanup(
            {"content": ["line1", "line2"]},
            _WRITE_FILE_SCHEMA["properties"],
            {"path", "content"},
        )
        self.assertEqual(data, {"content": ["line1", "line2"]})  # not a path field
        self.assertEqual(log, [])

    def test_no_op_on_correct_path(self):
        from jarvis.utils.tool_repair import _fix_path_cleanup

        data, log = _fix_path_cleanup(
            {"path": "main.py"},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"path": "main.py"})
        self.assertEqual(log, [])


class TestFixBooleanStrings(unittest.TestCase):
    def test_coerces_string_true(self):
        from jarvis.utils.tool_repair import _fix_boolean_strings

        props = {"force": {"type": "boolean"}}
        data, log = _fix_boolean_strings({"force": "true"}, props, set())
        self.assertIs(data["force"], True)
        self.assertEqual(len(log), 1)

    def test_coerces_string_false(self):
        from jarvis.utils.tool_repair import _fix_boolean_strings

        props = {"force": {"type": "boolean"}}
        data, log = _fix_boolean_strings({"force": "false"}, props, set())
        self.assertIs(data["force"], False)
        self.assertEqual(len(log), 1)

    def test_coerces_mixed_case(self):
        from jarvis.utils.tool_repair import _fix_boolean_strings

        props = {"force": {"type": "boolean"}}
        data, log = _fix_boolean_strings({"force": "True"}, props, set())
        self.assertIs(data["force"], True)
        self.assertEqual(len(log), 1)

    def test_skips_correct_bool(self):
        from jarvis.utils.tool_repair import _fix_boolean_strings

        props = {"force": {"type": "boolean"}}
        data, log = _fix_boolean_strings({"force": True}, props, set())
        self.assertIs(data["force"], True)
        self.assertEqual(log, [])

    def test_skips_non_boolean_field(self):
        from jarvis.utils.tool_repair import _fix_boolean_strings

        props = {"cmd": {"type": "string"}}
        data, log = _fix_boolean_strings({"cmd": "true"}, props, {"cmd"})
        self.assertEqual(data["cmd"], "true")  # not a boolean field
        self.assertEqual(log, [])

    def test_coerces_int_to_bool(self):
        from jarvis.utils.tool_repair import _fix_boolean_strings

        props = {"force": {"type": "boolean"}}
        data, log = _fix_boolean_strings({"force": 1}, props, set())
        self.assertIs(data["force"], True)
        self.assertEqual(len(log), 1)


class TestFixCoerceToString(unittest.TestCase):
    def test_serialises_dict_to_json(self):
        from jarvis.utils.tool_repair import _fix_coerce_to_string

        data, log = _fix_coerce_to_string(
            {"content": {"key": "value", "nested": [1, 2]}},
            _WRITE_FILE_SCHEMA["properties"],
            {"path", "content"},
        )
        self.assertIsInstance(data["content"], str)
        self.assertIn('"key"', data["content"])
        self.assertIn('"value"', data["content"])
        self.assertEqual(len(log), 1)

    def test_serialises_list_to_json(self):
        from jarvis.utils.tool_repair import _fix_coerce_to_string

        data, log = _fix_coerce_to_string(
            {"content": ["line1", "line2", "line3"]},
            _WRITE_FILE_SCHEMA["properties"],
            {"path", "content"},
        )
        self.assertIsInstance(data["content"], str)
        self.assertIn("line1", data["content"])
        self.assertEqual(len(log), 1)

    def test_coerces_number_to_string(self):
        from jarvis.utils.tool_repair import _fix_coerce_to_string

        data, log = _fix_coerce_to_string(
            {"content": 12345},
            _WRITE_FILE_SCHEMA["properties"],
            {"path", "content"},
        )
        self.assertEqual(data["content"], "12345")
        self.assertEqual(len(log), 1)

    def test_coerces_bool_to_string(self):
        from jarvis.utils.tool_repair import _fix_coerce_to_string

        data, log = _fix_coerce_to_string(
            {"content": True},
            _WRITE_FILE_SCHEMA["properties"],
            {"path", "content"},
        )
        self.assertEqual(data["content"], "true")
        self.assertEqual(len(log), 1)

    def test_skips_correct_string(self):
        from jarvis.utils.tool_repair import _fix_coerce_to_string

        data, log = _fix_coerce_to_string(
            {"content": "hello world"},
            _WRITE_FILE_SCHEMA["properties"],
            {"path", "content"},
        )
        self.assertEqual(data["content"], "hello world")
        self.assertEqual(log, [])

    def test_skips_null(self):
        from jarvis.utils.tool_repair import _fix_coerce_to_string

        data, log = _fix_coerce_to_string(
            {"content": None},
            _WRITE_FILE_SCHEMA["properties"],
            {"path", "content"},
        )
        self.assertIsNone(data["content"])
        self.assertEqual(log, [])

    def test_skips_non_string_field(self):
        from jarvis.utils.tool_repair import _fix_coerce_to_string

        data, log = _fix_coerce_to_string(
            {"offset": {"bad": "dict"}},
            _READ_FILE_SCHEMA["properties"],
            {"path"},
        )
        self.assertEqual(data, {"offset": {"bad": "dict"}})  # not a string field
        self.assertEqual(log, [])


# ── integration tests for repair_tool_input ──────────────────────────────


class TestRepairToolInput(unittest.TestCase):
    def test_correct_input_passes_through(self):
        raw = {"path": "main.py"}
        data, log = repair_tool_input("read_file", raw)
        self.assertEqual(data, {"path": "main.py"})
        self.assertIs(data, raw)
        self.assertEqual(log, [])

    def test_strips_null_optional(self):
        data, log = repair_tool_input(
            "read_file",
            {"path": "main.py", "offset": None},
        )
        self.assertEqual(data, {"path": "main.py"})
        self.assertIn("null", log[0])

    def test_strips_extra_fields(self):
        data, log = repair_tool_input(
            "run_bash",
            {"cmd": "ls", "language": "bash", "description": "list"},
        )
        self.assertEqual(data, {"cmd": "ls"})
        self.assertEqual(len(log), 2)

    def test_lesson_save_topic_content_alias(self):
        """Regression: topic/content must not be stripped leaving empty input."""
        data, log = repair_tool_input(
            "lesson_save",
            {
                "topic": "tone-personality",
                "content": "When user talks casually in Hindi, stay natural.",
            },
        )
        self.assertEqual(
            data,
            {
                "task": "tone-personality",
                "lesson": "When user talks casually in Hindi, stay natural.",
            },
        )
        self.assertTrue(any("renamed" in entry for entry in log))
        self.assertFalse(any("removed unknown field" in entry for entry in log))

    def test_stringified_array(self):
        data, log = repair_tool_input(
            "click_menu",
            {"app": "Finder", "path": '["Go", "Utilities"]'},
        )
        self.assertEqual(data, {"app": "Finder", "path": ["Go", "Utilities"]})
        self.assertIn("stringified", log[0])

    def test_string_number_coercion(self):
        data, log = repair_tool_input(
            "run_bash",
            {"cmd": "sleep 5", "timeout": "30"},
        )
        self.assertEqual(data, {"cmd": "sleep 5", "timeout": 30})
        self.assertIn("string", log[0])

    def test_markdown_path_cleaned(self):
        data, log = repair_tool_input(
            "read_file",
            {"path": "[main.py](some/url)"},
        )
        self.assertEqual(data, {"path": "main.py"})
        self.assertIn("markdown", log[0])

    def test_optional_offset_omitted_is_not_repaired(self):
        """Omitting optional offset with limit is valid — tool defaults offset=0."""
        raw = {"path": "main.py", "limit": 100}
        data, log = repair_tool_input("read_file", raw)
        self.assertEqual(log, [])
        self.assertIs(data, raw)

    def test_multiple_repairs_at_once(self):
        """Model sends multiple issues in one call."""
        data, log = repair_tool_input(
            "read_file",
            {
                "path": "[main.py](some/url)",
                "offset": None,
                "limit": "50",
                "extra_field": True,
            },
        )
        # All repairs applied (offset is optional — not injected)
        self.assertEqual(data, {"path": "main.py", "limit": 50})
        self.assertGreaterEqual(len(log), 3)  # markdown + null + string + extra

    def test_unknown_tool_returns_unchanged(self):
        data, log = repair_tool_input("nonexistent_tool", {"a": 1})
        self.assertEqual(data, {"a": 1})
        self.assertEqual(log, [])

    def test_empty_input(self):
        data, log = repair_tool_input("read_file", {})
        self.assertEqual(data, {})
        self.assertEqual(log, [])

    def test_explicit_schema_overrides_lookup(self):
        """When schema is passed explicitly, use it instead of registry lookup."""
        data, log = repair_tool_input(
            "fake_tool",
            {"name": None},
            schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        )
        # 'name' is optional (not in required) so null is stripped
        self.assertEqual(data, {})
        self.assertEqual(len(log), 1)

    def test_null_on_required_stays(self):
        """Required field with null should NOT be stripped - let Python handle it."""
        data, log = repair_tool_input("write_file", {"path": None, "content": "hi"})
        # 'path' is required in write_file schema
        self.assertIn("path", data)
        self.assertIsNone(data["path"])
        # No log entry for the null — we don't strip required fields

    # ── new fixer integration tests ─────────────────────────────────────

    def test_path_as_single_element_list(self):
        """path=["main.py"] → path="main.py" """
        data, log = repair_tool_input("read_file", {"path": ["main.py"]})
        self.assertEqual(data, {"path": "main.py"})
        self.assertIn("unwrapped", " ".join(log))

    def test_path_with_whitespace(self):
        """path with trailing whitespace gets trimmed."""
        data, log = repair_tool_input("read_file", {"path": "  main.py  "})
        self.assertEqual(data, {"path": "main.py"})
        self.assertIn("whitespace", " ".join(log))

    def test_boolean_string_force_true(self):
        """force='true' → force=True"""
        data, log = repair_tool_input("read_file", {"path": "main.py", "force": "true"})
        self.assertIs(data["force"], True)

    def test_boolean_string_force_false(self):
        """force='false' → force=False — critical for safety!"""
        data, log = repair_tool_input("read_file", {"path": "main.py", "force": "false"})
        self.assertIs(data["force"], False)

    def test_content_as_dict(self):
        """content as dict gets serialised to JSON string."""
        data, log = repair_tool_input(
            "write_file",
            {"path": "config.json", "content": {"key": "value", "nested": [1]}},
        )
        self.assertIsInstance(data["content"], str)
        self.assertIn('"key"', data["content"])
        self.assertIn("serialised", " ".join(log))

    def test_content_as_list(self):
        """content as list gets serialised to JSON string."""
        data, log = repair_tool_input(
            "write_file",
            {"path": "out.txt", "content": ["line1", "line2"]},
        )
        self.assertIsInstance(data["content"], str)
        self.assertIn("line1", data["content"])

    def test_write_file_full_stack_single_element_path(self):
        """write_file with path as single-element list + content as dict."""
        data, log = repair_tool_input(
            "write_file",
            {"path": ["config.json"], "content": {"name": "test"}},
        )
        self.assertEqual(data["path"], "config.json")  # unwrapped
        self.assertIsInstance(data["content"], str)  # serialised
        self.assertIn("name", data["content"])
        self.assertGreaterEqual(len(log), 2)

    def test_edit_file_old_str_as_dict(self):
        """edit_file old_str as structured data gets serialised."""
        data, log = repair_tool_input(
            "edit_file",
            {"path": "main.py", "old_str": {"old": "text"}, "new_str": "new text"},
        )
        self.assertIsInstance(data["old_str"], str)
        self.assertIn('"old"', data["old_str"])


class TestRepairToolInputEdgeCases(unittest.TestCase):
    def test_non_dict_input_passthrough(self):
        """If raw input is not a dict (shouldn't happen, but be safe)."""
        # repair_tool_input creates dict(raw) which works for many types.
        # But string would give strange results - test with None edge case.
        data, log = repair_tool_input("read_file", {})  # empty dict
        self.assertEqual(data, {})

    def test_schema_with_no_properties(self):
        """Some tools like check_permissions have empty properties."""
        data, log = repair_tool_input("check_permissions", {})
        self.assertEqual(data, {})
        self.assertEqual(log, [])

    def test_schema_with_no_properties_but_extra_fields(self):
        """Empty schema with extra fields should NOT strip (dynamic schema)."""
        # check_permissions schema: {"type": "object", "properties": {}}
        # Dynamic schema rule: no props + no required = don't strip
        data, log = repair_tool_input("check_permissions", {"unknown": "value"})
        self.assertEqual(data, {"unknown": "value"})
        self.assertEqual(log, [])

    def test_float_where_int_expected(self):
        data, log = repair_tool_input(
            "run_bash",
            {"cmd": "ls", "timeout": 5.0},
        )
        self.assertEqual(data, {"cmd": "ls", "timeout": 5})
        self.assertIn("float", log[0])


class TestGetSchema(unittest.TestCase):
    """Test that schema lookup works against the real tool registry."""

    def test_finds_read_file(self):
        schema = _get_schema("read_file")
        self.assertIsNotNone(schema)
        self.assertEqual(schema["type"], "object")
        props = schema.get("properties", {})
        self.assertIn("path", props)

    def test_finds_run_bash(self):
        schema = _get_schema("run_bash")
        self.assertIsNotNone(schema)
        props = schema.get("properties", {})
        self.assertIn("cmd", props)

    def test_returns_none_for_unknown(self):
        schema = _get_schema("completely_fake_tool_12345")
        self.assertIsNone(schema)

    def test_finds_mac_tools(self):
        schema = _get_schema("click_element")
        self.assertIsNotNone(schema)
        props = schema.get("properties", {})
        self.assertIn("app", props)
        self.assertIn("query", props)

    def test_finds_memory_tools(self):
        schema = _get_schema("memory_save")
        self.assertIsNotNone(schema)


# ── integration test with real render.py module (mock tool execution) ──


def _minimal_valid_input(schema: dict[str, Any]) -> dict[str, Any]:
    """Build a schema-valid minimal dict from required fields only."""
    props = schema.get("properties", {})
    required = schema.get("required", [])
    data: dict[str, Any] = {}
    for key in required:
        prop = props.get(key, {})
        ptype = prop.get("type", "string")
        if ptype == "string":
            data[key] = "test"
        elif ptype == "integer":
            data[key] = 1
        elif ptype == "number":
            data[key] = 1.0
        elif ptype == "boolean":
            data[key] = True
        elif ptype == "array":
            items = prop.get("items", {})
            if items.get("type") == "string":
                data[key] = ["a"]
            elif items.get("type") == "object":
                # build a schema-valid item recursively so the nested
                # array-object fixer has nothing to repair
                data[key] = [_minimal_valid_input(items)]
            else:
                data[key] = []
        elif ptype == "object":
            data[key] = {}
        else:
            data[key] = "test"
    return data


def _tool_schema(name: str) -> dict[str, Any] | None:
    group = TOOL_NAME_TO_GROUP.get(name)
    if not group:
        return None
    for tool in TOOL_GROUPS.get(group, []):
        if tool["name"] == name:
            return tool.get("input_schema")
    return None


class TestAllToolsRepairCoverage(unittest.TestCase):
    """Every registered tool must have a schema and pass correct input unchanged."""

    def test_every_func_tool_has_schema(self):
        missing = [name for name in FUNC if _tool_schema(name) is None]
        self.assertEqual(missing, [], f"tools missing input_schema: {missing}")

    def test_valid_minimal_input_unmodified_for_all_tools(self):
        for name in sorted(FUNC.keys()):
            schema = _tool_schema(name)
            self.assertIsNotNone(schema, name)
            raw = _minimal_valid_input(schema)  # type: ignore[arg-type]
            data, log = repair_tool_input(name, raw, schema=schema)
            with self.subTest(tool=name):
                self.assertEqual(log, [], msg=f"{name} repaired valid input")
                self.assertIs(data, raw, msg=f"{name} should return same dict")

    def test_incorrect_input_repaired_for_representative_tools(self):
        cases = [
            ("read_file", {"path": ["main.py"]}, {"path": "main.py"}),
            ("run_bash", {"cmd": "ls", "timeout": "30"}, {"cmd": "ls", "timeout": 30}),
            ("write_file", {"path": "a.txt", "content": {"k": "v"}}, None),
            ("lesson_save", {"topic": "x", "content": "y"}, {"task": "x", "lesson": "y"}),
            ("click_menu", {"app": "Safari", "path": '["File"]'}, {"app": "Safari", "path": ["File"]}),
        ]
        for name, broken, expected in cases:
            data, log = repair_tool_input(name, broken)
            with self.subTest(tool=name):
                self.assertTrue(log, msg=f"{name} should produce repair log")
                if expected is not None:
                    self.assertEqual(data, expected)
                if name == "write_file":
                    self.assertIsInstance(data["content"], str)


class TestAliasRepairs(unittest.TestCase):
    """Claude-Code / OpenAI style param names must be renamed, not stripped."""

    def test_read_file_file_path_alias(self):
        data, log = repair_tool_input("read_file", {"file_path": "main.py"})
        self.assertEqual(data, {"path": "main.py"})
        self.assertTrue(any("renamed" in entry for entry in log))

    def test_edit_file_claude_style_params(self):
        data, log = repair_tool_input(
            "edit_file",
            {"file_path": "main.py", "old_string": "a", "new_string": "b"},
        )
        self.assertEqual(data, {"path": "main.py", "old_str": "a", "new_str": "b"})
        self.assertEqual(len(log), 3)

    def test_run_bash_command_alias(self):
        data, log = repair_tool_input("run_bash", {"command": "ls"})
        self.assertEqual(data, {"cmd": "ls"})

    def test_search_code_query_alias(self):
        data, log = repair_tool_input("search_code", {"query": "def main"})
        self.assertEqual(data, {"pattern": "def main"})

    def test_bool_not_renamed_into_string_field(self):
        """edit_file replace=true must not become new_str=True."""
        data, log = repair_tool_input(
            "edit_file",
            {"path": "a.py", "old_str": "x", "new_str": "y", "replace": True},
        )
        self.assertEqual(data["new_str"], "y")
        self.assertNotIn("replace", data)  # stripped, not renamed


class TestWrappedArguments(unittest.TestCase):
    def test_unwraps_arguments_envelope(self):
        data, log = repair_tool_input(
            "read_file", {"arguments": {"path": "main.py"}}
        )
        self.assertEqual(data, {"path": "main.py"})
        self.assertTrue(any("envelope" in entry for entry in log))

    def test_does_not_unwrap_real_schema_property(self):
        schema = {
            "type": "object",
            "properties": {"input": {"type": "object"}},
            "required": ["input"],
        }
        raw = {"input": {"a": 1}}
        data, log = repair_tool_input("fake_tool", raw, schema=schema)
        self.assertEqual(data, {"input": {"a": 1}})
        self.assertEqual(log, [])


class TestContainerWrapping(unittest.TestCase):
    def test_bare_string_wrapped_for_string_array(self):
        data, log = repair_tool_input("read_bundle", {"paths": "a.py"})
        self.assertEqual(data["paths"], ["a.py"])

    def test_bare_object_wrapped_for_object_array(self):
        data, log = repair_tool_input(
            "multi_edit",
            {"edits": {"path": "a.py", "old_str": "x", "new_str": "y"}},
        )
        self.assertEqual(
            data["edits"], [{"path": "a.py", "old_str": "x", "new_str": "y"}]
        )

    def test_malformed_json_array_string_not_wrapped(self):
        data, log = repair_tool_input("read_bundle", {"paths": "[broken"})
        self.assertEqual(data["paths"], "[broken")


class TestNestedArrayObjectRepair(unittest.TestCase):
    def test_multi_edit_items_with_claude_style_names(self):
        data, log = repair_tool_input(
            "multi_edit",
            {
                "edits": [
                    {"file_path": "a.py", "old_string": "x", "new_string": "y"},
                    {"path": "b.py", "old_str": "1", "new_str": "2"},
                ]
            },
        )
        self.assertEqual(
            data["edits"][0], {"path": "a.py", "old_str": "x", "new_str": "y"}
        )
        self.assertEqual(
            data["edits"][1], {"path": "b.py", "old_str": "1", "new_str": "2"}
        )
        self.assertTrue(any("edits[0]" in entry for entry in log))

    def test_does_not_mutate_caller_input(self):
        raw = {"edits": [{"file_path": "a.py", "old_string": "x", "new_string": "y"}]}
        repair_tool_input("multi_edit", raw)
        self.assertEqual(
            raw["edits"][0],
            {"file_path": "a.py", "old_string": "x", "new_string": "y"},
        )

    def test_valid_items_untouched(self):
        raw = {"edits": [{"path": "a.py", "old_str": "x", "new_str": "y"}]}
        data, log = repair_tool_input("multi_edit", raw)
        self.assertIs(data, raw)
        self.assertEqual(log, [])


class TestNewCoercions(unittest.TestCase):
    def test_float_string_for_integer_field(self):
        data, log = repair_tool_input("run_bash", {"cmd": "ls", "timeout": "30.0"})
        self.assertEqual(data["timeout"], 30)
        self.assertIsInstance(data["timeout"], int)

    def test_boolean_yes_and_one(self):
        data, _ = repair_tool_input("read_file", {"path": "a.py", "force": "yes"})
        self.assertIs(data["force"], True)
        data, _ = repair_tool_input("read_file", {"path": "a.py", "force": "0"})
        self.assertIs(data["force"], False)

    def test_cmd_single_element_list_unwrapped(self):
        data, log = repair_tool_input("run_bash", {"cmd": ["ls -la"]})
        self.assertEqual(data["cmd"], "ls -la")

    def test_content_lines_joined_not_json(self):
        data, log = repair_tool_input(
            "write_file", {"path": "a.txt", "content": ["line1", "line2"]}
        )
        self.assertEqual(data["content"], "line1\nline2")

    def test_stringified_whole_arguments(self):
        data, log = repair_tool_input("read_file", '{"path": "main.py"}')
        self.assertEqual(data, {"path": "main.py"})
        self.assertTrue(any("stringified JSON arguments" in entry for entry in log))

    def test_unparseable_string_arguments_returned_as_is(self):
        data, log = repair_tool_input("read_file", "not json at all")
        self.assertEqual(data, "not json at all")
        self.assertEqual(log, [])

    def test_markdown_link_with_trailing_text_untouched(self):
        """fullmatch only — values that merely START with a link are content."""
        raw = {"path": "[main.py](url) and more"}
        data, log = repair_tool_input("read_file", raw)
        self.assertEqual(data["path"], "[main.py](url) and more")


class TestRepairInRenderFlow(unittest.TestCase):
    """Verify that the repair layer integrates correctly into _run_tool flow.

    We test the actual repair_tool_input call as it would be invoked from
    render.py: tool name + model input dict → repaired dict + log.
    """

    def test_empty_log_for_clean_input(self):
        """A well-formed tool call produces no repair log."""
        raw = {"path": "main.py", "offset": 0}
        data, log = repair_tool_input("read_file", raw)
        self.assertEqual(log, [])
        self.assertIs(data, raw)

    def test_repair_log_has_entries_for_broken_input(self):
        """A broken tool call produces repair log entries."""
        _data, log = repair_tool_input(
            "click_menu",
            {"app": "Safari", "path": '["File", "New Tab"]'},
        )
        self.assertGreater(len(log), 0)

    def test_write_file_required_null_not_stripped(self):
        """Required field with null stays — render.py's TypeError handler catches it."""
        data, log = repair_tool_input(
            "write_file",
            {"path": None, "content": "hello"},
        )
        self.assertIsNone(data.get("path"))  # not stripped
        self.assertEqual(data.get("content"), "hello")
        # No repair log entry for required null


if __name__ == "__main__":
    unittest.main()
