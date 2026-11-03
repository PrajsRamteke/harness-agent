"""The Files drawer's Source Control tab (jarvis/web/git_api.py: source_control,
stage, unstage, discard, scm_diff, commit(staged_only=True)).

Runs real git in a temp repository (a fake can't tell whether the porcelain is
parsed right or a pathspec really matches). The Trash is the temp folder's own.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.web import files_api, git_api

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("a = 1\n")
    (root / "src" / "b.py").write_text("b = 1\n")
    (root / "gone.py").write_text("bye\n")
    (root / "old_name.py").write_text("moved = True\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(files_api, "_cache", {})
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@t.t")
    git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    (root / "src" / "a.py").write_text("a = 2\n")
    (root / "src" / "b.py").write_text("b = 2\nb2 = 3\n")
    (root / "gone.py").unlink()
    (root / "new.py").write_text("n = 1\nm = 2\n")
    return root


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def groups(root: Path) -> dict[str, dict[str, str]]:
    res = git_api.source_control(str(root))
    assert res["ok"], res
    return {g: {r["path"]: r["status"] for r in res[g]} for g in ("staged", "unstaged", "conflicts")}


def test_lists_unstaged_changes_with_counts(repo):
    res = git_api.source_control(str(repo))
    assert res["ok"] and res["branch"] == "main" and res["prefix"] == "" and res["total"] == 4
    assert (res["upstream"], res["ahead"], res["remotes"]) == ("", 0, False)
    assert res["detached"] is False and isinstance(res["gh"], bool)
    assert res["staged"] == [] and res["conflicts"] == []
    by = {r["path"]: r for r in res["unstaged"]}
    assert {p: r["status"] for p, r in by.items()} == {
        "gone.py": "deleted", "new.py": "untracked", "src/a.py": "modified", "src/b.py": "modified"}
    assert (by["src/b.py"]["added"], by["src/b.py"]["removed"]) == (2, 1)
    assert by["new.py"]["added"] == 2


def test_stage_unstage_one_and_all(repo):
    assert git_api.stage(str(repo), ["src/a.py", "gone.py"])["ok"]
    g = groups(repo)
    assert g["staged"] == {"src/a.py": "modified", "gone.py": "deleted"}
    assert "src/a.py" not in g["unstaged"] and "gone.py" not in g["unstaged"]

    assert git_api.unstage(str(repo), ["src/a.py"])["ok"]
    g = groups(repo)
    assert g["staged"] == {"gone.py": "deleted"} and g["unstaged"]["src/a.py"] == "modified"
    assert (repo / "src" / "a.py").read_text() == "a = 2\n"  # unstaging never touches the file

    assert git_api.stage(str(repo), everything=True)["ok"]
    g = groups(repo)
    assert g["unstaged"] == {} and set(g["staged"]) == {"gone.py", "new.py", "src/a.py", "src/b.py"}
    assert g["staged"]["new.py"] == "added"

    assert git_api.unstage(str(repo), everything=True)["ok"]
    assert groups(repo)["staged"] == {}


def test_partly_staged_file_is_in_both_groups_and_diffs_differ(repo):
    git_api.stage(str(repo), ["src/a.py"])
    (repo / "src" / "a.py").write_text("a = 3\n")
    g = groups(repo)
    assert g["staged"]["src/a.py"] == "modified" and g["unstaged"]["src/a.py"] == "modified"
    staged = git_api.scm_diff(str(repo), "src/a.py", "staged")
    unstaged = git_api.scm_diff(str(repo), "src/a.py", "unstaged")
    assert staged["ok"] and unstaged["ok"] and staged["hunks"] and unstaged["hunks"]
    assert "a = 3" not in str(staged["hunks"]) and "a = 1" not in str(unstaged["hunks"])
    assert "a = 1" in str(staged["hunks"]) and "a = 2" in str(staged["hunks"])   # HEAD → index
    assert "a = 2" in str(unstaged["hunks"]) and "a = 3" in str(unstaged["hunks"])  # index → disk


def test_staged_rename_unstages_with_both_paths(repo):
    git(repo, "mv", "old_name.py", "new_name.py")
    g = groups(repo)
    assert g["staged"].get("new_name.py") == "renamed"
    row = next(r for r in git_api.source_control(str(repo))["staged"] if r["path"] == "new_name.py")
    assert row["old_path"] == "old_name.py"
    assert git_api.unstage(str(repo), ["new_name.py", "old_name.py"])["ok"]
    g = groups(repo)
    assert "new_name.py" not in g["staged"] and g["unstaged"]["new_name.py"] == "untracked"
    assert g["unstaged"]["old_name.py"] == "deleted"


def test_discard_keeps_a_copy_in_the_trash(repo, tmp_path):
    trash = tmp_path / "home" / ".Trash"
    res = git_api.discard(str(repo), ["src/b.py", "new.py", "gone.py"])
    assert res["ok"] and res["discarded"] == 3 and res["trashed"] == ["new.py"]
    assert (repo / "src" / "b.py").read_text() == "b = 1\n"          # back to HEAD
    assert (repo / "gone.py").read_text() == "bye\n"                 # deleted file restored
    assert not (repo / "new.py").exists()
    assert (trash / "b (discarded).py").read_text() == "b = 2\nb2 = 3\n"
    assert (trash / "new.py").read_text() == "n = 1\nm = 2\n"
    assert groups(repo)["unstaged"] == {"src/a.py": "modified"}


def test_discard_goes_back_to_the_staged_text_not_head(repo):
    git_api.stage(str(repo), ["src/a.py"])
    (repo / "src" / "a.py").write_text("a = 3\n")
    assert git_api.discard(str(repo), ["src/a.py"])["ok"]
    assert (repo / "src" / "a.py").read_text() == "a = 2\n"
    assert groups(repo)["staged"] == {"src/a.py": "modified"}


def test_discard_all_and_nothing_left(repo):
    assert git_api.discard(str(repo), everything=True)["ok"]
    assert groups(repo) == {"staged": {}, "unstaged": {}, "conflicts": {}}
    assert git_api.discard(str(repo), everything=True) == {"ok": False, "error": "Nothing to discard"}


def test_commit_staged_only_leaves_the_rest(repo):
    git_api.stage(str(repo), ["src/a.py"])
    res = git_api.commit(str(repo), "only a", staged_only=True)
    assert res["ok"], res
    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["src/a.py"]
    assert set(groups(repo)["unstaged"]) == {"gone.py", "new.py", "src/b.py"}
    again = git_api.commit(str(repo), "nothing", staged_only=True)
    assert again["ok"] is False and "stage" in again["error"].lower()


@pytest.mark.parametrize("bad", ["../x", "/etc/passwd", ".git/config", "src/../../x", ""])
def test_paths_outside_the_repository_are_refused(repo, bad):
    for fn in (git_api.stage, git_api.unstage, git_api.discard):
        res = fn(str(repo), [bad])
        assert res["ok"] is False, (fn.__name__, bad)
    assert (repo / "src" / "b.py").read_text() == "b = 2\nb2 = 3\n"


def test_subfolder_prefix_and_post_routing(repo, monkeypatch):
    monkeypatch.chdir(repo / "src")
    res = git_api.post("scm", {})
    assert res["ok"] and res["prefix"] == "src/"
    assert git_api.post("discard", {"paths": ["src/a.py"]}, busy=True)["code"] == "busy"
    assert git_api.post("stage", {"paths": ["src/a.py"]})["ok"]
    diff = git_api.post("diff", {"side": "staged", "path": "src/a.py"})
    assert diff["ok"] and diff["side"] == "staged" and diff["status"] == "modified"
    assert git_api.post("diff", {"side": "unstaged", "path": "src/a.py"})["code"] == "gone"


def test_ahead_of_the_upstream(repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    git_api.stage(str(repo), ["src/a.py"])
    assert git_api.commit(str(repo), "one", staged_only=True)["ok"]
    res = git_api.source_control(str(repo))
    assert (res["upstream"], res["ahead"], res["remotes"]) == ("origin/main", 1, True)


def test_not_a_repository(tmp_path):
    res = git_api.source_control(str(tmp_path))
    assert res["ok"] is False and "repository" in res["error"].lower()
