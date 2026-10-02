"""The MCP marketplace — well-known servers, one click (or one word) to add.

Best effort: these are the vendors' published endpoints (each answered an MCP
``initialize`` when this list was last checked), and endpoints move. A wrong one
just fails to connect with a plain message; nothing here is trusted.

``auth`` says how a server signs in, and drives the buttons the UIs show:

* ``oauth`` — hosted; Jarvis opens the vendor's sign-in page (dynamic client
  registration), nothing to paste.
* ``open``  — hosted, no account needed.
* ``key``   — needs a token / API key (``credentials`` names the variables).
* ``app``   — hosted with sign-in, but the vendor only lets *registered* apps
  sign in (no dynamic registration — Slack). The user creates their own app
  once (``setup`` walks them through it, with a pre-filled link where the
  vendor has one) and pastes its Client ID / Secret.
* ``desktop`` — served by a desktop app on this computer (Figma); ``setup``
  says how to switch it on, nothing to paste.
* ``local`` — runs a command on this computer (npx / uvx).

``fields`` gives the credentials friendly labels, hints and whether to hide the
typed value. Values are always stored in ``mcp_secrets`` and referenced as
``${VAR}`` in the config — never written to a config file.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from typing import Any

from .auth import CALLBACK_PATH, DEFAULT_PORT

CATEGORIES = [
    "Popular",
    "Communication",
    "Productivity",
    "Developer tools",
    "Design",
    "Cloud & data",
    "Payments",
    "Analytics",
    "Search & web",
    "On this computer",
]

# Shown first when nothing is typed (and as the "Popular" category).
POPULAR = [
    "slack", "notion", "linear", "github", "atlassian", "figma", "clickup", "canva",
    "sentry", "supabase", "stripe", "playwright",
]

# Every user scope Slack's MCP server asks for (its protected-resource metadata).
# The app must request the same set, or Slack refuses the sign-in.
_SLACK_USER_SCOPES = [
    "canvases:read", "canvases:write", "channels:history", "channels:read", "channels:write",
    "chat:write", "emoji:read", "files:read", "files:write", "groups:history", "groups:read",
    "groups:write", "im:history", "im:read", "im:write", "lists:read", "lists:write",
    "mpim:history", "mpim:read", "mpim:write", "reactions:read", "reactions:write",
    "search:read.files", "search:read.im", "search:read.mpim", "search:read.private",
    "search:read.public", "search:read.users", "users:read", "users:read.email",
]


def oauth_port() -> int:
    """The loopback port pre-registered apps send the browser back to."""
    try:
        return int(os.getenv("HARNESS_MCP_OAUTH_PORT", "") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def oauth_redirect_url() -> str:
    return f"http://localhost:{oauth_port()}{CALLBACK_PATH}"


def slack_manifest() -> dict[str, Any]:
    return {
        "display_information": {
            "name": "Jarvis",
            "description": "Lets Jarvis search, read and send Slack messages for you through Slack's MCP server.",
            "background_color": "#1d1f24",
        },
        "oauth_config": {
            "redirect_urls": [oauth_redirect_url()],
            "scopes": {"user": list(_SLACK_USER_SCOPES)},
        },
        "settings": {
            "org_deploy_enabled": False,
            "socket_mode_enabled": False,
            "token_rotation_enabled": False,
        },
    }


def slack_manifest_url() -> str:
    """Slack's "create an app from this manifest" page, already filled in."""
    blob = json.dumps(slack_manifest(), separators=(",", ":"))
    return "https://api.slack.com/apps?new_app=1&manifest_json=" + urllib.parse.quote(blob, safe="")


def _hosted(id_: str, label: str, desc: str, category: str, url: str, color: str, **extra: Any) -> dict[str, Any]:
    return {"id": id_, "label": label, "desc": desc, "category": category, "url": url,
            "auth": extra.pop("auth", "oauth"), "color": color, **extra}


def _local(id_: str, label: str, desc: str, command: str, args: list[str], color: str, **extra: Any) -> dict[str, Any]:
    return {"id": id_, "label": label, "desc": desc, "category": extra.pop("category", "On this computer"),
            "command": command, "args": args, "auth": extra.pop("auth", "local"), "color": color, **extra}


