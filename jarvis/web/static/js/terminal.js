/** Terminal — a real shell, docked at the bottom of the main column.
 *
 * The top-bar button (or Ctrl + `) opens a resizable panel under the
 * composer: the transcript shrinks and the composer sits on top of the panel. Each
 * tab is a live PTY shell on the computer (terminal_api.py): xterm.js draws
 * it, keystrokes POST to the server, output is long-polled back.
 *
 * Shells belong to the Jarvis that runs them, not to this page: hiding the
 * panel, reloading the page or switching project leaves them running, and
 * opening the panel again re-attaches to them (their output replays from the
 * server's buffer). Only a tab's × ends a shell.
 */
import { $, storageGet, storageSet, showToast, copyText, haptic, debounce } from './utils.js';
import { api } from './api.js';
import { icon } from './icons.js';
import { isLightTheme } from './theme.js';

const HEIGHT_KEY = 'jarvis-term-h';
const MIN_H = 140;
const DEFAULT_H = 300;
const MIN_CHAT = 120;   // transcript rows always left above the panel
const MAX_SESSIONS = 8;
const COARSE = window.matchMedia?.('(pointer: coarse)');

const state = {
  open: false,
  max: false,
  split: false,
  height: Math.max(MIN_H, Number(storageGet(HEIGHT_KEY, DEFAULT_H)) || DEFAULT_H),
  sessions: [],   // { id, title, term, fit, search, pane, cursor, exited, disposed, queue, flush }
  active: null,
  secondary: null,
  allowed: true,
  remoteLive: false,  // shells already running on the server before this page attached
};

const el = {};
let built = false;
let attaching = null;   // the in-flight re-attach / first spawn

// ─── Terminal look ───────────────────────────────────────────────────────
const DARK_ANSI = {
  black: '#3b3b46', red: '#ff6b6b', green: '#5fdc9a', yellow: '#e5c07b',
  blue: '#61afef', magenta: '#c678dd', cyan: '#56b6c2', white: '#c8c8d4',
  brightBlack: '#6b6b7b', brightRed: '#ff8787', brightGreen: '#7ef0b4',
  brightYellow: '#f2d27f', brightBlue: '#82c4ff', brightMagenta: '#d79cf0',
  brightCyan: '#7fd4df', brightWhite: '#f2f2f8',
};
const LIGHT_ANSI = {
  black: '#24292f', red: '#cf222e', green: '#116329', yellow: '#7d4e00',
  blue: '#0969da', magenta: '#8250df', cyan: '#1b7c83', white: '#6e7781',
  brightBlack: '#57606a', brightRed: '#a40e26', brightGreen: '#1a7f37',
  brightYellow: '#633c01', brightBlue: '#218bff', brightMagenta: '#a475f9',
  brightCyan: '#3192aa', brightWhite: '#8c959f',
};

function theme() {
  // The pane colour is defined on the panel (terminal.css), not on :root.
  const css = getComputedStyle(el.panel || document.documentElement);
  const v = (name, fallback) => (css.getPropertyValue(name) || '').trim() || fallback;
  const light = isLightTheme();
  const bg = v('--term-pane', light ? '#ffffff' : '#131318');
  return {
    background: bg,
    foreground: v('--text', light ? '#1f2328' : '#e6e6ee'),
    cursor: v('--accent', '#8b5cf6'),
    cursorAccent: bg,
    selectionBackground: v('--accent-soft', 'rgba(139,92,246,.28)'),
    ...(light ? LIGHT_ANSI : DARK_ANSI),
  };
}

function monoFont() {
  const font = getComputedStyle(document.documentElement).getPropertyValue('--font-mono').trim();
  return font || 'ui-monospace, SFMono-Regular, Menlo, monospace';
}

