"""MCP config follows the files, Claude Code's sources/headersHelper are read,
another tool's server can be taken over, Claude plugin skills are found, and
credentials never reach tool output."""
from __future__ import annotations

import json
import os
import threading
import time
import types

import jarvis.mcp.config as mcp_config
from jarvis import state
from jarvis.mcp import install as mcp_install
from jarvis.mcp import registry as mcp_reg
from jarvis.repl import render
from jarvis.utils import redact

REAL_SOURCES = mcp_config._global_sources  # before ext_env narrows it to Jarvis's file


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    # A distinct mtime even on coarse filesystems.
    stamp = time.time() + getattr(_write, "n", 0)
    _write.n = getattr(_write, "n", 0) + 1
    os.utime(path, (stamp, stamp))


def _real_sources(monkeypatch):
    monkeypatch.setattr(mcp_config, "_global_sources", REAL_SOURCES)
    monkeypatch.setattr(mcp_config, "_CHECK_EVERY", 0.0)
    monkeypatch.setattr(state, "global_mcp", True)


def _gh(token):
    return {"type": "local", "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
            "environment": {"GITHUB_PERSONAL_ACCESS_TOKEN": token}}


# ── config follows the files ─────────────────────────────────────────────

def test_get_config_sees_a_token_edited_on_disk(ext_env, monkeypatch):
    _real_sources(monkeypatch)
    oc = ext_env.home / ".config" / "opencode" / "opencode.json"
    _write(oc, {"mcp": {"github": _gh("old-token")}})
    assert mcp_config.get_config().get_server("github")["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "old-token"

    _write(oc, {"mcp": {"github": _gh("new-token")}})
    assert mcp_config.get_config().get_server("github")["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "new-token"


def test_get_config_is_cached_while_nothing_changes(ext_env, monkeypatch):
    _real_sources(monkeypatch)
    first = mcp_config.get_config()
    assert mcp_config.get_config() is first


# ── Claude Code sources ──────────────────────────────────────────────────

def test_reads_claude_home_mcp_json_with_headers_helper(ext_env, monkeypatch):
    _real_sources(monkeypatch)
    helper = "printf '{\"Authorization\":\"Bearer %s\"}' \"$(gh auth token)\""
    _write(ext_env.home / ".mcp.json", {"mcpServers": {"github": {
        "type": "http", "url": "https://api.githubcopilot.com/mcp/", "headersHelper": helper}}})
    # OpenCode defines it too — Claude's comes first.
    _write(ext_env.home / ".config" / "opencode" / "opencode.json", {"mcp": {"github": _gh("x")}})
    cfg = mcp_config.reload_config()
    assert cfg.get_source("github") == "claude"
    assert cfg.get_server("github") == {"type": "http", "url": "https://api.githubcopilot.com/mcp/", "headersHelper": helper}
    assert "opencode" in cfg.get_also("github")


def test_reads_claude_local_scope_for_this_folder(ext_env, monkeypatch):
    _real_sources(monkeypatch)
    _write(ext_env.home / ".claude.json", {
        "mcpServers": {"shared": {"command": "echo"}},
        "projects": {
            str(ext_env.proj): {"mcpServers": {"here": {"command": "echo", "args": ["local"]}}},
            "/somewhere/else": {"mcpServers": {"elsewhere": {"command": "echo"}}},
        },
    })
    servers = mcp_config.reload_config().list_servers()
    assert servers["here"]["args"] == ["local"]
    assert "shared" in servers and "elsewhere" not in servers


def test_parent_mcp_json_up_to_home(ext_env, monkeypatch, tmp_path):
    _real_sources(monkeypatch)
    nested = ext_env.home / "code" / "app"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    _write(ext_env.home / "code" / ".mcp.json", {"mcpServers": {"parent": {"command": "echo"}}})
    _write(tmp_path / ".mcp.json", {"mcpServers": {"above-home": {"command": "echo"}}})
    servers = mcp_config.reload_config().list_servers()
    assert "parent" in servers and "above-home" not in servers


# ── headersHelper ────────────────────────────────────────────────────────

def test_headers_helper_merges_its_json():
    cfg = {"type": "http", "url": "https://x.test/mcp", "headers": {"X-A": "1"},
           "headersHelper": "printf '{\"Authorization\":\"Bearer %s\"}' \"$CLAUDE_CODE_MCP_SERVER_NAME\""}
    out, err = mcp_reg.resolve_headers_helper("gh", cfg)
    assert err is None
    assert out["headers"] == {"X-A": "1", "Authorization": "Bearer gh"}
    assert "headersHelper" not in out
    assert not mcp_reg._wants_oauth(out) and not mcp_reg._wants_oauth(cfg)


def test_headers_helper_errors_are_readable():
    _cfg, err = mcp_reg.resolve_headers_helper("s", {"headersHelper": "echo nope >&2; exit 3"})
    assert err == "headersHelper failed (exit 3): nope"
    _cfg, err = mcp_reg.resolve_headers_helper("s", {"headersHelper": "echo not-json"})
    assert "JSON object" in err


# ── a live server restarts when its entry changes ────────────────────────

def test_live_server_restarts_when_its_config_changes(monkeypatch):
    reg = mcp_reg.MCPRegistry()
    old = {"type": "stdio", "command": "npx", "env": {"T": "old"}}
    new = {"type": "stdio", "command": "npx", "env": {"T": "new"}}
    reg._servers["github"] = mcp_reg.MCPServerState(config=old, connected=True)
    reg._servers["same"] = mcp_reg.MCPServerState(config=old, connected=True)
    reg._started_from.update({"github": old, "same": old})
    done = threading.Event()
    calls = []
    monkeypatch.setattr(reg, "disconnect", lambda n: calls.append(("disconnect", n)))

    def fake_connect(n, cfg, **_kw):
        calls.append(("connect", n, cfg["env"]["T"]))
        done.set()

    monkeypatch.setattr(reg, "connect", fake_connect)
    cfg = types.SimpleNamespace(get_server=lambda n: {"github": new, "same": old}.get(n))
    reg._restart_changed(None, cfg)
    assert done.wait(5)
    assert calls == [("disconnect", "github"), ("connect", "github", "new")]


def test_registry_listens_to_config_reloads():
    assert mcp_reg.mcp_registry._restart_changed in mcp_config._listeners


def test_restart_skips_a_server_already_restarted_on_the_new_entry(monkeypatch):
    reg = mcp_reg.MCPRegistry()
    old = {"command": "a"}
    new = {"command": "b"}
    reg._servers["s"] = mcp_reg.MCPServerState(config=old, connected=True)
    reg._started_from["s"] = old
    started = []
    real_thread = threading.Thread

    class Inline(real_thread):
        def start(self):
            reg._started_from["s"] = new  # a manual reconnect got there first
            self.run()

    monkeypatch.setattr(mcp_reg.threading, "Thread", Inline)
    monkeypatch.setattr(reg, "disconnect", lambda n: started.append(n))
    reg._restart_changed(None, types.SimpleNamespace(get_server=lambda n: new))
    assert started == []


# ── taking over another tool's server ────────────────────────────────────

def test_add_mcp_replace_takes_over_another_tools_server(ext_env, monkeypatch):
    oc = ext_env.home / ".config" / "opencode" / "opencode.json"
    monkeypatch.setattr(mcp_config, "_global_sources", lambda: [
        ("jarvis", mcp_config.MCP_GLOBAL_CONFIG_FILE, ""), ("opencode", oc, "mcp")])
    monkeypatch.setattr(state, "global_mcp", True)
    _write(oc, {"mcp": {"github": _gh("old")}})

    res = mcp_install.add_mcp(name="github", command="npx", args=["-y", "@modelcontextprotocol/server-github"],
                              env={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_" + "n" * 36}, connect=False)
    assert res["servers"][0]["status"] == "exists"
    assert "replace=true" in res["servers"][0]["message"]

    res = mcp_install.add_mcp(name="github", command="npx", args=["-y", "@modelcontextprotocol/server-github"],
                              env={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_" + "n" * 36}, connect=False, replace=True)
    row = res["servers"][0]
    assert row["status"] == "added" and "takes over from OpenCode" in row["scope_note"]
    cfg = mcp_config.get_config()
    assert cfg.get_source("github") == "jarvis"
    assert cfg.get_server("github")["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "${GITHUB_PERSONAL_ACCESS_TOKEN}"
    assert json.loads(oc.read_text())["mcp"]["github"]["environment"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "old"


# ── Claude Code plugin skills ────────────────────────────────────────────

def _plugin(home, key, scope, enabled, project=None):
    from jarvis.storage import skills  # noqa: F401  (imported for the fixture's patches)

    name = key.split("@")[0]
    install = home / ".claude" / "plugins" / "cache" / name
    (install / "skills" / f"{name}-skill").mkdir(parents=True)
    (install / "skills" / f"{name}-skill" / "SKILL.md").write_text(
        f"---\nname: {name}-skill\ndescription: from the {name} plugin\n---\nbody\n")
    entry = {"scope": scope, "installPath": str(install)}
    if project:
        entry["projectPath"] = str(project)
    return entry, enabled


def test_claude_plugin_skills_follow_enabled_plugins(ext_env):
    from jarvis.storage import skills

    home = ext_env.home
    entries = {
        "on@m": _plugin(home, "on@m", "user", True),
        "off@m": _plugin(home, "off@m", "user", False),
        "here@m": _plugin(home, "here@m", "project", None, project=ext_env.proj),
        "there@m": _plugin(home, "there@m", "project", None, project=home / "other"),
    }
    _write(home / ".claude" / "plugins" / "installed_plugins.json",
           {"version": 2, "plugins": {k: [e] for k, (e, _on) in entries.items()}})
    _write(home / ".claude" / "settings.json", {"enabledPlugins": {"on@m": True, "off@m": False}})
    _write(ext_env.proj / ".claude" / "settings.json", {"enabledPlugins": {"here@m": True}})
    _write(home / "other" / ".claude" / "settings.json", {"enabledPlugins": {"there@m": True}})

    names = [d.parent.name for d in skills.claude_plugin_skill_dirs()]
    assert names == ["on", "here"]
    state.global_skills = True
    skills.invalidate_cache()
    found = {s["name"]: s for s in skills.discover_skills(include_global=True)}
    assert {"on-skill", "here-skill"} <= set(found) and "off-skill" not in found
    assert found["on-skill"]["tool"] == "claude"


# ── credentials never reach tool output ──────────────────────────────────

def test_redact_hides_token_shapes_and_stored_secrets(ext_env):
    from jarvis.mcp import secrets as mcp_secrets

    mcp_secrets.set_secret("WEIRD_KEY", "plain-looking-value-123")
    text = "\n".join([
        '"GITHUB_TOKEN": "github_pat_11A3T7' + "x" * 70 + '"',
        "figd_" + "k" * 40,
        "ghp_" + "a" * 36,
        "and plain-looking-value-123 here",
        "nothing secret: sk-short, gh_hello",
    ])
    out = redact.redact(text)
    assert "x" * 20 not in out and "k" * 20 not in out and "a" * 20 not in out
    assert "plain-looking-value-123" not in out
    assert out.count(redact.MARKER) == 4
    assert "nothing secret: sk-short, gh_hello" in out


def test_tool_output_is_redacted_and_marker_writes_refused(ext_env, monkeypatch):
    token = "ghp_" + "z" * 36
    monkeypatch.setitem(render.FUNC, "fake_tool", lambda: f"TOKEN={token}")
    b = types.SimpleNamespace(id="t1", name="fake_tool", input={})
    _b, _icon, _ap, out = render._run_tool(b)
    assert token not in out and redact.MARKER in out

    wrote = []
    monkeypatch.setitem(render.FUNC, "write_file", lambda **kw: wrote.append(kw) or "ok")
    b = types.SimpleNamespace(id="t2", name="write_file",
                              input={"path": "x.json", "content": '{"t": "ghp_…' + redact.MARKER + '"}'})
    _b, _icon, _ap, out = render._run_tool(b)
    assert out.startswith("ERROR") and "placeholder" in out and not wrote
