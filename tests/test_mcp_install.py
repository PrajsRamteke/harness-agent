"""Adding MCP servers: parsing what people paste, scope handling, credentials."""
import json

import pytest

from jarvis.mcp import install, secrets
from jarvis.mcp.config import get_config, reload_config


# ── secrets ────────────────────────────────────────────────────────────────


def test_expand_uses_env_then_store_then_default(ext_env, monkeypatch):
    secrets.set_secret("FROM_STORE", "s3")
    monkeypatch.setenv("FROM_ENV", "e1")
    monkeypatch.setenv("BOTH", "env-wins")
    secrets.set_secret("BOTH", "store")
    out = secrets.expand({
        "a": "${FROM_ENV}", "b": ["x-${FROM_STORE}"], "c": "${NOPE:-fallback}", "d": "${BOTH}", "e": "${UNKNOWN}",
    })
    assert out == {"a": "e1", "b": ["x-s3"], "c": "fallback", "d": "env-wins", "e": "${UNKNOWN}"}


def test_missing_lists_unset_references_only(ext_env, monkeypatch):
    monkeypatch.delenv("TOKEN_A", raising=False)
    secrets.set_secret("TOKEN_B", "x")
    cfg = {"env": {"A": "${TOKEN_A}", "B": "${TOKEN_B}", "C": "${TOKEN_C:-ok}"}, "args": ["--k=${TOKEN_A}"]}
    assert secrets.missing(cfg) == ["TOKEN_A"]


def test_secret_store_is_private(ext_env):
    secrets.set_secret("KEY_ONE", "abc")
    assert oct(secrets.SECRETS_FILE.stat().st_mode)[-3:] == "600"
    assert secrets.delete_secret("KEY_ONE") and not secrets.delete_secret("KEY_ONE")
    with pytest.raises(ValueError):
        secrets.set_secret("not valid", "x")


@pytest.mark.parametrize("value", ["", "YOUR_API_KEY", "your-api-key-here", "<token>", "xxxx", "API_KEY", "changeme"])
def test_placeholders_are_recognised(value):
    assert secrets.is_placeholder(value)


@pytest.mark.parametrize("value", ["ghp_1a2b3c4d5e6f", "sk-live-9f8e7d", "/Users/me/projects"])
def test_real_values_are_not_placeholders(value):
    assert not secrets.is_placeholder(value)


def test_protect_env_and_headers_keep_secrets_out_of_config():
    env, stored = secrets.protect_env("gh", {"GITHUB_TOKEN": "ghp_real", "REGION": "eu", "API_KEY": "YOUR_API_KEY"})
    assert env == {"GITHUB_TOKEN": "${GITHUB_TOKEN}", "REGION": "eu", "API_KEY": "${API_KEY}"}
    assert stored == {"GITHUB_TOKEN": "ghp_real"}  # the placeholder is a blank to fill in, not a value
    headers, stored = secrets.protect_headers("linear", {"Authorization": "Bearer abc123", "X-Team": "core"})
    assert headers == {"Authorization": "Bearer ${MCP_LINEAR_AUTHORIZATION}", "X-Team": "core"}
    assert stored == {"MCP_LINEAR_AUTHORIZATION": "abc123"}


# ── parsing ────────────────────────────────────────────────────────────────


def spec_of(text):
    (spec,) = install.parse_source(text)
    return spec


def test_hosted_url():
    sp = spec_of("https://mcp.linear.app/mcp")
    assert sp["name"] == "linear"
    assert sp["entry"] == {"type": "http", "url": "https://mcp.linear.app/mcp"}


def test_sse_endpoint_is_guessed():
    assert spec_of("https://mcp.asana.com/sse")["entry"]["type"] == "sse"


def test_well_known_name():
    sp = spec_of("notion")
    assert sp["entry"]["url"] == "https://mcp.notion.com/mcp"


def test_github_remote_carries_its_credential():
    sp = spec_of("github")
    assert sp["entry"]["headers"]["Authorization"] == "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}"
    assert sp["credentials"] == ["GITHUB_PERSONAL_ACCESS_TOKEN"]


def test_npx_command_names_the_server():
    sp = spec_of("npx -y @modelcontextprotocol/server-filesystem /tmp")
    assert sp["name"] == "filesystem"
    assert sp["entry"] == {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}


def test_env_prefix_becomes_env():
    sp = spec_of("BRAVE_API_KEY=abc npx -y @modelcontextprotocol/server-brave-search")
    assert sp["entry"]["env"] == {"BRAVE_API_KEY": "abc"}
    assert sp["name"] == "brave-search"


