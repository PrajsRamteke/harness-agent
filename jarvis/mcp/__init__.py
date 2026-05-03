"""MCP (Model Context Protocol) support — connect external tool servers to Jarvis.

Usage:
    /mcp list                        — show configured & connected servers
    /mcp add <name> --command <cmd>  — add a stdio server
    /mcp add <name> --url <url>      — add an SSE server
    /mcp remove <name>               — remove a server config
    /mcp connect <name>              — connect a configured server
    /mcp disconnect <name>           — disconnect a server
    /mcp reload                      — reload config from file
"""

from .config import MCPConfig
from .registry import mcp_registry
from .manager import handle_mcp_command

__all__ = ["MCPConfig", "mcp_registry", "handle_mcp_command"]
