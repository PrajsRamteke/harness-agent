"""Agent discovery — find and load <name>.md agent definitions.

Agents replace the legacy mode system. An "agent" is a markdown file with YAML
frontmatter that contributes a system-prompt addon when the user activates it.

Layout (parallels skills):

Project-local (always scanned; first-found wins by name):
    <cwd>/.harness/agents/<name>.md          (canonical)
    <cwd>/.claude/agents/<name>.md           (Claude Code compat)
    <cwd>/.opencode/agents/<name>.md         (OpenCode compat)
    <cwd>/.agents/<name>.md                  (legacy compat)
    <cwd>/.cursor/agents/<name>.md           (Cursor compat)

Global (opt-in via state.global_agents → settings.json `agent.global`):
    ~/.harness/agents/<name>.md              (canonical)
    ~/.config/harness-agent/agents/<name>.md (legacy fallback)
    ~/.claude/agents/<name>.md
    ~/.config/opencode/agents/<name>.md

Required frontmatter:
    ---
    name: <lowercase-kebab-case>          # must equal the filename stem
    description: <one-line summary>
    icon: "<single emoji or char>"        # optional, status-bar badge
    color: "<#hex or name>"               # optional, status-bar accent
    model: <model-id>                     # optional, future use
    ---

    <agent body — addon markdown>
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import time
from typing import Optional

from ..constants import (
    CONFIG_DIR,
    HARNESS_AGENTS_DIR,
    PROJECT_AGENTS_DIRNAME,
)
from .. import state


# Directories under the project root to scan (in priority order — first wins).
PROJECT_AGENT_DIRS = [
    PROJECT_AGENTS_DIRNAME,   # .harness/agents
    ".claude/agents",
    ".opencode/agents",
    ".agents",
    ".cursor/agents",
]

# Source labels used for the modal/scope display.
SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"


# ── cache ────────────────────────────────────────────────────────────────
_cache: list[dict] = []
_cache_key: str = ""
_cache_ts: float = 0.0
_CACHE_TTL = 5.0


def _find_project_root() -> pathlib.Path:
    """Walk up from CWD to find git root or the first dir with .harness/.skills."""
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


_NAME_RE = re.compile(r'^[a-z0-9]+(?:[_-][a-z0-9]+)*$')

# Tools like Claude Code / OpenCode let their agents use color tokens
# like ``orange``, ``error``, ``accent`` that Rich's color parser rejects
# and that would crash the Textual renderer. We map known synonyms to
# valid hex codes and drop anything else so Rich falls back to default.
_COLOR_SYNONYMS = {
    "orange":  "#d29922",
    "amber":   "#d29922",
    "warning": "#d29922",
    "error":   "#f85149",
    "danger":  "#f85149",
    "accent":  "#58a6ff",
    "primary": "#58a6ff",
    "info":    "#58a6ff",
    "success": "#3fb950",
    "muted":   "#8b949e",
    "subtle":  "#8b949e",
}


def _sanitize_color(value: str) -> str:
    """Return a Rich-parseable color, or '' when the input is unusable.

    Accepts: '#RRGGBB' hex codes, the small set of named Rich colors, plus
    a handful of OpenCode/Claude Code synonyms (`orange`, `error`, `accent`
    …). Anything else is dropped so the UI falls back to its default accent
    instead of raising ``rich.errors.MissingStyle`` during render.
    """
    v = (value or "").strip()
    if not v:
        return ""
    lower = v.lower()
    if lower in _COLOR_SYNONYMS:
        return _COLOR_SYNONYMS[lower]
    # Validate with Rich — single source of truth for what's renderable.
    try:
        from rich.color import Color
        Color.parse(v)
        return v
    except Exception:
        return ""


def _validate(name: str, description: str) -> bool:
    if not name or not isinstance(name, str) or len(name) > 64:
        return False
    if not _NAME_RE.match(name):
        return False
    if not description or not isinstance(description, str):
        return False
    if len(description) > 1024:
        return False
    return True


def _scan_agent_dir(agent_dir: pathlib.Path, scope: str, source_tag: str) -> list[dict]:
    """Scan one directory for ``<name>.md`` agent files."""
    out: list[dict] = []
    if not agent_dir.is_dir():
        return out
    try:
        for entry in sorted(agent_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix.lower() != ".md":
                continue
            if entry.name.startswith("_") or entry.name.startswith("."):
                continue
            try:
                content = entry.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            fields, body = _parse_frontmatter(content)
            name = (fields.get("name") or entry.stem).strip().lower()
            description = (fields.get("description") or "").strip()
            if not _validate(name, description):
                continue
            if entry.stem.lower() != name:
                # Filename must match `name:` field for unambiguous lookup.
                continue
            out.append({
                "name": name,
                "description": description,
                "icon": (fields.get("icon") or "").strip(),
                "color": _sanitize_color(fields.get("color") or ""),
                "model": (fields.get("model") or "").strip(),
                "path": str(entry),
                "source_dir": str(agent_dir),
                "source_tag": source_tag,
                "scope": scope,
                "_body": body,
            })
    except PermissionError:
        pass
    return out


def _seed_default_agents() -> None:
    """Copy bundled defaults (coding/reverse_eng/setup) into global
    ~/.harness/agents/ and also seed the **coding** agent into the current
    project's .harness/agents/ so it's visible even without global agents.

    Idempotent: never overwrites an existing user file. Runs once per process via
    a sentinel attribute check before each discover call.
    """
    try:
        HARNESS_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        bundled = pathlib.Path(__file__).resolve().parent.parent / "constants" / "default_agents"
        if not bundled.is_dir():
            return

        project_root = _find_project_root()
        project_agents_dir = project_root / PROJECT_AGENTS_DIRNAME

        for src in bundled.glob("*.md"):
            # Always seed to global dir (persists across projects)
            dst_global = HARNESS_AGENTS_DIR / src.name
            if not dst_global.exists():
                shutil.copyfile(src, dst_global)

            # Seed coding agent into the project dir so it's visible as a
            # project-level agent (no global_agents toggle required).
            # Other agents only go to project if the dir already exists
            # (user has opted into per-project agents).
            if src.name == "coding.md":
                project_agents_dir.mkdir(parents=True, exist_ok=True)
                dst_project = project_agents_dir / src.name
                if not dst_project.exists():
                    shutil.copyfile(src, dst_project)
            elif project_agents_dir.exists():
                dst_project = project_agents_dir / src.name
                if not dst_project.exists():
                    shutil.copyfile(src, dst_project)
    except Exception:
        pass


_seeded = False


def discover_agents(force: bool = False, include_global: bool | None = None) -> list[dict]:
    """Discover available agents (project + optional global).

    Args:
        force: bypass TTL cache.
        include_global: if None, read from ``state.global_agents``.

    Returns: list of dicts with keys
        name, description, icon, color, model, path, source_dir, source_tag, scope, _body
    Body content is included since agent definitions are typically short
    (a few KB) and the active agent's body is injected into the system prompt
    every turn — caching it here avoids re-reading the file on each API call.
    """
    global _cache, _cache_ts, _cache_key, _seeded

    if not _seeded:
        _seed_default_agents()
        _seeded = True

    if include_global is None:
        include_global = getattr(state, "global_agents", False)

    cache_mode = "global" if include_global else "project"
    now = time.monotonic()
    if not force and _cache and _cache_key == cache_mode and (now - _cache_ts) < _CACHE_TTL:
        return list(_cache)

    found: dict[str, dict] = {}

    # 1. Project-level dirs (always scanned).
    root = _find_project_root()
    for rel in PROJECT_AGENT_DIRS:
        d = root / rel
        for agent in _scan_agent_dir(d, scope=SCOPE_PROJECT, source_tag=rel):
            found.setdefault(agent["name"], agent)

    # 2. Global dirs (opt-in).
    if include_global:
        global_dirs = [
            ("~/.harness/agents", HARNESS_AGENTS_DIR),
            ("~/.config/harness-agent/agents", CONFIG_DIR / "agents"),
            ("~/.claude/agents", pathlib.Path.home() / ".claude" / "agents"),
            ("~/.config/opencode/agents", pathlib.Path.home() / ".config" / "opencode" / "agents"),
        ]
        for tag, gdir in global_dirs:
            for agent in _scan_agent_dir(gdir, scope=SCOPE_GLOBAL, source_tag=tag):
                found.setdefault(agent["name"], agent)

    result = list(found.values())
    _cache = result
    _cache_key = cache_mode
    _cache_ts = now
    return result


def list_agents() -> list[dict]:
    """All discovered agents — cached, header view (still includes _body)."""
    return discover_agents()


def find_agent(name: str) -> Optional[dict]:
    """Look up a single agent by name (case-insensitive)."""
    if not name:
        return None
    name_l = name.strip().lower()
    for a in discover_agents():
        if a["name"] == name_l:
            return a
    return None


def load_agent_body(name: str) -> Optional[str]:
    """Return the agent's markdown body (everything after frontmatter).

    Returns None if the agent doesn't exist or its body cannot be read.
    """
    agent = find_agent(name)
    if not agent:
        return None
    if agent.get("_body"):
        return agent["_body"]
    try:
        text = pathlib.Path(agent["path"]).read_text(encoding="utf-8", errors="replace")
        _, body = _parse_frontmatter(text)
        return body
    except Exception:
        return None


def agent_count() -> int:
    return len(discover_agents())


def global_count() -> int:
    """How many global-only agents are hidden in project-only mode."""
    project = discover_agents(force=True, include_global=False)
    project_names = {a["name"] for a in project}
    all_a = discover_agents(force=True, include_global=True)
    return sum(1 for a in all_a if a["name"] not in project_names)


def invalidate_cache() -> None:
    """Force the next discover_agents() call to re-scan."""
    global _cache, _cache_ts, _cache_key
    _cache = []
    _cache_ts = 0.0
    _cache_key = ""


# ── creation helpers (used by /agent new and /agent init) ────────────────

_AGENT_TEMPLATE = """---
name: {name}
description: {description}
icon: ""
color: ""
---

