"""The session file-change ledger behind the web Changes panel."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from jarvis import file_changes as fc
from jarvis import state


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """A project directory that is also Jarvis' project root, with a clean ledger."""
    root = tmp_path.resolve()
    monkeypatch.setattr(fc, "CWD", root)
    monkeypatch.chdir(root)
    monkeypatch.setattr(state, "current_session_id", 1)
    fc.reset()
    yield root
    fc.reset()


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def edit(path, new, action="edit"):
    """What write_file / edit_file do: change the file, then report before/after."""
    before = path.read_text() if path.exists() else None
    path.write_text(new)
    fc.record(path, before, new, action)


def by_path(summary):
    return {f["path"]: f for f in summary["files"]}


# ─── Net diff against the first-touch baseline ──────────────────────────


def test_many_edits_read_as_one_net_change(proj):
    f = write(proj, "app.py", "a\nb\nc\nd\n")
    edit(f, "a\nB\nc\nd\n")
    edit(f, "a\nB\nc\nd\ne\n")
    edit(f, "a\nB\nc\nD\ne\n")

    row = by_path(fc.summaries())["app.py"]
    assert row["status"] == "modified"
    assert (row["added"], row["removed"]) == (3, 2)   # b→B, d→D, +e
    assert row["edits"] == 3

    d = fc.detail(row["id"])
    kinds = [r[0] for h in d["hunks"] for r in h["rows"]]
    assert kinds.count("+") == 3 and kinds.count("-") == 2
    first = d["hunks"][0]["rows"][0]
    assert first[0] == " " and first[1] == 1 and first[2] == 1   # numbered context row
    assert [s["action"] for s in d["steps"]] == ["edit"] * 3


def test_created_file_is_added_and_empty_files_count(proj):
    new = proj / "pkg" / "mod.py"
    new.parent.mkdir()
    new.write_text("x = 1\ny = 2\n")
    fc.record(new, None, "x = 1\ny = 2\n", "create")
    empty = proj / "pkg" / "__init__.py"
    empty.write_text("")
    fc.record(empty, None, "", "create")

    files = by_path(fc.summaries())
    assert files["pkg/mod.py"]["status"] == "added"
    assert (files["pkg/mod.py"]["added"], files["pkg/mod.py"]["removed"]) == (2, 0)
    assert files["pkg/__init__.py"]["status"] == "added"
    d = fc.detail(files["pkg/mod.py"]["id"])
    assert [r[0] for r in d["hunks"][0]["rows"]] == ["+", "+"]
    assert [r[2] for r in d["hunks"][0]["rows"]] == [1, 2]


def test_edit_that_is_undone_disappears(proj):
    f = write(proj, "a.txt", "one\ntwo\n")
    edit(f, "one\nTWO\n")
    assert fc.summaries()["totals"]["files"] == 1
    edit(f, "one\ntwo\n")
    assert fc.summaries()["files"] == []


def test_created_then_removed_leaves_nothing(proj):
    f = proj / "tmp.txt"
    f.write_text("scratch")
    fc.record(f, None, "scratch", "create")
    f.unlink()
    assert fc.summaries()["files"] == []


def test_change_made_behind_our_back_is_noticed(proj):
    f = write(proj, "cfg.toml", "a = 1\n")
    edit(f, "a = 2\n")
    f.write_text("a = 2\nb = 3\n")          # the user's editor, or a later shell command
    row = by_path(fc.summaries())["cfg.toml"]
    assert (row["added"], row["removed"]) == (2, 1)
    f.unlink()
    assert by_path(fc.summaries())["cfg.toml"]["status"] == "deleted"


def test_totals_and_order(proj):
    a = write(proj, "a.py", "1\n")
    b = write(proj, "b.py", "1\n")
    edit(a, "2\n")
    fc.record(proj / "c.py", None, "x\n", "create")
    (proj / "c.py").write_text("x\n")
    edit(b, "2\n3\n")
    s = fc.summaries()
    assert [f["path"] for f in s["files"]] == ["b.py", "c.py", "a.py"]   # newest first
    assert s["totals"]["files"] == 3
    assert s["totals"]["by_status"] == {"modified": 2, "added": 1}
    assert s["totals"]["added"] == 1 + 1 + 2 and s["totals"]["removed"] == 2