// ─── Build the shell around the panel ────────────────────────────────────
function build() {
  if (built) return;
  built = true;
  el.main = document.querySelector('.main');
  el.panel = $('term-panel');
  el.body = $('term-body');
  el.tabs = $('term-tabs');
  el.empty = $('term-empty');
  el.btn = $('term-btn');
  el.dot = $('term-live-dot');
  el.handle = $('term-resize');
  el.find = $('term-find');
  el.findInput = $('term-find-input');
  el.maxBtn = $('term-max');

  applyHeight();
  el.btn.addEventListener('click', () => (state.open ? hide() : open()));
  $('term-hide').addEventListener('click', () => hide({ focusComposer: true }));
  $('term-add').addEventListener('click', () => spawn());
  $('term-empty-new').addEventListener('click', () => spawn());
  $('term-split').addEventListener('click', () => setSplit(!state.split));
  el.maxBtn.addEventListener('click', () => setMax(!state.max));
  $('term-clear').addEventListener('click', () => clearActive());
  $('term-copy').addEventListener('click', () => copySelection());
  $('term-paste').addEventListener('click', () => pasteInto());
  $('term-search').addEventListener('click', () => setFind(el.find.hidden));
  $('term-find-close').addEventListener('click', () => setFind(false));
  $('term-find-next').addEventListener('click', () => findNext());
  $('term-find-prev').addEventListener('click', () => findPrev());
  el.findInput.addEventListener('input', () => searchNow());
  el.findInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setFind(false); }
    else if (e.key === 'Enter') { e.preventDefault(); e.shiftKey ? findPrev() : findNext(); }
  });

  // Keys typed into a shell belong to the shell: Option/Alt+B, D, F … are word
  // moves there, not the page's Alt shortcuts (sidebar, inspector …).
  el.panel.addEventListener('keydown', (e) => {
    if (e.altKey && !e.metaKey && !e.ctrlKey) e.stopPropagation();
  });

  bindResize();
  // The app can switch dark/light: repaint every terminal in the new palette.
  new MutationObserver(() => {
    const paint = theme();
    for (const s of state.sessions) {
      if (!s.disposed) s.term.options.theme = paint;
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-variant', 'data-accent', 'style'] });
  window.addEventListener('resize', debounce(() => { applyHeight(); fitAll(); }, 120));
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => fitAll()).observe(el.panel);
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey && (e.key === '`' || e.code === 'Backquote')) {
      e.preventDefault();
      toggle();
    }
  });
}

// ─── Panel open / close ─────────────────────────────────────────────────
/** Ctrl + `: open, jump into the terminal, or hide it — like an editor's panel. */
function toggle() {
  if (!state.open) { open(); return; }
  // Open but typing elsewhere: Ctrl+` jumps into the terminal first, like an editor's panel.
  if (!el.panel.contains(document.activeElement)) { focusActive(); return; }
  hide({ focusComposer: true });
}

function open() {
  build();
  if (!state.allowed) {
    showToast('The terminal is only available on this computer or your own network.', true);
    return;
  }
  state.open = true;
  el.panel.hidden = false;
  el.panel.inert = false;
  el.panel.setAttribute('aria-hidden', 'false');
  el.btn.setAttribute('aria-expanded', 'true');
  el.btn.classList.add('is-on');
  document.body.classList.add('term-open');
  document.body.classList.toggle('term-max', state.max);
  if (!state.sessions.length) attach();
  else { syncPanes(); requestAnimationFrame(() => { fitAll(); focusActive(); }); }
  syncDot();
}

function hide({ focusComposer = false } = {}) {
  if (!state.open) return;
  const hadFocus = el.panel.contains(document.activeElement);
  state.open = false;
  el.panel.setAttribute('aria-hidden', 'true');
  el.panel.inert = true;
  el.panel.hidden = true;
  el.btn.setAttribute('aria-expanded', 'false');
  el.btn.classList.remove('is-on');
  document.body.classList.remove('term-open', 'term-max');
  syncDot();
  if (hadFocus || focusComposer) $('prompt')?.focus();
}

function syncDot() {
  if (!el.dot) return;
  const live = state.sessions.length ? state.sessions.some((s) => !s.exited) : state.remoteLive;
  el.dot.hidden = state.open || !live;
}

