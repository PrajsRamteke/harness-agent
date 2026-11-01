"""The web inspector's git card (jarvis/web/git_api.py).

Every git / gh / open call goes through ``git_api._run``; these tests replace it
with a fake that records the command and answers from a script, so nothing is
ever really committed, pushed, stashed or switched.
"""
from __future__ import annotations

import pytest

from jarvis.web import git_api


class FakeGit:
    """Answers commands by their longest matching prefix; records every call."""

    def __init__(self, answers: dict[tuple[str, ...], tuple[int, str, str]]):
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, args, cwd, timeout=git_api.GIT_TIMEOUT):
        self.calls.append(list(args))
        best = None
        for prefix, answer in self.answers.items():
            if tuple(args[:len(prefix)]) == prefix and (best is None or len(prefix) > len(best[0])):
                best = (prefix, answer)
        return best[1] if best else (0, "", "")

    def ran(self, *args: str) -> bool:
        return list(args) in self.calls


@pytest.fixture
def fake(monkeypatch):
    def install(answers):
        f = FakeGit(answers)
        monkeypatch.setattr(git_api, "_run", f)
        return f
    return install


REPO = {
    ("git", "rev-parse", "--show-toplevel"): (0, "/repo\n", ""),
    ("git", "status"): (0, "## main...origin/main [ahead 2, behind 1]\n M a.py\nA  b.py\n?? c.txt\n", ""),
    ("git", "rev-parse", "--verify", "-q", "HEAD"): (0, "abc\n", ""),
    ("git", "diff", "--numstat", "HEAD"): (0, "10\t3\ta.py\n5\t0\tb.py\n-\t-\timg.png\n", ""),
    ("git", "remote"): (0, "origin\n", ""),
}


def test_branch_line_parsing():
    p = git_api._parse_branch_line
    assert p("## main...origin/main [ahead 2, behind 1]") == {
        "branch": "main", "upstream": "origin/main", "ahead": 2, "behind": 1, "gone": False}
    assert p("## feat/x")["upstream"] == ""
    assert p("## No commits yet on main")["branch"] == "main"
    assert p("## dev...origin/dev [gone]")["gone"] is True
    assert p("## HEAD (no branch)")["branch"] == ""


def test_status_reads_branch_tracking_and_line_counts(fake, tmp_path):
    (tmp_path / "c.txt").write_text("one\ntwo\nthree\n")
    answers = dict(REPO)
    answers[("git", "rev-parse", "--show-toplevel")] = (0, f"{tmp_path}\n", "")
    fake(answers)
    st = git_api.status(str(tmp_path))
    assert st["repo"] and st["branch"] == "main" and st["upstream"] == "origin/main"
    assert (st["ahead"], st["behind"]) == (2, 1)
    assert (st["files"], st["staged"], st["untracked"]) == (3, 1, 1)
    assert (st["added"], st["removed"]) == (10 + 5 + 3, 3)  # the untracked file's 3 lines count
    assert st["remotes"] == ["origin"]


def test_status_outside_a_repo(fake):
    fake({("git", "rev-parse", "--show-toplevel"): (128, "", "fatal: not a git repository")})
    st = git_api.status("/tmp")
    assert st["ok"] and st["repo"] is False and "editors" in st


def test_commit_adds_everything_commits_then_pushes(fake):
    f = fake({**REPO, ("git", "diff", "--cached", "--quiet"): (1, "", ""),
              ("git", "rev-parse", "--short", "HEAD"): (0, "1a2b3c4\n", "")})
    res = git_api.commit("/repo", "feat: do it\n\n- details", then_push=True)
    assert res["ok"] and res["sha"] == "1a2b3c4" and res["pushed"] is True
    assert f.ran("git", "add", "--all")
    assert f.ran("git", "commit", "-m", "feat: do it\n\n- details")
    assert f.ran("git", "push")  # it has an upstream


def test_commit_refuses_an_empty_message_or_nothing_staged(fake):
    f = fake({**REPO, ("git", "diff", "--cached", "--quiet"): (0, "", "")})
    assert not git_api.commit("/repo", "  ")["ok"]
    res = git_api.commit("/repo", "msg")
    assert not res["ok"] and res["error"] == "Nothing to commit"
    assert not any(c[:2] == ["git", "commit"] for c in f.calls)


def test_commit_kept_when_the_push_fails(fake):
    fake({**REPO, ("git", "diff", "--cached", "--quiet"): (1, "", ""),
          ("git", "push"): (1, "", " ! [rejected]        main -> main (fetch first)\nerror: failed to push some refs")})
    res = git_api.commit("/repo", "msg", then_push=True)
    assert res["ok"] and res["pushed"] is False and "rejected" in res["push_error"]


def test_push_publishes_a_branch_without_upstream(fake):
    answers = {**REPO, ("git", "status"): (0, "## feat/new\n", "")}
    f = fake(answers)
    res = git_api.push("/repo")
    assert res == {"ok": True, "published": True, "remote": "origin"}
    assert f.ran("git", "push", "-u", "origin", "HEAD")


def test_push_without_a_remote_says_so(fake):
    fake({**REPO, ("git", "status"): (0, "## main\n", ""), ("git", "remote"): (0, "", "")})
    res = git_api.push("/repo")
    assert not res["ok"] and "no remote" in res["error"]


