/** Project switcher: every running Jarvis on this computer, behind this one link.
 *
 *   PROJECTS
 *   ● billing-api    Working… · Refactor invoice model
 *   ● admin-panel    Needs your approval
 *   ● harness        New reply
 *
 * A click switches in place — no page load: the page points its requests at
 * the other project (`setBase`), paints its snapshot (prefetched on hover) and
 * moves the live stream over. Rows are still real links, so ⌘/middle-click
 * opens a project in another tab and Back / Forward walk the switches.
 *
 * The list follows the server's `projects` events (a project opened, closed,
 * got busy, finished, asks for approval) with a slow poll as a fallback. A
 * project that closes leaves the list and its chat shows up in Recent sessions;
 * if it was the one on screen, the page moves to another one. If the terminal
 * this link belongs to closes, the page moves to another running Jarvis.
 */
import { $, escapeHtml, readToken, showToast, BASE, setBase } from './utils.js';
import { fetchProjects, fetchStateAt, switchTransport, stopProjectById } from './api.js';
import { store, patchStore } from './store.js';
import { closeAllModals } from './modal.js';
import { syncPrompts } from './prompts.js';
import { invalidateSnapshot } from './chat.js';
import { resetChanges } from './changes.js';
import { handleCommandsEvent } from './commands.js';
import { handleMcpEvent } from './mcp.js';
import { setProjectItems } from './catalog.js';
import { icon } from './icons.js';
import { openFolders } from './folders.js';

const POLL_MS = 10_000;         // the fallback; `projects` events carry changes as they happen
const POLL_ALONE_MS = 20_000;
const PREFETCH_TTL_MS = 8_000;
const CLOSED_KEY = 'jarvis-closed-projects';

let onSnapshot = () => {};
let hostId = '';        // the server this page was loaded from
let hubLink = '';       // its shareable link (QR / copy link while another project is shown)
let projects = [];
let receivedAt = 0;     // when `projects` arrived (each `busy_for` counts from then)
let directLinks = {};   // id → that Jarvis's own link (only on this computer / LAN)
let timer = 0;
let sig = '';
let switching = null;   // id being switched to
let queued = null;      // a click that came while switching
let lost = false;       // the server this page came from stopped answering
const unseen = new Set();         // finished while you were looking at another project
const wasBusy = new Map();
const goneHandled = new Set();
const prefetched = new Map();     // id → { at, promise }

// ─── Small helpers ────────────────────────────────────────────────────────

const idFromPath = (path) => (path.match(/^\/p\/([A-Za-z0-9_-]+)/) || [])[1] || '';
export const currentProjectId = () => (BASE ? BASE.slice(3) : hostId);
const baseFor = (id) => (id && id !== hostId ? `/p/${id}` : '');
const find = (id) => projects.find((p) => p.id === id);
export const findProject = find;
const nameOf = (p) => p?.project || 'project';

function hrefFor(id) {
  return `${baseFor(id)}/?token=${encodeURIComponent(readToken())}`;
}

/** The link to share for what is on screen: this server's link, pointed at that project. */
export function shareLink() {
  if (!BASE || !hubLink) return store.remoteUrl || location.href;
  try {
    const url = new URL(hubLink);
    url.pathname = `${BASE}/`;
    return url.toString();
  } catch {
    return store.remoteUrl || location.href;
  }
}

/** The running project (other than the one shown) whose current chat is `sid`. */
export function projectForSession(sid) {
  if (sid === null || sid === undefined || sid === '') return null;
  return projects.find((p) => p.id !== currentProjectId() && String(p.session_id) === String(sid)) || null;
}

// ─── Closed projects → Recent sessions ────────────────────────────────────

function readClosed() {
  try {
    const raw = JSON.parse(localStorage.getItem(CLOSED_KEY) || '{}');
    return raw && typeof raw === 'object' ? raw : {};
  } catch {
    return {};
  }
}

/** Where a session in Recent sessions came from, if its project was closed. */
export function closedProjectFor(sid) {
  return readClosed()[String(sid)]?.project || '';
}

function rememberClosed(p) {
  if (p?.session_id === null || p?.session_id === undefined) return;
  const all = readClosed();
  all[String(p.session_id)] = { project: nameOf(p), at: Date.now() };
  // Keep the newest 30.
  const keep = Object.entries(all).sort((a, b) => b[1].at - a[1].at).slice(0, 30);
  try { localStorage.setItem(CLOSED_KEY, JSON.stringify(Object.fromEntries(keep))); } catch { /* private mode */ }
}

