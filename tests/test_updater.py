"""Background auto-update behavior."""
from __future__ import annotations

import pathlib

from jarvis.install_sync import SyncResult
from jarvis.updater import check_and_update


def test_check_and_update_succeeds_when_sync_resets_dirty_managed_install(
    monkeypatch, tmp_path,
):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()

    monkeypatch.setattr("jarvis.updater.find_install_root", lambda: root)
    monkeypatch.setattr("jarvis.updater.pip_install_repo", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "jarvis.updater.harness_agent_models_available",
        lambda: True,
    )

    def fake_git(*args, cwd, timeout=30):
        cmd = list(args)
        if cmd == ["rev-parse", "HEAD"]:
            return 0, "old123"
        if cmd == ["fetch", "origin"]:
            return 0, ""
        if cmd == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return 1, ""
        if cmd == ["rev-parse", "origin/main"]:
            return 0, "new456"
        if cmd == ["rev-list", "--count", "HEAD..origin/main"]:
            return 0, "2"
        if cmd == ["log", "--oneline", "HEAD..origin/main"]:
            return 0, "abc123 feat: opus 4.8\ndef456 fix: upgrade"
        return 1, ""

    monkeypatch.setattr("jarvis.updater._git", fake_git)
    monkeypatch.setattr(
        "jarvis.updater.sync_repo_to_remote",
        lambda repo, **kwargs: SyncResult(
            ok=True,
            branch="main",
            discarded_local=("jarvis/constants/providers.py",),
            method="reset",
        ),
    )

    result = check_and_update()
    assert result is not None
    assert result["updated"] is True
    assert result["count"] == 2
    assert result["pip_ok"] is True
    assert len(result["commits"]) == 2


def test_check_and_update_aborts_when_sync_fails_on_dirty_dev_clone(
    monkeypatch, tmp_path,
):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()

    monkeypatch.setattr("jarvis.updater.find_install_root", lambda: root)
    pip_called = {"n": 0}

    def _pip(*_a, **_k):
        pip_called["n"] += 1
        return True

    monkeypatch.setattr("jarvis.updater.pip_install_repo", _pip)

    def fake_git(*args, cwd, timeout=30):
        cmd = list(args)
        if cmd == ["rev-parse", "HEAD"]:
            return 0, "old123"
        if cmd == ["fetch", "origin"]:
            return 0, ""
        if cmd == ["rev-parse", "origin/main"]:
            return 0, "new456"
        if cmd == ["rev-list", "--count", "HEAD..origin/main"]:
            return 0, "1"
        if cmd == ["log", "--oneline", "HEAD..origin/main"]:
            return 0, "abc123 feat"
        return 1, ""

    monkeypatch.setattr("jarvis.updater._git", fake_git)
    monkeypatch.setattr(
        "jarvis.updater.sync_repo_to_remote",
        lambda repo, **kwargs: SyncResult(
            ok=False,
            error="uncommitted local changes: jarvis/constants/providers.py",
        ),
    )

    result = check_and_update()
    assert result is None
    assert pip_called["n"] == 0