def test_checkout_local_remote_new_and_invalid(fake):
    f = fake({("git", "check-ref-format"): (0, "dev\n", ""),
              ("git", "show-ref", "--verify", "-q", "refs/heads/dev"): (0, "", ""),
              ("git", "rev-parse", "--show-toplevel"): (1, "", "")})
    assert git_api.checkout("/repo", "dev")["ok"] and f.ran("git", "switch", "dev")

    f = fake({("git", "check-ref-format"): (0, "origin/x\n", ""),
              ("git", "show-ref", "--verify", "-q", "refs/heads/origin/x"): (1, "", ""),
              ("git", "show-ref", "--verify", "-q", "refs/remotes/origin/x"): (0, "", ""),
              ("git", "rev-parse", "--show-toplevel"): (1, "", "")})
    assert git_api.checkout("/repo", "origin/x")["ok"] and f.ran("git", "switch", "--track", "origin/x")

    f = fake({("git", "check-ref-format"): (0, "feat/y\n", ""), ("git", "rev-parse", "--show-toplevel"): (1, "", "")})
    assert git_api.checkout("/repo", "feat/y", create=True)["ok"] and f.ran("git", "switch", "-c", "feat/y")

    fake({("git", "check-ref-format"): (1, "", "fatal")})
    assert not git_api.checkout("/repo", "bad name..")["ok"]
    assert not git_api.checkout("/repo", "-rf")["ok"]


def test_checkout_error_comes_from_git(fake):
    fake({("git", "check-ref-format"): (0, "dev\n", ""),
          ("git", "show-ref", "--verify", "-q", "refs/heads/dev"): (0, "", ""),
          ("git", "switch"): (1, "", "error: Your local changes to the following files would be overwritten by checkout:\n\ta.py\nAborting")})
    res = git_api.checkout("/repo", "dev")
    assert not res["ok"] and res["error"].startswith("Your local changes")


def test_stash_includes_untracked_and_reports_nothing(fake):
    f = fake({("git", "stash"): (0, "Saved working directory and index state On main: wip\n", "")})
    assert git_api.stash("/repo", "wip") == {"ok": True, "message": "wip"}
    assert f.ran("git", "stash", "push", "--include-untracked", "-m", "wip")
    fake({("git", "stash"): (0, "No local changes to save\n", "")})
    assert git_api.stash("/repo")["error"] == "Nothing to stash"


def test_checkout_and_stash_wait_while_jarvis_works(fake):
    f = fake({})
    for op in ("checkout", "stash"):
        res = git_api.post(op, {"branch": "dev"}, busy=True)
        assert not res["ok"] and res["code"] == "busy"
    assert f.calls == []


def test_create_pr_pushes_first_and_returns_the_url(fake, monkeypatch):
    monkeypatch.setattr(git_api.shutil, "which", lambda n: "/bin/" + n)
    f = fake({**REPO, ("gh", "pr", "create"): (0, "https://github.com/o/r/pull/7\n", "")})
    res = git_api.create_pr("/repo", "Add thing", "## Summary\n- x")
    assert res == {"ok": True, "url": "https://github.com/o/r/pull/7"}
    assert f.ran("git", "push")  # ahead 2 → pushed before gh
    assert f.ran("gh", "pr", "create", "--title", "Add thing", "--body", "## Summary\n- x")


def test_create_pr_finds_an_existing_one_and_needs_gh(fake, monkeypatch):
    monkeypatch.setattr(git_api.shutil, "which", lambda n: "/bin/" + n)
    fake({**REPO, ("git", "status"): (0, "## main...origin/main\n", ""),
          ("gh", "pr", "create"): (1, "", 'a pull request for branch "main" into branch "dev" already exists:\nhttps://github.com/o/r/pull/3\n')})
    res = git_api.create_pr("/repo")
    assert res["ok"] and res["existing"] and res["url"].endswith("/pull/3")
    monkeypatch.setattr(git_api.shutil, "which", lambda n: None)
    assert "GitHub CLI" in git_api.create_pr("/repo")["error"]


def test_generate_asks_the_model_with_the_diff(fake, monkeypatch):
    fake({**REPO, ("git", "diff", "HEAD"): (0, "diff --git a/a.py b/a.py\n+new line\n", ""),
          ("git", "log", "-8"): (0, "feat(web): earlier\n", "")})
    seen = {}

    def ask(system, user):
        seen["system"], seen["user"] = system, user
        return "feat(web): add the thing"

    monkeypatch.setattr(git_api, "_ask_model", ask)
    res = git_api.generate("/repo")
    assert res == {"ok": True, "message": "feat(web): add the thing"}
    assert "+new line" in seen["user"] and "feat(web): earlier" in seen["user"]

    monkeypatch.setattr(git_api, "_ask_model", lambda s, u: "Add the thing\n\n## Summary\n- x")
    fake({**REPO, ("git", "symbolic-ref"): (0, "origin/main\n", ""),
          ("git", "log", "origin/main..HEAD"): (0, "- one\n", "")})
    res = git_api.generate("/repo", "pr")
    assert res["ok"] and res["title"] == "Add the thing" and res["body"].startswith("## Summary")


def test_model_text_is_cleaned():
    assert git_api._clean_message("Here's the commit message:\n```\nfix: x\n```") == "fix: x"
    assert git_api._clean_message("Title: Add y\n\nbody") == "Add y\n\nbody"


def test_open_in_only_knows_its_apps(fake, monkeypatch):
    f = fake({})
    assert not git_api.open_in("/repo", "rm -rf")["ok"]
    monkeypatch.setattr(git_api.sys, "platform", "darwin")
    assert git_api.open_in("/repo", "finder")["ok"] and f.ran("open", "/repo")
    monkeypatch.setattr(git_api, "_mac_app", lambda names: names[0] if names else None)
    assert git_api.open_in("/repo", "cursor")["ok"] and f.ran("open", "-a", "Cursor", "/repo")