function setMax(on) {
  state.max = on;
  el.panel.classList.toggle('is-max', on);
  document.body.classList.toggle('term-max', on && state.open);
  el.maxBtn.setAttribute('aria-pressed', String(on));
  el.maxBtn.title = on ? 'Restore size' : 'Maximize';
  el.maxBtn.setAttribute('aria-label', on ? 'Restore terminal size' : 'Maximize terminal');
  el.maxBtn.innerHTML = icon(on ? 'minimize-2' : 'maximize-2');
  // Maximised hides the composer: don't leave focus in a box you can't see.
  const dock = document.querySelector('.dock');
  if (on && state.open && dock?.contains(document.activeElement)) focusActive();
  requestAnimationFrame(() => fitAll());
}

function setSplit(on) {
  state.split = on;
  $('term-split').setAttribute('aria-pressed', String(on));
  if (on) {
    if (!state.secondary) {
      state.secondary = state.sessions.find((s) => s.id && s.id !== state.active)?.id || null;
    }
    if (!state.secondary) { spawn({ secondary: true }); return; }
  } else {
    state.secondary = null;
  }
  renderTabs();
  syncPanes();
}

/** Height from the saved value, never so tall the transcript disappears. */
function maxHeight() {
  const main = el.main?.clientHeight || window.innerHeight;
  const cs = el.main ? getComputedStyle(el.main) : null;
  const dock = parseFloat(cs?.getPropertyValue('--dock-h')) || 140;
  const top = parseFloat(cs?.getPropertyValue('--top-h')) || 48;
  return Math.max(MIN_H, main - dock - top - MIN_CHAT);
}

function applyHeight() {
  const h = Math.min(state.height, maxHeight());
  el.panel?.style.setProperty('--term-h', `${Math.round(h)}px`);
}

// ─── Sessions ────────────────────────────────────────────────────────────
function sessionById(id) { return state.sessions.find((s) => s.id === id) || null; }
function activeSession() { return sessionById(state.active); }

/** First open on this page: re-attach to the shells this Jarvis already runs
 *  (a reload, another device), or start one. */
function attach() {
  if (attaching) return attaching;
  attaching = (async () => {
    let list = [];
    try {
      const res = await api('/api/terminal/list');
      state.allowed = res.allowed !== false;
      list = Array.isArray(res.terminals) ? res.terminals : [];
    } catch { /* fall through: spawn reports the error */ }
    if (!state.allowed) {
      hide();
      syncAllowed();
      showToast('The terminal is only available on this computer or your own network.', true);
      return;
    }
    for (const t of list) {
      if (t.exited) api('/api/terminal/close', 'POST', { id: t.id }).catch(() => {});
    }
    const live = list.filter((t) => !t.exited && !sessionById(t.id));
    if (!live.length) {
      if (!state.sessions.length) await spawn();
      return;
    }
    for (const t of live) adopt(t);
    if (!state.active) state.active = state.sessions[0]?.id || null;
    renderTabs();
    syncPanes();
    requestAnimationFrame(() => { fitAll(); focusActive(); });
  })().finally(() => { attaching = null; syncEmpty(); syncDot(); });
  return attaching;
}

/** xterm view + pane for one shell (not yet connected to the server). */
function makeView(title = 'Terminal') {
  const pane = document.createElement('div');
  pane.className = 'term-pane';
  const mount = document.createElement('div');
  mount.className = 'term-xterm';
  pane.append(mount);
  el.body.append(pane);

  const term = new window.Terminal({
    cursorBlink: true,
    cursorStyle: 'bar',
    fontFamily: monoFont(),
    fontSize: 12.5,
    lineHeight: 1.25,
    letterSpacing: 0,
    scrollback: 8000,
    allowProposedApi: true,
    macOptionIsMeta: true,
    theme: theme(),
    rightClickSelectsWord: true,
  });
  let fit = null;
  let search = null;
  try {
    if (window.FitAddon?.FitAddon) { fit = new window.FitAddon.FitAddon(); term.loadAddon(fit); }
    if (window.SearchAddon?.SearchAddon) { search = new window.SearchAddon.SearchAddon(); term.loadAddon(search); }
  } catch (err) { console.warn('terminal addon failed', err); }
  term.open(mount);
  const s = {
    id: null, title, term, fit, search, pane, cursor: 0,
    exited: false, disposed: false, queue: '', flush: 0,
  };
  pane.addEventListener('mousedown', () => { if (s.id && s.id !== state.active) selectTab(s.id, { focus: false }); });
  return s;
}

