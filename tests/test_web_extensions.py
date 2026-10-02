"""Web remote: adding / removing MCP servers and skills over HTTP (extensions_api).

The real handler on a real socket, a scratch HOME + project folder (``ext_env``),
no network and no MCP server process: stdio entries are added with
``connect=False`` and skills come from a local folder.
"""
from __future__ import annotations

import json
import queue
import stat
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jarvis import state
from jarvis.web import handler as web_handler
from jarvis.web.bridge import WebBridge
from jarvis.web.server import start_web_server, stop_web_server

STATIC = Path(web_handler.__file__).with_name("static")


@pytest.fixture
def remote(ext_env):
    bridge = WebBridge()
    server, _urls, port = start_web_server(bridge=bridge, app=None, port=28941, host="127.0.0.1")

    def call(path, body=None, *, token=True):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST" if body is not None else "GET")
        if token:
            req.add_header("Authorization", f"Bearer {bridge.token}")
        data = None
        if body is not None:
            req.add_header("Content-Type", "application/json")
            data = json.dumps(body).encode()
        with urllib.request.urlopen(req, data=data, timeout=10) as res:
            return json.loads(res.read())

    yield ext_env, bridge, call
    stop_web_server(server, bridge)


def drain(sub):
    out = []
    while True:
        try:
            out.append(json.loads(sub.get_nowait()))
        except queue.Empty:
            return out


def write_skill(folder: Path, name: str, desc: str = "Does a thing when asked.") -> None:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n\nSteps.\n")


# ─── MCP: list + parse ─────────────────────────────────────────────────────


def test_mcp_list_shape_and_auth(remote):
    env, _bridge, call = remote
    data = call("/api/mcp")
    assert data["servers"] == [] and data["global_mcp"] is False
    assert data["project_config_path"] == str(env.proj / ".mcp.json")
    assert data["global_config_path"].endswith("harness-agent/mcp.json")
    assert data["pending_auth"] == []
    ids = {c["id"] for c in data["catalog"]}
    assert {"linear", "notion", "github"} <= ids
    assert all({"id", "label", "kind"} <= set(c) for c in data["catalog"])
    with pytest.raises(urllib.error.HTTPError) as err:
        call("/api/mcp", token=False)
    assert err.value.code == 401


def test_parse_understands_what_people_paste(remote):
    _env, _bridge, call = remote

    hosted = call("/api/mcp/parse", {"source": "https://mcp.example.com/mcp"})
    (sv,) = hosted["servers"]
    assert hosted["ok"] and sv["transport"] == "http" and sv["local"] is False
    assert sv["endpoint"] == "https://mcp.example.com/mcp"

    local = call("/api/mcp/parse", {"source": "npx -y @modelcontextprotocol/server-memory"})
    (sv,) = local["servers"]
    assert sv["local"] is True and sv["name"] == "memory" and sv["endpoint"].startswith("npx -y")

    line = call("/api/mcp/parse", {"source": "claude mcp add --transport sse -s project asana https://mcp.asana.com/sse"})
    (sv,) = line["servers"]
    assert (sv["name"], sv["transport"], sv["scope_hint"]) == ("asana", "sse", "project")

    snippet = {"mcpServers": {"brave": {"command": "npx", "args": ["-y", "brave"], "env": {"BRAVE_API_KEY": "YOUR_API_KEY"}}}}
    keyed = call("/api/mcp/parse", {"source": json.dumps(snippet)})
    assert keyed["servers"][0]["credentials"] == ["BRAVE_API_KEY"]

    bad = call("/api/mcp/parse", {"source": "hello world"})
    assert bad["ok"] is False and "Not sure" in bad["error"]
    assert call("/api/mcp/parse", {"source": ""})["ok"] is False
    # a dry run reads only: no fresh list glued on
    assert "mcp" not in hosted


# ─── MCP: add / remove / move ──────────────────────────────────────────────


