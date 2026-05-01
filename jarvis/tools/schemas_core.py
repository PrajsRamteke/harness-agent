"""Tool JSON schemas for file/shell/git/internet tools."""

CORE_TOOLS = [
    {"name":"read_file","description":(
        "Read a text file. Refuses node_modules/.venv/build/dist/caches, binary "
        "files (images, archives, compiled), files > 2MB, and outside-project "
        "paths. Use offset/limit for line ranges and avoid rereading files already "
        "present in context. Only pass force=true if the user explicitly asked to "
        "read that specific outside/blocked file."),
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string"},
        "offset":{"type":"integer","description":"0-indexed starting line"},
        "limit":{"type":"integer","description":"number of lines; 0 = all"},
        "force":{"type":"boolean","description":"bypass binary/skip-dir guards; use sparingly"}},
        "required":["path"]}},
    {"name":"read_document","description":(
        "Read PDF, images (OCR), CSV/TSV, JSON, HTML, XLSX, XML, YAML, Markdown, or code/text "
        "in one call—picks the right parser by file type. Use this instead of `read_file` for "
        "PDFs, spreadsheets, data files, images with text, etc. Single file: pass `path`. "
        "Several files: pass `paths`. Many files under a folder: pass `directory` and optional "
        "`pattern` (glob, default **/*). Project-scoped by default; use force only for "
        "explicit outside-project requests. Caps: `max_files`, `max_chars_per_file`, `csv_max_rows`."
    ),
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string","description":"One file to read (omit if using paths or directory)."},
        "paths":{"type":"array","items":{"type":"string"},"description":"Multiple explicit file paths."},
        "directory":{"type":"string","description":"Folder to scan when path/paths omitted."},
        "pattern":{"type":"string","description":"Glob under directory. Default **/*."},
        "max_files":{"type":"integer","description":"Bulk cap. Default 32, max 80."},
        "max_chars_per_file":{"type":"integer","description":"Per-file text cap. Default 48000."},
        "csv_max_rows":{"type":"integer","description":"Max data rows for CSV/Excel preview. Default 200."},
        "force":{"type":"boolean","description":"Bypass skip-dir and large-file guard when user asked explicitly."}}}},
    {"name":"write_file","description":"Create or overwrite a file",
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string"},"content":{"type":"string"},
        "allow_outside_project":{"type":"boolean","description":"Default false. Only true if the user explicitly asked to write outside the current project."}},
        "required":["path","content"]}},
    {"name":"edit_file","description":"Replace old_str with new_str. old_str must be unique unless replace_all=true.",
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string"},"old_str":{"type":"string"},
        "new_str":{"type":"string"},"replace_all":{"type":"boolean"},
        "allow_outside_project":{"type":"boolean","description":"Default false. Only true if the user explicitly asked to edit outside the current project."}},
        "required":["path","old_str","new_str"]}},
    {"name":"list_dir","description":(
        "List project directory entries. Refuses outside-project paths unless "
        "allow_outside_project=true. By default hides node_modules/.venv/build/"
        "dist/caches — pass show_all=true to include them."),
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string"},
        "show_all":{"type":"boolean"},
        "allow_outside_project":{"type":"boolean","description":"Default false. Only true if the user explicitly asked to inspect outside the current project."}}}},
    {"name":"run_bash","description":"Execute a shell command in the working directory",
     "input_schema":{"type":"object","properties":{
        "cmd":{"type":"string"},"timeout":{"type":"integer"}},"required":["cmd"]}},
    {"name":"search_code","description":"Regex search with ripgrep (or grep fallback). Project-scoped by default.",
     "input_schema":{"type":"object","properties":{
        "pattern":{"type":"string"},"path":{"type":"string"},
        "allow_outside_project":{"type":"boolean","description":"Default false. Only true if the user explicitly asked to search outside the current project."}},"required":["pattern"]}},
    {"name":"glob_files","description":(
        "Find files by glob pattern under a specific project directory. Pass `path` "
        "when the pattern belongs under a known subfolder. Project-scoped by default "
        "and skips dependency/cache dirs."
    ),
     "input_schema":{"type":"object","properties":{
        "pattern":{"type":"string","description":"Relative glob under `path`, e.g. '**/*.py' or '**/analytics/constants*'."},
        "path":{"type":"string","description":"Directory to search under. Default current project root."},
        "max_results":{"type":"integer","description":"Maximum file paths to return. Default 200, max 1000."},
        "allow_outside_project":{"type":"boolean","description":"Default false. Only true if the user explicitly asked to glob outside the current project."}},
        "required":["pattern"]}},
    {"name":"rank_files","description":(
        "Cheaply rank likely relevant files before reading many files. Use this first "
        "for broad tasks like finding code, resumes, IDs, screenshots, docs, configs, "
        "or unknown files in a folder. Returns compact paths/scores and optional snippets."
    ),
     "input_schema":{"type":"object","properties":{
        "query":{"type":"string","description":"What you are trying to find or solve."},
        "path":{"type":"string","description":"Folder or file to scan. Default current directory."},
        "pattern":{"type":"string","description":"Glob under path. Default **/*."},
        "max_files":{"type":"integer","description":"Maximum ranked results. Default 30, max 100."},
        "scan_limit":{"type":"integer","description":"Maximum files to inspect cheaply. Default 700, max 3000."},
        "include_snippets":{"type":"boolean","description":"Read small text previews to score content matches. Default false."},
        "max_snippet_chars":{"type":"integer","description":"Snippet chars per matched text file. Default 240."},
        "allow_outside_project":{"type":"boolean","description":"Default false. Only true if the user explicitly asked to rank files outside the current project."}},
        "required":["query"]}},
    {"name":"fast_find","description":(
        "Fast file/folder search by name across the Mac using Spotlight (mdfind) — "
        "near-instant (milliseconds), indexed. Falls back to 'fd' if installed. "
        "ALWAYS prefer this over `run_bash` with 'find ~', 'find /', or recursive "
        "globbing — those scan the disk and take 30s+. Use fast_find for any query "
        "of the form 'where is X', 'find my Y', 'locate Z', 'find all PNGs named qr', "
        "'search for resume.pdf', etc. Supports an ext filter so you can combine name "
        "+ extension in one call (e.g. query='qr', ext='png')."
    ),
     "input_schema":{"type":"object","properties":{
        "query":{"type":"string","description":"Name or substring to search for, e.g. 'resume', 'harness', 'qr'."},
        "path":{"type":"string","description":"Optional folder to scope the search, e.g. '~/Desktop'. Empty = whole Mac."},
        "kind":{"type":"string","enum":["any","file","folder"],"description":"Filter results. Default 'any'."},
        "max_results":{"type":"integer","description":"Max results. Default 50, max 500."},
        "ext":{"type":"string","description":"Extension filter, e.g. '.png' or 'png,jpg'. Optional."}},
        "required":["query"]}},
    {"name":"git_status","description":"git status","input_schema":{"type":"object","properties":{}}},
    {"name":"git_diff","description":"git diff","input_schema":{"type":"object","properties":{"path":{"type":"string"}}}},
    {"name":"git_log","description":"git log","input_schema":{"type":"object","properties":{"n":{"type":"integer"}}}},
    {"name":"read_project_graph","description":(
        "Read (or build) the project structure graph — a compact map of all source "
        "files, their exports, imports, directory tree, framework, and dependencies. "
        "Use this at the START of any coding task to navigate the project without "
        "expensive search_code/glob_files discovery calls. The graph persists on "
        "disk across sessions so you never rediscover structure. Pass rebuild=true "
        "if the project changed significantly since last graph build."
    ),
     "input_schema":{"type":"object","properties":{
        "rebuild":{"type":"boolean","description":"Force full rescan. Default false — reads existing graph if available."}},
        "required":[]}},
    {"name":"update_project_graph","description":(
        "Update the project graph after making file changes (edit_file, write_file, "
        "rename, delete). Fast incremental update: only rescans the files you list. "
        "Call this after modifying files so the graph stays current. Pass empty or "
        "omit files to do a full rebuild."
    ),
     "input_schema":{"type":"object","properties":{
        "files":{"type":"array","items":{"type":"string"},
                  "description":"List of changed file paths (relative to project root). If omitted, rescans everything."}},
        "required":[]}},
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
    {"name":"web_search","description":"Search the web using DuckDuckGo (no browser opened, no API key needed). Returns titles, URLs, and snippets for the top results. Use this to look up current information, news, docs, prices, weather, etc. IMPORTANT: do NOT hardcode years like '2024' or '2025' in your query — rely on the CURRENT DATE & TIME injected in the system prompt. Use recency words ('latest', 'current', 'today') and the tool will auto-append the actual current year; otherwise omit year entirely.",
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
        "science, facts, prices, current events. Never trust a single source. "
        "IMPORTANT: do NOT hardcode years (e.g. '2024', '2025') in the query — "
        "use the CURRENT DATE & TIME from the system prompt. Recency words "
        "('latest', 'current', 'today') auto-inject the real current year."
    ),
     "input_schema":{"type":"object","properties":{
        "query":{"type":"string","description":"What to research and verify"},
        "min_sources":{"type":"integer","description":"Minimum sources to fetch (default 5)"},
        "max_sources":{"type":"integer","description":"Maximum sources to fetch (default 10)"}},"required":["query"]}},
]
