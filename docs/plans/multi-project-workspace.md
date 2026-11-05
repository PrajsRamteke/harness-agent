# Multi-Project Workspace — Planning Doc

Status: **Draft / not started** · Owner: TBD · Date: 2026-11-04
Scope: web remote (primary), TUI (parity), agent tools, terminal panel.

---

## 1. Summary

Let a user curate a **Workspace**: a saved, ordered list of projects (repos/folders)
they work on — e.g. `api`, `web-client`, `infra`. From anywhere in Jarvis they can:

1. **Switch** to any workspace project in under ~200 ms (warm) or a few seconds (cold),
   with the chat, terminals, file browser and git state of that project.
2. **Let the agent reach across projects** — in a chat rooted in `web-client`,
   "update the client to match the new `/orders` endpoint in api" works without
   `allow_outside_project` hacks, because `api` is a *linked* project.
3. **Open terminals in any project** from one panel, jump between them by keyboard,
   and see every project's shells in one place.
4. **Delegate across projects** (stretch): the agent in `web-client` hands a task to
   the Jarvis running in `api` and waits for its result — real multi-repo work, each
   repo with its own context, agent, skills and approvals.

**Is it feasible?** Yes. Most of the hard infrastructure already exists (per-folder
Jarvis processes, a symmetric hub that forwards `/p/<id>/api/*`, a headless launcher,
in-place project switching, per-instance PTY terminals, a stop-all/restore snapshot).
What's missing is a **persistent, user-owned project list**, **multi-root path scoping
for tools**, a **cross-project terminal view**, and **warm-start / idle management**.

---

## 2. What exists today (building blocks)

| Capability | Where | Notes |
|---|---|---|
| One Jarvis process = one project root | `jarvis/constants/paths.py:24` `set_cwd` | `CWD` is a process-wide global patched into every module. **Key constraint** — one process can't be "in" two projects. |
| Tool scope guard | `jarvis/path_resolve.py:62` `project_scope_error` | Single root (`CWD`); escape hatch `allow_outside_project=true` (`tools/files.py`, `tools/dirs.py`). |
| Instance registry | `jarvis/web/registry.py` | `~/.config/harness-agent/instances/<pid>.json` — cwd, project, session, busy, needs_approval, heartbeat. |
| Hub / forwarding | `jarvis/web/hub.py` | `/p/<id>/api/*` → that instance's loopback port with its token. No elected hub. |
| In-place switching | `static/js/projects.js:387` `switchProject` | Prefetch on hover (8 s TTL), `switchTransport`, history push. |
| Start a project from the browser | `jarvis/web/launcher.py:168` `open_project` | Headless `jarvis --headless`; `MAX_RUNNING = 16`; `LAUNCH_TIMEOUT = 45`. |
| Stop all / Restore | `launcher.py:371-470`, `static/js/workspace.js` | Snapshot `workspace_snapshot.json` of what was running — *not* a curated list. |
| Folder picker + recent dirs | `jarvis/web/fs_api.py`, `static/js/folders.js` | `recent_dirs.json`, `MAX_RECENT = 12`, project markers. |
| Project order | `projects.js:37` `ORDER_KEY` | **localStorage only** → per device, not shared with TUI/phone. |
| Terminal panel | `jarvis/web/terminal_api.py:477` `open_terminal(cwd=)` | Shells belong to an instance; re-attach on `jarvis:project-switched`. `open_terminal` already accepts any `cwd`. |
| Sessions by project | `storage/session_library.py`, `static/js/sessions.js` | Sessions carry `cwd`; `openSession` routes a chat to its folder's Jarvis. |
| `/cd` | `commands/files_shell.py:18`, `fs_api.change_cwd` | Moves the *whole process* to another root. |

**Gaps this plan fills**

- No first-class "my projects" list that survives restarts, shared across devices and TUI.
- Not-running projects are invisible in the Projects list (only running + recent).
- Agent tools are hard-scoped to one root; cross-repo work needs per-call escapes.
- Terminals are siloed per instance; no "all terminals" view or quick project jump.
- Cold start on every switch to a stopped project; no idle reaping of headless ones.

