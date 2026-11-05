/** Sidebar: reasoning and transcript switches, recent sessions, usage */
import { $, escapeHtml, formatCount, debounce, countTo, animateEl, SPRING, isMac, storageGet, storageSet } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe } from './store.js';
import { fetchSessionList } from './api.js';
import {
  newChat,
  onSessionChange,
  runAction,
  toggleSetting,
} from './actions.js';
import { toggleTheme, isLightTheme, openAppearance } from './theme.js';
import { closeInspector, isDocked, isInspectorOpen } from './inspector.js';
import { closedProjectFor, projectForSession, switchProject } from './projects.js';
import { openSession, openSessions as openSessionsDialogRaw, sessionMenu, whereOf, relTime, dayLabel } from './sessions.js';

const openSessionsDialog = (q = '', folder = null) => openSessionsDialogRaw(q, folder);

let openPicker = () => {};
let recentSig = '';
let lastSessionId = null;
let lastTitle = null;

// ─── Drawer (narrow screens) ──────────────────────────────────────────────

export function openSidebar() {
  // Both drawers slide over the page on small screens: one at a time.
  if (!isDocked() && isInspectorOpen()) closeInspector();
  document.body.classList.add('side-open');
  $('sidebar')?.querySelector('button, input')?.focus({ preventScroll: true });
}

export function closeSidebar() {
  document.body.classList.remove('side-open');
}

function closeOnNarrow() {
  if (window.matchMedia('(max-width: 960px)').matches) closeSidebar();
}

// ─── Collapse (wide screens) ─────────────────────────────────────────────

const COLLAPSE_KEY = 'jarvis-side-collapsed';

function setCollapsed(on) {
  document.body.classList.toggle('side-collapsed', on);
  const btn = $('menu-btn');
  if (btn) {
    const narrow = window.matchMedia('(max-width: 960px)').matches;
    btn.setAttribute('aria-expanded', String(narrow ? document.body.classList.contains('side-open') : !on));
    btn.title = narrow ? 'Open panel' : `${on ? 'Show' : 'Hide'} sidebar (${isMac ? '⌥' : 'Alt'} B)`;
  }
  storageSet(COLLAPSE_KEY, on ? '1' : '0');
}

// ─── Width (wide screens): drag the right edge, ← →, double-click resets ───

const WIDTH_KEY = 'jarvis-side-w';
const DEFAULT_W = 280;
const MIN_W = 220;
const maxWidth = () => Math.max(MIN_W, Math.min(420, Math.round(window.innerWidth * 0.4)));
const clampWidth = (w) => Math.max(MIN_W, Math.min(maxWidth(), Math.round(w)));
let sideWidth = DEFAULT_W;

function applySideWidth() {
  const w = clampWidth(sideWidth);
  document.documentElement.style.setProperty('--side-w', `${w}px`);
  const handle = $('side-resize');
  if (handle) {
    handle.setAttribute('aria-valuemin', String(MIN_W));
    handle.setAttribute('aria-valuemax', String(maxWidth()));
    handle.setAttribute('aria-valuenow', String(w));
  }
}

function setSideWidth(w) {
  sideWidth = clampWidth(w);
  applySideWidth();
  storageSet(WIDTH_KEY, String(sideWidth));
}

function initSideResize() {
  sideWidth = Number(storageGet(WIDTH_KEY, DEFAULT_W)) || DEFAULT_W;
  applySideWidth();
  window.addEventListener('resize', applySideWidth);
  const handle = $('side-resize');
  if (!handle) return;
  let startLeft = 0;
  const move = (e) => {
    sideWidth = clampWidth(e.clientX - startLeft);
    applySideWidth();
  };
  const stop = () => {
    document.body.classList.remove('is-side-resizing');
    handle.removeEventListener('pointermove', move);
    handle.removeEventListener('pointerup', stop);
    handle.removeEventListener('pointercancel', stop);
    storageSet(WIDTH_KEY, String(sideWidth));
  };
  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    startLeft = $('sidebar').getBoundingClientRect().left;
    handle.setPointerCapture(e.pointerId);
    document.body.classList.add('is-side-resizing');
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
  });
  handle.addEventListener('dblclick', () => setSideWidth(DEFAULT_W));
  handle.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const step = e.shiftKey ? 64 : 24;
    setSideWidth(sideWidth + (e.key === 'ArrowRight' ? step : -step));
  });
}

