"""ask_project — hand a task to another linked project's Jarvis (guards + happy path, no real processes)."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest


@pytest.fixture()
def dg(tmp_path, monkeypatch):
    from jarvis import constants, state
    from jarvis.storage import workspace
    from jarvis.tools import project_delegate as pd
    from jarvis.web import instance_client

    monkeypatch.setattr(workspace, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(workspace, "WORKSPACE_FILE", tmp_path / "cfg" / "workspace.json")
    monkeypatch.setattr(workspace, "LOCK_FILE", tmp_path / "cfg" / "workspace.lock")
    workspace.load(fresh=True)
    repos = {}
    for name in ("api", "web", "infra"):
        d = tmp_path / "code" / name
        (d / ".git").mkdir(parents=True)
        repos[name] = str(d.resolve())
        workspace.add(repos[name], name=name)
    workspace.update("api", links=["web"])
    monkeypatch.chdir(repos["api"])
    constants.set_cwd(repos["api"])
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "update the client"}])
    monkeypatch.delenv(pd.DELEGATED_ENV, raising=False)
    yield SimpleNamespace(pd=pd, ic=instance_client, ws=workspace, repos=repos, state=state, mp=monkeypatch)
    constants.set_cwd(os.getcwd())


def test_hidden_until_enabled_and_linked(dg):
    assert dg.pd.exposed() is False            # setting off
    dg.ws.update_settings(delegation=True)
    assert dg.pd.exposed() is True
    dg.ws.update("api", links=[])
    assert dg.pd.exposed() is False            # nobody to ask


def test_off_message(dg):
    assert dg.pd.ask_project(project="web", task="do it") == dg.pd.OFF_MESSAGE


def test_empty_task_and_unlinked_target(dg):
    dg.ws.update_settings(delegation=True)
    assert "empty" in dg.pd.ask_project(project="web", task=" ")
    out = dg.pd.ask_project(project="infra", task="do it")
    assert out.startswith("ERROR") and "not linked" in out
    assert dg.pd.ask_project(project="api", task="do it").startswith("ERROR")
    assert "No workspace project" in dg.pd.ask_project(project="zzz", task="do it")


def test_delegated_work_cannot_delegate_again(dg):
    dg.ws.update_settings(delegation=True)
    dg.state.messages = [{"role": "user", "content": f"{dg.pd.PREFIX}api: fix the DTO"}]
    out = dg.pd.ask_project(project="web", task="do it")
    assert out.startswith("ERROR") and "can't delegate again" in out
    dg.state.messages = [{"role": "user", "content": "hi"}]
    dg.mp.setenv(dg.pd.DELEGATED_ENV, "1")
    assert dg.pd.is_delegated()


def test_no_wait_starts_and_returns(dg):
    dg.ws.update_settings(delegation=True)
    instance_client = dg.ic
    sent = {}
    dg.mp.setattr(dg.pd, "_ensure_running", lambda proj: ("iid-1", ""))
    dg.mp.setattr(dg.pd, "_open_tab", lambda iid: (7, 0, ""))
    dg.mp.setattr(instance_client, "post_json", lambda iid, path, body=None, **k: sent.update(iid=iid, path=path, body=body) or {"ok": True})
    out = dg.pd.ask_project(project="web", task="add /orders to the client", wait=False)
    assert "tab 7" in out and not out.startswith("ERROR")
    assert sent["iid"] == "iid-1" and sent["path"] == "/api/prompt"
    assert sent["body"]["text"].startswith(f"{dg.pd.PREFIX}api: ") and "add /orders" in sent["body"]["text"]


def test_rejected_prompt_reports_error(dg):
    instance_client = dg.ic
    dg.ws.update_settings(delegation=True)
    dg.mp.setattr(dg.pd, "_ensure_running", lambda proj: ("iid-1", ""))
    dg.mp.setattr(dg.pd, "_open_tab", lambda iid: (7, 0, ""))
    dg.mp.setattr(instance_client, "post_json", lambda *a, **k: {"ok": False, "error": "busy"})
    out = dg.pd.ask_project(project="web", task="x", wait=False)
    assert out.startswith("ERROR") and "busy" in out
