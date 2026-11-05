/** Workspace projects in the sidebar (the saved list from jarvis/storage/workspace.py).
 *
 *   PROJECTS                         [📁+] [⚙] [⏻] [+ Open folder]
 *   ▎● API          ⌥1   ⎇ main · Working 2m          ⋯
 *   ▎○ Web client   ⌥2   ⎇ feat/orders                ⋯
 *   ▎◌ Infra        ⌥3   Stopped · click to start     ⋯    ← click = start + switch
 *   ── Other running ──
 *    ○ scratch                                   ☆
 *
 * Until a second project is added the section looks exactly as it always did
 * (projects.js draws today's list); with none yet, a small card suggests
 * adding the folder you are in.
 *
 * This module owns the data (`GET /api/workspace`, refreshed on the `workspace`
 * and `projects` events, after every change and on `jarvis:project-switched`),
 * the rows, the add / change calls, ⌥1…⌥9, the ⌘K items and — for a project
 * that is not running — the optimistic switch: `POST /api/workspace/open`, a
 * "Starting…" strip painted from the project's `last_state`, the existing launch
 * flow (`fetchLaunch`), then the ordinary `switchProject`. A message sent from
 * the composer meanwhile is held here and goes out once the switch is done.
 *
 * Workspace calls are host-level (like `/api/projects/*`): they describe every
 * project, not the one on screen, so they are asked of the server the page was
 * loaded from and do not use BASE.
 *
 * Events: `jarvis:workspace-changed` (document; the data changed),
 * `jarvis:open-terminal-in` (window; detail { projectId, instanceId, path, name }
 * — the terminal panel opens a shell in that project).
 */
import { $, escapeHtml, showToast, readToken, isMac } from './utils.js';
import { icon } from './icons.js';
import { store } from './store.js';
import { fetchLaunch } from './api.js';
import { switchProject, refreshProjects, currentProjectId, findProject } from './projects.js';
import { openFolders } from './folders.js';
import { openProjectMenu, openWorkspaceSettings, confirmRemove, initWsMenu, openCreateGroup } from './wsmenu.js';

const API = '/api/workspace';
const POLL_MS = 400;
const MIGRATED_KEY = 'jarvis-workspace-migrated';
const OLD_ORDER_KEY = 'jarvis-project-order';
const HINT_KEY = 'jarvis-ws-hint-dismissed';
const FOLD_KEY = 'jarvis-ws-group-folded';   // ids of groups whose member list is collapsed
const SLOT_KEYS = isMac ? '⌥' : 'Alt+';

let ws = null;          // the last GET /api/workspace
let sig = '';
let timer = 0;
let loading = false;
let again = false;
let booting = null;     // { id, name, launchId, cancelled, background } while a stopped project starts under the page
let held = [];          // messages sent while it boots
let bootTitle = null;
let arriving = false;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const strip = (p) => String(p || '').replace(/\/+$/, '');
const samePath = (a, b) => !!a && !!b && strip(a) === strip(b);

// ─── Reading the data ─────────────────────────────────────────────────────

const rowsOf = () => ws?.projects || [];
/** Workspace projects in the server's order (a copy). */
export const workspaceRows = () => [...rowsOf()];
/** The list becomes a Workspace with two projects; before that the sidebar looks as it always did. */
export const workspaceActive = () => rowsOf().length >= 2;
/** Master groups (virtual main projects) and the plain projects they gather. */
export const isGroupRow = (r) => r?.kind === 'group';
export const workspaceGroups = () => rowsOf().filter(isGroupRow);
export const workspacePlainRows = () => rowsOf().filter((r) => !isGroupRow(r));
/** A group needs two projects to gather: the "New master group" affordances wait for them. */
export const canCreateGroup = () => workspacePlainRows().length >= 2;
export const workspaceReady = () => !!ws;
export const workspaceSettings = () => ({ ...(ws?.settings || {}) });
export const workspaceProject = (id) => rowsOf().find((r) => r.id === id) || null;
export const inWorkspacePath = (path) => rowsOf().some((r) => samePath(r.path, path));
/** The workspace project whose Jarvis is on screen. */
export const workspaceCurrentRow = () => {
  const cur = currentProjectId();
  return rowsOf().find((r) => r.instances.some((i) => i.id === cur)) || null;
};
/** Ids of every running Jarvis that belongs to a workspace project. */
export const workspaceInstanceIds = () => new Set(rowsOf().flatMap((r) => r.instances.map((i) => i.id)));
export const workspaceMax = () => ws?.max_projects || 40;