---

## 3. Goals / Non-goals

**Goals**
- G1 Add / remove / rename / reorder / color projects; persisted server-side.
- G2 One-keystroke switch (`⌥1…⌥9`, ⌘K, sidebar) with p95 < 300 ms for warm projects.
- G3 Agent can read/search any linked project by name; writes gated by policy.
- G4 Terminal panel can open a shell in any workspace project and switch between
  projects' shells without switching the chat.
- G5 TUI parity for list / add / switch / link.
- G6 Zero regression for a user with one project (feature invisible until used).

**Non-goals (v1)**
- Running one process with several roots (rewriting the global `CWD`). Rejected — see §11.
- Cross-machine workspaces / cloud sync.
- Monorepo sub-package detection (could be a later "suggest projects" feature).
- Shared chat transcripts across projects.

---

## 4. User stories

1. *As a dev on 3 repos*, I add `api`, `web`, `infra` once; every time I open Jarvis
   they are in the sidebar, in my order, with colors — running or not.
2. I press `⌥2` → I'm in `web` with its last chat; its terminals are there.
3. In `web`, I type "the `OrderDTO` in @api:src/dto/order.ts changed — update our
   types and the fetch call". Jarvis reads `api` directly and edits `web`.
4. I open the terminal, pick **+ ▾ → infra**, run `terraform plan` there while my
   chat stays in `web`. Tab badge shows `infra` in its color.
5. `⌥⇧T` opens **All terminals**: every shell in every project, grouped, with a
   running/idle indicator; click one to attach.
6. (Stretch) "Ask api to add a `GET /orders/:id/status` endpoint and tell me the
   response shape" — the `api` Jarvis does it under its own approvals; my chat gets
   the summary and continues.
7. On my phone (web remote), the same workspace appears; I switch projects there.

---

## 5. Concepts & data model

### 5.1 Workspace file

`~/.config/harness-agent/workspace.json` (dir 700, file 600, atomic write via tmp +
`os.replace`, like `fs_api.remember_dir`). Global — one workspace per user in v1.

```jsonc
{
  "version": 1,
  "projects": [
    {
      "id": "api",                       // slug, unique, stable; used in `api:path` refs (not in /p routes — see 5.3)
      "path": "/Users/me/code/api",      // realpath at add time
      "name": "API",                     // display name (defaults to folder name)
      "color": "#7c5cff",                // from accent presets
      "icon": "server",                  // optional, from static/icons
      "startup": "lazy",                 // lazy | warm | pinned  (see §7)
      "links": ["web"],                  // projects this one's agent may read (see §8)
      "link_write": "ask",               // ask | allow | deny  for writes into linked projects
      "added_at": 1793846400
    }
  ],
  "order": ["api", "web", "infra"],
  "settings": {
    "warm_limit": 3,                     // max headless projects kept running idle
    "idle_stop_minutes": 30,             // reap idle headless projects (never terminal-started ones)
    "link_all": false                    // shortcut: every project links every other
  }
}
```

Rules:
- `id` = slugified folder name, de-duplicated (`api`, `api-2`); editable; regex
  `^[a-z0-9][a-z0-9-_]{0,31}$`.
- Refuse `/`, `~` itself, and system folders as projects (same check as `fs_api.resolve_dir`
  + a denylist) — linking `~` would make scoping meaningless.
- Missing path → project shown as *Missing* with "Locate…" / "Remove"; never auto-deleted.
- Per-project overrides (agent, model) stay in `<project>/.harness/settings.json` — the
  workspace file only holds *workspace* concerns.

### 5.2 New module: `jarvis/storage/workspace.py` (UI-free)

```
load() -> Workspace                      # cached, mtime-checked (like mcp/config.get_config)
save(ws) / add(path, name=None) / remove(id) / update(id, **fields) / reorder(ids)
find_by_path(path) -> Project | None     # realpath match
current() -> Project | None              # project containing CWD
roots_for(project_id) -> list[Root]      # own root + linked roots, each {id, path, writable}
resolve_ref("api:src/x.ts") -> Path      # project-qualified path
add_listener(fn)                         # web StateWatcher pushes `workspace` events
```

