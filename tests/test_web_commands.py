"""Web /command: list, create, edit, rename, copy and delete custom slash commands."""
from __future__ import annotations

import json
import pathlib
import threading
import urllib.request
from types import SimpleNamespace

import pytest


@pytest.fixture()
def cmd_env(tmp_path, monkeypatch):
    """A scratch project + HOME: nothing reaches the real ~/.harness or ~/.claude."""
    import jarvis.storage.commands as cc
    import jarvis.web.commands_api as api
    from jarvis import state

    home, proj = tmp_path / "home", tmp_path / "proj"
    home.mkdir()
    proj.mkdir()
    (proj / ".git").mkdir()  # the project root
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(cc, "HARNESS_COMMANDS_DIR", home / ".harness" / "commands")
    monkeypatch.setattr(api, "HARNESS_COMMANDS_DIR", home / ".harness" / "commands")
    monkeypatch.setattr(cc, "CONFIG_DIR", home / ".config" / "harness-agent")
    monkeypatch.setattr(cc, "_find_project_root", lambda: proj)
    monkeypatch.setattr(state, "global_commands", True)
    monkeypatch.chdir(proj)
    cc.invalidate_cache()
    yield SimpleNamespace(home=home, proj=proj, global_dir=home / ".harness" / "commands",
                          project_dir=proj / ".harness" / "commands")
    cc.invalidate_cache()


@pytest.fixture()
def server(cmd_env):
    from jarvis.web.handler import WebHandler
    from jarvis.web.server import _JarvisHTTPServer

    events: list[tuple[str, dict]] = []
    bridge = SimpleNamespace(token="tok", emit=lambda kind, data: events.append((kind, data)))
    handler = type("H", (WebHandler,), {"bridge": bridge, "app": None})
    srv = _JarvisHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, payload=None):
        req = urllib.request.Request(
            base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer tok"},
            method="GET" if payload is None else "POST",
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read())

    yield SimpleNamespace(call=call, events=events)
    srv.shutdown()
    srv.server_close()


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_lists_project_and_global_commands_with_what_the_ui_needs(cmd_env, server):
    _write(cmd_env.project_dir / "review.md",
           '---\nname: review\ndescription: Review code\nargument-hint: "[file]"\n---\n\nReview $ARGUMENTS now.\n')
    _write(cmd_env.home / ".claude" / "commands" / "standup.md", "Write my standup from git log.\n")
    _write(cmd_env.global_dir / "help.md", "Shadowed by the built-in /help.\n")

    body = server.call("/api/commands")
    rows = {r["name"]: r for r in body["commands"]}
    assert [r["name"] for r in body["commands"]][0] == "review"  # project first
    review = rows["review"]
    assert review["scope"] == "project" and review["takes_args"] is True
    assert review["argument_hint"] == "[file]" and review["placeholders"] == ["$ARGUMENTS"]
    assert review["body"] == "Review $ARGUMENTS now." and review["active"] is True
    standup = rows["standup"]
    assert standup["scope"] == "global" and standup["tool"] == "claude"
    assert standup["takes_args"] is False and standup["description"] == "Write my standup from git log."
    assert rows["help"]["builtin"] is True  # a built-in always wins
    assert "model" in body["reserved"] and body["global_commands"] is True
    assert body["project_dir"].endswith(".harness/commands")


def test_global_commands_off_marks_them_hidden(cmd_env, server, monkeypatch):
    from jarvis import state

    _write(cmd_env.global_dir / "standup.md", "Standup.\n")
    monkeypatch.setattr(state, "global_commands", False)
    body = server.call("/api/commands")
    assert body["hidden_global_count"] == 1
    assert body["commands"][0]["active"] is False