const sessionsChanged = () => document.dispatchEvent(new Event('jarvis:sessions-changed'));

// ─── Rendering ────────────────────────────────────────────────────────────

function status(p) {
  if (p.needs_approval) return { cls: 'is-ask', text: 'Needs approval' };
  if (p.busy) return { cls: 'is-busy', text: 'Working' };
  if (unseen.has(p.id)) return { cls: 'is-new', text: 'New reply' };
  return { cls: 'is-idle', text: '' };
}

/** 42 → "42s", 130 → "2m", 3900 → "1h 5m" */
function elapsed(sec) {
  const s = Math.max(0, Math.floor(sec));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

const busyFor = (p) => (p.busy_for == null ? null : p.busy_for + (Date.now() - receivedAt) / 1000);

/** Chats grouped by folder, in list order: [{ key, name, cwd, rows }] */
function groupByFolder(rows) {
  const groups = new Map();
  for (const p of rows) {
    const key = p.cwd || p.project || p.id;
    if (!groups.has(key)) groups.set(key, { key, name: nameOf(p), cwd: p.cwd, rows: [] });
    groups.get(key).rows.push(p);
  }
  return [...groups.values()];
}

/** What a chat is called: its title, plus a number when two read the same → { text, n } */
function chatLabels(rows) {
  const seen = new Map();
  const out = new Map();
  for (const p of rows) {
    const text = p.session_title || 'New chat';
    const n = (seen.get(text) || 0) + 1;
    seen.set(text, n);
    out.set(p.id, { text, n });
  }
  return out;
}

const labelText = ({ text, n }) => (n > 1 ? `${text} (${n})` : text);

/** "harness" alone in its folder; "“Fix the tests” in harness" when it shares it. */
function describe(p) {
  const group = groupByFolder(projects).find((g) => g.rows.includes(p));
  if (!group || group.rows.length < 2) return nameOf(p);
  return `“${labelText(chatLabels(group.rows).get(p.id))}” in ${nameOf(p)}`;
}

function rowHtml(p, { title, n = 1, sub, active, child }) {
  const st = status(p);
  const secs = p.busy ? busyFor(p) : null;
  const time = secs != null ? ` <span class="pr-time" data-id="${escapeHtml(p.id)}">${elapsed(secs)}</span>` : '';
  const meta = [
    st.text ? `<span class="pr-state">${st.text}${time}</span>` : '',
    sub ? `<span class="pr-sub">${escapeHtml(sub)}</span>` : '',
  ].filter(Boolean).join('<span class="pr-sep" aria-hidden="true">·</span>');
  const where = p.headless ? 'Opened from the web' : 'Running in a terminal';
  const stop = p.headless
    ? `<button type="button" class="pr-stop" data-stop="${escapeHtml(p.id)}" aria-label="Stop ${escapeHtml(title)}" title="Stop this Jarvis (opened from the web)">${icon('power')}</button>`
    : '';
  return `
    <div class="project-item${p.headless ? ' has-stop' : ''}${child ? ' is-child' : ''}" role="listitem">
      <a class="project-row ${st.cls}${active ? ' is-active' : ''}" href="${escapeHtml(hrefFor(p.id))}"
         data-id="${escapeHtml(p.id)}" ${active ? 'aria-current="page"' : ''} title="${escapeHtml(p.cwd || p.project)} · ${where}">
        <span class="pr-ind" aria-hidden="true">${st.cls === 'is-ask' ? icon('shield') : ''}</span>
        <span class="pr-body">
          <span class="pr-title"><span class="pr-name">${escapeHtml(title)}</span>${n > 1 ? `<span class="pr-n" title="Another chat here has the same title">${n}</span>` : ''}<span class="pr-src" title="${where}">${icon(p.headless ? 'globe' : 'terminal')}</span></span>
          ${meta ? `<span class="pr-meta">${meta}</span>` : ''}
        </span>
      </a>${stop}
    </div>`;
}

function renderList(cur) {
  const list = $('projects-list');
  if (!list) return;
  const shown = switching || cur;
  // Several chats in one folder: a folder header, then each chat by its title
  // (not "harness", "harness 2", "harness 3" that all read the same).
  list.innerHTML = groupByFolder(projects).map((g) => {
    if (g.rows.length === 1) {
      const p = g.rows[0];
      return rowHtml(p, { title: g.name, sub: p.session_title || p.model || 'New chat', active: p.id === shown });
    }
    const labels = chatLabels(g.rows);
    const working = g.rows.filter((p) => p.busy).length;
    const asking = g.rows.filter((p) => p.needs_approval).length;
    const live = asking ? `<span class="pg-live is-ask">${asking} waiting</span>`
      : working ? `<span class="pg-live">${working} working</span>` : '';
    // A chat's own line: its state while it has one, else its model (the folder's already above).
    return `
      <div class="proj-group" role="presentation" title="${escapeHtml(g.cwd || g.name)}">
        <span class="pg-ic" aria-hidden="true">${icon('folder')}</span>
        <span class="pg-name">${escapeHtml(g.name)}</span>
        <span class="pg-count">${g.rows.length} chats</span>${live}
      </div>
      <div class="proj-children">${g.rows.map((p) => rowHtml(p, {
        ...labels.get(p.id),
        title: labels.get(p.id).text,
        sub: status(p).text ? '' : p.model || '',
        active: p.id === shown,
        child: true,
      })).join('')}</div>`;
  }).join('');
}

/** Working times tick on their own; the list is redrawn only when something else changes. */
function tickTimes() {
  for (const el of document.querySelectorAll('#projects-list .pr-time')) {
    const secs = busyFor(find(el.dataset.id) || {});
    if (secs != null) el.textContent = elapsed(secs);
  }
}

function renderBanner(cur) {
  const banner = $('proj-banner');
  if (!banner) return;
  const waiting = projects.length > 1 && projects.find((p) => p.needs_approval && p.id !== cur);
  if (!waiting) {
    banner.hidden = true;
    return;
  }
  $('proj-banner-text').textContent = `${describe(waiting)} needs your approval`;
  banner.href = hrefFor(waiting.id);
  banner.dataset.id = waiting.id;
  banner.hidden = false;
}

function render({ force = false } = {}) {
  const cur = currentProjectId();
  const many = projects.length > 1;
  const section = $('projects-sec');
  // Always there once known: it holds "Open folder", even with one project.
  if (section) section.hidden = !projects.length;
  const next = JSON.stringify([cur, switching, [...unseen], projects.map((p) =>
    [p.id, p.project, p.session_id, p.session_title, p.model, p.busy, p.needs_approval, p.headless, p.cwd])]);
  if (next === sig && !force) return;
  sig = next;
  if (projects.length) renderList(cur);
  renderBanner(cur);
  setProjectItems(many ? projects.filter((p) => p.id !== cur).map((p) => ({
    group: 'Projects',
    label: describe(p),
    desc: status(p).text || p.session_title || p.cwd || 'Switch to this project',
    icon: 'folder',
    keys: `project switch ${p.cwd || ''}`,
    run: () => switchProject(p.id),
  })) : []);
}

/** A new list (event or poll): note what finished / closed, then draw it. */
function applyList(rows) {
  if (!Array.isArray(rows)) return;
  const cur = currentProjectId();
  const ids = new Set(rows.map((p) => p.id));
  let closed = false;
  for (const p of projects) {
    if (ids.has(p.id)) continue;
    rememberClosed(p);
    unseen.delete(p.id);
    wasBusy.delete(p.id);
    prefetched.delete(p.id);
    closed = true;
  }
  for (const p of rows) {
    if (wasBusy.get(p.id) && !p.busy && p.id !== cur) unseen.add(p.id);
    wasBusy.set(p.id, !!p.busy);
  }
  const sessionsMoved = JSON.stringify(projects.map((p) => [p.id, p.session_id]))
    !== JSON.stringify(rows.map((p) => [p.id, p.session_id]));
  projects = rows;
  receivedAt = Date.now();
  render();
  if (closed || sessionsMoved) sessionsChanged();
  // The project on screen is no longer running.
  if (BASE && !ids.has(cur) && rows.length && !switching) projectGone();
}

// ─── Switching ────────────────────────────────────────────────────────────

function prefetch(id) {
  if (!id || id === currentProjectId()) return null;
  const hit = prefetched.get(id);
  if (hit && Date.now() - hit.at < PREFETCH_TTL_MS) return hit.promise;
  const promise = fetchStateAt(baseFor(id));
  promise.catch(() => prefetched.delete(id));
  prefetched.set(id, { at: Date.now(), promise });
  return promise;
}

/** Forget what the shown project left on screen before the next one paints. */
function resetView() {
  syncPrompts([]);              // its approval / question is not this project's
  closeAllModals();
  invalidateSnapshot();
  resetChanges();
  onSnapshot({ type: 'tool_wave_reset', data: {} });
  // Not a turn ending: no "Done" flash, sound or notification.
  patchStore({ busy: false, busySince: 0, statusLabel: '', doneFlash: null, stopRequested: false });
}

/**
 * Show project `id` in this tab. Resolves when it is on screen (or the switch
 * failed and the current project stayed). `push: false` for Back / Forward.
 */
export async function switchProject(id, { push = true } = {}) {
  if (!id) return;
  if (switching) {
    queued = id;
    return;
  }
  if (id === currentProjectId()) {
    document.body.classList.remove('side-open');
    return;
  }
  if (!find(id)) {
    showToast('That project is no longer running', true);
    refresh();
    return;
  }
  switching = id;
  render({ force: true });
  document.body.classList.add('is-switching');
  const base = baseFor(id);
  let snap;
  try {
    snap = await (prefetch(id) || fetchStateAt(base));
  } catch (err) {
    switching = null;
    document.body.classList.remove('is-switching');
    prefetched.delete(id);
    if (err?.status === 404 || err?.status === 502) {
      showToast(`${describe(find(id))} was just closed`, true);
      refresh();
    } else if (err?.status === 401) {
      showToast('This link has expired — open the new one from your terminal', true);
    } else {
      showToast(`Could not reach ${describe(find(id))}. Try again.`, true);
    }
    render({ force: true });
    return;
  }
  prefetched.delete(id);
  setBase(base);
  if (push) {
    try { history.pushState({ project: id }, '', hrefFor(id)); } catch { /* sandboxed */ }
  }
  resetView();
  unseen.delete(id);
  onSnapshot({ type: 'snapshot', data: snap });
  switchTransport();
  // Lists that differ per project folder.
  handleCommandsEvent();
  handleMcpEvent({});
  sessionsChanged();
  switching = null;
  render({ force: true });
  document.body.classList.remove('side-open');
  requestAnimationFrame(() => document.body.classList.remove('is-switching'));
  if (queued && queued !== id) {
    const next = queued;
    queued = null;
    switchProject(next);
  }
  queued = null;
}

/** The project on screen closed while this server is still up: move to another one. */
export function projectGone() {
  const id = currentProjectId();
  if (!BASE || goneHandled.has(id)) return;
  goneHandled.add(id);
  const gone = find(id);
  if (gone) rememberClosed(gone);
  projects = projects.filter((p) => p.id !== id);
  const next = find(hostId) || projects[0];
  const name = gone ? nameOf(gone) : 'That project';
  showToast(`${name} was closed — its chat is in Recent sessions`);
  if (next) switchProject(next.id);
  else location.replace(`/?token=${encodeURIComponent(readToken())}`);
  sessionsChanged();
}

/** Can the browser reach that Jarvis? (any answer counts — the token is checked once there) */
async function reachable(link) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 1500);
  try {
    await fetch(`${new URL(link).origin}/static/js/icons.js`, { mode: 'no-cors', cache: 'no-store', signal: ctrl.signal });
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(t);
  }
}

