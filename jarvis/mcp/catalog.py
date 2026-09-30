"""A short list of well-known MCP servers — one click / one word to add.

Best effort: these are the vendors' published endpoints, and endpoints move. A
wrong one just fails to connect with a plain message; nothing here is trusted.
Hosted ones (``url``) sign in through the browser; ``credentials`` names the
variables a server needs when it wants a key instead.
"""
from __future__ import annotations

from typing import Any

CATALOG: list[dict[str, Any]] = [
    {"id": "linear", "label": "Linear", "desc": "Issues, projects and cycles", "url": "https://mcp.linear.app/mcp"},
    {"id": "notion", "label": "Notion", "desc": "Search and edit pages", "url": "https://mcp.notion.com/mcp"},
    {"id": "sentry", "label": "Sentry", "desc": "Errors and performance", "url": "https://mcp.sentry.dev/mcp", "repo": "getsentry/sentry-mcp"},
    {"id": "atlassian", "label": "Atlassian", "desc": "Jira and Confluence", "url": "https://mcp.atlassian.com/v1/sse"},
    {"id": "asana", "label": "Asana", "desc": "Tasks and projects", "url": "https://mcp.asana.com/sse"},
    {"id": "figma", "label": "Figma", "desc": "Read designs", "url": "https://mcp.figma.com/mcp"},
    {"id": "stripe", "label": "Stripe", "desc": "Payments API", "url": "https://mcp.stripe.com"},
    {"id": "vercel", "label": "Vercel", "desc": "Projects and deployments", "url": "https://mcp.vercel.com"},
    {"id": "supabase", "label": "Supabase", "desc": "Databases and edge functions", "url": "https://mcp.supabase.com/mcp"},
    {"id": "context7", "label": "Context7", "desc": "Up-to-date library docs", "url": "https://mcp.context7.com/mcp", "repo": "upstash/context7"},
    {
        "id": "github",
        "repo": "github/github-mcp-server",
        "label": "GitHub",
        "desc": "Repos, issues and pull requests",
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": {"Authorization": "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}"},
        "credentials": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        "note": "Needs a GitHub personal access token.",
    },
    {"id": "playwright", "label": "Playwright", "desc": "Drive a real browser", "command": "npx", "args": ["-y", "@playwright/mcp@latest"], "repo": "microsoft/playwright-mcp"},
    {"id": "fetch", "label": "Fetch", "desc": "Read web pages as markdown", "command": "uvx", "args": ["mcp-server-fetch"]},
    {"id": "memory", "label": "Memory", "desc": "Knowledge-graph memory", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
    {
        "id": "filesystem",
        "label": "Filesystem",
        "desc": "Read and write files in this folder",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "${CWD}"],
    },
]

_BY_ID = {c["id"]: c for c in CATALOG}


def lookup(key: str) -> dict[str, Any] | None:
    return _BY_ID.get((key or "").strip().lower())


def by_repo(owner: str, repo: str) -> dict[str, Any] | None:
    """A well-known server whose home is this GitHub repo (its README isn't the place to read the config from)."""
    key = f"{owner}/{repo}".lower()
    for c in CATALOG:
        if c.get("repo", "").lower() == key:
            return c
    return None


def by_url(url: str) -> dict[str, Any] | None:
    u = (url or "").split("?", 1)[0].rstrip("/").lower()
    for c in CATALOG:
        if c.get("url") and c["url"].rstrip("/").lower() == u:
            return c
    return None


def public() -> list[dict[str, Any]]:
    """What the UIs show as quick-add chips."""
    return [
        {
            "id": c["id"],
            "label": c["label"],
            "desc": c["desc"],
            "kind": "remote" if c.get("url") else "local",
            "note": c.get("note", ""),
        }
        for c in CATALOG
    ]
