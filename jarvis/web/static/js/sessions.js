/** Sessions — every saved chat, by project, searchable down to the message.
 *
 *   ┌ PROJECTS ──────────┐  [🔍 Search titles and messages ……]  [+ New chat]
 *   │ ● This project 103 │  [Active | Pinned | Archived]   [Recent | Oldest | Longest]
 *   │   All projects 260 │  PINNED
 *   │   habuild-mobile 17│  ☐ Fix the login page        harness · 30 msgs · 2h
 *   │   crm-fe-remix  12 │  TODAY
 *   │   Not recorded 111 │  ☐ Landing page on hex …     ⟂ "…rate limit kicks in…"
 *   └────────────────────┘  ─────────────────────────────────────────────────
 *                            3 selected   Pin · Archive · Delete · Clear
 *
 * Every chat records the folder it was started in (storage/session_library.py),
 * so the list can show one project or all of them. Opening a chat from another
 * folder runs it *there*: in that folder's Jarvis if one is running (a new tab
 * when it's busy), else it starts one (`jarvis --headless`, like Open folder)
 * and resumes the chat in it — tools then run in the right project.
 *
 * Shared with the sidebar's Recent list (sidebar.js): `openSession`,
 * `sessionMenu`, `relTime`, `dayLabel`.
 */
import { $, escapeHtml, showToast, haptic, copyText, isMac, debounce, storageGet, storageSet } from './utils.js';
import { icon } from './icons.js';
import { openModal, closeModal, isModalOpen } from './modal.js';
import { fetchSessionList, fetchSessionExport, openProjectAt, fetchLaunch, pickerAction } from './api.js';
import { store } from './store.js';
import { runAction, newChat } from './actions.js';
import { tabsPost } from './tabs.js';
import { projectForSession, projectForFolder, switchProject, refreshProjects, currentProjectId } from './projects.js';
import { openMenu, spin, plural, seg } from './extui.js';
import { toolbar, footer, empty, section, arrowRows, setHomeSub } from './dialog.js';
import { renameInline } from './rename.js';

const ID = 'sessions';
const PAGE = 60;
const SCOPE_KEY = 'jarvis-sessions-scope';
const VIEWS = [
  { value: 'active', label: 'Active' },
  { value: 'pinned', label: 'Pinned' },
  { value: 'archived', label: 'Archived' },
];
const SORTS = [
  { value: 'recent', label: 'Recent' },
  { value: 'oldest', label: 'Oldest' },
  { value: 'longest', label: 'Longest' },
];
const LAUNCH_POLL_MS = 700;
const LAUNCH_LIMIT_MS = 60_000;

const S = {
  scope: 'project',   // project | all | folder
  cwd: '',            // with scope = folder ("" = folder not recorded)
  view: 'active',
  sort: 'recent',
  q: '',
  data: null,         // last answer (folders, counts …)
  rows: [],
  more: false,
  loading: false,
  error: '',
  seq: 0,
  selected: new Set(),
  anchor: null,       // last row clicked for shift-select
  armedBulk: '',      // 'delete' while the bulk delete waits for its second click
  undo: null,         // { ids, label, timer }
};

// ─── Small helpers (shared with the sidebar) ──────────────────────────────

/** 1793593355 → "just now" · "5m" · "3h" · "Yesterday" · "Mon" · "Oct 28" · "Oct 28, 2025" */
export function relTime(ts) {
  if (!ts) return '';
  const now = Date.now();
  const s = Math.max(0, now / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  const d = new Date(ts * 1000);
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const days = Math.floor((today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  if (days <= 0) return `${Math.floor(s / 3600)}h ago`;
  if (days === 1) return 'Yesterday';
  if (days < 7) return d.toLocaleDateString(undefined, { weekday: 'short' });
  const sameYear = d.getFullYear() === today.getFullYear();
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }) });
}