Concurrency: several Jarvis processes read/write it. Writes take an advisory
`fcntl.flock` on `workspace.lock`; readers rely on atomic replace + mtime cache.

### 5.3 Relationship to existing concepts

| Concept | Today | After |
|---|---|---|
| Instance (`registry`) | a running process | unchanged; each workspace project maps to 0..n instances |
| Recent dirs | auto list, 12 | stays; "Add to workspace" action on recent rows |
| Project order (localStorage) | per device | migrated into `workspace.order`; localStorage kept only for non-workspace (ad-hoc) folders |
| Stop-all snapshot | what was running | stays; *Restore* also offered per workspace project |
| `/p/<instance-id>` routes | instance id | unchanged (instances, not projects, are routable) |

---

## 6. UX design

### 6.1 Web — sidebar *Projects* section

```
PROJECTS                         [+]  [⚙]
 ● API          ⌥1   main  ·  Working 2m
 ○ Web client   ⌥2   feat/orders
 ◌ Infra        ⌥3   (stopped)            ← click = start + switch
 ─ Other running ─
 ○ ~/scratch/tmp                   web ⏻
```

- Workspace projects always listed (running or not), in `workspace.order`; running-but-
  not-in-workspace folders below a divider with "☆ Add to workspace".
- Row state reuses existing indicators (busy ring, approval shield, New reply dot) plus
  **◌ stopped** and **◐ starting**.
- Color stripe on the left; branch from the instance's git state when running.
- Drag to reorder (reuse `reorder.js`) → `POST /api/workspace/reorder`.
- Row ⋯ menu: Rename · Color · Links… · Open terminal here · Start/Stop · Reveal in
  Finder · Remove from workspace.

### 6.2 Adding projects

- `[+]` → existing folder picker (`folders.js`) in **"Add to workspace"** mode: footer
  primary = *Add N folders*; multi-select with checkboxes; marker chips help pick repos.
- Bulk add: type a parent path (`~/code`) → "Add all 7 git repos inside" option.
- First-run empty state in the Projects section: "Working on several repos? Add them
  here" + the current folder pre-suggested.
- ⌘K: *Add project…*, *Add this folder to workspace*.

### 6.3 Switching

- `⌥1…⌥9` → workspace project N (free in `static/js/shortcuts.js:63-74`, which only maps
  `⌥`+letters). `⌥0` → project switcher (⌘K filtered to projects).
- ⌘K items: `Switch to API`, `Terminal in Infra`, `New chat in Web`.
- Stopped project → optimistic switch: shell UI paints immediately from cached
  `last_state` (title, last chat name, branch) with a *Starting…* strip; the composer
  is **usable during boot** — a typed prompt is queued and sent when the instance is
  `ready` (reuse `prompt_queue`). Cold start = existing launch flow (`fetchLaunch` poll).
- Warm project → existing `switchProject` (prefetched on hover).

### 6.4 Terminal panel

```
[ api ▸ zsh ] [ api ▸ npm dev ] [ infra ▸ zsh ] [ + ▾ ]            [All ⌥⇧T] [⤢] [–]
```

- **+ ▾** menu: *New shell in <current>* (default, `Ctrl+⇧\``) · one entry per workspace
  project · *Other folder…*.
- Each tab: project color badge + name prefix. Tabs from other projects are shown
  only when "Show all projects' terminals" is on (toggle in panel; default **on** once
  the workspace has ≥2 projects).
- **Where does the shell live?** Two options:
  - (A) In the *current* instance with `cwd=<other project>` — trivial
    (`open_terminal(cwd=)` exists) but the shell dies with this instance and isn't
    visible from the other project.
  - (B) In the *target project's* instance, reached through the hub
    (`/p/<id>/api/terminal/open|poll|input`) — the shell belongs to the project it runs
    in, survives switching, and shows up there too. Requires that project to be running
    (start headless if not).
  - **Recommendation: (B)**, with (A) as fallback for folders not in the workspace.
- **All terminals** view: grid/list of every shell across running workspace instances
  (`GET /api/workspace/terminals` aggregates `terminal/list` from each instance via the
  hub). Shows cwd, last line, running command, idle time.
