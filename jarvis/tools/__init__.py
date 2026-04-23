"""Tool registry: TOOLS (schemas) and FUNC (name -> callable)."""
from .files import read_file, write_file, edit_file
from .dirs import list_dir, glob_files
from .shell import run_bash
from .search import search_code
from .git import git_status, git_diff, git_log
from .mac import (
    applescript, launch_app, focus_app, quit_app, list_apps, frontmost_app,
    read_ui, click_element, wait, check_permissions,
    type_text, key_press, click_menu, click_at,
    clipboard_get, clipboard_set,
    open_url, notify, shortcut_run, mac_control,
)
from .web import web_search, fetch_url, verified_search
from .schemas_core import CORE_TOOLS, INTERNET_TOOLS
from .schemas_mac import MAC_TOOLS

TOOLS = CORE_TOOLS + MAC_TOOLS + INTERNET_TOOLS

FUNC = {
    "read_file": read_file, "write_file": write_file, "edit_file": edit_file,
    "list_dir": list_dir, "run_bash": run_bash, "search_code": search_code,
    "glob_files": glob_files, "git_status": git_status, "git_diff": git_diff,
    "git_log": git_log,
    # mac
    "launch_app": launch_app, "focus_app": focus_app, "quit_app": quit_app,
    "list_apps": list_apps, "frontmost_app": frontmost_app,
    "applescript": applescript, "read_ui": read_ui,
    "click_element": click_element, "wait": wait,
    "check_permissions": check_permissions,
    "type_text": type_text, "key_press": key_press,
    "click_menu": click_menu, "click_at": click_at,
    "clipboard_get": clipboard_get, "clipboard_set": clipboard_set,
    "open_url": open_url, "notify": notify,
    "shortcut_run": shortcut_run, "mac_control": mac_control,
    # internet
    "web_search": web_search, "fetch_url": fetch_url,
    "verified_search": verified_search,
}