def test_large_files_keep_counts_but_no_diff(proj):
    f = write(proj, "big.txt", "x\n" * 10)
    huge = "line\n" * (fc.MAX_TEXT // 4)
    edit(f, huge)
    row = by_path(fc.summaries())["big.txt"]
    assert row["big"] is True and row["status"] == "modified"
    assert fc.detail(row["id"])["hunks"] == []
    assert "too large" in fc.patch_text()


def test_a_session_cannot_hold_unbounded_text(proj, monkeypatch):
    monkeypatch.setattr(fc, "MAX_LEDGER_CHARS", 2000)
    a = write(proj, "a.txt", "x\n" * 300)             # 600 chars before + 600 after: fits
    edit(a, "y\n" * 300)
    b = write(proj, "b.txt", "z\n" * 450)             # 900 + 900 more: over the cap
    edit(b, "w\n" * 450)
    files = by_path(fc.summaries())
    assert not files["a.txt"].get("big")
    assert files["b.txt"]["big"] is True and files["b.txt"]["status"] == "modified"
    assert fc.detail(files["b.txt"]["id"])["hunks"] == []
    assert fc._held(fc._ledger()) <= 2000


def test_diff_rows_are_capped(proj, monkeypatch):
    monkeypatch.setattr(fc, "MAX_DIFF_LINES", 20)
    f = write(proj, "long.txt", "")
    edit(f, "".join(f"row {i}\n" for i in range(100)), "write")
    d = fc.detail(by_path(fc.summaries())["long.txt"]["id"])
    assert sum(len(h["rows"]) for h in d["hunks"]) == 20
    assert d["hidden"] == 80


def test_sessions_keep_separate_ledgers(proj):
    f = write(proj, "a.txt", "1\n")
    edit(f, "2\n")
    state.current_session_id = 2
    assert fc.summaries()["files"] == []
    state.current_session_id = 1                 # resumed in the same process
    assert fc.summaries()["totals"]["files"] == 1


def test_ledger_survives_a_restart(proj):
    """Edits persist to SQLite and the Changes panel restores after a restart."""
    f = write(proj, "app.py", "a\nb\nc\n")
    edit(f, "a\nB\nc\nd\n")
    edit(f, "a\nB\nC\nd\n", "write")
    fc.flush()

    # Simulate a process restart: drop all in-memory state.
    fc._ledgers.clear()
    fc._persisted_sids.clear()
    fc._dirty_sids.clear()

    files = by_path(fc.summaries())
    assert list(files) == ["app.py"]
    row = files["app.py"]
    assert row["status"] == "modified" and row["edits"] == 2
    assert (row["added"], row["removed"]) == (3, 2)
    d = fc.detail(row["id"])
    assert len(d["hunks"]) > 0 and [s["action"] for s in d["steps"]] == ["edit", "write"]


def test_restored_file_tracks_later_outside_edits(proj):
    f = write(proj, "app.py", "a\n")
    edit(f, "b\n")
    fc.flush()
    fc._ledgers.clear()
    fc._persisted_sids.clear()
    fc._dirty_sids.clear()

    f.write_text("b\nc\n")   # edited outside Jarvis after the restart
    row = by_path(fc.summaries())["app.py"]
    assert row["added"] == 2


def test_deleted_session_forgets_its_changes(proj):
    from jarvis.storage.sessions import db_conn

    f = write(proj, "app.py", "a\n")
    edit(f, "b\n")
    fc.flush()
    fc.forget_session(1)
    assert fc.summaries()["files"] == []
    conn = db_conn()
    try:
        left = conn.execute(
            "SELECT COUNT(*) FROM session_file_changes WHERE session_id = 1"
        ).fetchone()[0]
    finally:
        conn.close()
    assert left == 0


def test_listeners_hear_about_each_change(proj):
    heard = []
    fc.subscribe(heard.append)
    try:
        f = write(proj, "a.txt", "1\n")
        edit(f, "2\n")
        edit(f, "3\n")
    finally:
        fc.unsubscribe(heard.append)
    assert heard == [fc.file_id(f)] * 2
    event = fc.change_event(heard[0])
    assert event["focus"] == heard[0]
    assert event["detail"]["path"] == "a.txt" and event["changes"]["totals"]["files"] == 1


def test_broken_listener_never_breaks_a_write(proj):
    def boom(_fid):
        raise RuntimeError("nope")

    fc.subscribe(boom)
    try:
        f = write(proj, "a.txt", "1\n")
        edit(f, "2\n")
    finally:
        fc.unsubscribe(boom)
    assert fc.summaries()["totals"]["files"] == 1


# ─── Shell commands ─────────────────────────────────────────────────────


def sh(cmd, root):
    finish = fc.watch_shell(cmd)
    subprocess.run(cmd, shell=True, cwd=root, check=False, capture_output=True)
    finish()


def test_rm_shows_as_deleted(proj):
    write(proj, "old.py", "print('bye')\n")
    write(proj, "keep.py", "print('stay')\n")
    sh("rm old.py", proj)
    files = by_path(fc.summaries())
    assert list(files) == ["old.py"]
    assert files["old.py"]["status"] == "deleted" and files["old.py"]["removed"] == 1
    d = fc.detail(files["old.py"]["id"])
    assert [r[0] for r in d["hunks"][0]["rows"]] == ["-"]


def test_mv_reads_as_a_rename(proj):
    write(proj, "a.py", "def f():\n    return 1\n")
    sh("mv a.py b.py", proj)
    s = fc.summaries()
    assert [(f["path"], f["status"], f.get("from")) for f in s["files"]] == [("b.py", "renamed", "a.py")]
    assert fc.detail(s["files"][0]["id"])["hunks"] == []
    assert "rename from a.py" in fc.patch_text() and "rename to b.py" in fc.patch_text()


def test_in_place_edit_and_redirect(proj):
    f = write(proj, "conf.ini", "mode=dev\n")
    sh("sed -i.bak 's/dev/prod/' conf.ini && echo done > out.log", proj)
    files = by_path(fc.summaries())
    assert files["conf.ini"]["status"] == "modified"
    assert files["out.log"]["status"] == "added"
    assert f.read_text() == "mode=prod\n"


def test_cd_then_rm_and_globs(proj):
    write(proj, "sub/a.tmp", "1\n")
    write(proj, "sub/b.tmp", "2\n")
    write(proj, "sub/c.py", "3\n")
    sh("cd sub && rm *.tmp", proj)
    assert sorted(by_path(fc.summaries())) == ["sub/a.tmp", "sub/b.tmp"]


def test_rm_recursive_walks_the_directory(proj):
    write(proj, "gen/one.py", "1\n")
    write(proj, "gen/deep/two.py", "2\n")
    sh("rm -rf gen", proj)
    assert sorted(by_path(fc.summaries())) == ["gen/deep/two.py", "gen/one.py"]


def test_created_directory_reports_its_files(proj):
    write(proj, "tpl/a.txt", "a\n")
    write(proj, "tpl/b.txt", "b\n")
    sh("cp -r tpl copy", proj)
    files = by_path(fc.summaries())
    assert files["copy/a.txt"]["status"] == "added" and files["copy/b.txt"]["status"] == "added"


def test_commands_that_change_nothing_record_nothing(proj):
    write(proj, "a.py", "x = 1\n")
    sh("cat a.py && ls && git status", proj)
    sh("rm does-not-exist.py", proj)
    assert fc.summaries()["files"] == []


def test_shell_ignores_files_outside_the_project_and_skip_dirs(proj, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "x.txt"
    outside.write_text("keep\n")
    vendored = write(proj, "node_modules/pkg/index.js", "1\n")
    sh(f"rm {outside} {vendored}", proj)
    assert fc.summaries()["files"] == []
    assert not outside.exists()


def test_globs_outside_the_project_are_never_expanded(proj, monkeypatch):
    calls = []
    real = fc.glob.iglob
    monkeypatch.setattr(fc.glob, "iglob", lambda pattern, *a, **k: calls.append(pattern) or real(pattern, *a, **k))
    write(proj, "a.py", "1\n")
    fc._shell_targets("find /usr/*/*/* -name x")
    fc._shell_targets("ls ~/*")
    assert calls == []                              # nothing outside the project was listed
    fc._shell_targets("rm *.py")
    assert calls == [str(proj / "*.py")]


def test_shell_targets_tolerate_odd_input(proj):
    write(proj, "a.py", "1\n")
    for cmd in ["echo 'unbalanced", "", "  ", "a.py " * 500, "python -c \"open('x','w')\""]:
        assert isinstance(fc._shell_targets(cmd), list)
    assert len(fc._shell_targets("cat " + " ".join(f"f{i}.txt" for i in range(500)))) <= fc.MAX_SHELL_FILES


# ─── The patch is real ─────────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_patch_applies_to_the_original_tree(proj, tmp_path):
    write(proj, "keep.txt", "same\n")
    write(proj, "edit.py", "a\nb\nc\nd\ne\n")
    write(proj, "gone.txt", "bye\n")
    write(proj, "mv_me.txt", "moved\n")
    original = tmp_path / "original"
    shutil.copytree(proj, original, ignore=shutil.ignore_patterns("original"))

    edit(proj / "edit.py", "a\nB\nc\nd\ne\nf\n")
    new = proj / "sub" / "new.py"
    new.parent.mkdir()
    new.write_text("print(1)\n")
    fc.record(new, None, "print(1)\n", "create")
    sh("rm gone.txt && mv mv_me.txt moved.txt", proj)

    patch = fc.patch_text()
    assert patch.startswith("diff --git")
    subprocess.run(["git", "init", "-q"], cwd=original, check=True)
    (original / "changes.patch").write_text(patch)
    res = subprocess.run(["git", "apply", "--check", "changes.patch"], cwd=original, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    subprocess.run(["git", "apply", "changes.patch"], cwd=original, check=True)
    for rel in ("edit.py", "sub/new.py", "moved.txt", "keep.txt"):
        assert (original / rel).read_text() == (proj / rel).read_text()
    assert not (original / "gone.txt").exists() and not (original / "mv_me.txt").exists()
    assert os.path.exists(original / "moved.txt")
