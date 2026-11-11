# Web Settings: one two-pane modal

Status: planned. Scope: the **web** Settings dialog only (`jarvis/web/static`). The terminal
`/settings` modal is not touched.

## Goal

Today `#settings` is one long scrolling list in a 680px card, and Appearance and Keyboard
shortcuts are separate dialogs that Settings closes itself to open. Make Settings one modal
in the T3 Code / Antigravity shape:

```
┌──────────────────────────────────────────────────────────────────────┐
│ [⚙] Settings · Changes apply right away …                        [×] │
├───────────────────┬──────────────────────────────────────────────────┤
│ [🔍 Search      ] │  Chat                                            │
│                   │  ┌────────────────────────────────────────────┐  │
│  Model            │  │ Show thinking                          [●] │  │
│ ▌Chat             │  │ Auto-approve commands                  [ ] │  │
│  Agents & tools   │  │ Pinned context                 Edit ›  [●] │  │
│  Parallel agents  │  └────────────────────────────────────────────┘  │
│  Terminal         │                                                  │
│  Web remote       │                                                  │
│  ───────────      │                                                  │
│  Appearance       │                                                  │
│  Keyboard         │                                                  │
├───────────────────┴──────────────────────────────────────────────────┤
│ 📄 Saved for every chat · ~/.config/harness-agent/settings.json  esc │
└──────────────────────────────────────────────────────────────────────┘
```

Clicking a section on the left shows it on the right. Nothing closes, nothing stacks.

## Sections (left nav, in this order)

| id | Label | Icon | Content | Saved |
|---|---|---|---|---|
| `model` | Model | `cpu` | today's client "Model" group: Model, Provider, Agent, Thinking effort, Commit text model | session |
| `chat` | Chat | from server | server section `chat` | settings.json |
| `sources` | Agents, skills & tools | from server | server section `sources` | settings.json |
| `subagents` | Parallel agents | from server | server section `subagents` | settings.json |
| `terminal` | Terminal | from server | server section `terminal` | settings.json |
| `web` | Web remote | from server | server section `web` | settings.json |
| — divider, small label "This browser" — | | | | |
| `appearance` | Appearance | `palette` | the whole Appearance dialog body, embedded | this browser |
| `keymap` | Keyboard shortcuts | `keyboard` | the whole keymap editor, embedded; nav shows a count badge when `customizedCount() > 0` | this browser |

Server section ids/titles keep coming from `settings_api.SECTIONS`; the page must render a
server section it doesn't know (a new one added later) without a code change.

Providers, MCP, Skills, Commands, Memory, Pin, Local models stay their own dialogs. The
existing row links ("Manage" on MCP servers, "Edit" on Pinned context, "Change" on
Model / Provider / Agent) keep working exactly as now (they close Settings and open the picker).

## Backend (`jarvis/web/settings_api.py`) — small

- Add `SECTION_ICONS = {"chat": "message-circle", "sources": "sparkles", "subagents": "users",
  "terminal": "terminal", "web": "globe"}` and put `"icon"` on each section in `public()`.
  Leave `SECTIONS` a list of 2-tuples (a test unpacks it). Every icon name must exist in
  `static/js/icons.js` — check, and pick another existing one if it doesn't (do not regenerate
  icons.js unless truly needed).
- Nothing else changes: same routes, same `apply()`.

## Markup (`static/index.html`)

`#settings` card becomes:

```
.modal#settings
  .modal-card.settings-card.dlg-card  (keep role/aria, keep .ext-card)
    .picker-head                      (unchanged: back button hidden, icon, title, close)
    .set-shell
      nav.set-nav#settings-nav  aria-label="Settings sections"
        #settings-bar                 (search — toolbar() from dialog.js, as now)
        #settings-nav-list            (rendered by settings.js)
      .set-main
        .set-pane#settings-body       (rows for model + server sections, and search results)
        .set-pane#appearance  hidden  (Appearance blocks moved here verbatim)
        .set-pane#keymap      hidden  (#keymap-bar, #keymap-body, #keymap-foot moved here)
    .dlg-foot#settings-foot
    .set-menu#set-menu                (unchanged, still a child of the card)
```

- **Appearance**: delete the `<div class="modal" id="appearance">` dialog. Move its
  `.settings-body` content (every `.setting-block`, ids untouched) into the pane. The pane
  element itself carries `id="appearance"` so `#appearance [data-pref]` (theme.js) and
  `#appearance .setting-block` (overlays.css) keep matching. Drop its `.picker-head`.
- **Keymap**: delete the `<div class="modal" id="keymap">` dialog. The pane carries
  `id="keymap"` and holds `#keymap-bar`, `#keymap-body`, `#keymap-foot` (the keymap editor
  finds everything by those ids and by `$('keymap')`).
