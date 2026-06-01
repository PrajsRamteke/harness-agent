"""Manual /upgrade command behavior."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from jarvis.install_sync import SyncResult


def test_cmd_upgrade_succeeds_after_managed_reset(monkeypatch):
    from jarvis.commands import upgrade as upgrade_mod

    root = upgrade_mod.pathlib.Path("/Users/prajwal/.local/share/harness-agent")
    printed: list[str] = []

    monkeypatch.setattr(upgrade_mod, "find_install_root", lambda: root)
    monkeypatch.setattr(
        upgrade_mod,
        "console",
        SimpleNamespace(print=lambda msg, *a, **k: printed.append(str(msg))),
    )
    monkeypatch.setattr(
        upgrade_mod,
        "sync_repo_to_remote",
        lambda repo, **kwargs: SyncResult(
            ok=True,
            branch="main",
            discarded_local=("jarvis/constants/providers.py", "tests/test_oauth.py"),
            method="reset",
        ),
    )
    monkeypatch.setattr(upgrade_mod, "pip_install_repo", lambda *_a, **_k: True)
    reexec_called = {"n": 0}
    monkeypatch.setattr(
        upgrade_mod,
        "reexec_jarvis",
        lambda **kwargs: reexec_called.__setitem__("n", reexec_called["n"] + 1),
    )
    monkeypatch.setattr(upgrade_mod, "VERSION", "1.0.0")

    models_file = root / "jarvis" / "constants" / "models.py"
    with mock.patch.object(
        upgrade_mod.pathlib.Path,
        "is_file",
        autospec=True,
        side_effect=lambda self: self == models_file,
    ), mock.patch.object(
        upgrade_mod.pathlib.Path,
        "read_text",
        autospec=True,
        return_value='VERSION = "1.1.0"\n',
    ):
        assert upgrade_mod.cmd_upgrade("") is True

    assert reexec_called["n"] == 1
    assert any("Local edits discarded" in line for line in printed)
    assert any("Upgrade complete" in line for line in printed)


def test_cmd_upgrade_aborts_on_dirty_dev_clone(monkeypatch, tmp_path):
    from jarvis.commands import upgrade as upgrade_mod

    (tmp_path / ".git").mkdir()
    printed: list[str] = []

    monkeypatch.setattr(upgrade_mod, "find_install_root", lambda: tmp_path)
    monkeypatch.setattr(
        upgrade_mod,
        "console",
        SimpleNamespace(print=lambda msg, *a, **k: printed.append(str(msg))),
    )
    monkeypatch.setattr(
        upgrade_mod,
        "sync_repo_to_remote",
        lambda repo, **kwargs: SyncResult(
            ok=False,
            error="uncommitted local changes: jarvis/constants/providers.py",
        ),
    )
    pip_called = {"n": 0}
    monkeypatch.setattr(
        upgrade_mod,
        "pip_install_repo",
        lambda *_a, **_k: pip_called.__setitem__("n", pip_called["n"] + 1) or True,
    )

    assert upgrade_mod.cmd_upgrade("") is True
    assert pip_called["n"] == 0
    assert any("uncommitted local changes" in line for line in printed)
