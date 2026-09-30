"""Tools that install skills and MCP servers for the user.

"Add this skill / that MCP server" is one call here — the model never edits
config files or unpacks archives by hand. The heavy lifting lives in
``jarvis.storage.skill_install`` and ``jarvis.mcp.install`` (shared with the
``/skill`` / ``/mcp`` commands and both UIs).

* **scope** — ``project`` (this folder: ``.harness/skills``, ``.mcp.json``) or
  ``global`` (every project). Default global, unless the user says "here" /
  "in this project".
* **approval** — a call the *agent* makes asks the user first (same prompt as a
  shell command; skipped with auto-approve), so text on a web page can't talk
  the model into installing something.
* **credentials** — a key an MCP server needs is asked for with a hidden input
  box; it never passes through the model.
* **sign-in** — a hosted server that wants a browser sign-in is added and
  reported ``auth_required``; the UI shows an *Authenticate* button.
"""
from __future__ import annotations

from typing import Any

from ..console import console
from ..mcp import install as mcp_install
from ..mcp.auth import coordinator as auth_coordinator
from ..mcp.registry import mcp_registry
from ..storage import skill_install as skills_install
from .shell import _bash_lock, ask_approval


def _approve(description: str) -> bool:
    """Ask the user before installing (agent-initiated only)."""
    with _bash_lock:
        return ask_approval(description) is None


def _scope(scope: str | None) -> str:
    s = (scope or "global").strip().lower()
    return "project" if s in ("project", "local", "here", "repo", "workspace") else "global"


# ── skills ────────────────────────────────────────────────────────────────


def skill_install(
    source: str,
    scope: str = "global",
    skill: str | None = None,
    all: bool = False,  # noqa: A002 — the tool argument is called "all"
    overwrite: bool = False,
) -> str:
    res = skills_install.install_skills(
        source,
        scope=_scope(scope),
        names=[skill] if skill else None,
        install_all=bool(all),
        overwrite=bool(overwrite),
        confirm=_approve,
    )
    if res.get("needs_choice"):
        lines = [f"{res['label']} has {len(res['skills'])} skills — ask the user which to install "
                 "(ask_user_question), then call skill_install again with skill='<name>' (or all=true):"]
        for s in res["skills"]:
            desc = (s.get("description") or "").strip().replace("\n", " ")
            lines.append(f"  • {s['name']}: {desc[:110]}{'…' if len(desc) > 110 else ''}")
        return "\n".join(lines)
    if not res.get("ok"):
        return f"ERROR: {res.get('error', 'could not install')}"
    sc = res["scope"]
    lines = [f"Installed {len(res['installed'])} skill{'s' if len(res['installed']) != 1 else ''} ({sc}):"]
    for i in res["installed"]:
        extra = f" — note: {'; '.join(i['problems'])}" if i.get("problems") else ""
        lines.append(f"  ✓ {i['name']}{extra}")
    for s in res.get("skipped", []):
        lines.append(f"  – skipped {s['name']}: {s['reason']}")
    if res.get("scope_note"):
        lines.append(res["scope_note"])
    lines.append("Load one with skill_load('<name>'); its header shows up in the skill list from the next message.")
    lines.append("Skills are instructions you will follow — they came from " + res["label"] + ".")
    return "\n".join(lines)


def skill_remove(name: str, scope: str | None = None) -> str:
    if not _approve(f"remove skill '{name}'" + (f" ({scope})" if scope else "")):
        return "USER DENIED"
    res = skills_install.remove_skill(name, scope=_scope(scope) if scope else None)
    return res["message"] if res.get("ok") else f"ERROR: {res.get('error', 'could not remove')}"


# ── MCP ───────────────────────────────────────────────────────────────────


def _ask_credentials(name: str, missing: list[str]) -> dict[str, str]:
    """Hidden input for each missing key (TUI and web). Empty / cancelled → skipped."""
    got: dict[str, str] = {}
    for var in missing:
        try:
            val = console.input(f"[bold]{var}[/] for MCP server [cyan]{name}[/] (hidden — leave empty to skip): ", password=True)
        except (EOFError, KeyboardInterrupt, RuntimeError):
            break
        if val and val.strip():
            got[var] = val.strip()
    return got


def _render(results: list[dict[str, Any]]) -> str:
    lines = [mcp_install.summarize(results)]
    for r in results:
        st = r.get("status")
        nm = r.get("name")
        if st == "connected" and r.get("tools"):
            sample = ", ".join(f"mcp__{nm}__{t}" for t in r["tools"][:6])
            more = f" (+{r['tool_count'] - 6} more)" if r.get("tool_count", 0) > 6 else ""
            lines.append(f"  tools: {sample}{more}")
            lines.append("  These tools are callable from the next step.")
        if st == "auth_required":
            lines.append(
                f"  Tell the user: a “Sign in to {nm}” button is showing — click it, approve in the browser, "
                "and the tools appear on their own. Don't call mcp__ tools for it until then."
            )
        if st == "needs_credentials":
            lines.append(f"  The key(s) {', '.join(r.get('missing', []))} weren't entered. "
                         "Call mcp_connect again to be asked, or the user can set them in the MCP dialog.")
        if r.get("notes"):
            lines.extend(f"  note: {n}" for n in r["notes"])
    return "\n".join(lines)


