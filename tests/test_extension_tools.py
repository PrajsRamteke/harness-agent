"""The agent-facing tools that install skills and MCP servers."""
import json

import pytest

from jarvis.mcp import secrets
from jarvis.tools import FUNC, TOOL_GROUPS, extensions as ext
from jarvis.tools.router import select_tools
from jarvis.tools.plan import PLAN_MODE_ALLOWED
from jarvis.repl.render import _SERIAL_TOOLS

TOOL_NAMES = ["skill_install", "skill_remove", "mcp_add", "mcp_list", "mcp_connect", "mcp_remove"]


@pytest.fixture()
def approving(monkeypatch):
    """Records every approval the tools ask for; answers yes."""
    asked = []
    monkeypatch.setattr(ext, "_approve", lambda d: asked.append(d) or True)
    return asked


def names_for(text):
    return {t["name"] for t in select_tools([{"role": "user", "content": text}])}


# ── registration & routing ─────────────────────────────────────────────────


def test_tools_are_registered_and_serial():
    assert {t["name"] for t in TOOL_GROUPS["extensions"]} == set(TOOL_NAMES)
    for n in TOOL_NAMES:
        assert n in FUNC and n in _SERIAL_TOOLS
    assert "mcp_list" in PLAN_MODE_ALLOWED and "mcp_add" not in PLAN_MODE_ALLOWED


@pytest.mark.parametrize("text", [
    "add linear",
    "please install this skill https://github.com/anthropics/skills/tree/main/skills/pdf",
    "add mcp https://mcp.notion.com/mcp",
    "npx skills add anthropics/skills",
    "set up the sentry mcp server",
    "claude mcp add --transport http x https://x.example/mcp",
])
def test_router_offers_install_tools(text):
    assert set(TOOL_NAMES) <= names_for(text)


@pytest.mark.parametrize("text", ["hello there", "fix the bug in foo.py", "what's the weather"])
def test_router_keeps_them_out_otherwise(text):
    assert not set(TOOL_NAMES) & names_for(text)


def test_router_offers_them_while_a_sign_in_waits(monkeypatch):
    monkeypatch.setattr("jarvis.tools.extensions.sign_in_waiting", lambda: True)
    assert "mcp_connect" in names_for("continue")


def test_every_schema_is_valid_json_schema_shape():
    for t in TOOL_GROUPS["extensions"]:
        assert t["input_schema"]["type"] == "object"
        assert t["description"]


# ── mcp_add ────────────────────────────────────────────────────────────────


def test_mcp_add_stdio_is_approved_and_written(ext_env, approving):
    out = ext.mcp_add(source="npx -y @acme/tool-mcp", scope="project", connect=False)
    assert "✓ tool (project) added" in out
    assert "runs: npx -y @acme/tool-mcp" in approving[0]
    assert "tool" in json.loads((ext_env.proj / ".mcp.json").read_text())["mcpServers"]


def test_mcp_add_declined(ext_env, monkeypatch):
    monkeypatch.setattr(ext, "_approve", lambda d: False)
    out = ext.mcp_add(source="https://mcp.linear.app/mcp", connect=False)
    assert "declined" in out and not (ext_env.home / ".config/harness-agent/mcp.json").exists()


def test_mcp_add_reports_sign_in_needed(ext_env, approving, monkeypatch):
    from jarvis.mcp import install

    monkeypatch.setattr(
        install, "server_status",
        lambda name, connect=True, note="": {"name": name, "status": "auth_required", "auth": {"url": "https://a/authorize?state=1"}},
    )
    out = ext.mcp_add(source="https://mcp.linear.app/mcp", scope="global")
    assert "sign-in needed" in out and "Authenticate" in out and "Sign in to linear" in out


def test_mcp_add_asks_for_a_missing_key_with_a_hidden_prompt(ext_env, approving, monkeypatch):
    calls = []

    class FakeConsole:
        def input(self, prompt="", *, password=False, **kw):
            calls.append((prompt, password))
            return "sekret-123"

    monkeypatch.setattr(ext, "console", FakeConsole())
    out = ext.mcp_add(
        source='{"mcpServers": {"brave": {"command": "npx", "args": ["b"], "env": {"BRAVE_API_KEY": "YOUR_API_KEY"}}}}',
        scope="project", connect=False,
    )
    assert calls and calls[0][1] is True and "BRAVE_API_KEY" in calls[0][0]
    assert secrets.get_secret("BRAVE_API_KEY") == "sekret-123"
    assert "sekret-123" not in out and "sekret-123" not in (ext_env.proj / ".mcp.json").read_text()


