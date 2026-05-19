# Harness — Jarvis Terminal Agent

**AI coding agent for your terminal.**  
Chat, run tools, edit files, execute shell commands, and control macOS — all from one TUI.



---

## ✨ Overview

Harness is a **terminal-native AI agent** that lives in your terminal. You talk to it, it uses tools — reads/writes files, runs shell commands, searches code, uses git, controls macOS apps, OCRs images, browses the web — and gets work done right where your code lives.

> **No web UI, no daemon.** Just `jarvis` in your project folder.

---

## 🚀 Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install | bash
source ~/.zshrc   # or ~/.zprofile on macOS
jarvis
```

That's it. You'll be prompted to pick an auth method on first run.

---

## 🖼️ Screenshots


|     |     |
| --- | --- |
|     |     |


---

## 📋 Table of Contents

- [Features](#-features)
- [Requirements](#-requirements)
- [Installation](#-installation)
- [Usage](#-usage)
- [Slash Commands](#-slash-commands)
- [Environment Variables](#-environment-variables)
- [Project Layout](#-project-layout)
- [Notes](#-notes)

---

## 🧰 Features


|                                                                                                                                 |                                                                                                                                            |
| ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| 💬 Interactive TUIRich terminal UI with markdown rendering, syntax-highlighted code, panels, and streaming responses.          | 🔐 Dual AuthUse an **API key** (sk-ant-…) or sign in with **OAuth** via PKCE.                                                             |
| 📁 File OperationsRead, write, edit files. List directories, glob patterns, rank files by relevance, search code with ripgrep. | 🐚 Shell AccessRun any shell command, view output inline — no context switching.                                                          |
| ⎇ Git IntegrationStatus, diff, log — all from the chat. No need to tab out.                                                    | 🖥️ macOS ControlLaunch/focus/quit apps, click UI elements, type text, run AppleScript, use keyboard shortcuts, clipboard, notifications. |
| 🌐 Web AccessSearch the web and fetch URLs. Verified search cross-checks multiple sources for factual answers.                 | 🧠 Persistent MemoryRemembers facts about you across sessions. Stores skills, notes, and aliases under `~/.config/claude-agent/`.         |
| 📊 Cost Tracking`/cost` shows token usage and estimated USD spend per session.                                                 | 🔌 MCP SupportModel Context Protocol — connect external tools and data sources.                                                           |
| 🎨 ThemesBuilt-in **red** and **purple** themes. Easily extensible.                                                            |                                                                                                                                            |


---

## ✅ Requirements

- **Python 3.10+** — If your system Python is older, install a newer one:
  ```bash
  brew install python@3.11
  ```
- **macOS** — required for macOS control features. Core agent works on any platform.
- **API key** (sk-ant-…) or a **Pro/Max subscription**

---

## 📦 Installation

### One-command install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install | bash
```

After install, open a **new terminal**, go to any project, and run:

```bash
jarvis
```

**Troubleshooting: "command not found: jarvis"**

If your shell can't find `jarvis`, add `~/.local/bin` to your PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"
jarvis
```

Add that line to your `~/.zshrc` to make it permanent.



### Development setup

```bash
git clone https://github.com/PrajsRamteke/harness-agent.git
cd harness-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Then run:

```bash
jarvis        # TUI mode (default)
# or
python agent.py --legacy   # Rich REPL mode
```

---

## 🎮 Usage

```bash
jarvis
```

Run it from the **folder you want it to work in**. The status bar shows the current project path — all file operations, code searches, and shell commands are scoped to that directory.

### First run

On first launch, you'll pick how to authenticate:


| Option      | How it works                                                                      |
| ----------- | --------------------------------------------------------------------------------- |
| **API key** | Paste an `sk-ant-…` key. Saved at `~/.config/claude-agent/key` (permissions: 600) |
| **OAuth**   | Opens your browser to sign in with your Pro/Max account via PKCE                  |


### ⌨️ Slash Commands


| Command             | What it does                                                    |
| ------------------- | --------------------------------------------------------------- |
| `/help`             | List all commands                                               |
| `/model <name>`     | Switch models (e.g. `opus-4-7`, `haiku-4-5`)                    |
| `/agent`            | Open the agent picker — choose a project or global agent        |
| `/agent <name>`     | Activate an agent by name (Tab cycles through agents)           |
| `/agent new <name>` | Scaffold a new agent in `.harness/agents/<name>.md`             |
| `/agent init`       | Scaffold a `.harness/` tree in the current project              |
| `/skill`            | Open the skill browser (LLM auto-invokes skills by description) |
| `/verbose` / `F2`   | Toggle internal thinking and tool traces (shown by default)     |
| `/cost`             | Show token usage + estimated USD cost                           |
| `/clear`            | Reset the conversation                                          |
| `/logout`           | Clear saved credentials                                         |
| `/theme`            | Switch themes                                                   |


---

## ⚙️ Environment Variables


| Variable                       | What it does                           | Default                            |
| ------------------------------ | -------------------------------------- | ---------------------------------- |
| `ANTHROPIC_API_KEY`            | Use this key instead of the stored one | —                                  |
| `CLAUDE_MODEL`                 | Override default model                 | `sonnet-4-6`                       |
| `HARNESS_MAX_PARALLEL_TOOLS`   | Max concurrent tool workers            | `64` (capped)                      |
| `HARNESS_HTTP_READ_TIMEOUT`    | Streaming response timeout (s)         | `240` (OpenRouter), `600` (direct) |
| `HARNESS_HTTP_CONNECT_TIMEOUT` | Connection timeout (s)                 | `30`                               |
| `HARNESS_STREAM_REPLY`         | Set to `0` to disable live streaming   | `1`                                |


