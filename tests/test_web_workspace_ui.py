"""Workspace sidebar & switching (static/js/wsprojects.js, wsmenu.js …): static checks."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.web import handler as web_handler

STATIC = Path(web_handler.__file__).with_name("static")
JS = STATIC / "js"
MINE = ["wsprojects", "wsmenu", "projects", "folders", "shortcuts", "palette", "events", "reorder", "composer"]


def _src(name: str) -> str:
    return (JS / f"{name}.js").read_text()


def test_new_modules_are_imported_by_the_app():
    assert "./wsprojects.js" in _src("projects") and "initWsProjects" in _src("projects")
    assert "./wsprojects.js" in _src("events") and "./wsprojects.js" in _src("composer")
    assert "./wsprojects.js" in _src("shortcuts")
    assert "./wsmenu.js" in _src("wsprojects")
    assert "wsprojects.css" in (STATIC / "css" / "projects.css").read_text()
    assert (STATIC / "css" / "wsprojects.css").is_file()


def test_every_named_import_exists_in_its_module():
    exports: dict[str, set[str]] = {}
    for f in JS.glob("*.js"):
        text = f.read_text()
        names = set(re.findall(r"export\s+(?:async\s+)?(?:function|const|let|class)\s+([A-Za-z0-9_$]+)", text))
        for group in re.findall(r"export\s*\{([^}]*)\}", text):
            names |= {p.split(" as ")[-1].strip() for p in group.split(",") if p.strip()}
        exports[f.stem] = names
    bad = []
    for name in MINE:
        for names, mod in re.findall(r"import\s*\{([^}]*)\}\s*from\s*'\./([a-z_0-9]+)\.js'", _src(name)):
            for item in (n.split(" as ")[0].strip() for n in names.split(",") if n.strip()):
                if item not in exports.get(mod, set()):
                    bad.append(f"{name}.js imports {item} from {mod}.js")
    assert bad == []


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("name", MINE)
def test_modules_parse(name):
    res = subprocess.run(["node", "--check", str(JS / f"{name}.js")], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_workspace_requests_are_host_level_and_pass_the_base_guard():
    src = _src("wsprojects")
    fetches = [ln for ln in src.splitlines() if re.search(r"\bfetch\(", ln)]
    assert fetches == ["  const res = await fetch(path, opts);"]   # one call, path comes from wsJson's callers
    assert "EventSource" not in src and "xhr.open" not in src
    assert "const API = '/api/workspace'" in src


def test_digit_shortcuts_use_event_code_not_key():
    src = _src("shortcuts")
    assert "Digit([0-9])" in src and "e.code" in src
    assert not re.search(r"e\.key\s*===\s*'[0-9]'", src)
    assert "switchToSlot" in src and "openProjectPalette" in src


def test_contracts_the_other_agents_rely_on():
    ws = _src("wsprojects")
    assert "window.dispatchEvent(new CustomEvent('jarvis:open-terminal-in'" in ws
    assert "projectId" in ws and "instanceId" in ws
    assert "case 'workspace':" in _src("events")
    assert "jarvis-workspace-migrated" in ws and "import_order" in ws
    assert "jarvis-project-order" in _src("projects")           # ad-hoc folders keep the local order
    assert "zone === 'ws'" in _src("projects") and "data-zone" in _src("reorder") + _src("wsprojects")
    assert "isBooting()" in _src("composer") and "holdPrompt(" in _src("composer")
    assert "scanRepos" in _src("folders") and "Add all" in _src("folders")


def test_shortcut_help_lists_the_project_keys():
    src = _src("shortcuts")
    assert "Switch to workspace project" in src and "[ALT, '0']" in src