// ─── Chats grip: drag up/down to split the sidebar between Projects and Chats ─

const SPLIT_KEY = 'jarvis-side-projects-h';
const MIN_PROJECTS_H = 48;
let projectsH = null; // null = automatic layout

/** Tallest the Projects list may get: everything in the Chats section below its header. */
function maxProjectsH(list) {
  const scroll = document.querySelector('.side-scroll');
  const head = document.querySelector('.side-recent > .sec-head');
  if (!scroll || !head) return list.clientHeight;
  // .side-recent is the last block in .side-scroll, so everything below its
  // header (scope, search, list — or any overflow, as a negative) is ours to take.
  const room = scroll.getBoundingClientRect().bottom - head.getBoundingClientRect().bottom;
  return list.getBoundingClientRect().height + room;
}

function applySplit() {
  const scroll = document.querySelector('.side-scroll');
  const list = $('projects-list');
  const grip = $('chats-grip');
  if (!scroll || !list) return;
  if (projectsH == null) {
    scroll.classList.remove('has-custom-split');
    scroll.style.removeProperty('--projects-h');
    grip?.removeAttribute('aria-valuenow');
    return;
  }
  // Measure the room before switching layouts, then clamp: down to the Chats header, up to MIN.
  const max = maxProjectsH(list);
  scroll.classList.add('has-custom-split');
  const h = Math.max(MIN_PROJECTS_H, Math.min(projectsH, max));
  scroll.style.setProperty('--projects-h', `${Math.round(h)}px`);
  projectsH = h;
  grip?.setAttribute('aria-valuenow', String(Math.round(h)));
}

function setSplit(h, persist = true) {
  projectsH = h == null ? null : Math.max(MIN_PROJECTS_H, Math.round(h));
  applySplit();
  if (persist) storageSet(SPLIT_KEY, projectsH == null ? '' : String(projectsH));
}

function initChatsGrip() {
  const grip = $('chats-grip');
  const list = $('projects-list');
  if (!grip || !list) return;
  const saved = Number(storageGet(SPLIT_KEY, ''));
  projectsH = saved > 0 ? saved : null;
  applySplit();
  // Re-clamp when the sidebar's height changes (window resize, settings block
  // hidden/shown) or the Projects section grows (restore bar, header wraps).
  if (typeof ResizeObserver === 'function') {
    let queued = false;
    const ro = new ResizeObserver(() => {
      if (queued || projectsH == null) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; applySplit(); });
    });
    const scroll = document.querySelector('.side-scroll');
    if (scroll) ro.observe(scroll);
    const sec = $('projects-sec');
    if (sec) ro.observe(sec);
  } else {
    window.addEventListener('resize', applySplit);
  }
  let startY = 0;
  let startH = 0;
  const move = (e) => {
    // Dragging up pulls the boundary up: Projects shrinks, Chats grows.
    projectsH = startH + (e.clientY - startY);
    applySplit();
  };
  const stop = () => {
    document.body.classList.remove('is-grip-dragging');
    grip.removeEventListener('pointermove', move);
    grip.removeEventListener('pointerup', stop);
    grip.removeEventListener('pointercancel', stop);
    if (projectsH != null) storageSet(SPLIT_KEY, String(projectsH));
  };
  grip.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    startY = e.clientY;
    startH = list.clientHeight;
    grip.setPointerCapture(e.pointerId);
    document.body.classList.add('is-grip-dragging');
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', stop);
    grip.addEventListener('pointercancel', stop);
  });
  grip.addEventListener('dblclick', () => setSplit(null));
  grip.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
    e.preventDefault();
    const step = e.shiftKey ? 64 : 24;
    setSplit(list.clientHeight + (e.key === 'ArrowDown' ? step : -step));
  });
}

/** The top bar's panel button and Alt+B: a drawer on phones, a collapse on desktops. */
export function toggleSidebar() {
  if (window.matchMedia('(max-width: 960px)').matches) {
    if (document.body.classList.contains('side-open')) closeSidebar();
    else openSidebar();
    return;
  }
  setCollapsed(!document.body.classList.contains('side-collapsed'));
}

// ─── Rendering ────────────────────────────────────────────────────────────

