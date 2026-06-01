"""Install sync helpers."""
import pathlib

from jarvis.install_sync import (
    MANAGED_INSTALL_DIR,
    find_install_root,
    harness_agent_models_available,
    is_managed_install,
    sync_repo_to_remote,
)


def test_find_install_root_from_dev_tree():
    root = find_install_root()
    assert root is not None
    assert (root / "jarvis" / "cli.py").is_file()
    assert (root / "pyproject.toml").is_file()


def test_harness_agent_models_available():
    assert harness_agent_models_available() is True


def test_is_managed_install():
    assert is_managed_install(MANAGED_INSTALL_DIR) is True
    assert is_managed_install(pathlib.Path("/tmp/harness-dev")) is False


def test_sync_repo_to_remote_blocks_dirty_dev_clone(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        "jarvis.install_sync.is_managed_install",
        lambda _root: False,
    )
    monkeypatch.setattr(
        "jarvis.install_sync._git_run",
        lambda root, args, **kwargs: (
            (0, "", "")
            if args[:2] == ["fetch", "origin"]
            else (0, "main", "")
            if args[:2] == ["rev-parse", "--abbrev-ref"]
            else (0, "", "")
        ),
    )
    monkeypatch.setattr(
        "jarvis.install_sync.git_dirty_files",
        lambda _root: ["jarvis/constants/providers.py"],
    )

    result = sync_repo_to_remote(tmp_path)
    assert result.ok is False
    assert "uncommitted local changes" in result.error


def test_sync_repo_to_remote_resets_dirty_managed_install(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    calls: list[list[str]] = []

    def fake_git(root, args, **kwargs):
        calls.append(args)
        if args[:2] == ["fetch", "origin"]:
            return 0, "", ""
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return 0, "main", ""
        if args[:2] == ["reset", "--hard"]:
            return 0, "HEAD is now at cf7508f", ""
        return 1, "", "unexpected"

    monkeypatch.setattr("jarvis.install_sync.is_managed_install", lambda _root: True)
    monkeypatch.setattr("jarvis.install_sync._git_run", fake_git)
    monkeypatch.setattr(
        "jarvis.install_sync.git_dirty_files",
        lambda _root: ["jarvis/constants/providers.py", "tests/test_oauth.py"],
    )

    result = sync_repo_to_remote(tmp_path)
    assert result.ok is True
    assert result.method == "reset"
    assert result.discarded_local == (
        "jarvis/constants/providers.py",
        "tests/test_oauth.py",
    )
    assert any(args[:2] == ["reset", "--hard"] for args in calls)


def test_sync_repo_to_remote_managed_install_always_resets(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    calls: list[list[str]] = []

    def fake_git(root, args, **kwargs):
        calls.append(args)
        if args[:2] == ["fetch", "origin"]:
            return 0, "", ""
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return 0, "main", ""
        if args[:2] == ["reset", "--hard"]:
            return 0, "HEAD is now at cf7508f", ""
        return 1, "", "unexpected"

    monkeypatch.setattr("jarvis.install_sync.is_managed_install", lambda _root: True)
    monkeypatch.setattr("jarvis.install_sync._git_run", fake_git)
    monkeypatch.setattr("jarvis.install_sync.git_dirty_files", lambda _root: [])

    result = sync_repo_to_remote(tmp_path)
    assert result.ok is True
    assert result.method == "reset"
    assert result.discarded_local == ()
    assert not any(args[:3] == ["pull", "--ff-only", "origin"] for args in calls)


def test_sync_repo_to_remote_pulls_clean_tree(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    calls: list[list[str]] = []

    def fake_git(root, args, **kwargs):
        calls.append(args)
        if args[:2] == ["fetch", "origin"]:
            return 0, "", ""
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return 0, "main", ""
        if args[:3] == ["pull", "--ff-only", "origin"]:
            return 0, "Already up to date.", ""
        return 1, "", "unexpected"

    monkeypatch.setattr("jarvis.install_sync.is_managed_install", lambda _root: False)
    monkeypatch.setattr("jarvis.install_sync._git_run", fake_git)
    monkeypatch.setattr("jarvis.install_sync.git_dirty_files", lambda _root: [])

    result = sync_repo_to_remote(tmp_path)
    assert result.ok is True
    assert result.method == "pull"
    assert result.discarded_local == ()
    assert any(args[:3] == ["pull", "--ff-only", "origin"] for args in calls)

