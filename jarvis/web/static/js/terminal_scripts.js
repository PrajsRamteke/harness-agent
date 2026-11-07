/** Package scripts — run `npm run dev:staging` & co. from a dropdown instead of
 * hunting through package.json files and typing the command by hand.
 *
 * The *Scripts* button in the terminal header (or ⌥⇧R anywhere) opens a
 * searchable list of every script in every package.json of the project
 * (scripts_api.py finds them — monorepos included — and works out npm / pnpm /
 * yarn / bun per package). Picking one types the command into the shell you're
 * looking at, exactly as if you had typed it:
 *
 *  - Enter / click: run it in the active shell. The page asks the server where
 *    that shell is (`cd apps/web && …` only when it's somewhere else) and
 *    whether a program holds it — a busy shell (a dev server already running)
 *    is left alone and the script gets a new tab, named after it.
 *  - ⌘/Ctrl+Enter or the + button: always a new tab, started in the package folder.
 *  - Shift+Enter or the cursor button: write it at the prompt without running,
 *    to add arguments (`-- --port 4000`).
 *
 * The scripts you ran last come first (per project, in this browser). The list
 * is a popover on <body>, not inside the panel, so a short panel doesn't clip it.
 *
 * terminal.js owns the shells and hands over what this needs in initTerminalScripts.
 */
import { escapeHtml, storageGet, storageSet, showToast, isMac } from './utils.js';
import { apiAt } from './api.js';
import { icon } from './icons.js';
import { matches } from './keymap.js';

const RECENT_MAX = 5;
const RECENT_KEY = 'jarvis-term-scripts:';   // + project root
const FRESH_MS = 20_000;     // a list this young shows at once (and refreshes behind)
const POP_W = 480;
const POP_MAX_H = 460;
const POP_MIN_ROOM = 260;    // less room than this below the button: open upwards
const EDGE = 8;
const REL_MAX_UP = 3;        // `cd ../../..` at most; further away gets the full path
const MOD = isMac ? '⌘' : 'Ctrl';
const COARSE = window.matchMedia?.('(pointer: coarse)');

let ctx = {
  active: () => null,              // the shell the toolbar acts on
  inst: () => '',                  // instance id the list comes from (the active shell's Jarvis)
  typeInto: () => {},              // (s, data) keys to a shell
  focus: () => {},                 // focus the active shell
  ensureOpen: () => false,         // open the panel; false when the terminal isn't allowed here
  ready: () => Promise.resolve(),  // resolves once the panel has re-attached to its shells
  runInNewShell: async () => null, // ({ inst, cwd, title, input }) → session | null
};

const el = {};
const data = { inst: null, root: '', packages: [], truncated: false, shell: null, at: 0, error: '', loading: false };
let open = false;
let query = '';
let rows = [];        // the options on screen, in order: { pkg, script, id }
let cursor = 0;
let loadSeq = 0;

// ─── Wiring (terminal.js) ──────────────────────────────────────────────
export function initTerminalScripts(context) {
  ctx = { ...ctx, ...context };
  el.btn = document.getElementById('term-scripts');
  el.btn?.addEventListener('click', (e) => { e.stopPropagation(); toggleScripts(); });
  // ⌥⇧R: capture phase, so it works from inside a shell too (the panel stops Alt keys there).
  document.addEventListener('keydown', (e) => {
    if (!matches(e, 'term.scripts')) return;
    e.preventDefault();
    e.stopPropagation();
    toggleScripts();
  }, true);
  document.addEventListener('jarvis:term-scripts', () => openScripts());
  document.addEventListener('jarvis:terminal-state', (e) => { if (!e.detail?.open) closeScripts({ focusShell: false }); });
  document.addEventListener('jarvis:project-switched', () => { closeScripts({ focusShell: false }); data.at = 0; });
}

export function toggleScripts() {
  if (open) closeScripts();
  else openScripts();
}

