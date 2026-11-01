"""`jw` / `jarvis web`: open Jarvis in the browser with no terminal to keep open."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from jarvis import install_sync
from jarvis.web import fs_api, launcher, quick, registry

_FAKE = r'''
import os, sys, time, signal
sys.path.insert(0, {root!r})
from jarvis.web import registry
registry.INSTANCES_DIR = __import__("pathlib").Path(os.environ["FAKE_INSTANCES"])
if os.environ.get("FAKE_MODE") == "crash":
    print("RuntimeError: no provider configured", flush=True)
    sys.exit(3)
reg = registry.Registration(instance_id=str(os.getpid()), port=4321, token="tok",
                            info=lambda: {{"cwd": os.getcwd(), "session_id": 1,
                                          "session_title": "hello", "headless": True}})
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
reg.start()
while True:
    time.sleep(0.1)
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    inst = tmp_path / "instances"
    monkeypatch.setattr(registry, "INSTANCES_DIR", inst)
    monkeypatch.setattr(launcher, "LOG_DIR", inst / "logs")
    monkeypatch.setattr(fs_api, "RECENT_FILE", tmp_path / "recent_dirs.json")
    monkeypatch.setenv("FAKE_INSTANCES", str(inst))
    script = tmp_path / "fake_jarvis.py"
    script.write_text(_FAKE.format(root=str(Path(__file__).resolve().parents[1])))
    monkeypatch.setattr(launcher, "command", lambda: [sys.executable, str(script)])
    opened: list[str] = []
    monkeypatch.setattr(quick, "_open_browser", lambda url: opened.append(url) or True)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    yield {"proj": proj, "opened": opened}
    for rec in registry.list_instances():
        if rec.get("headless"):
            try:
                os.kill(int(rec["pid"]), 15)
            except OSError:
                pass


def _headless_in(folder: Path) -> list[dict]:
    real = os.path.realpath(folder)
    return [r for r in registry.list_instances()
            if r.get("headless") and os.path.realpath(r["cwd"]) == real]


def test_jw_starts_a_jarvis_here_and_opens_its_link(env, capsys):
    assert quick.run([]) == 0
    [rec] = _headless_in(env["proj"])
    assert env["opened"] == ["http://127.0.0.1:4321/?token=tok"]
    out = capsys.readouterr().out
    assert "Jarvis started in" in out and "127.0.0.1:4321" in out
    # Running it again opens the same Jarvis instead of a second one…
    assert quick.run([]) == 0
    assert len(_headless_in(env["proj"])) == 1 and len(env["opened"]) == 2
    assert "already runs" in capsys.readouterr().out
    # …unless --new asks for one.
    assert quick.run(["--new", "--no-open"]) == 0
    assert len(_headless_in(env["proj"])) == 2 and len(env["opened"]) == 2


def test_jw_takes_a_folder_and_refuses_bad_ones(env, capsys):
    other = env["proj"].parent / "other"
    other.mkdir()
    assert quick.run(["../other", "--no-open"]) == 0
    assert len(_headless_in(other)) == 1 and not _headless_in(env["proj"])
    assert quick.run(["../missing"]) == 2
    assert "doesn't exist" in capsys.readouterr().err


def test_jw_reuses_a_terminal_jarvis_in_the_folder(env, capsys):
    reg = registry.Registration(instance_id="term", port=8765, token="tt",
                                info=lambda: {"cwd": str(env["proj"]), "headless": False})
    reg.start()
    try:
        assert quick.run([]) == 0
        assert env["opened"] == ["http://127.0.0.1:8765/?token=tt"]
        assert "(terminal)" in capsys.readouterr().out
        assert not _headless_in(env["proj"])
        # stop never touches a terminal Jarvis
        assert quick.run(["stop"]) == 0
        assert "quit it there" in capsys.readouterr().out
        assert registry.get("term") is not None
    finally:
        reg.stop()


def test_jw_stop_and_ls(env, capsys):
    assert quick.run(["--no-open"]) == 0
    assert quick.run(["ls"]) == 0
    out = capsys.readouterr().out
    assert "browser-only" in out and "hello" in out
    assert quick.run(["stop"]) == 0
    assert "stopped" in capsys.readouterr().out
    assert not _headless_in(env["proj"])
    assert quick.run(["stop"]) == 0
    assert "No browser-only Jarvis" in capsys.readouterr().out
    assert quick.run(["ls"]) == 0
    assert "No Jarvis web remote" in capsys.readouterr().out


def test_jw_stop_all(env):
    other = env["proj"].parent / "b"
    other.mkdir()
    assert quick.run(["--no-open"]) == 0 and quick.run([str(other), "--no-open"]) == 0
    assert quick.run(["stop", "--all"]) == 0
    deadline = time.time() + 5
    while registry.list_instances() and time.time() < deadline:
        time.sleep(0.1)
    assert registry.list_instances() == []


def test_jw_says_why_a_start_failed(env, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODE", "crash")
    assert quick.run([]) == 1
    assert "no provider configured" in capsys.readouterr().err
    assert env["opened"] == []


def test_jarvis_web_is_the_same_command(monkeypatch):
    from jarvis import cli

    seen: list[list[str]] = []
    monkeypatch.setattr(quick, "main", lambda: seen.append(sys.argv[1:]))
    monkeypatch.setattr(sys, "argv", ["jarvis", "web", "stop", "--all"])
    monkeypatch.setattr(quick, "PROG", quick.PROG)  # restored after the test
    cli.main()
    assert seen == [["stop", "--all"]] and quick.PROG == "jarvis web"


def test_update_links_jw_beside_the_jarvis_link(tmp_path, monkeypatch):
    venv_bin = tmp_path / "venv" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    for name in ("python", "jarvis", "jw"):
        (venv_bin / name).write_text("")
    path_dir = tmp_path / "bin"
    path_dir.mkdir()
    (path_dir / "jarvis").symlink_to(venv_bin / "jarvis")
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "jarvis").symlink_to(venv_bin / "jarvis")
    (taken / "jw").write_text("someone else's jw")
    monkeypatch.setattr(install_sync, "running_python", lambda: str(venv_bin / "python"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(path_dir), str(taken)]))
    made = install_sync.link_short_commands()
    assert made == [path_dir / "jw"] and (path_dir / "jw").resolve() == (venv_bin / "jw").resolve()
    assert (taken / "jw").read_text() == "someone else's jw"  # never replaced
    assert install_sync.link_short_commands() == []  # already there
