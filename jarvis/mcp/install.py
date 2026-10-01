"""Add, remove and repair MCP servers from whatever the user has in hand.

The agent tools, the ``/mcp`` command and both UIs call in here, so they all
behave the same:

* ``parse_source`` understands what people paste — a hosted URL, ``npx -y pkg``,
  ``claude mcp add …`` from a README, a JSON snippet (Claude Code / Cursor /
  VS Code shapes), a GitHub repo link (its README is read) or a well-known
  name (``linear``).
* ``add_mcp`` writes it to the **project** (``.mcp.json``) or **global** scope,
  keeps credentials out of the file, connects, and reports one of
  ``connected`` · ``auth_required`` (browser sign-in — the UI shows the button) ·
  ``needs_credentials`` (a key to fill in) · ``failed`` · ``exists``.
* ``describe_servers`` is the one list every screen renders.

Nothing in here prompts or prints; callers decide how to ask.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import urllib.parse
from typing import Any, Callable

from .. import state
from . import catalog
from . import secrets as mcp_secrets
from .auth import coordinator as auth_coordinator, has_saved_login
from .config import (
    _json_to_server_candidates,
    _normalize_server_entry,
    delete_server,
    ensure_global_visible,
    get_config,
    guess_remote_type,
    locate_server,
    read_scope_server,
    reload_config,
    scope_path,
    write_server,
)
from .registry import needs_auth
from .registry import mcp_registry
from .sources import SOURCE_LABELS, format_endpoint

SCOPES = ("project", "global")
_LAUNCHERS = {
    "npx", "uvx", "uv", "node", "python", "python3", "docker", "podman", "bunx", "bun",
    "pnpm", "pnpx", "yarn", "deno", "pipx", "dotnet", "java", "npm",
}
_ADD_CMD_RE = re.compile(r"^\s*(?:(?:claude|codex|gemini|qwen|amp|opencode|cursor)\s+)?mcp\s+add\b", re.I)
_NPM_RE = re.compile(r"^(@[a-z0-9][\w.~-]*/)?[a-z0-9][\w.~-]*(@[\w.^~-]+)?$", re.I)
_MAX_TOOLS_LISTED = 60


class ParseError(ValueError):
    """The text isn't something we can turn into a server. The message says why and what to try."""


# ── names ─────────────────────────────────────────────────────────────────


def sanitize_name(raw: str, fallback: str = "server") -> str:
    """Server names end up inside tool names (``mcp__<name>__<tool>``): short, lowercase, no ``__``."""
    name = re.sub(r"[^a-z0-9_-]+", "-", (raw or "").strip().lower())
    name = re.sub(r"_{2,}", "_", name)
    name = re.sub(r"-{2,}", "-", name).strip("-_")
    return (name[:24].strip("-_")) or fallback


def _strip_decor(name: str) -> str:
    name = re.sub(r"@(latest|next|beta|\d[\w.]*)$", "", name)
    name = name.split("/")[-1]
    for pat in (r"^mcp[-_]server[-_]", r"^server[-_]", r"[-_]mcp[-_]server$", r"[-_]mcp$", r"^mcp[-_]", r"[-_]server$"):
        stripped = re.sub(pat, "", name, flags=re.I)
        if stripped:
            name = stripped
    return name


def _name_from_url(url: str) -> str:
    known = catalog.by_url(url)
    if known:
        return known["id"]
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if host == "localhost" or re.fullmatch(r"[\d.]+|[0-9a-f:]+", host or "x"):
        # an address, not a name — "127.0.0.1" would otherwise become "1"
        return sanitize_name(f"local-{parsed.port}" if parsed.port else "local", "local")
    parts = [p for p in host.split(".") if p and p not in ("www", "mcp", "api", "app", "com", "org", "io", "dev", "net", "ai", "co", "app")]
    return sanitize_name(parts[-1] if parts else host, "remote")


