"""Tool JSON schemas for file/shell/git/internet tools."""

# Optional on codebase tools; only meaningful when the workspace links other projects.
_PROJ = {"type": "string", "description": "Linked project id, 'a,b', or '*' for all linked. Default: this project."}
_PATH = {"type": "string", "description": "Project-relative path (`id:path` for a linked project)."}
_OUTSIDE = {"type": "boolean", "description": "Only when the user explicitly asked for a path outside the project."}
_PROJ1 = {"type": "string", "description": "Linked project id to run in. Default: this project."}

CONTEXT_TOOLS = [
    {"name": "resolve_context", "description": (
        "Codebase context in ONE call: describe the task and get the related files together "
        "(target + imports + importers + tests + types + configs) instead of 5-20 separate "
        "reads and searches. Modes: skeleton (default) = full target files + symbol/import "
        "summaries of related ones; full = every body (capped); manifest = file list only, "
        "then read_bundle the 3–8 paths that matter. Packs from earlier turns shrink to "
        "their file list later — re-read with read_bundle when needed."
    ),
     "input_schema": {"type": "object", "properties": {
        "task": {"type": "string", "description": (
            "The coding task, specific: 'Fix login bug in JWT token refresh'."
        )},
        "mode": {"type": "string", "enum": ["full", "skeleton", "manifest"],
                 "description": "Bundle density. Default skeleton."},
        "max_chars": {"type": "integer",
                      "description": "Output cap (default ~80K chars). Rarely needed."},
        "project": _PROJ,
     }, "required": ["task"]}},
    {"name": "read_bundle", "description": (
        "Read 2–20 known paths in one call (budget split across files, parallel I/O). "
        "Use it instead of many read_file calls."
    ),
     "input_schema": {"type": "object", "properties": {
        "paths": {"type": "array", "items": {"type": "string"},
                  "description": "Project-relative paths (`id:path` for a linked project). Max 20."},
        "mode": {"type": "string", "enum": ["full", "skeleton", "manifest"],
                 "description": "Default full."},
        "max_chars": {"type": "integer",
                      "description": "Output cap (default ~80K chars). Rarely needed."},
     }, "required": ["paths"]}},
]

