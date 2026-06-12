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
- `HARNESS_MAX_PARALLEL_TOOLS` — max concurrent tool workers (default/cap: 64)
- `HARNESS_BUNDLE_MAX_CHARS` — max chars in resolve_context/read_bundle output (default: 120000)
- `HARNESS_BUNDLE_PER_FILE_MAX` — per-file cap inside a bundle (default: 20000)
- `HARNESS_BUNDLE_MODE` — default for resolve_context: `full` | `skeleton` | `manifest` (default: skeleton)
- `HARNESS_BUNDLE_MODE_READ` — default for read_bundle (default: full)
- `HARNESS_HTTP_READ_TIMEOUT` — streaming response timeout in seconds (default: 240 OpenRouter, 600 direct)
- `HARNESS_HTTP_CONNECT_TIMEOUT` — connection timeout (default: 30)
- `HARNESS_STREAM_REPLY` — set to `0` to disable live streaming of assistant text

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
| `tui/` | Textual app (`app.py`), command palette, session/model/agent/skill pickers, MCP modal |
| `commands/` | Slash command handlers dispatched from `dispatch.py` (`agent.py` activates agents, `skill.py` lists/loads skills) |
| `storage/` | SQLite sessions (`sessions.py`), user memory (`memory.py`), **agents (`agents.py`)**, **skills (`skills.py`)**, **custom commands (`commands.py`)**, unified settings (`settings.py`), prefs (`prefs.py`) |
| `mcp/` | MCP server management: config (`config.py`), registry (`registry.py`), manager (`manager.py`) |
| `utils/` | Shared helpers: `io.py` (secure file writes), `http.py`, `html_clean.py`, `serialize.py`, `time_fmt.py` |
| `state.py` | **Module-level mutable globals** shared across the package (client, messages, model, flags, theme, **active_agent**) — mutate via `jarvis.state.<name> = ...` |
| `constants/` | Paths (`~/.config/harness-agent/`, `~/.harness/`), model names, OAuth endpoints, system prompt, provider identifiers, **`default_agents/*.md`** (bundled coding/reverse_eng/setup) |

### Key data flows

- **Tool routing**: Each API call goes through `tools/router.py:select_tools()`, which regex-scans the last 4 messages and keeps any tool groups already active in the tool-call loop.
- **Conversation state**: All messages live in `state.messages` (plain dicts). The tool-call loop in `main.py:_send_and_loop()` / `tui/app.py` continues until `stop_reason == "end_turn"`.
- **Tool execution**: Tools in `repl/render.py` run concurrently via `ThreadPoolExecutor` except for tools in `_SERIAL_TOOLS` (shell, file edits, macOS UI control, MCP tools) which run single-threaded.
- **Persistence**: Sessions stored in SQLite at `~/.config/harness-agent/sessions.db`. Pinned context from `~/.config/harness-agent/pinned.txt`. Aliases from `~/.config/harness-agent/aliases.json`. Unified preferences in `~/.config/harness-agent/settings.json` (global) merged with `<cwd>/.harness/settings.json` (per-project override).
- **Auth**: `auth/client.py:make_client()` checks for `ANTHROPIC_API_KEY`, then stored key/OAuth tokens, then prompts interactively. Sets `state.provider` and `state.auth_mode`.
- **Project context**: On startup, detects `AGENTS.md`, `AGENT.md`, `CLAUDE.md`, or `JARVIS.md` in CWD and stores only the path in `state.project_context_*`; file content is loaded on demand via `read_file()`.
- **Agents**: Markdown files with YAML frontmatter (`storage/agents.py`). Project sources scanned always: `.harness/agents/`, `.claude/agents/`, `.opencode/agents/`, `.agents/`, `.cursor/agents/`. Global (opt-in via `agent.global`): `~/.harness/agents/`, `~/.claude/agents/`, `~/.config/opencode/agents/`. At most one active agent at a time; its body is appended to the system prompt by `repl/system.py:_agent_addon_block()`. Bundled defaults (coding/reverse_eng/setup) seeded into `~/.harness/agents/` on first run.
- **Skills**: SKILL.md packs auto-invoked by the LLM based on `description:` frontmatter. Project sources: `.harness/skills/`, `.skills/`, `.opencode/skills/`, `.claude/skills/`, `.agents/skills/`. Global (opt-in via `skills.global`): `~/.harness/skills/`, `~/.config/harness-agent/skills/`, `~/.claude/skills/`, `~/.config/opencode/skills/`. The picker modal (`tui/skill_modal.py`) is read-only browsing — no sticky selection.
- **Custom commands**: User-defined prompt templates (`storage/commands.py`) triggered directly as `/<name> [args]` — dispatch falls through to them after all built-ins (`commands/dispatch.py:try_custom_command`). Project sources (recursive): `.harness/commands/`, `.claude/commands/`, `.opencode/command(s)/`, `.agents/commands/`. Global (opt-in via `commands.global`, default **on**): `~/.harness/commands/`, `~/.config/harness-agent/commands/`, `~/.claude/commands/`, `~/.config/opencode/command(s)/`. Body placeholders `$ARGUMENTS` and `$1`…`$9` are filled from the typed args (`expand_template`); args with no placeholder are appended. Managed via `/command` (`commands/command.py`): new/edit/show/delete/refresh/run, `global on|off`, `scope`, `export`/`import`. In the TUI, bare `/command` opens the manager modal (`tui/command_modal.py`): Enter inserts `/<name> ` into the prompt box, `t` inserts the full template for editing, `n`/`e` open an in-app editor sub-modal (name + description inputs + template TextArea; `^s` saves via `storage/commands.py:write_command`, which edits project AND global files in place and renames on name change), `d` delete, `i`/`x` import/export, `g`/`s` scope toggles. Custom commands also appear in the palette via `commands_catalog.filter_commands`; built-ins always shadow same-named custom commands.

### Adding a new tool

1. Implement the handler function in `jarvis/tools/` (or a subdirectory).
2. Add its JSON schema to `schemas_core.py` (always available) or a new group dict.
3. Register the group in `jarvis/tools/__init__.py` (`TOOL_GROUPS`, `TOOL_NAME_TO_GROUP`, `FUNC`).
4. If specialized, add a regex trigger in `tools/router.py:select_tools()`.
5. Wire the tool name → handler by adding it to the `FUNC` dict in `tools/__init__.py` — this is what `repl/render.py` uses to dispatch `tool_use` blocks.

**`ask_user_question`**: Structured multiple-choice prompts for the LLM. TUI shows options in `#askbar` above the status strip (↑/↓, Enter; space toggles when `allow_multiple`). Blocks in `_SERIAL_TOOLS`; uses `TUIConsole.prompt_ask_user_question` from worker threads.

### Themes and agents

- **Themes**: 15 built-in palettes defined in `jarvis/tui/theme.py:PALETTES` (red, blue, purple, green, orange, yellow, rose, slate, ocean, cyberpunk, monochrome, forest, dracula, sunset, dark); persisted to `~/.config/harness-agent/settings.json` under `theme`. `/theme` switches live at runtime.
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
