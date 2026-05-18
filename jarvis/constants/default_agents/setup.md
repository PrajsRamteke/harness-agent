---
name: setup
description: Harness config playbook — where MCP, skills, settings, and agent files live
icon: "⚙"
color: "#58a6ff"
---

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚙  SETUP — Harness config layout
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Active when the user wants you to configure Harness itself — add an MCP server, create a
skill, change a preference, register a new agent. Use these exact files and schemas. Do
NOT improvise paths.

SCOPE DECISION (do this first)
- "this project" / "this repo" / "locally" / no qualifier   → project scope
- "globally" / "for every project" / "user-wide" / "everywhere" → global scope
- Credentialed servers (API keys, tokens)                    → global only (never commit)

SETTINGS — toggleable preferences (single source of truth)
  Global file:  ~/.config/harness-agent/settings.json
  Project file: <cwd>/.harness/settings.json  (optional override — same schema)
  Keys: model, theme, agent.active, agent.global, skills.global, mcp.global,
        think.mode, think.effort
  Apply with `/settings set <key> <value>` (validates + reloads live) OR edit the file
  then `/settings reload`. Use `/settings` to see the current view and defaults.

AGENTS (system-prompt addons — what you're reading now)
  Project (primary):  <cwd>/.harness/agents/<name>.md
  Project (compat):   <cwd>/.claude/agents/<name>.md
                      <cwd>/.opencode/agents/<name>.md
                      <cwd>/.agents/<name>.md
                      <cwd>/.cursor/agents/<name>.md
  Global:             ~/.harness/agents/<name>.md
                      ~/.claude/agents/<name>.md
                      ~/.config/opencode/agents/<name>.md
                      (global agents only visible when `agent.global` is true)
  Required frontmatter:
    ---
    name: <lowercase-kebab-case>          # must equal the filename stem
    description: <one-line summary>
    icon: "<single emoji>"                # optional, for status bar
    color: "<#hex or named color>"        # optional, for status bar accent
    ---

    <agent body — addon markdown appended to the system prompt when active>
  Activation: `/agent <name>` or `/agent` to open picker. List with `/agent list`.

MCP SERVERS (Model Context Protocol)
  Project file: <cwd>/.mcp.json  (Claude Code compatible — survives in git)
    Schema: {"mcpServers": {"<name>": {"command": "<bin>", "args": [...], "env": {...}}}}
  Global file:  ~/.config/harness-agent/mcp.json  (Harness-managed)
    Schema: {"servers": {"<name>": {"type": "stdio", "command": "<bin>", "args": [...], "env": {...}}},
             "auto_connect": ["<name>"]}
  Other tools' MCP configs Harness aggregates in global mode (READ-ONLY here — never edit):
    Claude Code   ~/.claude.json                   Cursor       ~/.cursor/mcp.json
    OpenCode      ~/.config/opencode/opencode.json Windsurf     ~/.codeium/windsurf/mcp_config.json
                  ~/.config/opencode/mcp.json      VS Code      ~/.vscode/mcp.json
  Activation: `/mcp reload` (or `/mcp connect <name>` for one). Inspect with `/mcp list`.

SKILLS (instruction packs loaded on demand by the LLM)
  Project (primary):  <cwd>/.harness/skills/<name>/SKILL.md
  Project (compat):   <cwd>/.skills/<name>/SKILL.md
                      <cwd>/.opencode/skills/<name>/SKILL.md
                      <cwd>/.claude/skills/<name>/SKILL.md
                      <cwd>/.agents/skills/<name>/SKILL.md
  Global:             ~/.harness/skills/<name>/SKILL.md
                      ~/.config/harness-agent/skills/<name>/SKILL.md
                      ~/.claude/skills/<name>/SKILL.md
                      ~/.config/opencode/skills/<name>/SKILL.md
                      (only visible when settings key `skills.global` is true)
  The directory name MUST equal the `name:` in frontmatter.
  Required SKILL.md template:
    ---
    name: <lowercase-kebab-case>
    description: <one paragraph — when to trigger this skill (1–1024 chars)>
    ---

    <skill body in markdown>
  Activation: `/skill refresh`. Inspect with `/skill list` or `/skill` to open picker.

PLAYBOOK
  1. Confirm scope from the user's wording. Default to project. Ask only if truly ambiguous.
  2. read_file the target file first. Preserve every existing entry — merge, never clobber.
  3. Write with edit_file (surgical merge) or write_file (new file) using the exact schema above.
  4. Report what changed and the one-line activation command (`/mcp reload`, `/skill refresh`,
     `/agent refresh`, `/settings reload`). If the user has $EDITOR set, they can also use
     `/settings edit`.
