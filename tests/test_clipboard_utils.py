"""Tests for jarvis.utils.clipboard — clean-copy helpers used by ⌃Y and /copy."""
import pytest

from jarvis.utils.clipboard import (
    conversation_plain_text,
    extract_last_code_block,
    normalize_copy_text,
)


# ─── normalize_copy_text ──────────────────────────────────────────────────

def test_normalize_strips_trailing_spaces_per_line():
    text = "hello   \nworld  \t \nend"
    assert normalize_copy_text(text) == "hello\nworld\nend"


def test_normalize_collapses_blank_line_runs():
    text = "a\n\n\n\n\nb"
    assert normalize_copy_text(text) == "a\n\nb"


def test_normalize_unifies_crlf():
    assert normalize_copy_text("a\r\nb\rc") == "a\nb\nc"


def test_normalize_strips_outer_newlines():
    assert normalize_copy_text("\n\ntext\n\n") == "text"


def test_normalize_empty():
    assert normalize_copy_text("") == ""
    assert normalize_copy_text(None or "") == ""


# ─── extract_last_code_block ──────────────────────────────────────────────

def test_extract_no_block_returns_none():
    assert extract_last_code_block("no code here") is None
    assert extract_last_code_block("") is None
    assert extract_last_code_block(None or "") is None


def test_extract_single_block_strips_fences():
    text = "before\n```python\nprint('hi')\n```\nafter"
    assert extract_last_code_block(text) == "print('hi')"


def test_extract_takes_last_of_multiple_blocks():
    text = "```\nfirst\n```\nmiddle\n```js\nsecond()\n```"
    assert extract_last_code_block(text) == "second()"


def test_extract_multiline_block():
    text = "```sh\nline1\nline2\n```"
    assert extract_last_code_block(text) == "line1\nline2"


def test_extract_block_without_language():
    text = "```\nplain\n```"
    assert extract_last_code_block(text) == "plain"


# ─── conversation_plain_text ──────────────────────────────────────────────

def test_conversation_basic_roles():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello!"},
    ]
    out = conversation_plain_text(messages)
    assert "## You\n\nhi" in out
    assert "## Assistant\n\nhello!" in out


def test_conversation_skips_tool_noise():
    messages = [
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hidden reasoning"},
                {"type": "tool_use", "name": "read_file", "input": {"path": "x"}},
                {"type": "text", "text": "the answer"},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "1", "content": "raw"}],
        },
    ]
    out = conversation_plain_text(messages)
    assert "hidden reasoning" not in out
    assert "read_file" not in out
    assert "raw" not in out
    assert "the answer" in out
    # tool_result-only user message contributes no section
    assert out.count("## You") == 1


def test_conversation_empty():
    assert conversation_plain_text([]) == ""
    assert conversation_plain_text(None or []) == ""


# ─── /copy command arg routing ────────────────────────────────────────────

@pytest.fixture()
def _copies(monkeypatch):
    """Capture what /copy puts on the clipboard; silence console output."""
    import jarvis.commands.context as ctx
    from jarvis import state

    copied: list[str] = []
    monkeypatch.setattr(ctx, "_copy_to_clipboard", lambda t: copied.append(t) or True)
    monkeypatch.setattr(ctx.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(state, "last_assistant_text", "reply\n```py\ncode()\n```", raising=False)
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "q"}], raising=False)
    return copied


def test_copy_default_copies_last_reply(_copies):
    from jarvis.commands.context import handle_context

    handled, new_inp = handle_context("/copy", "")
    assert handled and new_inp is None
    assert _copies and "reply" in _copies[0]


def test_copy_code_copies_last_code_block(_copies):
    from jarvis.commands.context import handle_context

    handle_context("/copy", "code")
    assert _copies == ["code()"]


def test_copy_all_copies_conversation(_copies):
    from jarvis.commands.context import handle_context

    handle_context("/copy", "all")
    assert _copies and "## You" in _copies[0]


def test_copy_nothing_to_copy(monkeypatch):
    import jarvis.commands.context as ctx
    from jarvis import state

    copied: list[str] = []
    monkeypatch.setattr(ctx, "_copy_to_clipboard", lambda t: copied.append(t) or True)
    monkeypatch.setattr(ctx.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(state, "last_assistant_text", "", raising=False)
    handled, _ = ctx.handle_context("/copy", "")
    assert handled
    assert copied == []
