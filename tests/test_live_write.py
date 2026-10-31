"""write_file content reaches disk while it streams (jarvis/tools/live_write.py)."""
import json
import random
from types import SimpleNamespace

import pytest

from jarvis import path_resolve, state
from jarvis.tools import files, live_write
from jarvis.tools.live_write import LiveWrites


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(path_resolve, "CWD", root)
    monkeypatch.setattr(files, "project_scope_error", path_resolve.project_scope_error)
    monkeypatch.setattr(live_write, "DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(state, "plan_mode", False, raising=False)
    monkeypatch.setattr(files, "_emit_diff", lambda *a, **k: None)
    return root


def _chunks(text: str, seed: int = 7) -> list[str]:
    rnd = random.Random(seed)
    out, i = [], 0
    while i < len(text):
        n = rnd.randint(1, 9)
        out.append(text[i:i + n])
        i += n
    return out


def _stream(live: LiveWrites, args: dict, key=0, upto: str | None = None) -> None:
    """Stream the call's JSON; ``upto`` cuts it right after the last ``upto``."""
    raw = json.dumps(args)
    if upto is not None:
        raw = raw[:raw.rindex(upto) + len(upto)]
    live.feed(key, "write_file", "")
    for c in _chunks(raw):
        live.feed(key, "write_file", c)


CONTENT = 'line "one"\n\ttab \\ slash é 😀 \u2028\nend'


def test_new_file_is_written_while_streaming_and_claimed(project):
    target = project / "site" / "index.html"
    live = LiveWrites()
    live.feed(0, "write_file", "")
    raw = json.dumps({"path": str(target), "content": CONTENT})
    half = raw.index("tab")
    for c in _chunks(raw[:half]):
        live.feed(0, "write_file", c)
    assert target.read_text() == 'line "one"\n\t'  # already on disk mid-stream
    for c in _chunks(raw[half:]):
        live.feed(0, "write_file", c)
    [r] = live.close()
    assert r.complete and not r.draft and target.read_text() == CONTENT

    out = files.write_file(str(target), CONTENT)
    assert out.startswith("WROTE") and target.read_text() == CONTENT
    assert not live_write._owns(target)  # claimed: treated as a new file


def test_cut_off_write_keeps_what_arrived_and_tells_the_model(project, monkeypatch):
    from jarvis.repl import render
    from jarvis.repl.stream import check_tool_inputs

    for name in ("emit_tool_start", "emit_tool_done", "report_turn_phase"):
        monkeypatch.setattr(render, name, lambda *a, **k: None)
    target = project / "big.html"
    live = LiveWrites()
    _stream(live, {"path": str(target), "content": "a\nb\nc\nd"}, upto="c")
    [r] = live.close()
    assert not r.complete and target.read_text() == "a\nb\nc"
    assert live.incomplete() == [r]

    block = SimpleNamespace(type="tool_use", id="w1", name="write_file",
                            input={"path": str(target), "content": "a\nb\nc"})
    check_tool_inputs(SimpleNamespace(stop_reason="max_tokens", content=[block]), live)
    _, _, _, out = render._run_tool(block)
    assert out.startswith("ERROR: this tool call was cut off")
    assert "already written to" in out and "append only the rest" in out
    assert target.read_text() == "a\nb\nc"  # kept, not discarded


def test_existing_file_streams_to_a_draft_not_the_original(project):
    target = project / "app.py"
    target.write_text("original\n")
    live = LiveWrites()
    _stream(live, {"path": str(target), "content": "new body"}, upto="new b")
    [r] = live.close()
    assert r.draft and target.read_text() == "original\n"
    assert open(r.saved_to).read() == "new b"
    assert "was NOT changed" in live_write.partial_note(r)

    done = LiveWrites()
    _stream(done, {"path": str(target), "content": "new body"})
    [r2] = done.close()
    assert r2.complete and r2.saved_to == ""  # complete: draft removed


def test_plan_mode_and_outside_project_only_draft(project, tmp_path, monkeypatch):
    outside = tmp_path / "elsewhere.txt"
    live = LiveWrites()
    _stream(live, {"path": str(outside), "content": "xyz"}, upto="xy")
    assert not outside.exists() and live.close()[0].draft

    monkeypatch.setattr(state, "plan_mode", True, raising=False)
    inside = project / "plan.txt"
    live = LiveWrites()
    _stream(live, {"path": str(inside), "content": "xyz"}, upto="xy")
    assert not inside.exists() and live.close()[0].draft


def test_discard_removes_only_an_untouched_live_file(project):
    target = project / "secret.env"
    live = LiveWrites()
    _stream(live, {"path": str(target), "content": "TOKEN=x"})
    live.close()
    live_write.discard(str(target))
    assert not target.exists()

    edited = project / "kept.txt"
    live = LiveWrites()
    _stream(live, {"path": str(edited), "content": "v1"})
    live.close()
    edited.write_text("user changed this")
    live_write.discard(str(edited))
    assert edited.read_text() == "user changed this"


def test_name_repeated_on_every_delta_is_one_call(project):
    """Codex / Responses streams send the tool name with every args delta."""
    target = project / "codex.txt"
    live = LiveWrites()
    live.feed("fc1", "write_file", "")
    for c in _chunks(json.dumps({"path": str(target), "content": CONTENT})):
        live.feed("fc1", "write_file", c)
    [r] = live.close()
    assert r.complete and target.read_text() == CONTENT


def test_name_streamed_in_pieces_and_index_reuse(project):
    a, b = project / "a.txt", project / "b.txt"
    live = LiveWrites()
    live.feed(0, "write", "")          # name still arriving
    live.feed(0, "write_file", "")
    for c in _chunks(json.dumps({"path": str(a), "content": "A"})):
        live.feed(0, "write_file", c)
    live.feed(0, "write_file", "")      # next message reuses block index 0
    for c in _chunks(json.dumps({"content": "B", "path": str(b)})):  # content first
        live.feed(0, "write_file", c)
    ra, rb = live.close()
    assert a.read_text() == "A" and b.read_text() == "B" and ra.complete and rb.complete


def test_other_tools_and_non_string_fields_are_ignored(project):
    target = project / "flags.txt"
    live = LiveWrites()
    live.feed(1, "run_bash", '{"cmd": "echo hi"}')
    _stream(live, {"path": str(target), "allow_outside_project": False,
                   "extra": {"n": [1, "}"]}, "content": "ok"}, key=2)
    [r] = live.close()
    assert r.complete and target.read_text() == "ok"