def test_bare_npm_package():
    assert spec_of("@upstash/context7-mcp")["entry"]["args"] == ["-y", "@upstash/context7-mcp"]


def test_claude_mcp_add_http():
    sp = spec_of("claude mcp add --transport http sentry https://mcp.sentry.dev/mcp")
    assert (sp["name"], sp["entry"]["type"], sp["entry"]["url"]) == ("sentry", "http", "https://mcp.sentry.dev/mcp")


def test_claude_mcp_add_stdio_with_env_and_scope():
    sp = spec_of("claude mcp add -s project github -e GITHUB_TOKEN=ghp_x -- npx -y @modelcontextprotocol/server-github")
    assert sp["name"] == "github" and sp["scope_hint"] == "project"
    assert sp["entry"]["command"] == "npx" and sp["entry"]["env"] == {"GITHUB_TOKEN": "ghp_x"}


def test_claude_mcp_add_header():
    sp = spec_of('claude mcp add --transport http api https://x.example/mcp -H "Authorization: Bearer t0k"')
    assert sp["entry"]["headers"] == {"Authorization": "Bearer t0k"}


def test_json_claude_shape():
    specs = install.parse_source('{"mcpServers": {"a": {"command": "uvx", "args": ["x"]}, "b": {"url": "https://b.example/mcp"}}}')
    assert [s["name"] for s in specs] == ["a", "b"]
    assert specs[1]["entry"]["type"] == "http"


def test_json_fragment_without_braces():
    (sp,) = install.parse_source('"time": {"command": "uvx", "args": ["mcp-server-time"]}')
    assert sp["name"] == "time"


def test_vscode_inputs_become_credentials():
    (sp,) = install.parse_source('{"servers": {"x": {"type": "stdio", "command": "npx", "args": ["x"], "env": {"K": "${input:api-key}"}}}}')
    assert sp["entry"]["env"] == {"K": "${API_KEY}"}


@pytest.mark.parametrize("text", ["", "hello world", '{"a": 1}', "{not json", "claude mcp add"])
def test_unparseable_text_says_so(text):
    with pytest.raises(install.ParseError):
        install.parse_source(text)


def test_github_repo_reads_readme(monkeypatch):
    readme = "# thing\n\n```bash\nclaude mcp add --transport http thing https://mcp.thing.dev/mcp\n```\n"

    class R:
        status_code = 200
        text = readme

    monkeypatch.setattr("httpx.get", lambda *a, **k: R())
    (sp,) = install.parse_source("https://github.com/acme/thing-mcp")
    assert sp["entry"]["url"] == "https://mcp.thing.dev/mcp"
    assert any("README" in n for n in sp["notes"])


def test_github_repo_with_many_servers_asks_which(monkeypatch):
    readme = "```json\n{\"mcpServers\": {\"a\": {\"command\": \"npx\", \"args\": [\"a\"]}, \"b\": {\"command\": \"npx\", \"args\": [\"b\"]}}}\n```"

    class R:
        status_code = 200
        text = readme

    monkeypatch.setattr("httpx.get", lambda *a, **k: R())
    with pytest.raises(install.ParseError, match="several servers"):
        install.parse_source("https://github.com/acme/collection")


@pytest.mark.parametrize("raw,expected", [
    ("My Fancy Server!", "my-fancy-server"),
    ("a__b", "a_b"),
    ("x" * 60, "x" * 24),
    ("", "server"),
])
def test_names_are_safe_for_tool_names(raw, expected):
    assert install.sanitize_name(raw) == expected


# ── add / remove / move ────────────────────────────────────────────────────

STDIO = "npx -y @acme/tool-mcp"


def test_add_to_global_writes_jarvis_file_and_turns_scope_on(ext_env):
    from jarvis import state

    res = install.add_mcp(STDIO, scope="global", connect=False)
    assert res["ok"], res
    (srv,) = res["servers"]
    assert srv["status"] == "added" and srv["scope"] == "global"
    assert "turned it on" in srv["scope_note"]
    assert state.global_mcp is True
    data = json.loads((ext_env.home / ".config/harness-agent/mcp.json").read_text())
    assert data["servers"]["tool"]["command"] == "npx"
    assert data["auto_connect"] == ["tool"]
    assert get_config().get_scope("tool") == "global"


def test_add_to_project_writes_shared_format(ext_env):
    res = install.add_mcp("https://mcp.linear.app/mcp", scope="project", connect=False)
    assert res["ok"]
    data = json.loads((ext_env.proj / ".mcp.json").read_text())
    assert data == {"mcpServers": {"linear": {"type": "http", "url": "https://mcp.linear.app/mcp"}}}
    assert get_config().get_scope("linear") == "project"


