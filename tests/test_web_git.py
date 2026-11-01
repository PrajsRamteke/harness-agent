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
    # The page checks this before a From/Into PR (an older server ignored them).
    assert st["api"] == git_api.GIT_API_VERSION >= 2


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


LOCAL_BRANCHES = {("git", "for-each-ref", "--format=%(refname:short)", "refs/heads"): (0, "main\nfeat/old\n", "")}


def test_pr_from_main_moves_the_work_to_a_new_branch(fake, monkeypatch):
    """On main with changes: create feat/x from them, commit, push, PR feat/x → main."""
    monkeypatch.setattr(git_api.shutil, "which", lambda n: "/bin/" + n)
    f = fake({**REPO, **LOCAL_BRANCHES,
              ("git", "check-ref-format"): (0, "feat/x\n", ""),
              ("git", "diff", "--cached", "--quiet"): (1, "", ""),
              ("git", "rev-parse", "--short", "HEAD"): (0, "9f8e7d6\n", ""),
              ("gh", "pr", "create"): (0, "https://github.com/o/r/pull/7\n", "")})
    res = git_api.create_pr("/repo", "Add thing", "## Summary\n- x", base="main", head="feat/x")
    assert res["ok"] and res["url"].endswith("/pull/7")
    assert res["created_branch"] and res["commit"] == "9f8e7d6" and res["head"] == "feat/x"
    order = [c[:3] for c in f.calls if c[:2] in (["git", "switch"], ["git", "commit"], ["git", "push"], ["gh", "pr"])]
    assert order == [["git", "switch", "-c"], ["git", "commit", "-m"], ["git", "push", "-u"], ["gh", "pr", "create"]]
    assert f.ran("git", "switch", "-c", "feat/x")
    assert f.ran("git", "push", "-u", "origin", "HEAD")
    assert f.ran("gh", "pr", "create", "--base", "main", "--head", "feat/x", "--title", "Add thing", "--body", "## Summary\n- x")


def test_pr_needs_two_different_branches_and_a_title_for_changes(fake, monkeypatch):
    monkeypatch.setattr(git_api.shutil, "which", lambda n: "/bin/" + n)
    f = fake({**REPO, **LOCAL_BRANCHES, ("git", "check-ref-format"): (0, "feat/x\n", "")})
    res = git_api.create_pr("/repo", "t", base="main", head="main")
    assert not res["ok"] and "another" in res["error"]
    res = git_api.create_pr("/repo", "", base="main")  # head = the branch you're on = main
    assert not res["ok"] and "another" in res["error"]
    res = git_api.create_pr("/repo", "   ", base="main", head="feat/x")
    assert not res["ok"] and "title" in res["error"]
    assert not any(c[:2] in (["git", "commit"], ["git", "push"], ["gh", "pr"]) for c in f.calls)


def test_pr_from_another_existing_branch_pushes_it_as_is(fake, monkeypatch):
    monkeypatch.setattr(git_api.shutil, "which", lambda n: "/bin/" + n)
    f = fake({**REPO, **LOCAL_BRANCHES, ("git", "check-ref-format"): (0, "feat/old\n", ""),
              ("gh", "pr", "create"): (0, "https://github.com/o/r/pull/8\n", "")})
    res = git_api.create_pr("/repo", "", base="main", head="feat/old")
    assert res["ok"] and not res["created_branch"] and res["commit"] == ""
    assert f.ran("git", "push", "-u", "origin", "feat/old")
    assert f.ran("gh", "pr", "create", "--base", "main", "--head", "feat/old", "--fill")
    assert not any(c[:2] in (["git", "switch"], ["git", "commit"]) for c in f.calls)


def test_create_pr_finds_an_existing_one_and_needs_gh(fake, monkeypatch):
    monkeypatch.setattr(git_api.shutil, "which", lambda n: "/bin/" + n)
    fake({**REPO, **LOCAL_BRANCHES, ("git", "status"): (0, "## feat/old...origin/feat/old\n", ""),
          ("git", "check-ref-format"): (0, "feat/old\n", ""),
          ("gh", "pr", "create"): (1, "", 'a pull request for branch "feat/old" into branch "main" already exists:\nhttps://github.com/o/r/pull/3\n')})
    res = git_api.create_pr("/repo", base="main")
    assert res["ok"] and res["existing"] and res["url"].endswith("/pull/3")
    monkeypatch.setattr(git_api.shutil, "which", lambda n: None)
    assert "GitHub CLI" in git_api.create_pr("/repo")["error"]


