"""MCP server configuration — discovery, scope handling, and persistence.

Two scopes:

* **project** — The ``.mcp.json`` file in the current working directory
  (Claude Code compatible). Always loaded — you control what goes here.

* **global**  — Aggregated from multiple external tool config files when
  ``state.global_mcp = True`` (toggled with ``/mcp global on``):

    - Jarvis     : ``~/.config/harness-agent/mcp.json``
    - Claude Code: ``~/.claude.json``                (``mcpServers`` key)
    - Claude Code: ``~/.claude/mcp.json``
    - OpenCode   : ``~/.config/opencode/opencode.json`` (``mcp`` key)
    - OpenCode   : ``~/.config/opencode/mcp.json``
    - Cursor     : ``~/.cursor/mcp.json``
    - Codex      : ``~/.codex/config.toml``           (``[mcp_servers.*]``, ``$CODEX_HOME``)
    - Gemini CLI : ``~/.gemini/settings.json``        (``mcpServers`` key)
    - Antigravity: ``~/.gemini/antigravity/mcp_config.json``
    - Kiro       : ``~/.kiro/settings/mcp.json``
    - Copilot CLI: ``~/.copilot/mcp-config.json``
    - Windsurf   : ``~/.codeium/windsurf/mcp_config.json``
    - VS Code    : ``~/.vscode/mcp.json``

Both Jarvis schema (``servers`` + ``auto_connect``) and Claude-Code schema
(``mcpServers``) are accepted at every source. The first source to define a
given name wins (project > Jarvis-global > Claude > OpenCode > Cursor > …).

``/mcp add`` and ``/mcp remove`` only mutate the Jarvis global file. Other
tools' configs are read-only (edit them with their own UIs).
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

try:  # Python 3.11+
    import tomllib as _toml
except ImportError:  # pragma: no cover — 3.10 without tomli just skips Codex
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ImportError:
        _toml = None


MCP_GLOBAL_CONFIG_FILE = pathlib.Path.home() / ".config" / "harness-agent" / "mcp.json"
MCP_PROJECT_CONFIG_FILENAME = ".mcp.json"

# Back-compat alias — some callers / docs reference this name.
MCP_CONFIG_FILE = MCP_GLOBAL_CONFIG_FILE


def _project_config_path() -> pathlib.Path:
    """Path to the project-local MCP config (resolved against the current CWD)."""
    return pathlib.Path.cwd() / MCP_PROJECT_CONFIG_FILENAME


# ── external-tool global sources ─────────────────────────────────────────
# Each tuple is ``(label, path, json_pointer)`` where ``json_pointer`` is a
# dotted path inside the loaded JSON ("" means the root document itself).
# The parser at that pointer must yield either ``{"servers": ...}``,
# ``{"mcpServers": ...}`` or just a server-map at the top level.

def _global_sources() -> list[tuple[str, pathlib.Path, str]]:
    home = pathlib.Path.home()
    codex_home = pathlib.Path(os.environ.get("CODEX_HOME") or home / ".codex")
    return [
        ("jarvis",       MCP_GLOBAL_CONFIG_FILE,                       ""),
        ("claude",       home / ".claude.json",                        ""),
        ("claude",       home / ".claude" / "mcp.json",                ""),
        ("opencode",     home / ".config" / "opencode" / "opencode.json", "mcp"),
        ("opencode",     home / ".config" / "opencode" / "mcp.json",   ""),
        ("cursor",       home / ".cursor" / "mcp.json",                ""),
        ("codex",        codex_home / "config.toml",                   "mcp_servers"),
        ("gemini",       home / ".gemini" / "settings.json",           ""),
        ("antigravity",  home / ".gemini" / "antigravity" / "mcp_config.json", ""),
        ("kiro",         home / ".kiro" / "settings" / "mcp.json",     ""),
        ("copilot",      home / ".copilot" / "mcp-config.json",        ""),
        ("windsurf",     home / ".codeium" / "windsurf" / "mcp_config.json", ""),
        ("vscode",       home / ".vscode" / "mcp.json",                ""),
    ]


# ── parsing helpers ──────────────────────────────────────────────────────

def _normalize_claude_code_entry(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Convert an external server entry to the internal Jarvis schema.

    Accepts a handful of community conventions in addition to the Claude Code
    format:

    * ``command`` may be a string (+ ``args``) **or** a single list giving the
      full argv (OpenCode style)
    * ``env`` and ``environment`` are both honored
    * ``type`` may be ``"local"`` (treated as stdio) or ``"remote"`` (streamable
      http — falls back to sse on its own when the server only speaks that)
    * ``enabled: false`` skips the entry entirely
    * the URL may be ``url``, ``httpUrl`` (Gemini CLI) or ``serverUrl``
      (Windsurf / Antigravity)
    """
    if not isinstance(cfg, dict):
        return None
    if cfg.get("enabled") is False:
        return None
    if not cfg.get("url") and (cfg.get("httpUrl") or cfg.get("serverUrl")):
        cfg = {**cfg, "url": cfg.get("httpUrl") or cfg.get("serverUrl")}

    declared_type = str(cfg.get("type", "")).lower()
    entry: dict[str, Any] = {}

    raw_command = cfg.get("command")
    has_command = raw_command not in (None, "", [])
    has_url = bool(cfg.get("url"))

    is_stdio = (
        declared_type in ("stdio", "local") or
        (declared_type == "" and has_command)
    )
    is_sse = (
        declared_type in ("sse", "http", "remote", "streamable-http", "streamable_http", "streamablehttp") or
        (declared_type == "" and has_url and not has_command)
    )

    if is_stdio and has_command:
        if isinstance(raw_command, list):
            if not raw_command:
                return None
            entry["command"] = str(raw_command[0])
            entry["args"] = [str(x) for x in raw_command[1:]] + [
                str(x) for x in cfg.get("args", [])
            ]
        else:
            entry["command"] = str(raw_command)
            entry["args"] = [str(x) for x in cfg.get("args", [])]
        entry["type"] = "stdio"
        env = cfg.get("env") or cfg.get("environment")
        if isinstance(env, dict):
            entry["env"] = {str(k): str(v) for k, v in env.items()}
        if cfg.get("cwd"):
            entry["cwd"] = str(cfg["cwd"])
        return entry

    if is_sse and has_url:
        entry["type"] = "sse" if declared_type == "sse" else guess_remote_type(str(cfg["url"]))
        entry["url"] = str(cfg["url"])
        headers: dict[str, str] = {}
        if isinstance(cfg.get("headers"), dict):
            headers.update(cfg["headers"])
        # Codex: http_headers, env_http_headers {header: ENV_VAR}, bearer_token_env_var
        if isinstance(cfg.get("http_headers"), dict):
            headers.update({str(k): str(v) for k, v in cfg["http_headers"].items()})
        if isinstance(cfg.get("env_http_headers"), dict):
            headers.update({str(k): "${%s}" % v for k, v in cfg["env_http_headers"].items()})
        if cfg.get("bearer_token_env_var"):
            headers.setdefault("Authorization", "Bearer ${%s}" % cfg["bearer_token_env_var"])
        if headers:
            entry["headers"] = headers
        if cfg.get("oauth") is False:
            entry["oauth"] = False
        return entry

    return None