- Pump: `runPump` today polls one instance. Extend to one `poll` request **per instance
  with open tabs** (each is a single held request already — `read_many`), capped at
  ~6 concurrent; tabs of stopped instances show *Project stopped — Start*.
- Terminal-over-tunnel policy is per instance (`bridge.terminal_over_tunnel`); the
  aggregated view must respect each target's flag (hub already forwards
  `X-Forwarded-For`, so the target refuses correctly).

### 6.5 Chat-level cross-project UX

- Composer `@` menu: after `@`, typing a project id + `:` lists that project's files
  (`@api:src/…`). Plain `@` lists current-project files first, then a *Linked projects*
  group.
- A **context chip** under the composer: `Linked: api, infra` (click → Links dialog).
- Tool rows show a project badge when a tool touched a non-current root
  (`edit_file · api › src/dto/order.ts`).
- Changes drawer (`file_changes`) groups diffs by project.

### 6.6 TUI

- `/project` (alias `/workspace`, `/p`):
  - bare → modal list (reuse `modal_chrome.py`): ↑↓, Enter = switch, `a` add (path
    input with completion), `d` remove, `l` links, `r` rename, `J/K` reorder.
  - `/project add [path]`, `/project rm <id>`, `/project link <id> [<id>…]`,
    `/project <id>` switch.
- **Switch in TUI** = `fs_api.change_cwd` (what `/cd` does) on the current process,
  refused while busy, plus resume of that project's most recent chat. (The TUI is one
  process — it can't hold several projects; that's the web's job.) Offer
  "Open in web instead" when the web remote is on.
- Footer shows `api ⌥1` badge; `ctrl+o` opens the project modal (verify free in
  `tui/terminal_keys.py` + bindings before committing to it).

---

## 7. Fast switching: warm pool & lifecycle

Startup modes per project:

| Mode | Behaviour |
|---|---|
| `lazy` (default) | Started on first switch/terminal/delegation; reaped after `idle_stop_minutes`. |
| `warm` | Started in background when any Jarvis web remote starts; reaped like lazy but restarted up to `warm_limit`. |
| `pinned` | Always kept running; never reaped. |

**Reaper** (in `launcher.py`, runs in whichever instance currently holds a
`reaper.lock` flock — avoids electing a hub while preventing N reapers): stops a
headless instance only if all are true — not busy, no pending approval, no open
terminals with a foreground process, no browser attached in the last N minutes, idle
> `idle_stop_minutes`. Never touches terminal-started (non-headless) instances.

**Cold-start budget**: measure current `spawn → ready` (logged by `launcher._watch`).
If > 3 s, profile imports in `jarvis --headless` (likely provider catalogs / MCP
connects); defer MCP connects and catalog fetches until first prompt in headless mode.
Target: cold < 2.5 s p50.

**Memory**: each instance is a full Python process (measure RSS; expect 80–150 MB).
`warm_limit` default 3 keeps it bounded; `MAX_RUNNING = 16` stays as hard cap.

**Cached last state**: registry already stores session title/model; on stop, write
`last_state` (branch, last session id/title, last activity) into the workspace
project entry so stopped rows and optimistic switches have something to paint.

---

## 8. Agent cross-project access (multi-root scoping)

### 8.1 Model

Each running instance still has **one primary root** (`CWD`). The workspace adds
**linked roots**: `workspace.roots_for(current_project)` → `[own (rw), linked… (ro|ask|rw)]`.

### 8.2 Changes