def test_existing_project_file_keeps_its_schema(ext_env):
    (ext_env.proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"old": {"command": "echo"}}}))
    install.add_mcp(STDIO, scope="project", connect=False)
    data = json.loads((ext_env.proj / ".mcp.json").read_text())
    assert set(data) == {"mcpServers"} and set(data["mcpServers"]) == {"old", "tool"}

    (ext_env.proj / ".mcp.json").write_text(json.dumps({"servers": {"old": {"type": "stdio", "command": "echo"}}, "auto_connect": []}))
    install.add_mcp("uvx mcp-server-time", scope="project", connect=False)
    data = json.loads((ext_env.proj / ".mcp.json").read_text())
    assert "time" in data["servers"] and data["auto_connect"] == ["time"]


def test_secrets_stay_out_of_the_config_file(ext_env):
    res = install.add_mcp(
        "https://api.example.com/mcp", name="api", scope="project", connect=False,
    )
    assert res["ok"]
    res = install.add_mcp(
        command="npx", args=["-y", "@acme/x"], name="x", scope="project", connect=False,
        env={"ACME_TOKEN": "ghp_secretvalue", "REGION": "eu"},
    )
    text = (ext_env.proj / ".mcp.json").read_text()
    assert "ghp_secretvalue" not in text
    assert "${ACME_TOKEN}" in text and '"REGION": "eu"' in text
    assert secrets.get_secret("ACME_TOKEN") == "ghp_secretvalue"
    assert res["servers"][0]["status"] == "added"


def test_header_credentials_are_stored_not_written(ext_env):
    install.add_mcp(url="https://api.example.com/mcp", name="api", headers={"Authorization": "Bearer tok-123"}, scope="global", connect=False)
    text = (ext_env.home / ".config/harness-agent/mcp.json").read_text()
    assert "tok-123" not in text and "Bearer ${MCP_API_AUTHORIZATION}" in text
    assert secrets.get_secret("MCP_API_AUTHORIZATION") == "tok-123"


def test_missing_key_is_reported_not_connected(ext_env):
    res = install.add_mcp('{"mcpServers": {"brave": {"command": "npx", "args": ["b"], "env": {"BRAVE_API_KEY": "YOUR_API_KEY_HERE"}}}}',
                          scope="project")
    (srv,) = res["servers"]
    assert srv["status"] == "needs_credentials" and srv["missing"] == ["BRAVE_API_KEY"]


def test_set_credentials_unblocks_the_server(ext_env):
    install.add_mcp('{"mcpServers": {"brave": {"command": "npx", "args": ["b"], "env": {"BRAVE_API_KEY": "YOUR_API_KEY_HERE"}}}}', scope="project")
    res = install.set_credentials("brave", {"BRAVE_API_KEY": "real"}, connect=False)
    assert res["status"] == "added" and res["ok"]
    assert install.set_credentials("brave", {"BRAVE_API_KEY": " "}, connect=False)["ok"] is False


def test_adding_twice_reports_exists(ext_env):
    install.add_mcp(STDIO, scope="project", connect=False)
    res = install.add_mcp(STDIO, scope="project", connect=False)
    assert res["servers"][0].get("exists") is True


def test_replace_overwrites(ext_env):
    install.add_mcp(STDIO, scope="project", connect=False)
    install.add_mcp(command="node", args=["x.js"], name="tool", scope="project", connect=False, replace=True)
    data = json.loads((ext_env.proj / ".mcp.json").read_text())
    assert data["mcpServers"]["tool"]["command"] == "node"


def test_confirm_can_veto(ext_env):
    seen = []
    res = install.add_mcp(STDIO, scope="global", connect=False, confirm=lambda d: seen.append(d) or False)
    assert res["servers"][0]["status"] == "denied"
    assert "npx -y @acme/tool-mcp" in seen[0]
    assert not (ext_env.home / ".config/harness-agent/mcp.json").exists()


def test_scope_hint_from_pasted_command(ext_env):
    install.add_mcp("claude mcp add -s project --transport http sentry https://mcp.sentry.dev/mcp", connect=False)
    assert (ext_env.proj / ".mcp.json").exists()


def test_bad_scope_is_rejected(ext_env):
    res = install.add_mcp(STDIO, scope="nowhere", connect=False)
    assert res["ok"] is False


