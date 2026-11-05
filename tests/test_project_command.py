"""/project — the TUI side of the multi-project workspace."""
from __future__ import annotations

import asyncio
import io
import os
from types import SimpleNamespace

import pytest
from rich.console import Console


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    """An isolated workspace file, three scratch repos, a recording console."""
    from jarvis.commands import project as pc
    from jarvis.storage import workspace

    monkeypatch.setattr(workspace, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(workspace, "WORKSPACE_FILE", tmp_path / "cfg" / "workspace.json")
    monkeypatch.setattr(workspace, "LOCK_FILE", tmp_path / "cfg" / "workspace.lock")
    workspace.load(fresh=True)

    buf = io.StringIO()
    monkeypatch.setattr(pc, "console", Console(file=buf, width=220, force_terminal=False, color_system=None))
    monkeypatch.setattr(pc, "running_elsewhere", lambda _p: [])
    monkeypatch.setattr(pc, "_busy_probe", None)

    repos = {}
    for name in ("api", "web", "infra"):
        d = tmp_path / "code" / name
        (d / ".git").mkdir(parents=True)
        repos[name] = str(d.resolve())
    monkeypatch.chdir(repos["api"])
    from jarvis import constants

    monkeypatch.setattr(constants.paths, "CWD", repos["api"], raising=False)

    def run(line: str) -> str:
        buf.seek(0)
        buf.truncate()
        head, _, arg = line.partition(" ")
        assert pc.handle_project(head, arg)
        return buf.getvalue()

    return SimpleNamespace(run=run, repos=repos, pc=pc, workspace=workspace, tmp=tmp_path, monkeypatch=monkeypatch)


def ids(ws) -> list[str]:
    return [p["id"] for p in ws.workspace.projects()]


def test_add_default_current_folder_and_listing(ws):
    out = ws.run("/project add")
    assert "added api" in out
    assert ids(ws) == ["api"]
    listing = ws.run("/project list")
    assert "api" in listing and "here" in listing


def test_add_several_paths_with_errors_per_path(ws, tmp_path):
    out = ws.run(f"/project add {ws.repos['web']} {tmp_path}/nope {ws.repos['web']}")
    assert out.count("added web") == 1
    assert "doesn't exist" in out          # the missing folder
    assert "already in your workspace" in out  # the duplicate
    assert ids(ws) == ["web"]


def test_add_expands_tilde_and_relative(ws, monkeypatch, tmp_path):
    home = tmp_path / "homedir"
    (home / "proj").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    out = ws.run("/project add ~/proj ../web")
    assert "added proj" in out and "added web" in out


def test_remove_by_id_and_unknown(ws):
    ws.run(f"/project add {ws.repos['api']} {ws.repos['web']}")
    out = ws.run("/project rm web nonsense")
    assert "removed web" in out and "no project" in out
    assert ids(ws) == ["api"]
    assert ws.run("/project remove api").count("removed api") == 1


def test_link_unlink_write_need_current_in_workspace(ws):
    ws.run(f"/project add {ws.repos['web']}")  # cwd (api) is NOT in the workspace
    for line in ("/project link web", "/project unlink web", "/project write allow"):
        assert "run /project add first" in ws.run(line)


def test_link_unlink_and_write_policy(ws):
    ws.run(f"/project add {ws.repos['api']} {ws.repos['web']} {ws.repos['infra']}")
    out = ws.run("/project link web infra ghost api")
    assert "can now read web" in out and "can now read infra" in out
    assert "no project “ghost”" in out and "reads itself" in out
    api = ws.workspace.get("api")
    assert api["links"] == ["web", "infra"]
    assert "already linked" in ws.run("/project link web")
    assert "no longer links web" in ws.run("/project unlink web")
    assert ws.workspace.get("api")["links"] == ["infra"]
    assert "writes into linked projects: allow" in ws.run("/project write allow")
    assert ws.workspace.get("api")["link_write"] == "allow"
    assert "✗" in ws.run("/project write sometimes")
    assert "allow" in ws.run("/project write")


def test_rename_color_startup_and_bad_values(ws):
    ws.run(f"/project add {ws.repos['api']}")
    assert "Billing API" in ws.run("/project rename api Billing API")
    assert ws.workspace.get("api")["name"] == "Billing API"
    ws.run("/project color api #112233")
    assert ws.workspace.get("api")["color"] == "#112233"
    assert "✗" in ws.run("/project color api red")
    ws.run("/project startup api warm")
    assert ws.workspace.get("api")["startup"] == "warm"
    assert "✗" in ws.run("/project startup api sometimes")
    assert "usage" in ws.run("/project rename api")


def test_settings_show_and_set(ws):
    assert "warm_limit" in ws.run("/project settings")
    ws.run("/project settings warm_limit 5")
    ws.run("/project settings link_all on")
    s = ws.workspace.load()["settings"]
    assert s["warm_limit"] == 5 and s["link_all"] is True
    assert "unknown setting" in ws.run("/project settings nope 1")


def test_scan_lists_and_adds_all(ws, tmp_path):
    root = tmp_path / "code"
    (root / "plain-dir").mkdir()
    out = ws.run(f"/project scan {root}")
    assert "3 new" in out and "api" in out and ids(ws) == []
    ws.run(f"/project add {ws.repos['web']}")
    out = ws.run(f"/project scan {root} --all")
    assert sorted(ids(ws)) == ["api", "infra", "web"]
    assert "added api" in out and "added infra" in out and "added web" not in out


def test_scan_finds_nested_but_not_inside_repos(ws, tmp_path):
    nested = tmp_path / "mono" / "group" / "svc"
    (nested / ".git").mkdir(parents=True)
    (nested / "vendor" / "dep" / ".git").mkdir(parents=True)
    assert ws.pc.find_repos(tmp_path / "mono") == [str(nested.resolve())]


def test_switch_calls_change_cwd_and_resumes_latest_chat(ws, monkeypatch):
    ws.run(f"/project add {ws.repos['api']} {ws.repos['web']}")
    seen = {}

    def fake_change(raw):
        seen["raw"] = raw
        return {"ok": True, "cwd": raw, "display": raw}

    monkeypatch.setattr("jarvis.web.fs_api.change_cwd", fake_change)
    monkeypatch.setattr(ws.pc, "latest_session_id", lambda path: 42 if path == ws.repos["web"] else None)
    resumed = []
    monkeypatch.setattr("jarvis.commands.session._resume_session", lambda sid: resumed.append(sid))
    out = ws.run("/project web")
    assert seen["raw"] == ws.repos["web"] and resumed == [42]
    assert "project web" in out
    ws.run("/project switch 1")  # by position
    assert seen["raw"] == ws.repos["api"]


def test_switch_without_chat_starts_new_one(ws, monkeypatch):
    ws.run(f"/project add {ws.repos['web']}")
    monkeypatch.setattr("jarvis.web.fs_api.change_cwd", lambda raw: {"ok": True, "cwd": raw, "display": raw})
    monkeypatch.setattr(ws.pc, "latest_session_id", lambda path: None)
    started = []
    monkeypatch.setattr(ws.pc, "start_chat_here", lambda printer: started.append(True))
    ws.run("/project web")
    assert started == [True]


def test_switch_refused_while_busy(ws, monkeypatch):
    ws.run(f"/project add {ws.repos['web']}")
    called = []
    monkeypatch.setattr("jarvis.web.fs_api.change_cwd", lambda raw: called.append(raw))
    ws.pc.set_busy_probe(lambda: True)
    try:
        out = ws.run("/project web")
    finally:
        ws.pc.set_busy_probe(None)
    assert "busy" in out and called == []


def test_switch_unknown_missing_and_failed(ws, monkeypatch, tmp_path):
    ws.run(f"/project add {ws.repos['web']}")
    assert "no project or subcommand" in ws.run("/project ghost")
    assert "No project “ghost”" in ws.run("/project switch ghost")
    monkeypatch.setattr("jarvis.web.fs_api.change_cwd", lambda raw: {"ok": False, "error": "nope, denied"})
    assert "nope, denied" in ws.run("/project web")
    import shutil

    shutil.rmtree(ws.repos["web"])
    assert "no longer exists" in ws.run("/project web")


def test_switch_mentions_web_instance(ws, monkeypatch):
    ws.run(f"/project add {ws.repos['web']}")
    monkeypatch.setattr("jarvis.web.fs_api.change_cwd", lambda raw: {"ok": True, "cwd": raw, "display": raw})
    monkeypatch.setattr(ws.pc, "running_elsewhere", lambda p: [{"id": "abc", "pid": 1}])
    monkeypatch.setattr(ws.pc, "latest_session_id", lambda path: None)
    monkeypatch.setattr(ws.pc, "start_chat_here", lambda printer: None)
    assert "also running in the web" in ws.run("/project web")


def test_aliases_and_dispatch(ws, monkeypatch):
    from jarvis.commands import dispatch

    monkeypatch.setattr(dispatch, "handle_project", ws.pc.handle_project)
    ws.run(f"/project add {ws.repos['api']}")
    for alias in ("/workspace", "/p", "/project"):
        assert dispatch.handle_slash(f"{alias} list")[0] == "ok"
    assert ws.pc.handle_project("/other", "") is False


def test_app_routing_sniffer(ws):
    ws.run(f"/project add {ws.repos['api']} {ws.repos['web']}")
    assert ws.pc.handled_by_app("/project")
    assert ws.pc.handled_by_app("/p web")
    assert ws.pc.handled_by_app("/workspace switch web")
    assert not ws.pc.handled_by_app("/project add x")
    assert not ws.pc.handled_by_app("/project list")
    assert not ws.pc.handled_by_app("/project ghost")
    assert not ws.pc.handled_by_app("/session")


def test_catalog_help_and_reserved_names():
    from jarvis.commands.help import _SECTIONS
    from jarvis.storage.commands import _is_reserved
    from jarvis.tui.commands_catalog import COMMANDS, filter_commands

    assert any(c == "/project" for c, _ in COMMANDS)
    assert any(c.startswith("/project") for _, rows in _SECTIONS for c, _ in rows)
    assert filter_commands("/proj")
    assert _is_reserved("workspace") and _is_reserved("p") and _is_reserved("project")


def test_path_completion(tmp_path):
    from jarvis.tui.project_modal import complete_path

    (tmp_path / "alpha").mkdir()
    (tmp_path / "alps").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "file.txt").write_text("x")
    done, names = complete_path(str(tmp_path) + "/b")
    assert done == str(tmp_path) + "/beta/" and names == ["beta"]
    done, names = complete_path(str(tmp_path) + "/al")
    assert done == str(tmp_path) + "/alp" and names == ["alpha", "alps"]
    assert complete_path(str(tmp_path) + "/zzz") == (str(tmp_path) + "/zzz", [])
    done, _ = complete_path("~/")
    assert done.startswith("~/")