| Area | Change |
|---|---|
| `path_resolve.py` | `project_scope_error(path, tool, *, write=False)` checks all roots from `workspace.roots_for`; returns `None` when inside a linked root and (`not write` or policy allows). New `resolve_project_path("api:src/x")` used by `robust_resolve` when the string matches `^<id>:`. Keep `allow_outside_project` semantics for truly outside paths. |
| `tools/files.py`, `tools/dirs.py`, `tools/search.py`, `tools/context/*`, `read_document`, `prompt_refs.py` | Accept `api:…` paths (via resolver). `search_code`, `glob_files`, `rank_files`, `resolve_context` gain optional `project` param: an id, a list, or `"*"` = all linked. Results prefixed `api:` when not in the primary root. |
| Writes into linked roots | `write_file` / `edit_file` / `multi_edit` pass `write=True`; policy `link_write`: `deny` → error with hint; `ask` → approval prompt (reuse the shell-approval modal pattern) "Edit a file in **api** (linked project)?"; `allow` → proceed. |
| `run_bash` / `run_bg` | New optional `project` param → command runs with that root as cwd (validated against roots). Approval text names the project. |
| Git tools | `git_status/diff/log` gain `project` param. |
| `file_changes.py` / undo / Changes drawer | Store absolute paths + project id; `/undo` works across roots. |
| System prompt (`constants/system_prompt.py:10`) | After `PROJECT ROOT:` add `LINKED PROJECTS: api → /…/api (main) [read-only] · infra → … [ask before edit]` plus one line: "Refer to linked files as `<id>:path`." Only when links exist (zero tokens otherwise). |
| Project context | Linked projects' `CLAUDE.md`/`AGENTS.md` paths listed (lazy, path only — same as today's `project_context_*`). |
| Subagents (`spawn_agents`) | Brief may carry `project` → the agent's tools default `project=` to that root. (In-process, so tools must take roots explicitly rather than rely on `CWD`; covered by the resolver changes.) |
| Skills / agents / commands / MCP | **Not** merged across projects — they stay the primary project's. Explicit non-goal; mention in docs. |

### 8.3 Safety rules

- Links are **directional** and explicit (`web` links `api` ≠ `api` links `web`),
  unless `settings.link_all`.
- Read access to a linked root never includes `.env*`, keychains, or files the redactor
  flags — existing `utils/redact.py` applies to tool output regardless.
- Linked roots are re-validated (realpath, still exists, not `~`/`/`) on every
  workspace reload; symlink escapes checked with `is_within` on resolved paths.
- Over a tunnel (`_may_hand_out_token` false): workspace **mutations** (add / link /
  write policy) are refused — same rule as `POST /api/projects/open`. Reads of the list
  are fine.

---

## 9. Cross-project delegation (stretch — Phase 4)

New agent tool `ask_project(project, task, wait=true, timeout=600)`:

1. Resolve project → running instance (start headless if needed, via launcher).
2. POST the task to that instance through the hub as a **new tab** (`POST /api/tabs
   {op: new}` then send) so it doesn't disturb the user's chat there; tag the tab
   "from web-client".
3. Wait on that instance's state (`busy` false) via its local API; approvals raised
   there surface in the user's UI through the existing `#proj-banner` ("Needs approval
   in api").
4. Return the final assistant message + list of changed files to the caller.