function computeSig() {
  return JSON.stringify([
    rowsOf().map((r) => [r.id, r.name, r.color, r.status, r.branch, r.slot, r.instance_id, r.links, r.link_write, r.startup,
      r.kind, r.members, r.master_session, (r.masters || []).map((m) => [m.id, m.name]),
      r.last_state?.session_title, r.instances.map((i) => [i.id, i.busy, i.needs_approval, i.session_title, i.model, i.terminals])]),
    (ws?.ad_hoc || []).map((a) => a.id),
    booting?.id || '',
    held.length,
  ]);
}

// ─── Talking to the server ────────────────────────────────────────────────

async function wsJson(path, method = 'GET', payload) {
  const token = readToken();
  const opts = { method, headers: token ? { Authorization: `Bearer ${token}` } : {}, cache: 'no-store' };
  if (payload !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(payload);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => null);
  if (!res.ok && !(data && typeof data === 'object' && 'ok' in data)) {
    const err = new Error(data?.error || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

/**
 * One change (`add`, `update`, `remove`, `reorder`, `open`, `stop`, `settings`, `import_order`).
 * Never throws: the answer always has `ok`. A refusal for a remote visit
 * (`code: 'remote'`) is shown as the server's own message; other errors are too,
 * unless `quiet` (the caller words them).
 */
export async function wsPost(op, body = {}, { quiet = false } = {}) {
  let res;
  try {
    res = await wsJson(`${API}/${op}`, 'POST', body);
  } catch (err) {
    res = { ok: false, code: 'network', error: err?.status === 401 ? 'This link has expired.' : 'Jarvis is not reachable right now.' };
  }
  if (!res || typeof res !== 'object') res = { ok: false, code: 'error', error: 'Unexpected answer from Jarvis.' };
  if (res.ok === false && res.error && (res.code === 'remote' || !quiet)) showToast(res.error, true);
  if (op !== 'open') refreshWorkspace({ now: true });
  return res;
}

/** Git repos directly inside `path` → `{ ok, repos: [{ path, name, display, in_workspace }] }`. */
export async function scanRepos(path) {
  try {
    return await wsJson(`${API}/scan?path=${encodeURIComponent(path || '')}`);
  } catch {
    return { ok: false, repos: [] };
  }
}

/** Add folders → `{ ok, added: [project], errors: [{ path, code, error }] }`. */
export async function addToWorkspace(paths, { open = false } = {}) {
  const res = await wsPost('add', { paths, ...(open ? { open: true } : {}) }, { quiet: true });
  if (res.ok === false && !res.errors && res.code !== 'remote' && res.error) showToast(res.error, true);
  return res;
}

/** The folder was moved: add the new place, carry the old project's look and links over, drop the old one. */
export async function relocateProject(oldId, newPath) {
  const old = workspaceProject(oldId);
  if (!old) return { ok: false, error: 'That project is no longer in the workspace.' };
  const before = rowsOf().map((r) => r.id);
  const res = await addToWorkspace([newPath]);
  const proj = res.added?.[0];
  if (!proj) return { ok: false, errors: res.errors, error: res.errors?.[0]?.error || res.error };
  await wsPost('update', {
    id: proj.id, name: old.name, color: old.color, startup: old.startup, link_write: old.link_write,
    links: old.links.filter((x) => x !== old.id),
  }, { quiet: true });
  await wsPost('remove', { id: old.id }, { quiet: true });
  // The new folder takes the old one's place in the list.
  await wsPost('reorder', { order: before.map((id) => (id === old.id ? proj.id : id)) }, { quiet: true });
  return { ok: true, project: proj };
}

// ─── Refreshing ───────────────────────────────────────────────────────────

/** Re-read the workspace (soon; `now` skips the short wait that merges a burst of events). */
export function refreshWorkspace({ now = false } = {}) {
  clearTimeout(timer);
  timer = setTimeout(load, now ? 0 : 150);
}

/** Re-read it and resolve when the page has the answer. */
export async function loadWorkspace() {
  clearTimeout(timer);
  await load();
}

async function load() {
  if (loading) {
    again = true;
    return;
  }
  loading = true;
  try {
    const data = await wsJson(API);
    if (data?.ok) apply(data);
  } catch {
    // An older Jarvis without /api/workspace, or a blip: the list stays as it is.
  } finally {
    loading = false;
    if (again) {
      again = false;
      refreshWorkspace();
    }
  }
}

function changed() {
  sig = computeSig();
  document.dispatchEvent(new Event('jarvis:workspace-changed'));
}

function apply(data) {
  ws = data;
  if (computeSig() !== sig) changed();
  renderChrome();
  migrateOrder();
}

/** `workspace` event from the shown project's server: workspace.json changed (here or in another Jarvis). */
export function handleWorkspaceEvent(data) {
  if (!ws || data?.revision === undefined || data.revision !== ws.revision) refreshWorkspace();
}

/** The drag order the old localStorage list held → the server, once. */
async function migrateOrder() {
  if (!ws) return;
  try {
    if (localStorage.getItem(MIGRATED_KEY)) return;
  } catch {
    return;
  }
  let keys = [];
  try {
    const raw = JSON.parse(localStorage.getItem(OLD_ORDER_KEY) || '[]');
    keys = Array.isArray(raw) ? raw.filter((k) => typeof k === 'string' && k.startsWith('/')) : [];
  } catch { /* unreadable: nothing to carry over */ }
  if (keys.length && rowsOf().length) {
    let res;
    try {
      res = await wsJson(`${API}/import_order`, 'POST', { paths: keys });
    } catch {
      return; // try again next time
    }
    if (!res?.ok) return; // refused for a remote visit: from this computer it still works
    refreshWorkspace({ now: true });
  }
  try { localStorage.setItem(MIGRATED_KEY, '1'); } catch { /* private mode */ }
}

/** Drag / keyboard reorder of the workspace rows (reorder.js zone `ws`). */
export function reorderWorkspace(ids) {
  if (!ws) return;
  const byId = new Map(rowsOf().map((r) => [r.id, r]));
  const placed = ids.filter((id) => byId.has(id));
  const rest = rowsOf().filter((r) => !placed.includes(r.id));
  ws.projects = [...placed.map((id) => byId.get(id)), ...rest].map((r, i) => ({ ...r, slot: i + 1 }));
  changed();
  wsPost('reorder', { order: ws.projects.map((r) => r.id) }).then((res) => { if (!res.ok) loadWorkspace(); });
}

// ─── Rows ─────────────────────────────────────────────────────────────────

const slotHint = (row) => (row.slot >= 1 && row.slot <= 9 ? `<kbd class="ws-slot" title="${SLOT_KEYS}${row.slot} switches here">${SLOT_KEYS}${row.slot}</kbd>` : '');

/** The Jarvis that stands for a project: one that needs you, a busy one, else the best. */
function pickInstance(row) {
  const pool = row.instances.map((i) => findProject(i.id) || { ...i, project: row.name, cwd: row.path });
  return pool.find((p) => p.needs_approval) || pool.find((p) => p.busy)
    || pool.find((p) => p.id === row.instance_id) || pool[0] || null;
}

/**
 * The workspace rows as HTML. `ctx` is projects.js's own toolkit so the state
 * indicators stay identical: { shown, status(p), busyFor(p), elapsed(s), hrefFor(id) }.
 */
export function workspaceRowsHtml(ctx) {
  // A project that belongs to a master group is listed inside that group only — never twice.
  const inGroup = new Set(rowsOf().filter(isGroupRow).flatMap((g) => g.members || []));
  return rowsOf().filter((row) => isGroupRow(row) || !inGroup.has(row.id)).map((row) => `<div class="pj-group pj-ws" data-key="${escapeHtml(row.id)}" data-zone="ws">${rowHtml(row, ctx)}</div>`).join('');
}

function folded() {
  try { return new Set(JSON.parse(localStorage.getItem(FOLD_KEY) || '[]')); } catch { return new Set(); }
}

function toggleFold(id, btn) {
  const set = folded();
  const fold = !set.has(id);
  if (fold) set.add(id);
  else set.delete(id);
  try { localStorage.setItem(FOLD_KEY, JSON.stringify([...set])); } catch { /* private mode */ }
  btn.setAttribute('aria-expanded', String(!fold));
  btn.closest('.ws-members')?.classList.toggle('is-folded', fold);
}

/** The collapsible member list under a group row: each member is a full project row, nested. */
function membersHtml(row, ctx) {
  const members = (row.members || []).map((id) => workspaceProject(id)).filter(Boolean);
  if (!members.length) return '';
  const fold = folded().has(row.id);
  const list = members.map((m) => `<div class="ws-nested" data-member-of="${escapeHtml(row.id)}">${rowHtml(m, ctx, { nested: true })}</div>`).join('');
  return `<div class="ws-members${fold ? ' is-folded' : ''}" style="--ws-color:${escapeHtml(row.color)}">
    <button type="button" class="ws-members-toggle" data-ws-fold="${escapeHtml(row.id)}" aria-expanded="${!fold}">${icon('chevron-down')}<span>${members.length} ${members.length === 1 ? 'project' : 'projects'}</span></button>
    <div class="ws-member-list" role="list">${list}</div>
  </div>`;
}

function metaFor(row, inst, st, secs, ctx) {
  const bits = [];
  if (isGroupRow(row)) {
    const n = (row.members || []).length;
    bits.push(`<span class="ws-master">Master · ${n} ${n === 1 ? 'project' : 'projects'}</span>`);
  }
  if (row.status === 'running') {
    if (st.text) {
      const time = secs != null ? ` <span class="pr-time" data-id="${escapeHtml(inst.id)}">${ctx.elapsed(secs)}</span>` : '';
      bits.push(`<span class="pr-state">${st.text}${time}</span>`);
    }
  } else if (row.status === 'starting') bits.push('<span class="pr-state">Starting…</span>');
  else if (row.status === 'missing') bits.push('<span class="pr-state">Folder not found</span>');
  else bits.push('<span class="pr-state">Stopped</span>');
  if (row.branch) bits.push(`<span class="ws-branch" title="Branch">${icon('git-branch')}<span class="ws-branch-name">${escapeHtml(row.branch)}</span></span>`);
  const sub = isGroupRow(row) ? (row.status === 'stopped' ? 'click to open' : '')
    : row.status === 'running' ? (inst?.session_title || inst?.model || 'New chat')
      : row.status === 'stopped' ? (row.last_state?.session_title || 'click to start') : '';
  if (sub) bits.push(`<span class="pr-sub">${escapeHtml(sub)}</span>`);
  return bits.join('<span class="pr-sep" aria-hidden="true">·</span>');
}

function rowHtml(row, ctx, { nested = false } = {}) {
  const running = row.status === 'running';
  const inst = running ? pickInstance(row) : null;
  const st = inst ? ctx.status(inst) : { cls: '', text: '' };
  const isBoot = booting?.id === row.id && !booting.cancelled;
  const state = running ? st.cls : `is-${isBoot ? 'starting' : row.status}`;
  const active = running ? row.instances.some((i) => i.id === ctx.shown) : isBoot;
  const secs = inst?.busy ? ctx.busyFor(inst) : null;
  const href = running && inst ? ctx.hrefFor(inst.id) : '#';
  const hint = running ? row.display : row.status === 'missing' ? `${row.display} — not found` : `${row.display} — click to start`;
  const chats = row.instances.length > 1 ? `<span class="pr-n" title="${row.instances.length} chats running here">${row.instances.length}</span>` : '';
  const group = isGroupRow(row);
  const globe = group ? `<span class="ws-globe" aria-hidden="true">${icon('globe')}</span>` : '';
  const masters = row.masters || [];
  const badge = !group && !nested && masters.length
    ? `<span class="ws-gbadge" role="img" aria-label="In master group ${escapeHtml(masters.map((m) => m.name).join(', '))}" title="Master group: ${escapeHtml(masters.map((m) => m.name).join(', '))}">${icon('globe')}</span>`
    : '';
  const missing = row.status === 'missing'
    ? `<span class="ws-fix"><button type="button" class="ws-act" data-ws-act="locate" data-ws-id="${escapeHtml(row.id)}">Locate…</button><button type="button" class="ws-act is-danger" data-ws-act="remove" data-ws-id="${escapeHtml(row.id)}">Remove</button></span>`
    : '';
  return `
    <div class="project-item ws-item has-more${group ? ' is-group' : ''}" role="listitem" style="--ws-color:${escapeHtml(row.color)}">
      <a class="project-row ws-row ${state}${active ? ' is-active' : ''}" href="${escapeHtml(href)}" data-ws="${escapeHtml(row.id)}"
         data-id="${running && inst ? escapeHtml(inst.id) : ''}" ${active ? 'aria-current="page"' : ''} title="${escapeHtml(hint)}">
        <span class="pr-ind" aria-hidden="true">${st.cls === 'is-ask' ? icon('shield') : row.status === 'missing' ? icon('triangle-alert') : ''}</span>
        <span class="pr-body">
          <span class="pr-title">${globe}<span class="pr-name">${escapeHtml(row.name)}</span>${badge}${chats}${slotHint(row)}</span>
          <span class="pr-meta">${metaFor(row, inst, st, secs, ctx)}</span>
        </span>
      </a>
      <button type="button" class="ws-more" data-ws-menu="${escapeHtml(row.id)}" aria-haspopup="menu" aria-label="Actions for ${escapeHtml(row.name)}" title="${group ? 'Master chat, members, colour, start / stop…' : 'Rename, colour, links, start / stop…'}">${icon('ellipsis')}</button>${missing}
    </div>${group ? membersHtml(row, ctx) : ''}`;
}

/** The divider above running folders that are not in the workspace. */
export const otherRunningHtml = () => `<div class="ws-divider" role="presentation" title="Folders running now that are not in your workspace — ☆ adds one"><span>Other running</span></div>`;

/** The ☆ button on a running folder that is not in the workspace. */
export function adoptButtonHtml(cwd) {
  if (!workspaceActive() || !cwd) return '';
  return `<button type="button" class="pr-add" data-ws-add="${escapeHtml(cwd)}" aria-label="Add to workspace" title="☆ Add to workspace">☆</button>`;
}

/** Clicks inside the list. `true` = handled here (projects.js does nothing more). */
export function handleWsClick(e) {
  const more = e.target.closest('[data-ws-menu]');
  if (more) {
    e.preventDefault();
    const row = workspaceProject(more.dataset.wsMenu);
    if (row) openProjectMenu(more, row);
    return true;
  }
  const fold = e.target.closest('[data-ws-fold]');
  if (fold) {
    e.preventDefault();
    toggleFold(fold.dataset.wsFold, fold);
    return true;
  }
  const add = e.target.closest('[data-ws-add]');
  if (add) {
    e.preventDefault();
    adoptFolder(add.dataset.wsAdd);
    return true;
  }
  const act = e.target.closest('[data-ws-act]');
  if (act) {
    e.preventDefault();
    const row = workspaceProject(act.dataset.wsId);
    if (!row) return true;
    if (act.dataset.wsAct === 'locate') openFolders({ workspace: true, locate: { id: row.id, name: row.name } });
    else confirmRemove(row);
    return true;
  }
  const rowEl = e.target.closest('.project-row[data-ws]');
  if (!rowEl) return false;
  const plain = !(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0);
  if (!plain && rowEl.dataset.id) return false; // ⌘-click on a running project: the browser opens it in a tab
  e.preventDefault();
  if (plain) openWorkspaceProject(rowEl.dataset.ws);
  return true;
}

/** Right-click / long-press on a row: the same menu as ⋯. */
export function handleWsContextMenu(e) {
  const rowEl = e.target.closest('.project-row[data-ws]');
  const row = rowEl && workspaceProject(rowEl.dataset.ws);
  if (!row) return false;
  e.preventDefault();
  openProjectMenu(rowEl, row, { x: e.clientX, y: e.clientY });
  return true;
}

async function adoptFolder(cwd) {
  const res = await addToWorkspace([cwd]);
  const proj = res.added?.[0];
  if (proj) showToast(`${proj.name} added to your workspace`);
  else if (res.errors?.[0]) showToast(res.errors[0].error, true);
}

// ─── Switching ────────────────────────────────────────────────────────────

/**
 * Show a workspace project: switch to it when it runs, otherwise start it and
 * switch once it is up (the page paints from `last_state` meanwhile).
 * Resolves true when the project is on screen (or, `background`, started).
 */
export async function openWorkspaceProject(id, { background = false } = {}) {
  const row = workspaceProject(id);
  if (!row) return false;
  document.body.classList.remove('side-open');
  if (row.status === 'missing') {
    showToast(`${row.name}’s folder isn’t there any more — Locate… or Remove it`, true);
    return false;
  }
  if (row.status === 'running') {
    if (background) return true;
    const inst = pickInstance(row);
    if (booting) cancelBoot({ toast: false });
    if (inst && !row.instances.some((i) => i.id === currentProjectId())) await switchProject(inst.id);
    return true;
  }
  if (booting?.id === id) return false;
  if (row.status === 'starting') {
    showToast(`${row.name} is still starting…`);
    return false;
  }
  if (booting && !background) cancelBoot({ toast: false });
  const res = await wsPost('open', { id });
  if (!res.ok) return false;
  const launch = res.launch || {};
  if (res.reused || (launch.status === 'ready' && (res.instance_id || launch.instance_id))) {
    await refreshProjects();
    loadWorkspace();
    if (!background) await switchProject(res.instance_id || launch.instance_id);
    return true;
  }
  const mine = { id, name: row.name, launchId: launch.id, background, cancelled: false };
  if (!background) {
    booting = mine;
    paintBoot(row);
    changed();
  } else showToast(`Starting ${row.name}…`);
  refreshWorkspace({ now: true });
  return follow(mine);
}

async function follow(mine) {
  let misses = 0;
  for (;;) {
    await sleep(POLL_MS);
    let launch = null;
    try {
      launch = (await fetchLaunch(mine.launchId))?.launch || null;
    } catch {
      launch = null;
    }
    if (!launch) {
      misses += 1;
      if (misses > 20) return fail(mine, 'Lost track of the new Jarvis. Check the Projects list — it may still be starting.');
      continue;
    }
    misses = 0;
    if (launch.status === 'failed') return fail(mine, launch.error || 'Something went wrong.');
    if (launch.status === 'ready') return arrive(mine, launch);
  }
}

async function arrive(mine, launch) {
  await refreshProjects(); // switchProject only knows running Jarvises the list has
  loadWorkspace();
  if (mine.cancelled || mine.background) {
    showToast(`${mine.name} is ready — it’s under Projects`);
    return true;
  }
  arriving = true;
  try {
    await switchProject(launch.instance_id);
  } finally {
    arriving = false;
  }
  // A switch already under way only queues this one: wait until it is really on screen.
  for (let i = 0; i < 40 && currentProjectId() !== launch.instance_id; i += 1) await sleep(100);
  const there = currentProjectId() === launch.instance_id;
  if (booting === mine) booting = null;
  unpaintBoot(false);
  changed();
  if (there) await flushHeld();
  else restoreHeld();
  return there;
}

function fail(mine, message) {
  if (mine.cancelled || mine.background) {
    showToast(`Couldn’t start ${mine.name}: ${message}`, true);
    return false;
  }
  if (booting === mine) booting = null;
  unpaintBoot(true);
  restoreHeld();
  changed();
  showToast(`Couldn’t open ${mine.name}: ${message}`, true);
  return false;
}

/** Stop waiting for the project under the page (it keeps starting; a toast says when it is up). */
export function cancelBoot({ toast = true } = {}) {
  const mine = booting;
  if (!mine) return;
  mine.cancelled = true;
  booting = null;
  unpaintBoot(true);
  restoreHeld();
  changed();
  if (toast) showToast(`${mine.name} keeps starting in the background`);
}

// ─── The "Starting…" strip and the message held meanwhile ────────────────

/** True while a project is starting under the page: the composer holds what is sent. */
export const isBooting = () => !!booting && !booting.cancelled;

/** composer.js: a message sent during boot waits here. */
export function holdPrompt(text) {
  held.push(String(text));
  drawBoot();
  showToast(held.length === 1
    ? `Queued — it is sent to ${booting?.name || 'the project'} as soon as it is up`
    : `${held.length} messages queued until ${booting?.name || 'the project'} is up`);
}

function paintBoot(row) {
  const bar = $('ws-boot');
  if (!bar) return;
  bar.style.setProperty('--ws-color', row.color || '');
  bar.hidden = false;
  document.body.classList.add('is-booting');
  const h1 = $('session-title');
  if (h1) {
    bootTitle = { prev: h1.textContent, set: row.last_state?.session_title || 'New session' };
    h1.textContent = bootTitle.set;
  }
  bar.dataset.row = row.id;
  drawBoot();
}

function drawBoot() {
  const bar = $('ws-boot');
  const row = booting && workspaceProject(booting.id);
  if (!bar || !booting) return;
  const last = row?.last_state || {};
  const where = [row?.branch || last.branch ? `⎇ ${row?.branch || last.branch}` : '', last.session_title || ''].filter(Boolean).join(' · ');
  bar.innerHTML = `
    <span class="spinner" aria-hidden="true"></span>
    <span class="wb-text"><strong>Starting ${escapeHtml(booting.name)}…</strong><span>${escapeHtml(where || 'You can already type — your message is sent when it is ready')}${held.length ? ` · ${held.length} queued` : ''}</span></span>
    <button type="button" class="btn btn-quiet wb-cancel" data-ws-boot-cancel>Cancel</button>`;
}

function unpaintBoot(restore) {
  const bar = $('ws-boot');
  if (bar) {
    bar.hidden = true;
    bar.innerHTML = '';
  }
  document.body.classList.remove('is-booting');
  const h1 = $('session-title');
  if (restore && bootTitle && h1 && h1.textContent === bootTitle.set) h1.textContent = bootTitle.prev;
  bootTitle = null;
}

async function flushHeld() {
  const list = held;
  held = [];
  if (!list.length) return;
  const { submitPrompt } = await import('./composer.js');
  for (const text of list) await submitPrompt(text);
}

function restoreHeld() {
  const text = held.join('\n\n');
  held = [];
  if (!text) return;
  import('./composer.js').then((m) => m.fillPrompt(text));
  showToast('Your message is back in the box — the project did not open');
}

// ─── Keyboard + ⌘K ────────────────────────────────────────────────────────

/** ⌥1…⌥9: workspace project N. `false` = nothing there (the key then does what it always did). */
export function switchToSlot(n) {
  if (!workspaceActive()) return false;
  const row = rowsOf()[n - 1];
  if (!row) return false;
  openWorkspaceProject(row.id);
  return true;
}

export function openTerminalIn(row) {
  window.dispatchEvent(new CustomEvent('jarvis:open-terminal-in', {
    detail: { projectId: row.id, instanceId: row.instance_id || null, path: row.path, name: row.name },
  }));
}

async function newChatIn(id) {
  if (!(await openWorkspaceProject(id))) return;
  const { newChat } = await import('./actions.js');
  await newChat();
}

function describeRow(row) {
  if (row.status === 'running') {
    const inst = pickInstance(row);
    if (inst?.needs_approval) return 'Needs approval';
    if (inst?.busy) return 'Working';
    return inst?.session_title || row.display;
  }
  if (row.status === 'missing') return 'Folder not found';
  if (row.status === 'starting') return 'Starting…';
  return 'Not running — starts in a few seconds';
}

/** ⌘K items: add / settings always; switch / new chat / terminal per project once it is a Workspace. */
export function workspaceItems() {
  const items = [];
  const cur = currentProjectId();
  if (workspaceActive()) {
    for (const row of rowsOf()) {
      const here = row.instances.some((i) => i.id === cur);
      const group = isGroupRow(row);
      if (!here) {
        items.push({
          group: 'Projects', label: group ? `Open master: ${row.name}` : `Switch to ${row.name}`,
          desc: group ? `Master chat · ${(row.members || []).length} projects` : describeRow(row), icon: group ? 'globe' : 'folder',
          keys: `project switch workspace master group hub ${row.display} ${row.branch || ''}`, hint: row.slot <= 9 ? `${SLOT_KEYS}${row.slot}` : '',
          run: () => openWorkspaceProject(row.id),
        });
      }
      if (row.status === 'missing') continue; // nothing to open there
      if (!group) items.push({
        group: 'Projects', label: `New chat in ${row.name}`, desc: 'Switch there and start a fresh chat', icon: 'plus', searchOnly: true,
        keys: `project new chat ${row.display}`, run: () => newChatIn(row.id),
      });
      items.push({
        group: 'Projects', label: `Terminal in ${row.name}`, desc: 'Open a shell in this project’s folder', icon: 'terminal', searchOnly: true,
        keys: `project terminal shell ${row.display}`, run: () => openTerminalIn(row),
      });
    }
  }
  if (canCreateGroup()) {
    items.push({
      group: 'Projects', label: 'New master group…', desc: 'One shared chat over several projects', icon: 'globe',
      keys: 'project master group new create hub main root folder', run: () => openCreateGroup(),
    });
  }
  items.push({
    group: 'Projects', label: 'Add project…', desc: 'Pick folders to keep in your workspace', icon: 'folder-plus',
    keys: 'project add workspace folder repo repos', run: () => openFolders({ workspace: true }),
  });
  const cwd = store.session?.cwd;
  if (cwd && !inWorkspacePath(cwd)) {
    items.push({
      group: 'Projects', label: 'Add this folder to workspace', desc: store.session.cwd_display || cwd, icon: 'plus',
      keys: 'project add workspace current folder', run: () => adoptFolder(cwd),
    });
  }
  items.push({
    group: 'Projects', label: 'Workspace settings', desc: 'Warm projects, idle stop, links, delegation', icon: 'settings',
    keys: 'project workspace settings warm idle link delegation', run: () => openWorkspaceSettings(),
  });
  return items;
}

// ─── Header buttons + the first-run card ─────────────────────────────────

function renderChrome() {
  const gear = $('ws-settings');
  if (gear) gear.hidden = !workspaceActive();
  const can = canCreateGroup();
  const gbtn = $('ws-group');
  if (gbtn) gbtn.hidden = !can;
  const cta = $('ws-groupcta');
  if (cta) cta.hidden = !can || workspaceGroups().length > 0;   // the first time: a labelled invitation
  renderHint();
}

function renderHint() {
  const box = $('ws-hint');
  if (!box) return;
  let dismissed = false;
  try { dismissed = !!localStorage.getItem(HINT_KEY); } catch { /* private mode */ }
  const cwd = store.session?.cwd || '';
  const show = ws && !rowsOf().length && !dismissed;
  box.hidden = !show;
  if (!show) {
    box.innerHTML = '';
    return;
  }
  const name = (store.session?.cwd_display || cwd).split('/').filter(Boolean).pop() || 'this folder';
  box.innerHTML = `
    <div class="ws-hint-card">
      <button type="button" class="icon-btn ws-hint-x" data-ws-hint="dismiss" aria-label="Hide this tip" title="Hide">${icon('x')}</button>
      <strong>Working on several repos? Add them here</strong>
      <p>Keep them in the sidebar, switch with ${SLOT_KEYS}1–9, even when they aren’t running.</p>
      <div class="ws-hint-act">
        ${cwd ? `<button type="button" class="btn btn-primary" data-ws-hint="add-current" title="${escapeHtml(cwd)}">${icon('plus')}<span>Add ${escapeHtml(name)}</span></button>` : ''}
        <button type="button" class="btn" data-ws-hint="choose">${icon('folder-plus')}<span>Choose folders…</span></button>
      </div>
    </div>`;
}

function onHintClick(e) {
  const btn = e.target.closest('[data-ws-hint]');
  if (!btn) return;
  const act = btn.dataset.wsHint;
  if (act === 'dismiss') {
    try { localStorage.setItem(HINT_KEY, '1'); } catch { /* private mode */ }
    renderHint();
  } else if (act === 'add-current') adoptFolder(store.session?.cwd);
  else if (act === 'choose') openFolders({ workspace: true });
}

/**
 * The markup this feature adds to the page, built here so index.html stays as it is:
 * the ⚙ / folder-plus buttons in the Projects header, the first-run card, the
 * "Starting…" strip and the dialog shell wsmenu.js fills.
 */
function ensureChrome() {
  const make = (tag, props, html = '') => Object.assign(document.createElement(tag), props, { innerHTML: html });
  const actions = document.querySelector('#projects-sec .proj-actions');
  if (actions && !$('ws-add')) {
    const gear = make('button', {
      className: 'icon-btn ws-hbtn', id: 'ws-settings', type: 'button', hidden: true, title: 'Workspace settings',
    }, icon('settings'));
    gear.setAttribute('aria-label', 'Workspace settings');
    const add = make('button', {
      className: 'icon-btn ws-hbtn', id: 'ws-add', type: 'button', title: 'Add folders to your workspace \u2014 they stay in this list, running or not',
    }, icon('folder-plus'));
    add.setAttribute('aria-label', 'Add folders to your workspace');
    const grp = make('button', {
      className: 'icon-btn ws-hbtn', id: 'ws-group', type: 'button', hidden: true, title: 'New master group — one shared chat over several projects',
    }, icon('globe'));
    grp.setAttribute('aria-label', 'New master group');
    actions.insertBefore(gear, actions.firstChild);
    actions.insertBefore(grp, gear.nextSibling);
    actions.insertBefore(add, $('open-folder'));
  }
  const list = $('projects-list');
  if (list && !$('ws-groupcta')) {
    const cta = make('button', { className: 'ws-groupcta', id: 'ws-groupcta', type: 'button', hidden: true }, `${icon('globe')}<span>New master group…</span>`);
    cta.title = 'One shared chat over several projects';
    list.after(cta);
  }
  if (list && !$('ws-hint')) list.after(make('div', { className: 'ws-hint', id: 'ws-hint', hidden: true }));
  const banner = $('proj-banner');
  if (banner && !$('ws-boot')) {
    const bar = make('div', { className: 'ws-boot', id: 'ws-boot', hidden: true });
    bar.setAttribute('role', 'status');
    banner.before(bar);
  }
  if (!$('wsdlg')) {
    const modal = make('div', { className: 'modal', id: 'wsdlg', hidden: true }, `
      <div class="modal-card glass-strong small-card dlg-card ws-card" id="wsdlg-card" role="dialog" aria-modal="true" aria-labelledby="wsdlg-title">
        <div class="picker-head">
          <button class="icon-btn dlg-back" type="button" hidden aria-label="Back">${icon('arrow-left')}</button>
          <span class="picker-icon" id="wsdlg-ic">${icon('folder')}</span>
          <div class="picker-titles">
            <h2 id="wsdlg-title">Workspace</h2>
            <p id="wsdlg-sub"></p>
          </div>
          <button class="icon-btn" data-close="wsdlg" type="button" aria-label="Close">${icon('x')}</button>
        </div>
        <div class="dlg-body ws-dlg-body" id="wsdlg-body"></div>
        <div class="dlg-foot ws-dlg-foot" id="wsdlg-foot"></div>
      </div>`);
    document.body.appendChild(modal);
  }
}

export function initWsProjects() {
  ensureChrome();
  initWsMenu();
  $('ws-add')?.addEventListener('click', () => {
    document.body.classList.remove('side-open');
    openFolders({ workspace: true });
  });
  $('ws-settings')?.addEventListener('click', () => openWorkspaceSettings());
  for (const id of ['ws-group', 'ws-groupcta']) {
    $(id)?.addEventListener('click', () => {
      document.body.classList.remove('side-open');
      openCreateGroup();
    });
  }
  $('ws-hint')?.addEventListener('click', onHintClick);
  $('ws-boot')?.addEventListener('click', (e) => {
    if (e.target.closest('[data-ws-boot-cancel]')) cancelBoot();
  });
  // Another way into another project (a row, ⌘K, Back) ends the wait: the new Jarvis keeps starting.
  document.addEventListener('jarvis:project-switched', () => {
    if (booting && !arriving) cancelBoot({ toast: false });
    refreshWorkspace();
  });
  // The folder on screen decides what the first-run card suggests.
  document.addEventListener('jarvis:projects-changed', renderHint);
  loadWorkspace();
}