/** Date section a chat falls in: Today · Yesterday · Previous 7 days · This month · "October 2026". */
export function dayLabel(ts) {
  if (!ts) return 'Older';
  const d = new Date(ts * 1000);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.floor((today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return 'Previous 7 days';
  if (d.getFullYear() === today.getFullYear() && d.getMonth() === today.getMonth()) return 'Earlier this month';
  return d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
}

const fullDate = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : '');
const sessionsChanged = () => document.dispatchEvent(new Event('jarvis:sessions-changed'));
const closeDrawer = () => {
  if (window.matchMedia('(max-width: 960px)').matches) document.body.classList.remove('side-open');
};

/** Where a chat would open: 'here' · 'running' (another Jarvis has it on screen)
 *  · 'folder' (another folder's Jarvis is running) · 'launch' (start one there). */
export function whereOf(s) {
  if (!s) return { kind: 'here' };
  const live = projectForSession(s.id);
  if (live) return { kind: 'running', project: live };
  const here = store.session.cwd || '';
  if (!s.cwd || s.here || (here && s.cwd === here)) return { kind: 'here' };
  const p = projectForFolder(s.cwd);
  if (p && p.id !== currentProjectId()) return { kind: 'folder', project: p };
  if (p) return { kind: 'here' };
  return { kind: 'launch' };
}

// ─── Opening a chat ───────────────────────────────────────────────────────

async function resumeHere(sid, msg = 'Session resumed') {
  // A reply is running: don't stop it — the chat opens beside it in a new tab.
  if (store.busy) return openInTab(sid);
  return runAction('session_resume', { session_id: sid }, msg);
}

export async function openInTab(sid) {
  try {
    const res = await tabsPost('new', 0, { session_id: sid });
    if (!res?.ok) {
      showToast(res?.error || 'Couldn’t open a new tab', true);
      return { ok: false };
    }
    showToast(res.already ? 'Already open — switched to its tab' : 'Opened in a new tab');
    return { ok: true };
  } catch (err) {
    showToast(err?.status === 404 ? 'Tabs need the terminal Jarvis' : `Couldn’t open a new tab: ${err.message || err}`, true);
    return { ok: false };
  }
}

async function waitForLaunch(launch) {
  const started = Date.now();
  let misses = 0;
  while (Date.now() - started < LAUNCH_LIMIT_MS) {
    await new Promise((r) => setTimeout(r, LAUNCH_POLL_MS));
    let next;
    try {
      next = (await fetchLaunch(launch.id))?.launch;
    } catch {
      if (++misses > 10) break;
      continue;
    }
    if (next?.status === 'ready') return next;
    if (next?.status === 'failed') throw new Error(next.error || 'it stopped while starting');
  }
  throw new Error('it took too long to start');
}

/** Open chat `s` where it belongs — by default in a new tab, never over the current chat.
 *  `where`: auto · here (replace the current tab's chat) · tab (new tab here). */
export async function openSession(s, { where = 'auto' } = {}) {
  if (!s) return { ok: false };
  if (s.active && where !== 'tab') {
    closeDrawer();
    return { ok: true, here: true };
  }
  if (where === 'tab') return openInTab(s.id);
  if (where === 'here') {
    if (s.cwd && !s.here) await pickerAction('session_move', { session_id: s.id });
    return resumeHere(s.id, s.cwd && !s.here ? `Opened here — it now belongs to ${store.session.cwd_display || 'this folder'}` : 'Session resumed');
  }
  const w = whereOf(s);
  // A chat from this folder opens in its own new tab; the current tab keeps its chat.
  if (w.kind === 'here') return openInTab(s.id);
  if (w.kind === 'running') {
    await switchProject(w.project.id);
    return { ok: true, switched: true };
  }
  const name = s.project || 'its folder';
  if (w.kind === 'folder') {
    await switchProject(w.project.id);
    if (currentProjectId() !== w.project.id) return { ok: false };
    // Never replace what that Jarvis has on screen: open the chat in a new tab beside it.
    return openInTab(s.id);
  }
  // No Jarvis runs in that folder: start one there, then resume the chat in it.
  showToast(`Starting Jarvis in ${name}…`);
  let res;
  try {
    res = await openProjectAt(s.cwd, true);
  } catch (err) {
    res = { ok: false, error: err?.status === 401 ? 'This link has expired.' : 'Jarvis is not reachable right now.' };
  }
  if (!res?.ok) {
    if (res?.code === 'not_found' || res?.code === 'not_a_folder') {
      showToast(`${s.cwd_display || name} is gone — opening the chat here instead`, true);
      return resumeHere(s.id);
    }
    showToast(res?.error || `Couldn’t open ${name}`, true);
    return { ok: false };
  }
  let instance = res.instance_id;
  if (!res.reused) {
    try {
      instance = (await waitForLaunch(res.launch)).instance_id;
    } catch (err) {
      showToast(`Couldn’t start Jarvis in ${name}: ${err.message || err}`, true);
      return { ok: false };
    }
  }
  await refreshProjects();
  await switchProject(instance);
  if (currentProjectId() !== instance) return { ok: false };
  return runAction('session_resume', { session_id: s.id }, `Opened in ${name} — your other projects keep running`);
}

// ─── The ⋯ menu (sidebar rows and dialog rows) ────────────────────────────

async function exportSession(s, how) {
  let res;
  try {
    res = await fetchSessionExport(s.id);
  } catch (err) {
    showToast(err.message || 'Couldn’t export that chat', true);
    return;
  }
  if (how === 'copy') {
    if (await copyText(res.markdown)) showToast('Copied as Markdown');
    else showToast('Couldn’t copy — your browser blocked it', true);
    return;
  }
  const url = URL.createObjectURL(new Blob([res.markdown], { type: 'text/markdown;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = res.name || `session-${s.id}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  showToast(`Saved ${a.download}`);
}

export async function setPinned(ids, pinned) {
  const res = await pickerAction('session_pin', { session_ids: ids, pinned });
  if (!res.ok) showToast(res.error || 'Couldn’t pin that', true);
  else sessionsChanged();
  return res;
}

export async function setArchived(ids, archived) {
  const res = await pickerAction('session_archive', { session_ids: ids, archived });
  if (!res.ok) showToast(res.error || 'Couldn’t archive that', true);
  else sessionsChanged();
  return res;
}

/** The ⋯ menu for one chat. `titleEl` lets Rename edit in place. Resolves with what happened. */
export async function sessionMenu(anchor, s, { titleEl = null } = {}) {
  const w = whereOf(s);
  const elsewhere = w.kind !== 'here';
  const items = [];
  if (elsewhere) {
    items.push({ key: 'open', label: w.kind === 'running' ? `Switch to ${w.project.project || 'its project'}` : `Open in ${s.project || 'its project'}`, icon: 'external-link' });
    items.push({ key: 'here', label: 'Open here instead', icon: 'arrow-right-left' });
  } else if (!s.active) {
    items.push({ key: 'tab', label: s.tab ? 'Show its tab' : 'Open in new tab', icon: 'app-window' });
    items.push({ key: 'here', label: 'Open in current tab', icon: 'arrow-right-left' });
  }
  items.push({ key: 'rename', label: 'Rename', icon: 'pencil' });
  items.push({ key: 'pin', label: s.pinned ? 'Unpin' : 'Pin to top', icon: s.pinned ? 'pin-off' : 'pin' });
  if (!s.here && s.cwd && !elsewhere) items.push({ key: 'move', label: 'Move to this project', icon: 'arrow-right-left' });
  items.push({ divider: true, key: 'd1' });
  items.push({ key: 'download', label: 'Download as Markdown', icon: 'download' });
  items.push({ key: 'copy', label: 'Copy as Markdown', icon: 'copy' });
  items.push({ divider: true, key: 'd2' });
  const open = s.active || !!s.tab;
  items.push({ key: 'archive', label: s.archived ? 'Restore from archive' : 'Archive', icon: 'archive', disabled: open && !s.archived, reason: open ? 'Open right now' : '' });
  items.push({ key: 'delete', label: 'Delete', icon: 'trash-2', danger: true, confirm: true, disabled: open, reason: open ? 'Open right now' : '' });

  const key = await openMenu(anchor, items);
  switch (key) {
    case 'open': return openSession(s);
    case 'here': return openSession(s, { where: 'here' });
    case 'tab': return openSession(s, { where: 'tab' });
    case 'rename': {
      if (!titleEl) return null;
      const title = await renameInline(titleEl, { sessionId: s.id, value: s.auto_title ? '' : s.title, label: `New title for session ${s.id}`, className: 'lr-rename' });
      if (title) {
        s.title = title;
        s.auto_title = false;
        sessionsChanged();
      }
      return { renamed: title };
    }
    case 'pin': return setPinned([s.id], !s.pinned);
    case 'move': {
      const res = await pickerAction('session_move', { session_id: s.id });
      if (res.ok) {
        showToast(`Moved to ${res.display || 'this project'}`);
        sessionsChanged();
      } else showToast(res.error || 'Couldn’t move it', true);
      return res;
    }
    case 'download': return exportSession(s, 'download');
    case 'copy': return exportSession(s, 'copy');
    case 'archive': {
      const res = await setArchived([s.id], !s.archived);
      if (res.ok) showToast(s.archived ? 'Restored' : 'Archived — find it under Sessions › Archived');
      return res;
    }
    case 'delete': {
      const res = await pickerAction('session_delete', { session_id: s.id });
      if (res.ok) {
        showToast('Chat deleted');
        sessionsChanged();
      } else showToast(res.error || 'Couldn’t delete it', true);
      return res;
    }
    default: return null;
  }
}

// ─── Dialog: data ─────────────────────────────────────────────────────────

function params(offset = 0) {
  return {
    scope: S.scope, cwd: S.scope === 'folder' ? S.cwd : '', view: S.view,
    sort: S.sort, q: S.q.trim(), limit: PAGE, offset, folders: 1,
  };
}

async function load({ append = false } = {}) {
  const seq = ++S.seq;
  S.loading = true;
  if (!append) S.error = '';
  renderList();
  let data;
  try {
    data = await fetchSessionList(params(append ? S.rows.length : 0));
  } catch (err) {
    if (seq !== S.seq) return;
    S.loading = false;
    S.error = err?.message || 'Couldn’t load sessions';
    renderAll();
    return;
  }
  if (seq !== S.seq) return; // a newer search / filter won
  S.loading = false;
  S.data = data;
  S.rows = append ? [...S.rows, ...(data.sessions || [])] : (data.sessions || []);
  S.more = !!data.more;
  // Selections of rows that left the list (filter change) are dropped.
  const ids = new Set(S.rows.map((r) => r.id));
  for (const id of [...S.selected]) if (!ids.has(id)) S.selected.delete(id);
  renderAll();
}

const reloadSoon = debounce(() => load(), 220);

/** Something changed elsewhere (sidebar, another page): an open dialog catches up. */
export function refreshSessionsDialog() {
  if (isModalOpen(ID)) reloadSoon();
}

// ─── Dialog: markup ───────────────────────────────────────────────────────

function railItem({ key, label, sub = '', count = 0, on = false, ic = 'folder', title = '', dim = false }) {
  return `<button type="button" class="ss-folder${on ? ' is-on' : ''}${dim ? ' is-dim' : ''}" data-act="scope" data-key="${escapeHtml(key)}" aria-pressed="${on}"${title ? ` title="${escapeHtml(title)}"` : ''}>
    ${icon(ic)}<span class="ss-f-text"><span class="ss-f-name">${escapeHtml(label)}</span>${sub ? `<span class="ss-f-sub">${escapeHtml(sub)}</span>` : ''}</span>
    <em class="ss-f-count">${count || ''}</em>
  </button>`;
}

function railHtml() {
  const d = S.data;
  if (!d) return '';
  const counts = d.counts || {};
  const here = d.current_cwd || '';
  const isOn = (key) => (key === 'project' ? S.scope === 'project' : key === 'all' ? S.scope === 'all' : S.scope === 'folder' && `f:${S.cwd}` === key);
  const top = [
    railItem({ key: 'project', label: 'This project', sub: d.current_name || '', count: counts.project, on: isOn('project'), ic: 'folder-open', title: d.current_display }),
    railItem({ key: 'all', label: 'All projects', count: counts.all, on: isOn('all'), ic: 'history' }),
  ].join('');
  const others = (d.folders || []).filter((f) => f.cwd !== here && (f.count || S.view === 'archived'));
  const list = others.map((f) => railItem({
    key: `f:${f.cwd}`,
    label: f.name,
    sub: f.known ? f.display : '',
    count: S.view === 'archived' ? f.archived : f.count,
    on: isOn(`f:${f.cwd}`),
    ic: f.known ? 'folder' : 'circle-help',
    title: f.known ? `${f.display}${f.exists ? '' : ' (folder no longer exists)'}` : f.display,
    dim: f.known && !f.exists,
  })).join('');
  return `<nav class="ss-rail" aria-label="Projects">
    ${top}
    ${others.length ? `<div class="ss-rail-label">Other projects</div>${list}` : ''}
  </nav>`;
}

/** Phones: the projects rail becomes one row of chips above the list. */
function chipsHtml() {
  const d = S.data;
  if (!d) return '';
  const here = d.current_cwd || '';
  const chip = (key, label, n, on) => `<button type="button" class="ss-chip" data-act="scope" data-key="${escapeHtml(key)}" aria-pressed="${on}">${escapeHtml(label)}${n ? `<em>${n}</em>` : ''}</button>`;
  const others = (d.folders || []).filter((f) => f.cwd !== here && f.count);
  return `<div class="ss-chips" role="group" aria-label="Project">
    ${chip('project', d.current_name || 'This project', d.counts?.project, S.scope === 'project')}
    ${chip('all', 'All', d.counts?.all, S.scope === 'all')}
    ${others.map((f) => chip(`f:${f.cwd}`, f.name, f.count, S.scope === 'folder' && S.cwd === f.cwd)).join('')}
  </div>`;
}

function badgeOf(s) {
  if (s.active) return '<span class="badge is-live">Open now</span>';
  if (s.tab) return `<span class="badge ss-b-tab" title="Open in another tab of this Jarvis">${icon('app-window')}${s.tab.busy ? 'Working' : 'In a tab'}</span>`;
  const w = whereOf(s);
  if (w.kind === 'running') return `<span class="badge is-live" title="On screen in ${escapeHtml(w.project.project)} — click to switch there">${icon('external-link')}Running</span>`;
  return '';
}

function snippetHtml(sn) {
  if (!sn?.text) return '';
  const t = sn.text;
  const at = Math.max(0, Math.min(t.length, sn.at || 0));
  const hit = sn.len ? `<mark>${escapeHtml(t.slice(at, at + sn.len))}</mark>` : '';
  const who = { you: 'You', jarvis: 'Jarvis', tool: 'Tool' }[sn.kind] || '';
  return `<span class="ss-snip"><span class="ss-snip-who">${who}</span>${escapeHtml(t.slice(0, at))}${hit}${escapeHtml(t.slice(at + (sn.len || 0)))}</span>`;
}

function rowHtml(s, i) {
  const showProject = S.scope === 'all' || (S.q && S.scope !== 'folder');
  const w = whereOf(s);
  const proj = showProject && (s.project || !s.cwd)
    ? `<span class="ss-proj${s.here ? ' is-here' : ''}${w.kind !== 'here' ? ' is-away' : ''}" title="${escapeHtml(s.cwd_display || 'Folder not recorded')}${s.cwd_guessed ? ' (worked out from the files it used)' : ''}">${icon(s.cwd ? 'folder' : 'circle-help')}${escapeHtml(s.project || 'not recorded')}</span>`
    : '';
  const meta = [
    `${s.msg_count} msg${s.msg_count === 1 ? '' : 's'}`,
    s.model ? escapeHtml(s.model) : '',
  ].filter(Boolean).join(' · ');
  const sel = S.selected.has(s.id);
  const away = w.kind === 'folder' || w.kind === 'launch';
  return `<li class="ss-row${s.active ? ' is-active' : ''}${sel ? ' is-selected' : ''}${s.pinned ? ' is-pinned' : ''}" data-id="${s.id}" style="--i:${Math.min(i, 14)}">
    <label class="ss-check" title="Select (shift-click for a range)"><input type="checkbox" data-act="select" ${sel ? 'checked' : ''} aria-label="Select “${escapeHtml(s.title)}”"></label>
    <button type="button" class="ss-main" data-act="open" title="${away ? `Opens in ${escapeHtml(s.project)} — ${escapeHtml(s.cwd_display)}` : 'Open this chat'}">
      <span class="ss-line1">
        ${s.pinned ? `<span class="ss-pin" title="Pinned">${icon('pin')}</span>` : ''}
        <span class="ss-title">${escapeHtml(s.title)}</span>
        ${badgeOf(s)}
      </span>
      ${snippetHtml(s.snippet)}
      <span class="ss-line2">${proj}<span class="ss-meta">${meta}</span><time class="ss-time" title="${escapeHtml(fullDate(s.updated_at))}">${escapeHtml(relTime(s.updated_at))}</time></span>
    </button>
    <span class="ss-acts">
      <button type="button" class="row-btn is-icon" data-act="pin" aria-label="${s.pinned ? 'Unpin' : 'Pin'} “${escapeHtml(s.title)}”" title="${s.pinned ? 'Unpin' : 'Pin to top'}">${icon(s.pinned ? 'pin-off' : 'pin')}</button>
      ${w.kind === 'here' && !s.active ? `<button type="button" class="row-btn is-icon" data-act="tab" aria-label="Open “${escapeHtml(s.title)}” in a new tab" title="${s.tab ? 'Show its tab' : `Open in new tab (${isMac ? '⌘' : 'Ctrl'}↵)`}">${icon('app-window')}</button>` : ''}
      <button type="button" class="row-btn is-icon" data-act="menu" aria-haspopup="menu" aria-label="More for “${escapeHtml(s.title)}”" title="More">${icon('ellipsis')}</button>
    </span>
  </li>`;
}

function grouped(rows) {
  if (S.q) return [[`${plural(S.data?.total ?? rows.length, 'match')}`, rows]];
  if (S.sort !== 'recent') return [['', rows]];
  const out = [];
  let cur = null;
  for (const s of rows) {
    const label = s.pinned && S.view === 'active' ? 'Pinned' : dayLabel(s.updated_at);
    if (!cur || cur[0] !== label) {
      cur = [label, []];
      out.push(cur);
    }
    cur[1].push(s);
  }
  return out;
}

function emptyHtml() {
  if (S.error) {
    return empty('Couldn’t load sessions', escapeHtml(S.error), { ic: 'circle-alert', action: '<button type="button" class="btn" data-act="reload">Try again</button>' });
  }
  if (S.q) {
    const wider = S.scope !== 'all' ? '<button type="button" class="btn" data-act="scope" data-key="all">Search all projects</button>' : '';
    return empty('Nothing matches', 'Titles and every message were searched. Try other words.', { ic: 'search', action: wider });
  }
  if (S.view === 'pinned') return empty('No pinned chats', 'Pin the chats you come back to — they stay at the top of every list.', { ic: 'pin' });
  if (S.view === 'archived') return empty('Nothing archived', 'Archive a chat to tidy the list without deleting it.', { ic: 'archive' });
  if (S.scope === 'project') {
    return empty(`No chats in ${escapeHtml(S.data?.current_name || 'this project')} yet`, 'Chats you have here show up in this list.', {
      ic: 'folder-open', action: '<button type="button" class="btn" data-act="scope" data-key="all">Show all projects</button>',
    });
  }
  return empty('No saved chats yet', 'Conversations are saved as you chat.', { ic: 'history' });
}

function listHtml() {
  if (!S.data && S.loading) return `<div class="ss-loading">${spin('Loading')}</div>`;
  if (!S.rows.length) return S.loading ? `<div class="ss-loading">${spin('Searching')}</div>` : emptyHtml();
  let i = 0;
  const groups = grouped(S.rows).map(([label, rows]) => `
    ${label ? section(label, { count: S.q ? null : rows.length }) : ''}
    <ul class="ss-list" role="list">${rows.map((s) => rowHtml(s, i++)).join('')}</ul>`).join('');
  const more = S.more
    ? `<div class="ss-more"><button type="button" class="btn btn-sm" data-act="more"${S.loading ? ' disabled' : ''}>${S.loading ? spin('Loading') : `${icon('chevron-down')}<span>Show more</span>`}</button></div>`
    : '';
  return `${groups}${more}`;
}

function filtersHtml() {
  return `<div class="ss-filters">
    ${seg(VIEWS.map((v) => ({ ...v, label: v.value === 'pinned' && S.data?.counts?.pinned ? `Pinned ${S.data.counts.pinned}` : v.value === 'archived' && S.data?.counts?.archived ? `Archived ${S.data.counts.archived}` : v.label })), S.view, { label: 'Show', act: 'view' })}
    <span class="dlg-spacer"></span>
    ${S.q ? '' : seg(SORTS, S.sort, { label: 'Sort', act: 'sort' })}
  </div>`;
}

function undoHtml() {
  if (!S.undo) return '';
  return `<p class="pv-msg is-ok ss-undo" role="status">${icon('circle-check')}<span>${escapeHtml(S.undo.label)}</span>
    <button type="button" class="btn btn-sm" data-act="undo">Undo</button></p>`;
}

function footHtml() {
  const n = S.selected.size;
  if (n) {
    const rows = S.rows.filter((r) => S.selected.has(r.id));
    const allPinned = rows.every((r) => r.pinned);
    const archivedView = S.view === 'archived';
    const deleting = S.armedBulk === 'delete';
    return `<div class="ss-bulk" role="toolbar" aria-label="Selected chats">
      <strong>${n} selected</strong>
      <button type="button" class="btn btn-sm btn-quiet" data-act="select-all">Select all ${S.rows.length}</button>
      <span class="dlg-spacer"></span>
      ${archivedView ? '' : `<button type="button" class="btn btn-sm" data-act="bulk-pin">${icon(allPinned ? 'pin-off' : 'pin')}<span>${allPinned ? 'Unpin' : 'Pin'}</span></button>`}
      <button type="button" class="btn btn-sm" data-act="bulk-archive">${icon('archive')}<span>${archivedView ? 'Restore' : 'Archive'}</span></button>
      <button type="button" class="btn btn-sm${deleting ? ' btn-danger' : ''}" data-act="bulk-delete">${icon('trash-2')}<span>${deleting ? `Delete ${n} for good?` : 'Delete'}</span></button>
      <button type="button" class="row-btn is-icon" data-act="select-none" aria-label="Clear selection" title="Clear selection">${icon('x')}</button>
    </div>`;
  }
  const d = S.data;
  const projects = (d?.folders || []).filter((f) => f.known && f.count).length;
  const note = d ? `${plural(d.counts?.all || 0, 'chat')} in ${plural(projects, 'project')}` : '';
  return footer({ note, noteIc: 'history', hints: [['↑↓', 'move'], ['↵', 'open'], [isMac ? '⌘ ↵' : 'Ctrl ↵', 'new tab'], ['esc', 'close']] });
}

// ─── Dialog: render ───────────────────────────────────────────────────────

function patchEl(el, html) {
  if (el && el._html !== html) {
    el.innerHTML = html;
    el._html = html;
  }
}

function renderBar() {
  const bar = $(`${ID}-bar`);
  if (!bar) return;
  if (!bar.dataset.built) {
    bar.dataset.built = '1';
    bar.innerHTML = `${toolbar({
      id: `${ID}-search`,
      placeholder: 'Search titles and messages',
      value: S.q,
      action: { act: 'new', label: 'New chat', ic: 'plus', id: `${ID}-new` },
    })}<div id="${ID}-filters"></div>`;
  }
  patchEl($(`${ID}-filters`), filtersHtml());
}

function renderList() {
  const box = $(`${ID}-list`);
  if (!box) return;
  patchEl(box, `${undoHtml()}${chipsHtml()}${listHtml()}`);
  box.classList.toggle('is-loading', S.loading && !!S.rows.length);
}

function subtitle() {
  const d = S.data;
  if (!d) return 'Every saved chat, by project';
  if (S.scope === 'project') return `In ${d.current_display || d.current_name || 'this project'}`;
  if (S.scope === 'folder') {
    const f = (d.folders || []).find((x) => x.cwd === S.cwd);
    return f?.known ? `In ${f.display}` : 'Chats saved before folders were recorded';
  }
  return 'Every saved chat, across projects';
}

function renderAll() {
  if (!$(ID)) return;
  patchEl($(`${ID}-rail`), railHtml());
  renderBar();
  renderList();
  patchEl($(`${ID}-foot`), footHtml());
  $(`${ID}-card`)?.classList.toggle('is-selecting', S.selected.size > 0);
  setHomeSub(ID, subtitle());
}

// ─── Dialog: actions ──────────────────────────────────────────────────────

const rowOf = (el) => S.rows.find((r) => r.id === Number(el.closest('[data-id]')?.dataset.id || 0)) || null;

function setScope(key) {
  if (key === 'project' || key === 'all') {
    S.scope = key;
    S.cwd = '';
    storageSet(SCOPE_KEY, key);
  } else if (key.startsWith('f:')) {
    S.scope = 'folder';
    S.cwd = key.slice(2);
  }
  S.selected.clear();
  S.armedBulk = '';
  $(`${ID}-list`)?.scrollTo?.({ top: 0 });
  load();
}

async function openRow(s, where = 'auto') {
  const w = whereOf(s);
  if (where === 'auto' && w.kind !== 'here') closeModal(ID); // the switch / launch takes over the page
  const res = await openSession(s, { where });
  if (res?.ok && isModalOpen(ID)) closeModal(ID);
}

function toggleSelect(s, { range = false } = {}) {
  if (range && S.anchor != null) {
    const ids = S.rows.map((r) => r.id);
    const a = ids.indexOf(S.anchor);
    const b = ids.indexOf(s.id);
    if (a >= 0 && b >= 0) {
      const on = !S.selected.has(s.id);
      for (const id of ids.slice(Math.min(a, b), Math.max(a, b) + 1)) {
        if (on) S.selected.add(id);
        else S.selected.delete(id);
      }
    }
  } else if (S.selected.has(s.id)) S.selected.delete(s.id);
  else S.selected.add(s.id);
  S.anchor = s.id;
  S.armedBulk = '';
  renderAll();
}

function armUndo(ids, label, restore) {
  if (S.undo?.timer) clearTimeout(S.undo.timer);
  S.undo = { ids, label, restore };
  S.undo.timer = setTimeout(() => {
    S.undo = null;
    if (isModalOpen(ID)) renderList();
  }, 9000);
}

async function bulk(kind) {
  const ids = [...S.selected];
  if (!ids.length) return;
  const rows = S.rows.filter((r) => S.selected.has(r.id));
  if (kind === 'pin') {
    const on = !rows.every((r) => r.pinned);
    const res = await setPinned(ids, on);
    if (res.ok) showToast(`${on ? 'Pinned' : 'Unpinned'} ${plural(ids.length, 'chat')}`);
  } else if (kind === 'archive') {
    const archiving = S.view !== 'archived';
    const open = rows.filter((r) => r.active || r.tab).map((r) => r.id);
    const target = archiving ? ids.filter((id) => !open.includes(id)) : ids;
    if (!target.length) {
      showToast('Those chats are open right now', true);
      return;
    }
    const res = await setArchived(target, archiving);
    if (res.ok) {
      armUndo(target, `${archiving ? 'Archived' : 'Restored'} ${plural(target.length, 'chat')}${open.length && archiving ? ` · ${open.length} open one${open.length === 1 ? '' : 's'} kept` : ''}`,
        () => setArchived(target, !archiving));
    }
  } else if (kind === 'delete') {
    if (S.armedBulk !== 'delete') {
      S.armedBulk = 'delete';
      patchEl($(`${ID}-foot`), footHtml());
      return;
    }
    const res = await pickerAction('session_delete', { session_ids: ids });
    if (!res.ok) {
      showToast(res.error || 'Couldn’t delete them', true);
      return;
    }
    const kept = res.skipped?.length ? ` · ${res.skipped.length} open one${res.skipped.length === 1 ? '' : 's'} kept` : '';
    showToast(`Deleted ${plural(res.deleted?.length || 0, 'chat')}${kept}`);
    sessionsChanged();
  }
  S.selected.clear();
  S.armedBulk = '';
  haptic(10);
  await load();
}

async function onClick(e) {
  const el = e.target.closest('[data-act]');
  const card = $(ID);
  if (!el || !card?.contains(el) || el.disabled) return;
  const act = el.dataset.act;
  const s = rowOf(el);
  if (act === 'select') { // the checkbox: shift-click selects a range
    e.preventDefault();
    if (s) toggleSelect(s, { range: e.shiftKey });
    return;
  }
  switch (act) {
    case 'scope': setScope(el.dataset.key); break;
    case 'view':
      S.view = el.dataset.val;
      S.selected.clear();
      load();
      break;
    case 'sort':
      S.sort = el.dataset.val;
      load();
      break;
    case 'new':
      closeModal(ID);
      newChat();
      break;
    case 'open':
      if (!s) break;
      if (e.shiftKey || e.metaKey || e.ctrlKey || S.selected.size) {
        if ((e.metaKey || e.ctrlKey) && !S.selected.size && !e.shiftKey) { openRow(s, 'tab'); break; }
        toggleSelect(s, { range: e.shiftKey });
        break;
      }
      openRow(s);
      break;
    case 'tab': if (s) openRow(s, 'tab'); break;
    case 'pin':
      if (s && (await setPinned([s.id], !s.pinned)).ok) load();
      break;
    case 'menu': {
      if (!s) break;
      const before = JSON.stringify([s.title, s.pinned, s.archived]);
      const res = await sessionMenu(el, s, { titleEl: el.closest('.ss-row')?.querySelector('.ss-title') });
      if (res && (res.ok || res.renamed || JSON.stringify([s.title, s.pinned, s.archived]) !== before)) {
        if (res.switched || (res.ok && !isModalOpen(ID))) break;
        load();
      }
      break;
    }
    case 'more': load({ append: true }); break;
    case 'reload': load(); break;
    case 'select-all':
      S.rows.forEach((r) => S.selected.add(r.id));
      renderAll();
      break;
    case 'select-none':
      S.selected.clear();
      S.armedBulk = '';
      renderAll();
      break;
    case 'bulk-pin': bulk('pin'); break;
    case 'bulk-archive': bulk('archive'); break;
    case 'bulk-delete': bulk('delete'); break;
    case 'undo': {
      const u = S.undo;
      if (!u) break;
      clearTimeout(u.timer);
      S.undo = null;
      await u.restore();
      load();
      break;
    }
    default:
  }
}

function onInput(e) {
  if (e.target.id !== `${ID}-search`) return;
  S.q = e.target.value;
  S.selected.clear();
  renderBar();
  reloadSoon();
}

function onKey(e) {
  if (e.isComposing) return;
  const search = $(`${ID}-search`);
  const rows = [...($(`${ID}-list`)?.querySelectorAll('.ss-main') || [])];
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
    const target = e.target === search ? rows[0] : e.target.closest?.('.ss-main');
    const s = target && rowOf(target);
    if (s) {
      e.preventDefault();
      openRow(s, whereOf(s).kind === 'here' ? 'tab' : 'auto');
    }
    return;
  }
  if (e.key === 'Enter' && e.target === search) {
    e.preventDefault();
    rows[0]?.click();
    return;
  }
  if (arrowRows(e, search, rows)) return;
  if ((e.key === 'Delete' || e.key === 'Backspace') && e.target.closest?.('.ss-main') && !S.selected.size) {
    const s = rowOf(e.target);
    if (s && !s.active && !s.tab && S.view !== 'archived') {
      e.preventDefault();
      setArchived([s.id], true).then((res) => {
        if (!res.ok) return;
        armUndo([s.id], `Archived “${s.title}”`, () => setArchived([s.id], false));
        load();
      });
    }
  }
}

/** Open the Sessions dialog. `arg`: a search to start with, or "all" / "archived" / "pinned";
 *  `folder`: show that project folder's chats ("" = folder not recorded). */
export function openSessions(arg = '', folder = null) {
  const a = String(arg || '').trim();
  S.scope = storageGet(SCOPE_KEY, 'project') === 'all' ? 'all' : 'project';
  S.cwd = '';
  S.view = 'active';
  S.q = '';
  if (folder !== null && folder !== undefined) {
    if (folder && folder === store.session.cwd) S.scope = 'project';
    else {
      S.scope = 'folder';
      S.cwd = folder;
    }
  } else if (a === 'all') S.scope = 'all';
  else if (a === 'archived' || a === 'pinned') S.view = a;
  else if (a) {
    S.q = a;
    S.scope = 'all';
  }
  S.sort = 'recent';
  S.selected.clear();
  S.armedBulk = '';
  S.rows = [];
  S.data = null;
  const bar = $(`${ID}-bar`);
  if (bar) delete bar.dataset.built;
  renderAll();
  openModal(ID, {
    focus: undefined,
    onClose: () => {
      S.seq++;
      if (S.undo?.timer) clearTimeout(S.undo.timer);
      S.undo = null;
    },
  });
  load();
  requestAnimationFrame(() => $(`${ID}-search`)?.focus({ preventScroll: true }));
}

export function initSessions() {
  const card = $(ID);
  if (!card) return;
  card.addEventListener('click', onClick);
  card.addEventListener('input', onInput);
  card.addEventListener('keydown', onKey);
  document.addEventListener('jarvis:sessions-changed', refreshSessionsDialog);
}