/** A shell the server already runs. */
function adopt(t) {
  if (!window.Terminal) return null;
  const s = makeView(t.title || 'Terminal');
  s.id = t.id;
  state.sessions.push(s);
  wireSession(s);
  poll(s);
  requestAnimationFrame(() => { try { s.fit?.fit(); } catch { /* not laid out */ } sendResize(s); });
  return s;
}

async function spawn({ secondary = false } = {}) {
  build();
  if (state.sessions.length >= MAX_SESSIONS) {
    showToast(`You can have up to ${MAX_SESSIONS} terminals — close one first.`, true);
    return;
  }
  if (!window.Terminal) {
    showToast('The terminal engine didn’t load — reload the page.', true);
    return;
  }
  const s = makeView();
  state.sessions.push(s);
  renderTabs();
  syncPanes();
  await new Promise((r) => requestAnimationFrame(r));
  try { s.fit?.fit(); } catch { /* not laid out */ }

  try {
    const res = await api('/api/terminal/open', 'POST', {
      cols: s.term.cols || 80, rows: s.term.rows || 24,
    });
    if (!res.ok) throw new Error(res.error || 'Could not start the shell.');
    if (s.disposed) {   // closed (or project switched) while it started
      api('/api/terminal/close', 'POST', { id: res.terminal.id }).catch(() => {});
      return;
    }
    s.id = res.terminal.id;
    s.title = res.terminal.title || s.title;
    // A split opens the new shell on the right and puts you in it.
    if (secondary && state.active) state.secondary = state.active;
    state.active = s.id;
    renderTabs();
    syncPanes();
    focusActive();
    wireSession(s);
    poll(s);
  } catch (err) {
    disposeView(s);
    renderTabs();
    syncPanes();
    const local = err?.payload?.code === 'local_only';
    if (local) { state.allowed = false; syncAllowed(); hide(); }
    showToast(local ? 'The terminal is only available on this computer or your own network.'
      : (err.message || 'The terminal didn’t start.'), true);
  }
}

function wireSession(s) {
  s.term.onData((data) => {
    s.queue += data;
    if (!s.flush) s.flush = setTimeout(() => flushInput(s), 8);
  });
  s.sendResize = debounce(() => sendResize(s), 150);
  s.term.onResize(() => s.sendResize());
  s.term.attachCustomKeyEventHandler((e) => {
    if (e.type !== 'keydown') return true;
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.shiftKey && (e.key === 'C' || e.key === 'c')) { copySelection(); return false; }
    if (mod && e.shiftKey && (e.key === 'V' || e.key === 'v')) { pasteInto(); return false; }
    if (e.metaKey && !e.shiftKey && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); setFind(true); return false; }
    if (e.ctrlKey && !e.shiftKey && !e.altKey && (e.key === '`' || e.code === 'Backquote')) { hide({ focusComposer: true }); return false; }
    return true;
  });
  s.pane.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    if (s.term.hasSelection()) copySelection();
    else pasteInto();
  });
}

function flushInput(s) {
  s.flush = 0;
  if (!s.id || s.disposed) { s.queue = ''; return; }
  const data = s.queue;
  s.queue = '';
  if (!data) return;
  api('/api/terminal/input', 'POST', { id: s.id, data }).catch((err) => {
    if (err?.payload?.code === 'gone') markExited(s);
  });
}

function sendResize(s) {
  if (!s.id || s.disposed || !s.term.cols) return;
  api('/api/terminal/resize', 'POST', { id: s.id, cols: s.term.cols, rows: s.term.rows }).catch(() => {});
}

function markExited(s) {
  if (s.exited) return;
  s.exited = true;
  renderTabs();
  syncDot();
}

