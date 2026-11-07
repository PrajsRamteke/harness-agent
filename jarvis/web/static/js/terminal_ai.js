/** AI in the terminal panel — ask Jarvis about what a shell printed without
 * leaving it. Everything goes to the chat that is already open on the page
 * (its composer, or straight to it as a message): no new chat, no new box.
 *
 *  - Errors: once a command prints something that looks like a failure (a
 *    traceback, `command not found`, `npm ERR!`, `error[E0425]:` …) a chip in
 *    the pane offers "Fix with Jarvis" (the command + its output go to the
 *    chat) or "Add to message" (they land in the composer to edit first).
 *  - Selection: select text in a shell → Ask Jarvis / Add to message / Copy.
 *  - Links: ⌘/Ctrl-click a URL to open it, a file path (`src/app.py:42`,
 *    `File "x.py", line 3`) to open it in Files at that line.
 *  - ⌘K (Ctrl+Shift+K elsewhere): describe a command in words and the model
 *    writes it at the prompt — never run; Enter stays yours.
 *
 * terminal.js owns the shells; it calls attachAi / noteInput / afterOutput /
 * clearAi / detachAi per shell and hands over what this needs in `initTerminalAi`.
 */
import { $, showToast, copyText, haptic, isMac } from './utils.js';
import { apiAt } from './api.js';
import { icon } from './icons.js';
import { store } from './store.js';
import { matches } from './keymap.js';

const SCAN_MS = 220;            // output is checked for errors at most this often
const MAX_SEND_LINES = 200;     // rows of a command's output sent to the chat …
const HEAD_LINES = 20;          // … the first of which (command, first error) always stay
const MAX_SEND_CHARS = 16_000;
const FALLBACK_LINES = 40;      // no command seen (a reload): this much before the error
const ASK_CONTEXT_LINES = 30;   // recent output the command writer sees
const MAX_WRAP_ROWS = 30;       // longest wrapped line scanned for links
const ASK_HISTORY = 20;
const COARSE = window.matchMedia?.('(pointer: coarse)');
const MOD = isMac ? '⌘' : 'Ctrl';

/** Output rows that say something failed. Tested one row at a time, never
 *  on the command line itself — so `grep error log.txt` doesn't count. */
const ERROR_PATTERNS = [
  /\bcommand not found\b/,
  /^(?:zsh|bash|sh|dash|fish): (?!correct )\S/,          // the shell itself complaining
  /\bTraceback \(most recent call last\):/,
  /^\s*(?:[A-Z]\w*)?(?:Error|Exception)(?: \[[^\]]+\])?: \S/, // TypeError: …  Error: …
  /^\s*(?:error|fatal)(?:\[[A-Z]?\d+\])?: \S/i,            // error: …  error[E0425]: …  fatal: …
  /:\d+(?::\d+)?:? (?:fatal )?error\b/i,                    // file.c:3:5: error …
  /\bnpm (?:ERR!|error) /,
  /^\s*ERR(?:!|_[A-Z_]+)\b/,
  /^\[(?:ERROR|FATAL)\]/,
  /\b(?:Segmentation fault|Bus error|Abort trap|core dumped)\b/,
  /\bpanicked at\b/,
  /\bUnhandled(?:Promise)?Rejection\b/,
  /\b(?:ModuleNotFoundError|ImportError|SyntaxError)\b/,
  /\bCannot find module\b/,
  /\bNo such file or directory\b/,
  /\bPermission denied\b/,
  /^(?:FAILED|ERROR) \S/,                                    // pytest's summary
  /\b(?:BUILD|Build|build) FAILED\b/,
  /\bexited with (?:exit )?code [1-9]\d*/,
];

let ctx = {
  active: () => null,         // the shell the toolbar acts on
  typeInto: () => {},         // (s, data) keys to a shell
  focus: () => {},            // focus the active shell
  relayout: () => {},         // panel rows changed: refit the shells
  roomForChat: () => {},      // un-maximise so the transcript + composer show
};
const hooks = { ask: null, addToChat: null, openFile: null };

let asking = false;
const askHistory = [];
let historyAt = -1;

// ─── Wiring (terminal.js) ──────────────────────────────────────────────
export function initTerminalAi(context, { onAsk, onAddToChat, onOpenFile } = {}) {
  ctx = { ...ctx, ...context };
  Object.assign(hooks, { ask: onAsk || null, addToChat: onAddToChat || null, openFile: onOpenFile || null });
  initAskBar();
  $('term-ai-last')?.addEventListener('click', () => addLastOutput());
}

