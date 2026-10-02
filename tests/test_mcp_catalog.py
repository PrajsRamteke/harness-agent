"""The MCP marketplace: catalog shape, search, and the Slack-style app setup."""
import json
import urllib.parse

from jarvis.mcp import catalog, install
from jarvis.mcp import secrets as mcp_secrets


def test_every_entry_is_complete_and_unique():
    ids = [c["id"] for c in catalog.CATALOG]
    assert len(ids) == len(set(ids))
    assert set(catalog.POPULAR) <= set(ids)
    for c in catalog.CATALOG:
        assert c["auth"] in catalog.AUTH_LABELS, c["id"]
        assert c["category"] in catalog.CATEGORIES, c["id"]
        assert c["label"] and c["desc"] and c["color"].startswith("#"), c["id"]
        assert bool(c.get("url")) != bool(c.get("command")), c["id"]
        if c["auth"] in ("key", "app"):
            assert c.get("credentials"), c["id"]
            assert set(c.get("fields") or {}) == set(c["credentials"]), c["id"]
            assert (c.get("setup") or {}).get("link"), c["id"]
        if c["auth"] == "app":
            assert isinstance(c.get("oauth"), dict) and c.get("url"), c["id"]
        if c["auth"] == "desktop":
            assert (c.get("setup") or {}).get("steps"), c["id"]
        if c["auth"] == "local":
            assert c.get("command"), c["id"]


def test_search_needs_every_word_and_ranks_prefix_matches_first():
    assert [c["id"] for c in catalog.search("slack")] == ["slack"]
    assert catalog.search("jira")[0]["id"] == "atlassian"   # keywords count
    assert all("postgres" in (c["keywords"] + c["desc"]).lower() for c in catalog.search("postgres"))
    assert catalog.search("cloud")[0]["id"].startswith("cloudflare")
    assert not catalog.search("slack figma")
    # categories
    popular = [c["id"] for c in catalog.search("", "Popular")]
    assert popular == catalog.POPULAR
    assert {c["category"] for c in catalog.search("", "Design")} == {"Design"}
    # the auth kind is searchable too ("api key", "local")
    assert {c["auth"] for c in catalog.search("api key")} == {"key"}


def test_slack_manifest_matches_what_slack_asks_for(monkeypatch):
    monkeypatch.setenv("HARNESS_MCP_OAUTH_PORT", "40123")
    url = catalog.slack_manifest_url()
    assert url.startswith("https://api.slack.com/apps?new_app=1&manifest_json=")
    manifest = json.loads(urllib.parse.unquote(url.split("manifest_json=", 1)[1]))
    assert manifest["oauth_config"]["redirect_urls"] == ["http://localhost:40123/callback"]
    # every scope mcp.slack.com's protected-resource metadata lists
    assert len(manifest["oauth_config"]["scopes"]["user"]) == 30
    assert "chat:write" in manifest["oauth_config"]["scopes"]["user"]
    pub = catalog.public_item(catalog.lookup("slack"))
    assert pub["setup"]["link"] == url and pub["redirect_url"] == "http://localhost:40123/callback"
    assert pub["fields"]["SLACK_CLIENT_ID"]["secret"] is False
    assert pub["fields"]["SLACK_CLIENT_SECRET"]["secret"] is True


def test_adding_slack_keeps_secrets_out_of_the_config(ext_env, monkeypatch):
    monkeypatch.setattr(install.mcp_registry, "connect", lambda name, cfg, **kw: None)
    monkeypatch.setattr(install.mcp_registry, "get_server_tools", lambda name: [])

    res = install.add_mcp("slack", connect=False)
    assert res["servers"][0]["status"] == "needs_credentials"
    assert res["servers"][0]["missing"] == ["SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"]
    assert "Pre-filled link: https://api.slack.com/apps?new_app=1" in res["message"]

    res = install.add_mcp("slack", credentials={"SLACK_CLIENT_ID": "111.222", "SLACK_CLIENT_SECRET": "shh"},
                          replace=True, connect=False)
    assert res["servers"][0]["status"] == "added"
    raw = ext_env.home.joinpath(".config/harness-agent/mcp.json").read_text()
    assert "shh" not in raw and "111.222" not in raw
    entry = json.loads(raw)["servers"]["slack"]
    assert entry["url"] == "https://mcp.slack.com/mcp"
    assert entry["oauth"] == {"clientId": "${SLACK_CLIENT_ID}", "clientSecret": "${SLACK_CLIENT_SECRET}",
                              "callbackPort": catalog.oauth_port()}
    assert mcp_secrets.get_secret("SLACK_CLIENT_SECRET") == "shh"