async function poll(s) {
  let backoff = 300;
  while (!s.disposed) {
    if (document.hidden || !state.open) { await sleep(500); continue; }
    try {
      const res = await api(
        `/api/terminal/read?id=${encodeURIComponent(s.id)}&cursor=${s.cursor}&wait=15000`,
      );
      if (s.disposed) return;
      if (!res.ok) {
        if (res.code === 'local_only') { s.term.write('\r\n\x1b[31mThe terminal is only available on this computer.\x1b[0m\r\n'); markExited(s); return; }
        throw new Error(res.error || 'read failed');
      }
      if (res.reset) s.term.reset();
      if (res.data) s.term.write(res.data);
      s.cursor = res.cursor;
      backoff = 300;
      if (res.exited) { markExited(s); return; }
    } catch (err) {
      if (s.disposed) return;
      if (err?.payload?.code === 'gone' || err?.status === 404) {
        s.term.write('\r\n\x1b[90m[terminal closed]\x1b[0m\r\n');
        markExited(s);
        return;
      }
      await sleep(backoff);
      backoff = Math.min(4000, backoff * 1.7);
    }
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Drop the view only (the shell keeps running unless killSession asked). */
function disposeView(s) {
  if (s.disposed) return;
  s.disposed = true;
  clearTimeout(s.flush);
  try { s.term.dispose(); } catch { /* already gone */ }
  s.pane.remove();
  state.sessions = state.sessions.filter((x) => x !== s);
  if (state.active === s.id) state.active = state.sessions.find((x) => x.id && x.id !== state.secondary)?.id || state.sessions[0]?.id || null;
  if (state.secondary === s.id || state.secondary === state.active) {
    state.secondary = state.split ? state.sessions.find((x) => x.id && x.id !== state.active)?.id || null : null;
  }
  if (!state.secondary && state.split) {
    state.split = false;
    $('term-split')?.setAttribute('aria-pressed', 'false');
  }
}

function killSession(s) {
  if (!s) return;
  if (s.id) api('/api/terminal/close', 'POST', { id: s.id }).catch(() => {});
  disposeView(s);
  renderTabs();
  syncPanes();
  syncDot();
  if (state.sessions.length) { fitAll(); focusActive(); }
}

function clearActive() {
  const s = activeSession();
  if (!s) return;
  s.term.clear();
  // Ask the shell to redraw its prompt (Ctrl+L) — only when nothing is typed after it.
  if (s.id && !s.exited) api('/api/terminal/input', 'POST', { id: s.id, data: '\x0c' }).catch(() => {});
  focusActive();
}

function focusActive() {
  const s = activeSession();
  if (s && !s.disposed) s.term.focus();
}

async function copySelection() {
  const s = activeSession();
  if (!s) return;
  const text = s.term.getSelection();
  if (!text) { showToast('Select some text in the terminal first.'); return; }
  await copyText(text);
  showToast('Copied');
  haptic(6);
}

async function pasteInto() {
  const s = activeSession();
  if (!s || !s.id || s.exited) return;
  let text = '';
  try { text = await navigator.clipboard.readText(); } catch { /* denied */ }
  if (!text) { showToast('Nothing to paste, or the browser blocked the clipboard.', true); return; }
  s.term.paste(text);   // bracketed paste when the shell asks for it; goes out via onData
  focusActive();
}

// ─── Find ────────────────────────────────────────────────────────────────
function setFind(open) {
  el.find.hidden = !open;
  $('term-search').setAttribute('aria-pressed', String(open));
  if (open) { el.findInput.focus(); el.findInput.select(); searchNow(); }
  else { clearSearch(); focusActive(); }
  requestAnimationFrame(() => fitAll());
}

function clearSearch() {
  for (const s of state.sessions) s.search?.clearDecorations?.();
}

function searchNow() {
  const s = activeSession();
  if (!s?.search) return;
  const q = el.findInput.value;
  if (!q) { s.search.clearDecorations?.(); return; }
  s.search.findNext(q, { incremental: true });
}

function findNext() {
  const s = activeSession();
  const q = el.findInput.value;
  if (s?.search && q) s.search.findNext(q);
}

function findPrev() {
  const s = activeSession();
  const q = el.findInput.value;
  if (s?.search && q) s.search.findPrevious(q);
}

// ─── Tabs + panes ────────────────────────────────────────────────────────
function renderTabs() {
  if (!el.tabs) return;
  el.tabs.replaceChildren();
  for (const s of state.sessions) {
    const tab = document.createElement('div');
    tab.className = 'term-tab';
    tab.setAttribute('role', 'tab');
    tab.tabIndex = s.id === state.active ? 0 : -1;
    tab.setAttribute('aria-selected', String(s.id === state.active));
    tab.classList.toggle('is-active', s.id === state.active);
    tab.classList.toggle('is-dead', s.exited);
    tab.classList.toggle('is-pending', !s.id);
    if (state.split && s.id && s.id === state.secondary) tab.classList.add('is-second');

    const dot = document.createElement('span');
    dot.className = 'term-tab-dot';
    dot.setAttribute('aria-hidden', 'true');

    const name = document.createElement('span');
    name.className = 'term-tab-name';
    name.textContent = s.title || 'Terminal';
    tab.title = s.exited ? `${s.title} — exited · double-click to rename` : `${s.title} · double-click to rename`;

    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'term-tab-x';
    x.tabIndex = -1;
    x.setAttribute('aria-label', `Close ${s.title || 'terminal'}`);
    x.title = 'Close (ends the shell)';
    x.innerHTML = icon('x');
    x.addEventListener('click', (e) => { e.stopPropagation(); killSession(s); });

    tab.append(dot, name, x);
    tab.addEventListener('click', () => { if (s.id) selectTab(s.id); });
    tab.addEventListener('auxclick', (e) => { if (e.button === 1) { e.preventDefault(); killSession(s); } });
    tab.addEventListener('dblclick', (e) => { e.stopPropagation(); renameTab(s, name); });
    tab.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (s.id) selectTab(s.id); }
      else if (e.key === 'F2') { e.preventDefault(); renameTab(s, name); }
      else if (e.key === 'Delete') { e.preventDefault(); killSession(s); }
      else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        const tabs = [...el.tabs.querySelectorAll('.term-tab')];
        const i = tabs.indexOf(tab) + (e.key === 'ArrowRight' ? 1 : -1);
        tabs[(i + tabs.length) % tabs.length]?.focus();
      }
    });
    el.tabs.append(tab);
  }
  syncEmpty();
}