def mcp_add(
    source: str | None = None,
    name: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    url: str | None = None,
    env: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    scope: str = "global",
    connect: bool = True,
    replace: bool = False,
) -> str:
    res = mcp_install.add_mcp(
        source,
        name=name,
        command=command,
        args=args,
        url=url,
        env=env,
        headers=headers,
        scope=_scope(scope),
        connect=bool(connect),
        replace=bool(replace),
        confirm=_approve,
    )
    if not res.get("servers"):
        return f"ERROR: {res.get('error', 'nothing to add')}"
    # A key the server needs: ask for it here, hidden, then try again.
    for r in res["servers"]:
        if r.get("status") == "needs_credentials" and r.get("missing"):
            got = _ask_credentials(r["name"], list(r["missing"]))
            if got:
                fixed = mcp_install.set_credentials(r["name"], got, connect=bool(connect))
                r.update({k: v for k, v in fixed.items() if k != "ok"})
    return _render(res["servers"])


def mcp_list() -> str:
    data = mcp_install.describe_servers()
    servers = data["servers"]
    if not servers:
        where = "project (.mcp.json)" if not data["global_mcp"] else "project or global"
        return (f"No MCP servers in scope ({where}). Add one with mcp_add — a hosted URL, an npx/uvx command, "
                "a `claude mcp add …` line, JSON, a GitHub link, or a name like linear/notion/sentry.")
    lines = [f"MCP servers ({'project + global' if data['global_mcp'] else 'project only'}):"]
    for s in servers:
        h = s["health"]
        st = h.get("status")
        word = {
            "live": f"connected · {s['tool_count']} tools",
            "auth": "SIGN-IN NEEDED (user clicks Authenticate)",
            "failed": f"failed: {h.get('last_connect_error') or h.get('detail') or ''}",
            "warn": "needs attention: " + (h.get("detail") or ""),
            "connecting": "connecting…",
            "idle": "not connected",
        }.get(st, st)
        lines.append(f"  • {s['name']} [{s['scope']}, {s['transport']}] {word}")
        if s["needs_credentials"]:
            lines.append(f"      needs: {', '.join(s['needs_credentials'])}")
    if data["pending_auth"]:
        lines.append("Waiting for the user to sign in: " + ", ".join(p["name"] for p in data["pending_auth"]))
    return "\n".join(lines)


def mcp_remove(name: str, scope: str | None = None) -> str:
    if not _approve(f"remove MCP server '{name}'" + (f" ({scope})" if scope else "")):
        return "USER DENIED"
    res = mcp_install.remove_mcp(name, scope=_scope(scope) if scope else None)
    return res["message"] if res.get("ok") else f"ERROR: {res.get('error', 'could not remove')}"


def mcp_connect(name: str, action: str = "connect") -> str:
    """Connect / reconnect / disconnect a configured server, or start its sign-in again."""
    act = (action or "connect").strip().lower()
    cfg = mcp_install.get_config().get_server(name)
    if cfg is None:
        return f"ERROR: no MCP server named '{name}' in the current scope. mcp_list shows what's there."
    if act == "disconnect":
        err = mcp_registry.disconnect(name)
        return f"ERROR: {err}" if err else f"Disconnected {name}."
    if act in ("signout", "sign_out", "logout"):
        mcp_registry.sign_out(name, cfg)
        return f"Signed out of {name} and forgot the saved login."
    if act == "reconnect" and mcp_registry.is_connected(name):
        mcp_registry.disconnect(name)
    if act not in ("connect", "reconnect", "authenticate", "auth", "signin", "sign_in"):
        return "ERROR: action must be connect, reconnect, disconnect, authenticate or signout."

    from ..mcp import secrets as mcp_secrets

    missing = mcp_secrets.missing(cfg)
    if missing:
        got = _ask_credentials(name, missing)
        if got:
            res = mcp_install.set_credentials(name, got, connect=True)
            return _render([res])
        return _render([{"name": name, "status": "needs_credentials", "missing": missing}])
    if act in ("authenticate", "auth", "signin", "sign_in"):
        res = mcp_registry.authenticate(name, cfg)
        if res.get("connected"):
            return _render([mcp_install.server_status(name, connect=False)])
        if res.get("ok"):
            return _render([{"name": name, "status": "auth_required", "auth": res}])
        return f"ERROR: {res.get('error', 'could not start sign-in')}"
    return _render([mcp_install.server_status(name, connect=True)])


