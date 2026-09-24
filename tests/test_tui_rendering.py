"""Pure rendering helpers behind the TUI transcript (no app needed)."""
import pathlib

from jarvis.tui import tool_format as tf
from jarvis.tui.md_render import render_markdown, split_stable
from jarvis.tui.prompt_history import PromptHistory


# ── tool rows ────────────────────────────────────────────────────────────


def test_tool_titles_and_args():
    assert tf.tool_title("read_file") == "Read"
    assert tf.tool_title("run_bash") == "Bash"
    assert tf.tool_title("mcp__context7__query-docs") == "Query Docs"
    assert tf.tool_args("run_bash", {"cmd": "pytest -q"}) == "pytest -q"
    assert tf.tool_args("read_file", {"path": "a.py", "offset": 10, "limit": 20}) == "a.py L11–30"
    assert tf.tool_args("search_code", {"pattern": "stall", "path": "jarvis"}) == '"stall" in jarvis'
    assert tf.tool_args("mcp__context7__query-docs", {"libraryId": "x"}).startswith("context7")
    edits = {"edits": [{"path": f"f{i}.py"} for i in range(5)]}
    assert tf.tool_args("multi_edit", edits) == "f0.py, f1.py, f2.py +2 more"


def test_short_path_relativizes_cwd_and_home():
    cwd = pathlib.Path.cwd()
    assert tf.short_path(str(cwd / "pkg" / "mod.py")) == "pkg/mod.py"
    assert tf.short_path("relative/x.py") == "relative/x.py"


def test_tool_summaries():
    assert tf.tool_summary("read_file", {}, "a\nb\nc") == (["Read 3 lines"], False)
    assert tf.tool_summary("edit_file", {}, "EDITED x (2 replacements)") == (["Applied 2 edits"], False)
    lines, err = tf.tool_summary("run_bash", {}, "$ make\nexit=2\nboom\nfailed")
    assert err and lines[-1] == "exit 2" and "boom" in lines
    assert tf.tool_summary("run_bash", {}, "$ true\nexit=0\n") == (["done"], False)
    assert tf.tool_summary("fetch_url", {}, "ERROR: HTTP 404") == (["HTTP 404"], True)
    assert tf.tool_summary("run_bash", {}, "USER DENIED") == (["denied by user"], True)
    assert tf.tool_summary("search_code", {}, "a:1: x\nb:2: y") == (["2 matches"], False)
    assert tf.tool_summary("list_dir", {}, "d a\nf b\nf c") == (["3 entries"], False)


def test_tool_summary_hides_repair_note():
    from jarvis.utils.tool_repair import REPAIR_NOTE_MARKER

    out = f"a\nb\n{REPAIR_NOTE_MARKER} renamed 'x' → 'y']"
    assert tf.tool_summary("read_file", {}, out) == (["Read 2 lines"], False)


# ── markdown ─────────────────────────────────────────────────────────────


def test_render_markdown_is_left_aligned_text():
    text = render_markdown("# Title\n\nbody `code`\n\n```py\nx = 1\n```", 60)
    lines = text.plain.split("\n")
    assert lines[0].startswith("Title"), "H1 must not be centered/boxed"
    assert any("x = 1" in ln for ln in lines)
    assert max(len(ln) for ln in lines) <= 60


def test_split_stable_never_cuts_inside_fence_or_list():
    para = "word " * 60
    src = f"{para}\n\n```py\n" + "x = 1\n\n" * 200 + "```\n\nafter"
    cut = split_stable(src)
    assert cut > 0
    assert src[:cut].count("```") % 2 == 0, "cut landed inside a code fence"
    lst = f"{para}\n\n" * 6 + "- a\n\n- b\n\n- c"
    cut = split_stable(lst)
    assert not lst[cut:].startswith("- b") and not lst[cut:].startswith("- c")
    assert split_stable("short text") == 0


# ── prompt history ───────────────────────────────────────────────────────


def test_prompt_history_persists_and_dedupes(tmp_path):
    path = tmp_path / "hist.json"
    h = PromptHistory(path)
    h.add("one")
    h.add("one")
    h.add("two")
    assert PromptHistory(path).entries == ["one", "two"]
    assert h.prev("draft") == "two"
    assert h.prev("x") == "one"
    assert h.prev("x") is None
    assert h.next() == "two"
    assert h.next() == "draft"
    assert h.next() is None


# ── themes ───────────────────────────────────────────────────────────────


def test_every_palette_is_selectable_and_builds_a_textual_theme():
    from jarvis import state
    from jarvis.storage.settings import _coerce
    from jarvis.tui import theme as ui

    names = ui.theme_names()
    assert set(names) == set(ui.PALETTES)
    assert names[0] == ui.DEFAULT_THEME
    for name in names:
        assert _coerce("theme", name) == name, f"settings rejects theme {name}"
        assert name in state.THEMES, f"state.THEMES missing {name}"
        th = ui.textual_theme(name)
        assert th.name == ui.textual_theme_name(name)
        assert "jv-accent" in th.variables and "jv-user-bg" in th.variables


def test_paste_chips_round_trip():
    from jarvis.tui import paste_chips as pc

    big = "\n".join(f"line {i}" for i in range(30))
    assert pc.should_collapse(big) and not pc.should_collapse("short")
    chip = pc.make_chip(big)
    assert chip.startswith("[Pasted text #") and "+30 lines" in chip
    assert pc.expand_chips(f"see {chip} please") == f"see {big} please"
    pc.reset()
    assert pc.expand_chips(chip) == chip  # unknown after reset: left as-is


def test_shell_commands_display_without_cwd_noise():
    import os

    from jarvis.utils.display_paths import shorten_command, shorten_paths

    cwd = os.getcwd()
    home = os.path.expanduser("~")
    assert shorten_command(f"cd {cwd} && pytest -q") == "pytest -q"
    assert shorten_command(f'cd "{cwd}" ; ls {cwd}/jarvis') == "ls jarvis"
    assert shorten_command("cd /somewhere/else && ls") == "cd /somewhere/else && ls"
    assert shorten_paths(f"{cwd}/a.py and {home}/x") == "a.py and ~/x"
    assert tf.tool_args("run_bash", {"cmd": f"cd {cwd} && make test"}) == "make test"
