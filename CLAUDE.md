# Project Context

This file provides guidance to AI coding assistants when working with code in this repository.

## Commands

```bash
# Set up development environment
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Run the TUI (default)
python agent.py
# or after install:
jarvis

# Run legacy Rich REPL
python agent.py --legacy

# Install dependencies only
pip install -r requirements.txt
```

There is no automated test suite — `tests/` is empty. Manual testing is done by running `jarvis` directly.

## Environment Variables

- `ANTHROPIC_API_KEY` — bypass auth prompt
- `CLAUDE_MODEL` — override default model (default: `sonnet-4-6`)
- `HARNESS_MAX_PARALLEL_TOOLS` — max concurrent tool workers (default/cap: 64)
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
| `tui/` | Textual app (`app.py`), command palette modal, session picker modal, model picker modal |
| `commands/` | Slash command handlers dispatched from `dispatch.py` |
| `storage/` | SQLite session history (`sessions.py`), user memory (`memory.py`), skills (`skills.py`), prefs (`prefs.py`) |
| `mcp/` | MCP server management: config (`config.py`), registry (`registry.py`), manager (`manager.py`) |
| `state.py` | **Module-level mutable globals** shared across the package (client, messages, model, flags, theme, mode) — mutate via `jarvis.state.<name> = ...` |
| `constants/` | Paths (`~/.config/claude-agent/`), model names, OAuth endpoints, system prompt, provider identifiers |

### Key data flows

- **Tool routing**: Each API call goes through `tools/router.py:select_tools()`, which regex-scans the last 4 messages and keeps any tool groups already active in the tool-call loop.
- **Conversation state**: All messages live in `state.messages` (plain dicts). The tool-call loop in `main.py:_send_and_loop()` / `tui/app.py` continues until `stop_reason == "end_turn"`.
- **Tool execution**: Tools in `repl/render.py` run concurrently via `ThreadPoolExecutor` except for tools in `_SERIAL_TOOLS` (shell, file edits, macOS UI control, MCP tools) which run single-threaded.
- **Persistence**: Sessions stored in SQLite at `~/.config/claude-agent/sessions.db`. Pinned context from `~/.config/claude-agent/pin`. Aliases from `~/.config/claude-agent/aliases.json`.
- **Auth**: `auth/client.py:make_client()` checks for `ANTHROPIC_API_KEY`, then stored key/OAuth tokens, then prompts interactively. Sets `state.provider` and `state.auth_mode`.
- **Project context**: On startup, detects `JARVIS.md`, `CLAUDE.md`, or `AGENT.md` in CWD and loads it into `state.project_context_*` globals, injected into the system prompt each turn.

### Adding a new tool

1. Implement the handler function in `jarvis/tools/` (or a subdirectory).
2. Add its JSON schema to `schemas_core.py` (always available) or a new group dict.
3. Register the group in `jarvis/tools/__init__.py` (`TOOL_GROUPS`, `TOOL_NAME_TO_GROUP`, `FUNC`).
4. If specialized, add a regex trigger in `tools/router.py:select_tools()`.
5. Wire the tool name → handler in `repl/render.py` (dispatches `tool_use` blocks) — actually `FUNC` dict in `tools/__init__.py` handles this automatically.

### Theme and mode system

- Two built-in themes (`"red"`, `"purple"`) stored in `state.THEMES`; persisted to `~/.config/claude-agent/last_theme.json`.
- Two modes (`"default"`, `"coding"`) control which system prompt addons are active; defined in `constants/` and applied in `repl/system.py`.