def test_add_to_project_keeps_secrets_out_of_the_file(remote):
    env, bridge, call = remote
    sub = bridge.subscribe()
    snippet = {"mcpServers": {"tool": {"command": "echo", "args": ["hi"], "env": {"API_TOKEN": "s3cret-value", "MODE": "fast"}}}}
    res = call("/api/mcp/add", {"source": json.dumps(snippet), "scope": "project", "connect": False})

    assert res["ok"]
    (sv,) = res["servers"]
    assert (sv["name"], sv["scope"], sv["status"]) == ("tool", "project", "added")
    written = (env.proj / ".mcp.json").read_text()
    assert "s3cret-value" not in written
    entry = json.loads(written)["mcpServers"]["tool"]
    assert entry["env"] == {"API_TOKEN": "${API_TOKEN}", "MODE": "fast"}
    secrets_file = env.home / ".config" / "harness-agent" / "mcp_secrets.json"
    assert json.loads(secrets_file.read_text()) == {"API_TOKEN": "s3cret-value"}
    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600

    # the reply carries the fresh list, and other pages get an event
    assert [s["name"] for s in res["mcp"]["servers"]] == ["tool"]
    (evt,) = [e for e in drain(sub) if e["type"] == "mcp"]
    assert evt["data"]["event"] == "add"

    again = call("/api/mcp/add", {"source": json.dumps(snippet), "scope": "project", "connect": False})
    assert again["servers"][0]["exists"] is True
    assert len(json.loads((env.proj / ".mcp.json").read_text())["mcpServers"]) == 1


def test_add_global_turns_the_scope_on_and_flags_missing_keys(remote):
    env, _bridge, call = remote
    assert state.global_mcp is False
    snippet = {"mcpServers": {"brave": {"command": "npx", "args": ["-y", "brave"], "env": {"BRAVE_API_KEY": "YOUR_API_KEY"}}}}
    res = call("/api/mcp/add", {"source": json.dumps(snippet), "scope": "global", "connect": False})

    (sv,) = res["servers"]
    assert sv["status"] == "needs_credentials" and sv["missing"] == ["BRAVE_API_KEY"]
    assert "turned it on" in sv["scope_note"]
    assert res["global_mcp"] is True and state.global_mcp is True
    cfg = json.loads((env.home / ".config" / "harness-agent" / "mcp.json").read_text())
    assert cfg["servers"]["brave"]["env"] == {"BRAVE_API_KEY": "${BRAVE_API_KEY}"}
    assert cfg["auto_connect"] == ["brave"]
    listed = call("/api/mcp")["servers"]
    assert listed[0]["needs_credentials"] == ["BRAVE_API_KEY"]
    assert listed[0]["scope"] == "global" and listed[0]["removable"] is True


def test_credentials_are_stored_and_the_server_reports_its_state(remote):
    env, _bridge, call = remote
    call("/api/mcp/add", {"source": json.dumps({"mcpServers": {"ghost": {
        "command": "no-such-binary-for-jarvis-tests", "env": {"GHOST_KEY": "YOUR_KEY"}}}}), "scope": "project", "connect": False})
    assert call("/api/mcp/credentials", {"name": "ghost", "values": {}})["ok"] is False
    res = call("/api/mcp/credentials", {"name": "ghost", "values": {"GHOST_KEY": "abc123"}})
    # the key is saved; connecting a missing program fails with its own message
    assert json.loads((env.home / ".config" / "harness-agent" / "mcp_secrets.json").read_text()) == {"GHOST_KEY": "abc123"}
    assert res["ok"] is False and res["status"] == "failed" and res["error"]
    listed = call("/api/mcp")["servers"][0]
    assert listed["needs_credentials"] == [] and listed["health"]["status"] == "failed"


