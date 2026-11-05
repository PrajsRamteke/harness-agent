/** Terminal — a real shell, docked at the bottom of the main column.
 *
 * The top-bar button (or Ctrl + `) opens a resizable panel under the
 * composer: the transcript shrinks and the composer sits on top of the panel. Each
 * tab is a live PTY shell on the computer (terminal_api.py): xterm.js draws
 * it, keystrokes POST to the server (one request at a time, so they arrive in
 * order), output is long-polled back — every shell in one held request
 * (`pump`), never one per shell, and the next one only once xterm has drawn
 * the last answer.
 *
 * Shells belong to the Jarvis that runs them, not to this page: hiding the
 * panel, reloading the page or switching project leaves them running, and
 * opening the panel again re-attaches to them (their output replays from the
 * server's buffer). Only a tab's × ends a shell.
 *
 * Across projects (workspace): a shell opened for project X lives in X's own
 * Jarvis (`s.inst` = its instance id, reached through `/p/<id>/api/terminal/*`
 * — `apiAt`), survives switching the chat to another project, and shows up in
 * X too. Only a master group's panel is shared: the hub and its member projects
 * see each other's shells (tabs carry the project's colour + name; "Show the group's
 * terminals" decides whether the siblings' shells get a tab here; the All-terminals
 * view lists the group's shells). A project in no group is independent: its panel
 * holds only its own shells, whatever else is running. The pump holds one `poll`
 * per instance.
 *
 * The AI tools on top (error chip, selection → chat, clickable links, ⌘K
 * command writer) live in terminal_ai.js.
 */
import { $, escapeHtml, storageGet, storageSet, showToast, copyText, haptic, debounce } from './utils.js';
import {
  apiAt, fetchWorkspace, fetchWorkspaceTerminals, openWorkspaceProject, fetchLaunch,
} from './api.js';
import { icon } from './icons.js';
import { isLightTheme } from './theme.js';
import { allProjects, currentProjectId, hostProjectId, findProject } from './projects.js';
import { groupInfo } from './tabs.js';
import {
  initTerminalAi, attachAi, detachAi, noteInput, afterOutput, clearAi, handleAiKey,
} from './terminal_ai.js';

const HEIGHT_KEY = 'jarvis-term-h';
const MIN_H = 140;
const DEFAULT_H = 300;
const MIN_CHAT = 120;   // transcript rows always left above the panel
const MAX_SESSIONS = 24;   // tabs on this page (each project's Jarvis allows PER_INSTANCE of its own)
const PER_INSTANCE = 8;
const MIN_DOCK_REST = 160;  // rows the Files drawer keeps above a panel docked in it
const RESIZE_MS = 80;              // a size change reaches the shell at most this late
const HIDDEN_PAUSE_MS = 45_000;    // a background tab lets go of its held request (api.js does too)
const ALL_KEY = 'jarvis-term-all';   // '1' | '0': the master group's other shells get a tab here (unset: on)
const MAX_PUMPS = 6;                // instances polled at once (the browser allows ~6 connections per host)
const LAUNCH_POLL_MS = 1000;
const LAUNCH_POLLS = 90;
const COARSE = window.matchMedia?.('(pointer: coarse)');

const state = {
  open: false,
  max: false,
  split: false,
  height: Math.max(MIN_H, Number(storageGet(HEIGHT_KEY, DEFAULT_H)) || DEFAULT_H),
  // { id, inst (instance id of the Jarvis the shell lives in), explicit (opened here on purpose),
  //   stopped (that project closed), title, term, fit, search, pane, cursor, exited, disposed, queue, flush }
  sessions: [],
  active: null,
  secondary: null,
  allowed: true,
  allView: false,     // the All-terminals view is showing
  remoteLive: false,  // shells already running on the server before this page attached
};

const el = {};
let built = false;
let attaching = null;   // the in-flight re-attach / first spawn
// One held poll per instance that has tabs: instance id → { running, abort }.
const pumps = new Map();
const ws = { projects: [], at: 0 };   // the workspace rows (GET /api/workspace), for badges + the menu
const instAllowed = new Map();        // instance id → may this page open shells there (tunnel policy)
let hiddenSince = 0;
// Docked in another view (the Files drawer): where it lives, what it shares
// its height with, and who takes the focus when it hides there.
const docked = { host: null, grow: null, focusBack: null, home: null, homeNext: null };

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
  buildExtras();
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
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-variant', 'data-palette', 'data-accent', 'style'] });
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

function open({ spawnIfEmpty = true } = {}) {
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
  if (!state.sessions.length) attach({ spawnIfEmpty });
  else { syncPanes(); requestAnimationFrame(() => { fitAll(); focusActive(); }); }
  syncDot();
  announce();
}

function hide({ focusComposer = false } = {}) {
  if (!state.open) return;
  const hadFocus = el.panel.contains(document.activeElement);
  state.open = false;
  setMenu(false);
  setAllView(false);
  el.panel.setAttribute('aria-hidden', 'true');
  el.panel.inert = true;
  el.panel.hidden = true;
  el.btn.setAttribute('aria-expanded', 'false');
  el.btn.classList.remove('is-on');
  document.body.classList.remove('term-open', 'term-max');
  syncDot();
  announce();
  if (!hadFocus && !focusComposer) return;
  if (docked.host) docked.focusBack?.();
  else $('prompt')?.focus();
}

/** Open / hidden / allowed changed: views that offer the terminal (the Files footer) follow. */
function announce() {
  document.dispatchEvent(new CustomEvent('jarvis:terminal-state', { detail: terminalState() }));
}

export function terminalState() {
  return { open: state.open, allowed: state.allowed };
}

/** A button's toggle: open (and jump in) or hide — the top-bar button's behaviour. */
export function toggleTerminal() {
  if (state.open) hide();
  else open();
}

// ─── Docking ─────────────────────────────────────────────────────────────
/** Move the panel — the same shells and views — to the bottom of `host`, in
 *  front of `before`. It shares its height with `grow` (the host's flexible
 *  part); `focusBack` takes the focus when it hides there. `undockTerminal()`
 *  puts it back under the composer. */
export function dockTerminal(host, { before = null, grow = null, focusBack = null } = {}) {
  build();
  if (!host) return;
  if (!docked.host) {
    docked.home = el.panel.parentNode;
    docked.homeNext = el.panel.nextSibling;
  }
  Object.assign(docked, { host, grow, focusBack });
  host.insertBefore(el.panel, before);
  el.panel.classList.add('is-docked');
  relayout();
}

