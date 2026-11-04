"""scripts/pr_shots.py — spec parsing, PR Markdown, and the assets-branch upload.

The upload test runs against throwaway repos (a bare "remote" + a clone), never
this checkout, and checks the clone's HEAD, index and working tree are untouched.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("pr_shots", ROOT / "scripts" / "pr_shots.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pr_shots"] = mod
    spec.loader.exec_module(mod)
    return mod


ps = _load()


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def test_load_spec_normalizes_names_and_titles():
    spec = ps.load_spec(json.dumps([{"name": "Files Drawer!"}, {"name": "files-drawer"}, {}]))
    names = [v["name"] for v in spec["views"]]
    assert names == ["files-drawer", "files-drawer-2", "view-3"]
    assert spec["views"][0]["title"] == "Files drawer"


def test_load_spec_rejects_empty_and_bad_steps():
    with pytest.raises(ps.ShotError):
        ps.load_spec('{"views": []}')
    with pytest.raises(ps.ShotError):
        ps.load_spec('{"views": [{"name": "x", "steps": "click"}]}')


def test_viewport_presets_and_custom():
    assert ps.viewport({}) == (1440, 900, False)
    assert ps.viewport({"viewport": "mobile"}) == (390, 844, True)
    assert ps.viewport({"viewport": [500, 700]}) == (500, 700, True)
    with pytest.raises(ps.ShotError):
        ps.viewport({"viewport": "watch"})


def test_markdown_side_by_side_and_new_ui():
    m = {"base": "origin/main", "base_sha": "abcdef1234", "dirty": True, "views": [
        {"title": "Files drawer", "note": "Tabs stay open.",
         "before": {"path": "/x/files-before.png"}, "after": {"path": "/x/files-after.png"}},
        {"title": "New panel", "before": None, "after": {"path": "/x/panel-after.png"}},
    ]}
    md = ps.markdown(m, {"/x/files-before.png": "https://u/b.png", "/x/files-after.png": "https://u/a.png",
                         "/x/panel-after.png": "https://u/p.png"})
    assert "## Screenshots" in md and "`abcdef1`" in md and "uncommitted" in md
    assert "### Files drawer\nTabs stay open." in md
    assert '<img src="https://u/b.png"' in md and '<img src="https://u/a.png"' in md
    assert "| _not in base_ | <img src=\"https://u/p.png\"" in md


def test_upload_writes_orphan_branch_without_touching_checkout(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "feat/thing")
    _git(work, "config", "user.name", "T")
    _git(work, "config", "user.email", "t@example.com")
    (work / "app.txt").write_text("v1\n")
    _git(work, "add", "app.txt")
    _git(work, "commit", "-qm", "init")
    _git(work, "remote", "add", "origin", str(remote))
    (work / "app.txt").write_text("v2 uncommitted\n")  # must survive the upload
    head, status = _git(work, "rev-parse", "HEAD"), _git(work, "status", "--porcelain")

    run = tmp_path / "20260101-000000"
    (run / "shots").mkdir(parents=True)
    shots = {}
    for side in ("before", "after"):
        p = run / "shots" / f"drawer-{side}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + side.encode())
        shots[side] = str(p)
    manifest = run / "manifest.json"
    manifest.write_text(json.dumps({"run_dir": str(run), "head": "feat/thing", "base": "main", "base_sha": head,
                                    "dirty": True, "views": [{"name": "drawer", "title": "Drawer",
                                    "before": {"path": shots["before"], "errors": []},
                                    "after": {"path": shots["after"], "errors": []}}]}))

    monkeypatch.setattr(ps, "ROOT", work)
    monkeypatch.setattr(ps, "_owner_repo", lambda remote: "acme/app")
    args = type("A", (), {"manifest": str(manifest), "remote": "origin", "branch": "pr-assets", "folder": ""})
    assert ps.cmd_upload(args) == 0
    # second upload stacks on top of the first (fetch → parent → push)
    assert ps.cmd_upload(args) == 0

    files = _git(remote, "ls-tree", "-r", "--name-only", "pr-assets").splitlines()
    assert "README.md" in files
    assert "feat-thing/20260101-000000/drawer-before.png" in files
    assert "feat-thing/20260101-000000/drawer-after.png" in files
    assert len(_git(remote, "rev-list", "pr-assets").splitlines()) == 2
    blob = subprocess.run(["git", "show", "pr-assets:feat-thing/20260101-000000/drawer-after.png"],
                          cwd=remote, capture_output=True, check=True).stdout
    assert blob == b"\x89PNG\r\n\x1a\nafter"

    # the checkout is exactly as it was
    assert _git(work, "rev-parse", "HEAD") == head
    assert _git(work, "status", "--porcelain") == status
    assert (work / "app.txt").read_text() == "v2 uncommitted\n"
    assert "pr-assets" not in _git(work, "branch", "--list")

    m = json.loads(manifest.read_text())
    commit = m["uploaded"]["commit"]
    assert m["uploaded"]["urls"][shots["after"]] == (
        f"https://github.com/acme/app/blob/{commit}/feat-thing/20260101-000000/drawer-after.png?raw=true")
    assert f"/blob/{commit}/" in (run / "screenshots.md").read_text()


def test_owner_repo_parses_https_and_ssh(monkeypatch):
    for url in ("https://github.com/PrajsRamteke/harness-agent.git", "git@github.com:PrajsRamteke/harness-agent.git",
                "https://github.com/PrajsRamteke/harness-agent"):
        monkeypatch.setattr(ps, "git", lambda *a, _u=url, **k: _u)
        assert ps._owner_repo("origin") == "PrajsRamteke/harness-agent"