/** A new xterm view: error chip, selection bar, link tips and link provider. */
export function attachAi(s) {
  const ai = {
    cmd: null, scan: null, error: null, timer: 0, point: null, disposables: [],
    chip: buildChip(s), sel: buildSelectionBar(s), tip: document.createElement('div'),
  };
  s.ai = ai;
  ai.tip.className = 'term-link-tip';
  ai.tip.hidden = true;
  ai.tip.setAttribute('aria-hidden', 'true');
  s.pane.append(ai.chip, ai.sel, ai.tip);
  try {
    ai.disposables.push(s.term.registerLinkProvider({ provideLinks: (y, done) => done(linksOnRow(s, y)) }));
    ai.disposables.push(s.term.onSelectionChange(() => { if (!s.term.hasSelection()) hideSelectionBar(s); else if (COARSE?.matches) showSelectionBar(s); }));
  } catch (err) { console.warn('terminal AI hooks failed', err); }
  // A drag ends: offer the selection to Jarvis right where the mouse let go.
  s.pane.addEventListener('pointerup', (e) => {
    if (e.target.closest?.('.term-ai-sel, .term-ai-chip')) return;
    const r = s.pane.getBoundingClientRect();
    ai.point = { x: e.clientX - r.left, y: e.clientY - r.top };
    setTimeout(() => { if (s.term.hasSelection()) showSelectionBar(s); }, 0);
  });
  s.pane.addEventListener('wheel', () => hideSelectionBar(s), { passive: true });
}

export function detachAi(s) {
  const ai = s.ai;
  if (!ai) return;
  clearTimeout(ai.timer);
  for (const d of ai.disposables) { try { d.dispose(); } catch { /* gone with the view */ } }
  ai.cmd?.dispose?.();
  ai.scan?.dispose?.();
  ai.error?.marker?.dispose?.();
  s.ai = null;
}

/** What you typed into a shell (not replayed history). Enter at the prompt
 *  starts a new command: its row is where its output — and any error — begins. */
export function noteInput(s, data) {
  const ai = s.ai;
  if (!ai) return;
  hideSelectionBar(s);
  if (!data.includes('\r') || onAltScreen(s)) return;
  const b = buffer(s);
  const here = cursorRow(s);
  let row = here;
  while (row > 0 && b.getLine(row)?.isWrapped) row--;
  ai.cmd?.dispose?.();
  ai.cmd = marker(s, row - here);
  ai.scan?.dispose?.();
  ai.scan = marker(s, 0);
  clearError(s);
}

/** Output was drawn. `replayed`: history from before this page attached —
 *  old errors there aren't news, so scanning starts after it. */
export function afterOutput(s, replayed) {
  const ai = s.ai;
  if (!ai) return;
  if (replayed) {
    clearTimeout(ai.timer);
    ai.timer = 0;
    ai.scan?.dispose?.();
    ai.scan = onAltScreen(s) ? null : marker(s, 0);
    return;
  }
  if (!ai.timer) ai.timer = setTimeout(() => { ai.timer = 0; scan(s); }, SCAN_MS);
}

/** The Clear button: the screen is gone, and so is what pointed into it. */
export function clearAi(s) {
  if (!s?.ai) return;
  clearError(s);
  hideSelectionBar(s);
  s.ai.cmd?.dispose?.();
  s.ai.cmd = null;
  s.ai.scan?.dispose?.();
  s.ai.scan = null;
}

/** Keys inside a shell that belong to the AI tools. True when handled. */
export function handleAiKey(e) {
  if (e.type !== 'keydown') return false;
  if (matches(e, 'term.ai')) {
    // Not the page's ⌘K palette: here it writes a command.
    e.preventDefault();
    e.stopPropagation();
    setAsk(true);
    return true;
  }
  return false;
}

// ─── Reading the screen ────────────────────────────────────────────────
const buffer = (s) => s.term.buffer.active;
const onAltScreen = (s) => buffer(s).type === 'alternate';
const cursorRow = (s) => { const b = buffer(s); return b.baseY + b.cursorY; };
const rowOf = (m) => (m && !m.isDisposed ? m.line : -1);

function marker(s, offset) {
  try { return s.term.registerMarker(offset) || null; } catch { return null; }
}

