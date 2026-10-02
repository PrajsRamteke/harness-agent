<div align="center">

<img src="assets/jarvis-logo.png" alt="Jarvis" width="132" />

# Jarvis

**The open-source AI agent that works where your code lives.**

Jarvis reads your project, runs commands, edits files and checks its own work, right in your terminal.
It remembers how you like things done, learns from every task, and runs on free models the moment you install it.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-1f2a4d)](#installation)
[![Version](https://img.shields.io/badge/version-0.3.0-6CB6FF)](https://github.com/PrajsRamteke/harness-agent/commits/main)
[![Free models](https://img.shields.io/badge/free%20models-built%20in-FFB648)](#models-and-providers)
[![GitHub stars](https://img.shields.io/github/stars/PrajsRamteke/harness-agent?style=flat&color=6CB6FF)](https://github.com/PrajsRamteke/harness-agent/stargazers)

[Install](#installation) · [Features](#features) · [Usage](#usage) · [Commands](#slash-commands) · [Configuration](#configuration) · [Development](#development)

</div>

---

## Why Jarvis

| | |
| --- | --- |
| **Free from the first run** | Jarvis starts on Harness Agent, a free model provider. No API key, no account, no credit card, no ads. |
| **Remembers you** | Facts about you and your preferences are saved and loaded into every new session. |
| **Learns from its own work** | After solving a tricky task, Jarvis writes a short lesson and recalls it the next time a similar task appears. |
| **Checks the web, then checks again** | Verified search reads several independent sources and reports how well they agree. |
| **Follows you to your phone** | `/web` opens the live session in any browser via a QR code. `/web anywhere` works off your network too. |
| **Any model you like** | Claude and ChatGPT subscriptions, your own API keys, and around 200 providers from [models.dev](https://models.dev). |
| **Brings your existing setup** | Reads agents, skills, MCP servers and commands from Claude Code, Cursor, Codex, Gemini, OpenCode and more. |
| **Uses your Mac, too** | Opens apps, clicks, types, runs AppleScript and Shortcuts, takes screenshots and reads text from images. |

---

## Installation

You need **Python 3.10 or newer**. The installer puts Jarvis in its own virtual environment and adds a single `jarvis` command.

### macOS and Linux

```bash
curl -fsSL https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install | bash
```

Then open a **new terminal** in any project folder and run:

```bash
jarvis
```

<details>
<summary>No Python 3.10+ on macOS yet?</summary>

```bash
brew install python@3.11
curl -fsSL https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install | bash
source ~/.zshrc        # or ~/.zprofile, so `jarvis` is on your PATH
jarvis
```

The installer also looks in `/opt/homebrew/bin/python3.*` if Homebrew isn't on your `PATH` yet.

</details>

### Windows

In **PowerShell** or **Command Prompt**:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install.ps1 | iex"
```

Open a **new terminal** in your project folder and run `jarvis`. No admin rights are needed: Jarvis is installed to `%LOCALAPPDATA%\harness-agent` and the command to `%USERPROFILE%\.local\bin`.

<details>
<summary>No Python or Git yet?</summary>

```powershell
winget install -e --id Python.Python.3.12
winget install -e --id Git.Git
```

Open a new terminal, then run the installer above. [Windows Terminal](https://aka.ms/terminal) is recommended for the best TUI experience.

</details>

### First run

There is nothing to set up. Jarvis starts on a free Harness Agent model straight away. When you want something else:

- `/model` to switch models (free models are listed first)
- `/provider` to sign in with **Claude Pro/Max** or **ChatGPT**, or to add an API key

### Updating

Jarvis checks for updates in the background each time it starts. To update by hand:

```bash
jarvis update            # pull the latest version and reinstall
jarvis update --check    # only show what's available
```

Running the installer again also updates. Set `HARNESS_SKIP_UPDATE=1` to turn off automatic updates.

### Uninstalling

```bash
# macOS / Linux
rm -f "$(command -v jarvis)"            # the jarvis command
rm -rf ~/.local/share/harness-agent     # the app
rm -rf ~/.config/harness-agent          # optional: settings, sessions and keys
```

```powershell
# Windows
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\harness-agent", "$HOME\.local\bin\jarvis.cmd"
```

---

## Features

### A coding agent in your terminal

- **Files and code**: read, write and edit files (including multi-file edits), list folders, glob, rank files by relevance and search with ripgrep.
- **Shell and git**: run commands with an approval step for risky ones, and check status, diffs and history without leaving the chat.
- **Parallel tools**: reads and searches run concurrently, up to 64 at a time.
- **Lean context**: each turn only sends the tool definitions it is likely to need, so tokens go further.
- **Every change tracked**: a live ledger of changed files with diffs, edit history and an applicable patch.
- **Background jobs**: long builds run in the background, and Jarvis picks the work back up when they finish.
- **Loops**: `/loop 5m <task>` repeats a task on a timer, or `/loop <task>` lets Jarvis pace itself.
- **Plan mode**: read-only research until you approve a plan (`/plan` or `⇧⇥`).
- **Headless runs**: `jarvis -p "<task>"` runs a single task without the TUI and exits.

### Models and providers

| Provider | How to connect | Cost |
| --- | --- | --- |
| **Harness Agent** | Built in, the default on first run | Free |
| **OpenRouter free tier** | `/provider` with a free OpenRouter key | Free models found live and tagged |
| **Claude Pro / Max** | `/provider` → sign in with Claude | Your subscription |
| **ChatGPT (Codex)** | `/provider` → sign in with ChatGPT | Your subscription |
| **Anthropic API** | `/provider` or `ANTHROPIC_API_KEY` | Pay as you go |
| **OpenRouter** | `/provider` or `OPENROUTER_API_KEY` | Pay as you go |
| **OpenCode Go / Zen** | `/provider` or `OPENCODE_API_KEY` / `OPENCODE_ZEN_API_KEY` | Pay as you go |
| **models.dev catalog** | `/provider`, or the provider's own variable (`DEEPSEEK_API_KEY`, `GROQ_API_KEY`, …) | Pay as you go |

Free model line-ups change often, so Jarvis discovers them at runtime instead of hard-coding a list. The model picker shows **Free**, **vision** and **In use** tags, and you can type `free` or `vision` to filter.

### Memory and lessons

- **Memory** keeps short facts about you (name, stack, commit style, preferences) and loads them into every session. Review or edit them with `/memory`.
- **Lessons** are written by Jarvis after it solves something: the kind of task and what actually worked. Relevant lessons are recalled for similar tasks, and the ones that keep helping are kept. Browse them with `/lesson`.
- `/scan` can build your memory from your documents and projects in one go.

Both are plain JSON files on your machine (see [Files and locations](#files-and-locations)).

### Web research

- `web_search` and `fetch_url` for searching and reading pages and docs.
- `verified_search` collects candidates from several domains, reads them in parallel, compares their claims and reports the level of agreement.
- A hallucination guard removes sentences that cite sources Jarvis never opened.

### Web remote: your session in a browser or on your phone

```bash
jarvis --web             # start with the web remote (default port 8765)
jarvis --web --tunnel    # also open a public HTTPS link
```

Or from inside a session: `/web` shows a QR code and link, `/web open` opens it in this computer's browser, and `/web anywhere` opens a tunnel (Cloudflare quick tunnel or ngrok) so it works from any network. The browser and terminal stay in sync. You can approve commands, review file changes, attach photos and documents, switch models and manage providers from your phone.

### macOS control

Launch, focus and quit apps, read the UI, click elements and menus, type and press keys, run AppleScript and Shortcuts, use the clipboard, send notifications, read text aloud, take screenshots of the screen, a window or a web page, and run OCR on images.

macOS needs **Accessibility**, **Automation** and **Screen Recording** permissions for these. Grant them in *System Settings → Privacy & Security*.

### Agents, skills, MCP servers and commands

Jarvis uses plain files for extensions, and it also reads the ones you made for other AI tools:

| Extension | What it does | Where Jarvis looks |
| --- | --- | --- |
| **Agents** | Personas appended to the system prompt. One is active at a time, and `Tab` cycles them. | `.harness/agents/`, `.claude/agents/`, `.cursor/agents/`, `.gemini/agents/`, `.opencode/agents/`, `.agents/` |
| **Skills** | `SKILL.md` packs that Jarvis loads on its own when a task matches their description. | `.harness/skills/`, `.claude/skills/`, `.opencode/skills/`, `.agents/skills/`, `.codex/`, `.cursor/`, `.gemini/`, `.kiro/` |
| **MCP servers** | External tools over the Model Context Protocol (stdio, streamable HTTP and SSE, with browser sign-in). | `.mcp.json`, plus Claude Code, Cursor, Codex, Gemini, OpenCode and other configs |
| **Commands** | Your prompt templates, run as `/<name> [args]` with `$ARGUMENTS` and `$1`…`$9`. | `.harness/commands/`, `.claude/commands/`, `.opencode/commands/`, `.agents/commands/` |

Each type has a **project** scope (the folder you're in) and a **global** scope (`~/.harness/…` plus other tools' home folders).

**Adding a skill or MCP server** takes one step. Any of these work:

- Ask Jarvis: *"add this skill: https://github.com/anthropics/skills/tree/main/skills/pdf"* or *"add the Linear MCP server"*. It asks for approval, installs it and connects it.
- `/skill add <link>` or `/mcp add <link>`, with `--project` or `--global`.
- The `/skill` and `/mcp` dialogs. Popular servers (Linear, Notion, Sentry, GitHub, …) take one click.

API keys for MCP servers go to `~/.config/harness-agent/mcp_secrets.json` (mode 600), never into `.mcp.json`. Hosted servers that need a login show an **Authenticate** button. Tools appear as soon as you finish signing in, without a restart.

### A terminal UI built for long sessions

- Streaming Markdown with syntax highlighting, inline diffs and collapsible thinking.
- A **sticky prompt** keeps your message pinned while you read a long reply.
- **Prompt polish** (`⌃G`) fixes spelling and grammar before you send, and pressing it again undoes the change.
- `@` attaches files, `!cmd` runs a shell command directly, and you can drag and drop images and documents.
- **22 themes** with live preview (`/theme`), including Tokyo Night, Catppuccin, Nord, Dracula and Gruvbox.
- **Pets**: adopt a cat, dog, bunny or dragon that levels up as you ship. It includes a focus timer and badges (`/pet`).
- Token, context and cost readout in the footer, and a session sidebar (`⌃B`).

---

## Usage

Run Jarvis from the folder you want it to work in. All file operations, searches and shell commands are scoped to that folder.

```bash
jarvis                         # start the TUI
jarvis "explain this repo"     # start the TUI and send a first prompt
jarvis -p "run the tests and fix failures"   # headless: one task, then exit
jarvis --web [PORT]            # start with the web remote
jarvis --web --tunnel          # web remote with a public HTTPS link
jarvis --legacy                # the older Rich REPL
jarvis update [--check]        # update Jarvis
```

**Project context**: Jarvis detects an `AGENTS.md`, `CLAUDE.md` or `JARVIS.md` in the project root and reads it when it needs to.

### Slash commands

Type `/` to see every command with fuzzy search, or press `⌃P` for the command palette.

| Area | Commands |
| --- | --- |
| **Conversation** | `/new` fresh chat · `/retry` resend last message · `/session` resume or delete sessions · `/export` save as Markdown · `/copy` copy last reply · `/clear` · `/exit` |
| **Models** | `/model` pick a model · `/provider` sign in, add keys, switch provider · `/think` reasoning effort · `/stats` and `/cost` usage |
| **Memory and context** | `/memory` · `/lesson` · `/pin` pinned context · `/unpin` · `/scan` build memory from your files |
| **Extensions** | `/agent` · `/agent init` · `/skill` · `/skill add <link>` · `/mcp` · `/mcp add <link>` · `/command` |
| **Modes** | `/plan` plan mode · `/auto` auto-approve shell · `/loop` repeat a task · `/verbose` show thinking and tool traces |
| **Web remote** | `/web` QR and link · `/web open` · `/web anywhere` public link · `/web stop` |
| **Interface** | `/theme` · `/sidebar` · `/pet` · `/settings` · `/local` quick local commands · `/paste` clipboard text or image |
| **Maintenance** | `/upgrade` · `/version` · `/help` |

### Keyboard shortcuts

| Keys | Action |
| --- | --- |
| `↵` / `⇧↵` | Send (queued while Jarvis is busy) / new line |
| `Esc` | Interrupt the running turn, or close a dialog |
| `⇥` / `⇧⇥` | Cycle agents / toggle plan mode |
| `⌃P` | Command palette |
| `⌃G` | Polish the prompt (press again to undo) |
| `⌃T` or `F2` | Show thinking and tool previews |
| `⌃F` or `F3` | Tools inspector (full file reads and command output) |
| `⌃B` | Toggle the session sidebar |
| `⌥↑` / `⌥↓` | Jump to the previous or next prompt |
| `⌃Y` | Copy the last reply |
| `?` or `F1` | All shortcuts |

---

## Configuration

### Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Use this Anthropic key (pins the provider to Anthropic) | — |
| `OPENROUTER_API_KEY` | OpenRouter key | — |
| `OPENCODE_API_KEY` / `OPENCODE_ZEN_API_KEY` | OpenCode Go / Zen keys | — |
| `<PROVIDER>_API_KEY` | Connects that models.dev provider, e.g. `DEEPSEEK_API_KEY`, `GROQ_API_KEY` | — |
| `HARNESS_PROVIDER` | Pin a provider: `anthropic`, `openrouter`, `opencode`, `opencode_zen`, or `md:<id>` | auto |
| `CLAUDE_MODEL` | Model to start with | free Harness Agent model |
| `HARNESS_MODELS_DEV` | Set to `0` to turn off the models.dev catalog | `1` |
| `HARNESS_MAX_PARALLEL_TOOLS` | Maximum concurrent tool workers | `64` |
| `HARNESS_HTTP_READ_TIMEOUT` | Seconds between bytes on a streaming response | `240` OpenRouter, `600` direct |
| `HARNESS_HTTP_CONNECT_TIMEOUT` | Connection timeout in seconds | `30` |
| `HARNESS_STREAM_REPLY` | Set to `0` to turn off live streaming | `1` |
| `HARNESS_MOUSE` | Set to `0` for native terminal text selection | `1` |
| `HARNESS_WEB_PORT` | Web remote port | `8765` |
| `HARNESS_WEB_TUNNEL` | Set to `1` to always open a public link with `--web` | `0` |
| `HARNESS_UPLOAD_MAX_MB` | Largest attachment the web remote accepts | `50` |
| `HARNESS_SKIP_UPDATE` | Set to `1` to turn off automatic updates | `0` |
| `HARNESS_CHROME` | Chrome or Chromium binary used for web page screenshots | auto-detected |

### Settings

Preferences live in `~/.config/harness-agent/settings.json`, and a project can override them in `.harness/settings.json`. Use `/settings` to view, edit, reset or reload them from inside Jarvis.

### Files and locations

| Path | Contents |
| --- | --- |
| `~/.config/harness-agent/` | Credentials (mode 600), `settings.json`, `sessions.db`, `memory.json`, `lessons.json`, `pet.json`, `mcp.json`, model catalog cache, web uploads |
| `~/.harness/` | Global agents, skills and commands |
| `<project>/.harness/` | Project agents, skills, commands and `settings.json` (`/agent init` creates it) |
| `<project>/.mcp.json` | Project MCP servers |

Your sessions, memory and lessons are files on your machine. Conversation content is sent only to the model provider you choose.

---

## Development

```bash
git clone https://github.com/PrajsRamteke/harness-agent.git
cd harness-agent
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .                 # installs dependencies and the `jarvis` command
jarvis --help
```

Run the test suite:

```bash
pip install pytest
python -m pytest tests/ -q
```

Tests run against a temporary `HOME`, so they never touch your real `~/.config/harness-agent`.

### Project layout

```
harness/
├── agent.py             Entry point (TUI by default, --legacy for the REPL)
├── pyproject.toml       Package metadata and dependencies
├── jarvis/
│   ├── cli.py           Command-line flags, `jarvis update`
│   ├── main.py          Core send-and-tool loop
│   ├── state.py         Shared session state
│   ├── auth/            API keys, OAuth (Claude, ChatGPT), provider clients, live model catalogs
│   ├── tools/           File, shell, git, search, web, vision, macOS and extension tools + routing
│   ├── repl/            Streaming, tool execution, context trimming, hallucination guard
│   ├── tui/             Textual app: transcript, dialogs, sidebar, pets, themes
│   ├── web/             Web remote: HTTP server, live sync, browser UI
│   ├── mcp/             MCP config, registry, browser sign-in, secrets
│   ├── storage/         Sessions, memory, lessons, agents, skills, commands, settings
│   ├── commands/        Slash command handlers
│   ├── pet/             Pet model, sprites and events
│   ├── constants/       Paths, models, providers, system prompt, default agents
│   └── utils/           Shared helpers
├── scripts/             Installers for macOS/Linux and Windows
├── assets/              Logo and images
└── tests/               pytest suite
```

### Adding a tool

1. Implement the handler in `jarvis/tools/`.
2. Add its JSON schema to `schemas_core.py`, or to a group for specialised tools.
3. Register it in `jarvis/tools/__init__.py` (`TOOL_GROUPS`, `TOOL_NAME_TO_GROUP`, `FUNC`).
4. For a specialised group, add a trigger in `tools/router.py:select_tools()`.
5. Optionally give it a readable transcript row in `tui/tool_format.py`.

`CLAUDE.md` has a detailed architecture guide for contributors and AI assistants.

---

## Troubleshooting

<details>
<summary><b><code>jarvis: command not found</code> (macOS / Linux)</b></summary>

Open a new terminal, or add `~/.local/bin` to your `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Add that line to `~/.zshrc` (or `~/.bashrc`) to make it permanent.

</details>

<details>
<summary><b><code>'jarvis' is not recognized</code> (Windows)</b></summary>

Open a new terminal so it picks up the updated `PATH`. For the current PowerShell window only:

```powershell
$env:Path = "$HOME\.local\bin;$env:Path"
```

</details>

<details>
<summary><b>macOS control or screenshots don't work</b></summary>

Give your terminal app **Accessibility**, **Automation** and **Screen Recording** permissions in *System Settings → Privacy & Security*, then restart the terminal.

</details>

<details>
<summary><b>A free model stopped responding</b></summary>

Free line-ups change often. Run `/model refresh` to fetch the current list, then pick another free model with `/model`.

</details>

---

## Acknowledgements

Provider and model details (context size, prices, tool support) come from [models.dev](https://models.dev), an open database by the OpenCode team (MIT licence, © models.dev).

## Support

- [Report a bug or request a feature](https://github.com/PrajsRamteke/harness-agent/issues)
- [Ask a question or share an idea](https://github.com/PrajsRamteke/harness-agent/discussions)

<div align="center">

[Built by Prajwal Ramteke](https://github.com/PrajsRamteke). If Jarvis helps you, consider giving it a ⭐.

</div>
