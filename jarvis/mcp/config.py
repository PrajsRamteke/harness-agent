"""MCP server configuration — read/write ~/.config/harness-agent/mcp.json."""

from __future__ import annotations

import json
import pathlib
from typing import Any


MCP_CONFIG_FILE = pathlib.Path.home() / ".config" / "harness-agent" / "mcp.json"

# Default config template
_DEFAULT_CONFIG: dict[str, Any] = {
    "servers": {},
    "auto_connect": [],
}


class MCPConfig:
    """Manage MCP server configurations stored in a JSON file.

    Schema:
    ```json
    {
      "servers": {
        "<name>": {
          "type": "stdio" | "sse",
          "command": "/path/to/binary",
          "args": ["--flag", "value"],
          "env": {"KEY": "VALUE"},
          "cwd": "/optional/working/dir",
          "url": "http://localhost:3000/sse",
          "headers": {"Authorization": "Bearer ..."}
        }
      },
      "auto_connect": ["server1", "server2"]
    }
    ```
    """

    def __init__(self) -> None:
        self.data: dict[str, Any] = _DEFAULT_CONFIG.copy()

    # ── load / save ──────────────────────────────────────────────────────

    def load(self, path: str | pathlib.Path | None = None) -> None:
        """Load config from JSON file. Missing file → empty config."""
        path = pathlib.Path(path) if path else MCP_CONFIG_FILE
        if not path.exists():
            self.data = _DEFAULT_CONFIG.copy()
            return
        try:
            raw = path.read_text(encoding="utf-8")
            self.data = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            self.data = _DEFAULT_CONFIG.copy()

    def save(self, path: str | pathlib.Path | None = None) -> None:
        """Serialize config to JSON file, creating parent dir if needed."""
        path = pathlib.Path(path) if path else MCP_CONFIG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # ── server CRUD ──────────────────────────────────────────────────────

    def list_servers(self) -> dict[str, dict[str, Any]]:
        """Return dict of all configured servers (name → config)."""
        return dict(self.data.get("servers", {}))

    def get_server(self, name: str) -> dict[str, Any] | None:
        """Get a single server config by name."""
        return self.data.get("servers", {}).get(name)

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
        """Add or update a server configuration."""
        if name in self.data.setdefault("servers", {}):
            raise ValueError(f"Server '{name}' already exists — remove it first")

        # Determine transport type
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
        else:  # sse
            entry["url"] = url
            if headers:
                entry["headers"] = headers

        self.data["servers"][name] = entry
        if auto_connect:
            self.data.setdefault("auto_connect", [])
            if name not in self.data["auto_connect"]:
                self.data["auto_connect"].append(name)

    def remove_server(self, name: str) -> bool:
        """Remove a server config. Returns True if it existed."""
        servers = self.data.get("servers", {})
        if name not in servers:
            return False
        del servers[name]
        # Clean from auto_connect too
        auto = self.data.get("auto_connect", [])
        if name in auto:
            auto.remove(name)
        return True

    def get_auto_connect(self) -> list[str]:
        """Return list of server names to auto-connect."""
        return list(self.data.get("auto_connect", []))


# Global config instance
_config: MCPConfig | None = None


def get_config() -> MCPConfig:
    """Get or create the global MCP config singleton."""
    global _config
    if _config is None:
        _config = MCPConfig()
        _config.load()
    return _config


def reload_config() -> MCPConfig:
    """Reload config from disk and return fresh instance."""
    global _config
    _config = MCPConfig()
    _config.load()
    return _config