export async function openScripts() {
  if (!ctx.ensureOpen()) return;
  // Opened together with the panel (⌥⇧R): its shells re-attach first, so the
  // list knows which shell it types into.
  try { await ctx.ready(); } catch { /* attach reports its own errors */ }
  if (open) return;
  build();
  open = true;
  query = '';
  cursor = 0;
  el.input.value = '';
  el.pop.hidden = false;
  el.btn?.setAttribute('aria-expanded', 'true');
  place();
  const inst = ctx.inst() || '';
  const fresh = data.inst === inst && Date.now() - data.at < FRESH_MS;
  if (!fresh && data.inst !== inst) Object.assign(data, { packages: [], root: '', error: '', at: 0 });
  render();
  load({ quiet: fresh && data.packages.length > 0 });
  requestAnimationFrame(() => el.input.focus());
}

export function closeScripts({ focusShell = true } = {}) {
  if (!open) return;
  open = false;
  el.pop.hidden = true;
  el.btn?.setAttribute('aria-expanded', 'false');
  if (focusShell) ctx.focus();
}

// ─── The popover ───────────────────────────────────────────────────────
function build() {
  if (el.pop) return;
  el.pop = document.createElement('div');
  el.pop.className = 'tsc-pop';
  el.pop.id = 'term-scripts-pop';
  el.pop.hidden = true;
  el.pop.setAttribute('role', 'dialog');
  el.pop.setAttribute('aria-label', 'Run a package script');
  el.pop.innerHTML = `
    <div class="tsc-head">
      <span class="tsc-search-ic" aria-hidden="true">${icon('search')}</span>
      <input class="tsc-input" type="text" placeholder="Run a script — dev, build, test…" autocomplete="off" spellcheck="false"
        role="combobox" aria-expanded="true" aria-controls="tsc-list" aria-autocomplete="list" aria-label="Search package scripts" enterkeyhint="go">
      <button type="button" class="term-tool tsc-refresh" title="Scan the package.json files again" aria-label="Scan again">${icon('refresh-cw')}</button>
    </div>
    <div class="tsc-list" id="tsc-list" role="listbox" aria-label="Package scripts"></div>
    <div class="tsc-foot" aria-hidden="true">
      <span><kbd>↵</kbd> run</span><span><kbd>${MOD}↵</kbd> new tab</span><span><kbd>⇧↵</kbd> write only</span><span><kbd>esc</kbd> close</span>
      <span class="tsc-where"></span>
    </div>`;
  document.body.append(el.pop);
  el.input = el.pop.querySelector('.tsc-input');
  el.list = el.pop.querySelector('.tsc-list');
  el.where = el.pop.querySelector('.tsc-where');
  el.refresh = el.pop.querySelector('.tsc-refresh');

  el.input.addEventListener('input', () => { query = el.input.value; cursor = 0; render(); });
  el.input.addEventListener('keydown', onKey);
  el.refresh.addEventListener('click', () => { load({ refresh: true }); el.input.focus(); });
  el.list.addEventListener('mousedown', (e) => e.preventDefault());   // keep the focus in the search box
  el.list.addEventListener('click', onListClick);
  el.list.addEventListener('mousemove', (e) => {
    const opt = e.target.closest('.tsc-opt');
    if (!opt) return;
    const i = Number(opt.dataset.i);
    if (i !== cursor) { cursor = i; syncCursor(false); }
  });
  document.addEventListener('pointerdown', (e) => {
    if (!open || el.pop.contains(e.target) || el.btn?.contains(e.target)) return;
    closeScripts({ focusShell: false });
  });
  window.addEventListener('resize', () => { if (open) place(); });
  const syncTouch = () => el.pop.classList.toggle('is-touch', !!COARSE?.matches);
  syncTouch();
  COARSE?.addEventListener?.('change', syncTouch);
}

