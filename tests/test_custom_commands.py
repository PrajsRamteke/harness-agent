"""Custom slash-command feature — discovery, expansion, scaffold, transfer."""
import pathlib

import pytest

from jarvis.storage import commands as cc
from jarvis import state


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point discovery at a temp project and keep global dirs out of the way."""
    monkeypatch.setattr(cc, "_find_project_root", lambda: tmp_path)
    monkeypatch.setattr(cc, "HARNESS_COMMANDS_DIR", tmp_path / "globalhome" / "commands")
    monkeypatch.setattr(state, "global_commands", False)
    cc.invalidate_cache()
    yield tmp_path
    cc.invalidate_cache()


def _write_cmd(root: pathlib.Path, rel_dir: str, name: str, body: str, frontmatter: str = ""):
    d = root / rel_dir
    d.mkdir(parents=True, exist_ok=True)
    content = f"---\n{frontmatter}\n---\n\n{body}" if frontmatter else body
    (d / f"{name}.md").write_text(content, encoding="utf-8")


# ── discovery ──────────────────────────────────────────────────────────────

def test_discovers_harness_and_claude_commands(tmp_path):
    _write_cmd(tmp_path, ".harness/commands", "pr-description",
               "Write a PR description.\n$ARGUMENTS",
               frontmatter="description: Generate a PR description")
    _write_cmd(tmp_path, ".claude/commands", "fix-tests", "Run the tests and fix failures.")

    found = {c["name"]: c for c in cc.discover_commands(force=True, include_global=False)}
    assert set(found) == {"pr-description", "fix-tests"}
    assert found["pr-description"]["description"] == "Generate a PR description"
    # No frontmatter → description falls back to the first body line.
    assert found["fix-tests"]["description"] == "Run the tests and fix failures."
    assert all(c["scope"] == "project" for c in found.values())


def test_harness_dir_wins_on_duplicate_names(tmp_path):
    _write_cmd(tmp_path, ".harness/commands", "deploy", "harness version")
    _write_cmd(tmp_path, ".claude/commands", "deploy", "claude version")

    found = cc.discover_commands(force=True, include_global=False)
    assert len(found) == 1
    assert found[0]["_body"].strip() == "harness version"


def test_subdirectories_are_scanned(tmp_path):
    _write_cmd(tmp_path, ".claude/commands/git", "pr", "Open a PR.")
    found = cc.discover_commands(force=True, include_global=False)
    assert [c["name"] for c in found] == ["pr"]


def test_invalid_names_and_empty_bodies_are_skipped(tmp_path):
    _write_cmd(tmp_path, ".harness/commands", "Bad Name", "body")   # invalid stem
    _write_cmd(tmp_path, ".harness/commands", "empty", "   \n  ")    # blank body
    _write_cmd(tmp_path, ".harness/commands", "readme", "docs")     # README skipped
    _write_cmd(tmp_path, ".harness/commands", "mismatch", "body",
               frontmatter="name: other-name")                       # name/file mismatch
    assert cc.discover_commands(force=True, include_global=False) == []


def test_find_command_is_case_insensitive(tmp_path):
    _write_cmd(tmp_path, ".harness/commands", "ship-it", "Ship it.")
    cc.invalidate_cache()
    assert cc.find_command("SHIP-IT")["name"] == "ship-it"
    assert cc.find_command("nope") is None


# ── template expansion ─────────────────────────────────────────────────────

def test_expand_arguments_placeholder():
    assert cc.expand_template("Review: $ARGUMENTS", "the auth module") == "Review: the auth module"


def test_expand_positional_placeholders():
    out = cc.expand_template("from $1 to $2", "main release")
    assert out == "from main to release"


def test_unfilled_positionals_are_removed():
    assert cc.expand_template("between $1 and $2", "only-one") == "between only-one and"


def test_args_appended_when_no_placeholder():
    out = cc.expand_template("Fix the bug.", "in login flow")
    assert out == "Fix the bug.\n\nin login flow"


def test_no_args_no_placeholder_unchanged():
    assert cc.expand_template("Just do it.", "") == "Just do it."


def test_expand_command_end_to_end(tmp_path):
    _write_cmd(tmp_path, ".harness/commands", "review", "Review $1 carefully. Notes: $ARGUMENTS")
    cc.invalidate_cache()
    out = cc.expand_command("review", "auth.py be strict")
    assert out == "Review auth.py carefully. Notes: auth.py be strict"
    assert cc.expand_command("missing", "x") is None


# ── scaffold / delete ──────────────────────────────────────────────────────

def test_scaffold_creates_triggerable_command(tmp_path):
    ok, path = cc.scaffold_command("daily-standup", scope="project", description="Standup notes")
    assert ok
    p = pathlib.Path(path)
    assert p == tmp_path / ".harness/commands/daily-standup.md"
    assert "description: Standup notes" in p.read_text()
    assert cc.find_command("daily-standup") is not None


def test_scaffold_rejects_reserved_and_invalid_names(tmp_path):
    ok, msg = cc.scaffold_command("help", scope="project")
    assert not ok and "built-in" in msg
    ok, msg = cc.scaffold_command("Bad Name", scope="project")
    assert not ok
    # duplicates refused
    assert cc.scaffold_command("dup", scope="project")[0]
    assert not cc.scaffold_command("dup", scope="project")[0]


def test_scaffold_global_scope_and_delete(tmp_path):
    ok, path = cc.scaffold_command("everywhere", scope="global")
    assert ok and str(cc.HARNESS_COMMANDS_DIR) in path
    monkey_global = cc.discover_commands(force=True, include_global=True)
    assert any(c["name"] == "everywhere" and c["scope"] == "global" for c in monkey_global)

    ok, deleted = cc.delete_command("everywhere")
    assert ok and not pathlib.Path(deleted).exists()
    assert cc.find_command("everywhere") is None


# ── write_command (TUI editor save path) ───────────────────────────────────

def test_write_command_creates_and_updates_in_place(tmp_path):
    ok, path = cc.write_command("report", "Daily report", "Summarize $ARGUMENTS")
    assert ok
    p = pathlib.Path(path)
    assert p == tmp_path / ".harness/commands/report.md"

    # update in place — same path, new content
    ok, path2 = cc.write_command("report", "Better description", "New body",
                                 existing_path=path)
    assert ok and path2 == path
    content = p.read_text()
    assert "description: Better description" in content and "New body" in content


def test_write_command_edits_global_file_where_it_lives(tmp_path):
    gdir = cc.HARNESS_COMMANDS_DIR
    gdir.mkdir(parents=True)
    gfile = gdir / "deploy.md"
    gfile.write_text("---\nname: deploy\ndescription: old\n---\n\nold body\n")

    ok, path = cc.write_command("deploy", "new desc", "new body",
                                existing_path=str(gfile))
    assert ok and pathlib.Path(path) == gfile  # saved back to the global dir
    assert "new body" in gfile.read_text()


def test_write_command_rename_moves_file_and_preserves_hint(tmp_path):
    d = tmp_path / ".harness/commands"
    d.mkdir(parents=True)
    old = d / "old-name.md"
    old.write_text('---\nname: old-name\ndescription: d\nargument-hint: "[file]"\n---\n\nbody\n')

    ok, path = cc.write_command("new-name", "d", "body", existing_path=str(old))
    assert ok
    assert not old.exists()
    new = pathlib.Path(path)
    assert new.name == "new-name.md"
    assert 'argument-hint: "[file]"' in new.read_text()


def test_write_command_rejects_bad_input(tmp_path):
    assert not cc.write_command("Bad Name", "d", "body")[0]
    assert not cc.write_command("help", "d", "body")[0]          # reserved
    assert not cc.write_command("empty-body", "d", "   ")[0]     # blank template
    # rename onto an existing sibling is refused
    d = tmp_path / ".harness/commands"
    d.mkdir(parents=True)
    (d / "a.md").write_text("body a")
    (d / "b.md").write_text("body b")
    ok, msg = cc.write_command("b", "d", "x", existing_path=str(d / "a.md"))
    assert not ok and "already exists" in msg


# ── import / export ────────────────────────────────────────────────────────

def test_export_to_global_and_import_back(tmp_path):
    _write_cmd(tmp_path, ".harness/commands", "zz-transfer", "transfer me")
    cc.invalidate_cache()

    res = cc.export_command_to_global("zz-transfer")
    assert res["added"] == ["zz-transfer"]
    assert (cc.HARNESS_COMMANDS_DIR / "zz-transfer.md").exists()

    # re-export is a no-op skip (project copy still wins discovery)
    res = cc.export_command_to_global("zz-transfer")
    assert res["skipped"] == ["zz-transfer"]

    # remove the project copy, then import the global one back
    (tmp_path / ".harness/commands/zz-transfer.md").unlink()
    cc.invalidate_cache()
    res = cc.import_command_to_project("zz-transfer")
    assert res["added"] == ["zz-transfer"]
    assert (tmp_path / ".harness/commands/zz-transfer.md").exists()


# ── handler / dispatch integration ─────────────────────────────────────────

def test_handle_command_run_returns_expanded_prompt(tmp_path):
    from jarvis.commands.command import handle_command
    _write_cmd(tmp_path, ".harness/commands", "greet", "Say hello to $ARGUMENTS")
    cc.invalidate_cache()

    handled, send = handle_command("/command", "run greet the team")
    assert handled and send == "Say hello to the team"

    # bare form: /command greet <args>
    handled, send = handle_command("/command", "greet everyone")
    assert handled and send == "Say hello to everyone"

    handled, send = handle_command("/command", "run nope")
    assert handled and send is None


def test_try_custom_command_fallback(tmp_path):
    from jarvis.commands.command import try_custom_command
    _write_cmd(tmp_path, ".harness/commands", "pr-description", "Describe the PR. $ARGUMENTS")
    cc.invalidate_cache()

    assert try_custom_command("/pr-description", "focus on auth") == \
        "Describe the PR. focus on auth"
    assert try_custom_command("/unknown-cmd", "") is None


def test_palette_lists_custom_commands(tmp_path):
    from jarvis.tui.commands_catalog import filter_commands
    _write_cmd(tmp_path, ".harness/commands", "zz-palette-cmd", "body",
               frontmatter="description: palette test entry")
    cc.invalidate_cache()

    matches = filter_commands("zz-palette-cmd")
    assert any(c.strip() == "/zz-palette-cmd" for c, _ in matches)


def test_palette_hides_custom_commands_shadowing_builtins(tmp_path):
    from jarvis.tui.commands_catalog import filter_commands
    _write_cmd(tmp_path, ".harness/commands", "help", "shadow attempt")
    cc.invalidate_cache()

    entries = [(c, d) for c, d in filter_commands("/help") if c.strip() == "/help"]
    assert len(entries) == 1  # only the built-in