/** Rows [from, to] as text lines, wrapped rows joined back into their line. */
function readRows(s, from, to) {
  const b = buffer(s);
  const out = [];
  const last = Math.min(to, b.length - 1);
  for (let i = Math.max(0, from); i <= last; i++) {
    const line = b.getLine(i);
    if (!line) continue;
    const text = line.translateToString(!b.getLine(i + 1)?.isWrapped);
    if (line.isWrapped && out.length) out[out.length - 1] += text;
    else out.push(text.replace(/\s+$/, ''));
  }
  while (out.length && !out[out.length - 1].trim()) out.pop();
  while (out.length && !out[0].trim()) out.shift();
  return out;
}

/** Long output keeps its head (the command, the first error) and its tail. */
function clip(lines) {
  let keep = lines;
  if (lines.length > MAX_SEND_LINES) {
    const cut = lines.length - MAX_SEND_LINES;
    keep = [...lines.slice(0, HEAD_LINES), `… (${cut} lines cut) …`, ...lines.slice(HEAD_LINES + cut)];
  }
  let text = keep.join('\n');
  if (text.length > MAX_SEND_CHARS) text = `…${text.slice(-MAX_SEND_CHARS)}`;
  return text;
}

/** The last command and what it printed so far (or the last rows, when the
 *  command was typed before this page attached). */
function lastCommandOutput(s, near = -1) {
  const end = cursorRow(s);
  const cmd = rowOf(s.ai?.cmd);
  const from = cmd >= 0 && (near < 0 || cmd <= near) ? cmd : Math.max(0, (near >= 0 ? near : end) - FALLBACK_LINES);
  return clip(readRows(s, from, end));
}

function scan(s) {
  const ai = s.ai;
  if (!ai || s.disposed || ai.error || onAltScreen(s)) return;
  const b = buffer(s);
  const end = cursorRow(s);
  let from = Math.max(0, rowOf(ai.scan));
  const cmd = rowOf(ai.cmd);
  if (cmd >= 0) {
    let first = cmd + 1;
    while (b.getLine(first)?.isWrapped) first++;   // the rest of a long command line
    from = Math.max(from, first);
  }
  for (let i = from; i <= end; i++) {
    const text = b.getLine(i)?.translateToString(true) || '';
    if (text && ERROR_PATTERNS.some((re) => re.test(text))) {
      showError(s, i, text.trim());
      break;
    }
  }
  // The cursor's row may still be growing: look at it again next time.
  ai.scan?.dispose?.();
  ai.scan = marker(s, 0);
}

// ─── Sending to the open chat ──────────────────────────────────────────
function fence(text) {
  const ticks = text.includes('```') ? '~~~~' : '```';
  return `${ticks}console\n${text}\n${ticks}`;
}

async function askJarvis(prompt) {
  if (!hooks.ask) return;
  ctx.roomForChat();
  const queued = store.busy;
  haptic(8);
  await hooks.ask(prompt);
  showToast(queued ? 'Queued for Jarvis — it runs after the current reply' : 'Sent to Jarvis');
}

function addToChat(text) {
  if (!hooks.addToChat) return;
  ctx.roomForChat();
  hooks.addToChat(`From my terminal:\n\n${fence(text)}\n\n`);
  haptic(6);
}

function addLastOutput() {
  const s = ctx.active();
  if (!s || s.disposed) { showToast('Open a terminal first.', true); return; }
  const text = lastCommandOutput(s);
  if (!text.trim()) { showToast('Nothing in this terminal yet.'); return; }
  addToChat(text);
}

// ─── Error chip ────────────────────────────────────────────────────────
function buildChip(s) {
  const chip = document.createElement('div');
  chip.className = 'term-ai-chip';
  chip.hidden = true;
  chip.setAttribute('role', 'status');
  chip.innerHTML = `
    <span class="tai-flag">${icon('triangle-alert')}<span class="tai-flag-text">Error in the output</span></span>
    <button type="button" class="tai-btn is-primary" data-act="fix">${icon('sparkles')}<span>Fix with Jarvis</span></button>
    <button type="button" class="tai-btn" data-act="add" title="Put the command and its output in your message, to add a question first">${icon('message-square')}<span>Add to message</span></button>
    <button type="button" class="tai-x" data-act="dismiss" aria-label="Dismiss" title="Dismiss">${icon('x')}</button>`;
  chip.addEventListener('click', (e) => {
    const act = e.target.closest?.('[data-act]')?.dataset.act;
    if (!act) return;
    const err = s.ai?.error;
    if (act === 'dismiss' || !err) { clearError(s); ctx.focus(); return; }
    const out = lastCommandOutput(s, rowOf(err.marker));
    clearError(s);
    if (act === 'fix') askJarvis(`This failed in my terminal. Find out why and fix it.\n\n${fence(out)}`);
    else addToChat(out);
  });
  return chip;
}