/** Under the Scripts button (or above it when the panel sits low), right edges lined up. */
function place() {
  const anchor = el.btn && el.btn.offsetParent ? el.btn.getBoundingClientRect()
    : { left: innerWidth / 2, right: innerWidth / 2 + POP_W / 2, top: innerHeight / 2, bottom: innerHeight / 2 };
  const width = Math.min(POP_W, innerWidth - EDGE * 2);
  const left = Math.max(EDGE, Math.min(anchor.right - width, innerWidth - width - EDGE));
  const below = innerHeight - anchor.bottom - EDGE - 4;
  const above = anchor.top - EDGE - 4;
  const up = below < POP_MIN_ROOM && above > below;
  const room = Math.min(POP_MAX_H, up ? above : below);
  Object.assign(el.pop.style, {
    left: `${left}px`,
    width: `${width}px`,
    maxHeight: `${Math.max(160, room)}px`,
    top: up ? '' : `${anchor.bottom + 4}px`,
    bottom: up ? `${innerHeight - anchor.top + 4}px` : '',
  });
  el.pop.classList.toggle('is-up', up);
}

// ─── Data ──────────────────────────────────────────────────────────────
async function load({ refresh = false, quiet = false } = {}) {
  const seq = ++loadSeq;
  const inst = ctx.inst() || '';
  const s = ctx.active();
  data.loading = true;
  if (!quiet) { data.error = ''; render(); }
  el.refresh?.classList.add('is-spinning');
  const qs = new URLSearchParams();
  if (s?.id && (s.inst || '') === inst) qs.set('id', s.id);
  if (refresh) qs.set('refresh', '1');
  try {
    const res = await apiAt(inst, `/api/terminal/scripts?${qs}`);
    if (seq !== loadSeq) return;
    if (res.ok === false) throw Object.assign(new Error(res.error || 'Couldn’t read the scripts.'), { payload: res });
    Object.assign(data, {
      inst, root: res.root || '', packages: Array.isArray(res.packages) ? res.packages : [],
      truncated: !!res.truncated, shell: res.shell || null, at: Date.now(), error: '',
    });
  } catch (err) {
    if (seq !== loadSeq) return;
    data.error = err?.status === 404
      ? 'This Jarvis is older than the page — restart it to list package scripts.'
      : err?.payload?.code === 'local_only'
        ? 'The terminal is only available on this computer or your own network.'
        : (err?.message || 'Couldn’t read the scripts.');
    if (quiet && data.packages.length) { data.error = ''; }   // keep showing the list we have
  } finally {
    if (seq === loadSeq) {
      data.loading = false;
      el.refresh?.classList.remove('is-spinning');
      if (open) render();
    }
  }
}

const trimSlash = (p) => (p && p.length > 1 ? p.replace(/\/+$/, '') : p || '');

/** The package the shell is standing in (deepest folder that contains its cwd). */
function herePackage() {
  const cwd = trimSlash(data.shell?.cwd || '');
  if (!cwd) return null;
  let best = null;
  for (const p of data.packages) {
    if ((cwd === p.path || cwd.startsWith(`${p.path}/`)) && (!best || p.path.length > best.path.length)) best = p;
  }
  return best;
}

// ─── Recent (per project, this browser) ────────────────────────────────
const recentKey = () => RECENT_KEY + (data.root || data.inst || '');
const keyOf = (pkg, script) => `${pkg.dir}\u0000${script.name}`;

function recentKeys() {
  try {
    const list = JSON.parse(storageGet(recentKey(), '[]'));
    return Array.isArray(list) ? list.filter((k) => typeof k === 'string') : [];
  } catch { return []; }
}

function remember(pkg, script) {
  const k = keyOf(pkg, script);
  storageSet(recentKey(), JSON.stringify([k, ...recentKeys().filter((x) => x !== k)].slice(0, RECENT_MAX)));
}

function recentRows() {
  const out = [];
  for (const k of recentKeys()) {
    const [dir, name] = k.split('\u0000');
    const pkg = data.packages.find((p) => p.dir === dir);
    const script = pkg?.scripts.find((x) => x.name === name);
    if (script) out.push({ pkg, script });
  }
  return out;
}