export function undockTerminal() {
  if (!docked.host || !docked.home) return;
  const next = docked.homeNext?.parentNode === docked.home ? docked.homeNext : null;
  docked.home.insertBefore(el.panel, next);
  el.panel.classList.remove('is-docked');
  Object.assign(docked, { host: null, grow: null, focusBack: null });
  relayout();
}

function relayout() {
  applyHeight();
  requestAnimationFrame(() => fitAll());
}

/** Live shells in the other running projects of this one's master group (this one's are in `state`). */
function othersLive() {
  const cur = currentProjectId();
  return allProjects().filter((p) => p.id !== cur && p.terminals > 0 && inScope(p.id));
}

function syncDot() {
  if (!el.dot) return;
  const here = state.sessions.length ? state.sessions.some((s) => !s.exited && !s.stopped) : state.remoteLive;
  const elsewhere = othersLive();
  // The open panel already shows this project's shells; other projects' still count.
  el.dot.hidden = !((here && !state.open) || elsewhere.length);
  el.btn.title = elsewhere.length
    ? `Terminal (Ctrl + \`) · shells running in ${elsewhere.map((p) => p.project || 'project').join(', ')}`
    : 'Terminal (Ctrl + `)';
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
    if (!state.secondary) { spawn({ secondary: true, inst: activeSession()?.inst || curInst() || null }); return; }
  } else {
    state.secondary = null;
  }
  renderTabs();
  syncPanes();
}

/** Height from the saved value, never so tall the transcript disappears. */
function maxHeight() {
  // Docked: the panel and the host's flexible part split the room between them.
  if (docked.host) {
    const room = el.panel.offsetHeight + (docked.grow?.offsetHeight || 0);
    return Math.max(MIN_H, room - MIN_DOCK_REST);
  }
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

/** Instance id of the project on screen ('' until the project list has loaded). */
const curInst = () => currentProjectId() || hostProjectId() || '';
/** What a session stores for "the shown project" while the list hasn't loaded yet. */
const instKey = (s) => s.inst || '';

/** Workspace project ids that share this panel with the project on screen: its master group
 *  (the hub + every member). Empty for a project in no group — its panel is its own. */
function scopeIds() {
  const ids = new Set();
  const cur = projectOfInst(curInst());
  if (!cur) return ids;
  const hubs = cur.kind === 'group' ? [cur] : (cur.masters || []).map((m) => ws.projects.find((p) => p.id === m.id) || { id: m.id });
  for (const h of hubs) {
    ids.add(h.id);
    for (const m of h.members || []) ids.add(m);
  }
  if (ids.size) ids.add(cur.id);
  return ids;
}

/** Is the project on screen part of a master group (hub or member)? */
const inGroup = () => scopeIds().size > 0;

/** Does the Jarvis `inst` belong to a project of the on-screen project's master group? */
function inScope(inst) {
  const p = projectOfInst(inst);
  return !!p && scopeIds().has(p.id);
}

/** "Show the group's terminals": on by default; only ever applies inside a master group. */
function showAll() {
  return storageGet(ALL_KEY, null) !== '0';
}

/** The workspace project whose Jarvis is instance `inst` (null: not a workspace project). */
function projectOfInst(inst) {
  if (!inst) return null;
  return ws.projects.find((p) => p.instances?.some((i) => i.id === inst)) || null;
}

/** Does this page show a tab for `s`? This project's own shells always; another project's only
 *  when it is in the same master group and the toggle is on (or it was opened here on purpose). */
function wanted(s) {
  if (!s.inst || s.inst === curInst()) return true;
  return inScope(s.inst) && (s.explicit || showAll());
}

/** First open on this page: re-attach to the shells this project's Jarvis (and, with
 *  the toggle on, the other running projects') already runs, or start one. */
function attach({ spawnIfEmpty = true } = {}) {
  if (attaching) return attaching;
  attaching = (async () => {
    const here = curInst();
    let list = [];
    try {
      const res = await apiAt(here, '/api/terminal/list');
      state.allowed = res.allowed !== false;
      instAllowed.set(here, state.allowed);
      list = Array.isArray(res.terminals) ? res.terminals : [];
    } catch { /* fall through: spawn reports the error */ }
    if (!state.allowed) {
      hide();
      syncAllowed();
      showToast('The terminal is only available on this computer or your own network.', true);
      return;
    }
    for (const t of list) {
      if (t.exited) apiAt(here, '/api/terminal/close', 'POST', { id: t.id }).catch(() => {});
    }
    for (const t of list) {
      if (!t.exited && !sessionById(t.id)) adopt(t, here);
    }
    if (!ws.at) await refreshWorkspace();   // the group scope comes from the workspace rows
    await adoptOthers(here);
    if (!state.sessions.length) {
      if (spawnIfEmpty) await spawn();
      return;
    }
    if (!activeSession()) {
      state.active = (state.sessions.find((x) => x.inst === here) || state.sessions[0])?.id || null;
    }
    renderTabs();
    syncPanes();
    requestAnimationFrame(() => { fitAll(); focusActive(); });
  })().finally(() => { attaching = null; syncEmpty(); syncDot(); });
  return attaching;
}

/** Tabs for the shells of the other running projects in this one's master group (one request:
 *  the server asks each instance and tells us which allow this page a shell). An independent
 *  project adopts nothing. */
async function adoptOthers(here) {
  if (!showAll() || !inGroup()) return;
  let res;
  try { res = await fetchWorkspaceTerminals(); } catch { return; }
  for (const g of res?.groups || []) {
    if (g.instance_id === here || !inScope(g.instance_id)) continue;
    instAllowed.set(g.instance_id, g.allowed !== false);
    if (g.allowed === false) continue;
    for (const t of g.terminals || []) {
      if (t.exited || sessionById(t.id)) continue;
      if (state.sessions.length >= MAX_SESSIONS) return;
      adopt(t, g.instance_id);
    }
  }
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
    id: null, inst: null, explicit: false, stopped: false, starting: false, proj: null, cwd: '',
    title, term, fit, search, pane, cursor: 0,
    exited: false, disposed: false, queue: '', flush: 0, sending: false,
    replay: false, sentSize: '', resizeTimer: 0, resizeAgain: false, ai: null,
  };
  attachAi(s);
  pane.addEventListener('mousedown', () => { if (s.id && s.id !== state.active) selectTab(s.id, { focus: false }); });
  return s;
}