def _descend(data: Any, pointer: str) -> Any:
    """Navigate a dotted JSON pointer (empty string returns root)."""
    if not pointer:
        return data
    cur = data
    for part in pointer.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _extract_servers(
    data: Any,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Pull servers out of a JSON blob accepting all known schemas."""
    servers: dict[str, dict[str, Any]] = {}
    auto: list[str] = []
    if not isinstance(data, dict):
        return servers, auto

    # Jarvis-native: {"servers": {...}, "auto_connect": [...]}
    raw = data.get("servers")
    if isinstance(raw, dict):
        for name, cfg in raw.items():
            if not isinstance(cfg, dict):
                continue
            entry = dict(cfg)
            entry.setdefault("type", guess_remote_type(str(entry["url"])) if "url" in entry else "stdio")
            servers[name] = entry
    raw_auto = data.get("auto_connect")
    if isinstance(raw_auto, list):
        auto.extend(str(n) for n in raw_auto)

    # Claude Code / Cursor / Windsurf: {"mcpServers": {...}} — all auto.
    cc = data.get("mcpServers")
    if isinstance(cc, dict):
        for name, cfg in cc.items():
            if name in servers:
                continue
            entry = _normalize_claude_code_entry(cfg)
            if entry is None:
                continue
            servers[name] = entry
            if name not in auto:
                auto.append(name)

    # Bare map at root (some VS Code variants).
    if not servers and not cc:
        looks_like_servers = all(
            isinstance(v, dict) and ("command" in v or "url" in v)
            for v in data.values()
        ) and len(data) > 0
        if looks_like_servers:
            for name, cfg in data.items():
                entry = _normalize_claude_code_entry(cfg)
                if entry is not None:
                    servers[name] = entry
                    if name not in auto:
                        auto.append(name)

    return servers, auto


def _parse_config_file(
    path: pathlib.Path,
    pointer: str = "",
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Parse one config file. Returns ``(servers, auto_connect)`` — empty on miss."""
    if not path.exists():
        return {}, []
    if path.suffix == ".toml":
        # Codex: a [mcp_servers.<name>] table per server, same fields as mcpServers.
        if _toml is None:
            return {}, []
        try:
            servers = _descend(_toml.loads(path.read_text(encoding="utf-8")), pointer)
        except (ValueError, OSError):
            return {}, []
        return _extract_servers({"mcpServers": servers} if isinstance(servers, dict) else None)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}, []
    return _extract_servers(_descend(raw, pointer))