def test_move_and_remove(remote):
    env, _bridge, call = remote
    call("/api/mcp/add", {"source": "npx -y @modelcontextprotocol/server-memory", "scope": "project", "connect": False})
    proj_file, glob_file = env.proj / ".mcp.json", env.home / ".config" / "harness-agent" / "mcp.json"

    moved = call("/api/mcp/move", {"name": "memory", "scope": "global"})
    assert moved["ok"] and "memory" in json.loads(glob_file.read_text())["servers"]
    assert "memory" not in json.loads(proj_file.read_text()).get("mcpServers", {})
    assert call("/api/mcp/move", {"name": "memory", "scope": "global"})["ok"] is False
    assert call("/api/mcp/move", {"name": "memory", "scope": "nowhere"})["ok"] is False

    assert call("/api/mcp/remove", {"name": "nope"})["ok"] is False
    removed = call("/api/mcp/remove", {"name": "memory", "scope": "global"})
    assert removed["ok"] and removed["mcp"]["servers"] == []
    assert json.loads(glob_file.read_text())["servers"] == {}


def test_sign_in_routes_for_a_server_that_isnt_waiting(remote):
    _env, _bridge, call = remote
    status = call("/api/mcp/auth?name=nobody")
    assert status["ok"] and status["connected"] is False and status["status"] == "none"
    paste = call("/api/mcp/auth/paste", {"name": "nobody", "address": "http://localhost:33418/callback?code=x&state=y"})
    assert paste["ok"] is False and paste["expired"] is True
    assert call("/api/mcp/auth/start", {"name": "nobody"})["ok"] is False
    assert call("/api/mcp/connect", {"name": "nobody"})["ok"] is False


# ─── MCP: marketplace ──────────────────────────────────────────────────────


def test_marketplace_rows_carry_what_the_dialog_shows(remote):
    _env, _bridge, call = remote
    data = call("/api/mcp")
    assert "Popular" in data["categories"] and "Communication" in data["categories"]
    assert data["oauth_redirect_url"].startswith("http://localhost:") and data["oauth_redirect_url"].endswith("/callback")
    rows = {c["id"]: c for c in data["catalog"]}
    assert len(rows) >= 40
    for c in rows.values():
        assert {"id", "label", "desc", "category", "auth", "color", "monogram", "installed", "popular"} <= set(c), c["id"]
        assert c["installed"] == ""
    slack = rows["slack"]
    assert slack["auth"] == "app" and slack["popular"] is True
    assert slack["fields"]["SLACK_CLIENT_ID"]["label"] == "Client ID"
    assert slack["fields"]["SLACK_CLIENT_SECRET"]["secret"] is True
    assert slack["setup"]["link"].startswith("https://api.slack.com/apps?new_app=1&manifest_json=")
    assert slack["setup"]["steps"] and slack["redirect_url"] == data["oauth_redirect_url"]
    assert rows["github"]["auth"] == "key" and rows["github"]["setup"]["link"].startswith("https://github.com/")

    # Added from the marketplace: the row says so, and the server card borrows its name and tile.
    call("/api/mcp/add", {"source": "memory", "scope": "project", "connect": False})
    data = call("/api/mcp")
    assert {c["id"]: c for c in data["catalog"]}["memory"]["installed"] == "memory"
    (sv,) = data["servers"]
    assert sv["catalog_id"] == "memory" and sv["label"] == "Memory" and sv["color"].startswith("#")


def test_marketplace_slack_keeps_its_app_secret_out_of_the_file(remote):
    env, _bridge, call = remote
    res = call("/api/mcp/add", {
        "source": "slack", "scope": "global", "connect": False,
        "credentials": {"SLACK_CLIENT_ID": "111.222", "SLACK_CLIENT_SECRET": "shh-secret"},
    })
    assert res["ok"], res
    raw = (env.home / ".config" / "harness-agent" / "mcp.json").read_text()
    assert "shh-secret" not in raw and "111.222" not in raw
    oauth = json.loads(raw)["servers"]["slack"]["oauth"]
    assert oauth["clientId"] == "${SLACK_CLIENT_ID}" and oauth["clientSecret"] == "${SLACK_CLIENT_SECRET}"
    assert isinstance(oauth["callbackPort"], int)
    secrets = json.loads((env.home / ".config" / "harness-agent" / "mcp_secrets.json").read_text())
    assert secrets["SLACK_CLIENT_ID"] == "111.222" and secrets["SLACK_CLIENT_SECRET"] == "shh-secret"