function renderSwitches(s) {
  const set = (id, checked, pendingKey, disabled = false) => {
    const el = $(id);
    if (!el) return;
    el.checked = !!checked;
    el.disabled = disabled;
    el.classList.toggle('is-pending', !!pendingKey && s.pendingToggle === pendingKey);
    el.closest('.switch-row')?.classList.toggle('is-disabled', disabled);
  };
  set('sw-thinking', s.session.show_internal, 'show_internal');
  set('sw-auto', s.session.auto_approve, 'auto_approve');
}

function renderUsage(s) {
  // The first numbers (page load, a resumed session) just appear; only
  // changes while connected count up.
  const animate = { animate: s.connected };
  countTo($('use-in'), s.session.tokens_in, formatCount, animate);
  countTo($('use-out'), s.session.tokens_out, formatCount, animate);
  countTo($('use-tools'), s.session.tool_calls, formatCount, animate);
  $('use-out').title = `${s.session.tokens_out} output tokens`;
  // Prompt cache (Anthropic, Codex): how much of the latest prompt was
  // served from cache — cheaper and faster than sending it fresh.
  const tin = Number(s.session.tokens_in) || 0;
  const cached = Number(s.session.tokens_cache_read) || 0;
  const pct = cached && tin ? Math.min(100, Math.floor((cached * 100) / tin)) : 0;
  const tip = pct
    ? `${tin} input tokens in the latest prompt — ${cached} (${pct}%) read from the prompt cache`
    : `${tin} input tokens`;
  $('use-in').title = tip;
  const badge = $('use-in-cache');
  if (badge) {
    badge.hidden = !pct;
    badge.textContent = pct ? `⚡${pct}%` : '';
    badge.title = tip;
  }
}

function renderThemeButton() {
  const btn = $('theme-btn');
  if (!btn) return;
  const light = isLightTheme();
  if (btn.dataset.theme === String(light)) return;
  const first = !btn.dataset.theme;
  btn.dataset.theme = String(light);
  btn.innerHTML = icon(light ? 'moon' : 'sun');
  if (!first) {
    animateEl(btn.firstElementChild, [
      { transform: 'rotate(-120deg) scale(0.4)', opacity: 0 },
      { transform: 'none', opacity: 1 },
    ], { duration: 520, easing: SPRING });
  }
  btn.setAttribute('aria-label', light ? 'Switch to dark theme' : 'Switch to light theme');
  btn.title = btn.getAttribute('aria-label');
}

function render(s) {
  renderSwitches(s);
  renderUsage(s);
  renderThemeButton();
  // New session, or the current one just got its title: the list is stale.
  if (s.session.session_id !== lastSessionId || s.session.session_title !== lastTitle) {
    lastSessionId = s.session.session_id;
    lastTitle = s.session.session_title;
    refreshRecent();
  }
  markActiveRecent(s.session.session_id);
}

// ─── Recent sessions ──────────────────────────────────────────────────────
//
//   CHATS                                   View all
//   [ harness 103 | All 260 ]
//   [🔍 Find a chat ……………… ]
//   PINNED · TODAY · YESTERDAY …   (This project)
//   ▾ harness 103 · ▸ billing 12 … (All: one group per project)

const SCOPE_KEY = 'jarvis-recent-scope';
const FOLDED_KEY = 'jarvis-recent-folded';
const PER_GROUP = 5;
const R = { scope: storageGet(SCOPE_KEY, 'project') === 'all' ? 'all' : 'project', q: '', data: null, seq: 0 };

function readFolded() {
  try {
    const v = JSON.parse(storageGet(FOLDED_KEY, '[]'));
    return new Set(Array.isArray(v) ? v : []);
  } catch {
    return new Set();
  }
}
let folded = readFolded();

function markActiveRecent(sid) {
  document.querySelectorAll('#recent-list .recent-row').forEach((row) => {
    row.classList.toggle('is-active', row.dataset.sid === String(sid));
  });
}