# ─── modal + footer (headless Textual) ───────────────────────────────────────

@pytest.fixture()
def hermetic_app(monkeypatch):
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.updater as updater

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None, raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    from jarvis.tui.app import JarvisTUI

    return JarvisTUI


def test_modal_lists_projects_and_switches_on_enter(ws, hermetic_app, monkeypatch):
    ws.run(f"/project add {ws.repos['api']} {ws.repos['web']}")
    switched = []

    async def run() -> None:
        app = hermetic_app()
        monkeypatch.setattr(app, "_switch_project", lambda ref: switched.append(ref) or True)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(0.3)
            app._open_project_modal()
            await pilot.pause(0.3)
            from textual.widgets import OptionList

            opts = app.screen.query_one("#project_list", OptionList)
            assert opts.option_count == 2
            await pilot.press("down", "enter")
            await pilot.pause(0.3)

    asyncio.run(run())
    assert switched == ["web"]


def test_modal_keys_write_policy_reorder_remove(ws, hermetic_app):
    ws.run(f"/project add {ws.repos['api']} {ws.repos['web']} {ws.repos['infra']}")

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(0.3)
            app._open_project_modal()
            await pilot.pause(0.3)
            await pilot.press("w")            # api: ask -> allow
            await pilot.pause(0.1)
            assert ws.workspace.get("api")["link_write"] == "allow"
            await pilot.press("J")            # api moves below web
            await pilot.pause(0.1)
            assert ids(ws) == ["web", "api", "infra"]
            await pilot.press("d", "y")       # remove api (confirm)
            await pilot.pause(0.2)
            assert ids(ws) == ["web", "infra"]
            await pilot.press("escape")
            await pilot.pause(0.1)

    asyncio.run(run())


def test_modal_links_dialog(ws, hermetic_app):
    ws.run(f"/project add {ws.repos['api']} {ws.repos['web']}")

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(0.3)
            app._open_project_modal()
            await pilot.pause(0.3)
            await pilot.press("l")            # links from api (highlighted: the current project)
            await pilot.pause(0.2)
            await pilot.press("space", "enter")
            await pilot.pause(0.2)
            assert ws.workspace.get("api")["links"] == ["web"]
            await pilot.press("escape")
            await pilot.pause(0.1)

    asyncio.run(run())


def test_footer_badge_only_with_two_projects(ws, hermetic_app):
    ws.run(f"/project add {ws.repos['api']}")

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause(0.3)
            assert app._project_badge() is None          # one project: no badge
            ws.run(f"/project add {ws.repos['web']}")
            badge = app._project_badge()
            assert badge and "api" in badge
            left, _right = app._footer_segments()
            assert any(seg[2] == "open_projects" for seg in left)
            os.chdir(ws.repos["web"])
            ws.monkeypatch.setattr("jarvis.constants.paths.CWD", ws.repos["web"], raising=False)
            assert "web" in app._project_badge()

    asyncio.run(run())