/**
 * The server this page came from stopped answering (its terminal was closed).
 * Move to another running Jarvis if there is one; otherwise keep waiting —
 * the page reconnects by itself if this one starts again.
 */
export async function serverLost() {
  if (lost) return;
  lost = true;
  const cur = currentProjectId();
  const order = [cur, ...projects.map((p) => p.id).filter((id) => id !== cur)];
  for (const id of order) {
    const link = id !== hostId && directLinks[id];
    if (!link || !(await reachable(link))) continue;
    const host = find(hostId);
    if (host) rememberClosed(host);
    showToast(`${host ? nameOf(host) : 'Jarvis'} was closed — moving you to ${nameOf(find(id))}`);
    // Saved notes live per address: tell the next page which chat came from where.
    const note = host?.session_id != null ? `#closed=${encodeURIComponent(`${host.session_id}:${nameOf(host)}`)}` : '';
    setTimeout(() => location.replace(link + note), 700);
    return;
  }
  lost = false; // nothing to move to: try again on the next failure
}

// ─── Stopping a project opened from the web ───────────────────────────────

async function stopProject(btn) {
  const id = btn.dataset.stop;
  const p = find(id);
  if (!p) return;
  if (btn.dataset.confirm !== '1') {
    // Two clicks: the first one asks, right where you clicked.
    btn.dataset.confirm = '1';
    btn.classList.add('is-confirm');
    btn.innerHTML = `<span>${p.busy ? 'Working — stop?' : 'Stop?'}</span>`;
    clearTimeout(btn._revert);
    btn._revert = setTimeout(() => {
      if (!btn.isConnected) return;
      btn.dataset.confirm = '';
      btn.classList.remove('is-confirm');
      btn.innerHTML = icon('power');
    }, 3500);
    return;
  }
  clearTimeout(btn._revert);
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" aria-hidden="true"></span>';
  // Leave it first if it's on screen, so the page never points at a stopped Jarvis.
  if (id === currentProjectId() && id !== hostId) {
    const other = find(hostId) || projects.find((x) => x.id !== id);
    goneHandled.add(id);
    if (other) await switchProject(other.id);
  }
  let res;
  try {
    res = await stopProjectById(id);
  } catch (err) {
    res = { ok: false, error: err?.status === 401 ? 'This link has expired.' : 'Jarvis is not reachable right now.' };
  }
  if (!res?.ok) {
    showToast(res?.error || `Couldn’t stop ${describe(p)}`, true);
    sig = '';
    render({ force: true });
    return;
  }
  rememberClosed(p);
  showToast(`${describe(p)} stopped${res.forced ? ' (it had to be forced)' : ''} — its chat is in Recent sessions`);
  refresh();
}