/** A shell a Jarvis already runs (`inst`: which one — its own project's). */
function adopt(t, inst = curInst(), { explicit = false } = {}) {
  if (!window.Terminal) return null;
  const s = makeView(t.title || 'Terminal');
  s.id = t.id;
  s.inst = inst || null;
  s.cwd = t.cwd || '';
  s.explicit = explicit || (!!inst && inst !== curInst());
  rememberProject(s);
  // Its first answer replays history: xterm must not answer the terminal
  // queries in it again (they'd reach the program as typed keys).
  s.replay = true;
  state.sessions.push(s);
  wireSession(s);
  kick(instKey(s));
  requestAnimationFrame(() => { try { s.fit?.fit(); } catch { /* not laid out */ } sendResize(s); });
  return s;
}

/** A new shell. `inst`: the Jarvis it lives in (default: the project on screen);
 *  `project`: a workspace row to start first when it isn't running; `cwd`: only for
 *  a folder that isn't a workspace project (today's behaviour: the shown Jarvis, that folder). */
async function spawn({ secondary = false, inst = curInst() || null, project = null, cwd = '' } = {}) {
  build();
  if (state.sessions.length >= MAX_SESSIONS) {
    showToast(`You can have up to ${MAX_SESSIONS} terminals — close one first.`, true);
    return;
  }
  if (inst && state.sessions.filter((x) => x.inst === inst && !x.stopped).length >= PER_INSTANCE) {
    showToast(`That project already has ${PER_INSTANCE} shells — close one first.`, true);
    return;
  }
  if (!window.Terminal) {
    showToast('The terminal engine didn’t load — reload the page.', true);
    return;
  }
  const s = makeView();
  s.inst = inst;
  s.explicit = !!inst && inst !== curInst();
  if (project) { s.proj = { name: project.name, color: project.color, id: project.id }; s.explicit = true; }
  state.sessions.push(s);
  if (project && !inst) {
    // Its Jarvis isn't running: start it, then open the shell there.
    s.starting = true;
    s.title = `Starting ${project.name}…`;
    s.term.write(`\x1b[90m${s.title}\x1b[0m\r\n`);
    renderTabs();
    syncPanes();
    try {
      s.inst = await startProject(project);
    } catch (err) {
      s.starting = false;
      disposeView(s);
      renderTabs();
      syncPanes();
      showToast(err.message || `Couldn’t start ${project.name}.`, true);
      return;
    }
    s.starting = false;
    s.title = 'Terminal';
    if (s.disposed) return;
    s.term.reset();
  }
  rememberProject(s);
  renderTabs();
  syncPanes();
  await new Promise((r) => requestAnimationFrame(r));
  try { s.fit?.fit(); } catch { /* not laid out */ }

  try {
    const res = await apiAt(s.inst, '/api/terminal/open', 'POST', {
      cols: s.term.cols || 80, rows: s.term.rows || 24, ...(cwd ? { cwd } : {}),
    });
    if (!res.ok) throw new Error(res.error || 'Could not start the shell.');
    if (s.disposed) {   // closed (or project switched) while it started
      apiAt(s.inst, '/api/terminal/close', 'POST', { id: res.terminal.id }).catch(() => {});
      return;
    }
    s.id = res.terminal.id;
    s.cwd = res.terminal.cwd || '';
    s.title = res.terminal.title || s.title;
    s.sentSize = `${s.term.cols || 80}x${s.term.rows || 24}`;
    // A split opens the new shell on the right and puts you in it.
    if (secondary && state.active) state.secondary = state.active;
    state.active = s.id;
    renderTabs();
    syncPanes();
    focusActive();
    wireSession(s);
    kick(instKey(s));
  } catch (err) {
    disposeView(s);
    renderTabs();
    syncPanes();
    const local = err?.payload?.code === 'local_only';
    if (local && (s.inst || '') === curInst()) { state.allowed = false; syncAllowed(); hide(); }
    if (local) instAllowed.set(s.inst || '', false);
    showToast(local ? 'The terminal is only available on this computer or your own network.'
      : (err.message || 'The terminal didn’t start.'), true);
  }
}

function wireSession(s) {
  s.term.onData((data) => {
    if (s.replay) return;   // xterm answering queries in replayed history, not you typing
    noteInput(s, data);
    s.queue += data;
    if (!s.flush && !s.sending) s.flush = setTimeout(() => flushInput(s), 8);
  });
  s.term.onResize(() => queueResize(s));
  s.term.attachCustomKeyEventHandler((e) => {
    if (e.type !== 'keydown') return true;
    if (handleAiKey(e)) return false;
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.shiftKey && (e.key === 'C' || e.key === 'c')) { copySelection(); return false; }
    if (mod && e.shiftKey && (e.key === 'V' || e.key === 'v')) { pasteInto(); return false; }
    if (e.metaKey && !e.shiftKey && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); setFind(true); return false; }
    // Ctrl+` is the panel's toggle: keep it out of the shell and let the
    // page-level shortcut handle it once (hiding here too would bubble up
    // to that listener and reopen the panel straight away).
    if (e.ctrlKey && !e.shiftKey && !e.altKey && (e.key === '`' || e.code === 'Backquote')) return false;
    return true;
  });
  s.pane.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    if (s.term.hasSelection()) copySelection();
    else pasteInto();
  });
}

/** Send what was typed. One request at a time: two in flight can reach the
 *  server in either order, which scrambles keys (and mouse reports — Claude
 *  Code reports every mouse move). Whatever is typed meanwhile goes next. */
function flushInput(s) {
  clearTimeout(s.flush);
  s.flush = 0;
  if (!s.id || s.disposed) { s.queue = ''; return; }
  if (s.sending || !s.queue) return;
  const data = s.queue;
  s.queue = '';
  s.sending = true;
  apiAt(s.inst, '/api/terminal/input', 'POST', { id: s.id, data })
    .catch((err) => {
      if (err?.payload?.code === 'gone') markExited(s);
      else if (err?.status === 404 || err?.status === 502) markStopped(s.inst);
    })
    .finally(() => {
      s.sending = false;
      if (s.queue) flushInput(s);
    });
}

function typeInto(s, data) {
  if (!s?.id || s.exited) return;
  s.queue += data;
  flushInput(s);
}

/** Tell the shell its new size right away, then at most every RESIZE_MS while
 *  it keeps changing (a drag). A program drawing for the old size into the
 *  new one is what leaves debris behind. */
function queueResize(s) {
  if (s.resizeTimer) { s.resizeAgain = true; return; }
  sendResize(s);
  s.resizeTimer = setTimeout(() => {
    s.resizeTimer = 0;
    if (s.resizeAgain) { s.resizeAgain = false; queueResize(s); }
  }, RESIZE_MS);
}