def test_remove_from_project_and_global(ext_env):
    install.add_mcp(STDIO, scope="project", connect=False)
    install.add_mcp("uvx mcp-server-time", scope="global", connect=False)
    assert install.remove_mcp("tool")["ok"]
    assert install.remove_mcp("time")["scope"] == "global"
    assert not install.remove_mcp("tool")["ok"]
    assert json.loads((ext_env.proj / ".mcp.json").read_text())["mcpServers"] == {}


def test_remove_needs_scope_when_ambiguous(ext_env):
    install.add_mcp(STDIO, scope="project", connect=False)
    install.add_mcp(STDIO, scope="global", connect=False)
    assert "both" in install.remove_mcp("tool")["error"]
    assert install.remove_mcp("tool", scope="global")["ok"]


def test_move_between_scopes(ext_env):
    install.add_mcp(STDIO, scope="project", connect=False)
    res = install.move_mcp("tool", "global")
    assert res["ok"], res
    assert get_config().get_scope("tool") == "global"
    assert json.loads((ext_env.proj / ".mcp.json").read_text())["mcpServers"] == {}
    assert not install.move_mcp("tool", "global")["ok"]


def test_describe_servers_shape(ext_env):
    install.add_mcp("https://mcp.linear.app/mcp", scope="project", connect=False)
    install.add_mcp(STDIO, scope="project", connect=False)
    data = install.describe_servers()
    by = {s["name"]: s for s in data["servers"]}
    assert by["linear"]["remote"] and by["linear"]["oauth"] and by["linear"]["removable"]
    assert not by["tool"]["remote"] and by["tool"]["transport"] == "stdio"
    assert by["tool"]["health"]["status"] in ("idle", "warn")
    assert {c["id"] for c in data["catalog"]} >= {"linear", "notion", "github"}
    assert data["project_config_path"].endswith(".mcp.json")
    assert install.describe_servers("lin")["servers"][0]["name"] == "linear"


def test_describe_source_previews_without_writing(ext_env):
    res = install.describe_source("BRAVE_API_KEY=abc npx -y @modelcontextprotocol/server-brave-search")
    assert res["ok"] and res["servers"][0]["credentials"] == []  # a real value is stored on add, nothing to ask
    res = install.describe_source('{"mcpServers": {"b": {"command": "npx", "args": ["b"], "env": {"K": "YOUR_KEY"}}}}')
    assert res["servers"][0]["credentials"] == ["K"]
    assert not install.describe_source("gibberish words here")["ok"]
    assert not (ext_env.proj / ".mcp.json").exists()


def test_reload_after_write_sees_new_server(ext_env):
    install.add_mcp(STDIO, scope="project", connect=False)
    assert "tool" in reload_config().list_servers()


# ── README reading is careful ──────────────────────────────────────────────


def fake_readme(monkeypatch, text):
    class R:
        status_code = 200

    R.text = text
    monkeypatch.setattr("httpx.get", lambda *a, **k: R())


def test_known_repo_uses_catalog_not_readme(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("README must not be fetched for a known server")

    monkeypatch.setattr("httpx.get", boom)
    (sp,) = install.parse_source("https://github.com/upstash/context7")
    assert sp["name"] == "context7" and sp["entry"]["url"] == "https://mcp.context7.com/mcp"


def test_readme_setup_wizard_is_not_a_server(monkeypatch):
    fake_readme(monkeypatch, "```bash\nnpx ctx7 setup\n```\n")
    with pytest.raises(install.ParseError, match="ready-to-run"):
        install.parse_source("https://github.com/acme/wizard")


def test_readme_json_with_comments_is_read(monkeypatch):
    fake_readme(monkeypatch, '```jsonc\n{\n  // add to your config\n  "mcpServers": {\n    "acme": {"url": "https://mcp.acme.dev/mcp",},\n  },\n}\n```\n')
    (sp,) = install.parse_source("https://github.com/acme/acme-mcp")
    assert sp["name"] == "acme" and sp["entry"]["url"] == "https://mcp.acme.dev/mcp"


def test_readme_config_beats_bare_commands(monkeypatch):
    fake_readme(monkeypatch, '```bash\nnpx -y acme-mcp-old\n```\n```json\n{"mcpServers": {"acme": {"command": "npx", "args": ["-y", "acme-mcp"]}}}\n```\n')
    (sp,) = install.parse_source("https://github.com/acme/acme-mcp")
    assert sp["entry"]["args"] == ["-y", "acme-mcp"]


def test_private_or_missing_readme_says_what_to_do(monkeypatch):
    class R:
        status_code = 404
        text = ""

    monkeypatch.setattr("httpx.get", lambda *a, **k: R())
    with pytest.raises(install.ParseError, match="Paste the run command"):
        install.parse_source("https://github.com/acme/private-thing")