function showError(s, row, text) {
  const ai = s.ai;
  ai.error = { marker: marker(s, row - cursorRow(s)), text };
  ai.chip.querySelector('.tai-flag').title = text.length > 200 ? `${text.slice(0, 199)}…` : text;
  ai.chip.querySelector('[data-act="fix"]').title = 'Send the command and its output to the open chat — Jarvis finds the cause and fixes it';
  ai.chip.hidden = false;
  requestAnimationFrame(() => ai.chip.classList.add('is-shown'));
}

function clearError(s) {
  const ai = s.ai;
  if (!ai) return;
  ai.error?.marker?.dispose?.();
  ai.error = null;
  ai.chip.classList.remove('is-shown');
  ai.chip.hidden = true;
}

// ─── Selection bar ─────────────────────────────────────────────────────
function buildSelectionBar(s) {
  const bar = document.createElement('div');
  bar.className = 'term-ai-sel';
  bar.hidden = true;
  bar.setAttribute('role', 'toolbar');
  bar.setAttribute('aria-label', 'Selected text');
  bar.innerHTML = `
    <button type="button" class="tai-btn is-primary" data-act="ask" title="Ask Jarvis to explain it — and fix it if something went wrong">${icon('sparkles')}<span>Ask Jarvis</span></button>
    <button type="button" class="tai-btn" data-act="add" title="Put it in your message">${icon('message-square')}<span>Add to message</span></button>
    <button type="button" class="tai-x" data-act="copy" aria-label="Copy" title="Copy">${icon('copy')}</button>`;
  // Keep the selection while a button is pressed.
  bar.addEventListener('mousedown', (e) => e.preventDefault());
  bar.addEventListener('click', async (e) => {
    const act = e.target.closest?.('[data-act]')?.dataset.act;
    const text = s.term.getSelection().replace(/\s+$/, '');
    if (!act || !text.trim()) { hideSelectionBar(s); return; }
    hideSelectionBar(s);
    if (act === 'copy') { await copyText(text); showToast('Copied'); s.term.focus(); return; }
    s.term.clearSelection();
    if (act === 'ask') askJarvis(`Explain this output from my terminal. If something went wrong, find out why and fix it.\n\n${fence(text)}`);
    else addToChat(text);
  });
  return bar;
}

function showSelectionBar(s) {
  const ai = s.ai;
  if (!ai || !s.term.getSelection().trim()) return;
  const bar = ai.sel;
  bar.hidden = false;
  const W = bar.offsetWidth || 260;
  const H = bar.offsetHeight || 30;
  const pw = s.pane.clientWidth;
  const ph = s.pane.clientHeight;
  const p = ai.point && !COARSE?.matches ? ai.point : { x: pw - W / 2 - 10, y: 6 - H };
  let y = p.y + 12;
  if (y + H > ph - 6) y = p.y - H - 12;   // no room below the pointer
  const x = Math.max(6, Math.min(p.x - W / 2, pw - W - 6));
  bar.style.left = `${Math.round(x)}px`;
  bar.style.top = `${Math.round(Math.max(6, y))}px`;
  requestAnimationFrame(() => bar.classList.add('is-shown'));
}

function hideSelectionBar(s) {
  const bar = s.ai?.sel;
  if (!bar || bar.hidden) return;
  bar.classList.remove('is-shown');
  bar.hidden = true;
}