def guess_remote_type(url: str) -> str:
    """``sse`` for the classic ``…/sse`` endpoints, otherwise streamable ``http``
    (the connector tries the other transport by itself if it guessed wrong)."""
    path = url.split("?", 1)[0].rstrip("/").lower()
    return "sse" if path.endswith("/sse") else "http"


# ── MCPConfig ────────────────────────────────────────────────────────────

class MCPConfig:
    """Merged view of project + (optionally) global MCP server configuration.

    Each loaded server carries a ``"_source"`` key indicating where it was
    discovered (``"project"`` for the project file, or one of the global
    source labels like ``"jarvis"``, ``"claude"``, ``"opencode"``, etc.). The
    field is stripped before being handed to the runtime connector.
    """

    def __init__(self) -> None:
        # name → server entry (with internal "_source" metadata)
        self._project_servers: dict[str, dict[str, Any]] = {}
        self._project_auto: list[str] = []
        self._project_path: pathlib.Path | None = None

        # Global aggregate (only populated when ``include_global`` was True)
        self._global_servers: dict[str, dict[str, Any]] = {}
        self._global_auto: list[str] = []
        # Per-source breakdown for /mcp paths
        self._global_source_files: list[tuple[str, pathlib.Path, bool, int]] = []
        # name → other global sources that define it too (shadowed)
        self._also: dict[str, list[str]] = {}
        # Whether the most recent load included globals
        self._include_global: bool = False

    # ── load / save ──────────────────────────────────────────────────────

    def load(
        self,
        path: str | pathlib.Path | None = None,
        *,
        project_path: str | pathlib.Path | None = None,
        include_global: bool | None = None,
    ) -> None:
        """Discover servers from project + (optionally) global sources.

        Args:
            path: Override for the **Jarvis global** file (kept positional for
                back-compat with the old single-file API).
            project_path: Override for the project file. Defaults to
                ``CWD/.mcp.json``.
            include_global: If True, load global sources. If None, falls back
                to ``state.global_mcp`` (default False).
        """
        # Resolve include_global from state if not explicit
        if include_global is None:
            try:
                from .. import state
                include_global = bool(getattr(state, "global_mcp", False))
            except Exception:
                include_global = False
        self._include_global = include_global

        # Project scope — always loaded.
        pp = pathlib.Path(project_path) if project_path else _project_config_path()
        if pp.exists():
            servers, auto = _parse_config_file(pp)
            for entry in servers.values():
                entry["_source"] = "project"
            self._project_servers = servers
            self._project_auto = auto
            self._project_path = pp
        else:
            self._project_servers = {}
            self._project_auto = []
            self._project_path = None

        # Global aggregate — only when requested.
        self._global_servers = {}
        self._global_auto = []
        self._global_source_files = []
        self._also = {}

        if include_global:
            sources = _global_sources()
            # If caller overrode the Jarvis-global path, swap it in.
            if path:
                sources = [(lbl, pathlib.Path(path) if lbl == "jarvis" else p, ptr)
                           for (lbl, p, ptr) in sources]
            for label, file_path, pointer in sources:
                servers, auto = _parse_config_file(file_path, pointer)
                count = 0
                for name, entry in servers.items():
                    if name in self._project_servers or name in self._global_servers:
                        also = self._also.setdefault(name, [])
                        if label not in also:
                            also.append(label)
                        continue
                    entry["_source"] = label
                    self._global_servers[name] = entry
                    count += 1
                for n in auto:
                    if n in self._global_servers and n not in self._global_auto:
                        self._global_auto.append(n)
                self._global_source_files.append(
                    (label, file_path, file_path.exists(), count)
                )

    def save(self, path: str | pathlib.Path | None = None) -> None:
        """Persist Jarvis-managed servers back to the Jarvis global file.

        Only servers whose ``_source`` is ``"jarvis"`` (i.e. ones added via
        ``/mcp add``) are written. Servers from other tools' config files are
        never rewritten by Jarvis.
        """
        target = pathlib.Path(path) if path else MCP_GLOBAL_CONFIG_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        servers_out: dict[str, dict[str, Any]] = {}
        for name, entry in self._global_servers.items():
            if entry.get("_source") != "jarvis":
                continue
            servers_out[name] = {k: v for k, v in entry.items() if k != "_source"}
        auto_out = [n for n in self._global_auto if n in servers_out]
        target.write_text(
            json.dumps(
                {"servers": servers_out, "auto_connect": auto_out},
                indent=2,
                ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )

    # ── back-compat shim ─────────────────────────────────────────────────

    @property
    def data(self) -> dict[str, Any]:
        """Merged view kept for legacy callers that read ``config.data`` directly."""
        return {
            "servers": self.list_servers(),
            "auto_connect": self.get_auto_connect(),
        }

    # ── queries ──────────────────────────────────────────────────────────

    def list_servers(self) -> dict[str, dict[str, Any]]:
        """All visible servers — project wins on name collision with globals."""
        merged: dict[str, dict[str, Any]] = {}
        for name, entry in self._global_servers.items():
            merged[name] = {k: v for k, v in entry.items() if k != "_source"}
        for name, entry in self._project_servers.items():
            merged[name] = {k: v for k, v in entry.items() if k != "_source"}
        return merged

    def get_server(self, name: str) -> dict[str, Any] | None:
        entry = self._project_servers.get(name) or self._global_servers.get(name)
        if entry is None:
            return None
        return {k: v for k, v in entry.items() if k != "_source"}

    def get_scope(self, name: str) -> str | None:
        """Return ``'project'``, ``'global'``, or ``None``."""
        if name in self._project_servers:
            return "project"
        if name in self._global_servers:
            return "global"
        return None

    def get_also(self, name: str) -> list[str]:
        """Other sources that define ``name`` too (shadowed by the one in use)."""
        own = self.get_source(name)
        return [s for s in self._also.get(name, []) if s != own]

    def get_source(self, name: str) -> str | None:
        """Per-server source label (``'project'``, ``'jarvis'``, ``'claude'``, …)."""
        if name in self._project_servers:
            return self._project_servers[name].get("_source", "project")
        if name in self._global_servers:
            return self._global_servers[name].get("_source", "global")
        return None

    def project_config_path(self) -> pathlib.Path | None:
        return self._project_path

    def include_global(self) -> bool:
        return self._include_global

    def global_sources(self) -> list[tuple[str, pathlib.Path, bool, int]]:
        """``(label, path, exists, server_count)`` for each scanned global file."""
        return list(self._global_source_files)

    # ── CRUD (Jarvis-managed entries only) ───────────────────────────────

    def add_server(
        self,
        name: str,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        auto_connect: bool = False,
    ) -> None:
        """Add a server to the Jarvis global file.

        Refuses to shadow servers that came from the project file or from
        other tools' config files (those must be edited in their own UIs).
        """
        if name in self._project_servers:
            raise ValueError(
                f"Server '{name}' is defined in the project config "
                f"({self._project_path}); edit that file directly."
            )
        existing = self._global_servers.get(name)
        if existing is not None:
            src = existing.get("_source", "global")
            if src != "jarvis":
                raise ValueError(
                    f"Server '{name}' is provided by the '{src}' tool config "
                    "— edit it there, not in Jarvis."
                )
            raise ValueError(f"Server '{name}' already exists — remove it first")

        if command:
            server_type = "stdio"
        elif url:
            server_type = guess_remote_type(url)
        else:
            raise ValueError("Must provide either --command (stdio) or --url (sse)")

        entry: dict[str, Any] = {"type": server_type}
        if server_type == "stdio":
            entry["command"] = command
            entry["args"] = args or []
            if env:
                entry["env"] = env
            if cwd:
                entry["cwd"] = cwd
        else:
            entry["url"] = url
            if headers:
                entry["headers"] = headers
        entry["_source"] = "jarvis"

        self._global_servers[name] = entry
        if auto_connect and name not in self._global_auto:
            self._global_auto.append(name)

    def remove_server(self, name: str) -> bool:
        """Remove a Jarvis-managed global server. Returns True if it existed.

        Raises ``ValueError`` if the name belongs to another scope/source.
        """
        if name in self._project_servers:
            raise ValueError(
                f"Server '{name}' is defined in the project config "
                f"({self._project_path}); edit that file to remove it."
            )
        entry = self._global_servers.get(name)
        if entry is None:
            return False
        src = entry.get("_source", "global")
        if src != "jarvis":
            raise ValueError(
                f"Server '{name}' is provided by the '{src}' tool config "
                "— remove it there, not in Jarvis."
            )
        del self._global_servers[name]
        if name in self._global_auto:
            self._global_auto.remove(name)
        return True

    def get_auto_connect(self) -> list[str]:
        """Names to auto-connect, in (global-then-project) order, deduped."""
        merged = self.list_servers()
        result: list[str] = []
        for n in self._global_auto:
            if n in merged and n not in result:
                result.append(n)
        for n in self._project_auto:
            if n in merged and n not in result:
                result.append(n)
        return result


# ── project import / merge ────────────────────────────────────────────────

def _normalize_server_entry(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Accept Jarvis-native or Claude-Code-style server entries."""
    if cfg.get("type") in ("stdio", "sse", "http") and ("command" in cfg or "url" in cfg):
        return {k: v for k, v in cfg.items() if not k.startswith("_")}
    return _normalize_claude_code_entry(cfg)


def _json_to_server_candidates(parsed: Any) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    """Extract ``(name, cfg)`` pairs from pasted/import JSON."""
    if not isinstance(parsed, dict):
        raise ValueError("JSON root must be an object")

    candidates: list[tuple[str, dict[str, Any]]] = []
    auto_names: list[str] = []

    if isinstance(parsed.get("mcpServers"), dict):
        for n, c in parsed["mcpServers"].items():
            candidates.append((str(n), c))
            auto_names.append(str(n))
    elif isinstance(parsed.get("servers"), dict):
        for n, c in parsed["servers"].items():
            candidates.append((str(n), c))
        auto_raw = parsed.get("auto_connect", [])
        if isinstance(auto_raw, list):
            auto_names = [str(x) for x in auto_raw]
    elif "command" in parsed or "url" in parsed:
        name = parsed.get("name") or parsed.get("id")
        if not name:
            raise ValueError("bare-form JSON needs a 'name' field")
        candidates.append((str(name), parsed))
        auto_names.append(str(name))
    else:
        raise ValueError(
            "JSON must contain 'mcpServers', 'servers', or a bare 'command'/'url'"
        )
    return candidates, auto_names


def collect_global_servers() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Scan every global MCP source (Cursor, Claude, Jarvis, …)."""
    servers: dict[str, dict[str, Any]] = {}
    auto: list[str] = []
    for _label, file_path, pointer in _global_sources():
        file_servers, file_auto = _parse_config_file(file_path, pointer)
        for name, entry in file_servers.items():
            if name not in servers:
                servers[name] = entry
        for n in file_auto:
            if n in servers and n not in auto:
                auto.append(n)
    return servers, auto


def global_source_summary() -> tuple[int, list[str]]:
    """``(server count, tools)`` across every global source, whether or not the
    global scope is on — so a project-only view can say what turning it on adds."""
    names: set[str] = set()
    tools: list[str] = []
    for label, file_path, pointer in _global_sources():
        file_servers, _auto = _parse_config_file(file_path, pointer)
        if file_servers and label not in tools:
            tools.append(label)
        names.update(file_servers)
    return len(names), tools


def save_project_mcp_file(
    servers: dict[str, dict[str, Any]],
    auto_connect: list[str],
    project_path: str | pathlib.Path | None = None,
) -> pathlib.Path:
    """Write ``.mcp.json`` in the project directory."""
    path = pathlib.Path(project_path) if project_path else _project_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    servers_out = {
        name: {k: v for k, v in entry.items() if not k.startswith("_")}
        for name, entry in servers.items()
    }
    auto_out = [n for n in auto_connect if n in servers_out]
    path.write_text(
        json.dumps(
            {"servers": servers_out, "auto_connect": auto_out},
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    return path


def import_server_to_project(
    name: str,
    *,
    project_path: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    """Copy one global MCP server into the project ``.mcp.json`` file."""
    path = pathlib.Path(project_path) if project_path else _project_config_path()
    project_servers, project_auto = (
        _parse_config_file(path) if path.exists() else ({}, [])
    )

    if name in project_servers:
        return {"added": [], "skipped": [name], "path": str(path)}

    global_servers, global_auto = collect_global_servers()
    entry = global_servers.get(name)
    if entry is None:
        return {
            "added": [],
            "skipped": [],
            "path": str(path),
            "error": f"'{name}' not found in global MCP configs",
        }

    project_servers[name] = entry
    if name in global_auto and name not in project_auto:
        project_auto.append(name)
    save_project_mcp_file(project_servers, project_auto, path)

    return {"added": [name], "skipped": [], "path": str(path)}


def export_server_to_global(
    name: str,
) -> dict[str, Any]:
    """Copy one project MCP server into the Jarvis global config file."""
    path = MCP_GLOBAL_CONFIG_FILE
    global_servers, global_auto = (
        _parse_config_file(path) if path.exists() else ({}, [])
    )

    if name in global_servers:
        return {"added": [], "skipped": [name], "path": str(path)}

    project_servers, project_auto = _parse_config_file(_project_config_path())
    entry = project_servers.get(name)
    if entry is None:
        return {
            "added": [],
            "skipped": [],
            "path": str(path),
            "error": f"'{name}' not found in project MCP config",
        }

    entry = {k: v for k, v in entry.items() if not k.startswith("_")}
    global_servers[name] = entry
    if name in project_auto and name not in global_auto:
        global_auto.append(name)

    save_project_mcp_file(global_servers, global_auto, path)
    return {"added": [name], "skipped": [], "path": str(path)}


def import_global_to_project(
    project_path: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    """Copy global MCP servers into the project ``.mcp.json`` file."""
    path = pathlib.Path(project_path) if project_path else _project_config_path()
    project_servers, project_auto = (
        _parse_config_file(path) if path.exists() else ({}, [])
    )
    global_servers, global_auto = collect_global_servers()

    added: list[str] = []
    skipped: list[str] = []
    for name, entry in global_servers.items():
        if name in project_servers:
            skipped.append(name)
            continue
        project_servers[name] = entry
        added.append(name)

    for n in global_auto:
        if n in project_servers and n not in project_auto:
            project_auto.append(n)

    if added:
        save_project_mcp_file(project_servers, project_auto, path)

    return {
        "added": added,
        "skipped": skipped,
        "path": str(path),
        "global_count": len(global_servers),
    }


def merge_json_into_project(
    parsed: Any,
    *,
    project_path: str | pathlib.Path | None = None,
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Merge pasted JSON server definitions into the project ``.mcp.json``."""
    candidates, auto_names = _json_to_server_candidates(parsed)
    if not candidates:
        return {"added": [], "skipped": [], "path": str(_project_config_path())}

    path = pathlib.Path(project_path) if project_path else _project_config_path()
    project_servers, project_auto = (
        _parse_config_file(path) if path.exists() else ({}, [])
    )

    added: list[str] = []
    skipped: list[str] = []
    for name, cfg in candidates:
        if name in project_servers:
            if skip_existing:
                skipped.append(name)
                continue
            raise ValueError(f"server '{name}' already exists in project")
        normalized = _normalize_server_entry(cfg)
        if normalized is None:
            raise ValueError(f"server '{name}': missing command or url")
        project_servers[name] = normalized
        added.append(name)

    for n in auto_names:
        if n in project_servers and n not in project_auto:
            project_auto.append(n)

    if added:
        save_project_mcp_file(project_servers, project_auto, path)

    return {"added": added, "skipped": skipped, "path": str(path)}


# ── scope-aware writers (install / move / remove) ────────────────────────
#
# These edit the file for a scope directly and keep whatever schema is already
# there: a Claude-Code style ``.mcp.json`` (``mcpServers``) stays that way, so
# the file keeps working in the other tools that read it. A new project file is
# written in that shared format; the Jarvis global file uses ``servers`` +
# ``auto_connect``.

SCOPES = ("project", "global")


def scope_path(scope: str, project_path: str | pathlib.Path | None = None) -> pathlib.Path:
    if scope == "project":
        return pathlib.Path(project_path) if project_path else _project_config_path()
    if scope == "global":
        return MCP_GLOBAL_CONFIG_FILE
    raise ValueError(f"scope must be 'project' or 'global', not '{scope}'")


def _read_raw(path: pathlib.Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_raw(path: pathlib.Path, data: dict[str, Any], *, private: bool) -> None:
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if private:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, path)


def _slot(raw: dict[str, Any], scope: str) -> str:
    if scope == "global":
        return "servers" if "mcpServers" not in raw else "mcpServers"
    if "mcpServers" in raw:
        return "mcpServers"
    if "servers" in raw:
        return "servers"
    return "mcpServers"


def server_in_scope(name: str, scope: str, project_path: str | pathlib.Path | None = None) -> bool:
    raw = _read_raw(scope_path(scope, project_path))
    return any(isinstance(raw.get(k), dict) and name in raw[k] for k in ("servers", "mcpServers"))


def read_scope_server(name: str, scope: str, project_path: str | pathlib.Path | None = None) -> dict[str, Any] | None:
    servers, _auto = _parse_config_file(scope_path(scope, project_path))
    entry = servers.get(name)
    return {k: v for k, v in entry.items() if not k.startswith("_")} if entry else None


def write_server(
    name: str,
    entry: dict[str, Any],
    *,
    scope: str,
    replace: bool = False,
    auto_connect: bool = True,
    project_path: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    """Add ``entry`` under ``name`` in the file for ``scope``. Returns ``{path, replaced}``."""
    path = scope_path(scope, project_path)
    raw = _read_raw(path)
    key = _slot(raw, scope)
    servers = raw.get(key)
    if not isinstance(servers, dict):
        servers = raw[key] = {}
    existed = name in servers
    if existed and not replace:
        raise ValueError(f"'{name}' is already in the {scope} config ({path})")
    servers[name] = {k: v for k, v in entry.items() if not str(k).startswith("_")}
    if key == "servers":
        auto = raw.get("auto_connect")
        if not isinstance(auto, list):
            auto = raw["auto_connect"] = []
        if auto_connect and name not in auto:
            auto.append(name)
        if not auto_connect and name in auto:
            auto.remove(name)
    _write_raw(path, raw, private=(scope == "global"))
    return {"path": str(path), "replaced": existed}


def delete_server(name: str, *, scope: str, project_path: str | pathlib.Path | None = None) -> bool:
    """Remove ``name`` from the file for ``scope``. True if it was there."""
    path = scope_path(scope, project_path)
    raw = _read_raw(path)
    removed = False
    for key in ("servers", "mcpServers"):
        block = raw.get(key)
        if isinstance(block, dict) and name in block:
            del block[name]
            removed = True
    auto = raw.get("auto_connect")
    if isinstance(auto, list) and name in auto:
        auto.remove(name)
    if removed:
        _write_raw(path, raw, private=(scope == "global"))
    return removed


def locate_server(name: str) -> list[dict[str, str]]:
    """Every place ``name`` is defined, whether or not that scope is switched on:
    ``[{scope, source, path, editable}]``. Other tools' configs are not editable."""
    found: list[dict[str, str]] = []
    pp = _project_config_path()
    if name in _parse_config_file(pp)[0]:
        found.append({"scope": "project", "source": "project", "path": str(pp), "editable": "yes"})
    for label, file_path, pointer in _global_sources():
        if name in _parse_config_file(file_path, pointer)[0]:
            found.append({
                "scope": "global",
                "source": label,
                "path": str(file_path),
                "editable": "yes" if label == "jarvis" else "no",
            })
    return found


def ensure_global_visible() -> bool:
    """Turn the global MCP scope on if it was off (a server installed globally is
    invisible otherwise). Returns True when this call switched it on."""
    from .. import state

    if getattr(state, "global_mcp", False):
        return False
    state.global_mcp = True
    try:
        state.save_mcp_config()
    except Exception:
        pass
    return True


# ── module-level singleton ───────────────────────────────────────────────

_config: MCPConfig | None = None


def get_config() -> MCPConfig:
    """Process-wide MCP config; reloads when ``state.global_mcp`` scope changes."""
    global _config
    try:
        from .. import state
        desired_global = bool(getattr(state, "global_mcp", False))
    except Exception:
        desired_global = False
    if _config is None or _config._include_global != desired_global:
        _config = MCPConfig()
        _config.load()
    return _config


def reload_config() -> MCPConfig:
    """Re-read every source from disk using the current ``state.global_mcp`` flag."""
    global _config
    _config = MCPConfig()
    _config.load()
    return _config