CATALOG: list[dict[str, Any]] = [
    # ── communication ────────────────────────────────────────────────────
    _hosted(
        "slack", "Slack", "Search, read and send messages, threads and canvases", "Communication",
        "https://mcp.slack.com/mcp", "#611f69",
        auth="app",
        oauth={"clientId": "${SLACK_CLIENT_ID}", "clientSecret": "${SLACK_CLIENT_SECRET}"},
        credentials=["SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"],
        fields={
            "SLACK_CLIENT_ID": {"label": "Client ID", "hint": "Basic Information → App Credentials", "secret": False,
                                "placeholder": "paste it here — looks like 1234567890.123…"},
            "SLACK_CLIENT_SECRET": {"label": "Client Secret", "hint": "Basic Information → App Credentials → Show",
                                    "secret": True},
        },
        setup={
            "title": "Slack needs its own app — a one-time, two-minute setup",
            "why": "Slack only lets registered apps sign in to its MCP server, so you create a small private "
                   "app in your workspace. Jarvis fills in everything for you.",
            "link_label": "Create the Slack app",
            "link": "slack_manifest",
            "steps": [
                "Open the pre-filled link, pick your workspace, then Next → Create.",
                "In the new app, open Agents (left side) and switch on “Slack Model Context Protocol (MCP) Server”.",
                "Open Basic Information → App Credentials and copy the Client ID and Client Secret here.",
            ],
            "note": "Some workspaces need an admin to approve new apps before you can sign in.",
        },
        docs="https://docs.slack.dev/ai/slack-mcp-server",
        keywords="chat messages channels dm canvas workspace",
    ),
    _hosted("intercom", "Intercom", "Conversations, contacts and help-center content", "Communication",
            "https://mcp.intercom.com/mcp", "#1f8ded", keywords="support customers inbox"),
    _hosted("fireflies", "Fireflies", "Meeting transcripts and summaries", "Communication",
            "https://api.fireflies.ai/mcp", "#8a3ffc", keywords="meetings notes transcripts calls"),
    _hosted("granola", "Granola", "Your meeting notes", "Communication",
            "https://mcp.granola.ai/mcp", "#3f7d3f", keywords="meetings notes"),

    # ── productivity ─────────────────────────────────────────────────────
    _hosted("notion", "Notion", "Search, read and edit pages and databases", "Productivity",
            "https://mcp.notion.com/mcp", "#2f2f2f", keywords="docs wiki pages notes"),
    _hosted("linear", "Linear", "Issues, projects and cycles", "Productivity",
            "https://mcp.linear.app/mcp", "#5e6ad2", keywords="issues tickets tracker"),
    _hosted("atlassian", "Atlassian", "Jira issues and Confluence pages", "Productivity",
            "https://mcp.atlassian.com/v1/sse", "#0c66e4", keywords="jira confluence tickets wiki"),
    _hosted("clickup", "ClickUp", "Tasks, docs and lists", "Productivity",
            "https://mcp.clickup.com/mcp", "#7b68ee", keywords="tasks project management"),
    _hosted("asana", "Asana", "Tasks and projects", "Productivity",
            "https://mcp.asana.com/sse", "#f06a6a", keywords="tasks project management"),
    _hosted("monday", "monday.com", "Boards, items and updates", "Productivity",
            "https://mcp.monday.com/mcp", "#ff3d57", keywords="boards tasks project management"),
    _hosted("todoist", "Todoist", "Your tasks and projects", "Productivity",
            "https://ai.todoist.net/mcp", "#e44332", keywords="todo tasks"),
    _hosted("airtable", "Airtable", "Bases, tables and records", "Productivity",
            "https://mcp.airtable.com/mcp", "#2d7ff9", keywords="database spreadsheet"),
    _hosted("miro", "Miro", "Boards, stickies and diagrams", "Productivity",
            "https://mcp.miro.com/", "#ffd02f", keywords="whiteboard diagrams"),
    _hosted("zapier", "Zapier", "Run actions in 8,000+ apps", "Productivity",
            "https://mcp.zapier.com/api/mcp/mcp", "#ff4f00", keywords="automation gmail sheets calendar"),
    _hosted("dropbox", "Dropbox", "Find and read your files", "Productivity",
            "https://mcp.dropbox.com/mcp", "#0061fe", keywords="files storage"),
    _hosted("calcom", "Cal.com", "Bookings and availability", "Productivity",
            "https://mcp.cal.com/mcp", "#292929", keywords="calendar scheduling meetings"),
    _hosted("attio", "Attio", "CRM records and lists", "Productivity",
            "https://mcp.attio.com/mcp", "#1a1d21", keywords="crm sales"),
    _hosted("close", "Close", "CRM leads and activities", "Productivity",
            "https://mcp.close.com/mcp", "#1463ff", keywords="crm sales"),

    # ── developer tools ──────────────────────────────────────────────────
    _hosted(
        "github", "GitHub", "Repos, issues and pull requests", "Developer tools",
        "https://api.githubcopilot.com/mcp/", "#24292f",
        auth="key", repo="github/github-mcp-server",
        headers={"Authorization": "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}"},
        credentials=["GITHUB_PERSONAL_ACCESS_TOKEN"],
        fields={"GITHUB_PERSONAL_ACCESS_TOKEN": {
            "label": "Personal access token", "hint": "github.com → Settings → Developer settings → Tokens",
            "secret": True, "placeholder": "github_pat_…"}},
        setup={"link_label": "Create a token", "link": "https://github.com/settings/personal-access-tokens/new"},
        note="Needs a GitHub personal access token.",
        keywords="git code pr issues",
    ),
    _hosted("gitlab", "GitLab", "Projects, issues and merge requests", "Developer tools",
            "https://gitlab.com/api/v4/mcp", "#fc6d26", keywords="git code mr issues"),
    _hosted("sentry", "Sentry", "Errors, issues and performance", "Developer tools",
            "https://mcp.sentry.dev/mcp", "#362d59", repo="getsentry/sentry-mcp", keywords="errors monitoring"),
    _hosted("context7", "Context7", "Up-to-date docs for any library", "Developer tools",
            "https://mcp.context7.com/mcp", "#059669", auth="open", repo="upstash/context7",
            keywords="docs libraries api reference"),
    _hosted("deepwiki", "DeepWiki", "Ask questions about any GitHub repo", "Developer tools",
            "https://mcp.deepwiki.com/mcp", "#0b7285", auth="open", keywords="docs github repos"),
    _hosted("huggingface", "Hugging Face", "Models, datasets and Spaces", "Developer tools",
            "https://huggingface.co/mcp", "#ff9d00", auth="open", keywords="ml ai models"),
    _hosted("postman", "Postman", "Collections, requests and APIs", "Developer tools",
            "https://mcp.postman.com/mcp", "#ff6c37", keywords="api http"),
    _hosted("semgrep", "Semgrep", "Scan code for security issues", "Developer tools",
            "https://mcp.semgrep.ai/mcp", "#1b2f3a", keywords="security scan"),
    _hosted("jam", "Jam", "Bug reports with replays and logs", "Developer tools",
            "https://mcp.jam.dev/mcp", "#e8467c", keywords="bugs qa"),
    _hosted("buildkite", "Buildkite", "Pipelines, builds and logs", "Developer tools",
            "https://mcp.buildkite.com/mcp", "#14cc80", keywords="ci builds"),
    _hosted("grafana", "Grafana", "Dashboards, alerts and queries", "Developer tools",
            "https://mcp.grafana.com/mcp", "#f46800", keywords="monitoring observability"),

    # ── design ───────────────────────────────────────────────────────────
    # mcp.figma.com answers 403 to sign-ups from apps Figma hasn't approved, so
    # Jarvis uses the server built into the Figma desktop app instead.
    _hosted(
        "figma", "Figma", "Designs, components and variables — via the Figma app", "Design",
        "http://127.0.0.1:3845/mcp", "#a259ff", auth="desktop",
        alt_urls=["https://mcp.figma.com/mcp"],
        alt_note="Figma's hosted server only accepts apps Figma has approved. Remove this one and add Figma "
                 "from the marketplace: “Figma” uses the desktop app, “Figma (token)” works with just a browser.",
        setup={
            "title": "Turn on Figma's desktop MCP server",
            "why": "Figma only lets apps it has approved use its hosted server, so Jarvis talks to the Figma "
                   "desktop app on this computer instead — no sign-in needed. No desktop app? Use “Figma (token)”.",
            "steps": [
                "Open the Figma desktop app (latest version) and any design file.",
                "Switch to Dev Mode (Shift+D).",
                "In the inspect panel's MCP server section, click “Enable desktop MCP server”, then Connect here.",
            ],
            "link_label": "Figma's guide",
            "link": "https://developers.figma.com/docs/figma-mcp-server/local-server-installation/",
        },
        keywords="design ui dev mode",
    ),
    # No desktop app: Framelink (MIT, github.com/GLips/Figma-Context-MCP) reads files through
    # Figma's REST API with a personal access token made in the browser.
    _local(
        "figma-token", "Figma (token)", "Read Figma files, frames and images — no desktop app needed", "npx",
        ["-y", "figma-developer-mcp", "--stdio"], "#a259ff", category="Design", auth="key",
        env={"FIGMA_API_KEY": "${FIGMA_API_KEY}"}, credentials=["FIGMA_API_KEY"],
        fields={"FIGMA_API_KEY": {
            "label": "Personal access token", "hint": "figma.com → Settings → Security → Personal access tokens",
            "secret": True, "placeholder": "figd_…"}},
        setup={
            "title": "Connect Figma with a personal access token",
            "why": "Works in any browser — no Figma desktop app. Jarvis reads your files through Figma's API "
                   "(Framelink, an open-source MCP server) with a token you create once.",
            "steps": [
                "Open Figma settings in your browser and go to the Security tab.",
                "Under Personal access tokens, click “Generate new token”; give File content read access.",
                "Copy the token (it starts with figd_) and paste it here. Then share a Figma link with Jarvis.",
            ],
            "link_label": "Open Figma settings",
            "link": "https://www.figma.com/settings",
            "note": "Read-only: Jarvis can read designs and export images, not edit them.",
        },
        repo="GLips/Figma-Context-MCP",
        keywords="design ui framelink browser web token",
    ),
    _hosted("canva", "Canva", "Create and edit designs", "Design",
            "https://mcp.canva.com/mcp", "#00c4cc", keywords="design graphics"),
    _hosted("webflow", "Webflow", "Sites, pages and CMS", "Design",
            "https://mcp.webflow.com/mcp", "#146ef5", keywords="website cms"),
    _hosted("wix", "Wix", "Sites and business data", "Design",
            "https://mcp.wix.com/mcp", "#0c6efc", keywords="website"),
    _hosted("cloudinary", "Cloudinary", "Images and video assets", "Design",
            "https://asset-management.mcp.cloudinary.com/sse", "#3448c5", keywords="images media"),

    # ── cloud & data ─────────────────────────────────────────────────────
    _hosted("supabase", "Supabase", "Databases, auth and edge functions", "Cloud & data",
            "https://mcp.supabase.com/mcp", "#3ecf8e", keywords="postgres database sql"),
    _hosted("vercel", "Vercel", "Projects, deployments and logs", "Cloud & data",
            "https://mcp.vercel.com", "#111111", keywords="deploy hosting"),
    _hosted("netlify", "Netlify", "Sites and deploys", "Cloud & data",
            "https://netlify-mcp.netlify.app/mcp", "#05bdba", keywords="deploy hosting"),
    _hosted("neon", "Neon", "Serverless Postgres", "Cloud & data",
            "https://mcp.neon.tech/mcp", "#00e599", keywords="postgres database sql"),
    _hosted("prisma", "Prisma Postgres", "Databases and migrations", "Cloud & data",
            "https://mcp.prisma.io/mcp", "#2d3748", keywords="postgres database orm"),
    _hosted("cloudflare", "Cloudflare", "Workers, KV, R2 and D1", "Cloud & data",
            "https://bindings.mcp.cloudflare.com/mcp", "#f38020", keywords="workers edge"),
    _hosted("cloudflare-observability", "Cloudflare Logs", "Worker logs and analytics", "Cloud & data",
            "https://observability.mcp.cloudflare.com/mcp", "#f38020", keywords="logs workers"),
    _hosted("cloudflare-docs", "Cloudflare Docs", "Search Cloudflare's documentation", "Cloud & data",
            "https://docs.mcp.cloudflare.com/mcp", "#f38020", auth="open", keywords="docs"),
    _hosted("railway", "Railway", "Projects, services and deploys", "Cloud & data",
            "https://mcp.railway.com", "#7c3aed", keywords="deploy hosting"),
    _hosted(
        "render", "Render", "Services, deploys and logs", "Cloud & data",
        "https://mcp.render.com/mcp", "#4a4aff", auth="key",
        headers={"Authorization": "Bearer ${RENDER_API_KEY}"},
        credentials=["RENDER_API_KEY"],
        fields={"RENDER_API_KEY": {"label": "API key", "hint": "dashboard.render.com → Account settings → API keys",
                                   "secret": True, "placeholder": "rnd_…"}},
        setup={"link_label": "Get an API key", "link": "https://dashboard.render.com/u/settings#api-keys"},
        keywords="deploy hosting",
    ),

    # ── payments ─────────────────────────────────────────────────────────
    _hosted("stripe", "Stripe", "Customers, payments and the Stripe API", "Payments",
            "https://mcp.stripe.com", "#635bff", keywords="billing invoices"),
    _hosted("paypal", "PayPal", "Invoices, orders and payments", "Payments",
            "https://mcp.paypal.com/mcp", "#003087", keywords="billing invoices"),
    _hosted("square", "Square", "Payments, orders and catalog", "Payments",
            "https://mcp.squareup.com/sse", "#3e4348", keywords="pos commerce"),

    # ── analytics ────────────────────────────────────────────────────────
    _hosted("posthog", "PostHog", "Product analytics, flags and errors", "Analytics",
            "https://mcp.posthog.com/mcp", "#f54e00", keywords="analytics feature flags"),
    _hosted("mixpanel", "Mixpanel", "Events, funnels and reports", "Analytics",
            "https://mcp.mixpanel.com/mcp", "#7856ff", keywords="analytics"),

    # ── search & web ─────────────────────────────────────────────────────
    _hosted("exa", "Exa", "Web search built for agents", "Search & web",
            "https://mcp.exa.ai/mcp", "#1f40ed", auth="open", keywords="search web"),
    _hosted("tavily", "Tavily", "Web search and page extraction", "Search & web",
            "https://mcp.tavily.com/mcp", "#2563eb", keywords="search web crawl"),
    _hosted("apify", "Apify", "Web scrapers and automation actors", "Search & web",
            "https://mcp.apify.com", "#97d700", keywords="scrape crawl"),
    _local("playwright", "Playwright", "Drive a real browser", "npx", ["-y", "@playwright/mcp@latest"], "#2ead33",
           category="Search & web", repo="microsoft/playwright-mcp", keywords="browser automation testing"),
    _local("chrome-devtools", "Chrome DevTools", "Debug pages: console, network, performance", "npx",
           ["-y", "chrome-devtools-mcp@latest"], "#4285f4", category="Search & web", keywords="browser debug"),
    _local("fetch", "Fetch", "Read web pages as markdown", "uvx", ["mcp-server-fetch"], "#64748b",
           category="Search & web", keywords="web http"),
    _local(
        "brave-search", "Brave Search", "Private web and news search", "npx",
        ["-y", "@brave/brave-search-mcp-server"], "#fb542b", category="Search & web", auth="key",
        env={"BRAVE_API_KEY": "${BRAVE_API_KEY}"}, credentials=["BRAVE_API_KEY"],
        fields={"BRAVE_API_KEY": {"label": "API key", "hint": "brave.com/search/api — free plan available",
                                  "secret": True}},
        setup={"link_label": "Get an API key", "link": "https://api-dashboard.search.brave.com/app/keys"},
        keywords="search web",
    ),

    # ── on this computer ─────────────────────────────────────────────────
    _local("filesystem", "Filesystem", "Read and write files in this folder", "npx",
           ["-y", "@modelcontextprotocol/server-filesystem", "${CWD}"], "#475569", keywords="files"),
    _local("git", "Git", "Read history, diffs and branches", "uvx", ["mcp-server-git"], "#f05032",
           keywords="version control"),
    _local("memory", "Memory", "A knowledge graph Jarvis remembers", "npx",
           ["-y", "@modelcontextprotocol/server-memory"], "#9333ea", keywords="notes recall"),
    _local("sequential-thinking", "Sequential Thinking", "Step-by-step problem solving", "npx",
           ["-y", "@modelcontextprotocol/server-sequential-thinking"], "#0891b2", keywords="reasoning"),
    _local("time", "Time", "Current time and time-zone conversion", "uvx", ["mcp-server-time"], "#ca8a04",
           keywords="clock timezone"),
]

