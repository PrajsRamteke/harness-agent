---
name: clickup
description: >-
  CRM Sprint ClickUp workflow for Habuild. Use this skill whenever the user mentions a ClickUp task ID, task link, ticket number, or asks you to work on something in ClickUp — even if they don't explicitly say "use the skill". This skill enforces confirmation before any action on a ticket.
---

# ClickUp — Internal Tools / CRM Sprint Workflow

## Who you are

- **User**: Prajwal Ramteke, prajwal.ramteke@habuild.in
- **ClickUp User ID**: 188634247, initials "PR"
- **Role**: Software Developer — CRM, Internal Tools, crm-fe-remix (frontend)
- **Pod**: Internal Tools / CRM Pod (NOT App Pod / Platform / Unified Notifications)

## Current Sprint

- **Sprint**: Internal Tool Sprint 7 (29/4/26 - 12/5/26)
- **List**: `Internal Tools Sprint 7 (29/4/26 - 12/5/26)` — List ID: `901614280410`
- **Board view**: https://app.clickup.com/9002212861/v/b/8c95qfx-153116
- **Previous sprint**: `Internal Tools Sprint 6 (15/4/26 - 28/4/26)` — List ID: `901614218431`
- **Next sprint**: `Internal Tools Sprint 8 (13/5/26 - 26/5/26)` — List ID: `901614484315`
- **Backlog**: `[Internal Tools] Backlog` — List ID: `901613486023`
- **EPICs**: `[Internal Tools] EPICs` — List ID: `901613486013`

## Workspace location

- **Space**: Product Engineering (Space ID: `90163602549`)
- **Folder**: Internal Tools (Folder ID: `90168482660`)

## Pods that belong to Prajwal

- **Internal Tools / CRM Pod** — THIS IS YOUR POD. Always check here first.
- DO NOT check any of these — they belong to OTHER PODS:
  - App Pod (Balamurugan, Pooja, Ayush, Himanshu)
  - Platform (Mantosh, Tejas)
  - Frontend Web (Nancy, Onkar, Jayesh)
  - Unified Notifications
  - Links Service
  - Payments
  - Support / OPS
  - Marketing
  - Projects/Programs

## How to find Prajwal's tickets

When the user asks "what tickets do I have" or similar:

1. FIRST check `[Internal Tools] Backlog` (List ID: `901613486023`) — this has ALL your active/backlog tickets since Sprint 7 list is empty.
2. THEN check `Internal Tools Sprint 7` (List ID: `901614280410`) — if it has tasks, check those too.
3. Filter tasks where assignees include "Prajwal Ramteke" or user ID 188634247.
4. Do NOT check App Pod, Platform, Unified Notifications, Frontend Web, or any other pod/folder.

## Prajwal's active tickets (as of May 2026)

| Ticket | Status | Due | Priority |
|--------|--------|-----|----------|
| FE Tech Task \| Chat Message optimisation (86d2xm2mt) | in dev | May 12 | normal |
| FE \| Add Buttons & Media Visibility for Template Messages in Chat Screen (86d2whfpb) | in dev | May 6 (overdue) | normal |
| FE \| Show DOB field on Member Info Panel General tab (86d2v26p6) | ready for deployment | May 5 (overdue) | normal |

## Ticket conventions

| Field | Convention |
|---|---|
| ready for dev | Ready to be picked up and worked on |
| in dev | Currently being worked on |
| in pr review | Pull request submitted, awaiting review |
| ready for deployment | Code merged, ready to deploy |
| in testing | Currently in QA/testing |
| deployed on prod | Shipped to production |
| blocked | Blocked by dependency or external issue |
| dev completed | Development finished, pending review/deploy |

## Workflow rules

1. **Always get confirmation first** — before starting any work on a ticket, summarize what you understand and ask for a go-ahead. Do not write code, move statuses, or make changes until the user explicitly says "yes" or "go ahead".

2. When the user gives you a ClickUp task (ID, link, or name), resolve it using the ClickUp MCP tools to get task details, then present:
   - Task name & ID
   - Current status
   - Description / acceptance criteria
   - Priority & due date (if set)
   - Any relevant custom fields

3. After confirmation, work on the ticket. Update ClickUp status as appropriate when work starts (e.g. "In Progress").

4. When done, update the task status and summarize what was completed.

## MCP tools available
- `mcp__clickup__get_task` — get task details by ID or name
- `mcp__clickup__update_task` — update status, description, priority, etc.
- `mcp__clickup__get_tasks` — list tasks in a list with optional filters
- `mcp__clickup__get_workspace_hierarchy` — get the full workspace tree