def test_marketplace_rows_know_what_is_installed(ext_env, monkeypatch):
    monkeypatch.setattr(install.mcp_registry, "connect", lambda name, cfg, **kw: None)
    monkeypatch.setattr(install.mcp_registry, "get_server_tools", lambda name: [])
    install.add_mcp("linear", connect=False)
    rows = {r["id"]: r for r in install.describe_market()}
    assert rows["linear"]["installed"] == "linear" and rows["notion"]["installed"] == ""
    listing = install.describe_servers()
    assert listing["categories"] == catalog.CATEGORIES
    srv = next(s for s in listing["servers"] if s["name"] == "linear")
    assert srv["catalog_id"] == "linear" and srv["label"] == "Linear" and srv["color"]


def test_set_oauth_app_for_a_host_without_self_sign_up(ext_env, monkeypatch):
    monkeypatch.setattr(install.mcp_registry, "connect", lambda name, cfg, **kw: None)
    monkeypatch.setattr(install.mcp_registry, "get_server_tools", lambda name: [])
    install.add_mcp("https://mcp.example.com/mcp", connect=False)
    res = install.set_oauth_app("example", "cid-1", "top-secret", connect=False)
    assert res["ok"], res
    raw = ext_env.home.joinpath(".config/harness-agent/mcp.json").read_text()
    entry = json.loads(raw)["servers"]["example"]
    assert entry["oauth"]["clientId"] == "cid-1"
    assert entry["oauth"]["clientSecret"].startswith("${") and "top-secret" not in raw
    assert install.set_oauth_app("example", "  ")["ok"] is False
    assert install.set_oauth_app("nope", "x")["ok"] is False


def test_figma_points_at_the_desktop_app_and_the_old_address_explains_why():
    from jarvis.mcp.registry import _describe_remote_error
    from mcp.client.auth import OAuthRegistrationError

    fig = catalog.lookup("figma")
    assert fig["auth"] == "desktop" and fig["url"].startswith("http://127.0.0.1:3845")
    assert catalog.for_server("figma", {"url": "https://mcp.figma.com/mcp"}) is fig
    kind, msg = _describe_remote_error([OAuthRegistrationError("Registration failed: 403 Forbidden")],
                                       {"url": "https://mcp.figma.com/mcp"})
    assert "desktop app" in msg
    kind, msg = _describe_remote_error([OAuthRegistrationError("Registration failed: 403 Forbidden")],
                                       {"url": "https://other.example.com/mcp"})
    assert "approved" in msg


def test_an_app_without_its_id_never_falls_back_to_self_sign_up(ext_env, monkeypatch):
    from jarvis.mcp.registry import mcp_registry

    monkeypatch.delenv("NO_SUCH_CLIENT_ID", raising=False)
    err = mcp_registry.connect("noid", {"type": "http", "url": "https://mcp.slack.com/mcp",
                                        "oauth": {"clientId": "${NO_SUCH_CLIENT_ID}"}})
    assert err.startswith("needs NO_SUCH_CLIENT_ID")
    assert mcp_registry.get_server_health("noid")["status"] == "failed"


def test_server_cards_get_keywords_and_the_old_address_note(ext_env):
    pub = catalog.public_item(catalog.lookup("atlassian"))
    assert "jira" in pub["keywords"]
    fields = install._catalog_fields("figma", {"url": "https://mcp.figma.com/mcp"})
    assert fields["catalog_id"] == "figma" and "desktop app" in fields["alt_note"]
    assert install._catalog_fields("figma", {"url": "http://127.0.0.1:3845/mcp"}).get("alt_note", "") == ""