_BY_ID = {c["id"]: c for c in CATALOG}

AUTH_LABELS = {
    "oauth": "Sign in",
    "open": "No account",
    "key": "API key",
    "app": "Your app",
    "desktop": "Desktop app",
    "local": "Runs locally",
}


def lookup(key: str) -> dict[str, Any] | None:
    return _BY_ID.get((key or "").strip().lower())


def by_repo(owner: str, repo: str) -> dict[str, Any] | None:
    """A well-known server whose home is this GitHub repo (its README isn't the place to read the config from)."""
    key = f"{owner}/{repo}".lower()
    for c in CATALOG:
        if c.get("repo", "").lower() == key:
            return c
    return None


def _norm_url(url: str) -> str:
    return (url or "").split("?", 1)[0].rstrip("/").lower()


def by_url(url: str) -> dict[str, Any] | None:
    u = _norm_url(url)
    for c in CATALOG:
        if c.get("url") and _norm_url(c["url"]) == u:
            return c
    return None


def by_alt_url(url: str) -> dict[str, Any] | None:
    """A catalog entry that replaced this (no longer usable) address."""
    u = _norm_url(url)
    for c in CATALOG:
        if any(_norm_url(a) == u for a in c.get("alt_urls") or []):
            return c
    return None


def for_server(name: str, cfg: dict[str, Any] | None) -> dict[str, Any] | None:
    """The catalog entry a configured server came from (same URL, or same name + command)."""
    cfg = cfg or {}
    if cfg.get("url"):
        hit = by_url(str(cfg["url"]))
        if hit:
            return hit
    item = lookup(name)
    if item and item.get("url") and cfg.get("url") and _norm_url(str(cfg["url"])) in {
        _norm_url(u) for u in [item["url"], *(item.get("alt_urls") or [])]
    }:
        return item
    if item and item.get("command") and cfg.get("command") == item["command"]:
        return item
    return None