def _name_from_command(command: str, args: list[str]) -> str:
    base = os.path.basename(command)
    cand = ""
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a in ("-y", "--yes", "run", "-m", "--rm", "-i", "-t", "-it", "exec", "dlx", "x", "tool"):
            continue
        if a.startswith("-"):
            if a in ("-e", "--env", "-v", "--volume", "-p", "--port", "--name", "--directory", "--from", "--with"):
                skip_next = True
            continue
        cand = a
        break
    if base in ("docker", "podman") and cand:
        cand = cand.split("/")[-1].split(":")[0]
    if not cand:
        cand = base
    cand = os.path.splitext(os.path.basename(cand))[0] if ("/" in cand or cand.endswith((".py", ".js", ".mjs"))) else cand
    return sanitize_name(_strip_decor(cand), sanitize_name(base, "server"))


# ── parsing ───────────────────────────────────────────────────────────────


def _spec(name: str, entry: dict[str, Any], *notes: str, scope_hint: str = "", credentials: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "entry": entry,
        "notes": [n for n in notes if n],
        "scope_hint": scope_hint,
        "credentials": credentials or [],
    }


def _from_catalog(item: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any]
    if item.get("url"):
        entry = {"type": guess_remote_type(item["url"]), "url": item["url"]}
        if item.get("headers"):
            entry["headers"] = dict(item["headers"])
    else:
        entry = {
            "type": "stdio",
            "command": item["command"],
            "args": [str(a).replace("${CWD}", str(os.getcwd())) for a in item.get("args", [])],
        }
    return _spec(item["id"], entry, item.get("note", ""), credentials=list(item.get("credentials") or []))


