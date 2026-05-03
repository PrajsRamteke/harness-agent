"""Skill discovery — find and load SKILL.md definitions.

Follows the OpenCode.ai skill convention:
- Project-level: .skills/<name>/SKILL.md (primary Jarvis format)
- OpenCode-compat: .opencode/skills/<name>/SKILL.md
- Claude-compat: .claude/skills/<name>/SKILL.md
- Agent-compat: .agents/skills/<name>/SKILL.md
- Global: ~/.config/harness-agent/skills/<name>/SKILL.md
- Global: ~/.config/opencode/skills/<name>/SKILL.md
- Global: ~/.claude/skills/<name>/SKILL.md

By default, only project-level skills are discovered. Set
`include_global=True` (or state.global_skills) to also include
global skills from the config directories.

Each SKILL.md must start with YAML frontmatter:
    ---
    name: <required>
    description: <required, 1-1024 chars>
    license: <optional>
    compatibility: <optional>
    metadata: <optional, string-to-string map>
    ---

During discovery we only read the frontmatter (header). Full content
is loaded on demand via load_skill().
"""
from __future__ import annotations

import os
import pathlib
import re
import time
from typing import Dict, List, Optional, Tuple

from ..constants import CONFIG_DIR
from .. import state

# Directories to scan for skills, in priority order (first-found wins for dupes)
SKILL_DIRS = [
    # Primary Jarvis format
    ".skills",
    # OpenCode compatibility
    ".opencode/skills",
    # Claude compatibility  
    ".claude/skills",
    # Agent compatibility
    ".agents/skills",
]

# ── cache ──────────────────────────────────────────────────────────────────────
_cache: list[dict] = []
_cache_key: str = ""  # "project" or "global" — which mode was cached
_cache_ts: float = 0
_CACHE_TTL = 5.0  # seconds before re-scanning


def _find_project_root() -> pathlib.Path:
    """Walk up from CWD to find git root or the first dir with .skills."""
    cwd = pathlib.Path.cwd().resolve()
    
    # Check git root first
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
    
    # Fallback: walk up looking for .git or .skills
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists() or (parent / ".skills").exists():
            return parent
    
    return cwd


def _find_skill_dirs(root: pathlib.Path) -> list[pathlib.Path]:
    """Find all directories that may contain skills under root."""
    dirs = []
    for skill_dir_name in SKILL_DIRS:
        d = root / skill_dir_name
        if d.is_dir():
            dirs.append(d)
    return dirs


def _parse_frontmatter(content: str) -> dict:
    """Parse YAML-like frontmatter from SKILL.md content.

    Returns a dict with 'name', 'description', and optional fields.
    Only these fields are extracted from the frontmatter.
    Unknown fields in frontmatter are silently ignored (per OpenCode spec).
    """
    result: dict = {}
    
    # Match frontmatter between --- markers
    m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not m:
        return result
    
    fm = m.group(1)
    
    # Parse simple key: value pairs (handle quoted and unquoted values)
    current_key = None
    current_val_lines = []
    
    for line in fm.split('\n'):
        # Check for a new key: value line (possibly with leading spaces for indentation)
        key_match = re.match(r'^(\w[\w.-]*)\s*:\s*(.*)', line)
        if key_match:
            # Save previous key if any
            if current_key and current_val_lines:
                val = '\n'.join(current_val_lines).strip()
                if val:
                    result[current_key] = val
            
            current_key = key_match.group(1)
            val_part = key_match.group(2).strip()
            # Strip quotes if present
            if val_part and val_part[0] in ('"', "'") and val_part[-1] == val_part[0]:
                val_part = val_part[1:-1]
            current_val_lines = [val_part] if val_part else []
        elif current_key and line.strip():
            # Continuation of multi-line value (indented)
            current_val_lines.append(line.strip())
        else:
            # Empty line — reset multi-line accumulation
            if current_key and current_val_lines:
                val = '\n'.join(current_val_lines).strip()
                if val:
                    result[current_key] = val
            current_key = None
            current_val_lines = []
    
    # Don't forget the last key
    if current_key and current_val_lines:
        val = '\n'.join(current_val_lines).strip()
        if val:
            result[current_key] = val
    
    return result


def _validate_skill(name: str, description: str) -> bool:
    """Validate skill name and description per OpenCode rules."""
    if not name or not isinstance(name, str):
        return False
    if not description or not isinstance(description, str):
        return False
    
    # Name must be 1-64 chars, lowercase alphanumeric with single hyphens
    if not re.match(r'^[a-z0-9]+(-[a-z0-9]+)*$', name):
        return False
    if len(name) > 64:
        return False
    
    # Description must be 1-1024 chars
    if len(description) > 1024:
        return False
    
    return True


