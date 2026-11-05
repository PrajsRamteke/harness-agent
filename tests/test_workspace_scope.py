"""Multi-project workspace — what the agent's tools may reach through links."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest


@pytest.fixture()
def scope(tmp_path, monkeypatch):
    """Isolated workspace with `api` (current), `web` and `infra` repos; api links web only."""
    from jarvis import constants
    from jarvis.storage import workspace
    from jarvis.tools import shell

    monkeypatch.setattr(workspace, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(workspace, "WORKSPACE_FILE", tmp_path / "cfg" / "workspace.json")
    monkeypatch.setattr(workspace, "LOCK_FILE", tmp_path / "cfg" / "workspace.lock")
    workspace.load(fresh=True)

    repos = {}
    for name in ("api", "web", "infra"):
        d = tmp_path / "code" / name
        (d / ".git").mkdir(parents=True)
        (d / "src").mkdir()
        (d / "src" / "x.ts").write_text(f"export const who = '{name}'\n")
        (d / "CLAUDE.md").write_text(f"# {name}\n")
        repos[name] = str(d.resolve())
        workspace.add(repos[name], name=name)
    workspace.update("api", links=["web"])
    workspace.update("web", link_write="ask")

    monkeypatch.chdir(repos["api"])
    constants.set_cwd(repos["api"])
    from jarvis import path_resolve

    path_resolve.reset_linked_approvals()
    answers: list[str] = []
    monkeypatch.setattr(shell, "ask_linked_write", lambda *a, **k: answers.pop(0) if answers else "n")
    yield SimpleNamespace(repos=repos, workspace=workspace, answers=answers, tmp=tmp_path, mp=monkeypatch)
    constants.set_cwd(os.getcwd())


def test_linked_read_allowed(scope):
    from jarvis.tools.files import read_file

    out = read_file("web:src/x.ts")
    assert "who = 'web'" in out and not out.startswith("ERROR")


def test_unlinked_known_project_is_an_error(scope):
    from jarvis.tools.files import read_file

    out = read_file("infra:src/x.ts")
    assert out.startswith("ERROR") and "isn't linked" in out


@pytest.mark.parametrize("ref", ["nope:src/x.ts", "C:foo", "http://example.com/x"])
def test_unknown_ids_and_urls_are_left_alone(scope, ref):
    from jarvis import path_resolve

    assert path_resolve.split_ref(ref) is None
    assert path_resolve.resolve_project_path(ref) is None


def test_resolve_project_path(scope):
    from jarvis import path_resolve

    p = path_resolve.resolve_project_path("web:src/x.ts")
    assert str(p) == os.path.join(scope.repos["web"], "src", "x.ts")


def test_write_policies(scope):
    from jarvis.tools.files import write_file

    target = os.path.join(scope.repos["web"], "src", "new.ts")

    scope.workspace.update("web", link_write="deny")
    assert "read-only" in write_file("web:src/new.ts", "1")
    assert not os.path.exists(target)

    scope.workspace.update("web", link_write="ask")
    scope.answers.append("n")
    assert write_file("web:src/new.ts", "1").startswith("ERROR")
    assert not os.path.exists(target)
    scope.answers.append("y")
    assert not write_file("web:src/new.ts", "1").startswith("ERROR")
    assert os.path.exists(target)

    os.remove(target)
    scope.workspace.update("web", link_write="allow")
    assert not write_file("web:src/new.ts", "2").startswith("ERROR")
    assert open(target).read() == "2"


def test_ask_always_remembers_project(scope):
    from jarvis.tools.files import write_file

    scope.workspace.update("web", link_write="ask")
    scope.answers.append("a")  # only one answer available: the second write must not ask again
    assert not write_file("web:src/a.ts", "1").startswith("ERROR")
    assert not write_file("web:src/b.ts", "1").startswith("ERROR")


def test_symlink_escape_out_of_linked_root_refused(scope):
    from jarvis.tools.files import read_file

    secret = scope.tmp / "secret.txt"
    secret.write_text("top secret")
    os.symlink(secret, os.path.join(scope.repos["web"], "link.txt"))
    out = read_file("web:link.txt")
    assert "top secret" not in out and out.startswith("ERROR")


def test_env_file_in_linked_root_refused(scope):
    from jarvis.tools.files import read_file

    open(os.path.join(scope.repos["web"], ".env"), "w").write("KEY=1")
    out = read_file("web:.env")
    assert out.startswith("ERROR") and "KEY=1" not in out


def test_search_code_all_projects_prefixes_ids(scope):
    from jarvis.tools.search import search_code

    out = search_code("who", project="*")
    assert "web:" in out and "who = 'web'" in out
    assert "infra" not in out


def test_run_bash_in_linked_project(scope):
    from jarvis.tools.shell import run_bash

    assert os.path.basename(scope.repos["web"]) in run_bash("pwd", project="web")


def test_run_bash_deny_refuses_mutating_commands(scope):
    from jarvis.tools.shell import run_bash

    scope.workspace.update("web", link_write="deny")
    out = run_bash("touch made.txt", project="web")
    assert out.startswith("ERROR") and "read-only" in out
    assert not os.path.exists(os.path.join(scope.repos["web"], "made.txt"))


def test_system_prompt_block_only_with_links(scope):
    from jarvis.constants.system_prompt import build_base_system, linked_projects_block

    text = build_base_system(__import__("pathlib").Path(scope.repos["api"]))
    assert "LINKED PROJECTS: web ->" in text and "[ask before edit]" in text
    assert "web:CLAUDE.md" in text and "infra" not in text.split("LINKED PROJECTS:")[1].split("\n")[0]
    # a project without links, and a folder outside the workspace, add nothing
    assert linked_projects_block(scope.repos["infra"]) == ""
    assert linked_projects_block(str(scope.tmp)) == ""
    assert "LINKED PROJECTS" not in build_base_system(__import__("pathlib").Path(scope.repos["infra"]))


def test_prompt_ref_expands_linked_file(scope):
    from jarvis.prompt_refs import expand_file_refs

    expanded, used = expand_file_refs("look at @web:src/x.ts please")
    assert "who = 'web'" in expanded and used


def test_prompt_ref_bad_project_stays_text(scope):
    from jarvis.prompt_refs import expand_file_refs

    expanded, _ = expand_file_refs("see @infra:src/x.ts")  # known but not linked: no crash
    assert "infra:src/x.ts" in expanded


def test_file_changes_records_project(scope):
    from jarvis import file_changes

    assert file_changes.project_of(os.path.join(scope.repos["web"], "src", "x.ts")) == "web"
    assert file_changes.project_of(os.path.join(scope.repos["api"], "src", "x.ts")) is None