---

## 🎛️ Agents & Skills

Harness uses two file-based extension points — **agents** (manual select) and
**skills** (LLM auto-invoke) — that aggregate from every AI tool's config
directory (Harness, Claude Code, OpenCode, Cursor, Windsurf, …).

```
project/
├── .harness/
│   ├── agents/                   ← project-local agents
│   │   ├── coding.md             ← user-creatable .md files with YAML frontmatter
│   │   ├── reverse_eng.md
│   │   └── setup.md
│   ├── skills/                   ← project-local skills
│   │   ├── debugging/SKILL.md
│   │   ├── testing/SKILL.md
│   │   └── security/SKILL.md
│   └── settings.json             ← (optional) per-project overrides
│
├── AGENTS.md  /  CLAUDE.md       ← project context (auto-detected)
└── …

~/.harness/                       ← user-global counterpart
├── agents/                       ← bundled coding/reverse_eng/setup seeded on first run
├── skills/
└── settings.json
```

**Agents** — markdown files with frontmatter (`name`, `description`, optional
`icon`/`color`). The active agent's body is appended to the system prompt.
One active at a time, shown in the status bar. `/agent` opens the picker;
`Tab` cycles. Project agents are always available; global ones require
`agent.global = true` in settings (or `/agent global on`).

**Skills** — `SKILL.md` packs with `name` + `description`. The LLM sees all
discovered descriptions and decides when to load a skill itself via
`/skill load <name>`. The `/skill` modal is a read-only browser.

---

## 🗂️ Project Layout

```
harness/
├── agent.py                # Entry point (routes to TUI or REPL)
├── pyproject.toml          # Package config
├── requirements.txt
├── CLAUDE.md               # Context file for AI assistants
├── JARVIS.md
│
├── jarvis/                 # Main package
│   ├── __main__.py         # `python -m jarvis`
│   ├── cli.py              # CLI entry point
│   ├── main.py             # Core send-and-loop logic
│   ├── state.py            # Module-level shared state
│   │
│   ├── auth/               # Authentication
│   │   ├── client.py       # Unified client factory
│   │   ├── api_key.py      # API key handling
│   │   ├── oauth_flow.py   # OAuth PKCE flow
│   │   ├── pkce.py         # PKCE utilities
│   │   ├── openrouter.py   # OpenRouter support
│   │   └── opencode.py     # OpenCode adapter
│   │
│   ├── tools/              # Tool implementations
│   │   ├── router.py       # Dynamic tool selection
│   │   ├── schemas_core.py # Core tool schemas
│   │   ├── schemas_mac.py  # macOS tool schemas
│   │   ├── mac/            # macOS control
│   │   └── web/            # Web fetch & search
│   │
│   ├── repl/               # Response handling
│   │   ├── stream.py       # Stream processing
│   │   ├── render.py       # Tool execution + rendering
│   │   ├── hallucination.py
│   │   └── trim.py         # Context trimming
│   │
│   ├── tui/                # Textual TUI
│   │   ├── app.py          # Terminal UI app
│   │   ├── agent_modal.py  # Agent picker
│   │   └── skill_modal.py  # Skill browser (read-only)
│   │
│   ├── commands/           # Slash commands
│   │   ├── dispatch.py
│   │   ├── agent.py        # /agent — pick / new / init / refresh
│   │   └── skill.py        # /skill — list / load / refresh
│   │
│   ├── storage/            # Persistence
│   │   ├── sessions.py     # SQLite session history
│   │   ├── memory.py       # User memory
│   │   ├── agents.py       # Agent discovery & loading
│   │   ├── skills.py       # Skill discovery & loading
│   │   ├── settings.py     # Unified settings.json (global + project merge)
│   │   └── prefs.py        # Legacy preferences
│   │
│   ├── mcp/                # MCP server management
│   │   ├── config.py
│   │   ├── registry.py
│   │   └── manager.py
│   │
│   ├── constants/          # Paths, models, prompts
│   └── utils/
│
├── scripts/                # Install scripts
├── assets/                 # Screenshots
└── tests/                  # (empty — manual testing)
```

---

## 📝 Notes

- **macOS permissions** — UI control tools need **Accessibility** and **Automation** permissions. Enable them in: System Settings → Privacy & Security → Accessibility / Automation.
- **Credentials** — All config, keys, and history live under `~/.config/claude-agent/`.
- **Tool selection is dynamic** — Harness only sends the schemas for tools it thinks you'll need, keeping context lean. Core file/code tools are always included; macOS, web, OCR tools are loaded on demand.
- **Project context** — Drop a `JARVIS.md` (or `CLAUDE.md`) in your project root, and the agent reads it automatically for project-specific instructions.

---

Built with ❤️ by [Prajwal Ramteke](https://github.com/PrajsRamteke)

[GitHub](https://github.com/PrajsRamteke/harness-agent) · [Issues](https://github.com/PrajsRamteke/harness-agent/issues) · [Discussions](https://github.com/PrajsRamteke/harness-agent/discussions)