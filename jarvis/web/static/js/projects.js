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
import { fetchProjects, fetchStateAt, switchTransport } from './api.js';
import { store, patchStore } from './store.js';
import { closeAllModals } from './modal.js';
import { syncPrompts } from './prompts.js';
import { invalidateSnapshot } from './chat.js';
import { resetChanges } from './changes.js';
import { handleCommandsEvent } from './commands.js';
import { handleMcpEvent } from './mcp.js';
import { setProjectItems } from './catalog.js';

const POLL_MS = 10_000;         // the fallback; `projects` events carry changes as they happen
const POLL_ALONE_MS = 20_000;
const PREFETCH_TTL_MS = 8_000;
const CLOSED_KEY = 'jarvis-closed-projects';

let onSnapshot = () => {};
let hostId = '';        // the server this page was loaded from
let hubLink = '';       // its shareable link (QR / copy link while another project is shown)
let projects = [];
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
  if (p.needs_approval) return { cls: 'is-ask', text: 'Needs your approval' };
  if (p.busy) return { cls: 'is-busy', text: 'Working…' };
  if (unseen.has(p.id)) return { cls: 'is-new', text: 'New reply' };
  return { cls: '', text: '' };
}

function renderList(cur) {
  const list = $('projects-list');
  if (!list) return;
  // Two chats in one folder: tell them apart.
  const seen = new Map();
  list.innerHTML = projects.map((p) => {
    const n = (seen.get(p.project) || 0) + 1;
    seen.set(p.project, n);
    const st = status(p);
    const detail = p.session_title || (p.model ? p.model : 'New chat');
    const meta = st.text ? `${st.text} · ${detail}` : detail;
    const active = p.id === (switching || cur);
    return `
      <a class="project-row ${st.cls}${active ? ' is-active' : ''}" role="listitem" href="${escapeHtml(hrefFor(p.id))}"
         data-id="${escapeHtml(p.id)}" ${active ? 'aria-current="page"' : ''} title="${escapeHtml(p.cwd || p.project)}">
        <span class="pr-dot" aria-hidden="true"></span>
        <span class="pr-body">
          <span class="pr-title">${escapeHtml(nameOf(p))}${n > 1 ? ` <span class="pr-n">${n}</span>` : ''}</span>
          <span class="pr-meta">${escapeHtml(meta)}</span>
        </span>
      </a>`;
  }).join('');
}

function renderBanner(cur) {
  const banner = $('proj-banner');
  if (!banner) return;
  const waiting = projects.length > 1 && projects.find((p) => p.needs_approval && p.id !== cur);
  if (!waiting) {
    banner.hidden = true;
    return;
  }
  $('proj-banner-text').textContent = `${nameOf(waiting)} needs your approval`;
  banner.href = hrefFor(waiting.id);
  banner.dataset.id = waiting.id;
  banner.hidden = false;
}

function render({ force = false } = {}) {
  const cur = currentProjectId();
  const many = projects.length > 1;
  const section = $('projects-sec');
  if (section) section.hidden = !many;
  const next = JSON.stringify([cur, switching, [...unseen], projects.map((p) =>
    [p.id, p.project, p.session_id, p.session_title, p.model, p.busy, p.needs_approval])]);
  if (next === sig && !force) return;
  sig = next;
  if (many) renderList(cur);
  renderBanner(cur);
  setProjectItems(many ? projects.filter((p) => p.id !== cur).map((p) => ({
    group: 'Projects',
    label: nameOf(p),
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
      showToast(`${nameOf(find(id))} was just closed`, true);
      refresh();
    } else if (err?.status === 401) {
      showToast('This link has expired — open the new one from your terminal', true);
    } else {
      showToast(`Could not reach ${nameOf(find(id))}. Try again.`, true);
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

// ─── Polling ──────────────────────────────────────────────────────────────

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
    const row = e.target.closest('.project-row');
    if (!row || !isPlainClick(e)) return; // ⌘-click: the browser opens it in a new tab
    e.preventDefault();
    switchProject(row.dataset.id);
  });
  // Hover / focus: fetch the snapshot now so the click paints at once.
  const warm = (e) => prefetch(e.target.closest?.('.project-row')?.dataset.id);
  list?.addEventListener('pointerover', warm);
  list?.addEventListener('focusin', warm);
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
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clearTimeout(timer);
    else refresh();
  });
}