// ─── Search ────────────────────────────────────────────────────────────
/** Lower is better; null = no match. Every word must match somewhere. */
function score(pkg, script, words) {
  const name = script.name.toLowerCase();
  const hay = `${name} ${pkg.name.toLowerCase()} ${pkg.dir.toLowerCase()} ${script.body.toLowerCase()}`;
  let total = 0;
  for (const w of words) {
    if (name === w) total += 0;
    else if (name.startsWith(w)) total += 1;
    else if (name.split(/[:._-]/).some((part) => part.startsWith(w))) total += 2;
    else if (name.includes(w)) total += 3;
    else if (pkg.name.toLowerCase().includes(w) || pkg.dir.toLowerCase().includes(w)) total += 5;
    else if (hay.includes(w)) total += 8;
    else return null;
  }
  return total + (script.hook ? 4 : 0);
}

/** `text` with the query's words wrapped in <mark>. */
function highlight(text, words) {
  if (!words.length) return escapeHtml(text);
  const lower = text.toLowerCase();
  const marks = new Array(text.length).fill(false);
  for (const w of words) {
    let at = lower.indexOf(w);
    while (w && at >= 0) {
      for (let i = at; i < at + w.length; i += 1) marks[i] = true;
      at = lower.indexOf(w, at + w.length);
    }
  }
  let out = '';
  let inMark = false;
  for (let i = 0; i < text.length; i += 1) {
    if (marks[i] !== inMark) { out += marks[i] ? '<mark>' : '</mark>'; inMark = marks[i]; }
    out += escapeHtml(text[i]);
  }
  return inMark ? `${out}</mark>` : out;
}

