"""Custom slash-command discovery — user-authored prompt templates.

A "command" is a markdown file whose body is a prompt template. Typing
``/<name> [args]`` expands the template and sends it as the user message —
e.g. drop ``pr-description.md`` into ``.harness/commands/`` and trigger it
with ``/pr-description``.

Layout (parallels agents/skills):

Project-local (always scanned; first-found wins by name):
    <cwd>/.harness/commands/<name>.md        (canonical)
    <cwd>/.claude/commands/<name>.md         (Claude Code compat)
    <cwd>/.opencode/command/<name>.md        (OpenCode compat)
    <cwd>/.opencode/commands/<name>.md
    <cwd>/.agents/commands/<name>.md         (legacy compat)

Global (opt-in via state.global_commands → settings.json `commands.global`,
default True):
    ~/.harness/commands/<name>.md            (canonical)
    ~/.config/harness-agent/commands/<name>.md
    ~/.claude/commands/<name>.md
    ~/.config/opencode/command/<name>.md
    ~/.config/opencode/commands/<name>.md

File format — frontmatter is OPTIONAL (Claude Code command files without
frontmatter work as-is; the whole file becomes the template):

    ---
    name: pr-description            # optional, must equal filename stem
    description: <one-line summary> # optional, shown in listings/palette
    argument-hint: "[extra notes]"  # optional, shown in listings
    ---

    <prompt template — sent as the user message>

Template placeholders (substituted at trigger time):
    $ARGUMENTS   → everything typed after the command name
    $1 … $9      → positional arguments (whitespace-split, shlex-aware)
If arguments are given but the template contains no placeholder, they are
appended on a new line so nothing the user typed is lost.

Subdirectories are scanned too (``.claude/commands/git/pr.md`` → ``/pr``).
"""
from __future__ import annotations

import pathlib
import re
import shlex
import shutil
import time
from typing import Optional

from ..constants import (
    CONFIG_DIR,
    HARNESS_COMMANDS_DIR,
    PROJECT_COMMANDS_DIRNAME,
)
from .. import state

# Directories under the project root to scan (in priority order — first wins).
PROJECT_COMMAND_DIRS = [
    PROJECT_COMMANDS_DIRNAME,   # .harness/commands
    ".claude/commands",
    ".opencode/command",
    ".opencode/commands",
    ".agents/commands",
]

SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"

_NAME_RE = re.compile(r'^[a-z0-9]+(?:[_-][a-z0-9]+)*$')

# ── cache ────────────────────────────────────────────────────────────────
_cache: list[dict] = []
_cache_key: str = ""
_cache_ts: float = 0.0
_CACHE_TTL = 5.0