// ─── Links ─────────────────────────────────────────────────────────────
const URL_RE = /\bhttps?:\/\/[^\s"'<>`{}|\\^]+/g;
const HOST_RE = /(?<![\w/.:@-])(?:localhost|127\.0\.0\.1|0\.0\.0\.0):\d{2,5}(?:\/[^\s"'<>`{}|\\^]*)?/g;
const PY_RE = /File "([^"\n]+)", line (\d+)/g;
// `src/app.py`, `./a.ts:12:5`, `/abs/x.go:3`, `a.ts(12,5)`, `app.py:42` (a bare name needs a line).
const PATH_RE = /(?<![\w./~@:-])((?:~|\.{1,2})?\/?(?:[\w@+-][\w.@+-]*\/)*[\w@+-][\w.@+-]*\.[A-Za-z]\w{0,9})(?::(\d+)(?::\d+)?|\((\d+),\s?\d+\))?(?![\w/])/g;
const LOOPBACK = new Set(['localhost', '127.0.0.1', '0.0.0.0', '[::1]', '::1']);
const PRIVATE_HOST = /^(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)|\.local$/;

const count = (str, ch) => str.split(ch).length - 1;

function trimUrl(url) {
  let u = url;
  for (;;) {
    if (/[.,;:!?'"]$/.test(u)) { u = u.slice(0, -1); continue; }
    const open = { ')': '(', ']': '[' }[u.slice(-1)];
    if (open && count(u, open) < count(u, u.slice(-1))) { u = u.slice(0, -1); continue; }
    return u;
  }
}

/** A dev server printed `localhost:5173`: from a phone on the LAN that's the
 *  computer Jarvis runs on, not the phone. */
function reachable(url) {
  try {
    const u = new URL(url);
    if (!LOOPBACK.has(u.hostname)) return u.href;
    const here = location.hostname;
    if (!LOOPBACK.has(here) && PRIVATE_HOST.test(here)) u.hostname = here;
    else if (u.hostname === '0.0.0.0') u.hostname = 'localhost';
    return u.href;
  } catch {
    return url;
  }
}

/** A path as the Files drawer names it (relative to the project), or null
 *  when it's outside the project. */
function projectRelative(raw) {
  let p = String(raw || '').trim();
  if (!p || p.startsWith('~')) return null;
  const root = String(store.session?.cwd || '').replace(/\/+$/, '');
  if (p.startsWith('/')) {
    if (!root || !(p === root || p.startsWith(`${root}/`))) return null;
    p = p.slice(root.length + 1);
  }
  const parts = [];
  for (const part of p.split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') {
      if (!parts.length) return null;
      parts.pop();
    } else {
      parts.push(part);
    }
  }
  return parts.length ? parts.join('/') : null;
}

/** Links on buffer row `y` (1-based), found in the whole wrapped line around it. */
function linksOnRow(s, y) {
  const b = buffer(s);
  const cols = s.term.cols;
  if (!cols) return [];
  let start = y - 1;
  while (start > 0 && y - 1 - start < MAX_WRAP_ROWS && b.getLine(start)?.isWrapped) start--;
  let end = y - 1;
  while (end - start < MAX_WRAP_ROWS && b.getLine(end + 1)?.isWrapped) end++;
  let text = '';
  for (let i = start; i <= end; i++) text += b.getLine(i)?.translateToString(i === end) ?? '';
  if (!text.trim()) return [];

  const at = (k) => ({ x: (k % cols) + 1, y: start + Math.floor(k / cols) + 1 });
  const taken = [];
  const links = [];
  const add = (from, to, kind, target) => {
    if (to <= from || taken.some(([a, z]) => from < z && to > a)) return;
    taken.push([from, to]);
    const range = { start: at(from), end: at(to - 1) };
    if (range.start.y > y || range.end.y < y) return;   // xterm asks one row at a time
    links.push({
      range,
      text: text.slice(from, to),
      decorations: { underline: true, pointerCursor: true },
      activate: (e) => openLink(e, kind, target),
      hover: (e) => showTip(s, e, kind, target),
      leave: () => hideTip(s),
    });
  };

  for (const m of text.matchAll(URL_RE)) {
    const url = trimUrl(m[0]);
    add(m.index, m.index + url.length, 'url', url);
  }
  for (const m of text.matchAll(HOST_RE)) {
    const host = trimUrl(m[0]);
    add(m.index, m.index + host.length, 'url', `http://${host}`);
  }
  for (const m of text.matchAll(PY_RE)) {
    add(m.index + 6, m.index + m[0].length, 'file', { path: m[1], line: Number(m[2]) });
  }
  for (const m of text.matchAll(PATH_RE)) {
    const line = Number(m[2] || m[3] || 0);
    if (!m[1].includes('/') && !line) continue;   // `e.g.`, `v1.2`, `example.com`
    add(m.index, m.index + m[0].length, 'file', { path: m[1], line });
  }
  return links;
}

function openLink(e, kind, target) {
  if (!COARSE?.matches && !(e.metaKey || e.ctrlKey)) return;   // a plain click focuses / selects
  if (kind === 'url') {
    window.open(reachable(target), '_blank', 'noopener,noreferrer');
    return;
  }
  const rel = projectRelative(target.path);
  if (rel === null) {
    copyText(target.path).then(() => showToast('Copied the path — it’s outside this project, so Files can’t open it'));
    return;
  }
  hooks.openFile?.(rel, target.line || 0);
}

function showTip(s, e, kind, target) {
  const tip = s.ai?.tip;
  if (!tip) return;
  const what = kind === 'url' ? 'open the link'
    : projectRelative(target.path) === null ? 'copy the path'
      : `open in Files${target.line ? ` at line ${target.line}` : ''}`;
  tip.textContent = COARSE?.matches ? `Tap to ${what}` : `${MOD}-click to ${what}`;
  tip.hidden = false;
  const r = s.pane.getBoundingClientRect();
  const W = tip.offsetWidth || 160;
  const x = Math.max(6, Math.min(e.clientX - r.left + 12, s.pane.clientWidth - W - 6));
  let y = e.clientY - r.top + 18;
  if (y + 26 > s.pane.clientHeight) y = e.clientY - r.top - 34;
  tip.style.left = `${Math.round(x)}px`;
  tip.style.top = `${Math.round(y)}px`;
}

function hideTip(s) {
  if (s.ai?.tip) s.ai.tip.hidden = true;
}

// ─── ⌘K: a command from words ──────────────────────────────────────────
function initAskBar() {
  const form = $('term-ask');
  const input = $('term-ask-input');
  if (!form || !input) return;
  $('term-ai')?.addEventListener('click', () => setAsk(form.hidden));
  $('term-ask-close')?.addEventListener('click', () => setAsk(false));
  form.addEventListener('submit', (e) => { e.preventDefault(); runAsk(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      setAsk(false);
    } else if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && askHistory.length && !asking) {
      e.preventDefault();
      historyAt = e.key === 'ArrowUp'
        ? Math.min(askHistory.length - 1, historyAt + 1)
        : Math.max(-1, historyAt - 1);
      input.value = historyAt < 0 ? '' : askHistory[historyAt];
    }
  });
}

export function setAsk(open) {
  const form = $('term-ask');
  const input = $('term-ask-input');
  if (!form || !input) return;
  form.hidden = !open;
  $('term-ai')?.setAttribute('aria-pressed', String(open));
  historyAt = -1;
  if (open) { input.focus(); input.select(); }
  else ctx.focus();
  ctx.relayout();
}

async function runAsk() {
  const form = $('term-ask');
  const input = $('term-ask-input');
  const request = input.value.trim();
  if (!request || asking) return;
  const s = ctx.active();
  if (!s?.id || s.exited || s.disposed) { showToast('Open a terminal first.', true); return; }
  if (onAltScreen(s)) { showToast('A full-screen program is running in this terminal — quit it first.', true); return; }
  asking = true;
  form.classList.add('is-busy');
  input.readOnly = true;
  form.setAttribute('aria-busy', 'true');
  const here = cursorRow(s);
  const context = clip(readRows(s, Math.max(0, here - ASK_CONTEXT_LINES), here));
  try {
    // The shell lives in its own project's Jarvis (`s.inst`): ask that one.
    const res = await apiAt(s.inst, '/api/terminal/suggest', 'POST', { id: s.id, request, context });
    if (!res?.ok || !res.command) throw new Error(res?.error || 'no command came back');
    if (askHistory[0] !== request) askHistory.unshift(request);
    askHistory.length = Math.min(askHistory.length, ASK_HISTORY);
    input.value = '';
    setAsk(false);
    if (s.disposed || s.exited) return;
    ctx.typeInto(s, '\x15');     // clear whatever is typed at the prompt
    s.term.paste(res.command);   // written, not run: Enter is yours
    if (res.risky) showToast('Read this one before you press Enter — it can delete or change things.', true);
    else showToast('Written at the prompt — press Enter to run it');
  } catch (err) {
    showToast(`No command: ${err?.payload?.error || err?.message || 'the request failed'}`, true);
  } finally {
    asking = false;
    form.classList.remove('is-busy');
    form.removeAttribute('aria-busy');
    input.readOnly = false;
  }
}

/** For tests and the page console: does this row read like a failure? */
export function looksLikeError(text) {
  return ERROR_PATTERNS.some((re) => re.test(String(text || '')));
}