# ── schemas ───────────────────────────────────────────────────────────────

_SCOPE_PROP = {
    "type": "string",
    "enum": ["global", "project"],
    "description": (
        "Where to install. 'global' (default) = available in every project. "
        "'project' = only this folder — use it when the user says 'here', 'in this project' or 'in this repo'."
    ),
}

EXTENSION_TOOLS = [
    {
        "name": "skill_install",
        "description": (
            "Install a skill for the user from a link — a GitHub repo or folder "
            "(https://github.com/o/r, …/tree/main/skills/pdf, o/r, o/r@skill), a skills.sh link, a SKILL.md link, "
            "a .zip/.tar.gz, a git URL or a local folder. Use this whenever the user says 'add/install this skill' "
            "— never copy files by hand. If the source holds several skills you get the list back: ask the user "
            "which (ask_user_question), then call again with skill='<name>' or all=true. If they only name a "
            "skill without a link, find its source first (web_search / fetch_url), then install. "
            "The user is asked to approve the install."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "The link, owner/repo, or folder path."},
                "scope": _SCOPE_PROP,
                "skill": {"type": "string", "description": "Install only this skill from a multi-skill source."},
                "all": {"type": "boolean", "description": "Install every skill in the source."},
                "overwrite": {"type": "boolean", "description": "Replace a skill that is already installed."},
            },
            "required": ["source"],
        },
    },
    {
        "name": "skill_remove",
        "description": "Remove a skill that was installed into the project or global skills folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "scope": {"type": "string", "enum": ["global", "project"], "description": "Only needed if the name exists in both."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "mcp_add",
        "description": (
            "Add an MCP server for the user and connect it. `source` can be a hosted address "
            "(https://mcp.linear.app/mcp), an install command (npx -y @scope/server, uvx mcp-server-x), a "
            "`claude mcp add …` line from a README, a JSON config snippet, a GitHub repo link (its README is read), "
            "or a well-known name (linear, notion, sentry, github, atlassian, asana, figma, stripe, vercel, "
            "supabase, context7, playwright, fetch, memory, filesystem). Or pass `url` / `command`+`args` directly. "
            "Use this instead of editing .mcp.json or running npx yourself. "
            "Results: connected (tools are usable next step) · auth_required (a hosted server needs the user to "
            "click the Authenticate button — tell them, then stop) · needs_credentials (a hidden prompt asks the "
            "user for the key; never ask them to paste keys into chat) · failed (read the error). "
            "The user is asked to approve the install."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Address, install command, JSON, GitHub link or well-known name."},
                "name": {"type": "string", "description": "Short name for the server (defaults to one derived from the source)."},
                "command": {"type": "string", "description": "Local server executable (instead of source), e.g. npx."},
                "args": {"type": "array", "items": {"type": "string"}, "description": "Arguments for `command`."},
                "url": {"type": "string", "description": "Hosted server address (instead of source)."},
                "env": {"type": "object", "additionalProperties": {"type": "string"},
                        "description": "Environment for a local server. Secret values are stored outside the config file."},
                "headers": {"type": "object", "additionalProperties": {"type": "string"},
                            "description": "HTTP headers for a hosted server, e.g. Authorization. Stored outside the config file."},
                "scope": _SCOPE_PROP,
                "connect": {"type": "boolean", "description": "Connect right away (default true)."},
                "replace": {"type": "boolean", "description": "Overwrite a server with the same name."},
            },
            "required": [],
        },
    },
    {
        "name": "mcp_list",
        "description": (
            "List the MCP servers in scope with their status: connected (tool count), sign-in needed, failed "
            "(with the error), or missing credentials. Use it to see why an MCP tool isn't available."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "mcp_connect",
        "description": (
            "Connect, reconnect or disconnect a configured MCP server, restart its browser sign-in "
            "(action='authenticate') or forget its saved login (action='signout'). Use it to retry after the "
            "user has signed in / fixed something. Asks the user (hidden prompt) for any missing key."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "action": {"type": "string", "enum": ["connect", "reconnect", "disconnect", "authenticate", "signout"]},
            },
            "required": ["name"],
        },
    },
    {
        "name": "mcp_remove",
        "description": "Remove an MCP server the user added (project or global) and forget its saved login.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "scope": {"type": "string", "enum": ["global", "project"], "description": "Only needed if the name exists in both."},
            },
            "required": ["name"],
        },
    },
]

# Whether a sign-in is waiting — keeps mcp_connect in reach so the agent can retry.
def sign_in_waiting() -> bool:
    try:
        return bool(auth_coordinator.pending())
    except Exception:
        return False


__all__ = [
    "EXTENSION_TOOLS", "skill_install", "skill_remove", "mcp_add", "mcp_list", "mcp_connect",
    "mcp_remove", "sign_in_waiting",
]