def test_oauth_app_route_saves_the_app_for_a_hosted_server(remote, monkeypatch):
    import jarvis.mcp.install as install

    env, _bridge, call = remote
    tried = []
    monkeypatch.setattr(install.mcp_registry, "connect", lambda name, cfg, **kw: tried.append(cfg) or "no network in tests")
    call("/api/mcp/add", {"source": "https://mcp.example.com/mcp", "name": "hub", "scope": "project", "connect": False})

    assert call("/api/mcp/app", {"name": "hub", "client_id": "  "})["ok"] is False
    assert call("/api/mcp/app", {"name": "nobody", "client_id": "x"})["ok"] is False
    res = call("/api/mcp/app", {"name": "hub", "client_id": "app-123", "client_secret": "s3cr3t"})
    assert res["status"] == "failed" and tried  # saved, then it tried to connect with it
    raw = (env.proj / ".mcp.json").read_text()
    assert "s3cr3t" not in raw
    oauth = json.loads(raw)["mcpServers"]["hub"]["oauth"]
    assert oauth["clientId"] == "app-123" and oauth["clientSecret"].startswith("${")
    listed = call("/api/mcp")["servers"][0]
    assert listed["oauth_app"] is True


def test_marketplace_is_wired_in_the_page():
    mcp = (STATIC / "js" / "mcp.js").read_text()
    for ident in ('id="mk-q"', 'id="mk-cats"', 'id="mk-list"', 'id="mk-custom"', "'mcp/app'", "mk-tabs"):
        assert ident in mcp, ident
    assert "chipsHtml" not in mcp  # the old wall of quick-add chips is gone
    for kind in ("oauth", "open", "key", "app", "desktop", "local"):
        assert f"  {kind}: {{ label:" in mcp, kind
    assert "pickerArg: 'market'" in (STATIC / "js" / "catalog.js").read_text()
    assert "item.pickerArg" in (STATIC / "js" / "palette.js").read_text()
    css = (STATIC / "css" / "extensions.css").read_text()
    assert ".mk-list" in css and ".mk-tile" in css and ".ex-errline" in css


# ─── Skills ────────────────────────────────────────────────────────────────


@pytest.fixture
def skill_source(tmp_path):
    src = tmp_path / "pack"
    write_skill(src / "alpha", "alpha", "Alpha does the first thing when asked.")
    write_skill(src / "beta", "beta", "Beta does the second thing when asked.")
    write_skill(src / "odd", "Odd Name", "The name isn't lowercase-hyphen.")
    return src


def test_inspect_lists_what_a_folder_holds(remote, skill_source):
    _env, _bridge, call = remote
    res = call("/api/skills/inspect", {"source": str(skill_source)})

    assert res["ok"] is True
    names = [s["name"] for s in res["skills"]]      # a list of found skills, not the installed listing
    assert names == ["alpha", "beta", "odd-name"]
    odd = res["skills"][2]
    assert odd["usable"] is True and odd["problems"]
    assert all(s["installed"] == "" for s in res["skills"])
    assert call("/api/skills/inspect", {"source": str(skill_source / "alpha")})["skills"][0]["name"] == "alpha"

    missing = call("/api/skills/inspect", {"source": str(skill_source.parent / "nope")})
    assert missing["ok"] is False and missing["error"]
    empty = skill_source.parent / "empty"
    empty.mkdir()
    assert "No skills found" in call("/api/skills/inspect", {"source": str(empty)})["error"]