// ─── Render ────────────────────────────────────────────────────────────
function render() {
  if (!el.list) return;
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  rows = [];
  const parts = [];
  const here = herePackage();
  const multi = data.packages.length > 1;

  const option = (pkg, script, { showPkg = false } = {}) => {
    const i = rows.length;
    rows.push({ pkg, script });
    const where = showPkg && multi ? `<span class="tsc-pkg">${escapeHtml(pkg.dir || pkg.name)}</span>` : '';
    return `<div class="tsc-opt${script.hook ? ' is-hook' : ''}" role="option" id="tsc-opt-${i}" data-i="${i}" aria-selected="false"
        title="${escapeHtml(script.run)}${pkg.dir ? ` · in ${escapeHtml(pkg.dir)}` : ''}\n${escapeHtml(script.body)}">
      <span class="tsc-run-ic" aria-hidden="true">${icon('play')}</span>
      <span class="tsc-name">${highlight(script.name, words)}</span>
      ${where}
      <span class="tsc-body">${highlight(script.body, words)}</span>
      ${script.hook ? '<span class="tsc-tag">hook</span>' : ''}
      <span class="tsc-acts">
        <button type="button" class="tsc-act" data-act="insert" tabindex="-1" title="Write at the prompt without running (⇧↵)" aria-label="Write ${escapeHtml(script.name)} at the prompt">${icon('text-cursor-input')}</button>
        <button type="button" class="tsc-act" data-act="tab" tabindex="-1" title="Run in a new terminal (${MOD}↵)" aria-label="Run ${escapeHtml(script.name)} in a new terminal">${icon('plus')}</button>
      </span>
    </div>`;
  };

  if (!data.packages.length) {
    if (data.loading) parts.push(skeleton());
    else if (data.error) {
      parts.push(`<div class="tsc-empty is-error">${icon('circle-alert')}<p>${escapeHtml(data.error)}</p>
        <button type="button" class="btn tsc-retry" data-act="retry">Try again</button></div>`);
    } else {
      parts.push(`<div class="tsc-empty">${icon('file-code')}<p><strong>No package scripts here</strong>
        <span>No package.json with a <code>scripts</code> block in ${escapeHtml(baseName(data.root) || 'this project')} or its folders
        (node_modules and build output are skipped).</span></p></div>`);
    }
  } else if (words.length) {
    // Search: one flat list, best match first, the package beside each row.
    const hits = [];
    data.packages.forEach((pkg, pi) => pkg.scripts.forEach((script, si) => {
      const sc = score(pkg, script, words);
      if (sc !== null) hits.push({ pkg, script, sc, order: pi * 10_000 + si, here: pkg === here ? 0 : 1 });
    }));
    hits.sort((a, b) => a.sc - b.sc || a.here - b.here || a.order - b.order);
    if (!hits.length) parts.push(`<div class="tsc-none">No script matches “${escapeHtml(query.trim())}”.</div>`);
    else {
      parts.push(`<div class="tsc-group" role="group" aria-label="Matches">`);
      for (const h of hits) parts.push(option(h.pkg, h.script, { showPkg: true }));
      parts.push('</div>');
    }
  } else {
    const recent = recentRows();
    if (recent.length) {
      parts.push(`<div class="tsc-group" role="group" aria-label="Recent"><div class="tsc-gh" role="presentation">
        <span class="tsc-gh-ic">${icon('history')}</span><span class="tsc-gh-name">Recent</span></div>`);
      for (const r of recent) parts.push(option(r.pkg, r.script, { showPkg: true }));
      parts.push('</div>');
    }
    // The shell's own package first, then the project's root, then by folder.
    const ordered = here ? [here, ...data.packages.filter((p) => p !== here)] : data.packages;
    for (const pkg of ordered) {
      const label = pkg.dir ? pkg.dir : './';
      parts.push(`<div class="tsc-group" role="group" aria-label="${escapeHtml(pkg.name)}"><div class="tsc-gh" role="presentation" title="${escapeHtml(pkg.path)}">
        <span class="tsc-gh-ic">${icon('folder')}</span>
        <span class="tsc-gh-name">${escapeHtml(pkg.name)}</span>
        ${multi || pkg.dir ? `<span class="tsc-gh-dir">${escapeHtml(label)}</span>` : ''}
        ${pkg === here && multi ? '<span class="tsc-chip">here</span>' : ''}
        <span class="tsc-pm">${escapeHtml(pkg.manager)}</span></div>`);
      for (const script of pkg.scripts) parts.push(option(pkg, script));
      parts.push('</div>');
    }
    if (data.truncated) parts.push('<div class="tsc-none">Only the first package.json files were read — very large project.</div>');
  }
  el.list.innerHTML = parts.join('');
  cursor = Math.min(cursor, Math.max(0, rows.length - 1));
  syncCursor(true);
  syncWhere();
}

function skeleton() {
  return `<div class="tsc-loading" aria-label="Looking for package scripts">${'<i></i>'.repeat(5)}</div>`;
}

function syncWhere() {
  if (!el.where) return;
  const s = ctx.active();
  const shell = data.shell;
  el.where.textContent = !s ? 'opens a new terminal'
    : shell?.busy ? `${s.title} is busy${shell.program ? ` (${shell.program})` : ''} → new tab`
      : `in ${s.title}`;
  el.where.classList.toggle('is-busy', !!shell?.busy);
}

function syncCursor(scroll) {
  const opts = el.list.querySelectorAll('.tsc-opt');
  opts.forEach((o, i) => {
    const on = i === cursor;
    o.classList.toggle('is-cursor', on);
    o.setAttribute('aria-selected', String(on));
  });
  const cur = opts[cursor];
  if (cur) {
    el.input.setAttribute('aria-activedescendant', cur.id);
    if (scroll) cur.scrollIntoView({ block: 'nearest' });
  } else {
    el.input.removeAttribute('aria-activedescendant');
  }
}

const baseName = (p) => (p || '').replace(/\/+$/, '').split('/').pop() || '';

