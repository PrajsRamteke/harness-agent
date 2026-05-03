"""Tool registry: TOOLS (schemas) and FUNC (name -> callable)."""
from .files import read_file, write_file, edit_file
from .read_document import read_document
from .dirs import list_dir, glob_files, rank_files, fast_find
from .shell import run_bash
from .search import search_code
from .git import git_status, git_diff, git_log
from .context import resolve_context, read_bundle
from .mac import (
    applescript, launch_app, focus_app, quit_app, list_apps, frontmost_app,
    read_ui, click_element, wait, check_permissions,
    type_text, key_press, click_menu, click_at,
    clipboard_get, clipboard_set,
    open_url, notify, speck, shortcut_run, mac_control,
)
from .web import web_search, fetch_url, verified_search
from .ocr import read_image_text, read_images_text
from .memory import memory_save, memory_list, memory_delete, MEMORY_TOOLS
from .skills import skill_save, skill_search, skill_list, skill_delete, SKILL_TOOLS
from .schemas_core import CORE_TOOLS, CONTEXT_TOOLS, INTERNET_TOOLS, OCR_TOOLS
from .schemas_mac import MAC_TOOLS

# MCP group starts empty — populated dynamically by the MCP registry
# when servers connect. Import is deferred to avoid circular imports.
MCP_TOOLS: list[dict] = []
TOOLS = CORE_TOOLS + MAC_TOOLS + INTERNET_TOOLS + MEMORY_TOOLS + SKILL_TOOLS + OCR_TOOLS + MCP_TOOLS
TOOL_GROUPS: dict[str, list[dict]] = {
    "core": CORE_TOOLS,
    "context": CONTEXT_TOOLS,
    "mac": MAC_TOOLS,
    "internet": INTERNET_TOOLS,
    "memory": MEMORY_TOOLS,
    "skills": SKILL_TOOLS,
    "ocr": OCR_TOOLS,
    "mcp": MCP_TOOLS,
}
TOOL_NAME_TO_GROUP: dict[str, str] = {
    tool["name"]: group
    for group, tools in TOOL_GROUPS.items()
    for tool in tools
}

FUNC = {
    "read_file": read_file, "read_document": read_document, "write_file": write_file, "edit_file": edit_file,
    "list_dir": list_dir, "run_bash": run_bash, "search_code": search_code,
    "glob_files": glob_files, "rank_files": rank_files,
    "fast_find": fast_find,
    "git_status": git_status, "git_diff": git_diff,
    "git_log": git_log,
    # context (connected context pack — replaces 5-20 reads with 1 call)
    "resolve_context": resolve_context,
    "read_bundle": read_bundle,
    # mac
    "launch_app": launch_app, "focus_app": focus_app, "quit_app": quit_app,
    "list_apps": list_apps, "frontmost_app": frontmost_app,
    "applescript": applescript, "read_ui": read_ui,
    "click_element": click_element, "wait": wait,
    "check_permissions": check_permissions,
    "type_text": type_text, "key_press": key_press,
    "click_menu": click_menu, "click_at": click_at,
    "clipboard_get": clipboard_get, "clipboard_set": clipboard_set,
    "open_url": open_url, "notify": notify, "speck": speck,
    "shortcut_run": shortcut_run, "mac_control": mac_control,
    # internet
    "web_search": web_search, "fetch_url": fetch_url,
    "verified_search": verified_search,
    # memory
    "memory_save": memory_save, "memory_list": memory_list,
    "memory_delete": memory_delete,
    # skills (agent self-learning)
    "skill_save": skill_save, "skill_search": skill_search,
    "skill_list": skill_list, "skill_delete": skill_delete,
    # ocr
    "read_image_text": read_image_text,
    "read_images_text": read_images_text,
}

# ── MCP registry integration ─────────────────────────────────────────────
# Wire the MCP registry into Jarvis's tool dictionaries so connected MCP
# servers dynamically add/remove their tools.
from ..mcp.registry import mcp_registry as _mcp_registry

_mcp_registry.init_jarvis(
    func_dict=FUNC,
    tool_groups=TOOL_GROUPS,
    tools_list=TOOLS,
    tool_name_to_group=TOOL_NAME_TO_GROUP,
)

# Re-export for convenience
from ..mcp.registry import mcp_registry
from ..mcp.config import get_config, MCPConfig
from ..mcp import handle_mcp_command