def setup_link(item: dict[str, Any]) -> str:
    link = str((item.get("setup") or {}).get("link") or "")
    if link == "slack_manifest":
        return slack_manifest_url()
    return link


def monogram(label: str) -> str:
    words = [w for w in label.replace(".", " ").split() if w[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:1].upper()
    return (words[0][:1] + words[1][:1]).upper()


def matches(item: dict[str, Any], query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    hay = " ".join(str(item.get(k, "")) for k in ("id", "label", "desc", "category", "keywords"))
    hay = f"{hay} {AUTH_LABELS.get(item.get('auth', ''), '')}".lower()
    return all(word in hay for word in q.split())


def search(query: str = "", category: str = "") -> list[dict[str, Any]]:
    """Catalog entries matching ``query`` (every word), popular ones first."""
    cat = (category or "").strip()
    rank = {cid: i for i, cid in enumerate(POPULAR)}
    out = [c for c in CATALOG if matches(c, query)]
    if cat == "Popular":
        out = [c for c in out if c["id"] in rank]
    elif cat:
        out = [c for c in out if c.get("category") == cat]
    q = (query or "").strip().lower()

    def key(c: dict[str, Any]) -> tuple:
        starts = 0 if q and (c["label"].lower().startswith(q) or c["id"].startswith(q)) else 1
        return (starts, rank.get(c["id"], 999), c["label"].lower())

    return sorted(out, key=key)


def public_item(c: dict[str, Any]) -> dict[str, Any]:
    setup = dict(c.get("setup") or {})
    if setup:
        setup["link"] = setup_link(c)
    fields = {
        var: {"label": var, "hint": "", "secret": True, "placeholder": "", **(c.get("fields") or {}).get(var, {})}
        for var in (c.get("credentials") or [])
    }
    return {
        "id": c["id"],
        "label": c["label"],
        "desc": c["desc"],
        "category": c.get("category", ""),
        "kind": "remote" if c.get("url") else "local",
        "auth": c.get("auth", "oauth"),
        "auth_label": AUTH_LABELS.get(c.get("auth", "oauth"), ""),
        "endpoint": c.get("url") or " ".join([c.get("command", "")] + [str(a) for a in c.get("args", [])]),
        "color": c.get("color", ""),
        "monogram": monogram(c["label"]),
        "note": c.get("note", ""),
        "alt_note": c.get("alt_note", ""),
        "keywords": c.get("keywords", ""),
        "docs": c.get("docs", ""),
        "credentials": list(c.get("credentials") or []),
        "fields": fields,
        "setup": setup,
        "popular": c["id"] in POPULAR,
        "redirect_url": oauth_redirect_url() if c.get("auth") == "app" else "",
    }


def public() -> list[dict[str, Any]]:
    """What the UIs show in the marketplace, popular ones first."""
    return [public_item(c) for c in search("")]
