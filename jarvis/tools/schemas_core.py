"""Tool JSON schemas for file/shell/git/internet tools."""

CORE_TOOLS = [
    {"name":"read_file","description":"Read a file. Optional offset/limit for line ranges.",
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string"},
        "offset":{"type":"integer","description":"0-indexed starting line"},
        "limit":{"type":"integer","description":"number of lines; 0 = all"}},
        "required":["path"]}},
    {"name":"write_file","description":"Create or overwrite a file",
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}},
    {"name":"edit_file","description":"Replace old_str with new_str. old_str must be unique unless replace_all=true.",
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string"},"old_str":{"type":"string"},
        "new_str":{"type":"string"},"replace_all":{"type":"boolean"}},
        "required":["path","old_str","new_str"]}},
    {"name":"list_dir","description":"List directory entries",
     "input_schema":{"type":"object","properties":{"path":{"type":"string"}}}},
    {"name":"run_bash","description":"Execute a shell command in the working directory",
     "input_schema":{"type":"object","properties":{
        "cmd":{"type":"string"},"timeout":{"type":"integer"}},"required":["cmd"]}},
    {"name":"search_code","description":"Regex search with ripgrep (or grep fallback)",
     "input_schema":{"type":"object","properties":{
        "pattern":{"type":"string"},"path":{"type":"string"}},"required":["pattern"]}},
    {"name":"glob_files","description":"Find files by glob pattern (e.g. '**/*.py')",
     "input_schema":{"type":"object","properties":{"pattern":{"type":"string"}},"required":["pattern"]}},
    {"name":"git_status","description":"git status","input_schema":{"type":"object","properties":{}}},
    {"name":"git_diff","description":"git diff","input_schema":{"type":"object","properties":{"path":{"type":"string"}}}},
    {"name":"git_log","description":"git log","input_schema":{"type":"object","properties":{"n":{"type":"integer"}}}},
]

OCR_TOOLS = [
    {"name": "read_image_text", "description": (
        "Extract text from an image file using macOS Vision framework (on-device OCR). "
        "Supports PNG, JPG, JPEG, HEIC, TIFF, BMP. Accurate, no internet required. "
        "Use this whenever the user points to a screenshot, photo, or image with text in it."
    ),
     "input_schema": {"type": "object", "properties": {
        "path": {"type": "string", "description": "Absolute or relative path to the image file"}},
        "required": ["path"]}},
    {"name": "read_images_text", "description": (
        "Bulk OCR many image files concurrently using macOS Vision. Use this for folders "
        "with many screenshots/photos where only some files contain useful IDs, documents, "
        "forms, licenses, or other important text. It scans only image extensions, limits "
        "the number of files, and returns compact per-file text previews to save tokens."
    ),
     "input_schema": {"type": "object", "properties": {
        "paths": {"type": "array", "items": {"type": "string"},
                  "description": "Optional explicit image paths. If omitted, directory + pattern are used."},
        "directory": {"type": "string", "description": "Folder to scan when paths is omitted. Default: current directory."},
        "pattern": {"type": "string", "description": "Glob under directory, e.g. '*.png' or '**/*'. Default: **/*"},
        "max_files": {"type": "integer", "description": "Maximum images to OCR. Default 80, max 200."},
        "max_workers": {"type": "integer", "description": "Concurrent OCR workers. Default up to 20, max HARNESS_MAX_PARALLEL_TOOLS."},
        "max_chars_per_image": {"type": "integer", "description": "Text preview cap per image. Default 800."},
        "include_empty": {"type": "boolean", "description": "Return empty/no-text results too. Default false."},
        "keywords": {"type": "array", "items": {"type": "string"},
                     "description": "Optional terms to prioritize in the output, e.g. required resume skills or ID document words."}}},
    },
]

INTERNET_TOOLS = [
    {"name":"web_search","description":"Search the web using DuckDuckGo (no browser opened, no API key needed). Returns titles, URLs, and snippets for the top results. Use this to look up current information, news, docs, prices, weather, etc.",
     "input_schema":{"type":"object","properties":{
        "query":{"type":"string","description":"Search query string"},
        "max_results":{"type":"integer","description":"Max number of results to return (default 8)"}},"required":["query"]}},
    {"name":"fetch_url","description":"Fetch a URL and return its content as plain text (HTML is stripped). Use this to read web pages, docs, JSON APIs, etc. without opening any browser.",
     "input_schema":{"type":"object","properties":{
        "url":{"type":"string","description":"Full URL to fetch (http/https)"},
        "raw":{"type":"boolean","description":"If true, return raw response body (HTML/JSON) instead of stripped text"}},"required":["url"]}},
    {"name":"verified_search","description":(
        "Multi-source VERIFIED web search. Searches 5-10 independent websites, "
        "fetches their content in parallel, scores each by domain credibility (1-10), "
        "extracts key claims, cross-checks every claim across ALL sources, and returns "
        "a structured report with: ✅ verified facts (≥50% source agreement), "
        "⚠️ contested points, 📚 source list with trust scores, and an overall confidence "
        "level. Use this instead of web_search whenever accuracy matters — news, health, "
        "science, facts, prices, current events. Never trust a single source."
    ),
     "input_schema":{"type":"object","properties":{
        "query":{"type":"string","description":"What to research and verify"},
        "min_sources":{"type":"integer","description":"Minimum sources to fetch (default 5)"},
        "max_sources":{"type":"integer","description":"Maximum sources to fetch (default 10)"}},"required":["query"]}},
]
