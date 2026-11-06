"""Package-script dropdown: scripts_api.py (finding package.json scripts, the
package manager per package) and TerminalSession.status (busy? which folder?)."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from jarvis.web import scripts_api, terminal_api


@pytest.fixture(autouse=True)
def _clean():
    scripts_api.clear_cache()
    yield
    terminal_api.close_all()
    scripts_api.clear_cache()


def _pkg(folder: Path, scripts: dict | None, **extra) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    data = {"name": extra.pop("name", folder.name), **extra}
    if scripts is not None:
        data["scripts"] = scripts
    (folder / "package.json").write_text(json.dumps(data))


def _monorepo(root: Path) -> None:
    _pkg(root, {"dev": "turbo dev", "build": "turbo build"}, name="acme", workspaces=["apps/*"])
    (root / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
    _pkg(root / "apps" / "web", {"predev": "node gen.js", "dev": "next dev", "dev:staging": "next dev -p 3001",
                                  "start:staging": "next start", "postlint": "echo no lint here"},
         name="@acme/web")
    _pkg(root / "apps" / "api", {"start": "node server.js"}, name="@acme/api")
    _pkg(root / "apps" / "docs", None, name="docs")                      # no scripts: left out
    _pkg(root / "node_modules" / "left-pad", {"test": "x"})               # never listed
    _pkg(root / "apps" / "web" / "dist", {"build": "x"})                  # build output: skipped


def _names(result):
    return {p["dir"]: [s["name"] for s in p["scripts"]] for p in result["packages"]}


def test_finds_every_package_with_scripts_and_skips_noise(tmp_path):
    _monorepo(tmp_path)
    res = scripts_api.list_scripts(str(tmp_path))
    assert res["ok"] and res["source"] == "walk"
    names = _names(res)
    assert list(names) == ["", "apps/api", "apps/web"]          # root first, then by folder
    # The hook npm runs by itself goes last; the rest keep the file's order.
    assert names["apps/web"] == ["dev", "dev:staging", "start:staging", "postlint", "predev"]
    web = next(p for p in res["packages"] if p["dir"] == "apps/web")
    assert web["name"] == "@acme/web"
    assert web["path"] == os.path.realpath(tmp_path / "apps" / "web")
    hook = {s["name"]: s["hook"] for s in web["scripts"]}
    # `predev` runs by itself before `dev`; `postlint` has no `lint`, so it's a plain script.
    assert hook == {"dev": False, "dev:staging": False, "start:staging": False, "postlint": False, "predev": True}
    assert res["count"] == 8


def test_the_lockfile_above_a_workspace_package_picks_the_manager(tmp_path):
    _monorepo(tmp_path)
    res = scripts_api.list_scripts(str(tmp_path))
    assert {p["manager"] for p in res["packages"]} == {"pnpm"}
    web = next(p for p in res["packages"] if p["dir"] == "apps/web")
    assert web["scripts"][1]["run"] == "pnpm run dev:staging"


@pytest.mark.parametrize("lock, manager", [
    ("bun.lockb", "bun"), ("bun.lock", "bun"), ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"), (None, "npm"),
])
def test_manager_from_the_lockfile(tmp_path, lock, manager):
    _pkg(tmp_path, {"dev": "vite"})
    if lock:
        (tmp_path / lock).write_text("")
    assert scripts_api.detect_manager(str(tmp_path)) == manager


def test_package_manager_field_beats_the_lockfile(tmp_path):
    _pkg(tmp_path, {"dev": "vite"}, packageManager="yarn@4.1.0")
    (tmp_path / "package-lock.json").write_text("{}")
    assert scripts_api.detect_manager(str(tmp_path)) == "yarn"


def test_odd_script_names_are_quoted_for_the_shell():
    assert scripts_api.run_command("npm", "dev:staging") == "npm run dev:staging"
    assert scripts_api.run_command("yarn", "build all") == "yarn run 'build all'"
    assert scripts_api.run_command("npm", "x;rm -rf ~") == "npm run 'x;rm -rf ~'"
    assert scripts_api.run_command("nope", "dev") == "npm run dev"


def test_broken_or_odd_package_json_files_are_skipped(tmp_path):
    _pkg(tmp_path, {"dev": "vite", "bad": 3, "": "x"})
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "package.json").write_text("{ not json")
    (tmp_path / "list").mkdir()
    (tmp_path / "list" / "package.json").write_text("[1, 2]")
    res = scripts_api.list_scripts(str(tmp_path))
    assert _names(res) == {"": ["dev"]}


def test_git_lists_only_what_is_not_ignored(tmp_path):
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("no git")
    _monorepo(tmp_path)
    _pkg(tmp_path / "generated" / "pkg", {"gen": "x"})
    (tmp_path / ".gitignore").write_text("generated/\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    res = scripts_api.list_scripts(str(tmp_path))
    assert res["source"] == "git"
    assert list(_names(res)) == ["", "apps/api", "apps/web"]


def test_results_are_cached_until_refresh(tmp_path):
    _pkg(tmp_path, {"dev": "vite"})
    first = scripts_api.list_scripts(str(tmp_path))
    _pkg(tmp_path, {"dev": "vite", "test": "vitest"})
    assert scripts_api.list_scripts(str(tmp_path)) is first
    assert _names(scripts_api.list_scripts(str(tmp_path), refresh=True)) == {"": ["dev", "test"]}


def test_no_package_json_is_an_empty_list(tmp_path):
    res = scripts_api.list_scripts(str(tmp_path))
    assert res["ok"] and res["packages"] == [] and res["count"] == 0


# ─── The shell: where it is, and whether a program holds it ─────────────

def _wait(pred, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("shell", ["/bin/zsh", "/bin/bash"])
def test_status_follows_cd_and_sees_a_running_program(monkeypatch, tmp_path, shell):
    if not os.path.exists(shell):
        pytest.skip(f"no {shell}")
    monkeypatch.setenv("SHELL", shell)
    sub = tmp_path / "apps"
    sub.mkdir()
    res = terminal_api.open_terminal(cwd=str(tmp_path))
    s = terminal_api.get_terminal(res["terminal"]["id"])
    assert _wait(lambda: not s.status()["busy"]), "a fresh shell at its prompt is not busy"
    s.write(f"cd {sub}\n")
    assert _wait(lambda: s.status()["cwd"] == os.path.realpath(sub))
    s.write("sleep 30\n")
    assert _wait(lambda: s.status()["busy"])
    assert s.status()["program"] == "sleep"
    s.write("\x03")                                     # Ctrl+C: back at the prompt
    assert _wait(lambda: not s.status()["busy"])


def test_status_route_and_scripts_route_are_terminal_only(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from jarvis.web.handler import WebHandler

    monkeypatch.chdir(tmp_path)
    _pkg(tmp_path, {"dev": "vite"})
    sent: list[tuple[int, dict]] = []

    def handler(allowed: bool):
        h = WebHandler.__new__(WebHandler)
        h._terminal_allowed = lambda: allowed
        h._send_json = lambda code, body: sent.append((code, body))
        h.bridge = SimpleNamespace(terminal_over_tunnel=False)
        return h

    assert handler(False)._handle_terminal_get("/api/terminal/scripts", {})
    assert sent[-1][1]["code"] == "local_only"
    assert handler(True)._handle_terminal_get("/api/terminal/scripts", {})
    code, body = sent[-1]
    assert code == 200 and body["shell"] is None
    assert [s["run"] for s in body["packages"][0]["scripts"]] == ["npm run dev"]
    assert handler(True)._handle_terminal_get("/api/terminal/status", {"id": ["nope"]})
    assert sent[-1][0] == 404 and sent[-1][1]["code"] == "gone"