# NOTE: CONTEXT_TOOLS (resolve_context/read_bundle) are intentionally NOT part
# of CORE_TOOLS. Core is always exposed to the model; the context-pack tools are
# coding-only and gated by router.select_tools() to the active coding agent so
# the model can't reach for whole-codebase bundling on non-coding turns.
CORE_TOOLS = [
    {"name": "ask_user_question", "description": (
        "Ask the user multiple-choice questions when their answer changes the plan "
        "(scope, approach, preference, ambiguity) — don't guess. Returns the selected "
        "option ids and labels as JSON."
    ),
     "input_schema": {"type": "object", "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "prompt": {"type": "string", "description": "The question."},
                    "header": {"type": "string", "description": "Short label (e.g. 'Auth approach')."},
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["id", "label"],
                        },
                    },
                    "allow_multiple": {
                        "type": "boolean",
                        "description": "Let the user pick several.",
                    },
                },
                "required": ["id", "prompt", "options"],
            },
            "minItems": 1,
        },
     }, "required": ["questions"]}},
    {"name":"read_file","description":(
        "Read one text file, or a line range via offset/limit. Lines come back as "
        "`N<TAB>text` — N is the 1-based line number, not part of the file (never copy it "
        "into old_str/new_str). Up to 2000 lines per call; a closing [showing lines …] note "
        "gives the offset to continue from. Refuses dependency/build dirs, binaries, files "
        "> 2MB and outside-project paths; force=true only when the user asked for that file."),
     "input_schema":{"type":"object","properties":{
        "path":_PATH,
        "offset":{"type":"integer","description":"0-indexed starting line (offset=100 starts at line 101)"},
        "limit":{"type":"integer","description":"number of lines; 0 = to the end"},
        "force":{"type":"boolean"}},
        "required":["path"]}},
    {"name":"read_document","description":(
        "Read PDF, images (OCR), CSV/TSV, XLSX, JSON, HTML, XML, YAML or Markdown — picks "
        "the parser by file type; use it instead of read_file for those. One file: `path`; "
        "several: `paths`; a folder: `directory` (+ glob `pattern`)."
    ),
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string"},
        "paths":{"type":"array","items":{"type":"string"}},
        "directory":{"type":"string"},
        "pattern":{"type":"string","description":"Default **/*."},
        "max_files":{"type":"integer","description":"Default 32, max 80."},
        "max_chars_per_file":{"type":"integer","description":"Default 48000."},
        "csv_max_rows":{"type":"integer","description":"CSV/Excel rows. Default 200."},
        "project":_PROJ,
        "force":{"type":"boolean","description":"Outside-project / skipped files, only when the user asked."}}}},
    {"name":"write_file","description":"Create or overwrite a file",
     "input_schema":{"type":"object","properties":{
        "path":_PATH,"content":{"type":"string"},
        "allow_outside_project":_OUTSIDE},
        "required":["path","content"]}},
    {"name":"edit_file","description":"Replace old_str with new_str in ONE file. old_str must be unique unless replace_all=true. For 2+ edits (same or different files), prefer multi_edit.",
     "input_schema":{"type":"object","properties":{
        "path":_PATH,"old_str":{"type":"string"},
        "new_str":{"type":"string"},"replace_all":{"type":"boolean"},
        "allow_outside_project":_OUTSIDE},
        "required":["path","old_str","new_str"]}},
    {"name":"multi_edit","description":(
        "Several edit_file patches in ONE call (2–30, one or more files), applied in "
        "order. Returns a status per edit."
    ),
     "input_schema":{"type":"object","properties":{
        "edits":{"type":"array","maxItems":30,
                 "items":{"type":"object","properties":{
                    "path":{"type":"string"},
                    "old_str":{"type":"string"},
                    "new_str":{"type":"string"},
                    "replace_all":{"type":"boolean"},
                    "allow_outside_project":{"type":"boolean"}},
                    "required":["path","old_str","new_str"]}},
        "allow_outside_project":_OUTSIDE},
        "required":["edits"]}},
    {"name":"list_dir","description":(
        "List a directory (full paths). Hides node_modules/.venv/build/dist/caches "
        "unless show_all=true."),
     "input_schema":{"type":"object","properties":{
        "path":{"type":"string"},
        "show_all":{"type":"boolean"},
        "allow_outside_project":_OUTSIDE}}},
    {"name":"run_bash","description":(
        "Run a shell command (/bin/sh) in the project root — no `cd` needed. Read-only "
        "commands (ls, cat, head, tail, rg, grep, find, sed -n, wc, git status/log/diff/show) "
        "need no approval and run in parallel. Timeout 120s (max 600); slow builds, test "
        "suites and servers go to run_bg."),
     "input_schema":{"type":"object","properties":{
        "cmd":{"type":"string"},"timeout":{"type":"integer"},"project":_PROJ1},"required":["cmd"]}},
    {"name":"search_code","description":"Regex search with ripgrep (or grep fallback). Project-scoped by default.",
     "input_schema":{"type":"object","properties":{
        "pattern":{"type":"string"},"path":{"type":"string"},"project":_PROJ,
        "allow_outside_project":_OUTSIDE},"required":["pattern"]}},
    {"name":"glob_files","description":(
        "Find files by glob pattern (skips dependency/cache dirs)."
    ),
     "input_schema":{"type":"object","properties":{
        "pattern":{"type":"string","description":"Glob under `path`, e.g. '**/*.py'."},
        "path":{"type":"string","description":"Default: project root."},
        "max_results":{"type":"integer","description":"Default 200, max 1000."},
        "project":_PROJ,
        "allow_outside_project":_OUTSIDE},
        "required":["pattern"]}},
    {"name":"rank_files","description":(
        "Rank the files most likely relevant to a query before reading any — for broad "
        "tasks where you don't know which files matter. Returns paths with scores."
    ),
     "input_schema":{"type":"object","properties":{
        "query":{"type":"string","description":"What you are trying to find or solve."},
        "path":{"type":"string","description":"Default: project root."},
        "pattern":{"type":"string","description":"Default **/*."},
        "max_files":{"type":"integer","description":"Default 30, max 100."},
        "scan_limit":{"type":"integer","description":"Files to inspect. Default 700, max 3000."},
        "include_snippets":{"type":"boolean","description":"Also score file contents. Default false."},
        "max_snippet_chars":{"type":"integer","description":"Default 240."},
        "project":_PROJ,
        "allow_outside_project":_OUTSIDE},
        "required":["query"]}},
    {"name":"fast_find","description":(
        "Find files/folders by name anywhere on the Mac via Spotlight — milliseconds. "
        "Use it for 'where is X' / 'find my Y' instead of `find ~` or `find /` in run_bash "
        "(those scan the disk for 30s+)."
    ),
     "input_schema":{"type":"object","properties":{
        "query":{"type":"string","description":"Name or part of it."},
        "path":{"type":"string","description":"Folder to search in. Empty = whole Mac."},
        "kind":{"type":"string","enum":["any","file","folder"]},
        "max_results":{"type":"integer","description":"Default 50, max 500."},
        "ext":{"type":"string","description":"e.g. 'png' or 'png,jpg'."}},
        "required":["query"]}},
    {"name":"git_status","description":"git status","input_schema":{"type":"object","properties":{"project":_PROJ1}}},
    {"name":"git_diff","description":"git diff — staged + unstaged vs HEAD by default; staged=true / false for only one.","input_schema":{"type":"object","properties":{"path":{"type":"string"},"staged":{"type":"boolean"},"project":_PROJ1}}},
    {"name":"git_log","description":"git log","input_schema":{"type":"object","properties":{"n":{"type":"integer"},"project":_PROJ1}}},
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
    {"name":"web_search","description":"Search the web using DuckDuckGo (no browser opened, no API key needed). Returns titles, URLs, and snippets for the top results. Use this to look up current information, news, docs, prices, weather, etc. IMPORTANT: do NOT hardcode years like '2024' or '2025' in your query — rely on the CURRENT DATE injected in the system prompt. Use recency words ('latest', 'current', 'today') and the tool will auto-append the actual current year; otherwise omit year entirely.",
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
        "a structured report with: ✓ verified facts (≥50% source agreement), "
        "⚠ contested points, ≡ source list with trust scores, and an overall confidence "
        "level. Use this instead of web_search whenever accuracy matters — news, health, "
        "science, facts, prices, current events. Never trust a single source. "
        "IMPORTANT: do NOT hardcode years (e.g. '2024', '2025') in the query — "
        "use the CURRENT DATE from the system prompt. Recency words "
        "('latest', 'current', 'today') auto-inject the real current year."
    ),
     "input_schema":{"type":"object","properties":{
        "query":{"type":"string","description":"What to research and verify"},
        "min_sources":{"type":"integer","description":"Minimum sources to fetch (default 5)"},
        "max_sources":{"type":"integer","description":"Maximum sources to fetch (default 10)"}},"required":["query"]}},
]