function sendResize(s) {
  if (!s.id || s.disposed || !s.term.cols) return;
  const size = `${s.term.cols}x${s.term.rows}`;
  if (size === s.sentSize) return;
  s.sentSize = size;
  apiAt(s.inst, '/api/terminal/resize', 'POST', { id: s.id, cols: s.term.cols, rows: s.term.rows }).catch(() => { s.sentSize = ''; });
}

function markExited(s) {
  if (s.exited) return;
  s.exited = true;
  renderTabs();
  syncDot();
}

/** The project's Jarvis is gone (closed, or its terminal quit): its tabs say so and offer Start. */
function markStopped(inst) {
  let any = false;
  for (const s of state.sessions) {
    if ((s.inst || '') !== (inst || '') || s.stopped || s.starting || s.disposed) continue;
    s.stopped = true;
    s.term.write('\r\n\x1b[90m[project stopped]\x1b[0m\r\n');
    any = true;
  }
  if (any) { renderTabs(); syncDot(); renderMenu(); }
}

/** Remember which workspace project a shell belongs to (the badge outlives the project's instance). */
function rememberProject(s) {
  const p = projectOfInst(s.inst);
  if (p) s.proj = { id: p.id, name: p.name, color: p.color };
  else if (!s.proj && s.inst) {
    const row = findProject(s.inst);
    if (row) s.proj = { id: '', name: row.project || 'project', color: '' };
  }
}

/** Start a workspace project's Jarvis and wait until it can be reached → its instance id. */
async function startProject(p) {
  const res = await openWorkspaceProject(p.id);
  if (!res?.ok) throw new Error(res?.error || `Couldn’t start ${p.name}.`);
  let launch = res.launch;
  for (let i = 0; i < LAUNCH_POLLS; i += 1) {
    if (!launch) break;
    if (launch.status === 'ready' && launch.instance_id) { refreshWorkspace(); return launch.instance_id; }
    if (launch.status === 'failed') throw new Error(launch.error || `Couldn’t start ${p.name}.`);
    await sleep(LAUNCH_POLL_MS);
    try { launch = (await fetchLaunch(launch.id))?.launch; } catch { launch = launch || null; }
  }
  throw new Error(`${p.name} took too long to start — check the Projects list.`);
}

// ─── Output: one held request per project's Jarvis ──────────────────────
/** Start the loops, or make a running one ask again now (a shell was added).
 *  `inst` limits the restart to that instance's loop. */
function kick(inst) {
  for (const id of pollOrder()) {
    const p = pumps.get(id);
    if (!p) runPump(id);
    else if (inst === undefined || inst === id) p.abort?.abort();
  }
}

function pollable() {
  return state.sessions.filter((s) => s.id && !s.disposed && !s.exited && !s.stopped);
}

/** The instances that get a held request: the shown project's first, at most MAX_PUMPS
 *  (the browser allows ~6 connections per host); ones the tunnel policy refuses are left out. */
function pollOrder() {
  const here = curInst();
  const ids = [];
  for (const s of pollable()) {
    const id = instKey(s);
    if (!ids.includes(id) && instAllowed.get(id) !== false) ids.push(id);
  }
  ids.sort((a, b) => (a === here ? 0 : 1) - (b === here ? 0 : 1));
  return ids.slice(0, MAX_PUMPS);
}

/** Shells keep producing output while the panel is hidden (a Claude Code run),
 *  so the loop keeps reading — just less often — instead of falling behind
 *  the server's buffer and replaying from a cut. One loop per instance with
 *  tabs: each is a single held `poll?ids=` to that project's own Jarvis. */
async function runPump(inst) {
  const me = { abort: null };
  pumps.set(inst, me);
  let backoff = 300;
  try {
    for (;;) {
      if (document.hidden && hiddenSince && Date.now() - hiddenSince > HIDDEN_PAUSE_MS) return;
      if (!pollOrder().includes(inst)) return;
      const list = pollable().filter((s) => instKey(s) === inst);
      if (!list.length) return;
      const ids = list.map((s) => `${s.id}:${s.cursor}`).join(',');
      const ctrl = new AbortController();
      me.abort = ctrl;
      let res;
      try {
        res = await apiAt(inst, `/api/terminal/poll?ids=${encodeURIComponent(ids)}&wait=15000`, 'GET', undefined, { signal: ctrl.signal });
      } catch (err) {
        if (ctrl.signal.aborted) continue;   // kicked: ask again with the new list
        if (err?.status === 404 || err?.status === 502) { markStopped(inst); return; }   // that project closed
        await sleep(backoff);
        backoff = Math.min(4000, backoff * 1.7);
        continue;
      } finally {
        if (me.abort === ctrl) me.abort = null;
      }
      backoff = 300;
      if (res.ok === false) {
        if (res.code === 'local_only') {
          instAllowed.set(inst, false);
          for (const s of list) {
            s.term.write('\r\n\x1b[31mThe terminal is only available on this computer.\x1b[0m\r\n');
            markExited(s);
          }
          return;
        }
        await sleep(1000);
        continue;
      }
      let more = false;
      const drawn = [];
      for (const [id, r] of Object.entries(res.terminals || {})) {
        const s = sessionById(id);
        if (!s || s.disposed) continue;
        if (r.ok === false) {
          if (r.code === 'gone') {
            s.term.write('\r\n\x1b[90m[terminal closed]\x1b[0m\r\n');
            markExited(s);
          }
          continue;
        }
        if (r.more) more = true;
        drawn.push(draw(s, r));
      }
      // Flow control: a program can print faster than xterm draws; asking for
      // more before the last batch is drawn piles it up in the page.
      await Promise.all(drawn);
      if (!more) {
        const rest = document.hidden ? 1000 : (state.open ? 0 : 250);
        if (rest) await sleep(rest);
      }
    }
  } finally {
    if (pumps.get(inst) === me) pumps.delete(inst);
    // An instance that was over the cap may have its turn now.
    const expired = document.hidden && hiddenSince && Date.now() - hiddenSince > HIDDEN_PAUSE_MS;
    if (!expired) {
      const next = pollOrder().find((id) => !pumps.has(id));
      if (next !== undefined) runPump(next);
    }
  }
}