// ─── Polling ──────────────────────────────────────────────────────────────

/** Re-read the list now (after opening / stopping a project). */
export function refreshProjects() {
  return refresh();
}

async function refresh() {
  clearTimeout(timer);
  let alone = true;
  try {
    const data = await fetchProjects();
    lost = false;
    hostId = data.self || hostId;
    hubLink = data.link || hubLink;
    directLinks = {};
    for (const p of data.projects || []) if (p.link) directLinks[p.id] = p.link;
    alone = (data.projects || []).length < 2;
    applyList(data.projects || []);
  } catch {
    // An older Jarvis without /api/projects, or a blip: keep what is shown.
  }
  if (!document.hidden) timer = setTimeout(refresh, alone ? POLL_ALONE_MS : POLL_MS);
}

/** `projects` event from the shown project's server (jarvis/web/sync.py). */
export function handleProjectsEvent(data) {
  if (Array.isArray(data?.projects)) applyList(data.projects);
}

/** `#closed=<session id>:<project>` from a page that moved here when its Jarvis closed. */
function takeClosedNote() {
  const m = /^#closed=(.+)$/.exec(location.hash);
  if (!m) return;
  const [sid, ...name] = decodeURIComponent(m[1]).split(':');
  if (sid) rememberClosed({ session_id: sid, project: name.join(':') });
  try { history.replaceState(history.state, '', location.pathname + location.search); } catch { /* sandboxed */ }
}

