# .harness/

Per-project Harness configuration:

- `agents/<name>.md` — project-local agents (frontmatter + body markdown).
- `skills/<name>/SKILL.md` — instruction packs the LLM auto-invokes when
  their `description:` matches the task.
- `settings.json` — overrides for this project (merged over the global
  `~/.config/harness-agent/settings.json`).

See `/agent` and `/skill` for activation and `~/.harness/` for the
user-global counterpart.
