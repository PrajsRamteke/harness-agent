# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Agent

```bash
# Activate venv first
source .venv/bin/activate

# Default: Textual TUI mode
python agent.py

# Legacy Rich REPL mode
python agent.py --legacy
```

Dependencies: `pip install -r requirements.txt` (anthropic, rich, textual — Python 3.10+)

## Environment Variables

- `ANTHROPIC_API_KEY` — skip auth prompt and use this key directly
- `CLAUDE_MODEL` — override default model (default: `claude-sonnet-4-6`)
- `HARNESS_MAX_PARALLEL_TOOLS` — max concurrent tool workers (default/cap: 64)

## Architecture

`agent.py` is a thin entrypoint that routes to either `jarvis/tui/app.py` (Textual TUI, default) or `jarvis/main.py` (Rich REPL, `--legacy`).

### Package layout (`jarvis/`)

| Subpackage | Role |
|---|---|
| `auth/` | Auth orchestration: API key (`api_key.py`), OAuth PKCE (`oauth_flow.py`, `pkce.py`), OpenRouter (`openrouter.py`), unified client factory (`client.py`) |
| `tools/` | Tool implementations + schema routing |
| `tools/router.py` | **Dynamic tool selection** — sends only the schemas likely needed each turn (core always included; web/mac/ocr/memory/skills groups added by regex matching recent messages) |
| `tools/schemas_core.py` / `schemas_mac.py` | JSON schema definitions for tool groups |
| `tools/mac/` | macOS control: app launch/focus/quit, AppleScript, JXA scripts, UI reading, clicks, keystrokes, clipboard |
| `tools/web/` | Web fetch + DuckDuckGo search with verified-source claim checking |
| `repl/` | Stream handling (`stream.py`), response rendering (`render.py`), hallucination guard (`hallucination.py`), context trimming (`trim.py`) |
| `tui/` | Textual app (`app.py`), command palette modal, session picker modal |
| `commands/` | Slash command handlers dispatched from `dispatch.py` |
| `storage/` | SQLite session history (`sessions.py`), user memory (`memory.py`), skills (`skills.py`), prefs (`prefs.py`) |
| `state.py` | **Module-level mutable globals** shared across the package (client, messages, model, flags) — mutate via `jarvis.state.<name> = ...` |
| `constants/` | Paths (`~/.config/claude-agent/`), model names, OAuth endpoints, system prompt, provider identifiers |

### Key data flows

- **Tool routing**: Each API call goes through `tools/router.py:select_tools()`, which regex-scans the last 4 messages and keeps any tool groups already active in the tool-call loop.
- **Conversation state**: All messages live in `state.messages` (plain dicts). The tool-call loop in `main.py:_send_and_loop()` / `tui/app.py` continues until `stop_reason == "end_turn"`.
- **Persistence**: Sessions stored in SQLite at `~/.config/claude-agent/sessions.db`. Pinned context from `~/.config/claude-agent/pin`. Aliases from `~/.config/claude-agent/aliases.json`.
- **Auth**: `auth/client.py:make_client()` checks for `ANTHROPIC_API_KEY`, then stored key/OAuth tokens, then prompts interactively. Sets `state.provider` and `state.auth_mode`.

### Adding a new tool

1. Implement in `jarvis/tools/` (or a subdirectory).
2. Add its JSON schema to `schemas_core.py` (always available) or a new group dict.
3. Register the group in `jarvis/tools/__init__.py` (`TOOL_GROUPS`, `TOOL_NAME_TO_GROUP`).
4. If it's a specialized group, add a regex trigger in `tools/router.py:select_tools()`.
5. Wire the tool name → handler function in `repl/render.py` (where `tool_use` blocks are dispatched).