export function initProjects({ onEvent }) {
  onSnapshot = onEvent;
  takeClosedNote();
  const list = $('projects-list');
  const isPlainClick = (e) => !(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0);
  list?.addEventListener('click', (e) => {
    const stop = e.target.closest('.pr-stop');
    if (stop) {
      e.preventDefault();
      stopProject(stop);
      return;
    }
    const row = e.target.closest('.project-row');
    if (!row || !isPlainClick(e)) return; // ⌘-click: the browser opens it in a new tab
    e.preventDefault();
    switchProject(row.dataset.id);
  });
  // Hover / focus: fetch the snapshot now so the click paints at once.
  const warm = (e) => prefetch(e.target.closest?.('.project-row')?.dataset.id);
  list?.addEventListener('pointerover', warm);
  list?.addEventListener('focusin', warm);
  $('open-folder')?.addEventListener('click', () => {
    document.body.classList.remove('side-open');
    openFolders();
  });
  $('proj-banner')?.addEventListener('click', (e) => {
    if (!isPlainClick(e)) return;
    e.preventDefault();
    switchProject(e.currentTarget.dataset.id);
  });
  window.addEventListener('popstate', () => {
    const id = idFromPath(location.pathname) || hostId;
    if (id !== currentProjectId()) switchProject(id, { push: false });
  });
  refresh();
  setInterval(() => { if (!document.hidden) tickTimes(); }, 1000);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clearTimeout(timer);
    else refresh();
  });
}
