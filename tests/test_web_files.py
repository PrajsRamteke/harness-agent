"""The web inspector's file browser (jarvis/web/files_api.py).

Real files in a temp folder; git is replaced by a fake so the markers and the
name index are deterministic and nothing depends on the machine's git state.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.web import files_api, git_api


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A small project; git answers "not a repository" unless a test says otherwise."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    (tmp_path / "src" / "util.py").write_text("x = 1\n")
    (tmp_path / "README.md").write_text("# Title\n")
    (tmp_path / ".env").write_text("A=1\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n")
    monkeypatch.setattr(git_api, "_run", lambda args, cwd, timeout=git_api.GIT_TIMEOUT: (128, "", "not a git repository"))
    monkeypatch.setattr(files_api, "_cache", {})
    # The Trash is the temp folder's own, never the real one.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "_home"))
    return tmp_path


def post(project, op, **data):
    return files_api.post(op, data, cwd=str(project))


# ─── Paths ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["../x", "a/../../x", "src/../../..", ".git/config", "a/.git/b"])
def test_paths_outside_or_in_git_are_refused(project, bad):
    for op in ("list", "read", "stat"):
        res = post(project, op, path=bad)
        assert res["ok"] is False, (op, bad)
    assert post(project, "write", path=bad, content="x")["ok"] is False
    assert post(project, "delete", path=bad)["ok"] is False


def test_absolute_paths_are_project_relative_never_the_real_file(project):
    # "/etc/passwd" means <project>/etc/passwd: it can't reach the machine's file.
    assert post(project, "read", path="/etc/passwd")["code"] == "not_found"
    assert post(project, "create", path="/tmp/evil.txt")["path"] == "tmp/evil.txt"
    assert (project / "tmp" / "evil.txt").exists() and not Path("/tmp/evil.txt").exists()


def test_symlink_pointing_away_is_refused(project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.txt").write_text("nope")
    os.symlink(outside / "secret.txt", project / "link.txt")
    os.symlink(outside, project / "linkdir")
    assert post(project, "read", path="link.txt")["code"] == "outside"
    assert post(project, "list", path="linkdir")["code"] == "outside"
    assert post(project, "write", path="link.txt", content="x")["code"] == "outside"
    assert (outside / "secret.txt").read_text() == "nope"


def test_symlink_inside_the_project_works(project):
    os.symlink(project / "README.md", project / "docs.md")
    assert post(project, "read", path="docs.md")["content"] == "# Title\n"


# ─── List ─────────────────────────────────────────────────────────────────

def test_list_puts_folders_first_hides_git_and_keeps_dotfiles(project):
    res = post(project, "list", path="")
    assert res["ok"]
    names = [e["name"] for e in res["entries"]]
    assert names == ["src", ".env", "README.md"]
    assert res["entries"][0]["dir"] is True
    assert ".git" not in names
    sub = post(project, "list", path="src")
    assert [e["path"] for e in sub["entries"]] == ["src/app.py", "src/util.py"]


def test_list_errors(project):
    assert post(project, "list", path="nope")["code"] == "not_found"
    assert post(project, "list", path="README.md")["code"] == "not_a_folder"


def test_list_marks_changed_files_and_their_folders(project, monkeypatch):
    def fake(args, cwd, timeout=git_api.GIT_TIMEOUT):
        if args[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return 0, f"{project.resolve()}\n", ""
        if args[:2] == ["git", "status"]:
            return 0, " M src/app.py\0?? README.md\0R  src/new.py\0src/old.py\0", ""
        if "check-ignore" in args:
            return 0, ".env\n", ""
        return 0, "", ""

    monkeypatch.setattr(git_api, "_run", fake)
    root = {e["name"]: e for e in post(project, "list", path="")["entries"]}
    assert root["src"]["git"] == "dirty"
    assert root["README.md"]["git"] == "U"
    assert root[".env"]["ignored"] is True and root["README.md"]["ignored"] is False
    sub = {e["name"]: e["git"] for e in post(project, "list", path="src")["entries"]}
    assert sub == {"app.py": "M", "util.py": ""}


def test_git_paths_are_relative_to_the_folder_inside_a_repo(project, monkeypatch):
    top = project.parent

    def fake(args, cwd, timeout=git_api.GIT_TIMEOUT):
        if args[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return 0, f"{top.resolve()}\n", ""
        if args[:2] == ["git", "status"]:
            return 0, f" M {project.name}/src/app.py\0 M elsewhere/x.py\0", ""
        return 1, "", ""

    monkeypatch.setattr(git_api, "_run", fake)
    res = post(project, "list", path="src")
    assert res["prefix"] == f"{project.name}/"
    assert {e["name"]: e["git"] for e in res["entries"]}["app.py"] == "M"


# ─── Read ─────────────────────────────────────────────────────────────────

def test_read_text(project):
    res = post(project, "read", path="src/app.py")
    assert res["ok"] and res["kind"] == "text"
    assert res["content"] == "print('hi')\n"
    assert res["eol"] == "lf" and res["bom"] is False and res["lines"] == 1
    assert res["ext"] == "py" and res["writable"] is True and res["etag"]


def test_read_detects_crlf_and_bom(project):
    (project / "win.txt").write_bytes(b"\xef\xbb\xbfa\r\nb\r\n")
    res = post(project, "read", path="win.txt")
    assert res["content"] == "a\nb\n" and res["eol"] == "crlf" and res["bom"] is True
    assert res["mixed_eol"] is False


def test_read_flags_binary_non_utf8_and_large(project):
    (project / "b.bin").write_bytes(b"ab\x00cd")
    (project / "latin.txt").write_bytes("caf\xe9".encode("latin-1"))
    (project / "big.txt").write_text("x" * (files_api.MAX_EDIT_BYTES + 1))
    assert post(project, "read", path="b.bin")["kind"] == "binary"
    latin = post(project, "read", path="latin.txt")
    assert latin["kind"] == "binary" and "UTF-8" in latin["reason"]
    assert post(project, "read", path="big.txt")["kind"] == "large"


def test_read_image_is_inline(project):
    (project / "p.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 10)
    res = post(project, "read", path="p.png")
    assert res["kind"] == "image" and res["data_url"].startswith("data:image/png;base64,")


def test_read_errors(project):
    assert post(project, "read", path="missing.py")["code"] == "not_found"
    assert post(project, "read", path="src")["code"] == "not_a_file"


# ─── Write ────────────────────────────────────────────────────────────────

def test_write_roundtrip_keeps_line_endings_bom_and_mode(project):
    f = project / "win.txt"
    f.write_bytes(b"\xef\xbb\xbfa\r\n")
    os.chmod(f, 0o750)
    opened = post(project, "read", path="win.txt")
    res = post(project, "write", path="win.txt", content="a\nb\n", etag=opened["etag"], eol="crlf", bom=True)
    assert res["ok"] and res["etag"] != opened["etag"]
    assert f.read_bytes() == b"\xef\xbb\xbfa\r\nb\r\n"
    assert (f.stat().st_mode & 0o777) == 0o750
    assert not [p for p in project.iterdir() if p.name.startswith(".jarvis-save-")]


def test_write_refuses_when_the_file_changed_on_disk(project):
    f = project / "src" / "app.py"
    opened = post(project, "read", path="src/app.py")
    f.write_text("changed by Jarvis\n")
    res = post(project, "write", path="src/app.py", content="mine\n", etag=opened["etag"])
    assert res["ok"] is False and res["code"] == "conflict"
    assert f.read_text() == "changed by Jarvis\n"
    forced = post(project, "write", path="src/app.py", content="mine\n", etag=opened["etag"], force=True)
    assert forced["ok"] and f.read_text() == "mine\n"


def test_write_needs_an_existing_text_file(project):
    assert post(project, "write", path="nope.py", content="x")["code"] == "not_found"
    assert post(project, "write", path="src/app.py", content=None)["code"] == "bad_content"
    too_big = "x" * (files_api.MAX_EDIT_BYTES + 1)
    assert post(project, "write", path="src/app.py", content=too_big)["code"] == "too_large"


def test_base_without_a_repository_is_none(project):
    res = post(project, "base", path="src/app.py")
    assert res["ok"] and res["repo"] is False and res["base"] is None


def test_stat_follows_changes(project):
    a = post(project, "stat", path="src/app.py")
    (project / "src" / "app.py").write_text("longer content now\n")
    b = post(project, "stat", path="src/app.py")
    assert a["exists"] and b["exists"] and a["etag"] != b["etag"]
    assert post(project, "stat", path="gone.py")["exists"] is False


# ─── Create / rename / delete ─────────────────────────────────────────────

def test_create_file_and_nested_folder(project):
    assert post(project, "create", path="src/new.py", kind="file")["ok"]
    assert (project / "src" / "new.py").read_text() == ""
    assert post(project, "create", path="pkg/sub/mod.py", kind="file")["ok"]
    assert (project / "pkg" / "sub" / "mod.py").is_file()
    assert post(project, "create", path="assets", kind="dir")["ok"]
    assert (project / "assets").is_dir()


def test_create_refuses_existing_and_bad_names(project):
    assert post(project, "create", path="src/app.py")["code"] == "exists"
    assert post(project, "create", path="")["code"] == "bad_name"
    assert post(project, "create", path="a/" + "x" * 300)["code"] == "bad_name"


def test_rename_moves_and_refuses_to_overwrite(project):
    assert post(project, "rename", path="src/util.py", to="src/helpers.py")["ok"]
    assert (project / "src" / "helpers.py").read_text() == "x = 1\n"
    assert not (project / "src" / "util.py").exists()
    assert post(project, "rename", path="src/helpers.py", to="src/app.py")["code"] == "exists"
    assert post(project, "rename", path="src", to="src/inner/src")["code"] == "bad_name"
    assert post(project, "rename", path="src/app.py", to="../out.py")["code"] == "outside"
    assert post(project, "rename", path="src/app.py", to="src/app.py")["unchanged"] is True


def test_rename_case_only(project):
    assert post(project, "rename", path="README.md", to="Readme.md")["ok"]
    assert "Readme.md" in os.listdir(project)


def test_delete_moves_to_the_trash(project):
    res = post(project, "delete", path="src/util.py")
    assert res["ok"] and not (project / "src" / "util.py").exists()
    assert Path(res["trash"]).read_text() == "x = 1\n"
    (project / "src" / "util.py").write_text("again\n")
    second = post(project, "delete", path="src/util.py")
    assert second["trash"] != res["trash"] and Path(res["trash"]).exists()


def test_delete_a_folder_and_protect_the_root(project):
    res = post(project, "delete", path="src")
    assert res["ok"] and (Path(res["trash"]) / "app.py").exists()
    assert post(project, "delete", path="")["code"] == "protected"
    assert post(project, "delete", path="gone")["code"] == "not_found"


# ─── Search ───────────────────────────────────────────────────────────────

def test_search_ranks_names_first_and_matches_every_word(project):
    (project / "src" / "deep").mkdir()
    (project / "src" / "deep" / "application_log.py").write_text("")
    res = post(project, "search", query="app")
    assert [r["path"] for r in res["results"]][:2] == ["src/app.py", "src/deep/application_log.py"]
    assert post(project, "search", query="src util")["results"][0]["path"] == "src/util.py"
    assert post(project, "search", query="zzz")["results"] == []
    assert post(project, "search", query="  ")["results"] == []


def test_search_is_fuzzy_and_never_lists_git(project):
    assert post(project, "search", query="rdm")["results"][0]["path"] == "README.md"
    assert not [r for r in post(project, "search", query="config")["results"] if r["path"].startswith(".git")]


def test_search_uses_git_files_and_skips_ones_deleted_from_disk(project, monkeypatch):
    def fake(args, cwd, timeout=git_api.GIT_TIMEOUT):
        if args[:2] == ["git", "ls-files"]:
            return 0, "src/app.py\0src/removed.py\0", ""
        return 128, "", ""

    monkeypatch.setattr(git_api, "_run", fake)
    assert [r["path"] for r in post(project, "search", query="py")["results"]] == ["src/app.py"]


def test_search_fallback_skips_noise_folders(project):
    (project / "node_modules" / "pkg").mkdir(parents=True)
    (project / "node_modules" / "pkg" / "app.js").write_text("")
    assert [r["path"] for r in post(project, "search", query="app.js")["results"]] == []


def test_unknown_op(project):
    assert post(project, "explode")["ok"] is False


# ─── Against real git (the fakes above can't catch a wrong git invocation) ─

@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_real_git_markers_ignored_and_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(files_api, "_cache", {})
    repo = tmp_path / "repo"
    app = repo / "app"
    (app / "src").mkdir(parents=True)
    (repo / "other").mkdir()
    (app / "src" / "a.py").write_text("a = 1\n")
    (app / "b.py").write_text("b = 1\n")
    (repo / "other" / "o.py").write_text("o = 1\n")
    (repo / ".gitignore").write_text("node_modules/\n*.log\n")
    (app / "node_modules").mkdir()
    (app / "node_modules" / "x.js").write_text("x\n")
    (app / "debug.log").write_text("log\n")

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    (app / "src" / "a.py").write_text("a = 2\n")            # modified
    (app / "new.py").write_text("n = 1\n")                   # untracked
    (repo / "other" / "o.py").write_text("o = 2\n")          # outside the opened folder

    top = files_api.post("list", {"path": ""}, cwd=str(app))
    by = {e["name"]: e for e in top["entries"]}
    assert top["repo"] is True and top["prefix"] == "app/"
    assert by["src"]["git"] == "dirty" and by["new.py"]["git"] == "U" and by["b.py"]["git"] == ""
    assert by["node_modules"]["ignored"] is True and by["debug.log"]["ignored"] is True
    assert by["b.py"]["ignored"] is False
    sub = {e["name"]: e["git"] for e in files_api.post("list", {"path": "src"}, cwd=str(app))["entries"]}
    assert sub == {"a.py": "M"}
    read = files_api.post("read", {"path": "src/a.py"}, cwd=str(app))
    assert read["git"] == "M" and read["prefix"] == "app/" and read["repo"] is True
    # The change gutter's base: HEAD's text for tracked files, "" for new ones, none for ignored / outside a repo.
    assert files_api.post("base", {"path": "src/a.py"}, cwd=str(app)) == {
        "ok": True, "path": "src/a.py", "repo": True, "tracked": True, "base": "a = 1\n"}
    new = files_api.post("base", {"path": "new.py"}, cwd=str(app))
    assert new["tracked"] is False and new["base"] == ""
    ignored = files_api.post("base", {"path": "debug.log"}, cwd=str(app))
    assert ignored["base"] is None and ignored["repo"] is True
    assert files_api.post("base", {"path": "missing.py"}, cwd=str(app))["code"] == "not_found"
    assert files_api.post("base", {"path": "../x"}, cwd=str(app))["ok"] is False
    # The name search sees tracked + untracked files, never ignored ones or the outside.
    found = [r["path"] for r in files_api.post("search", {"query": "py"}, cwd=str(app))["results"]]
    assert sorted(found) == ["b.py", "new.py", "src/a.py"]
