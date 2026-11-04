<div align="center">

<img src="assets/jarvis-logo.png" alt="Jarvis" width="120" />

# Jarvis

### Your AI helper that reads, writes and fixes code — for free.

Ask in plain words. Jarvis reads your project, makes the changes and checks its own work.<br/>
No account, no API key, no credit card — it works the moment you install it.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Platforms](https://img.shields.io/badge/macOS%20·%20Linux%20·%20Windows-1f2a4d)](#install)
[![Free models](https://img.shields.io/badge/free%20models-built%20in-FFB648)](#first-run)
[![GitHub stars](https://img.shields.io/github/stars/PrajsRamteke/harness-agent?style=flat&color=6CB6FF)](https://github.com/PrajsRamteke/harness-agent/stargazers)

**[Install](#install)** &nbsp;·&nbsp; **[In the terminal](#terminal)** &nbsp;·&nbsp; **[In the browser](#browser)** &nbsp;·&nbsp; **[Run from source](#source)**

</div>

<br/>

## ✨ What it does

| | |
| :-- | :-- |
| 💬 **Just ask** | "Fix the failing test", "explain this project", "add a dark mode". Jarvis does the work. |
| 🆓 **Free from day one** | Starts on a free model. Bring Claude, ChatGPT or your own key later if you want. |
| 🧠 **Remembers you** | Keeps your preferences and learns from every task it solves. |
| 📱 **Follows you anywhere** | Open the same chat in a browser or on your phone. |
| ✅ **Asks before risky steps** | You approve commands and see every file it changes. |

<br/>

<a id="install"></a>

## 📦 Install

You need **Python 3.10 or newer**. Pick the row that matches your computer:

| | Your computer | Install command | Then |
| :-: | :-- | :-- | :-- |
| **1** | 🍎 macOS · 🐧 Linux | [`curl … /scripts/install \| bash`](#1-macos--linux) | Open a new terminal → `jarvis` |
| **2** | 🍎 macOS **without** Python | [`brew install python@3.11` + command 1](#2-macos-without-python) | `source ~/.zshrc` → `jarvis` |
| **3** | 🪟 Windows | [`powershell … install.ps1 \| iex`](#3-windows) | Open a new terminal → `jarvis` |

<a id="1-macos--linux"></a>

### 1️⃣ macOS · Linux

```bash
curl -fsSL https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install | bash
```

Open a **new terminal** in any project folder and run `jarvis`.

<a id="2-macos-without-python"></a>

### 2️⃣ macOS without Python

Run these one after another:

```bash
brew install python@3.11
curl -fsSL https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install | bash
source ~/.zshrc        # or ~/.zprofile, so `jarvis` is on your PATH
jarvis
```

<a id="3-windows"></a>

### 3️⃣ Windows

In **PowerShell** or **Command Prompt**:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install.ps1 | iex"
```

Open a **new terminal** in your project folder and run `jarvis`. No admin rights needed.

<details>
<summary>No Python or Git on Windows yet?</summary>

<br/>

```powershell
winget install -e --id Python.Python.3.12
winget install -e --id Git.Git
```

Open a new terminal, then run the Windows command above.

</details>

### ✔️ What you get

| Command | What it opens |
| :-- | :-- |
| `jarvis` | Jarvis in the terminal |
| `jw` | Jarvis in the browser (on Windows: `jarvis web`) |

<details>
<summary><b><code>jarvis: command not found</code>?</b></summary>

<br/>

| Your computer | Fix |
| :-- | :-- |
| macOS · Linux | Open a new terminal, or run `export PATH="$HOME/.local/bin:$PATH"` (add it to `~/.zshrc` to keep it) |
| Windows | Open a new terminal, or run `$env:Path = "$HOME\.local\bin;$env:Path"` |
| `jw` missing | Run `jarvis update` — or use `jarvis web`, it's the same thing |

</details>

<br/>

<a id="terminal"></a>

## 🖥️ In the terminal — `jarvis`

Go to your project folder and start chatting:

```bash
cd my-project
jarvis
```

<p align="center">
  <img src="assets/terminal.png" alt="Jarvis running in the terminal" width="860" />
</p>

> **Tip:** type `/` to see every command, and `?` for keyboard shortcuts.

<br/>

<a id="browser"></a>

## 🌐 In the browser — `jw`

Prefer a normal app window? Run `jw` in your project folder. Jarvis opens in your browser, and you can close the terminal — it keeps running.

```bash
cd my-project
jw
```

<p align="center">
  <img src="assets/web.png" alt="Jarvis in the browser" width="860" />
</p>

> **Tip:** `jw stop` stops it. On Windows, use `jarvis web`.

<br/>

<a id="first-run"></a>

## 🚀 First run

Nothing to set up — Jarvis starts on a free model right away. When you want more:

| Type this | To |
| :-- | :-- |
| `/model` | Pick a different model (free ones are listed first) |
| `/provider` | Sign in with **Claude** or **ChatGPT**, or add an API key |
| `/web` | Get a QR code to open this chat on your phone |

<br/>

<a id="source"></a>

## 🛠️ Run from source

For developers who want to run or change the code. Needs **Git** and **Python 3.10+**.

### Step 1 — Get the code and install it locally

```bash
git clone https://github.com/PrajsRamteke/harness-agent.git
cd harness-agent
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .                 # installs dependencies + the `jarvis` and `jw` commands
```

### Step 2 — Run it

Pick any way. All of them run your local copy of the code:

| Way to run | Terminal | Browser |
| :-- | :-- | :-- |
| ⚡ **Commands** (venv active) | `jarvis` | `jw` |
| 🐍 **Plain Python** (from the repo folder) | `python agent.py` | `python agent.py web` |
| 📦 **Python module** | `python -m jarvis` | `python -m jarvis web` |

> **Tip:** your edits take effect the next time you start Jarvis. No reinstall needed.

### Step 3 (optional) — Use your copy everywhere

Makes `jarvis` and `jw` in any folder run this checkout, without activating the venv (macOS · Linux):

```bash
./scripts/install-global
```

<details>
<summary>Run the tests</summary>

<br/>

```bash
pip install pytest
python -m pytest tests/ -q
```

Tests use a temporary `HOME`, so they never touch your real settings or chats.

</details>

<br/>

## 🔄 Update or remove

<details>
<summary>Update</summary>

<br/>

Jarvis checks for updates by itself each time it starts. To update now:

| Command | What it does |
| :-- | :-- |
| `jarvis update` | Pull the latest version and reinstall |
| `jarvis update --check` | Only show what's new |
| `git pull` | Update a [source](#source) checkout |

Running the install command again also updates. Set `HARNESS_SKIP_UPDATE=1` to turn off automatic updates.

</details>

<details>
<summary>Uninstall</summary>

<br/>

**macOS · Linux**

```bash
rm -f "$(command -v jarvis)" "$(command -v jw)"
rm -rf ~/.local/share/harness-agent
rm -rf ~/.config/harness-agent      # optional: your settings and chats
```

**Windows**

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\harness-agent", "$HOME\.local\bin\jarvis.cmd"
```

</details>

<br/>

## 💬 Help

- 🐞 [Report a bug or ask for a feature](https://github.com/PrajsRamteke/harness-agent/issues)
- 💡 [Ask a question or share an idea](https://github.com/PrajsRamteke/harness-agent/discussions)

<br/>

<div align="center">

Built by [Prajwal Ramteke](https://github.com/PrajsRamteke) **If Jarvis helps you, give it a ⭐**

</div>
