"""Master groups — a virtual main project over several projects, with one shared master chat."""
from __future__ import annotations

import io
import os
import pathlib
from types import SimpleNamespace

import pytest
from rich.console import Console


@pytest.fixture()
def mg(tmp_path, monkeypatch):
    from jarvis import constants
    from jarvis.storage import workspace
    from jarvis.tools import shell

    monkeypatch.setattr(workspace, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(workspace, "WORKSPACE_FILE", tmp_path / "cfg" / "workspace.json")
    monkeypatch.setattr(workspace, "LOCK_FILE", tmp_path / "cfg" / "workspace.lock")
    workspace.load(fresh=True)
    repos = {}
    for name in ("a", "b", "c"):
        d = tmp_path / "code" / name
        (d / ".git").mkdir(parents=True)
        (d / "src").mkdir()
        (d / "src" / "x.ts").write_text(f"export const who = '{name}'\n")
        repos[name] = str(d.resolve())
        workspace.add(repos[name], name=name)
    group = workspace.create_group("Habuild", ["a", "b", "c"])
    answers: list[str] = []
    monkeypatch.setattr(shell, "ask_linked_write", lambda *a, **k: answers.pop(0) if answers else "n")
    yield SimpleNamespace(ws=workspace, repos=repos, group=group, hub=group["path"], answers=answers,
                          tmp=tmp_path, mp=monkeypatch)
    constants.set_cwd(os.getcwd())


def enter(mg, path):
    from jarvis import constants

    os.chdir(path)
    constants.set_cwd(path)


# ─── store ───────────────────────────────────────────────────────────────────

def test_create_group_makes_hub_and_links_members(mg):
    g = mg.group
    assert g["kind"] == "group" and g["links"] == ["a", "b", "c"] and os.path.isdir(g["path"])
    notes = pathlib.Path(g["path"], "PROJECTS.md").read_text()
    assert all(mg.repos[n] in notes for n in "abc")
    assert [x["id"] for x in mg.ws.groups_of("a")] == [g["id"]]


def test_group_validation(mg):
    with pytest.raises(mg.ws.WorkspaceError):
        mg.ws.create_group("  ", ["a"])
    with pytest.raises(mg.ws.WorkspaceError):
        mg.ws.create_group("x", [])
    with pytest.raises(mg.ws.WorkspaceError):
        mg.ws.create_group("x", ["nope"])
    with pytest.raises(mg.ws.WorkspaceError):  # a group is not a member of another group
        mg.ws.create_group("x", [mg.group["id"]])
    with pytest.raises(mg.ws.WorkspaceError):  # nor a link of a project
        mg.ws.update("a", links=[mg.group["id"]])


def test_group_policy_covers_all_members_and_removed_member_is_pruned(mg):
    mg.ws.update(mg.group["id"], link_write="deny")
    roots = {r["id"]: r for r in mg.ws.roots_for(mg.group["id"])}
    assert roots[mg.group["id"]]["primary"] and {roots[n]["write"] for n in "abc"} == {"deny"}
    mg.ws.remove("b")
    assert mg.ws.get(mg.group["id"])["links"] == ["a", "c"]
    notes = pathlib.Path(mg.hub, "PROJECTS.md").read_text()
    assert "| b | b |" not in notes and "| a | a |" in notes


def test_link_all_never_adds_groups_to_plain_projects(mg):
    mg.ws.update_settings(link_all=True)
    assert [p["id"] for p in mg.ws.links_of(mg.ws.get("a"))] == ["b", "c"]


def test_master_session_roundtrip(mg):
    mg.ws.set_master_session(mg.group["id"], 42)
    assert mg.ws.get(mg.group["id"])["master_session"] == 42
    mg.ws.set_master_session("a", 7)  # not a group: ignored
    assert mg.ws.get("a")["master_session"] is None


# ─── web state / api ─────────────────────────────────────────────────────────

def test_master_fields_for_member_and_hub(mg):
    from jarvis.web import master

    mg.ws.set_master_session(mg.group["id"], 9)
    member = master.fields(mg.repos["a"])
    assert member["master"] is None and member["masters"][0]["id"] == mg.group["id"]
    assert member["masters"][0]["session_id"] == 9
    hub = master.fields(mg.hub)
    assert hub["master"]["id"] == mg.group["id"] and [m["id"] for m in hub["master"]["members"]] == ["a", "b", "c"]
    assert hub["masters"] == []


def test_adopt_first_tab_session_as_master(mg):
    from jarvis import tabs
    from jarvis.web import master

    enter(mg, mg.hub)
    tab = SimpleNamespace(session_id=None)
    mg.mp.setattr(tabs, "all_tabs", lambda: [tab])
    assert master.adopt(mg.hub) is False  # no saved chat yet
    tab.session_id = 15
    assert master.adopt(mg.hub) is True
    assert mg.ws.get(mg.group["id"])["master_session"] == 15
    tab.session_id = 99
    assert master.adopt(mg.hub) is False  # already has a master chat: never replaced


def test_workspace_rows_and_create_group_route(mg):
    from jarvis.web import registry, workspace_api

    mg.mp.setattr(registry, "list_instances", lambda: [])
    rows = {r["id"]: r for r in workspace_api.project_rows(None)}
    g = rows[mg.group["id"]]
    assert g["kind"] == "group" and g["members"] == ["a", "b", "c"] and g["masters"] == []
    assert rows["a"]["kind"] == "project" and rows["a"]["masters"][0]["id"] == mg.group["id"]

    out = workspace_api.post("create_group", {"name": "Two", "members": ["a", "b"]}, direct=True, self_id=None)
    assert out["ok"] and out["project"]["links"] == ["a", "b"]
    remote = workspace_api.post("create_group", {"name": "Three", "members": ["a"]}, direct=False, self_id=None)
    assert remote["ok"] is False and remote["code"] == "remote"
    bad = workspace_api.post("create_group", {"name": "Four", "members": []}, direct=True, self_id=None)
    assert bad["ok"] is False and bad["code"] == "bad_value"


def test_open_group_resumes_master_chat(mg):
    from jarvis.web import launcher, registry, workspace_api

    mg.mp.setattr(registry, "list_instances", lambda: [])
    mg.ws.set_master_session(mg.group["id"], 33)
    seen = {}
    mg.mp.setattr(launcher, "open_project", lambda path, **kw: seen.update(path=path, **kw) or {"ok": True})
    workspace_api.post("open", {"id": mg.group["id"]}, direct=True, self_id=None)
    assert seen["resume_session"] == 33 and seen["reuse"] is True and seen["path"] == mg.hub


# ─── what the master agent sees ──────────────────────────────────────────────

def test_list_projects_says_three(mg):
    from jarvis.tools import workspace_tools as wt

    enter(mg, mg.hub)
    assert wt.exposed() is True
    out = wt.list_projects()
    assert "MASTER of the group 'Habuild'" in out and "access to 3 projects" in out
    for n in "abc":
        assert f"{n} —" in out and mg.repos[n] in out
    assert "ask before edit" in out


def test_list_projects_in_member_and_hidden_without_links(mg):
    from jarvis.tools import workspace_tools as wt

    enter(mg, mg.repos["a"])
    assert wt.exposed() is False  # a member with no links of its own: nothing to list, no tokens
    assert "access to 0 linked projects" in wt.list_projects()
    mg.ws.update("a", links=["b"])
    assert wt.exposed() is True and "access to 1 linked project besides" in wt.list_projects()
    enter(mg, str(mg.tmp))
    assert wt.exposed() is False and "isn't in the user's workspace" in wt.list_projects()


def test_system_prompt_names_the_master_group(mg):
    from jarvis.constants.system_prompt import build_base_system, linked_projects_block

    block = linked_projects_block(mg.hub)
    assert block.startswith("MASTER GROUP 'Habuild'") and "list_projects" in block
    assert all(f"{n} ->" in block for n in "abc")
    assert "MASTER GROUP" not in linked_projects_block(mg.repos["a"]) or linked_projects_block(mg.repos["a"]) == ""
    assert "LINKED PROJECTS" in build_base_system(pathlib.Path(mg.hub))


def test_master_reads_members_and_writes_follow_group_policy(mg):
    from jarvis.tools.files import read_file, write_file

    enter(mg, mg.hub)
    assert "who = 'b'" in read_file("b:src/x.ts")
    target = os.path.join(mg.repos["c"], "src", "new.ts")
    mg.ws.update(mg.group["id"], link_write="deny")
    assert write_file("c:src/new.ts", "1").startswith("ERROR") and not os.path.exists(target)
    mg.ws.update(mg.group["id"], link_write="ask")
    mg.answers.append("y")
    assert not write_file("c:src/new.ts", "1").startswith("ERROR") and os.path.exists(target)


def test_list_projects_registered_as_a_tool(mg):
    from jarvis import tools

    assert "list_projects" in tools.TOOL_GROUPS["workspace"][0]["name"]
    assert "list_projects" in getattr(tools, "TOOL_HANDLERS", {}) or any(
        t["name"] == "list_projects" for t in tools.TOOLS)


# ─── TUI ─────────────────────────────────────────────────────────────────────

def test_project_group_and_master_commands(mg):
    from jarvis.commands import project as pc

    buf = io.StringIO()
    mg.mp.setattr(pc, "console", Console(file=buf, width=200, force_terminal=False, color_system=None))
    mg.mp.setattr(pc, "running_elsewhere", lambda _p: [])
    assert pc.handle_project("/project", "group Duo a b")
    assert mg.ws.get("duo")["links"] == ["a", "b"] and "group Duo" in buf.getvalue()
    buf.truncate(0)
    pc.handle_project("/project", "group onlyname")
    assert "usage: /project group" in buf.getvalue()
    buf.truncate(0)
    pc.handle_project("/project", "master nope")
    assert "no master group" in buf.getvalue()
    buf.truncate(0)
    pc.handle_project("/project", "list")
    assert "◉" in buf.getvalue()


def test_watcher_adopts_master_chat_with_no_page_open(mg):
    from jarvis import tabs
    from jarvis.web.sync import StateWatcher

    enter(mg, mg.hub)
    mg.mp.setattr(tabs, "all_tabs", lambda: [SimpleNamespace(session_id=21)])
    bridge = SimpleNamespace(has_subscribers=lambda: False, emit=lambda *a, **k: None)
    StateWatcher(bridge, busy=lambda: False).tick()
    assert mg.ws.get(mg.group["id"])["master_session"] == 21
