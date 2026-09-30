"""MCP (Model Context Protocol) support — connect external tool servers to Jarvis.

Usage:
    /mcp list                        — show configured & connected servers
    /mcp add <link|npx …|json|name>  — add a server (--project or --global)
    /mcp add <name> --command <cmd>  — add a stdio server (classic form)
    /mcp add <name> --url <url>      — add a hosted server (classic form)
    /mcp auth <name>                 — sign in to a hosted server in the browser
    /mcp key <name>                  — enter the API key / token a server needs
    /mcp remove <name>               — remove a server config (project or global)
    /mcp connect <name>              — connect a configured server
    /mcp disconnect <name>           — disconnect a server
    /mcp reload                      — reload config from file

Layout: ``config`` (files + scopes), ``registry`` (connections, tools, health),
``auth`` (browser sign-in), ``secrets`` (keys kept out of config), ``install``
(parse / add / remove — shared by the agent tools and both UIs), ``catalog``
(well-known servers), ``manager`` (the /mcp command).
"""

from .config import MCPConfig
from .registry import mcp_registry
from .manager import handle_mcp_command

__all__ = ["MCPConfig", "mcp_registry", "handle_mcp_command"]