def _convert_vscode_inputs(obj: Any) -> Any:
    """VS Code ``${input:api-key}`` → ``${API_KEY}`` so it becomes a credential to fill in."""
    if isinstance(obj, str):
        return re.sub(
            r"\$\{input:([^}]+)\}",
            lambda m: "${" + re.sub(r"[^A-Za-z0-9]+", "_", m.group(1)).strip("_").upper() + "}",
            obj,
        )
    if isinstance(obj, list):
        return [_convert_vscode_inputs(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _convert_vscode_inputs(v) for k, v in obj.items()}
    return obj


def _strip_jsonc(text: str) -> str:
    """README snippets carry ``// comments`` and trailing commas — plain JSON otherwise."""
    out, i, in_str = [], 0, False
    while i < len(text):
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and text[i + 1:i + 2] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _parse_json(text: str) -> list[dict[str, Any]]:
    t = text.strip()
    if not t.startswith("{") and re.match(r'^"[^"]+"\s*:\s*\{', t):
        t = "{" + t.rstrip(",") + "}"
    try:
        try:
            data = json.loads(t)
        except json.JSONDecodeError:
            data = json.loads(_strip_jsonc(t))
    except json.JSONDecodeError as exc:
        raise ParseError(f"That looks like JSON but doesn't parse ({exc.msg} at line {exc.lineno}).") from exc
    if not isinstance(data, dict):
        raise ParseError("The JSON must be an object like {\"mcpServers\": {…}}.")
    data = _convert_vscode_inputs(data)
    try:
        candidates, _ = _json_to_server_candidates(data)
    except ValueError:
        if data and all(isinstance(v, dict) and ("command" in v or "url" in v) for v in data.values()):
            candidates = [(str(k), v) for k, v in data.items()]
        elif "command" in data or "url" in data:
            nm = _name_from_url(data["url"]) if data.get("url") else _name_from_command(str(data["command"]), list(data.get("args", [])))
            candidates = [(nm, data)]
        else:
            raise ParseError("No servers found in that JSON. Expected {\"mcpServers\": {\"name\": {\"command\": …}}}.")
    out = []
    for name, cfg in candidates:
        if not isinstance(cfg, dict):
            raise ParseError(f"Server '{name}' is not an object.")
        entry = _normalize_server_entry(cfg)
        if entry is None:
            raise ParseError(f"Server '{name}' needs either a \"command\" (local) or a \"url\" (hosted).")
        out.append(_spec(sanitize_name(name), entry))
    return out


def _split_env_prefix(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    env: dict[str, str] = {}
    i = 0
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        k, v = tokens[i].split("=", 1)
        env[k] = v
        i += 1
    return env, tokens[i:]


def _stdio_entry(command: str, args: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"type": "stdio", "command": command, "args": list(args)}
    if env:
        entry["env"] = dict(env)
    return entry


def _parse_launcher(text: str) -> dict[str, Any]:
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise ParseError(f"Couldn't read that command ({exc}).") from exc
    env, tokens = _split_env_prefix(tokens)
    if not tokens:
        raise ParseError("That command is empty.")
    return _spec(_name_from_command(tokens[0], tokens[1:]), _stdio_entry(tokens[0], tokens[1:], env))


def _parse_add_command(text: str) -> dict[str, Any]:
    """``claude mcp add --transport http linear https://mcp.linear.app/mcp`` and its cousins."""
    try:
        toks = shlex.split(text.replace("\\\n", " "))
    except ValueError as exc:
        raise ParseError(f"Couldn't read that command ({exc}).") from exc
    idx = next((i for i, t in enumerate(toks) if t.lower() == "add"), -1)
    toks = toks[idx + 1:]
    transport = ""
    scope_hint = ""
    env: dict[str, str] = {}
    headers: dict[str, str] = {}
    positional: list[str] = []
    after: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        val = toks[i + 1] if i + 1 < len(toks) else ""
        if t == "--":
            after = toks[i + 1:]
            break
        if t in ("--transport", "-t"):
            transport, i = val.lower(), i + 2
        elif t.startswith("--transport="):
            transport, i = t.split("=", 1)[1].lower(), i + 1
        elif t in ("--scope", "-s"):
            scope_hint, i = val.lower(), i + 2
        elif t.startswith("--scope="):
            scope_hint, i = t.split("=", 1)[1].lower(), i + 1
        elif t in ("--env", "-e"):
            if "=" in val:
                k, v = val.split("=", 1)
                env[k] = v
            i += 2
        elif t in ("--header", "-H"):
            if ":" in val:
                k, v = val.split(":", 1)
                headers[k.strip()] = v.strip()
            i += 2
        elif t.startswith("-"):
            i += 1
        else:
            positional.append(t)
            i += 1
    scope = "project" if scope_hint == "project" else ""
    if not positional and not after:
        raise ParseError("Missing the server name / address after `mcp add`.")
    name = sanitize_name(positional[0]) if positional else ""
    target = positional[1:] if positional else []
    if after:
        entry = _stdio_entry(after[0], after[1:], env)
        return _spec(name or _name_from_command(after[0], after[1:]), entry, scope_hint=scope)
    if target and re.match(r"^https?://", target[0]):
        url = target[0]
        entry = {"type": "sse" if transport == "sse" else ("http" if transport in ("http", "streamable-http") else guess_remote_type(url)), "url": url}
        if headers:
            entry["headers"] = headers
        return _spec(name or _name_from_url(url), entry, scope_hint=scope)
    if not target and re.match(r"^https?://", name or ""):
        url = positional[0]
        return _spec(_name_from_url(url), {"type": guess_remote_type(url), "url": url}, scope_hint=scope)
    if target:
        return _spec(name or _name_from_command(target[0], target[1:]), _stdio_entry(target[0], target[1:], env), scope_hint=scope)
    raise ParseError("Couldn't find a command or URL in that `mcp add` line.")


_NOT_A_SERVER = {"setup", "init", "install", "login", "logout", "auth", "configure", "config", "doctor",
                 "create", "add", "remove", "update", "upgrade", "help", "--help", "-h", "--version", "version"}


def _looks_like_server_command(entry: dict[str, Any]) -> bool:
    """``npx foo setup`` is an installer, not a server that speaks MCP over stdio."""
    words = [str(a) for a in entry.get("args", []) if not str(a).startswith("-")]
    return not any(w.lower() in _NOT_A_SERVER for w in words[1:2] + words[-1:])


def _github_repo(url: str) -> tuple[str, str] | None:
    u = urllib.parse.urlparse(url)
    if (u.hostname or "").lower() not in ("github.com", "www.github.com"):
        return None
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        return None
    return parts[0], re.sub(r"\.git$", "", parts[1])


def _from_github_readme(url: str) -> dict[str, Any]:
    """Best effort: the first ready-made config or command found in the repo's README."""
    import httpx

    repo = _github_repo(url)
    assert repo is not None
    owner, name = repo
    known = catalog.by_repo(owner, name)
    if known:
        spec = _from_catalog(known)
        spec["notes"].append(f"{owner}/{name} is a known server — using its published setup.")
        return spec
    text = ""
    for fname in ("README.md", "readme.md", "Readme.md"):
        try:
            r = httpx.get(
                f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/{fname}",
                timeout=15,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise ParseError(f"Couldn't reach GitHub ({exc.__class__.__name__}). Check the connection.") from exc
        if r.status_code == 200:
            text = r.text[:400_000]
            break
    if not text:
        raise ParseError(
            f"Couldn't read the README of {owner}/{name} (private or missing). "
            "Paste the run command (npx … / uvx …) or the config JSON instead."
        )
    note = f"Read from the README of {owner}/{name} — check it looks right."
    blocks = re.findall(r"```[a-zA-Z]*\n(.*?)```", text, re.S)
    found: list[dict[str, Any]] = []

    def take(spec: dict[str, Any]) -> None:
        if not any(f["name"] == spec["name"] for f in found):
            spec["notes"].append(note)
            found.append(spec)

    for block in blocks:
        if "mcpServers" in block or '"command"' in block or '"url"' in block:
            try:
                for sp in _parse_json(block):
                    take(sp)
            except ParseError:
                continue
    for block in blocks + [text]:
        for line in block.splitlines():
            ln = line.strip().lstrip("$>").strip()
            if _ADD_CMD_RE.match(ln):
                try:
                    take(_parse_add_command(ln))
                except ParseError:
                    continue
    if not found:  # a bare command is the last resort — READMEs also show setup wizards
        for block in blocks:
            for line in block.splitlines():
                ln = line.strip().lstrip("$>").strip()
                if ln.split(" ", 1)[0] in ("npx", "uvx", "bunx", "pipx") and len(ln.split()) > 1:
                    try:
                        spec = _parse_launcher(ln)
                    except ParseError:
                        continue
                    if _looks_like_server_command(spec["entry"]):
                        take(spec)
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        stem = sanitize_name(_strip_decor(name))
        match = [f for f in found if f["name"] == stem or f["name"] in stem or stem in f["name"]]
        if len(match) == 1:
            return match[0]
        listing = "; ".join(f"{f['name']}: {format_endpoint(f['entry'], 60)}" for f in found[:8])
        raise ParseError(
            f"{owner}/{name} lists several servers ({listing}). Tell me which one, or paste its command."
        )
    raise ParseError(
        f"{owner}/{name} doesn't show a ready-to-run command in its README. "
        "Open it, copy the install line (npx … / uvx … / claude mcp add …) and paste that."
    )


def _augment_from_catalog(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A known hosted server keeps the credentials it needs (GitHub wants a token)."""
    for sp in specs:
        entry = sp["entry"]
        known = catalog.by_url(str(entry.get("url") or "")) if entry.get("url") else None
        if known and known.get("headers") and not entry.get("headers"):
            entry["headers"] = dict(known["headers"])
            sp["credentials"] = sorted(set(sp.get("credentials") or []) | set(known.get("credentials") or []))
            if known.get("note"):
                sp["notes"].append(known["note"])
    return specs


def parse_source(text: str) -> list[dict[str, Any]]:
    """Turn pasted text into ``[{name, entry, notes, scope_hint, credentials}]`` (one or more servers)."""
    return _augment_from_catalog(_parse_source(text))


def _parse_source(text: str) -> list[dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        raise ParseError("Paste a server address, an install command or a JSON config.")
    if raw.startswith("{") or re.match(r'^"[^"]+"\s*:\s*\{', raw):
        return _parse_json(raw)
    if _ADD_CMD_RE.match(raw):
        return [_parse_add_command(raw)]
    head = raw.split(None, 1)[0]
    if re.match(r"^https?://\S+$", raw):
        if _github_repo(raw) and not urllib.parse.urlparse(raw).path.lower().endswith(("/mcp", "/sse")):
            return [_from_github_readme(raw)]
        known = catalog.by_url(raw)
        if known:
            return [_from_catalog(known)]
        return [_spec(_name_from_url(raw), {"type": guess_remote_type(raw), "url": raw})]
    item = catalog.lookup(raw)
    if item:
        return [_from_catalog(item)]
    if head in _LAUNCHERS or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", head) or head.startswith(("./", "/", "~")):
        return [_parse_launcher(raw)]
    if " " not in raw and _NPM_RE.match(raw) and (raw.startswith("@") or "mcp" in raw.lower()):
        return [_spec(_name_from_command("npx", ["-y", raw]), _stdio_entry("npx", ["-y", raw]))]
    raise ParseError(
        "Not sure what that is. Give me a server address (https://…), an install command "
        "(npx -y … / uvx …), a `claude mcp add …` line, a JSON config, a GitHub link or a name like “linear”."
    )


# ── describing ────────────────────────────────────────────────────────────


def _entry_credentials(entry: dict[str, Any]) -> list[str]:
    return mcp_secrets.missing(entry)


def describe_source(text: str) -> dict[str, Any]:
    """Dry run for the add panel: what would be added, and what it still needs."""
    try:
        specs = parse_source(text)
    except ParseError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # network etc.
        return {"ok": False, "error": f"Couldn't read that: {exc}"}
    servers = []
    for sp in specs:
        entry, stored = _protect(sp["name"], sp["entry"])
        # A real value in the pasted text is stored on add — nothing to ask for.
        asks = [n for n in _entry_credentials(entry) if n not in stored]
        servers.append({
            "name": sp["name"],
            "transport": entry.get("type", "stdio"),
            "endpoint": format_endpoint(entry, max_len=120),
            "local": entry.get("type", "stdio") == "stdio",
            "notes": sp["notes"],
            "credentials": sorted(set(asks) | set(sp.get("credentials") or [])),
            "scope_hint": sp["scope_hint"],
            "exists": [loc["scope"] for loc in locate_server(sp["name"])],
        })
    return {"ok": True, "servers": servers}


# ── secrets ───────────────────────────────────────────────────────────────


def _protect(name: str, entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Keep credentials out of the file: ``KEY=abc`` → ``KEY=${KEY}`` + a stored value."""
    out = dict(entry)
    stored: dict[str, str] = {}
    if isinstance(out.get("env"), dict):
        out["env"], s1 = mcp_secrets.protect_env(name, out["env"])
        stored.update(s1)
    if isinstance(out.get("headers"), dict):
        out["headers"], s2 = mcp_secrets.protect_headers(name, out["headers"])
        stored.update(s2)
    if out.get("type") in ("http", "sse") and out.get("url"):
        # ?api_key=abc in the address is a credential too
        u = urllib.parse.urlparse(str(out["url"]))
        q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
        changed = False
        new_q = []
        for k, v in q:
            if mcp_secrets.looks_secret(k) and "${" not in v:
                var = mcp_secrets.var_name(name, k)
                if not mcp_secrets.is_placeholder(v):
                    stored[var] = v
                new_q.append((k, "${" + var + "}"))
                changed = True
            else:
                new_q.append((k, v))
        if changed:
            out["url"] = urllib.parse.urlunparse(u._replace(query="&".join(f"{k}={v}" for k, v in new_q)))
    return out, stored


# ── add ───────────────────────────────────────────────────────────────────


def _entry_from_params(
    command: str | None,
    args: list[str] | None,
    url: str | None,
    env: dict[str, str] | None,
    headers: dict[str, str] | None,
    transport: str | None,
) -> dict[str, Any]:
    if command:
        return _stdio_entry(command, [str(a) for a in (args or [])], env)
    if url:
        t = (transport or "").lower()
        entry: dict[str, Any] = {"type": "sse" if t == "sse" else ("http" if t in ("http", "streamable-http") else guess_remote_type(url)), "url": url}
        if headers:
            entry["headers"] = dict(headers)
        return entry
    raise ParseError("Give a `source` (address, command, JSON, GitHub link or name) — or a `command` / `url`.")


def _is_local(entry: dict[str, Any]) -> bool:
    return entry.get("type", "stdio") == "stdio"


def command_line(entry: dict[str, Any]) -> str:
    return " ".join([str(entry.get("command", ""))] + [str(a) for a in entry.get("args", [])]).strip()


def server_status(name: str, *, connect: bool = True, note: str = "") -> dict[str, Any]:
    """Connect ``name`` (if asked) and describe where it ended up."""
    cfg = get_config().get_server(name)
    out: dict[str, Any] = {"name": name}
    if cfg is None:
        return {**out, "status": "failed", "error": "not visible in the current scope (turn global scope on, or check the project folder)"}
    missing = mcp_secrets.missing(cfg)
    if missing:
        return {**out, "status": "needs_credentials", "missing": missing,
                "message": "Needs " + ", ".join(missing) + " — enter it in the MCP dialog (or /mcp)."}
    if mcp_registry.is_connected(name):
        tools = [t.name for t in mcp_registry.get_server_tools(name)]
        return {**out, "status": "connected", "tool_count": len(tools), "tools": tools[:_MAX_TOOLS_LISTED]}
    if not connect:
        return {**out, "status": "added"}
    err = mcp_registry.connect(name, cfg)
    if err is None:
        tools = [t.name for t in mcp_registry.get_server_tools(name)]
        return {**out, "status": "connected", "tool_count": len(tools), "tools": tools[:_MAX_TOOLS_LISTED]}
    if needs_auth(err):
        req = auth_coordinator.get(name)
        return {**out, "status": "auth_required", "auth": req.public() if req else None,
                "message": "Sign-in needed — the Authenticate button is showing in the UI."}
    return {**out, "status": "failed", "error": err}


def add_mcp(
    source: str | None = None,
    *,
    name: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    url: str | None = None,
    env: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    transport: str | None = None,
    scope: str | None = None,
    connect: bool = True,
    replace: bool = False,
    credentials: dict[str, str] | None = None,
    confirm: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Install one or more servers. Returns ``{ok, servers:[…], message}``.

    ``scope`` is ``"project"`` (this folder's ``.mcp.json``) or ``"global"``
    (Jarvis's own file, switched on if it was hidden); ``None`` means global
    unless the pasted text says otherwise (``claude mcp add -s project``).
    ``confirm(description)`` is asked before anything is stored or run — pass it
    when the request didn't come straight from the user's own hands (an agent
    tool call), so a web page can't talk the model into installing a server.
    """
    try:
        if command or url:
            spec = _spec(sanitize_name(name or "") or (_name_from_url(url) if url else _name_from_command(command or "", args or [])),
                         _entry_from_params(command, args, url, env, headers, transport))
            specs = [spec]
        else:
            specs = parse_source(source or "")
            if name and len(specs) == 1:
                specs[0]["name"] = sanitize_name(name)
    except ParseError as exc:
        return {"ok": False, "error": str(exc), "servers": []}
    except Exception as exc:
        return {"ok": False, "error": f"Couldn't read that: {exc}", "servers": []}

    results: list[dict[str, Any]] = []
    scope_note = ""
    for sp in specs:
        sc = scope or sp.get("scope_hint") or "global"
        if sc not in SCOPES:
            return {"ok": False, "error": f"scope must be 'project' or 'global', not '{sc}'", "servers": results}
        nm = sp["name"]
        entry, stored = _protect(nm, sp["entry"])
        if env and not (command or url):
            protected_env, more = mcp_secrets.protect_env(nm, env)
            entry.setdefault("env", {}).update(protected_env)
            stored.update(more)
        if headers and not (command or url):
            protected_headers, more = mcp_secrets.protect_headers(nm, headers)
            entry.setdefault("headers", {}).update(protected_headers)
            stored.update(more)
        if credentials:
            stored.update({k: v for k, v in credentials.items() if v})

        if confirm is not None:
            what = f"runs: {command_line(entry)}" if _is_local(entry) else f"connects to: {entry.get('url')}"
            if not confirm(f"add MCP server '{nm}' ({sc}) — {what}"):
                results.append({"name": nm, "scope": sc, "status": "denied", "message": "You declined adding it."})
                continue

        elsewhere = [loc for loc in locate_server(nm) if loc["scope"] != sc or loc["editable"] != "yes"]
        here = [loc for loc in locate_server(nm) if loc["scope"] == sc and loc["editable"] == "yes"]
        if here and not replace:
            res = server_status(nm, connect=connect) if (sc == "project" or getattr(state, "global_mcp", False)) else {"name": nm, "status": "added"}
            res.update({"scope": sc, "path": here[0]["path"], "exists": True,
                        "message": f"'{nm}' is already in the {sc} config" + (" — connected it." if res.get("status") == "connected" else ".")})
            results.append(res)
            continue
        blocked = [loc for loc in elsewhere if loc["scope"] == sc and loc["editable"] != "yes"]
        if blocked:
            results.append({
                "name": nm, "scope": sc, "status": "exists",
                "message": f"'{nm}' already comes from {SOURCE_LABELS.get(blocked[0]['source'], blocked[0]['source'])} ({blocked[0]['path']}). Pick another name.",
            })
            continue

        for var, val in stored.items():
            mcp_secrets.set_secret(var, val)
        try:
            written = write_server(nm, entry, scope=sc, replace=replace)
        except (ValueError, OSError) as exc:
            results.append({"name": nm, "scope": sc, "status": "failed", "error": str(exc)})
            continue
        if sc == "global" and ensure_global_visible():
            scope_note = "Global MCP scope was off — I turned it on so this server is visible."
        reload_config()
        res = server_status(nm, connect=connect)
        res.update({"scope": sc, "path": written["path"], "replaced": written["replaced"], "notes": sp["notes"]})
        if scope_note:
            res["scope_note"] = scope_note
        results.append(res)

    _invalidate_prompt()
    ok = bool(results) and all(r.get("status") in ("connected", "auth_required", "added", "needs_credentials", "exists") for r in results)
    return {"ok": ok, "servers": results, "message": summarize(results)}


def summarize(results: list[dict[str, Any]]) -> str:
    lines = []
    for r in results:
        st = r.get("status")
        nm, sc = r.get("name"), r.get("scope", "")
        where = f" ({sc})" if sc else ""
        if st == "connected":
            lines.append(f"✓ {nm}{where} connected — {r.get('tool_count', 0)} tools")
        elif st == "auth_required":
            lines.append(f"✓ {nm}{where} added — sign-in needed: the user clicks the Authenticate button (it's showing in the UI) to finish")
        elif st == "needs_credentials":
            lines.append(f"✓ {nm}{where} added — needs {', '.join(r.get('missing', []))}; the user enters it in the MCP dialog")
        elif st == "added":
            lines.append(f"✓ {nm}{where} added (not connected yet)")
        elif st == "failed":
            lines.append(f"✗ {nm}{where}: {r.get('error', 'failed')}")
        elif st == "denied":
            lines.append(f"✗ {nm}{where}: {r.get('message', 'declined')}")
        else:
            lines.append(f"• {nm}{where}: {r.get('message', st)}")
        if r.get("scope_note"):
            lines.append("  " + r["scope_note"])
    return "\n".join(lines) or "nothing to add"


def _invalidate_prompt() -> None:
    try:
        from ..repl.system import invalidate_system_cache

        invalidate_system_cache()
    except Exception:
        pass


# ── remove / move / credentials ───────────────────────────────────────────


def remove_mcp(name: str, scope: str | None = None, *, forget_login: bool = True) -> dict[str, Any]:
    nm = (name or "").strip()
    found = locate_server(nm)
    editable = [f for f in found if f["editable"] == "yes" and (scope is None or f["scope"] == scope)]
    if not found:
        return {"ok": False, "error": f"No MCP server named '{nm}'."}
    if not editable:
        other = found[0]
        return {"ok": False, "error": f"'{nm}' comes from {SOURCE_LABELS.get(other['source'], other['source'])} ({other['path']}) — remove it there."}
    if len(editable) > 1 and scope is None:
        return {"ok": False, "error": f"'{nm}' exists in both project and global — say which scope to remove."}
    target = editable[0]
    cfg = read_scope_server(nm, target["scope"]) or {}
    try:
        if forget_login:
            mcp_registry.sign_out(nm, cfg)
        else:
            mcp_registry.disconnect(nm)
    except Exception:
        pass
    delete_server(nm, scope=target["scope"])
    reload_config()
    _invalidate_prompt()
    # still defined somewhere else? keep it visible.
    return {"ok": True, "name": nm, "scope": target["scope"], "message": f"Removed '{nm}' from the {target['scope']} config."}


def move_mcp(name: str, to_scope: str) -> dict[str, Any]:
    if to_scope not in SCOPES:
        return {"ok": False, "error": "scope must be 'project' or 'global'"}
    nm = (name or "").strip()
    src_scope = "global" if to_scope == "project" else "project"
    entry = read_scope_server(nm, src_scope)
    if entry is None:
        return {"ok": False, "error": f"'{nm}' isn't in the {src_scope} config (other tools' configs can't be moved)."}
    if read_scope_server(nm, to_scope) is not None:
        return {"ok": False, "error": f"'{nm}' is already in the {to_scope} config."}
    was_live = mcp_registry.is_connected(nm)
    try:
        write_server(nm, entry, scope=to_scope)
        delete_server(nm, scope=src_scope)
    except (ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc)}
    if to_scope == "global":
        ensure_global_visible()
    reload_config()
    _invalidate_prompt()
    return {"ok": True, "name": nm, "scope": to_scope, "was_connected": was_live,
            "message": f"Moved '{nm}' to the {to_scope} config."}


def set_credentials(name: str, values: dict[str, str], *, connect: bool = True) -> dict[str, Any]:
    """Store the keys a server asked for (kept out of its config), then connect it."""
    for var, val in (values or {}).items():
        if not str(val).strip():
            return {"ok": False, "error": f"{var} is empty."}
    try:
        for var, val in (values or {}).items():
            mcp_secrets.set_secret(var, str(val).strip())
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if mcp_registry.is_connected(name):
        mcp_registry.disconnect(name)
    res = server_status(name, connect=connect)
    res["ok"] = res.get("status") in ("connected", "auth_required", "added")
    return res


# ── listing (the one list every screen shows) ─────────────────────────────


def describe_servers(query: str = "") -> dict[str, Any]:
    config = get_config()
    servers_cfg = config.list_servers()
    names = sorted(servers_cfg)
    q = (query or "").strip().lower()
    auto = set(config.get_auto_connect())
    rows: list[dict[str, Any]] = []
    for name in names:
        cfg = servers_cfg[name] or {}
        if q and q not in name.lower() and q not in format_endpoint(cfg).lower():
            continue
        health = mcp_registry.get_server_health(name, cfg)
        transport = cfg.get("type") or ("http" if cfg.get("url") else "stdio")
        remote = transport in ("http", "sse")
        source = config.get_source(name) or ""
        connected = bool(health.get("connected"))
        tools = [t.name for t in mcp_registry.get_server_tools(name)] if connected else []
        state_obj = mcp_registry._servers.get(name)
        rows.append({
            "name": name,
            "scope": config.get_scope(name),
            "source": source,
            "source_label": SOURCE_LABELS.get(source, source),
            "also_labels": [SOURCE_LABELS.get(s, s) for s in config.get_also(name)],
            "removable": source in ("project", "jarvis"),
            "auto_connect": name in auto,
            "transport": (state_obj.transport if state_obj and state_obj.transport else transport),
            "remote": remote,
            "endpoint": format_endpoint(cfg, max_len=200),
            "health": health,
            "tools": tools[:_MAX_TOOLS_LISTED],
            "tool_count": len(tools),
            "needs_credentials": health.get("needs_credentials") or [],
            "signed_in": bool(remote and cfg.get("url") and has_saved_login(name, str(cfg["url"]))),
            "oauth": bool(remote and not _has_auth_header(cfg) and cfg.get("oauth") is not False),
        })
    return {
        "servers": rows,
        "global_mcp": bool(getattr(state, "global_mcp", False)),
        "counts": mcp_registry.health_counts(names),
        "project_config_path": str(scope_path("project")),
        "global_config_path": str(scope_path("global")),
        "project_config_exists": scope_path("project").exists(),
        "pending_auth": auth_coordinator.pending(),
        "catalog": catalog.public(),
    }


def _has_auth_header(cfg: dict[str, Any]) -> bool:
    headers = cfg.get("headers")
    return isinstance(headers, dict) and any(str(k).lower() == "authorization" for k in headers)