# {title}

<your agent body here — this content is appended to the system prompt when
this agent is active. Describe the role, workflow, tools to prefer, output
style, and any anti-hallucination rules.>
"""


def scaffold_agent(name: str, scope: str = "project", description: str = "") -> tuple[bool, str]:
    """Create a new agent file. Returns (success, path_or_message)."""
    name_l = name.strip().lower()
    if not _NAME_RE.match(name_l):
        return False, "name must be lowercase-kebab-case (a-z, 0-9, dashes)"
    desc = description.strip() or f"Custom agent: {name_l}"

    if scope == "global":
        target_dir = HARNESS_AGENTS_DIR
    else:
        root = _find_project_root()
        target_dir = root / PROJECT_AGENTS_DIRNAME

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return False, f"could not create {target_dir}: {e}"

    target = target_dir / f"{name_l}.md"
    if target.exists():
        return False, f"{target} already exists"

    try:
        target.write_text(
            _AGENT_TEMPLATE.format(name=name_l, description=desc, title=name_l.replace("-", " ").title()),
            encoding="utf-8",
        )
    except Exception as e:
        return False, f"write failed: {e}"

    invalidate_cache()
    return True, str(target)


def auto_activate_coding_agent() -> None:
    """On **every** ``jarvis`` launch: check the current directory.

    - Coding project (has ``package.json``, ``src/``, etc.) → activate
      the coding agent.
    - Not a coding project → reset to default (no agent).

    This means every fresh launch re-evaluates the environment.
    Mid-session ``/agent`` changes still work as before — they last
    until the next launch.
    """
    # --- Detect ---
    try:
        from ..project_context import detect_coding_project
        is_coding = detect_coding_project()
    except Exception:
        is_coding = False

    if is_coding:
        # Already correct → fast-path skip
        if state.active_agent_name == "coding":
            return

        # Find and activate the coding agent
        coding = find_agent("coding")
        if not coding:
            for a in discover_agents(force=True, include_global=True):
                if a["name"] == "coding":
                    coding = a
                    break

        if coding:
            state.set_active_agent(coding)
            try:
                from ..console import console
                icon = (coding.get("icon") or "").strip()
                color = (coding.get("color") or "#3fb950").strip()
                label = f"{icon} {coding['name']}" if icon else coding["name"]
                console.print(
                    f"[bold {color}]⚡ auto-activated {label}[/]  "
                    f"[dim](coding project detected — /agent to change)[/]"
                )
            except Exception:
                pass
    else:
        # Not a coding project → reset to default (no agent)
        if state.active_agent_name:
            state.set_active_agent(None)


def as_prompt_block() -> str:
    """Tiny system-prompt advert that lists what's available (no bodies).

    Surfaced so the LLM is aware that other agents exist and can suggest
    switching when relevant. The body of the active agent is appended
    separately in ``repl/system.py``.
    """
    agents = discover_agents()
    if not agents:
        return ""
    active_name = ""
    try:
        if isinstance(state.active_agent, dict):
            active_name = state.active_agent.get("name", "")
    except Exception:
        pass
    bits = []
    for a in agents:
        marker = " (active)" if a["name"] == active_name else ""
        bits.append(f"{a['name']}{marker}")
    line = ", ".join(bits[:12])
    if len(agents) > 12:
        line += f", … (+{len(agents) - 12} more)"
    return f"AGENTS available: {line}. Activate via `/agent <name>`."