def _find_project_root() -> pathlib.Path:
    """Walk up from CWD to find git root or the first dir with .harness."""
    cwd = pathlib.Path.cwd().resolve()
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            git_root = pathlib.Path(result.stdout.strip()).resolve()
            if git_root.exists():
                return git_root
    except Exception:
        pass

    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists() or (parent / ".harness").exists():
            return parent
    return cwd


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML-ish frontmatter. Returns (fields, body)."""
    fields: dict = {}
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)$', content, re.DOTALL)
    if not m:
        return fields, content
    fm, body = m.group(1), m.group(2)
    for line in fm.split("\n"):
        key_match = re.match(r'^([A-Za-z_][\w.-]*)\s*:\s*(.*)$', line)
        if not key_match:
            continue
        key, val = key_match.group(1).strip(), key_match.group(2).strip()
        if val and val[0] in ("'", '"') and val[-1] == val[0]:
            val = val[1:-1]
        fields[key] = val
    return fields, body


def _default_description(body: str) -> str:
    """First non-empty, non-heading-marker line of the body, truncated."""
    for line in body.splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:120]
    return ""


def _scan_command_dir(cmd_dir: pathlib.Path, scope: str, source_tag: str) -> list[dict]:
    """Scan one directory (recursively) for ``<name>.md`` command files."""
    out: list[dict] = []
    if not cmd_dir.is_dir():
        return out
    try:
        for entry in sorted(cmd_dir.rglob("*.md")):
            if not entry.is_file():
                continue
            if entry.name.startswith("_") or entry.name.startswith("."):
                continue
            # Skip directory READMEs — they document, not define, commands.
            if entry.stem.lower() == "readme":
                continue
            try:
                content = entry.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            fields, body = _parse_frontmatter(content)
            name = (fields.get("name") or entry.stem).strip().lower()
            if not _NAME_RE.match(name) or len(name) > 64:
                continue
            if fields.get("name") and entry.stem.lower() != name:
                # When frontmatter names the command, the filename must match.
                continue
            if not body.strip():
                continue
            description = (fields.get("description") or "").strip() or _default_description(body)
            out.append({
                "name": name,
                "description": description[:1024],
                "argument_hint": (fields.get("argument-hint") or fields.get("argument_hint") or "").strip(),
                "path": str(entry),
                "source_dir": str(cmd_dir),
                "source_tag": source_tag,
                "scope": scope,
                "_body": body,
            })
    except PermissionError:
        pass
    return out


def discover_commands(force: bool = False, include_global: bool | None = None) -> list[dict]:
    """Discover available custom commands (project + optional global).

    Returns: list of dicts with keys
        name, description, argument_hint, path, source_dir, source_tag, scope, _body
    Bodies are cached — command templates are tiny and expanded on every
    trigger, so re-reading the file each time buys nothing.
    """
    global _cache, _cache_ts, _cache_key

    if include_global is None:
        include_global = getattr(state, "global_commands", True)

    cache_mode = "global" if include_global else "project"
    now = time.monotonic()
    if not force and _cache and _cache_key == cache_mode and (now - _cache_ts) < _CACHE_TTL:
        return list(_cache)

    found: dict[str, dict] = {}

    # 1. Project-level dirs (always scanned).
    root = _find_project_root()
    for rel in PROJECT_COMMAND_DIRS:
        d = root / rel
        for cmd in _scan_command_dir(d, scope=SCOPE_PROJECT, source_tag=rel):
            found.setdefault(cmd["name"], cmd)

    # 2. Global dirs (opt-in).
    if include_global:
        global_dirs = [
            ("~/.harness/commands", HARNESS_COMMANDS_DIR),
            ("~/.config/harness-agent/commands", CONFIG_DIR / "commands"),
            ("~/.claude/commands", pathlib.Path.home() / ".claude" / "commands"),
            ("~/.config/opencode/command", pathlib.Path.home() / ".config" / "opencode" / "command"),
            ("~/.config/opencode/commands", pathlib.Path.home() / ".config" / "opencode" / "commands"),
        ]
        for tag, gdir in global_dirs:
            for cmd in _scan_command_dir(gdir, scope=SCOPE_GLOBAL, source_tag=tag):
                found.setdefault(cmd["name"], cmd)

    result = list(found.values())
    _cache = result
    _cache_key = cache_mode
    _cache_ts = now
    return result


def list_commands() -> list[dict]:
    """All discovered commands — cached header view (includes _body)."""
    return discover_commands()


def find_command(name: str) -> Optional[dict]:
    """Look up a single command by name (case-insensitive)."""
    if not name:
        return None
    name_l = name.strip().lower()
    for c in discover_commands():
        if c["name"] == name_l:
            return c
    return None


def command_count() -> int:
    return len(discover_commands())


def global_count() -> int:
    """How many global-only commands are hidden in project-only mode."""
    project = discover_commands(force=True, include_global=False)
    project_names = {c["name"] for c in project}
    all_c = discover_commands(force=True, include_global=True)
    return sum(1 for c in all_c if c["name"] not in project_names)


def invalidate_cache() -> None:
    """Force the next discover_commands() call to re-scan."""
    global _cache, _cache_ts, _cache_key
    _cache = []
    _cache_ts = 0.0
    _cache_key = ""


# ── template expansion ────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r'\$(ARGUMENTS|[1-9])')


def expand_template(body: str, args: str) -> str:
    """Substitute $ARGUMENTS / $1..$9 in *body* with *args*.

    If args are given but the template has no placeholder, append them on a
    new line so user input is never silently dropped. Unfilled positional
    placeholders are removed.
    """
    args = (args or "").strip()
    try:
        positional = shlex.split(args)
    except ValueError:
        positional = args.split()

    has_placeholder = bool(_PLACEHOLDER_RE.search(body))

    def _sub(m: re.Match) -> str:
        token = m.group(1)
        if token == "ARGUMENTS":
            return args
        idx = int(token) - 1
        return positional[idx] if idx < len(positional) else ""

    out = _PLACEHOLDER_RE.sub(_sub, body).strip()
    if args and not has_placeholder:
        out = f"{out}\n\n{args}"
    return out


def expand_command(name: str, args: str = "") -> Optional[str]:
    """Resolve *name* and expand its template with *args*.

    Returns the prompt text to send, or None when no such command exists.
    """
    rec = find_command(name)
    if not rec:
        return None
    body = rec.get("_body") or ""
    if not body:
        try:
            text = pathlib.Path(rec["path"]).read_text(encoding="utf-8", errors="replace")
            _, body = _parse_frontmatter(text)
        except Exception:
            return None
    return expand_template(body, args)


# ── creation / deletion helpers ───────────────────────────────────────────

_COMMAND_TEMPLATE = """---
name: {name}
description: {description}
argument-hint: ""
---