- `#shortcuts` (the read-only cheat sheet, `?`) stays a separate small dialog.
- Keep `<div class="set-menu" id="set-menu" role="listbox" hidden>` byte-for-byte (a test
  asserts that exact string).

## Page logic

### `settings.js`
- State: `active` (section id), `query`. `openSettings(section)` — a string picks that
  section; anything else (no arg, a click event — `$('settings-btn')` passes one) reopens the
  last one, remembered in `localStorage['jarvis-settings-section']` (try/catch), default `model`.
  Unknown id → `model`.
- `export function showSection(id)`: sets `active`, paints the nav (`aria-current="page"` on
  the active button), shows exactly one pane (`hidden` on the others), scrolls the pane to top,
  closes an open `.set-menu`, repaints the footer, and tells the embedded module to paint
  (`paintAppearance()` / the keymap's render — see below).
- Nav rows are real `<button type="button" class="set-nav-item" data-section="…">` with icon +
  label (+ badge). ↑/↓ move between nav items (and from the search ↓ goes to the first one),
  Enter/click selects. Render the nav with `patch()` so focus survives.
- **Search**: while `query` is non-empty the right side always shows `#settings-body` with the
  flat cross-section results (today's `bodyHtml()` behaviour: groups titled by section), and
  the nav has no `aria-current`. Include two jump rows in the results, matched by keywords
  (the current "This browser" rows and their `keys`): "Appearance → Open" and "Keyboard
  shortcuts → Open", whose button calls `showSection(...)` and clears the search. Clearing the
  search returns to `active`. With no query, `#settings-body` shows **only** the active
  section's group (no `section()` header needed — a pane title `<h3 class="set-pane-title">`
  with the section label instead).
- Footer per section: model → "Applies to this chat"; server sections → the current
  "Saved for every chat · `<file>`" note; appearance → "Saved in this browser"; keymap → the
  footer is hidden (`#settings-foot` `hidden`) because the keymap pane has its own
  `#keymap-foot` (count + Reset all).
- The `open` click action no longer handles `appearance` / `keymap` by closing Settings: both
  become `showSection`.
- `closeMenu()` / `openMenu()` measure against `$('settings-body')` — still correct (it is the
  scrolling pane). Make sure the pane, not the card, is the only scroller on the right.
- Esc order stays modal.js's: an open menu → a typed search is cleared → dialog closes. Do not
  add a `setView` sub-view for sections.

### `theme.js`
- `openAppearance()` must no longer call `openModal('appearance')`. To avoid an import cycle
  (settings.js already imports theme.js), add `export function setAppearanceOpener(fn)` and
  have `openAppearance()` do `paintAppearance(); opener?.()`. `initSettings` registers
  `() => openSettings('appearance')`. `paintAppearance` must be exported (settings.js calls it
  when the section is shown) if it isn't already.
- `#appearance [data-pref]` selectors keep working (the pane has that id).

### `keymap_editor.js`
- The editor no longer owns a modal. Replace `openModal(ID…)` / `closeModal(ID)` /
  `isModalOpen(ID)` / `topModal() !== ID` with "Settings is open on the keymap section":
  add `export function setKeymapHost({ open, isShown })`; `openKeymap()` calls `open()`
  (= `openSettings('keymap')`), and `onRecKey` / `render` guard on `isShown()`
  (= `isModalOpen('settings') && active === 'keymap'`). Drop the `back` option and the
  `setView` call (there is nothing to go back to — the nav is always visible). Export a
  `paintKeymap()` (reset `query`/`rec`, clear its search field, render) for `showSection`.
- Recording must stop (`rec = null`) when Settings closes or another section is shown.
- Esc while recording still only cancels the recording (the capture-phase handler runs before
  modal.js — keep `stopImmediatePropagation`).
- `window.confirm` for Reset all stays.

### Call sites to repoint (all keep their public function names, so most need no edit)
- `palette.js` `action: 'appearance'` / `'keymap'`, `composer.js` (`/theme`), `sidebar.js`
  (appearance button), `shortcuts.js` (`app.settings`, the cheat sheet's Customize) — verify
  each lands on the right section; the cheat sheet's Customize must close `#shortcuts` first
  as it does now.
- `pickers.js` calls `openSettings()` in three places (after picking the commit-text model
  etc.) — those should return to the section the user was on (last-section memory covers it).
- Search every `isModalOpen('appearance')`, `isModalOpen('keymap')`, `closeModal('appearance')`,
  `data-close="appearance"`, `data-close="keymap"` and fix it.

## CSS

`css/settings.css` (tokens only — `tokens.css` is the source of truth; no new colours, no
shadows beyond the modal's own, radii `--r-ctl` / `--r-card`, heights `--h-sm/md`):

- `.settings-card.dlg-card { max-width: 940px; height: min(680px, 86vh); }`
- `.set-shell { flex: 1; min-height: 0; display: grid; grid-template-columns: 216px minmax(0, 1fr); }`
- `.set-nav`: its own vertical scroller, `border-right: 1px solid var(--line)`, padding 8px;
  the search on top; `.set-nav-item` = 30px row, `--r-ctl`, icon 15px in `--text-3`, label
  13px; hover `--card-hover`; active = `--surface-2`/`--card-hover` fill + `--text` + icon in
  `--accent` (a filled row, not an underline — devtool look). `.set-nav-sep` hairline +
  11px uppercase label like `.dlg-section h3`. Badge like the count badges elsewhere.
- `.set-main { min-width: 0; min-height: 0; display: flex; flex-direction: column; }`,
  `.set-pane { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; }`,
  `.set-pane[hidden] { display: none; }`. `.set-pane-title` 15px/600 with a 12px dim
  one-line description under it.
- The keymap pane is a column: bar (fixed) / body (scrolls) / foot (fixed) — so for it the
  pane itself must not scroll; `#keymap-body` does.
- Remove now-dead rules: `#appearance .modal-card`, `#appearance .picker-head` in
  `overlays.css`; `.keymap-card.dlg-card` in `keymap.css` (check what else `.keymap-card`
  scoped and re-scope it to `#keymap`).
- **Phones / narrow (≤ 720px)**: `.set-shell` becomes one column; the nav turns into a
  horizontally scrolling chip strip under the search (same idea as `.ss-chips` in
  `sessions.css`: no scrollbar, the active chip scrolled into view), the divider becomes a thin
  vertical gap, labels stay (icon + label chips). The card goes full-height as other dialogs
  do on phones. Touch targets ≥ 36px.
- Frosted glass (`data-surface="glass"`), light / dim themes and every accent must still work:
  use only tokens.

Section switches don't animate the card size (fixed size); a 120ms opacity fade-in of the
pane is fine and must respect reduced motion.

## Tests

Update, don't delete:
- `tests/test_web_settings.py`: `test_public_lists_every_setting…` — also assert each section
  has an `icon`. `test_settings_opens_in_the_browser_with_a_gear` — still passes; extend with
  the new shape: `id="settings-nav"`, the `#appearance` and `#keymap` panes live **inside**
  `#settings` (their index is between the `#settings` modal's start and the next
  `<div class="modal"`), there is no `<div class="modal" id="appearance"` or
  `<div class="modal" id="keymap"` any more, and `settings.js` exports `showSection`.
- `tests/test_web_keymap.py` (line ~204–210 asserts on index.html + settings.js) and
  `tests/test_web_appearance.py`: adjust to the new markup.
- `tests/test_web_remote.py` fails on any icon, asset or import that doesn't exist — run it.
- Run: `python -m pytest tests/test_web_settings.py tests/test_web_keymap.py
  tests/test_web_appearance.py tests/test_web_remote.py -q`, then the whole `tests/test_web_*.py`
  set, then `python -m pytest tests/ -q` once at the end. Report real numbers.

## Visual check (required)

Recipe: `~/.claude/projects/-Users-prajwal-Desktop-harness-buddy-harness/memory/web-remote-visual-testing.md`
(sandbox web server with a scratch HOME + headless Chrome over CDP) and
`headless-chrome-dock-icons.md` (use Playwright's `chrome-headless-shell`, never full Chrome).
Any Chrome launch must pass
`--use-mock-keychain --user-data-dir="<scratchpad>/chrome-headless-profile" --no-first-run --no-default-browser-check`.

Screenshots to take and look at (1x device scale), saved in the scratchpad:
1. Desktop 1440×900, dark: Model, Chat, Terminal (open the theme menu), Appearance, Keyboard.
2. Desktop: a search for "theme" (cross-section results + the Appearance jump row).
3. Desktop light theme: one section.
4. Phone 390×844: Chat section and Appearance (chip strip visible, nothing overflows sideways).

Check in the page, not just by eye: no horizontal scroll, the right pane scrolls while the nav
stays, the choice menu is not clipped, Esc closes from every section, ⌘K → Appearance and
⌘K → "Customize keyboard shortcuts" land on their sections, changing a setting still saves
(`POST /api/settings/all` answers ok), recording a key in the keymap pane works and Esc there
cancels only the recording.

## Docs

Update the "Settings on the web" bullet (and the "Appearance dialog is the shared dialog
size" / "Appearance extras" lines that mention the separate dialog) in the repo `CLAUDE.md`
to describe the two-pane modal — a few sentences, same terse style.

## Out of scope

- Providers / MCP / Skills / Commands / Memory as panes (possible follow-up: nav links).
- The terminal (`tui/settings_modal.py`).
- Any new setting, any change to what a setting does.
- Restyling rows, switches, steppers or the Appearance controls themselves.
