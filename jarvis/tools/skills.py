"""Skill tools exposed to the model — list and load skills on demand.

These tools let the agent discover and use project-base skills (SKILL.md
files in .skills/, .opencode/skills/, .claude/skills/, or global config).

Following the OpenCode convention:
- skill_list() returns headers (name + description) only
- skill_load(name) returns the full skill content
"""
from ..storage import skills as sk


def skill_list() -> str:
    """List all available skills with their names and descriptions.

    Returns only headers (name + description), never full content.
    Use skill_load() to read the full skill content.
    """
    all_skills = sk.list_skills()
    if not all_skills:
        return "(no skills found — create .skills/<name>/SKILL.md with frontmatter)"
    
    lines = [f"Available skills ({len(all_skills)}):"]
    for s in all_skills:
        lines.append(f"  • {s['name']}: {s['description']}")
        lines.append(f"    (from {s['source_dir']})")
    
    lines.append("\nUse skill_load('<name>') to load a skill's full content.")
    return "\n".join(lines)


def skill_load(name: str) -> str:
    """Load a skill's full content by its name.

    Returns the entire SKILL.md (frontmatter + body) which contains
    the full reusable instructions for the agent to follow.

    Args:
        name: The skill name (e.g. 'git-release')
    """
    content = sk.load_skill(name)
    if content is None:
        available = [s["name"] for s in sk.list_skills()]
        if available:
            return f"Skill '{name}' not found. Available skills: {', '.join(available)}"
        return f"Skill '{name}' not found. No skills are currently available."
    return content


# ── schema definitions ─────────────────────────────────────────────────────────
SKILL_TOOLS = [
    {
        "name": "skill_list",
        "description": (
            "List all available project-base skills with their names and descriptions. "
            "These are reusable instructions defined in .skills/<name>/SKILL.md files. "
            "Each entry shows only the header (name + description). "
            "Use skill_load('<name>') to read the full skill content when a task "
            "matches a skill's purpose."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "skill_load",
        "description": (
            "Load the full content of a skill by its name. "
            "This returns the entire SKILL.md including frontmatter and body. "
            "Call skill_list() first to see available skills, then load the one "
            "that matches the current task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The skill name to load (e.g. 'git-release', 'example-skill')",
                },
            },
            "required": ["name"],
        },
    },
]
