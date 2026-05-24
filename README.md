# Harness — Jarvis Terminal Agent (Windows)

**AI coding agent for your terminal on Windows.**  
Chat, run tools, edit files, execute shell commands, and control Windows apps — all from one TUI.

> **This is the `windows` branch.** macOS users: use the `main` branch instead.

---

## Quick Start (Windows)

Open **PowerShell** and run:

```powershell
irm https://raw.githubusercontent.com/PrajsRamteke/harness-agent/windows/scripts/install.ps1 | iex
```

Then open a **new terminal**, go to any project folder, and run:

```powershell
jarvis
```

You'll be prompted to pick an auth method on first run.

### macOS install (unchanged — `main` branch)

```bash
curl -fsSL https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install | bash
source ~/.zshrc
jarvis
```

---

## Overview

Harness is a **terminal-native AI agent**. It reads/writes files, runs shell commands, searches code, uses git, controls Windows apps via UI Automation, OCRs images, browses the web — from one TUI.

> **No web UI, no daemon.** Just `jarvis` in your project folder.

---

## Features

| | |
| --- | --- |
| Interactive TUI with markdown, syntax highlighting, streaming | Dual auth: API key or OAuth PKCE |
| File ops: read, write, edit, glob, rank, ripgrep search | Shell: cmd/PowerShell via `run_bash` |
| Git status, diff, log inline | **Windows control**: launch/focus apps, UI tree, click, type, PowerShell, clipboard, toast notifications |
| Web search + verified multi-source lookup | Persistent memory, skills, MCP |
| Windows OCR (built-in Media OCR + optional Tesseract) | Themes, agents, cost tracking |

---

## Requirements

- **Windows 10/11**
- **Python 3.10+** — [python.org/downloads](https://www.python.org/downloads/) (enable **Add python.exe to PATH**)
- **Git for Windows** — [git-scm.com/download/win](https://git-scm.com/download/win)
- **API key** (sk-ant-…) or Anthropic Pro/Max (OAuth)

### Optional (recommended)

| Tool | Purpose |
| --- | --- |
| [Everything](https://www.voidtools.com/) (`es.exe` on PATH) | Instant `fast_find` across the PC |
| [ripgrep](https://github.com/BurntSushi/ripgrep/releases) (`rg`) | Fast `search_code` |
| [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) | OCR fallback if WinRT OCR fails |

---

## Installation

### One-command install (recommended)

```powershell
irm https://raw.githubusercontent.com/PrajsRamteke/harness-agent/windows/scripts/install.ps1 | iex
```

Install location: `%USERPROFILE%\.local\share\harness-agent`  
Command shim: `%USERPROFILE%\.local\bin\jarvis.cmd`

**If `jarvis` is not found**, add to PATH manually:

```powershell
[Environment]::SetEnvironmentVariable("Path", "$env:USERPROFILE\.local\bin;" + [Environment]::GetEnvironmentVariable("Path","User"), "User")
```

### Development setup

```powershell
git clone -b windows https://github.com/PrajsRamteke/harness-agent.git
cd harness-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
jarvis
```

---

## Windows desktop tools

| Tool | Description |
| --- | --- |
| `launch_app` / `focus_app` / `quit_app` | App lifecycle |
| `read_ui` / `click_element` | UI Automation tree + click by text |
| `type_text` / `key_press` | Keyboard simulation |
| `run_powershell` | Arbitrary PowerShell automation |
| `clipboard_get` / `clipboard_set` | Clipboard |
| `notify` | Toast notification |
| `speck` | Text-to-speech (SAPI) |
| `win_control` | Lock, sleep, battery, theme toggle, etc. |

Run `check_permissions` if UI tools fail — often caused by mixing elevated and non-elevated terminals/apps.

---

## Usage

```powershell
jarvis
```

Run from the folder you want to work in. File ops and shell commands are scoped to that directory.

### Slash commands

| Command | Action |
| --- | --- |
| `/help` | List commands |
| `/model <name>` | Switch model |
| `/agent` | Agent picker |
| `/skill` | Skill browser |
| `/cost` | Token usage |
| `/clear` | Reset conversation |

See full docs in `CLAUDE.md` / `JARVIS.md`.

---

## Environment variables

| Variable | Default |
| --- | --- |
| `ANTHROPIC_API_KEY` | — |
| `CLAUDE_MODEL` | `sonnet-4-6` |
| `HARNESS_MAX_PARALLEL_TOOLS` | `64` |
| `JARVIS_BRANCH` | `windows` (installer only) |

---

## Branch strategy

| Branch | Platform | Install |
| --- | --- | --- |
| `main` | macOS | `curl …/main/scripts/install \| bash` |
| `windows` | Windows only | `irm …/windows/scripts/install.ps1 \| iex` |

No runtime OS detection on this branch — it is built and tested for Windows only.

---

## Notes

- Config and sessions: `%USERPROFILE%\.config\harness-agent\`
- UI tools use **pywinauto** (UI Automation). Install is automatic via `pip install -e .`
- Tool selection is dynamic — core file/code tools always available; Windows desktop, web, OCR loaded on demand

---

Built by [Prajwal Ramteke](https://github.com/PrajsRamteke)