Guardrails: max depth 1 (a delegated task can't delegate), per-call timeout, user
can see and stop the delegated tab, the tool is in `_SERIAL_TOOLS`, off by default
behind setting `workspace.delegation`.

This is the "crazy" part: one prompt drives coordinated changes in several repos,
each with its own context, skills, and approvals.

---

## 10. API surface

Host-level (served by whichever instance the page talks to; like `/api/projects/*`):

| Method | Route | Body / query | Notes |
|---|---|---|---|
| GET | `/api/workspace` | — | projects + order + settings + per-project runtime (`running_ids`, state, `last_state`). |
| POST | `/api/workspace/add` | `{paths: [], names?}` | direct-only; returns added + errors per path (`not_found`, `duplicate`, `refused`). |
| POST | `/api/workspace/update` | `{id, name?, color?, icon?, startup?, links?, link_write?}` | direct-only for `links`/`link_write`. |
| POST | `/api/workspace/remove` | `{id}` | doesn't stop instances, doesn't delete chats. |
| POST | `/api/workspace/reorder` | `{order: []}` | |
| POST | `/api/workspace/open` | `{id, resume?: session_id}` | wraps `launcher.open_project(reuse=True)`. |
| GET | `/api/workspace/terminals` | — | aggregated `terminal/list` across running workspace instances (no tokens). |
| SSE | event `workspace` | — | pushed by `StateWatcher` on file mtime change. |

Project-level: no new routes for terminals (hub forwarding covers
`/p/<id>/api/terminal/*`). `state_fields` gains `workspace_project` (id or null) and
`links`.

---

## 11. Architecture decision: processes, not multi-root process

**Option A — one process, many roots** (make `CWD` per-tab/per-call). Rejected for v1:
`CWD` is patched into dozens of modules (`constants/paths.py:30`), plus `os.getcwd()`
calls in tools, subagents (`subagents/team.py:311`), MCP config (`.mcp.json` per
folder), skills/agents/commands discovery, settings merge, session `cwd`. Converting
all to an explicit context is a large, risky refactor with subtle cross-talk bugs.

**Option B — process per project + workspace layer (chosen).** Reuses registry, hub,
launcher, switcher, terminals. Isolation is free (MCP servers, skills, settings per
project). Cost: memory per process, cold starts → mitigated by warm pool (§7).
Cross-project *agent* access is added narrowly via linked roots (§8) without changing
the primary-root model.

```
             ┌──────────────── browser / phone ────────────────┐
             │ Projects sidebar · ⌥1..9 · Terminal panel (all) │
             └───────────────┬─────────────────────────────────┘
                             │ /api/workspace*  /p/<id>/api/*
        ┌────────────────────▼───────────────────┐
        │ Jarvis (web)  web-client   ← CWD       │──reads──┐
        │  tools: own root rw + linked roots     │         │
        └───────┬───────────────────────┬────────┘         │
         hub.forward              hub.forward              │
        ┌───────▼────────┐      ┌───────▼────────┐  ┌──────▼──────────────────┐
        │ Jarvis api     │      │ Jarvis infra   │  │ ~/.config/harness-agent │
        │ (headless,warm)│      │ (stopped→lazy) │  │  workspace.json (600)   │
        │ PTYs, chats    │      │                │  │  instances/*.json       │
        └────────────────┘      └────────────────┘  └─────────────────────────┘
```

---

## 12. Implementation phases

Each phase is shippable on its own and behind no flag except where noted.

### Phase 1 — Workspace list (foundation) · ~3–4 days
- `jarvis/storage/workspace.py` + tests (load/save/flock/migration/validation).
- Host routes in `handler.py` + `StateWatcher` `workspace` event.
- `projects.js`: render workspace projects (running + stopped), order from server;
  migrate `jarvis-project-order` localStorage → server once (`workspace.migrated` flag).
- Add-to-workspace mode in `folders.js`; row ⋯ menu; colors.
- `⌥1…⌥9` in `shortcuts.js`; ⌘K items in `catalog.js`.
- Stopped-project click → existing launch flow.
- CLAUDE.md section.
- **Exit**: add 3 projects, restart everything, they're back in order on desktop + phone.

### Phase 2 — Fast switching & lifecycle · ~3 days
- `last_state` cache; optimistic switch UI + queued prompt during boot.
- `startup` modes, warm pool on web-remote start, reaper with `reaper.lock`.
- Measure & trim headless cold start (defer MCP/catalogs until first prompt).
- **Exit**: warm switch p95 < 300 ms; cold p50 < 2.5 s; idle headless reaped.

### Phase 3 — Terminals across projects · ~3–4 days
- `+ ▾` project menu; open shells in target instance via hub (option B), fallback A.
- Multi-instance pump in `terminal.js` (one held poll per instance).
- Project badges on tabs; *All terminals* view + `/api/workspace/terminals`.
- **Exit**: shells in 3 projects open at once from one page; switching chats never
  detaches them; tunnel policy honored per instance.

### Phase 4 — Agent linked projects · ~4–5 days
- Resolver (`id:path`), multi-root `project_scope_error`, write policy + approval.
- `project` param on search/glob/rank/resolve_context/git/run_bash.
- System-prompt block; tool-row badges; Changes drawer grouping; undo across roots.
- Composer `@id:` completion; Links dialog; TUI `/project link`.
- **Exit**: story #3 works end-to-end with `link_write=ask`.

### Phase 5 — TUI parity · ~2 days
- `/project` command + modal; footer badge; switch = `change_cwd` + resume.

### Phase 6 (stretch) — Delegation `ask_project` · ~4 days, behind `workspace.delegation`
- Tool + schema, hub calls, approval surfacing, depth/timeout guards.

Rough total: **~3 weeks** for Phases 1–5; +1 week for delegation.

---

## 13. Testing plan

Follow existing patterns (`tests/conftest.py` isolates `HOME`).

| Test file | Covers |
|---|---|
| `tests/test_workspace_store.py` (new) | add/remove/reorder/rename, slug de-dup, refuse `~` and `/`, missing path, concurrent writers (two processes + flock), atomic write, migration from localStorage payload. |
| `tests/test_web_workspace.py` (new) | routes, direct-only refusal over tunnel, `workspace` SSE event, aggregation of terminals across two real servers (pattern from `test_web_hub.py`). |
| `tests/test_web_launch.py` (extend) | warm pool start, reaper rules (busy / approval / terminal / non-headless never reaped), `last_state`. |
| `tests/test_path_resolve.py`, `tests/test_multi_edit_scope.py` (extend) | `id:path` resolution, linked read allowed, linked write per policy, symlink escape refused, unknown id error text. |
| `tests/test_web_terminal.py` (extend) | open in another instance via hub, multi-instance poll. |
| `tests/test_web_hub.py` | existing `BASE` guard must still pass for any new `fetch` in JS. |
| Manual / `scripts/pr_shots.py` | before/after screenshots of sidebar, add dialog, terminal panel (pr-screenshots skill). |

Perf checks: script that times `switchProject` warm/cold and headless `spawn → ready`.

---

## 14. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Memory from many processes | Laptop slowdown | `warm_limit`, reaper, RSS shown in project ⋯ menu. |
| Cold start too slow | "Not quick" feeling | Optimistic UI + queued prompt; defer heavy init in headless. |
| Agent edits wrong repo | Data loss / surprise | Links explicit & directional; default `link_write=ask`; project badges on tool rows; undo across roots. |
| Concurrent workspace writes from N processes | Corrupt file | flock + atomic replace + schema validation with fallback to last good copy (`workspace.json.bak`). |
| Tunnel visitor adds `~` as a project / links secrets | Security | Mutations direct-only; denylist; redaction unchanged. |
| Terminal pump with many instances | Browser connection limits (6/host on HTTP/1.1) | One poll per instance with tabs, cap concurrency, background-tab backoff (existing 45 s rule). |
| Two instances in the same folder | Ambiguous target | Prefer the one with a browser attached / most recent activity; show both in row submenu (already grouped by folder today). |
| Feature clutter for single-project users | UX noise | Section collapses to today's look until a second project is added. |

---

## 15. Success metrics

- ≥ 30% of web-remote users with ≥ 2 workspace projects after 4 weeks (local
  telemetry only if/when it exists — otherwise dogfood feedback).
- Warm switch p95 < 300 ms; cold p50 < 2.5 s.
- Zero reports of edits landing in an unintended repo with default policy.

---

## 16. Open questions (need a decision before Phase 1/4)

1. **Workspace scope**: single global workspace (recommended v1) vs. named workspaces
   ("Work", "Side projects") switchable? Data model allows `workspaces[]` later.
2. **Default link policy**: no links by default (recommended) vs. `link_all` on for
   convenience?
3. **Write policy default**: `ask` (recommended) vs. `deny`.
4. **Terminal ownership**: confirm option B (shell lives in target project's instance).
5. **TUI switch semantics**: `change_cwd` in place (recommended) vs. "open in web".
6. **Delegation**: in scope for this feature or separate follow-up?
7. **Keyboard**: `⌥1…⌥9` for projects — any conflict with users' OS/IME (e.g. ⌥-digit
   characters on non-US layouts; `e.code` handling as done for letters)?

---

## 17. Docs to update when implementing

- `CLAUDE.md`: new *Workspace* section (store, routes, lifecycle, linked roots), update
  *Project switcher*, *Terminal panel*, *Open a folder* paragraphs.
- `README.md`: user-facing "Working on several projects".
- `/help` (`commands/help.py`): `/project`.