/** Write one terminal's answer; resolves once xterm has parsed it. */
function draw(s, r) {
  return new Promise((resolve) => {
    if (r.reset) s.term.reset();
    const replay = s.replay || !!r.reset;
    s.replay = replay;
    s.cursor = r.cursor;
    const done = () => {
      s.replay = replay && !!r.more;   // a long replay comes in several answers
      if (r.data) afterOutput(s, replay);
      if (r.exited) markExited(s);
      resolve();
    };
    if (r.data) s.term.write(r.data, done);
    else done();
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Drop the view only (the shell keeps running unless killSession asked). */
function disposeView(s) {
  if (s.disposed) return;
  s.disposed = true;
  clearTimeout(s.flush);
  clearTimeout(s.resizeTimer);
  detachAi(s);
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
  if (s.id && !s.stopped) apiAt(s.inst, '/api/terminal/close', 'POST', { id: s.id }).catch(() => {});
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
  clearAi(s);
  // Ask the shell to redraw its prompt (Ctrl+L) — only when nothing is typed after it.
  typeInto(s, '\x0c');
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
    tab.classList.toggle('is-stopped', s.stopped);
    if (state.split && s.id && s.id === state.secondary) tab.classList.add('is-second');

    const dot = document.createElement('span');
    dot.className = 'term-tab-dot';
    dot.setAttribute('aria-hidden', 'true');

    const info = inGroup() || (s.inst && s.inst !== curInst()) ? (projectOfInst(s.inst) || s.proj) : null;
    let badge = null;
    if (info?.name) {
      if (info.color && !s.exited && !s.stopped && s.id) dot.style.background = info.color;
      badge = document.createElement('span');
      badge.className = 'term-tab-proj';
      badge.textContent = info.name;
    }

    const name = document.createElement('span');
    name.className = 'term-tab-name';
    name.textContent = s.title || 'Terminal';
    const where = info?.name ? ` · ${info.name}` : '';
    tab.title = s.stopped ? `${s.title}${where} — project stopped`
      : s.exited ? `${s.title}${where} — exited · double-click to rename`
        : `${s.title}${where}${s.cwd ? ` · ${s.cwd}` : ''} · double-click to rename`;

    let start = null;
    if (s.stopped) {
      start = document.createElement('button');
      start.type = 'button';
      start.className = 'term-tab-start';
      start.tabIndex = -1;
      start.textContent = s.proj?.id ? 'Project stopped — Start' : 'Project stopped';
      start.disabled = !s.proj?.id;
      start.addEventListener('click', (e) => { e.stopPropagation(); restartProject(s); });
    }

    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'term-tab-x';
    x.tabIndex = -1;
    x.setAttribute('aria-label', `Close ${s.title || 'terminal'}`);
    x.title = 'Close (ends the shell)';
    x.innerHTML = icon('x');
    x.addEventListener('click', (e) => { e.stopPropagation(); killSession(s); });

    tab.append(dot, ...(badge ? [badge] : []), name, ...(start ? [start] : []), x);
    tab.addEventListener('click', () => { if (s.id) selectTab(s.id); });
    if (badge && info.color) tab.style.setProperty('--proj', info.color);
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
  renderProjects();
  syncEmpty();
}

// ─── Master groups: the hub's Projects strip ─────────────────────────────
/** A live shell in workspace project `pid` (the strip focuses it instead of opening another). */
function shellIn(pid) {
  return state.sessions.find((s) => !s.exited && !s.stopped && s.id
    && (s.proj?.id === pid || projectOfInst(s.inst)?.id === pid)) || null;
}

/** In a group's hub: one button per member project — a shell there, or the one already open. */
function renderProjects() {
  if (!el.projects) return;
  const members = groupInfo().master?.members || [];
  el.projects.hidden = !members.length;
  if (!members.length) { el.projects.replaceChildren(); return; }
  const had = el.projects.contains(document.activeElement) ? document.activeElement.dataset.pid : null;
  el.projects.innerHTML = '<span class="term-projects-label" aria-hidden="true">Projects</span>'
    + members.map((m) => {
      const p = ws.projects.find((x) => x.id === m.id);
      const off = !!p && p.status !== 'running';
      const missing = p?.status === 'missing';
      const open = !!shellIn(m.id);
      const name = escapeHtml(m.name || p?.name || 'project');
      const hint = open ? `Show the shell in ${name}` : off ? `Start ${name} and open a shell there` : `Open a shell in ${name}`;
      return `<button type="button" class="term-proj${open ? ' is-open' : ''}${off ? ' is-off' : ''}" data-pid="${escapeHtml(m.id)}"`
        + `${missing ? ' disabled' : ''} title="${hint}" aria-label="${hint}">${colorDot(m.color || p?.color)}<span>${name}</span></button>`;
    }).join('');
  if (had) el.projects.querySelector(`[data-pid="${CSS.escape(had)}"]`)?.focus();
}

function onProjectsStripClick(e) {
  const b = e.target.closest('button[data-pid]');
  if (!b || b.disabled) return;
  const s = shellIn(b.dataset.pid);
  if (s) { selectTab(s.id); return; }
  const p = ws.projects.find((x) => x.id === b.dataset.pid);
  if (p) openShellIn(p);
  else showToast('The project list is still loading — try again in a moment.', true);
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
      apiAt(s.inst, '/api/terminal/rename', 'POST', { id: s.id, title: next }).catch(() => {});
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
  announce();
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

// ─── Across projects: the + ▾ menu, the All-terminals view ───────────────
/** The workspace rows (names, colours, which Jarvis runs each) — for badges and the menu. */
async function refreshWorkspace() {
  try {
    const res = await fetchWorkspace();
    if (res && Array.isArray(res.projects)) { ws.projects = res.projects; ws.at = Date.now(); }
  } catch { /* an older server, or not reachable: the panel works as a single-project one */ }
  for (const s of state.sessions) rememberProject(s);
  syncWorkspaceUi();
}

/** Show the controls that only matter inside a master group (an independent project has none). */
function syncWorkspaceUi() {
  if (!el.addMore) return;
  const cross = inGroup() || groupInfo().masters.length > 0 || !!groupInfo().master;
  el.addMore.hidden = !cross;
  el.allBtn.hidden = !cross;
  if (!cross) { setMenu(false); setAllView(false); }
  renderTabs();
  renderMenu();
  if (state.allView) renderAllView();
}

function buildExtras() {
  const add = $('term-add');
  el.addMore = document.createElement('button');
  el.addMore.type = 'button';
  el.addMore.id = 'term-add-menu';
  el.addMore.className = 'term-add term-add-more';
  el.addMore.hidden = true;
  el.addMore.title = 'New shell in another project';
  el.addMore.setAttribute('aria-label', 'New shell in another project');
  el.addMore.setAttribute('aria-haspopup', 'menu');
  el.addMore.setAttribute('aria-expanded', 'false');
  el.addMore.innerHTML = icon('chevron-down');
  add.after(el.addMore);

  // A group's hub: its member projects, one click from a shell (hidden elsewhere).
  el.projects = document.createElement('div');
  el.projects.id = 'term-projects';
  el.projects.className = 'term-projects';
  el.projects.hidden = true;
  el.projects.setAttribute('role', 'toolbar');
  el.projects.setAttribute('aria-label', 'Open a shell in a member project');
  el.addMore.after(el.projects);
  el.projects.addEventListener('click', onProjectsStripClick);

  el.menu = document.createElement('div');
  el.menu.id = 'term-menu';
  el.menu.className = 'term-menu';
  el.menu.setAttribute('role', 'menu');
  el.menu.hidden = true;
  el.panel.append(el.menu);

  el.allBtn = document.createElement('button');
  el.allBtn.type = 'button';
  el.allBtn.id = 'term-all';
  el.allBtn.className = 'term-tool term-all-btn';
  el.allBtn.hidden = true;
  el.allBtn.setAttribute('aria-pressed', 'false');
  el.allBtn.title = 'All terminals (⌥⇧T)';
  el.allBtn.setAttribute('aria-label', 'All terminals');
  el.allBtn.innerHTML = `${icon('rows-3')}<span>All</span>`;
  el.panel.querySelector('.term-tools')?.prepend(el.allBtn);

  el.allView = document.createElement('section');
  el.allView.id = 'term-all-view';
  el.allView.className = 'term-allview';
  el.allView.setAttribute('aria-label', 'All terminals');
  el.allView.hidden = true;
  el.panel.append(el.allView);

  el.addMore.addEventListener('click', (e) => { e.stopPropagation(); setMenu(el.menu.hidden); });
  el.menu.addEventListener('click', onMenuClick);
  el.menu.addEventListener('keydown', onMenuKey);
  el.allBtn.addEventListener('click', () => setAllView(!state.allView));
  el.allView.addEventListener('click', onAllClick);
  el.allView.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setAllView(false); }
  });
  document.addEventListener('pointerdown', (e) => {
    if (el.menu.hidden || el.menu.contains(e.target) || el.addMore.contains(e.target)) return;
    setMenu(false);
  });
  // ⌥⇧T: capture phase, so it works from inside a shell too (the panel stops Alt keys there).
  document.addEventListener('keydown', (e) => {
    if (!(e.altKey && e.shiftKey && !e.metaKey && !e.ctrlKey && e.code === 'KeyT')) return;
    e.preventDefault();
    e.stopPropagation();
    if (!state.allowed) return;
    if (!state.open) open({ spawnIfEmpty: false });
    setAllView(!state.allView);
  }, true);
}

const colorDot = (color) => `<i class="tm-dot"${color ? ` style="--proj:${escapeHtml(color)}"` : ''}></i>`;

function setMenu(on) {
  if (!el.menu) return;
  if (on) {
    setAllView(false);
    el.menu.hidden = false;
    el.addMore.setAttribute('aria-expanded', 'true');
    renderMenu();
    const panel = el.panel.getBoundingClientRect();
    const btn = el.addMore.getBoundingClientRect();
    el.menu.style.left = `${Math.max(6, Math.min(btn.left - panel.left, panel.width - el.menu.offsetWidth - 6))}px`;
    el.menu.querySelector('button:not([disabled])')?.focus();
    refreshWorkspace();   // fresh status (running / stopped) every time it opens
  } else {
    if (el.menu.hidden) return;
    el.menu.hidden = true;
    el.addMore.setAttribute('aria-expanded', 'false');
  }
}

function currentName() {
  const cur = curInst();
  return projectOfInst(cur)?.name || findProject(cur)?.project || 'this project';
}

function renderMenu() {
  if (!el.menu || el.menu.hidden) return;
  const cur = projectOfInst(curInst());
  const items = [];
  items.push(`<button type="button" role="menuitem" class="term-menu-item" data-act="here">
    ${colorDot(cur?.color)}<span class="tm-label">New shell in <b>${escapeHtml(currentName())}</b></span><kbd>+</kbd></button>`);
  // Groups this project belongs to: a shell in the hub folder (their master shell).
  const groups = groupInfo().masters;
  for (const g of groups) {
    const p = ws.projects.find((x) => x.id === g.id);
    const note = p?.status === 'missing' ? 'folder missing' : p && p.status !== 'running' ? 'starts first' : '';
    items.push(`<button type="button" role="menuitem" class="term-menu-item${note ? ' is-off' : ''}" data-act="master" data-gid="${escapeHtml(g.id)}"${p?.status === 'missing' ? ' disabled' : ''}>
      <span class="tm-globe" aria-hidden="true"${g.color ? ` style="--proj:${escapeHtml(g.color)}"` : ''}>${icon('globe')}</span><span class="tm-label">Master shell (<b>${escapeHtml(g.name || 'group')}</b>)</span>${note ? `<span class="tm-note">${note}</span>` : ''}</button>`);
  }
  const masterIds = new Set(groups.map((g) => g.id));
  const scope = scopeIds();
  const others = ws.projects.filter((p) => p !== cur && !masterIds.has(p.id) && scope.has(p.id));
  if (others.length) items.push('<div class="term-menu-sep" role="separator"></div>');
  for (const p of others) {
    const note = p.missing || p.status === 'missing' ? 'folder missing'
      : p.status === 'running' ? ''
        : p.status === 'starting' ? 'starting…' : 'starts first';
    items.push(`<button type="button" role="menuitem" class="term-menu-item${note ? ' is-off' : ''}" data-act="project" data-pid="${escapeHtml(p.id)}"${p.status === 'missing' ? ' disabled' : ''}>
      ${colorDot(p.color)}<span class="tm-label">${escapeHtml(p.name)}</span>${note ? `<span class="tm-note">${note}</span>` : ''}</button>`);
  }
  items.push('<div class="term-menu-sep" role="separator"></div>');
  items.push(`<button type="button" role="menuitemcheckbox" class="term-menu-item" data-act="all" aria-checked="${showAll()}">
    <span class="tm-check" aria-hidden="true">${showAll() ? icon('check') : ''}</span><span class="tm-label">Show the group’s terminals</span></button>`);
  const had = el.menu.contains(document.activeElement) ? document.activeElement.dataset : null;
  el.menu.innerHTML = items.join('');
  if (had?.act) {
    const sel = had.pid ? `[data-act="${had.act}"][data-pid="${CSS.escape(had.pid)}"]`
      : had.gid ? `[data-act="${had.act}"][data-gid="${CSS.escape(had.gid)}"]` : `[data-act="${had.act}"]`;
    el.menu.querySelector(sel)?.focus();
  }
}

function onMenuClick(e) {
  const b = e.target.closest('button[data-act]');
  if (!b || b.disabled) return;
  const act = b.dataset.act;
  if (act === 'all') {
    setShowAll(!showAll());
    renderMenu();
    return;
  }
  setMenu(false);
  if (act === 'here') spawn();
  else if (act === 'master') {
    const g = ws.projects.find((x) => x.id === b.dataset.gid);
    if (g) openShellIn(g);
    else showToast('The project list is still loading — try again in a moment.', true);
  } else if (act === 'project') {
    const p = ws.projects.find((x) => x.id === b.dataset.pid);
    if (p) openShellIn(p);
  }
}

function onMenuKey(e) {
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setMenu(false); el.addMore.focus(); return; }
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  e.preventDefault();
  const items = [...el.menu.querySelectorAll('button:not([disabled])')];
  const i = items.indexOf(document.activeElement) + (e.key === 'ArrowDown' ? 1 : -1);
  items[(i + items.length) % items.length]?.focus();
}