function selectTab(id, { focus = true } = {}) {
  const s = sessionById(id);
  if (!s) return;
  if (state.split && id === state.secondary) {
    state.secondary = state.active;
  }
  state.active = id;
  renderTabs();
  syncPanes();
  if (focus) focusActive();
}

function renameTab(s, nameEl) {
  if (!s.id || !nameEl.isConnected) return;
  const input = document.createElement('input');
  input.className = 'term-tab-rename';
  input.value = s.title;
  input.maxLength = 40;
  input.setAttribute('aria-label', 'Terminal name');
  nameEl.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const commit = (save) => {
    if (done) return;
    done = true;
    const next = input.value.trim();
    if (save && next && next !== s.title) {
      s.title = next;
      api('/api/terminal/rename', 'POST', { id: s.id, title: next }).catch(() => {});
    }
    renderTabs();
    focusActive();
  };
  input.addEventListener('click', (e) => e.stopPropagation());
  input.addEventListener('dblclick', (e) => e.stopPropagation());
  input.addEventListener('keydown', (e) => {
    e.stopPropagation();
    if (e.key === 'Enter') { e.preventDefault(); commit(true); }
    else if (e.key === 'Escape') { e.preventDefault(); commit(false); }
  });
  input.addEventListener('blur', () => commit(true));
}

function syncPanes() {
  if (!el.body) return;
  const visible = state.sessions.filter((s) => {
    if (s.id && s.id === state.active) return true;
    return state.split && s.id && s.id === state.secondary;
  });
  if (!visible.length && state.sessions.length) visible.push(state.sessions[state.sessions.length - 1]);
  for (const s of state.sessions) {
    const show = visible.includes(s);
    s.pane.hidden = !show;
    s.pane.classList.toggle('is-focused', state.split && visible.length > 1 && s.id === state.active);
  }
  // Keep the DOM order stable while split: the left pane stays left when you click the right one.
  const split = state.split && visible.length > 1;
  el.body.classList.toggle('is-split', split);
  requestAnimationFrame(() => fitAll());
  syncEmpty();
}

function syncEmpty() {
  if (!el.empty) return;
  const empty = state.sessions.length === 0 && !attaching;
  el.empty.hidden = !empty;
  el.body.hidden = empty;
}

