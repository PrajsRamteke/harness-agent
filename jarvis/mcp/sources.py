"""Shared MCP server *source* presentation primitives.

Icons, human labels, display ordering, and endpoint formatting are used by
both the CLI manager (``mcp/manager.py``) and the TUI modal
(``tui/mcp_modal.py``). Keeping them here avoids the two copies drifting apart.
"""

SOURCE_ICONS = {
    "project":  "▣",
    "jarvis":   "✦",
    "claude":   "◆",
    "opencode": "◇",
    "cursor":   "⌘",
    "windsurf": "≈",
    "vscode":   "⬡",
}

SOURCE_LABELS = {
    "project":  "Project",
    "jarvis":   "Jarvis",
    "claude":   "Claude Code",
    "opencode": "OpenCode",
    "cursor":   "Cursor",
    "windsurf": "Windsurf",
    "vscode":   "VS Code",
}

SOURCE_ORDER = ["jarvis", "claude", "opencode", "cursor", "windsurf", "vscode"]


def format_endpoint(cfg: dict, max_len: int = 64) -> str:
    """Render the runtime command line / URL for an MCP server config."""
    if cfg.get("url"):
        s = str(cfg["url"])
    else:
        parts = [str(cfg.get("command", ""))]
        parts += [str(a) for a in cfg.get("args", [])]
        s = " ".join(p for p in parts if p)
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s
