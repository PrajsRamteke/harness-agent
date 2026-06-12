"""Tests for jarvis.utils.json_repair — stream-level tool-argument recovery."""

import json

from jarvis.utils.json_repair import repair_json_arguments


# ── pristine input passes through ────────────────────────────────────────


def test_valid_json_returned_as_is():
    raw = '{"path": "a.py", "content": "print(1)"}'
    assert repair_json_arguments(raw) == {"path": "a.py", "content": "print(1)"}


def test_valid_nested_json():
    raw = json.dumps({"edits": [{"old_str": "a", "new_str": "b"}], "path": "x.html"})
    assert repair_json_arguments(raw) == {
        "edits": [{"old_str": "a", "new_str": "b"}],
        "path": "x.html",
    }


# ── truncation (output-token cap mid-write) ──────────────────────────────


def test_truncated_mid_string():
    raw = '{"path": "index.html", "content": "<!DOCTYPE html><html'
    result = repair_json_arguments(raw)
    assert result == {"path": "index.html", "content": "<!DOCTYPE html><html"}


def test_truncated_mid_nested_array():
    raw = '{"path": "x.html", "edits": [{"old_str": "aaa", "new_str": "bbb"}, {"old_str": "cc'
    result = repair_json_arguments(raw)
    assert result["path"] == "x.html"
    assert result["edits"][0] == {"old_str": "aaa", "new_str": "bbb"}


def test_truncated_trailing_backslash():
    raw = '{"content": "line1\\nline2\\'
    result = repair_json_arguments(raw)
    assert result is not None
    assert result["content"].startswith("line1\nline2")


def test_truncated_partial_unicode_escape():
    raw = '{"content": "snowman \\u26'
    result = repair_json_arguments(raw)
    assert result is not None
    assert result["content"].startswith("snowman")


def test_truncated_dangling_comma():
    raw = '{"a": 1, "b": 2,'
    assert repair_json_arguments(raw) == {"a": 1, "b": 2}


def test_truncated_dangling_colon_becomes_null():
    raw = '{"a": 1, "b":'
    assert repair_json_arguments(raw) == {"a": 1, "b": None}


def test_truncated_dangling_key_dropped():
    # cut mid-key: lossy fallback drops the incomplete pair
    raw = '{"a": 1, "long_ke'
    assert repair_json_arguments(raw) == {"a": 1}


# ── unescaped inner quotes (the multi_edit HTML failure) ─────────────────


def test_unescaped_html_attribute_quotes():
    # Reproduction of the real failure: model streams HTML with raw quotes
    # inside a JSON string → "Expecting ',' delimiter at pos N".
    raw = (
        '{"path": "index.html", "old_str": "<h2 class="freebie-title glitch" '
        'data-text="FREE MODELS. NO LIMITS. NO ADS.">FREE MODELS.</h2>", '
        '"new_str": "<h2>ok</h2>"}'
    )
    result = repair_json_arguments(raw)
    assert result is not None
    assert result["path"] == "index.html"
    assert 'class="freebie-title glitch"' in result["old_str"]
    assert 'data-text="FREE MODELS. NO LIMITS. NO ADS."' in result["old_str"]
    assert result["new_str"] == "<h2>ok</h2>"


def test_unescaped_quotes_and_truncated():
    raw = '{"old_str": "<a href="x.html">link</a>", "new_str": "<a href="y.html'
    result = repair_json_arguments(raw)
    assert result is not None
    assert 'href="x.html"' in result["old_str"]


# ── raw control characters ───────────────────────────────────────────────


def test_literal_newlines_inside_string():
    raw = '{"content": "line1\nline2\n\tindented"}'
    result = repair_json_arguments(raw)
    assert result == {"content": "line1\nline2\n\tindented"}


# ── wrappers and garbage ─────────────────────────────────────────────────


def test_markdown_fenced_json():
    raw = '```json\n{"path": "a.py"}\n```'
    assert repair_json_arguments(raw) == {"path": "a.py"}


def test_leading_prose_before_object():
    raw = 'Here are the arguments: {"path": "a.py"}'
    assert repair_json_arguments(raw) == {"path": "a.py"}


def test_concatenated_objects_takes_first():
    raw = '{"path": "a.py"}{"path": "b.py"}'
    assert repair_json_arguments(raw) == {"path": "a.py"}


def test_trailing_garbage_after_object():
    raw = '{"path": "a.py"} and that is all'
    assert repair_json_arguments(raw) == {"path": "a.py"}


# ── unrecoverable input ──────────────────────────────────────────────────


def test_empty_string_returns_none():
    assert repair_json_arguments("") is None
    assert repair_json_arguments("   ") is None


def test_no_object_returns_none():
    assert repair_json_arguments("not json at all") is None


def test_bare_array_returns_none():
    assert repair_json_arguments('["a", "b"]') is None


# ── stream-repair visibility (⚒ repair note) ─────────────────────────────


def _make_stream_with_args(raw_args: str):
    from jarvis.auth.opencode_client import _OpenCodeStream

    s = _OpenCodeStream.__new__(_OpenCodeStream)
    s._collected_text = []
    s._collected_reasoning = []
    s._usage_obj = None
    s._collected_tool_calls = {
        0: {"id": "c1", "name": "multi_edit", "arguments": raw_args}
    }
    return s


def test_build_final_marks_repaired_args():
    raw = '{"path": "a.html", "old_str": "<h2 class="x">old</h2>", "new_str": "new"}'
    block = _make_stream_with_args(raw)._build_final().content[0]
    assert "__stream_error__" not in block.input
    assert block.input["__stream_repair__"].startswith("recovered malformed")
    assert block.input["path"] == "a.html"
    assert 'class="x"' in block.input["old_str"]


def test_build_final_no_marker_on_clean_args():
    block = _make_stream_with_args('{"path": "a.html"}')._build_final().content[0]
    assert "__stream_repair__" not in block.input


def test_run_tool_surfaces_stream_repair_note():
    from types import SimpleNamespace

    from jarvis.repl.render import _run_tool
    from jarvis.tools import FUNC
    from jarvis.utils.tool_repair import REPAIR_NOTE_MARKER

    FUNC["_fake_echo"] = lambda **kw: "ok"
    note = "recovered malformed streamed JSON arguments (test at pos 1)"
    b = SimpleNamespace(
        id="t-repair-1",
        name="_fake_echo",
        input={"x": 1, "__stream_repair__": note},
    )
    try:
        _b, _icon, args_preview, out = _run_tool(b)
    finally:
        del FUNC["_fake_echo"]
    # marker stripped before the tool ran and before history serialisation
    assert "__stream_repair__" not in b.input
    assert "__stream_repair__" not in args_preview
    # note surfaced via the standard repair-note path (drives the ⚒ glyph)
    assert REPAIR_NOTE_MARKER in out
    assert note in out