<your prompt template here — sent as the user message when you type /{name}.

Placeholders:
  $ARGUMENTS  → everything typed after /{name}
  $1 … $9     → positional arguments

Example body for a /pr-description command:
  Look at the current git diff and recent commits, then write a concise
  pull-request description with a summary, change list, and test notes.
  Extra context from me: $ARGUMENTS>
"""


def scaffold_command(name: str, scope: str = "project", description: str = "") -> tuple[bool, str]:
    """Create a new command file. Returns (success, path_or_message)."""
    name_l = name.strip().lstrip("/").lower()
    if not _NAME_RE.match(name_l):
        return False, "name must be lowercase-kebab-case (a-z, 0-9, dashes)"
    if _is_reserved(name_l):
        return False, f"'/{name_l}' is a built-in command — pick another name"
    desc = description.strip() or f"Custom command: {name_l}"

    if scope == "global":
        target_dir = HARNESS_COMMANDS_DIR
    else:
        root = _find_project_root()
        target_dir = root / PROJECT_COMMANDS_DIRNAME

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return False, f"could not create {target_dir}: {e}"

    target = target_dir / f"{name_l}.md"
    if target.exists():
        return False, f"{target} already exists"

    try:
        target.write_text(
            _COMMAND_TEMPLATE.format(name=name_l, description=desc),
            encoding="utf-8",
        )
    except Exception as e:
        return False, f"write failed: {e}"

    invalidate_cache()
    return True, str(target)


def write_command(
    name: str,
    description: str,
    body: str,
    *,
    scope: str = "project",
    existing_path: str | None = None,
) -> tuple[bool, str]:
    """Create or update a command file with explicit content.

    Used by the TUI editor modal. When *existing_path* is given the file is
    updated in place (same directory — works for project AND global copies);
    a changed name renames the file. Otherwise a new file is created in the
    directory implied by *scope*. Returns (success, path_or_message).
    """
    name_l = name.strip().lstrip("/").lower()
    if not _NAME_RE.match(name_l):
        return False, "name must be lowercase-kebab-case (a-z, 0-9, dashes)"
    if _is_reserved(name_l):
        return False, f"'/{name_l}' is a built-in command — pick another name"
    desc = " ".join(description.split()).strip() or f"Custom command: {name_l}"
    body_text = body.strip()
    if not body_text:
        return False, "template body is empty"

    old: pathlib.Path | None = None
    hint = ""
    if existing_path:
        old = pathlib.Path(existing_path)
        target_dir = old.parent
        # Preserve fields the editor doesn't surface (argument-hint).
        try:
            fields, _ = _parse_frontmatter(old.read_text(encoding="utf-8", errors="replace"))
            hint = (fields.get("argument-hint") or fields.get("argument_hint") or "").strip()
        except Exception:
            pass
    elif scope == "global":
        target_dir = HARNESS_COMMANDS_DIR
    else:
        target_dir = _find_project_root() / PROJECT_COMMANDS_DIRNAME

    target = target_dir / f"{name_l}.md"
    renaming = old is not None and old.name != target.name
    if (old is None or renaming) and target.exists():
        return False, f"{target} already exists"

    fm = [f"name: {name_l}", f"description: {desc}"]
    if hint:
        fm.append(f'argument-hint: "{hint}"')
    content = "---\n" + "\n".join(fm) + "\n---\n\n" + body_text + "\n"

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if renaming and old is not None and old.exists():
            old.unlink()
    except OSError as e:
        return False, f"write failed: {e}"

    invalidate_cache()
    return True, str(target)


def delete_command(name: str) -> tuple[bool, str]:
    """Delete a command file from disk. Returns (success, path_or_message).

    Searches both scopes regardless of the global toggle — the user named
    the command explicitly, so a hidden global copy should still be found.
    """
    name_l = name.strip().lower()
    cmds = discover_commands(force=True, include_global=True)
    rec = next((c for c in cmds if c["name"] == name_l), None)
    if not rec:
        return False, f"command '{name}' not found"
    try:
        pathlib.Path(rec["path"]).unlink()
    except OSError as e:
        return False, f"delete failed: {e}"
    invalidate_cache()
    return True, rec["path"]


def _is_reserved(name: str) -> bool:
    """True when /<name> collides with a built-in slash command."""
    try:
        from ..tui.commands_catalog import COMMANDS
        builtins = {c.strip().lstrip("/").split()[0] for c, _ in COMMANDS}
    except Exception:
        builtins = set()
    # Not all built-ins live in the palette catalog — pad with dispatch-only ones.
    builtins |= {
        "command", "commands", "skill", "skills", "agent", "agents",
        "quit", "retry", "paste", "multi", "scan", "mcp", "upgrade",
    }
    return name in builtins


# ── scope transfer (import / export) ──────────────────────────────────────

def import_command_to_project(name: str) -> dict:
    """Copy one global command into the project ``.harness/commands/`` dir."""
    name_l = name.strip().lower()
    cmds = discover_commands(force=True, include_global=True)
    rec = next((c for c in cmds if c["name"] == name_l), None)
    if rec is None:
        return {"added": [], "skipped": [], "error": f"'{name_l}' not found"}

    root = _find_project_root()
    target_dir = root / PROJECT_COMMANDS_DIRNAME
    target = target_dir / f"{name_l}.md"

    if rec.get("scope") == SCOPE_PROJECT:
        return {"added": [], "skipped": [name_l], "path": str(target if target.exists() else rec["path"])}

    if target.exists():
        return {"added": [], "skipped": [name_l], "path": str(target)}

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rec["path"], target)
    except OSError as e:
        return {"added": [], "skipped": [], "error": str(e)}

    invalidate_cache()
    return {"added": [name_l], "skipped": [], "path": str(target)}


def export_command_to_global(name: str) -> dict:
    """Copy one project command into the global ``~/.harness/commands/`` dir."""
    name_l = name.strip().lower()
    cmds = discover_commands(force=True, include_global=True)
    rec = next((c for c in cmds if c["name"] == name_l), None)
    if rec is None:
        return {"added": [], "skipped": [], "error": f"'{name_l}' not found"}

    if rec.get("scope") == SCOPE_GLOBAL:
        return {"added": [], "skipped": [name_l], "path": str(rec["path"])}

    target_dir = HARNESS_COMMANDS_DIR
    target = target_dir / f"{name_l}.md"

    if target.exists():
        return {"added": [], "skipped": [name_l], "path": str(target)}

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rec["path"], target)
    except OSError as e:
        return {"added": [], "skipped": [], "error": str(e)}

    invalidate_cache()
    return {"added": [name_l], "skipped": [], "path": str(target)}
