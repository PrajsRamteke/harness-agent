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
import pathlib
from typing import Any


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
    return [
        ("jarvis",       MCP_GLOBAL_CONFIG_FILE,                       ""),
        ("claude",       home / ".claude.json",                        ""),
        ("claude",       home / ".claude" / "mcp.json",                ""),
        ("opencode",     home / ".config" / "opencode" / "opencode.json", "mcp"),
        ("opencode",     home / ".config" / "opencode" / "mcp.json",   ""),
        ("cursor",       home / ".cursor" / "mcp.json",                ""),
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
    * ``type`` may be ``"local"`` (treated as stdio) or ``"remote"`` (sse)
    * ``enabled: false`` skips the entry entirely
    """
    if not isinstance(cfg, dict):
        return None
    if cfg.get("enabled") is False:
        return None

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
        declared_type in ("sse", "http", "remote") or
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
        entry["type"] = "sse"
        entry["url"] = str(cfg["url"])
        if isinstance(cfg.get("headers"), dict):
            entry["headers"] = dict(cfg["headers"])
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
            entry.setdefault("type", "sse" if "url" in entry else "stdio")
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
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}, []
    return _extract_servers(_descend(raw, pointer))


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
            server_type = "sse"
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


# ── module-level singleton ───────────────────────────────────────────────

_config: MCPConfig | None = None


def get_config() -> MCPConfig:
    """Process-wide MCP config; loads on first call respecting current scope flag."""
    global _config
    if _config is None:
        _config = MCPConfig()
        _config.load()
    return _config


def reload_config() -> MCPConfig:
    """Re-read every source from disk using the current ``state.global_mcp`` flag."""
    global _config
    _config = MCPConfig()
    _config.load()
    return _config
