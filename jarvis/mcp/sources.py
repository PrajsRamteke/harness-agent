"""Shared MCP server *source* presentation primitives.

Icons, human labels, display ordering, and endpoint formatting are used by
both the CLI manager (``mcp/manager.py``) and the TUI modal
(``tui/mcp_modal.py``). Keeping them here avoids the two copies drifting apart.
"""

from ..utils.origins import TOOLS

# Source ids are the shared tool ids (utils/origins.py) — the same tags the
# agent and skill lists show.
SOURCE_ICONS = {tool: icon for tool, (_label, icon) in TOOLS.items()}
SOURCE_LABELS = {tool: label for tool, (label, _icon) in TOOLS.items()}
SOURCE_ORDER = [t for t in TOOLS if t not in ("project", "agents")]


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