function syncAllowed() {
  if (el.btn) el.btn.hidden = !state.allowed;
}

function fitAll() {
  if (!state.open) return;
  for (const s of state.sessions) {
    if (s.pane.hidden || s.disposed) continue;
    try { s.fit?.fit(); } catch { /* element not laid out yet */ }
  }
}

// ─── Height drag ─────────────────────────────────────────────────────────
function bindResize() {
  let startY = 0;
  let startH = 0;
  let dragging = false;

  const onMove = (e) => {
    if (!dragging) return;
    state.height = Math.min(maxHeight(), Math.max(MIN_H, startH + (startY - e.clientY)));
    applyHeight();
  };
  const onUp = () => {
    if (!dragging) return;
    dragging = false;
    el.handle.classList.remove('is-dragging');
    document.body.classList.remove('is-term-resizing');
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onUp);
    storageSet(HEIGHT_KEY, Math.round(state.height));
    fitAll();
  };

  el.handle.addEventListener('pointerdown', (e) => {
    if (state.max) setMax(false);
    e.preventDefault();
    dragging = true;
    startY = e.clientY;
    startH = el.panel.offsetHeight;
    el.handle.classList.add('is-dragging');
    document.body.classList.add('is-term-resizing');
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  });
  el.handle.addEventListener('dblclick', () => {
    if (state.max) setMax(false);
    state.height = DEFAULT_H;
    applyHeight();
    storageSet(HEIGHT_KEY, DEFAULT_H);
  });
  el.handle.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
    e.preventDefault();
    if (state.max) setMax(false);
    state.height = Math.min(maxHeight(), Math.max(MIN_H, el.panel.offsetHeight + (e.key === 'ArrowUp' ? 24 : -24)));
    applyHeight();
    storageSet(HEIGHT_KEY, Math.round(state.height));
  });
}

// ─── Project switch ──────────────────────────────────────────────────────
/** Another project is on screen: its Jarvis runs its own shells. Let go of
 *  this one's views (the shells keep running there) and attach to the new
 *  project's — or start one if the panel is open. */
function onProjectSwitched() {
  state.remoteLive = false;
  for (const s of [...state.sessions]) disposeView(s);
  state.active = null;
  state.secondary = null;
  renderTabs();
  syncPanes();
  syncDot();
  if (state.open) attach();
}

// ─── Init ────────────────────────────────────────────────────────────────
/** Ask the server whether this page may open shells (a tunnel page may not). */
async function refreshAllowed() {
  try {
    const res = await api('/api/terminal/list');
    state.allowed = res.allowed !== false;
    syncAllowed();
    // Shells already running (a reload): show the live dot so they aren't forgotten.
    state.remoteLive = state.allowed && (res.terminals || []).some((t) => !t.exited);
    syncDot();
    return res;
  } catch {
    return null;
  }
}

/** The QR dialog's "Terminal on the link" switch moved: show or hide the button at once. */
async function onAccessChanged() {
  const was = state.allowed;
  await refreshAllowed();
  if (was && !state.allowed) {
    hide();
    showToast('The terminal is no longer available on the public link.', true);
  }
}

export function initTerminal() {
  build();
  // A tunnel visitor gets no shell unless the QR dialog's "Terminal on the link"
  // switch is on — the server answers `allowed` for this page.
  refreshAllowed();
  document.addEventListener('jarvis:terminal-access', onAccessChanged);
  // Copy / paste buttons only where there's no keyboard shortcut or right-click.
  const syncCoarse = () => el.panel.classList.toggle('is-touch', !!COARSE?.matches);
  syncCoarse();
  COARSE?.addEventListener?.('change', syncCoarse);
  document.addEventListener('jarvis:project-switched', onProjectSwitched);
  // xterm measures its cell size when a view opens; the mono web font may land later.
  document.fonts?.ready?.then(() => {
    for (const s of state.sessions) if (!s.disposed) s.term.options.fontFamily = monoFont();
    fitAll();
  });
  // A bookmarkable deep link: /#terminal drops straight into a shell.
  if (location.hash === '#terminal') open();
  window.addEventListener('hashchange', () => {
    if (location.hash === '#terminal') open();
  });
  syncEmpty();
}