/** The toggle: the group's other shells get a tab here, or only this project's (and the ones opened on purpose). */
async function setShowAll(on) {
  storageSet(ALL_KEY, on ? '1' : '0');
  if (on) {
    await adoptOthers(curInst());
    renderTabs();
    syncPanes();
    kick();
  } else {
    for (const s of [...state.sessions]) if (!wanted(s)) disposeView(s);
    renderTabs();
    syncPanes();
    if (!state.sessions.length && state.open) attach();
  }
  syncDot();
  if (state.allView) renderAllView();
}

/** A shell in a workspace project: its own Jarvis runs it (started first if it isn't running). */
function openShellIn(p) {
  if (p.status === 'missing') { showToast(`${p.name}’s folder is missing.`, true); return Promise.resolve(); }
  const running = p.status === 'running' && p.instance_id;
  return spawn({ inst: running ? p.instance_id : null, project: p });
}

/** `jarvis:open-terminal-in` { projectId, instanceId }: open the panel and a new shell in that project. */
async function openTerminalIn(detail = {}) {
  build();
  if (!state.allowed) { showToast('The terminal is only available on this computer or your own network.', true); return; }
  if (!ws.projects.length) await refreshWorkspace();
  if (!state.open) open({ spawnIfEmpty: false });
  if (attaching) await attaching;
  const { projectId, instanceId } = detail;
  const p = ws.projects.find((x) => x.id === projectId) || projectOfInst(instanceId);
  if (p) { await openShellIn(p); return; }
  await spawn({ inst: instanceId || curInst() || null });
}