function rowHtml(x, i) {
  const w = whereOf(x);
  const live = x.openIn
    ? `<span class="rr-tag is-live" title="On screen in ${escapeHtml(x.openIn.project)} — click to switch there">${escapeHtml(x.openIn.project)}</span>`
    : '';
  const away = !live && (w.kind === 'folder' || w.kind === 'launch') && (R.q || R.scope !== 'all')
    ? `<span class="rr-tag" title="${escapeHtml(x.cwd_display)}">${escapeHtml(x.project)}</span>` : '';
  const tab = x.tab ? `<span class="rr-tag is-tab" title="Open in another tab">${icon('app-window')}tab</span>` : '';
  const count = x.msg_count ? `${x.msg_count} msg${x.msg_count === 1 ? '' : 's'}` : '';
  const meta = [relTime(x.updated_at), count].filter(Boolean).join(' · ');
  const snip = x.snippet?.text
    ? `<span class="rr-snip">${escapeHtml(x.snippet.text.slice(0, x.snippet.at))}<mark>${escapeHtml(x.snippet.text.slice(x.snippet.at, x.snippet.at + x.snippet.len))}</mark>${escapeHtml(x.snippet.text.slice(x.snippet.at + x.snippet.len))}</span>`
    : '';
  const tip = w.kind === 'folder' || w.kind === 'launch' ? `${x.title}\nOpens in ${x.cwd_display}` : x.title;
  return `
    <div class="recent-item${x.pinned ? ' is-pinned' : ''}" role="listitem" data-sid="${x.id}" style="--i:${Math.min(i, 8)}">
      <button type="button" class="recent-row${x.active ? ' is-active' : ''}" data-sid="${x.id}" title="${escapeHtml(tip)}">
        <span class="rr-dot${x.tab || x.openIn ? ' is-open' : ''}" aria-hidden="true"></span>
        <span class="rr-body">
          <span class="rr-title">${escapeHtml(x.title)}</span>
          ${snip}
          <span class="rr-meta">${live}${away}${tab}${escapeHtml(meta)}</span>
        </span>
      </button>
      ${x.pinned ? `<span class="rr-pin" aria-label="Pinned">${icon('pin')}</span>` : ''}
      <button type="button" class="rr-more" data-more="${x.id}" aria-haspopup="menu" aria-label="More for “${escapeHtml(x.title)}”" title="More">${icon('ellipsis')}</button>
    </div>`;
}

function groupHead(label, { key = '', count = null, folder = false, open = true, here = false, title = '' } = {}) {
  if (!folder) return `<div class="recent-group" role="presentation">${escapeHtml(label)}</div>`;
  return `<button type="button" class="recent-folder${here ? ' is-here' : ''}" data-fold="${escapeHtml(key)}" aria-expanded="${open}" title="${escapeHtml(title)}">
      ${icon(open ? 'chevron-down' : 'chevron-right')}${icon('folder')}<span class="rf-name">${escapeHtml(label)}</span>${here ? '<span class="rf-here">here</span>' : ''}<em>${count ?? ''}</em>
    </button>`;
}

function recentHtml(rows, data) {
  let i = 0;
  if (R.q) {
    if (!rows.length) return `<p class="recent-empty">No chat matches “${escapeHtml(R.q)}”.${R.scope === 'project' ? ' <button type="button" class="link-btn" data-recent-scope="all">Search all projects</button>' : ''}</p>`;
    return rows.map((x) => rowHtml(x, i++)).join('');
  }
  if (R.scope === 'project') {
    if (!rows.length) {
      return `<p class="recent-empty">No chats in ${escapeHtml(data?.current_name || 'this project')} yet. <button type="button" class="link-btn" data-recent-scope="all">Show all projects</button></p>`;
    }
    let last = '';
    return rows.map((x) => {
      const label = x.pinned ? 'Pinned' : dayLabel(x.updated_at);
      const head = label !== last ? groupHead(label) : '';
      last = label;
      return head + rowHtml(x, i++);
    }).join('');
  }
  if (!rows.length) return '<p class="recent-empty">Saved sessions show up here.</p>';
  // All projects: one foldable group per folder (this one first), newest chats first.
  const counts = new Map((data?.folders || []).map((f) => [f.cwd, f]));
  const groups = new Map();
  const here = data?.current_cwd || '';
  if (rows.some((x) => x.cwd === here)) groups.set(here, []);
  for (const x of rows) {
    if (!groups.has(x.cwd)) groups.set(x.cwd, []);
    groups.get(x.cwd).push(x);
  }
  return [...groups.entries()].map(([cwd, list]) => {
    const f = counts.get(cwd);
    const key = cwd || '~none';
    const open = !folded.has(key);
    const name = cwd ? (list[0]?.project || f?.name || cwd) : 'Folder not recorded';
    const total = f?.count ?? list.length;
    const shown = list.slice(0, PER_GROUP);
    const rest = total - shown.length;
    const more = rest > 0
      ? `<button type="button" class="recent-more" data-folder="${escapeHtml(cwd)}">${icon('chevron-right')}<span>${rest} more in ${escapeHtml(name)}</span></button>`
      : '';
    return `<div class="recent-set${open ? '' : ' is-folded'}">
      ${groupHead(name, { key, count: total, folder: true, open, here: cwd === here && !!cwd, title: f?.display || '' })}
      <div class="recent-set-body">${open ? shown.map((x) => rowHtml(x, i++)).join('') + more : ''}</div>
    </div>`;
  }).join('');
}