def test_pr_onto_a_new_branch_waits_while_jarvis_works(fake):
    f = fake({**REPO})
    res = git_api.post("pr", {"head": "feat/x", "base": "main"}, busy=True)
    assert not res["ok"] and res["code"] == "busy"
    assert not any(c[:2] in (["git", "switch"], ["gh", "pr"]) for c in f.calls)


def test_branches_list_pr_targets_default_first(fake):
    fake({("git", "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)\t%(HEAD)", "refs/heads"): (0, "feat/a\t*\nmain\t\n", ""),
          ("git", "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/remotes"): (0, "origin/HEAD\norigin/dev\norigin/main\n", ""),
          ("git", "symbolic-ref"): (0, "origin/main\n", "")})
    res = git_api.branches("/repo")
    assert res["current"] == "feat/a" and res["base"] == "main"
    assert res["bases"] == ["main", "dev"]
    assert res["remote"] == ["origin/dev"]


def test_branch_names_from_titles_and_the_model(fake, monkeypatch):
    assert git_api.slug_branch("feat(web): Add the git card") == "feat/add-the-git-card"
    assert git_api.slug_branch("Fix crash on empty title") == "fix/crash-on-empty-title"
    assert git_api.slug_branch("!!!") == ""
    fake({**REPO, ("git", "diff", "HEAD"): (0, "+x\n", "")})
    monkeypatch.setattr(git_api, "_ask_model", lambda s, u, **kw: "fix/login-redirect")
    assert git_api.generate("/repo", "branch") == {"ok": True, "branch": "fix/login-redirect"}
    monkeypatch.setattr(git_api, "_ask_model", lambda s, u, **kw: "Sure! Improve Login Redirect")
    assert git_api.generate("/repo", "branch")["branch"] == "feat/sure-improve-login-redirect"


def test_generate_asks_the_model_with_the_diff(fake, monkeypatch):
    fake({**REPO, ("git", "diff", "HEAD"): (0, "diff --git a/a.py b/a.py\n+new line\n", ""),
          ("git", "log", "-8"): (0, "feat(web): earlier\n", "")})
    seen = {}

    def ask(system, user, **kw):
        seen["system"], seen["user"] = system, user
        return "feat(web): add the thing"

    monkeypatch.setattr(git_api, "_ask_model", ask)
    res = git_api.generate("/repo")
    assert res == {"ok": True, "message": "feat(web): add the thing"}
    assert "+new line" in seen["user"] and "feat(web): earlier" in seen["user"]

    monkeypatch.setattr(git_api, "_ask_model", lambda s, u, **kw: "Add the thing\n\n## Summary\n- x")
    fake({**REPO, ("git", "symbolic-ref"): (0, "origin/main\n", ""),
          ("git", "log", "origin/main..HEAD"): (0, "- one\n", "")})
    res = git_api.generate("/repo", "pr")
    assert res["ok"] and res["title"] == "Add the thing" and res["body"].startswith("## Summary")
    assert res["branch"] == "feat/add-the-thing"  # no Branch: line → named after the title


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


def test_pr_answer_carries_its_branch_and_a_title_names_one_without_the_model(fake, monkeypatch):
    fake({**REPO, ("git", "diff", "HEAD"): (0, "+x\n", ""), ("git", "symbolic-ref"): (0, "origin/main\n", "")})
    monkeypatch.setattr(git_api, "_ask_model",
                        lambda s, u, **kw: "Add the git card\nBranch: feat/web-git-card\n\n## Summary\n- x")
    res = git_api.generate("/repo", "pr")
    assert res == {"ok": True, "title": "Add the git card", "branch": "feat/web-git-card", "body": "## Summary\n- x"}

    def boom(*a, **kw):
        raise AssertionError("no model call needed")

    monkeypatch.setattr(git_api, "_ask_model", boom)
    assert git_api.generate("/repo", "branch", title="Fixes the login loop") == {"ok": True, "branch": "fix/the-login-loop"}


def test_same_question_is_answered_from_the_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(git_api, "_gen_cache", {})
    monkeypatch.setattr(git_api, "_ask_model_uncached", lambda s, u, max_tokens: calls.append(u) or "fix: x")
    assert git_api._ask_model("sys", "diff A") == "fix: x"
    assert git_api._ask_model("sys", "diff A") == "fix: x"
    assert git_api._ask_model("sys", "diff B") == "fix: x"
    assert calls == ["diff A", "diff B"]