// ─── Keys + clicks ─────────────────────────────────────────────────────
function onKey(e) {
  if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();
    if (el.input.value) { el.input.value = ''; query = ''; cursor = 0; render(); }
    else closeScripts();
    return;
  }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (!rows.length) return;
    cursor = (cursor + (e.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length;
    syncCursor(true);
    return;
  }
  if (e.key === 'PageDown' || e.key === 'PageUp') {
    e.preventDefault();
    if (!rows.length) return;
    cursor = Math.max(0, Math.min(rows.length - 1, cursor + (e.key === 'PageDown' ? 8 : -8)));
    syncCursor(true);
    return;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    const row = rows[cursor];
    if (!row) return;
    const mode = e.metaKey || e.ctrlKey ? 'tab' : e.shiftKey ? 'insert' : 'run';
    run(row.pkg, row.script, mode);
  }
}

function onListClick(e) {
  if (e.target.closest('[data-act="retry"]')) { load({ refresh: true }); return; }
  const opt = e.target.closest('.tsc-opt');
  if (!opt) return;
  const row = rows[Number(opt.dataset.i)];
  if (!row) return;
  const act = e.target.closest('.tsc-act')?.dataset.act;
  const mode = act === 'insert' ? 'insert' : act === 'tab' || e.metaKey || e.ctrlKey ? 'tab' : e.shiftKey ? 'insert' : 'run';
  run(row.pkg, row.script, mode);
}

// ─── Running ───────────────────────────────────────────────────────────
/** POSIX shell quoting, only when needed (`dev:staging` stays as it is). */
function shq(s) {
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`;
}

/** Where `cd` should go from `from` to reach `to`: '' (already there), a short
 *  relative path (`apps/web`, `../..`) or the full path. */
export function cdTarget(from, to) {
  from = trimSlash(from);
  to = trimSlash(to);
  if (!to || from === to) return '';
  if (!from) return to;
  const a = from.split('/').filter(Boolean);
  const b = to.split('/').filter(Boolean);
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i += 1;
  const up = a.length - i;
  if (i === 0 || up > REL_MAX_UP) return to;
  const rel = [...new Array(up).fill('..'), ...b.slice(i)].join('/');
  return rel || '.';
}

/** The line to type in a shell standing in `cwd`. */
export function commandLine(run, pkgPath, cwd) {
  const where = cdTarget(cwd, pkgPath);
  return where ? `cd ${shq(where)} && ${run}` : run;
}

async function run(pkg, script, mode) {
  closeScripts({ focusShell: false });
  remember(pkg, script);
  const s = ctx.active();
  const inst = data.inst || ctx.inst() || null;
  const usable = s && s.id && !s.exited && !s.stopped && (s.inst || '') === (data.inst || '');
  if (mode === 'tab' || !usable) {
    await inNewTab(pkg, script, mode, inst);
    return;
  }
  // Ask again right now: the dropdown may have been open a while.
  let status = null;
  try { status = await apiAt(s.inst, `/api/terminal/status?id=${encodeURIComponent(s.id)}`); } catch { /* an older server: type as before */ }
  if (status?.busy) {
    showToast(`${s.title} is busy${status.program ? ` running ${status.program}` : ''} — ${script.name} got a new tab`);
    await inNewTab(pkg, script, mode, inst);
    return;
  }
  const line = commandLine(script.run, pkg.path, status?.cwd || s.cwd || data.root);
  // Ctrl+U first: whatever was half-typed at the prompt would end up in front of the command.
  ctx.typeInto(s, `\x15${line}${mode === 'run' ? '\r' : ''}`);
  ctx.focus();
  if (mode === 'insert') showToast('Written at the prompt — add arguments, then press Enter');
}

async function inNewTab(pkg, script, mode, inst) {
  const s = await ctx.runInNewShell({
    inst,
    cwd: pkg.path,
    title: script.name.slice(0, 40),
    input: `${script.run}${mode === 'insert' ? '' : '\r'}`,
  });
  if (!s) return;
  if (mode === 'insert') showToast('Written at the prompt — add arguments, then press Enter');
}