def test_install_project_then_global_move_and_remove(remote, skill_source):
    env, bridge, call = remote
    sub = bridge.subscribe()

    choice = call("/api/skills/install", {"source": str(skill_source), "scope": "project"})
    assert choice["ok"] is False and choice["needs_choice"] is True
    assert not (env.proj / ".harness" / "skills").exists()

    res = call("/api/skills/install", {"source": str(skill_source), "scope": "project", "names": ["alpha", "odd-name"]})
    assert res["ok"] and [i["name"] for i in res["installed"]] == ["alpha", "odd-name"]
    md = (env.proj / ".harness" / "skills" / "alpha" / "SKILL.md")
    assert md.is_file()
    assert "name: odd-name" in (env.proj / ".harness" / "skills" / "odd-name" / "SKILL.md").read_text()
    assert [e for e in drain(sub) if e["type"] == "skills"]

    listed = call("/api/skills")
    by_name = {s["name"]: s for s in listed["skills"]}
    assert set(by_name) == {"alpha", "odd-name"}
    assert by_name["alpha"]["scope"] == "project" and by_name["alpha"]["managed"] is True
    assert by_name["alpha"]["origin"] == str(skill_source) and by_name["alpha"]["active"] is True
    assert res["skills"]["skills"] and listed["project_dir"].endswith(".harness/skills")

    again = call("/api/skills/install", {"source": str(skill_source), "scope": "project", "names": ["alpha"]})
    assert again["ok"] is False and "already installed" in again["error"]

    # global: hidden until the scope is on — installing turns it on and says so
    assert state.global_skills is False
    glob = call("/api/skills/install", {"source": str(skill_source), "scope": "global", "names": ["beta"]})
    assert glob["ok"] and "turned them on" in glob["scope_note"] and state.global_skills is True
    assert (env.home / ".harness" / "skills" / "beta" / "SKILL.md").is_file()

    moved = call("/api/skills/move", {"name": "alpha", "scope": "global"})
    assert moved["ok"] and (env.home / ".harness" / "skills" / "alpha").is_dir()
    assert not (env.proj / ".harness" / "skills" / "alpha").exists()
    assert call("/api/skills/move", {"name": "alpha", "scope": "global"})["ok"] is False

    removed = call("/api/skills/remove", {"name": "beta"})
    assert removed["ok"] and not (env.home / ".harness" / "skills" / "beta").exists()
    assert call("/api/skills/remove", {"name": "beta"})["ok"] is False
    assert {s["name"] for s in call("/api/skills")["skills"]} == {"alpha", "odd-name"}

    view = call("/api/skills/alpha")
    assert view["name"] == "alpha" and "Steps." in view["content"]


def test_skills_outside_jarvis_folders_are_not_removable(remote):
    env, _bridge, call = remote
    write_skill(env.proj / ".claude" / "skills" / "theirs", "theirs")
    row = next(s for s in call("/api/skills")["skills"] if s["name"] == "theirs")
    assert row["managed"] is False
    res = call("/api/skills/remove", {"name": "theirs"})
    assert res["ok"] is False and "another tool" in res["error"]
    assert (env.proj / ".claude" / "skills" / "theirs" / "SKILL.md").is_file()


# ─── Front end wiring ──────────────────────────────────────────────────────


def test_dialogs_banner_and_events_are_wired_in_the_page():
    html = (STATIC / "index.html").read_text()
    for ident in ("mcp", "mcp-body", "skills", "skills-body", "mcp-chip", "mcp-dot"):
        assert f'id="{ident}"' in html, ident
    assert "/static/css/extensions.css" in html
    events = (STATIC / "js" / "events.js").read_text()
    assert "case 'mcp':" in events and "case 'skills':" in events
    app = (STATIC / "js" / "app.js").read_text()
    assert "initMcp()" in app and "initSkills()" in app
    pickers = (STATIC / "js" / "pickers.js").read_text()
    assert "openMcp(arg)" in pickers and "openSkills()" in pickers