function renderScope(data) {
  const box = $('recent-scope');
  if (!box) return;
  const name = data?.current_name || 'This project';
  const html = `<div class="seg recent-seg" role="group" aria-label="Show chats from">
      <button type="button" data-recent-scope="project" aria-pressed="${R.scope === 'project'}" title="${escapeHtml(data?.current_display || '')}">${icon('folder-open')}<span>${escapeHtml(name)}</span>${data?.counts?.project ? `<em>${data.counts.project}</em>` : ''}</button>
      <button type="button" data-recent-scope="all" aria-pressed="${R.scope === 'all'}">${icon('history')}<span>All</span>${data?.counts?.all ? `<em>${data.counts.all}</em>` : ''}</button>
    </div>`;
  if (box._html !== html) {
    box.innerHTML = html;
    box._html = html;
  }
}

async function loadRecent() {
  const list = $('recent-list');
  if (!list) return;
  if (!list.childElementCount) list.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  const seq = ++R.seq;
  try {
    const data = await fetchSessionList({
      scope: R.scope, q: R.q.trim(), limit: R.q ? 30 : R.scope === 'all' ? 80 : 40, folders: 1,
    });
    if (seq !== R.seq) return;
    R.data = data;
    renderScope(data);
    const sessions = (data.sessions || []).map((x) => ({
      ...x,
      // Still the live chat of another running project: open it there, never twice.
      openIn: projectForSession(x.id),
      // Its project was closed: say where it came from.
      from: closedProjectFor(x.id),
    }));
    const sig = JSON.stringify([R.scope, R.q, [...folded], data.counts,
      sessions.map((x) => [x.id, x.title, x.msg_count, x.updated_at, x.pinned, x.tab?.id, x.openIn?.id, x.snippet?.text])]);
    if (sig === recentSig && list.querySelector('.recent-row, .recent-empty')) return;
    recentSig = sig;
    list._rows = sessions;
    list.innerHTML = recentHtml(sessions, data);
    markActiveRecent(store.session.session_id);
  } catch {
    if (seq !== R.seq) return;
    if (!list.querySelector('.recent-row')) list.innerHTML = '<p class="recent-empty">Could not load sessions.</p>';
  }
}

export const refreshRecent = debounce(loadRecent, 250);

function rowFor(el) {
  const sid = Number(el.closest('[data-sid]')?.dataset.sid || 0);
  return ($('recent-list')?._rows || []).find((x) => x.id === sid) || null;
}

async function onRecentClick(e) {
  const scope = e.target.closest('[data-recent-scope]');
  if (scope) {
    if (scope.getAttribute('aria-pressed') === 'true') return;
    R.scope = scope.dataset.recentScope;
    storageSet(SCOPE_KEY, R.scope);
    recentSig = '';
    loadRecent();
    return;
  }
  const fold = e.target.closest('[data-fold]');
  if (fold) {
    const key = fold.dataset.fold;
    if (folded.has(key)) folded.delete(key);
    else folded.add(key);
    storageSet(FOLDED_KEY, JSON.stringify([...folded]));
    recentSig = '';
    loadRecent();
    return;
  }
  const more = e.target.closest('.recent-more');
  if (more) {
    closeOnNarrow();
    openSessionsDialog('', more.dataset.folder ?? '');
    return;
  }
  const menuBtn = e.target.closest('[data-more]');
  if (menuBtn) {
    e.stopPropagation();
    const x = rowFor(menuBtn);
    if (!x) return;
    const res = await sessionMenu(menuBtn, x, { titleEl: menuBtn.closest('.recent-item')?.querySelector('.rr-title') });
    if (res?.ok || res?.renamed) closeOnNarrow();
    return;
  }
  const row = e.target.closest('.recent-row');
  if (!row) return;
  const x = rowFor(row);
  if (!x) return;
  if (x.openIn) {
    switchProject(x.openIn.id);
    return;
  }
  if (e.metaKey || e.ctrlKey) {
    if (whereOf(x).kind === 'here') openSession(x, { where: 'tab' });
    return;
  }
  if (x.active) {
    closeOnNarrow();
    return;
  }
  row.classList.add('is-active');
  const res = await openSession(x);
  if (res?.ok) closeOnNarrow();
  else markActiveRecent(store.session.session_id);
}

