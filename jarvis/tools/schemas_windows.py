"""Tool JSON schemas for Windows desktop control tools."""

WINDOWS_TOOLS = [
    {"name": "launch_app", "description": "Launch a Windows app by name or path (e.g. 'Notepad', 'chrome', 'C:\\\\Program Files\\\\...\\\\app.exe').",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "focus_app", "description": "Bring a running app window to the front.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "quit_app", "description": "Quit/force-stop a Windows app by process name or window title match.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "list_apps", "description": "List visible running apps (windows with titles).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "frontmost_app", "description": "Get the title of the active/frontmost window.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "run_powershell", "description": "Run arbitrary PowerShell. Highest-leverage Windows automation. Use for Explorer, Edge, Outlook, Notepad, scheduled tasks, registry, WMI.",
     "input_schema": {"type": "object", "properties": {
        "code": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["code"]}},
    {"name": "read_ui", "description": "Read the UI Automation tree of an app as text (no screenshot). Hierarchical dump of roles, names, and center coordinates. Use before click/type.",
     "input_schema": {"type": "object", "properties": {
        "app": {"type": "string", "description": "window title substring; blank = active window"},
        "max_depth": {"type": "integer"},
        "max_lines": {"type": "integer"},
        "max_chars": {"type": "integer"}}}},
    {"name": "click_element", "description": "Find a UI element by text (name/value, case-insensitive) and click it. Optional role filter ('Button','Edit','MenuItem',…).",
     "input_schema": {"type": "object", "properties": {
        "app": {"type": "string"}, "query": {"type": "string"},
        "role": {"type": "string"}, "nth": {"type": "integer"}},
        "required": ["app", "query"]}},
    {"name": "wait", "description": "Sleep N seconds to let the UI settle after a click/keystroke.",
     "input_schema": {"type": "object", "properties": {"seconds": {"type": "number"}}}},
    {"name": "check_permissions", "description": "Verify UI Automation is working. Call first if read_ui/click_element fail.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "type_text", "description": "Type a string into the focused app via keyboard simulation.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "key_press", "description": "Press a key or chord, e.g. 'enter', 'ctrl+f', 'ctrl+shift+t', 'down', 'win+r'.",
     "input_schema": {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]}},
    {"name": "click_menu", "description": "Click a menu item by path, e.g. app='Notepad', path=['File','Save As'].",
     "input_schema": {"type": "object", "properties": {
        "app": {"type": "string"},
        "path": {"type": "array", "items": {"type": "string"}}}, "required": ["app", "path"]}},
    {"name": "click_at", "description": "Click at absolute screen coordinates (last resort).",
     "input_schema": {"type": "object", "properties": {
        "x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}},
    {"name": "clipboard_get", "description": "Return current clipboard text.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "clipboard_set", "description": "Set clipboard text.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "open_url", "description": "Open a URL or file path in the default handler.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "notify", "description": "Show a Windows toast notification.",
     "input_schema": {"type": "object", "properties": {
        "title": {"type": "string"}, "message": {"type": "string"}}, "required": ["title"]}},
    {"name": "speck", "description": "Speak text aloud (Windows SAPI TTS). Brief utterances only — user hears this live. Optional voice name, optional rate hint.",
     "input_schema": {"type": "object", "properties": {
        "text": {"type": "string"},
        "voice": {"type": "string"},
        "rate": {"type": "integer"}}, "required": ["text"]}},
    {"name": "win_control", "description": "System controls. action ∈ {volume, mute, unmute, battery, wifi_on, wifi_off, sleep, lock, dark_mode, light_mode, toggle_dark}.",
     "input_schema": {"type": "object", "properties": {
        "action": {"type": "string"}, "value": {"type": "string"}}, "required": ["action"]}},
]