/** A stopped tab's Start: bring the project back and open a fresh shell there. */
async function restartProject(s) {
  const p = ws.projects.find((x) => x.id === s.proj?.id);
  if (!p) { showToast('Open that project from the sidebar to start it again.', true); return; }
  for (const x of [...state.sessions]) if (x.stopped && x.proj?.id === p.id) disposeView(x);
  renderTabs();
  syncPanes();
  await spawn({ inst: null, project: p });
}

// ─── All terminals: every shell of every running project ─────────────────
let allTimer = 0;
let allGroups = [];

function setAllView(on) {
  if (!el.allView || state.allView === on) return;
  state.allView = on;
  el.allView.hidden = !on;
  el.allBtn.setAttribute('aria-pressed', String(on));
  clearInterval(allTimer);
  if (on) {
    setMenu(false);
    renderAllView();
    loadAllView();
    allTimer = setInterval(loadAllView, 3000);
    el.allView.querySelector('button')?.focus();
  } else if (state.open) {
    focusActive();
  }
}

async function loadAllView() {
  if (!state.allView) return;
  try {
    const res = await fetchWorkspaceTerminals();
    allGroups = Array.isArray(res?.groups) ? res.groups : [];
    for (const g of allGroups) instAllowed.set(g.instance_id, g.allowed !== false);
  } catch { /* keep the last list */ }
  if (state.allView) renderAllView();
}

/** What the All view lists: [{ g, label, off }]. In a group's hub: the hub first ("Master"),
 *  then its members in order — those not running as dimmed rows (`off`); in a member: its group's
 *  projects only. Projects outside the group never show here. */
function viewGroups() {
  const { master: m, masters: ms } = groupInfo();
  const scope = scopeIds();
  if (!m) {
    const mine = new Map(ms.map((g) => [g.id, g]));
    return allGroups.filter((g) => scope.has(g.project_id)).map((g) => {
      const grp = mine.get(g.project_id);
      return { g, label: grp ? `Master · ${grp.name || g.name}` : g.name, off: false };
    });
  }
  const used = new Set();
  const out = [];
  const hub = allGroups.find((g) => g.project_id === m.id || g.instance_id === curInst());
  if (hub) { used.add(hub); out.push({ g: hub, label: `Master${m.name ? ` · ${m.name}` : ''}`, off: false }); }
  for (const mem of m.members || []) {
    const g = allGroups.find((x) => x.project_id === mem.id && !used.has(x));
    if (g) { used.add(g); out.push({ g, label: g.name || mem.name, off: false }); continue; }
    const p = ws.projects.find((x) => x.id === mem.id);
    out.push({
      g: { instance_id: '', project_id: mem.id, name: mem.name || p?.name || 'project', color: mem.color || p?.color || '', terminals: [] },
      label: mem.name || p?.name || 'project',
      off: true,
    });
  }
  return out;
}