VISION_TOOLS = [
    {"name":"screenshot","description":(
        "Take a screenshot and LOOK at it — the image comes back to you, so you can see "
        "UIs, check layout/rendering, verify a visual change, or debug a visual bug. "
        "Targets (first given wins): path = view an existing image file; url = render a "
        "web page in headless Chrome (http://localhost:3000, localhost:5173, or an .html "
        "file) at width×height; app = that app's front window (even if covered); region = "
        "a screen rectangle in points; nothing = the whole main display. Screen/app/region "
        "results say how image pixels map to click_at coordinates. After changing UI code, "
        "take a new screenshot to confirm the result instead of assuming."
    ),
     "input_schema":{"type":"object","properties":{
        "url":{"type":"string","description":"Page to render: http(s) URL, host:port, or local .html path"},
        "app":{"type":"string","description":"App whose front window to capture, e.g. 'Safari', 'Simulator'"},
        "path":{"type":"string","description":"Existing image file to view (png/jpg/gif/webp/heic/tiff)"},
        "region":{"type":"object","description":"Screen rectangle in points","properties":{
            "x":{"type":"integer"},"y":{"type":"integer"},
            "width":{"type":"integer"},"height":{"type":"integer"}}},
        "width":{"type":"integer","description":"url only: viewport width (default 1280)"},
        "height":{"type":"integer","description":"url only: viewport height (default 800)"},
        "wait":{"type":"number","description":"Seconds to let things settle first (url: page load budget, default 3)"}}}},
]

BACKGROUND_TOOLS = [
    {"name":"run_bg","description":(
        "Run a shell command in the BACKGROUND; returns a job id at once. For anything "
        "slow — test suites, builds, installs, dev servers, watchers. Keep working or end "
        "your turn instead of waiting: the result says whether Jarvis wakes you when it "
        "finishes. Same approval as run_bash; jobs survive across turns."
    ),
     "input_schema":{"type":"object","properties":{
        "cmd":{"type":"string"},"project":_PROJ1},"required":["cmd"]}},
    {"name":"bg_output","description":(
        "A background job's status, exit code and latest output; no job_id lists all "
        "jobs. wait=N blocks up to N seconds — only for a job about to finish."
    ),
     "input_schema":{"type":"object","properties":{
        "job_id":{"type":"integer"},
        "wait":{"type":"number","description":"Seconds (default 0)"},
        "tail":{"type":"integer","description":"Lines of output (default 60)"},
        "new_only":{"type":"boolean","description":"Only output since your last check"}}}},
    {"name":"bg_kill","description":"Stop a background job and its child processes.",
     "input_schema":{"type":"object","properties":{
        "job_id":{"type":"integer"}},"required":["job_id"]}},
]

LOOP_TOOLS = [
    {"name":"schedule_wakeup","description":(
        "Pace a /loop the user started: after doing the loop task, set when it runs next "
        "(delay_seconds 60–3600), or end it with stop=true once the job is done. Only works "
        "while a /loop is active — see LOOP MODE in the system prompt. In a self-paced loop, "
        "not calling it ends the loop after this run."
    ),
     "input_schema":{"type":"object","properties":{
        "delay_seconds":{"type":"number","description":"Seconds until the next run (clamped to 60–3600). Match it to how fast the watched thing changes."},
        "prompt":{"type":"string","description":"Task for the next runs; omit to keep the current /loop task"},
        "reason":{"type":"string","description":"One short sentence: what you're waiting for (shown to the user)"},
        "noop":{"type":"boolean","description":"true = nothing changed this run (quiet runs are folded together in the UI)"},
        "stop":{"type":"boolean","description":"true ends the loop (other fields ignored)"}}}},
]