def _scan_one_skill_dir(skill_dir: pathlib.Path, scope: str = "project") -> list[dict]:
    """Scan a single skill directory (e.g. .skills/) for SKILL.md files.

    Returns list of {name, description, path, source_dir, scope} dicts.
    `scope` is "project" for project-local skills, "global" for user-wide ones.
    """
    skills = []
    
    if not skill_dir.is_dir():
        return skills
    
    try:
        for entry in sorted(skill_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            
            try:
                content = skill_md.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            
            fm = _parse_frontmatter(content)
            name = (fm.get("name") or "").strip().lower()
            description = (fm.get("description") or "").strip()
            
            if not _validate_skill(name, description):
                continue
            
            # Verify directory name matches skill name
            if entry.name.lower() != name:
                continue
            
            skills.append({
                "name": name,
                "description": description,
                "path": str(skill_md),
                "source_dir": str(skill_dir),
                "scope": scope,
            })
    except PermissionError:
        pass
    
    return skills


def discover_skills(force: bool = False, include_global: bool | None = None) -> list[dict]:
    """Discover available skills.

    Args:
        force: If True, bypass the TTL cache and re-scan.
        include_global: If True, include skills from global config dirs.
            If None, read from ``state.global_skills`` (defaults to False).

    Returns deduplicated list of {name, description, path, source_dir, scope} dicts.
    Only reads frontmatter (name + description), never full content.
    """
    global _cache, _cache_ts, _cache_key

    if include_global is None:
        include_global = state.global_skills

    cache_mode = "global" if include_global else "project"
    now = time.monotonic()
    if not force and _cache and _cache_key == cache_mode and (now - _cache_ts) < _CACHE_TTL:
        return list(_cache)

    all_skills: dict[str, dict] = {}  # name -> skill dict

    # 1. Project-level skills — walk up from CWD (always included)
    root = _find_project_root()
    for skill_dir_name in SKILL_DIRS:
        skill_dir = root / skill_dir_name
        for skill in _scan_one_skill_dir(skill_dir, scope="project"):
            if skill["name"] not in all_skills:
                all_skills[skill["name"]] = skill

    # 2. Global skills — only when include_global=True
    if include_global:
        global_dirs = [
            ("global", CONFIG_DIR / "skills"),
            ("global", pathlib.Path.home() / ".config" / "opencode" / "skills"),
            ("global", pathlib.Path.home() / ".claude" / "skills"),
        ]
        for scope_label, gdir in global_dirs:
            for skill in _scan_one_skill_dir(gdir, scope="global"):
                if skill["name"] not in all_skills:
                    all_skills[skill["name"]] = skill

    result = list(all_skills.values())
    _cache = result
    _cache_key = cache_mode
    _cache_ts = now
    return result


def load_skill(name: str) -> str | None:
    """Load the full content of a skill by name.

    Returns the full SKILL.md text (including frontmatter), or None
    if no skill with that name is found.
    """
    skills = list_skills()
    for skill in skills:
        if skill["name"].lower() == name.lower():
            p = pathlib.Path(skill["path"])
            if p.exists():
                try:
                    return p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    return None
    return None


def list_skills() -> list[dict]:
    """Return all discovered skill headers (name + description + scope only)."""
    return discover_skills()


def skill_count() -> int:
    """Return the number of discovered skills."""
    return len(discover_skills())


def global_count() -> int:
    """Return number of global-only skills (not found in project scope)."""
    # Discover in project-only mode to get the baseline
    project_skills = discover_skills(force=True, include_global=False)
    project_names = {s["name"] for s in project_skills}
    all_skills = discover_skills(force=True, include_global=True)
    return sum(1 for s in all_skills if s["name"] not in project_names)


def as_prompt_block() -> str:
    """Format discovered skills as a compact system-prompt block.

    Only shows name + description (headers), never full content.
    The agent must use skill_load() to read the full skill content.

    Returns empty string if no skills found.
    """
    skills = discover_skills()
    if not skills:
        return ""
    
    scope_tag = "global" if state.global_skills else "project"
    lines = []
    for skill in skills:
        desc = skill["description"]
        tag = f" [{skill['scope']}]" if skill["scope"] == "global" else ""
        lines.append(f"- {skill['name']}{tag}: {desc}")
    
    header = (
        "AVAILABLE SKILLS (defined in .skills/ or global skills/ directories):\n"
        f"Scope: [{'global (project + global)' if state.global_skills else 'project-only (.skills/ directories)'}]\n"
        "These are reusable instructions you can load on demand. "
        "Only headers are shown below — use skill_load('<name>') to read "
        "the full skill content when a task matches a skill's purpose.\n\n"
    )
    
    return (
        header
        + "\n".join(lines)
        + f"\n\nUse skill_list() to refresh, skill_load('<name>') to read full content. "
          f"Toggle global skills with /skills global on|off."
    )