function renderAllView() {
  if (!el.allView) return;
  const groups = viewGroups();
  const count = groups.reduce((n, x) => n + (x.g.terminals || []).filter((t) => !t.exited).length, 0);
  const live = groups.filter((x) => !x.off).length;
  const parts = [`<header class="tav-head">
    <strong>All terminals</strong><span class="tav-count">${count} running · ${live} project${live === 1 ? '' : 's'}</span>
    <span class="term-spacer"></span>
    <label class="tav-toggle"><input type="checkbox" data-act="all"${showAll() ? ' checked' : ''}><span>Show the group’s terminals</span></label>
    <button type="button" class="term-tool" data-act="close" title="Back to the shells (Esc)" aria-label="Close the all-terminals view">${icon('x')}</button>
  </header><div class="tav-body">`];
  if (!groups.length) parts.push('<p class="tav-empty">No project is running a shell yet.</p>');
  for (const { g, label, off } of groups) {
    const rows = (g.terminals || []);
    if (off) {
      const missing = ws.projects.find((x) => x.id === g.project_id)?.status === 'missing';
      parts.push(`<section class="tav-group is-off"><h4>${colorDot(g.color)}<span>${escapeHtml(label)}</span><em>off</em>`
        + `<button type="button" class="tav-new" data-act="new" data-inst="" data-pid="${escapeHtml(g.project_id)}"${missing ? ' disabled' : ''}>Start &amp; open shell</button></h4>`
        + `<p class="tav-note">${missing ? 'Folder not found.' : 'Not running — starts in a few seconds.'}</p></section>`);
      continue;
    }
    parts.push(`<section class="tav-group"><h4>${colorDot(g.color)}<span>${escapeHtml(label)}</span><em>${rows.length}</em>`
      + `<button type="button" class="tav-new" data-act="new" data-inst="${escapeHtml(g.instance_id)}" data-pid="${escapeHtml(g.project_id)}"${g.allowed === false ? ' disabled' : ''}>+ New shell</button></h4>`);
    if (g.allowed === false) parts.push('<p class="tav-note">Shells are off on this link for that project.</p>');
    else if (!rows.length) parts.push('<p class="tav-note">No shells.</p>');
    for (const t of g.allowed === false ? [] : rows) {
      const open = sessionById(t.id);
      parts.push(`<button type="button" class="tav-row${t.exited ? ' is-exited' : ''}${open ? ' is-open' : ''}" data-act="attach" data-inst="${escapeHtml(g.instance_id)}" data-tid="${escapeHtml(t.id)}">
        <i class="tav-state" aria-hidden="true"></i><span class="tav-title">${escapeHtml(t.title || 'Terminal')}</span>
        <span class="tav-cwd">${escapeHtml(t.cwd || '')}</span>
        <span class="tav-run">${t.exited ? 'exited' : 'running'}${open ? ' · in panel' : ''}</span></button>`);
    }
    parts.push('</section>');
  }
  parts.push('</div>');
  const had = el.allView.contains(document.activeElement) ? document.activeElement?.dataset?.tid : null;
  el.allView.innerHTML = parts.join('');
  if (had) el.allView.querySelector(`[data-tid="${CSS.escape(had)}"]`)?.focus();
}

function onAllClick(e) {
  const b = e.target.closest('[data-act]');
  if (!b) return;
  const act = b.dataset.act;
  if (act === 'close') { setAllView(false); return; }
  if (act === 'all') { setShowAll(b.checked); return; }
  if (act === 'new') {
    setAllView(false);
    const p = ws.projects.find((x) => x.id === b.dataset.pid);
    if (p) openShellIn(p);
    else if (b.dataset.inst) spawn({ inst: b.dataset.inst });
    else showToast('The project list is still loading — try again in a moment.', true);
    return;
  }
  if (act !== 'attach') return;
  const g = allGroups.find((x) => x.instance_id === b.dataset.inst);
  const t = g?.terminals?.find((x) => x.id === b.dataset.tid);
  if (!t || t.exited) return;
  setAllView(false);
  let s = sessionById(t.id);
  if (!s) s = adopt(t, g.instance_id, { explicit: true });
  if (!s) return;
  renderTabs();
  selectTab(t.id);
  kick(instKey(s));
}

// ─── Project switch ──────────────────────────────────────────────────────
/** Another project is on screen. Shells belong to the Jarvis that runs them, so
 *  switching the chat does not detach them: inside the same master group (with "Show the
 *  group's terminals" on, or for shells opened on purpose) they keep their tabs and keep
 *  streaming. Switching to a project outside the group (or an independent one) lets the old
 *  project's views go (the shells keep running there) and attaches the new project's —
 *  or starts one if the panel is open. */
function onProjectSwitched() {
  state.remoteLive = false;
  adoptNullInst();
  for (const s of [...state.sessions]) if (!wanted(s)) disposeView(s);
  for (const p of pumps.values()) p.abort?.abort();   // loops end, or ask again with the new list
  renderTabs();
  syncPanes();
  syncDot();
  if (state.open) attach();
  else refreshAllowed();
  refreshWorkspace();
}

/** A shell opened before the project list loaded belongs to the project that was on screen then. */
function adoptNullInst() {
  const here = curInst();
  if (!here) return;
  for (const s of state.sessions) if (!s.inst) s.inst = here;
}

/** The project list changed: the workspace rows (running / stopped) may differ. A closed
 *  project's tabs find out from their own poll (the link answers 404 / 502). */
let wsTimer = 0;
function onProjectsChanged() {
  adoptNullInst();
  syncDot();
  clearTimeout(wsTimer);
  wsTimer = setTimeout(refreshWorkspace, 1200);
}

// ─── Init ────────────────────────────────────────────────────────────────
/** Ask the server whether this page may open shells (a tunnel page may not). */
async function refreshAllowed() {
  try {
    const here = curInst();
    const res = await apiAt(here, '/api/terminal/list');
    state.allowed = res.allowed !== false;
    instAllowed.set(here, state.allowed);
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

/** `hooks` (app.js) reach the chat that is open on the page:
 *  { onAsk(text) — send it, onAddToChat(text) — put it in the composer,
 *    onOpenFile(path, line) — show it in Files }. */
export function initTerminal(hooks = {}) {
  build();
  initTerminalAi({
    active: activeSession,
    typeInto,
    focus: focusActive,
    relayout: () => requestAnimationFrame(() => fitAll()),
    // Maximised hides the transcript and the composer: make room for the chat.
    roomForChat: () => { if (state.max) setMax(false); },
  }, hooks);
  // A tunnel visitor gets no shell unless the QR dialog's "Terminal on the link"
  // switch is on — the server answers `allowed` for this page.
  refreshAllowed();
  document.addEventListener('jarvis:terminal-access', onAccessChanged);
  // Copy / paste buttons only where there's no keyboard shortcut or right-click.
  const syncCoarse = () => el.panel.classList.toggle('is-touch', !!COARSE?.matches);
  syncCoarse();
  COARSE?.addEventListener?.('change', syncCoarse);
  document.addEventListener('jarvis:project-switched', onProjectSwitched);
  document.addEventListener('jarvis:open-terminal-in', (e) => openTerminalIn(e.detail || {}));
  document.addEventListener('jarvis:workspace-changed', () => refreshWorkspace());
  // Master groups changed (this project joined / left one, the hub's members moved).
  document.addEventListener('jarvis:groups-changed', () => syncWorkspaceUi());
  refreshWorkspace();
  document.addEventListener('jarvis:projects-changed', onProjectsChanged);
  document.addEventListener('visibilitychange', () => {
    hiddenSince = document.hidden ? Date.now() : 0;
    if (!document.hidden && pollable().length) kick();
  });
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
