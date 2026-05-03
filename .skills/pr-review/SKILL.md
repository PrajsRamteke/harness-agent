---
name: pr-review
description: |
  Review a GitHub pull request for the Jarvis Python agent framework. 
  Use this whenever the user asks to "review a PR", "check my PR", "code review", "review changes", 
  "review this diff", "review this PR", or links to a GitHub pull request — even if they don't explicitly 
  ask for a structured review. Also trigger when the user says "is this PR good?" or "any issues with these changes?"
---

# PR Review Skill

Perform a structured, in-depth code review of pull requests in the **Jarvis agent framework** (`/Users/prajwal/Desktop/harness`).

## Checklist — inspect every PR against these criteria

### 1. Type Safety & Annotations
- Are all function parameters and return values properly type-annotated? (`from __future__ import annotations` preferred)
- Are `Optional`, `Dict`, `List`, `Any` etc. imported from `typing` only where needed?
- Are `None` checks used correctly (no `if x is None` vs `if not x` confusion)?
- When adding new state globals to `jarvis/state.py`, is the type annotation present?

### 2. Error Handling
- Are `try/except` blocks specific about exception types? (never bare `except:`)
- Do tool functions return error strings (`f"ERROR: {e}"`) consistently?
- Are file/db/network operations wrapped in error handling?
- Does the code avoid silently swallowing exceptions (no `except: pass`)?
- For new tool implementations: does the handler return a string (not raise) on failure?

### 3. Tool Registration Correctness
- If adding a new tool function, is it:
  1. Implemented in the right subpackage (`jarvis/tools/`)
  2. Added to `jarvis/tools/__init__.py` in both `FUNC` dict and tool group list
  3. Added to the correct schema file (`schemas_core.py`, `schemas_mac.py`, etc.)
  4. Regex-trigger registered in `tools/router.py:select_tools()` if specialized
  5. Listed in `_SERIAL_TOOLS` in `repl/render.py` if it's stateful (shell, file edits, UI, MCP)
- If modifying an existing tool: does the schema (`input_schema`) exactly match the handler's parameters?

### 4. State Management (jarvis/state.py)
- Are new mutable globals added via `jarvis.state.<name> = ...` pattern?
- Are globals initialized at module level with a sensible default?
- Is the variable name prefixed with `_` if internal-only?
- No circular imports introduced — `state.py` should only import from `constants.py`, not from tools/repl packages.

### 5. Async & Concurrency
- If adding async code: is the event loop managed correctly? (Textual runs its own loop)
- Are thread-safe patterns used for shared state? (no concurrent mutations to `state.messages` etc.)
- If adding to `_SERIAL_TOOLS`, document *why* it must be serial.

### 6. Security
- API keys, tokens, secrets: are they read from env / keychain / `~/.config/`, never hardcoded?
- User-provided strings used in shell commands: are they sanitized / escaped? (prefer `shlex.quote()`)
- URLs / file paths from users: validated before use?
- No sensitive data in logs or debug output?

### 7. Code Style & Conventions
- Follows existing code style: double quotes (`"`) for strings, f-strings over `.format()` or `%`, snake_case functions/variables, PascalCase classes.
- Imports grouped: stdlib → third-party → local (with blank line separators).
- Line length reasonable (project doesn't enforce a strict limit, but avoid >120 chars).
- Docstrings on public functions (triple double-quotes).
- `from __future__ import annotations` at the top of new modules.

### 8. Architectural Fit
- Does the change follow the existing package layout? (no putting tool logic in `repl/`, no TUI code in `tools/`)
- Does it respect the data flow: user input → tools/router.py → tool execution → state update → render?
- For new features: does it belong in an existing subpackage, or warrants a new one?
- Does it integrate with existing patterns (session persistence, context trim, halluncination guard, streaming)?

### 9. Testing & Regressions
- Does the change need manual testing via `jarvis`? (no automated test suite exists)
- Are there any regressions to existing workflows (auth, streaming, tool execution, session load)?
- Does it break the TUI startup (`agent.py` → `jarvis/tui/app.py`)?

### 10. Dependencies
- Any new pip dependencies? If so, are they added to `pyproject.toml` and `requirements.txt`?
- Prefer stdlib solutions where possible to keep the dependency footprint small.

## Output Format

Provide the review in this structure:

```
## PR Review: <branch/PR title>

### Summary (1-2 sentences)
Overall assessment of the change.

### ✅ What's Good
- Bullet list of things done right.

### ⚠️ Issues Found
| # | Severity | File | Line(s) | Issue |
|---|----------|------|---------|-------|
| 1 | high/med/low | `path/file.py` | 42-45 | Description |
| … | | | | |

### 🔧 Suggestions (optional)
- Optional improvements that aren't blockers.

### 📋 Checklist Summary
- [x] Type safety
- [ ] Error handling (1 medium issue)
- [x] Tool registration
- [x] State management
- … (one line per checklist item)
```

Severity levels:
- **high**: definite bug, crash, security issue, or silent data loss
- **medium**: incorrect behavior in edge cases, missing error handling, type unsafety that could cause runtime errors
- **low**: style, readability, naming, minor redundancy

## Process

1. Fetch the PR diff (via `fetch_url` with the PR's `.diff` or `https://api.github.com/repos/<org>/<repo>/pulls/<number>/files`)
2. If the PR has a description or related issues, review those too for context
3. For each file in the diff, run through the checklist above
4. Use `search_code` to check existing patterns when evaluating architectural fit
5. Output the structured review
