"""Agents, skills and MCP servers from every tool's global folder, tagged with
the tool they come from (claude, cursor, codex, …)."""
from __future__ import annotations

import asyncio
import textwrap

import pytest

from jarvis.utils.origins import tool_for_path, tool_tag


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A fake home folder (Path.home() reads $HOME) and an empty project dir."""
    h = tmp_path / "home"
    proj = tmp_path / "proj"
    h.mkdir()
    proj.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.chdir(proj)
    from jarvis.storage import agents as ag, skills as sk

    ag.invalidate_cache()
    sk.invalidate_cache()
    yield h
    ag.invalidate_cache()
    sk.invalidate_cache()


def _skill(root, name, desc="does a thing"):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nbody\n")


def _agent(root, name, desc="an agent", model=""):
    root.mkdir(parents=True, exist_ok=True)
    extra = f"model: {model}\n" if model else ""
    (root / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {desc}\n{extra}---\nbody\n")


@pytest.mark.parametrize("path, tool", [
    ("/Users/x/.claude/skills", "claude"),
    ("/Users/x/.cursor/agents", "cursor"),
    ("/Users/x/.codex/skills", "codex"),
    ("/Users/x/.gemini/skills", "gemini"),
    ("/Users/x/.agents/skills", "agents"),
    ("/Users/x/.config/opencode/skills", "opencode"),
    ("/Users/x/.harness/skills", "jarvis"),
    ("/Users/x/.config/harness-agent/agents", "jarvis"),
    ("/repo/.skills", "jarvis"),
    # nearest folder wins: a worktree under ~/.codex still reads .claude as claude
    ("/Users/x/.codex/worktrees/app/.claude/skills", "claude"),
    ("/somewhere/else", "jarvis"),
])
def test_tool_for_path(path, tool):
    assert tool_for_path(path) == tool


def test_tool_tag_counts_other_tools_once():
    assert tool_tag("claude") == "claude"
    assert tool_tag("claude", ["cursor", "codex", "cursor", "claude"]) == "claude +2"


def test_skills_from_every_tool_are_found_and_tagged(home):
    from jarvis.storage import skills as sk

    _skill(home / ".claude" / "skills", "shared")
    _skill(home / ".cursor" / "skills", "shared")      # same skill in two tools
    _skill(home / ".codex" / "skills", "shared")
    _skill(home / ".cursor" / "skills", "cursor-only")
    _skill(home / ".codex" / "skills", "codex-only")
    _skill(home / ".gemini" / "skills", "gemini-only")
    _skill(home / ".agents" / "skills", "agents-only")
    _skill(home / ".kiro" / "skills", "kiro-only")

    by_name = {s["name"]: s for s in sk.discover_skills(force=True, include_global=True)}
    assert by_name["shared"]["tool"] == "claude"
    assert by_name["shared"]["also"] == ["codex", "cursor"]
    assert {n: by_name[n]["tool"] for n in ("cursor-only", "codex-only", "gemini-only", "agents-only", "kiro-only")} == {
        "cursor-only": "cursor", "codex-only": "codex", "gemini-only": "gemini",
        "agents-only": "agents", "kiro-only": "kiro",
    }
    # Global off: none of them.
    assert not [s for s in sk.discover_skills(force=True, include_global=False) if s["scope"] == "global"]


def test_codex_home_env_moves_codex_skills(home, tmp_path, monkeypatch):
    from jarvis.storage import skills as sk

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    _skill(tmp_path / "codex-home" / "skills", "from-codex-home")
    found = {s["name"]: s for s in sk.discover_skills(force=True, include_global=True)}
    assert "from-codex-home" in found


def test_project_skills_keep_their_tool(home):
    from jarvis.storage import skills as sk

    _skill(home.parent / "proj" / ".cursor" / "skills", "proj-cursor")
    _skill(home.parent / "proj" / ".harness" / "skills", "proj-jarvis")
    found = {s["name"]: s for s in sk.discover_skills(force=True, include_global=False)}
    assert found["proj-cursor"]["tool"] == "cursor" and found["proj-cursor"]["scope"] == "project"
    assert found["proj-jarvis"]["tool"] == "jarvis"


def test_describe_installed_carries_labels_and_searches_by_tool(home, monkeypatch):
    from jarvis import state
    from jarvis.storage import skill_install as si

    monkeypatch.setattr(state, "global_skills", True)
    _skill(home / ".claude" / "skills", "shared")
    _skill(home / ".cursor" / "skills", "shared")
    _skill(home / ".cursor" / "skills", "cursor-only")

    rows = {r["name"]: r for r in si.describe_installed()["skills"]}
    assert rows["shared"]["tool_label"] == "Claude Code"
    assert rows["shared"]["also_labels"] == ["Cursor"]
    assert rows["cursor-only"]["tool"] == "cursor"
    assert {r["name"] for r in si.describe_installed("cursor")["skills"]} >= {"shared", "cursor-only"}


def test_agents_from_cursor_and_gemini_are_found_and_tagged(home):
    from jarvis.storage import agents as ag

    _agent(home / ".claude" / "agents", "reviewer", model="sonnet")
    _agent(home / ".cursor" / "agents", "reviewer")
    _agent(home / ".cursor" / "agents", "cursor-agent")
    _agent(home / ".gemini" / "agents", "gemini-agent")
    _agent(home / ".config" / "opencode" / "agent", "oc-agent")   # OpenCode's older folder name

    by_name = {a["name"]: a for a in ag.discover_agents(force=True, include_global=True)}
    assert by_name["reviewer"]["tool"] == "claude" and by_name["reviewer"]["also"] == ["cursor"]
    assert by_name["cursor-agent"]["tool"] == "cursor"
    assert by_name["gemini-agent"]["tool"] == "gemini"
    assert by_name["oc-agent"]["tool"] == "opencode"


def test_mcp_reads_codex_gemini_antigravity_and_tracks_duplicates(home):
    from jarvis.mcp.config import MCPConfig, global_source_summary

    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text(textwrap.dedent("""
        model = "gpt-5"

        [mcp_servers.docs]
        command = "npx"
        args = ["-y", "docs-mcp"]
        env = { DOCS_KEY = "x" }

        [mcp_servers.remote]
        url = "https://mcp.example.com/mcp"
        bearer_token_env_var = "EXAMPLE_TOKEN"

        [mcp_servers.off]
        command = "nope"
        enabled = false
    """))
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    (home / ".gemini" / "settings.json").write_text(
        '{"mcpServers": {"gem": {"httpUrl": "https://gem.example.com/mcp"}, "docs": {"command": "docs"}}}')
    (home / ".gemini" / "antigravity" / "mcp_config.json").write_text(
        '{"mcpServers": {"supa": {"serverUrl": "https://supa.example.com/mcp"}}}')

    cfg = MCPConfig()
    cfg.load(project_path=home / "no-project.json", include_global=True)
    servers = cfg.list_servers()
    assert "off" not in servers
    assert servers["docs"] == {"command": "npx", "args": ["-y", "docs-mcp"], "type": "stdio", "env": {"DOCS_KEY": "x"}}
    assert servers["remote"]["headers"] == {"Authorization": "Bearer ${EXAMPLE_TOKEN}"}
    assert servers["gem"]["url"] == "https://gem.example.com/mcp"
    assert servers["supa"]["url"] == "https://supa.example.com/mcp"
    assert {n: cfg.get_source(n) for n in ("docs", "remote", "gem", "supa")} == {
        "docs": "codex", "remote": "codex", "gem": "gemini", "supa": "antigravity",
    }
    assert cfg.get_also("docs") == ["gemini"]   # gemini defines docs too; codex's wins
    assert cfg.get_also("gem") == []

    count, tools = global_source_summary()
    assert count == 4 and tools == ["codex", "gemini", "antigravity"]


@pytest.fixture()
def tui(monkeypatch):
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings
    import jarvis.tui.prompt_history as prompt_history
    import jarvis.updater as updater

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    return JarvisTUI


def _plain(renderable) -> str:
    from rich.console import Console

    con = Console(width=140, record=True, file=open("/dev/null", "w"))
    con.print(renderable)
    return con.export_text()


def _rows(opts):
    return [(opts.get_option_at_index(i).id, _plain(opts.get_option_at_index(i).prompt))
            for i in range(opts.option_count)]


def test_agent_picker_tags_rows_and_lists_hidden_global_agents(home, tui, monkeypatch):
    from jarvis import state
    from jarvis.tui.agent_modal import AgentPickerScreen

    _agent(home / ".claude" / "agents", "reviewer", model="sonnet")
    _agent(home / ".cursor" / "agents", "reviewer")
    _agent(home / ".cursor" / "agents", "cursor-agent")
    picked: list = []

    async def run() -> None:
        app = tui()
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            monkeypatch.setattr(state, "global_agents", False)
            app.push_screen(AgentPickerScreen(), picked.append)
            await pilot.pause(0.3)
            opts = app.screen.query_one("#agent_list")
            rows = dict(_rows(opts))
            # Global agents are off, but still listed (dimmed) with their tool.
            assert "claude +1" in rows["hidden::reviewer"] and "sonnet" in rows["hidden::reviewer"]
            assert "cursor" in rows["hidden::cursor-agent"]
            header = next(t for oid, t in _rows(opts) if oid is None and "GLOBAL" in t)
            assert "claude · cursor" in header
            # Enter on a hidden one turns global agents on and picks it.
            opts.highlighted = next(i for i in range(opts.option_count)
                                    if opts.get_option_at_index(i).id == "hidden::cursor-agent")
            await pilot.press("enter")
            await pilot.pause(0.2)

    asyncio.run(run())
    assert state.global_agents is True
    assert picked and picked[0]["name"] == "cursor-agent" and picked[0]["tool"] == "cursor"


def test_skill_browser_tags_rows_with_their_tool(home, tui, monkeypatch):
    from jarvis import state
    from jarvis.tui.skill_modal import SkillBrowserScreen

    _skill(home / ".claude" / "skills", "shared")
    _skill(home / ".cursor" / "skills", "shared")
    _skill(home / ".codex" / "skills", "codex-only")

    async def run() -> None:
        app = tui()
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            monkeypatch.setattr(state, "global_skills", True)
            app.push_screen(SkillBrowserScreen())
            await pilot.pause(0.3)
            rows = dict(_rows(app.screen.query_one("#skill_list")))
            assert "claude +1" in rows["shared"]
            assert "codex" in rows["codex-only"]

    asyncio.run(run())
