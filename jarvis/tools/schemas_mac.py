"""Tool JSON schemas for macOS control tools."""

MAC_TOOLS = [
    {"name":"launch_app","description":"Launch a Mac app by name (e.g. 'WhatsApp', 'Safari').",
     "input_schema":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}},
    {"name":"focus_app","description":"Bring a running app to front / activate it.",
     "input_schema":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}},
    {"name":"quit_app","description":"Quit a Mac app.",
     "input_schema":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}},
    {"name":"list_apps","description":"List visible running apps.",
     "input_schema":{"type":"object","properties":{}}},
    {"name":"frontmost_app","description":"Get the name of the frontmost app.",
     "input_schema":{"type":"object","properties":{}}},
    {"name":"applescript","description":"Run arbitrary AppleScript. Highest-leverage Mac automation. Use for Messages, Mail, Safari, Finder, Music, Notes, Reminders, Calendar, System Events.",
     "input_schema":{"type":"object","properties":{
        "code":{"type":"string"},"timeout":{"type":"integer"}},"required":["code"]}},
    {"name":"read_ui","description":"Read the accessibility UI tree of an app as text (no screenshot, no OCR). Hierarchical dump of every visible element: role, name, value, description, and center coordinates. Use this to SEE the screen before deciding what to click or type.",
     "input_schema":{"type":"object","properties":{
        "app":{"type":"string","description":"app name; blank = frontmost"},
        "max_depth":{"type":"integer"},
        "max_lines":{"type":"integer"},
        "max_chars":{"type":"integer"}}}},
    {"name":"click_element","description":"Find a UI element by text (matches name/value/description, case-insensitive) and click it. Much more reliable than click_at. Optional role filter ('button','row','textfield','link',…).",
     "input_schema":{"type":"object","properties":{
        "app":{"type":"string"},"query":{"type":"string"},
        "role":{"type":"string"},"nth":{"type":"integer"}},
        "required":["app","query"]}},
    {"name":"wait","description":"Sleep N seconds to let the UI settle after a click/keystroke before reading it again.",
     "input_schema":{"type":"object","properties":{"seconds":{"type":"number"}}}},
    {"name":"check_permissions","description":"Verify macOS Accessibility permission is granted to the terminal. Call this first if UI tools are failing.",
     "input_schema":{"type":"object","properties":{}}},
    {"name":"type_text","description":"Type a string into the frontmost app via keystroke.",
     "input_schema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}},
    {"name":"key_press","description":"Press a key or chord, e.g. 'return', 'cmd+f', 'cmd+shift+t', 'down'.",
     "input_schema":{"type":"object","properties":{"keys":{"type":"string"}},"required":["keys"]}},
    {"name":"click_menu","description":"Click a menu item by path, e.g. app='Safari', path=['File','New Window'].",
     "input_schema":{"type":"object","properties":{
        "app":{"type":"string"},
        "path":{"type":"array","items":{"type":"string"}}},"required":["app","path"]}},
    {"name":"click_at","description":"Click at absolute screen coordinates (last resort).",
     "input_schema":{"type":"object","properties":{
        "x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]}},
    {"name":"clipboard_get","description":"Return current clipboard text.",
     "input_schema":{"type":"object","properties":{}}},
    {"name":"clipboard_set","description":"Set clipboard text.",
     "input_schema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}},
    {"name":"open_url","description":"Open a URL or file path in the default handler (e.g. 'https://…', 'whatsapp://send?phone=…').",
     "input_schema":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}},
    {"name":"notify","description":"Show a macOS notification banner.",
     "input_schema":{"type":"object","properties":{
        "title":{"type":"string"},"message":{"type":"string"}},"required":["title"]}},
    {"name":"speck","description":"Speak text aloud (macOS TTS). Use only for brief, human-style utterances — the user hears this like a real conversation: a few words, not a paragraph. For long explanations, reply in text and speck a short blip (e.g. status). Optional `voice` (say -v), optional `rate` (words/min, 0=default).",
     "input_schema":{"type":"object","properties":{
        "text":{"type":"string","description":"A handful of words or one very short sentence (think in-person, not a script)."},
        "voice":{"type":"string","description":"Voice name; omit for default. Run `say -v '?'` in shell to list."},
        "rate":{"type":"integer","description":"Speech rate in words per minute; 0 = default."}},"required":["text"]}},
    {"name":"shortcut_run","description":"Run an Apple Shortcut by name, optionally with text input.",
     "input_schema":{"type":"object","properties":{
        "name":{"type":"string"},"input_text":{"type":"string"}},"required":["name"]}},
    {"name":"mac_control","description":"System controls. action ∈ {volume, mute, unmute, battery, wifi_on, wifi_off, sleep, lock, dark_mode, light_mode, toggle_dark}.",
     "input_schema":{"type":"object","properties":{
        "action":{"type":"string"},"value":{"type":"string"}},"required":["action"]}},
]
