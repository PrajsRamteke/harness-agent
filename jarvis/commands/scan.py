"""Handle /scan command — one-time LLM-driven system scan.

Usage:
  /scan me     — Triggers the LLM to deeply explore the system using its tools
                 (fast_find, read_file, read_document, read_images_text, search_code,
                 etc.), find important personal/user info, and save only genuinely
                 durable facts to memory via memory_save.

                 This is a ONE-TIME command. After it runs, /scan becomes inert
                 showing "already completed". The scan prompt is designed so the
                 LLM decides what's important — not the command itself.

  /scan        — Shows usage hint (does nothing).
"""

from typing import Tuple

from ..console import console
from .. import state


def handle_scan(c: str, arg: str) -> Tuple[bool, str]:
    """Return (handled, prompt_or_empty).

    If prompt is non-empty, the caller should send it as a user message to the LLM.
    The LLM then executes the scan using its own tools and saves facts to memory.
    """
    if c != "/scan":
        return False, ""

    if getattr(state, "scan_completed", False):
        console.print("[yellow]🔍 Scan already completed — this is a one-time command.[/]")
        console.print("[dim]Use /memory to view stored facts or /memory add to add more.[/]")
        return True, ""

    mode = (arg or "").strip().lower()

    if mode != "me":
        console.print(
            "[yellow]Usage: /scan me[/] — one-time AI-driven system scan.\n"
            "[dim]The LLM will explore your machine and save important facts to memory.[/]"
        )
        return True, ""

    # Flag immediately so it can't be re-run mid-scan
    state.scan_completed = True

    prompt = _build_scan_prompt()
    console.print("[cyan]🔍 AI-driven scan initiated...[/]")
    console.print(
        "[dim]The LLM is now exploring your system using its tools.[/]\n"
        "[dim]It will find important info and save it to memory automatically.[/]"
    )
    return True, prompt


def _build_scan_prompt() -> str:
    return r"""[SYSTEM OVERRIDE — USER PROFILE SCAN]

You are running a one-time system scan to build a durable user profile. Follow the instructions below precisely.

## YOUR MISSION
Discover who this user is — their identity, work, projects, interests, development environment, and anything else that would be genuinely useful for you to know across future sessions. Save only IMPORTANT, DURABLE facts to memory using `memory_save`. Skip transient data.

## TOOLS YOU CAN USE
- `fast_find` — search the entire Mac by filename (milliseconds via Spotlight). Use this FIRST for broad searches.
- `read_file` — read config files, dotfiles, shell profiles, source code.
- `read_document` — read PDFs, spreadsheets, markdown, plain text.
- `read_image_text` / `read_images_text` — OCR screenshots, photos, ID documents.
- `search_code` — find patterns in codebases.
- `glob_files` — find files by pattern under a directory.
- `run_bash` — run shell commands (e.g. `git remote get-url origin`, `git config --global user.name`).
- `memory_save` — save a durable fact to persistent memory.
- `memory_list` — check what's already stored to avoid duplicates.

## SCAN WORKFLOW (follow in order)

### Phase 1 — Identity
1. `run_bash` → `id -F` (macOS full name), `git config --global user.name`, `git config --global user.email`
2. `read_file` → `~/.gitconfig`, `~/.ssh/id_*.pub` for SSH emails
3. `fast_find` query=".zshrc" or read `~/.zshrc`, `~/.zshenv`, `~/.bash_profile` — extract exports, aliases, env vars
4. `fast_find` query=".env" kind="file" path="~/Desktop" — read any .env files for project context
5. `memory_save` each unique identity fact

### Phase 2 — Documents & Media
Search the Desktop, Downloads, Documents, and home folder for:
- Resumes, CVs, student IDs, certificates, ID cards (keywords: resume, cv, student, id_card, aadhar, pan, voter, license, passport, birth, marksheet, degree, diploma, transcript, certificate)
- Screenshots with potentially useful info (keywords: screenshot, screen, capture, photo)
- Notes files, pinned context, markdown files

Use: `fast_find` with appropriate queries and ext filters, then `read_document` or `read_image_text` on the matches. Extract key info and save via `memory_save`.

### Phase 3 — Projects & Work
1. Find git repos: `fast_find` query=".git" kind="folder" path="~/Desktop" — look for project roots
2. For each significant repo: check origin remote, main language, purpose
3. Check known work paths like `~/Desktop`
4. `read_file` on `package.json`, `README.md`, `pyproject.toml`, `Cargo.toml` for project metadata
5. `memory_save` project summaries (name, git origin, purpose)

### Phase 4 — Development Environment
Quick check for installed tools (run each command):
- `node --version`, `npm --version`, `python3 --version`
- `cargo --version`, `rustc --version`, `go version`, `java -version`
- `brew --version`, `nvm --version`, `bun --version`
- `code --version` (VS Code), `cursor --version` (Cursor)
Save only what's unusual or distinctive — NOT common tools everyone has.

### Phase 5 — Interests & Personal
1. Look at Desktop folders, personal project names for hobby signals
2. Check if there's a `~/Desktop` or similar personal folder
3. Any anime, gaming, music, photography, or other interest signals in folder names, notes, wallpaper files
4. `fast_find` query="anime" or "attack" or "aot" in Desktop/Downloads
5. `memory_save` any interesting personal facts

## WHAT TO SAVE (use memory_save for each)
✅ Full name, email, usernames (GitHub, etc.)
✅ Work: company, role, team structure, work repos
✅ Personal projects: name + description + language
✅ Education: degrees, institutions, certifications
✅ Interests: hobbies, anime, sports, music, etc.
✅ SSH keys and git identities
✅ Key environment variables (WORK_, HABUILD_, etc.)
✅ Important file locations (project folders, notes)

## WHAT TO SKIP (DO NOT save these)
❌ Git commit messages, branch names, log lines
❌ Uptime, timestamps, date/time
❌ Tool versions (Node, npm, Python, etc.) — unless unusual
❌ Transient state: current directory, session IDs, token counts
❌ File sizes, line counts, directory structure listings
❌ System load averages, memory usage
❌ Anything that will be different tomorrow

## REMINDERS
- Check `memory_list` first to avoid duplicating existing facts.
- Use `memory_save` with concise one-line facts (e.g. "Full name: Prajwal Bhimrao Ramteke" not paragraphs).
- You have full filesystem access — use it to explore thoroughly.
- Be curious but efficient: prioritize files/directories that are likely to contain useful profile info.
- When in doubt about whether something is important, ask yourself: "Will this help me serve this user better in a completely new session next week?" If yes, save it. If no, skip it.
- After scanning, write a brief summary to the user saying what you found and saved.

Begin.
"""