function onRecentAux(e) {
  if (e.button !== 1) return; // middle click: open in a new tab, like a browser
  const row = e.target.closest('.recent-row');
  const x = row && rowFor(row);
  if (!x || x.active || whereOf(x).kind !== 'here') return;
  e.preventDefault();
  openSession(x, { where: 'tab' });
}

function onRecentKey(e) {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  const search = $('recent-search');
  const rows = [...($('recent-list')?.querySelectorAll('.recent-row') || [])];
  if (!rows.length) return;
  const i = rows.indexOf(document.activeElement);
  if (e.target !== search && i === -1) return;
  e.preventDefault();
  const next = e.target === search ? (e.key === 'ArrowDown' ? 0 : rows.length - 1) : i + (e.key === 'ArrowDown' ? 1 : -1);
  if (next < 0) search?.focus();
  else rows[Math.min(next, rows.length - 1)].focus();
}

function initRecent() {
  const sec = document.querySelector('.side-recent');
  sec?.addEventListener('click', onRecentClick);
  sec?.addEventListener('auxclick', onRecentAux);
  sec?.addEventListener('keydown', onRecentKey);
  const search = $('recent-search');
  search?.addEventListener('input', () => {
    R.q = search.value;
    refreshRecent();
  });
  search?.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && search.value) {
      e.stopPropagation();
      search.value = '';
      R.q = '';
      loadRecent();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      $('recent-list')?.querySelector('.recent-row')?.click();
    }
  });
}

// ─── Hide / show the switches + usage block ───────────────────────────────

const BOTTOM_KEY = 'jarvis-side-bottom-hidden';

function setBottomHidden(hidden) {
  $('sidebar')?.classList.toggle('bottom-hidden', hidden);
  $('side-bottom-toggle')?.setAttribute('aria-expanded', String(!hidden));
}

function initBottomToggle() {
  setBottomHidden(storageGet(BOTTOM_KEY, '0') === '1');
  $('side-bottom-toggle')?.addEventListener('click', () => {
    const hidden = !$('sidebar').classList.contains('bottom-hidden');
    setBottomHidden(hidden);
    storageSet(BOTTOM_KEY, hidden ? '1' : '0');
  });
}

// ─── Init ─────────────────────────────────────────────────────────────────

export function initSidebar({ onOpenPicker }) {
  openPicker = onOpenPicker;

  $('menu-btn')?.addEventListener('click', toggleSidebar);
  setCollapsed(storageGet(COLLAPSE_KEY, '0') === '1');
  initSideResize();
  initChatsGrip();
  $('side-close')?.addEventListener('click', closeSidebar);
  $('scrim')?.addEventListener('click', closeSidebar);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.body.classList.contains('side-open')) closeSidebar();
  });

  $('new-chat')?.addEventListener('click', async () => {
    const res = await newChat();
    if (res.ok) {
      closeOnNarrow();
      if (!res.beside) $('prompt')?.focus(); // beside: the "Starting a new chat" dialog is up
    }
  });
  $('all-sessions')?.addEventListener('click', () => { closeOnNarrow(); openSessionsDialog(R.q.trim() || (R.scope === 'all' ? 'all' : '')); });
  initRecent();
  $('open-skills')?.addEventListener('click', () => { closeOnNarrow(); openPicker('skill'); });
  $('open-commands')?.addEventListener('click', () => { closeOnNarrow(); openPicker('command'); });
  $('open-mcp')?.addEventListener('click', () => { closeOnNarrow(); openPicker('mcp'); });
  initBottomToggle();
  $('theme-btn')?.addEventListener('click', toggleTheme);

  // The native flip already matches the optimistic store value; a failed
  // save reverts the store, and render() puts the checkbox back.
  const wire = (id, key) => $(id)?.addEventListener('change', () => toggleSetting(key));
  wire('sw-thinking', 'show_internal');
  wire('sw-auto', 'auto_approve');

  onSessionChange(refreshRecent);
  document.addEventListener('jarvis:sessions-changed', refreshRecent);
  subscribe(render);
  render(store);
  renderThemeButton();
}