def test_create_edit_rename_and_delete(cmd_env, server):
    from jarvis.storage import commands as cc

    res = server.call("/api/commands/save", {
        "name": "Explain", "description": "Explain code", "argument_hint": "<file>",
        "body": "Explain $1 briefly.", "scope": "project",
    })
    assert res["ok"] and res["created"] and res["name"] == "explain"
    path = cmd_env.project_dir / "explain.md"
    assert path.exists()
    assert server.events[-1] == ("commands", {"event": "save", "name": "explain"})
    assert [r["name"] for r in res["list"]["commands"]] == ["explain"]
    # The terminal runs what the browser made.
    assert cc.expand_command("explain", "app.py") == "Explain app.py briefly."

    # Edit in place: new text, hint cleared.
    res = server.call("/api/commands/save", {
        "name": "explain", "description": "Explain any code", "argument_hint": "",
        "body": "Explain $ARGUMENTS.", "path": str(path),
    })
    assert res["ok"] and not res["created"]
    rec = cc.find_command("explain")
    assert rec["description"] == "Explain any code" and rec["argument_hint"] == ""

    # Rename moves the file.
    res = server.call("/api/commands/save", {
        "name": "describe", "description": "Explain any code", "body": "Explain $ARGUMENTS.", "path": str(path),
    })
    assert res["ok"] and not path.exists() and (cmd_env.project_dir / "describe.md").exists()

    res = server.call("/api/commands/delete", {"name": "describe", "path": str(cmd_env.project_dir / "describe.md")})
    assert res["ok"] and res["list"]["commands"] == []
    assert not (cmd_env.project_dir / "describe.md").exists()


def test_save_refuses_bad_names_clashes_and_unknown_paths(cmd_env, server):
    _write(cmd_env.global_dir / "standup.md", "Standup.\n")

    res = server.call("/api/commands/save", {"name": "model", "body": "x"})
    assert not res["ok"] and res["field"] == "name" and "built-in" in res["error"]
    res = server.call("/api/commands/save", {"name": "bad name!", "body": "x"})
    assert not res["ok"] and res["field"] == "name"
    res = server.call("/api/commands/save", {"name": "ok", "body": "   "})
    assert not res["ok"] and res["field"] == "body"
    # A project /standup would shadow the global one.
    res = server.call("/api/commands/save", {"name": "standup", "body": "x", "scope": "project"})
    assert not res["ok"] and "already exists (global)" in res["error"]
    assert not (cmd_env.project_dir / "standup.md").exists()
    # The page may only edit files Jarvis discovered — never an arbitrary path.
    outside = cmd_env.home / "notes.md"
    _write(outside, "keep me\n")
    res = server.call("/api/commands/save", {"name": "notes", "body": "x", "path": str(outside)})
    assert not res["ok"] and outside.read_text() == "keep me\n"
    res = server.call("/api/commands/delete", {"name": "notes", "path": str(outside)})
    assert not res["ok"] and outside.exists()
    assert not any(kind == "commands" for kind, _ in server.events)  # nothing changed


def test_copy_between_scopes(cmd_env, server):
    _write(cmd_env.project_dir / "review.md", "Review it.\n")
    res = server.call("/api/commands/copy", {"name": "review", "scope": "global"})
    assert res["ok"] and (cmd_env.global_dir / "review.md").read_text() == "Review it.\n"
    res = server.call("/api/commands/copy", {"name": "review", "scope": "global"})
    assert not res["ok"] and "already in global" in res["error"]


def test_scope_switch_is_a_terminal_action(cmd_env, monkeypatch):
    from jarvis import state
    from jarvis.web.actions_api import run_web_action

    saved = []
    monkeypatch.setattr(state, "save_commands_config", lambda: saved.append(state.global_commands))
    res = run_web_action("commands_scope", {"global_commands": False}, console_print=lambda *a, **k: None)
    assert res == {"ok": True, "global_commands": False}
    assert state.global_commands is False and saved == [False]


def test_write_command_keeps_the_hint_unless_given(cmd_env):
    from jarvis.storage import commands as cc

    ok, path = cc.write_command("x", "d", "Body $1", scope="project")
    assert ok
    p = pathlib.Path(path)
    p.write_text('---\nname: x\ndescription: d\nargument-hint: "[thing]"\n---\n\nBody $1\n')
    cc.write_command("x", "d2", "Body $1", existing_path=path)  # the TUI editor: hint kept
    assert cc.find_command("x")["argument_hint"] == "[thing]"
    cc.write_command("x", "d2", "Body $1", existing_path=path, argument_hint="[a]  [b]")
    assert cc.find_command("x")["argument_hint"] == "[a] [b]"
    assert cc.placeholders_in("$2 then $ARGUMENTS and $2") == ["$2", "$ARGUMENTS"]
