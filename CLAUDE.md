# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Set up development environment (run from repo root, one step at a time)
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .             # installs deps + registers `jarvis`
jarvis --help                # verify CLI

# Run the TUI (default)
jarvis
# or:
python agent.py

# Run legacy Rich REPL
python agent.py --legacy

# Run unit tests
pip install pytest
python -m pytest tests/ -q
```

`pip install -r requirements.txt` installs libraries only (no `jarvis` entry point). Prefer `pip install -e .` for local development. See `requirements.txt` header for the full verified sequence.

## Environment Variables

- `ANTHROPIC_API_KEY` — bypass auth prompt (pins provider to Anthropic)
- `OPENROUTER_API_KEY` — OpenRouter key (pins provider to OpenRouter if no Anthropic state exists)
- `OPENCODE_API_KEY` — OpenCode Go key
- `OPENCODE_ZEN_API_KEY` — OpenCode Zen key
- `HARNESS_PROVIDER` — pin provider explicitly: `anthropic`, `openrouter`, `opencode`, or `opencode_zen`
- `CLAUDE_MODEL` — override default model (default: `sonnet-4-6`)
- `HARNESS_MODEL_CATALOG_TTL` — seconds a fetched free-model catalog stays fresh (default: 21600 / 6h)
- `HARNESS_OPENROUTER_HIDE_NO_TOOLS` — set to `1` to drop free OpenRouter models that can't call tools (listed by default, labelled `no tool use`)
- `HARNESS_MAX_PARALLEL_TOOLS` — max concurrent tool workers (default/cap: 64)
- `HARNESS_BUNDLE_MAX_CHARS` — max chars in resolve_context/read_bundle output (default: 120000)
- `HARNESS_BUNDLE_PER_FILE_MAX` — per-file cap inside a bundle (default: 20000)
- `HARNESS_BUNDLE_MODE` — default for resolve_context: `full` | `skeleton` | `manifest` (default: skeleton)
- `HARNESS_BUNDLE_MODE_READ` — default for read_bundle (default: full)
- `HARNESS_HTTP_READ_TIMEOUT` — streaming response timeout in seconds (default: 240 OpenRouter, 600 direct)
- `HARNESS_HTTP_CONNECT_TIMEOUT` — connection timeout (default: 30)
- `HARNESS_STREAM_REPLY` — set to `0` to disable live streaming of assistant text
- `HARNESS_MOUSE` — set to `0` to run the TUI without mouse capture (native terminal selection)
- `HARNESS_CHROME` — Chrome/Chromium binary for `screenshot(url=…)` (auto-detected otherwise)

## Architecture

`agent.py` is a thin entrypoint that routes to either `jarvis/tui/app.py` (Textual TUI, default) or `jarvis/main.py` (Rich REPL, `--legacy`).

### Package layout (`jarvis/`)

| Subpackage | Role |
|---|---|
| `auth/` | Auth orchestration: API key (`api_key.py`), OAuth PKCE (`oauth_flow.py`, `pkce.py`), OpenRouter (`openrouter.py`), OpenCode (`opencode.py`), unified client factory (`client.py`) |
| `tools/` | All tool implementations + schema routing |
| `tools/router.py` | **Dynamic tool selection** — regex-scans recent messages to include only likely-needed tool groups; core always included, specialized groups (web, mac, ocr, memory, skills, mcp) conditionally added |
| `tools/schemas_core.py` / `schemas_mac.py` | JSON schema definitions for tool groups |
| `tools/mac/` | macOS control: app launch/focus/quit, AppleScript, JXA scripts, UI reading, clicks, keystrokes, clipboard |
| `tools/web/` | Web fetch + DuckDuckGo search with verified-source claim checking (`_claims.py`) |
| `repl/` | Stream handling (`stream.py`), response rendering (`render.py`), hallucination guard (`hallucination.py`), context trimming (`trim.py`) |
| `tui/` | Textual app (`app.py`), widget transcript (`transcript.py`), console shim (`console_shim.py`), markdown renderer (`md_render.py`), tool rows/icons (`tool_format.py`), clickable footer (`footer.py`), sticky prompt (`sticky_prompt.py`), extra key sequences (`terminal_keys.py`: ESC+CR → `alt+enter`, i.e. Shift+Enter from VS Code/Cursor keybindings → newline), sidebar, prompt history + paste chips, pickers/modals (shared chrome in `modal_chrome.py`) |
| `commands/` | Slash command handlers dispatched from `dispatch.py` (`agent.py` activates agents, `skill.py` lists/loads skills) |
| `storage/` | SQLite sessions (`sessions.py`), user memory (`memory.py`), **agents (`agents.py`)**, **skills (`skills.py`)**, **custom commands (`commands.py`)**, unified settings (`settings.py`), prefs (`prefs.py`) |
| `mcp/` | MCP server management: config (`config.py`), registry (`registry.py`), manager (`manager.py`) |
| `utils/` | Shared helpers: `io.py` (secure file writes), `http.py`, `html_clean.py`, `serialize.py`, `time_fmt.py` |
| `pet/` | The TUI pets (UI-free): `model.py` (`Pet` stats/XP/levels/badges/streak/accessories, `Roster` of up to 6 pets, `pet.json`), `sprites.py` (species pixel art, palettes, accessories, growth, props, `pen_stage` scene, `sky`, text pets), `events.py` (tests/git moments from `run_bash` output), `session.py` (`/new` recap) |
| `state.py` | **Module-level mutable globals** shared across the package (client, messages, model, flags, theme, **active_agent**) — mutate via `jarvis.state.<name> = ...` |
| `constants/` | Paths (`~/.config/harness-agent/`, `~/.harness/`), model names, OAuth endpoints, system prompt, provider identifiers, **`default_agents/*.md`** (bundled coding/reverse_eng/setup) |

### Live model discovery

Free model line-ups change constantly, so free tiers are **discovered at
runtime**, not hard-coded:

- `auth/zen_catalog.py` — OpenCode Zen free tier (Harness Agent source).
- `auth/openrouter_catalog.py` — every $0 model in `openrouter.ai/api/v1/models`.
  **Nothing is hidden**: a row missing from `/model` is more confusing than a
  caveated one, so unusable models are labelled and sorted last instead.
  Two flags do that — `no tool use` (the harness always sends tools, so those
  models fail every turn) and `restricted` (answered `403 permission_denied`;
  OpenRouter gates a few free models to its allowlisted apps, and nothing in
  the catalog marks them, so the only way to know is to be refused once —
  entries age out after a week). `FreeModel.usable` is the combined check, and
  only usable models are ever auto-selected as a fallback.
- `auth/catalog_cache.py` — stale-while-revalidate disk cache under
  `~/.config/harness-agent/model_catalog/`.

**The picker never fetches on the UI thread.** `ModelPickerScreen.on_mount`
builds rows from the cache (sub-millisecond) and runs `refresh_model_catalogs()`
in a `@work(thread=True)` worker that swaps the rows in when it lands; the TUI
also warms the cache at startup (`_warm_model_catalogs_background`). Anything
calling `model_picker_rows(live=True)` **must** be on a worker thread.
`/model refresh` forces a live refresh and retries previously-refused models.

The `ModelSpec` entries in `constants/providers.py` for OpenRouter are only an
offline seed list — don't curate free models there by hand. Models discovered
at runtime register their pricing and vision support via
`register_dynamic_model()`, and `openrouter_default_model()` resolves the
default from the live catalog so a retired id never becomes a dead fallback.

### Key data flows

- **Tool routing**: Each API call goes through `tools/router.py:select_tools()`, which regex-scans the last 4 messages and keeps any tool groups already active in the tool-call loop.
- **Conversation state**: All messages live in `state.messages` (plain dicts). The tool-call loop in `main.py:_send_and_loop()` / `tui/app.py` continues until `stop_reason == "end_turn"`.
- **Tool execution**: Tools in `repl/render.py` run concurrently via `ThreadPoolExecutor` except for tools in `_SERIAL_TOOLS` (shell, file edits, macOS UI control, MCP tools) which run single-threaded.
- **TUI rendering**: every `console.*` call lands in `tui/console_shim.py:TUIConsole`, which mounts one widget per entry in the bottom-anchored `Transcript` (`tui/transcript.py`): `UserBlock`, `AssistantBlock` (streaming markdown), `ThinkingBlock`, `ToolBlock` (one row per tool call from `emit_tool_event` — `repl/tool_events.py` passes `input`/`output`), `DiffBlock` (from `file_diff`, placed under its edit row), `NoticeBlock` (plain prints, coalesced), `TurnFooter`. Blocks render themselves from their own data at paint time as pre-wrapped `Text` (cached per width/theme), so theme switches, ⌃T trace toggles and resizes restyle in place — **never replay history to restyle**. Markdown goes through Rich (`md_render.py`), not Textual's `Markdown` widget (too many widgets per message). Streaming: worker threads only append deltas to a locked buffer; the app's ~24fps pump (`mixins/activity.py:_tick_activity` → `TUIConsole.pump`) drains them, freezing text before the last safe paragraph break and splitting long replies into continuation blocks. Shared code checks `getattr(console, "renders_tool_rows", False)` to skip legacy REPL panels, and optional hooks (`file_diff`, `show_thinking`, `show_reply`, `show_plan`, `show_welcome`) to render natively; `WebMuxConsole` mirrors those hooks to web clients. Cancellation: Esc finalizes the UI immediately and marks the worker thread cancelled (`state.cancel_thread` / `state.turn_cancelled()`); stream deltas are owned by the thread that started the stream, so a stale worker can't touch the next turn.
- **Sticky prompt**: once the user box of the turn you're reading scrolls out of view, `tui/sticky_prompt.py:StickyPrompt` pins a copy at the top of the transcript — an overlay docked in `#body` (`layers: base overlay`), so nothing shifts. It follows the turn owning the top of the viewport (`Transcript.sticky_prompt()`, from the cached arrangement via `prompt_spans()` — never the compositor's full map). Click = jump back, `↑ ↓` / `alt+↑ alt+↓` = previous/next prompt (`Transcript.step_prompt`), `⎘` copies, hover expands, spinner while that turn runs. Synced from `_refresh_activity_widgets` (scroll + activity tick) and `Transcript.watch_virtual_size`; wired in `tui/mixins/prompt_nav.py`. Setting `ui.sticky_prompt` (default on).
- **Tool images (`screenshot`)**: `tools/screenshot.py` captures the screen / an app window (`screencapture -l`, window ids via JXA) / a region / a web page (headless Chrome) / an image file, scales to ≤1568 px, and returns text plus a `[[jarvis:image <path>]]` marker line. `render.py` turns markers into native `image` blocks inside the tool_result (`utils/tool_images.py:tool_result_content`); OpenAI-style providers (`opencode_client`) and Codex (`codex_client`) move those images into a user message right after the tool messages. `repl/trim.py:prune_tool_images` keeps only the newest 3 in each request (none for non-vision models). Models without vision get OCR text instead. Missing Screen Recording permission surfaces as a clear error.
- **Background jobs (`run_bg` / `bg_output` / `bg_kill`)**: `tools/background.py` — same danger check + approval as `run_bash` (`shell.ask_approval`), detached process group, output to `$TMPDIR/jarvis-bg/<pid>/job-N.log`, a watcher thread records the exit. Running / finished-but-unread jobs are listed in the system prompt (`repl/system.py:_background_jobs_block`); the TUI prints a notice on finish (`app._on_bg_job_finished`) and lists jobs in the sidebar. All jobs are killed at exit. Routed via the `vision` / `background` tool groups in `tools/router.py`.
- **Persistence**: Sessions stored in SQLite at `~/.config/harness-agent/sessions.db`. Pinned context from `~/.config/harness-agent/pinned.txt`. Aliases from `~/.config/harness-agent/aliases.json`. Unified preferences in `~/.config/harness-agent/settings.json` (global) merged with `<cwd>/.harness/settings.json` (per-project override).
- **Auth**: `auth/client.py:make_client()` checks for `ANTHROPIC_API_KEY`, then stored key/OAuth tokens, then prompts interactively. Sets `state.provider` and `state.auth_mode`.
- **Project context**: On startup, detects `AGENTS.md`, `AGENT.md`, `CLAUDE.md`, or `JARVIS.md` in CWD and stores only the path in `state.project_context_*`; file content is loaded on demand via `read_file()`.
- **Agents**: Markdown files with YAML frontmatter (`storage/agents.py`). Project sources scanned always: `.harness/agents/`, `.claude/agents/`, `.opencode/agents/`, `.agents/`, `.cursor/agents/`. Global (opt-in via `agent.global`): `~/.harness/agents/`, `~/.claude/agents/`, `~/.config/opencode/agents/`. At most one active agent at a time; its body is appended to the system prompt by `repl/system.py:_agent_addon_block()`. Bundled defaults (coding/reverse_eng/setup) seeded into `~/.harness/agents/` on first run.
- **Skills**: SKILL.md packs auto-invoked by the LLM based on `description:` frontmatter. Project sources: `.harness/skills/`, `.skills/`, `.opencode/skills/`, `.claude/skills/`, `.agents/skills/`. Global (opt-in via `skills.global`): `~/.harness/skills/`, `~/.config/harness-agent/skills/`, `~/.claude/skills/`, `~/.config/opencode/skills/`. The picker modal (`tui/skill_modal.py`) is read-only browsing — no sticky selection.
- **Custom commands**: User-defined prompt templates (`storage/commands.py`) triggered directly as `/<name> [args]` — dispatch falls through to them after all built-ins (`commands/dispatch.py:try_custom_command`). Project sources (recursive): `.harness/commands/`, `.claude/commands/`, `.opencode/command(s)/`, `.agents/commands/`. Global (opt-in via `commands.global`, default **on**): `~/.harness/commands/`, `~/.config/harness-agent/commands/`, `~/.claude/commands/`, `~/.config/opencode/command(s)/`. Body placeholders `$ARGUMENTS` and `$1`…`$9` are filled from the typed args (`expand_template`); args with no placeholder are appended. Managed via `/command` (`commands/command.py`): new/edit/show/delete/refresh/run, `global on|off`, `scope`, `export`/`import`. In the TUI, bare `/command` opens the manager modal (`tui/command_modal.py`): Enter inserts `/<name> ` into the prompt box, `t` inserts the full template for editing, `n`/`e` open an in-app editor sub-modal (name + description inputs + template TextArea; `^s` saves via `storage/commands.py:write_command`, which edits project AND global files in place and renames on name change), `d` delete, `i`/`x` import/export, `g`/`s` scope toggles. Custom commands also appear in the palette via `commands_catalog.filter_commands`; built-ins always shadow same-named custom commands.
- **Pets**: a roster of pets (cat · dog · bunny · dragon, which starts as an egg and hatches after 10 turns) lives in a pen docked at the bottom of the sidebar (`tui/pet_pen.py:PetPen`, one active pet). It wanders, naps when idle, paces while a turn runs, plays with toys that appear by themselves (box / butterfly / cup on a shelf), chases a laser dot on hover, runs to clicks; click it to pat. Buttons: `pat · feed · play · nap · trick` and `fish` (20 s catch-the-fish game) · `focus` (25 min pomodoro, pet naps on a keyboard) · `pets` (switch / adopt via `pet_modal.PetAdoptScreen`) · `card` (`pet_modal.PetCardScreen`: portrait, stats, badges, wardrobe, roster). Scenery follows the clock (`sprites.sky`: clouds/moon+stars, December snow, October pumpkin). With the sidebar hidden the pet moves into the composer (`tui/pet_widget.py:PetBuddy` + `PetBubble` overlay in the always-blank row above the composer). `tui/mixins/pet.py:PetMixin` wires it up: turn start/finish (`_begin_turn`/`_turn_done`), tool results with input/output (`console_shim._tool_done` → `pet/events.classify`: tests pass/fail/fixed, commit, push, merge conflict), diffs (`console_shim.file_diff` → `_pet_diff`: lines shipped today, "whoa!" at ≥100 lines), typing, and the 2 s `_slow_refresh` tick (focus timer, rate-limited nudges). Level unlocks accessories (glasses Lv3 while working, party hat 5, scarf 7, crown 10) and growth stages (Lv5, Lv10); badges come from counters (`model.BADGES`). Desktop notifications (`utils/notify.py`) when a long turn ends while unfocused and when focus ends. `/pet …` (`commands/pet.py`) runs on the worker thread and reaches the UI through `pet.set_reaction_hook` / `pet.set_action_hook`; `/new` prints the session recap (`commands/history.py`). State: `~/.config/harness-agent/pet.json` (roster; old single-pet files still load); settings `pet.enabled`, `pet.nudges`, `pet.notify`. **Tests** must not touch the real `pet.json` — `tests/conftest.py` redirects `PET_FILE` and resets hooks/session for every test.

### Adding a new tool

1. Implement the handler function in `jarvis/tools/` (or a subdirectory).
2. Add its JSON schema to `schemas_core.py` (always available) or a new group dict.
3. Register the group in `jarvis/tools/__init__.py` (`TOOL_GROUPS`, `TOOL_NAME_TO_GROUP`, `FUNC`).
4. If specialized, add a regex trigger in `tools/router.py:select_tools()`.
5. Wire the tool name → handler by adding it to the `FUNC` dict in `tools/__init__.py` — this is what `repl/render.py` uses to dispatch `tool_use` blocks.
6. Optional: give it a readable transcript row in `tui/tool_format.py` (`_TITLES`, `tool_args`, `tool_summary`).

**`ask_user_question`**: Structured multiple-choice prompts for the LLM. TUI shows options in `#askbar` above the composer (↑/↓ + Enter or 1–9; space toggles when `allow_multiple`). Blocks in `_SERIAL_TOOLS`; uses `TUIConsole.prompt_ask_user_question` from worker threads.

### Themes and agents

- **Themes**: built-in palettes in `jarvis/tui/theme.py:PALETTES` (opencode — the default —, claude, tokyonight, catppuccin, gruvbox, nord, kimchi, red, blue, purple, green, orange, yellow, rose, slate, ocean, cyberpunk, monochrome, forest, dracula, sunset, dark). Each is also registered as a Textual theme (`textual_theme()`), exposing `$jv-*` CSS variables used by transcript widgets; `state.THEMES` derives entries for every palette. Persisted under `theme` in settings.json; `/theme` previews live.
- **Dialogs**: every modal subclasses `tui/modal_chrome.py:TuiModalScreen` (shared frame, `esc` hint on the title, slide-in, compact inputs). Build list rows with `picker_row()` (name · dim detail · right-aligned meta, `●` for current, query matches accented), group them with `section_header()` (disabled rows — the cursor skips them), and write hints with `hint_line()`; see `model_modal.py` / `session_modal.py` for the reference layout.
- **Mouse**: on by default (wheel scroll, select-to-copy). `HARNESS_MOUSE=0` or settings `ui.mouse: false` restores native terminal selection; `tui/mouse_toggle.py` toggles are no-ops while the app owns the mouse.
- **Agents** (replaced the legacy mode system): User-creatable markdown files. The active agent's body is appended to the system prompt as an addon. Status bar shows the active agent (icon + name + scope hint). Tab key cycles through discovered agents. `/agent` opens the picker; `/agent init` scaffolds `.harness/`. Active agent persisted by name as `agent.active`.

### Adding a new agent

1. Drop a markdown file in `.harness/agents/<name>.md` (project) or `~/.harness/agents/<name>.md` (global) — or run `/agent new <name>`.
2. Required frontmatter: `name` (lowercase-kebab, must match filename), `description`. Optional: `icon` (single emoji), `color` (hex/name), `model` (pin a model).
3. Body markdown below the second `---` is appended to the system prompt when active.
4. `/agent refresh` to pick up new files. `/agent <name>` to activate.

### Adding a new skill

1. Create `.harness/skills/<name>/SKILL.md` (project) or `~/.harness/skills/<name>/SKILL.md` (global).
2. Required frontmatter: `name`, `description`. The directory name MUST equal `name`.
3. Body markdown is the skill content. The LLM auto-invokes via `/skill load <name>` when the description matches the task.

### Adding a custom command

1. Run `/command new <name> [description]` — or drop a markdown file at `.harness/commands/<name>.md` (project) / `~/.harness/commands/<name>.md` (global).
2. Frontmatter is optional (`name`, `description`, `argument-hint`); without it the whole file is the template and the first line becomes the description. If `name` is given it must match the filename stem (lowercase-kebab).
3. The body is the prompt template; `$ARGUMENTS` and `$1`…`$9` are substituted from whatever the user types after `/<name>`.
4. Trigger with `/<name> [args]` (or `/command run <name> [args]`). `/command refresh` re-scans; `/command export|import <name>` moves between scopes.