def test_mcp_add_key_prompt_can_be_skipped(ext_env, approving, monkeypatch):
    class FakeConsole:
        def input(self, *a, **k):
            return ""

    monkeypatch.setattr(ext, "console", FakeConsole())
    out = ext.mcp_add(source='{"mcpServers": {"b": {"command": "npx", "args": ["b"], "env": {"K": "YOUR_KEY"}}}}', scope="project")
    assert "needs K" in out


def test_mcp_add_error_for_nonsense(ext_env, approving):
    assert ext.mcp_add(source="hello world").startswith("ERROR:")
    assert ext.mcp_add().startswith("ERROR:")


def test_mcp_list_and_remove(ext_env, approving):
    assert "No MCP servers in scope" in ext.mcp_list()
    ext.mcp_add(source="npx -y @acme/tool-mcp", scope="project", connect=False)
    listing = ext.mcp_list()
    assert "tool [project, stdio]" in listing
    assert "Removed 'tool'" in ext.mcp_remove("tool")
    assert ext.mcp_remove("tool").startswith("ERROR:")


def test_mcp_remove_needs_approval(ext_env, monkeypatch):
    monkeypatch.setattr(ext, "_approve", lambda d: False)
    assert ext.mcp_remove("anything") == "USER DENIED"


def test_mcp_connect_unknown_server(ext_env):
    assert ext.mcp_connect("ghost").startswith("ERROR: no MCP server named 'ghost'")


def test_mcp_connect_rejects_unknown_action(ext_env, approving):
    ext.mcp_add(source="npx -y @acme/tool-mcp", scope="project", connect=False)
    assert ext.mcp_connect("tool", "dance").startswith("ERROR:")


# ── skills ─────────────────────────────────────────────────────────────────


def make_repo(root):
    for name in ("pdf", "docx"):
        d = root / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Handles {name} files.\n---\nbody\n")


def test_skill_install_asks_which_when_many(ext_env, approving, tmp_path):
    make_repo(tmp_path / "r")
    out = ext.skill_install(str(tmp_path / "r"), scope="project")
    assert "2 skills" in out and "pdf" in out and "docx" in out and "ask_user_question" in out
    assert not approving  # nothing to approve until a choice is made


def test_skill_install_one(ext_env, approving, tmp_path):
    make_repo(tmp_path / "r")
    out = ext.skill_install(str(tmp_path / "r"), scope="project", skill="pdf")
    assert "Installed 1 skill (project)" in out and "✓ pdf" in out
    assert "install skill pdf" in approving[0] and "(project)" in approving[0]
    assert (ext_env.proj / ".harness/skills/pdf/SKILL.md").is_file()


def test_skill_install_default_scope_is_global(ext_env, approving, tmp_path):
    make_repo(tmp_path / "r")
    out = ext.skill_install(str(tmp_path / "r"), skill="docx")
    assert "(global)" in out and (ext_env.home / ".harness/skills/docx/SKILL.md").is_file()


def test_skill_install_declined(ext_env, monkeypatch, tmp_path):
    make_repo(tmp_path / "r")
    monkeypatch.setattr(ext, "_approve", lambda d: False)
    out = ext.skill_install(str(tmp_path / "r"), skill="pdf", scope="project")
    assert out.startswith("ERROR:") and "declined" in out


def test_skill_install_bad_source(ext_env, approving):
    assert ext.skill_install("hello there").startswith("ERROR:")


def test_skill_remove(ext_env, approving, tmp_path):
    make_repo(tmp_path / "r")
    ext.skill_install(str(tmp_path / "r"), skill="pdf", scope="project")
    assert "Removed skill 'pdf'" in ext.skill_remove("pdf")
    assert ext.skill_remove("pdf").startswith("ERROR:")
