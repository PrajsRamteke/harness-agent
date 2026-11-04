/** Find in file — the bar that ⌘F / Ctrl+F opens over the code editor (files.js).
 *
 * The editor is a <textarea>, so the browser's own find can't mark matches in it
 * (and can't reach lines scrolled out of view). This bar searches the textarea's
 * text itself: every match is painted by a plain copy of the text laid under the
 * coloured layer (.fb-fm-in, matches wrapped in <mark>), and the current match is
 * the textarea's selection — so Enter / ⇧Enter / ⌘G walk through them and the
 * caret ends up where you stopped.
 *
 * files.js owns the editor; this module only needs the `.fb-view` element that
 * holds it (see mountEditor) and the hooks given to initFind(). */
import { escapeHtml, storageGet, storageSet } from './utils.js';
import { icon } from './icons.js';

const OPTS_KEY = 'jarvis-files-find-opts';
const MAX_MATCHES = 5000;        // past this the count reads "5000+" and no more are kept
const MARKS_MAX_CHARS = 300_000; // bigger files are searched but not painted (same cap as the colouring)
const REFRESH_MS = 120;          // wait after an edit before searching the new text again
const SEEK_MS = 90;              // wait after a keystroke in the box before jumping to the match
const LINE_PX = 20;              // the editor's line height (css .fb-text)
const PAD_PX = 12;               // the editor's top padding (same)

const state = {
  open: false,
  query: '',
  opts: loadOpts(),              // { cs: match case, word: whole word, re: regex }
  matches: [],
  capped: false,
  idx: -1,
  invalid: false,
};
let hooks = { onMove: () => {}, onToggle: () => {}, escHeld: () => false };
let refreshTimer = 0;
let seekTimer = 0;

function loadOpts() {
  try {
    const o = JSON.parse(storageGet(OPTS_KEY, '{}'));
    return { cs: !!o.cs, word: !!o.word, re: !!o.re };
  } catch {
    return { cs: false, word: false, re: false };
  }
}

/** `onMove`: the selection moved (repaint the caret line / status). `onToggle(open)`: the bar opened or closed. `escHeld`: true while something else (a change peek) still needs the editor's Esc. */
export function initFind(h = {}) {
  hooks = { ...hooks, ...h };
}

export const isFindOpen = () => state.open;

const barOf = (root) => root?.querySelector('.fb-find') || null;
const taOf = (root) => root?.querySelector('.fb-text') || null;

function regexFor() {
  if (!state.query) return null;
  let src = state.opts.re ? state.query : state.query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  if (state.opts.word) src = `\\b(?:${src})\\b`;
  try {
    return new RegExp(src, state.opts.cs ? 'gm' : 'gim');
  } catch {
    return null;
  }
}

/** Every match of the query in `text` as [start, end] pairs (empty matches skipped). */
function findAll(text) {
  state.invalid = false;
  state.capped = false;
  const re = regexFor();
  if (!re) {
    state.invalid = !!state.query;
    return [];
  }
  const out = [];
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m[0] === '') {
      re.lastIndex += 1;
    } else {
      out.push([m.index, m.index + m[0].length]);
      if (out.length >= MAX_MATCHES) {
        state.capped = true;
        break;
      }
    }
  }
  return out;
}

/** Index of the first match that starts at or after `pos` (wraps to the first). */
function nearest(pos) {
  if (!state.matches.length) return -1;
  const i = state.matches.findIndex(([s]) => s >= pos);
  return i < 0 ? 0 : i;
}

// ─── The painted copy ─────────────────────────────────────────────────────

function paintMarks(root) {
  const layer = root?.querySelector('.fb-fm-in');
  const ta = taOf(root);
  if (!layer || !ta) return;
  const on = state.open && state.matches.length && ta.value.length <= MARKS_MAX_CHARS;
  if (!on) {
    layer.textContent = '';
    return;
  }
  layer.style.tabSize = ta.style.tabSize || '4';
  const text = ta.value;
  let html = '';
  let at = 0;
  state.matches.forEach(([s, e], i) => {
    html += `${escapeHtml(text.slice(at, s))}<mark${i === state.idx ? ' class="is-cur"' : ''}>${escapeHtml(text.slice(s, e))}</mark>`;
    at = e;
  });
  layer.innerHTML = html + escapeHtml(text.slice(at));
  syncFind(root);
}

/** Keep the painted copy under the textarea's scroll (called from files.js next to syncHighlight). */
export function syncFind(root) {
  const layer = root?.querySelector('.fb-fm-in');
  const ta = taOf(root);
  if (layer && ta && layer.firstChild) layer.style.transform = `translate(${-ta.scrollLeft}px, ${-ta.scrollTop}px)`;
}

/** Move the current-match highlight without repainting everything. */
function markCurrent(root) {
  const layer = root?.querySelector('.fb-fm-in');
  if (!layer || !layer.firstChild) return null;
  layer.querySelector('mark.is-cur')?.classList.remove('is-cur');
  const mk = layer.querySelectorAll('mark')[state.idx] || null;
  mk?.classList.add('is-cur');
  return mk;
}

/** Scroll the textarea so the current match is on screen (a third of the way down when it wasn't). */
function reveal(root, mk) {
  const ta = taOf(root);
  const m = state.matches[state.idx];
  if (!ta || !m) return;
  let top;
  let left = null;
  let right = null;
  if (mk) {
    top = mk.offsetTop;
    left = mk.offsetLeft;
    right = mk.offsetLeft + mk.offsetWidth;
  } else { // not painted (huge file): count the lines instead — right unless the lines wrap
    top = PAD_PX + ta.value.slice(0, m[0]).split('\n').length * LINE_PX - LINE_PX;
  }
  const bar = barOf(root);
  const covered = bar && !bar.hidden ? bar.offsetHeight + 12 : 0; // the bar sits over the top right
  if (top < ta.scrollTop + LINE_PX || top + LINE_PX * 2 > ta.scrollTop + ta.clientHeight - LINE_PX) {
    ta.scrollTop = Math.max(0, top - Math.max(ta.clientHeight / 3, covered + LINE_PX));
  }
  if (left !== null && ta.scrollWidth > ta.clientWidth && (left < ta.scrollLeft || right > ta.scrollLeft + ta.clientWidth - 24)) {
    ta.scrollLeft = Math.max(0, left - ta.clientWidth / 3);
  }
}

// ─── The bar ──────────────────────────────────────────────────────────────

function build(root) {
  const bar = document.createElement('div');
  bar.className = 'fb-find';
  bar.setAttribute('role', 'search');
  bar.setAttribute('aria-label', 'Find in file');
  const opt = (key, label, tip) => `<button type="button" class="fb-find-opt" data-opt="${key}" aria-pressed="${state.opts[key]}" title="${tip}" aria-label="${tip}">${label}</button>`;
  const step = (dir, ic, tip) => `<button type="button" class="tool-icon" data-find="${dir}" title="${tip}" aria-label="${tip}">${icon(ic)}</button>`;
  bar.innerHTML = `
    <span class="fb-find-ic">${icon('search')}</span>
    <input class="fb-find-q" type="text" placeholder="Find" aria-label="Find in file" spellcheck="false" autocomplete="off"
      autocapitalize="off" autocorrect="off" data-esc-cancels data-gramm="false" data-1p-ignore data-lpignore="true">
    ${opt('cs', 'Aa', 'Match case')}${opt('word', '<u>ab</u>', 'Whole word')}${opt('re', '.*', 'Regular expression')}
    <span class="fb-find-n" aria-live="polite"></span>
    ${step('prev', 'chevron-up', 'Previous match (⇧↵)')}${step('next', 'chevron-down', 'Next match (↵)')}
    <button type="button" class="tool-icon" data-find="close" title="Close (Esc)" aria-label="Close find">${icon('x')}</button>`;
  root.appendChild(bar);
  const input = bar.querySelector('.fb-find-q');
  input.value = state.query;

  input.addEventListener('input', () => {
    state.query = input.value;
    clearTimeout(seekTimer);
    seekTimer = setTimeout(() => {
      seekTimer = 0;
      search(root, { select: true });
    }, SEEK_MS);
  });
  input.addEventListener('keydown', (e) => {
    if (e.isComposing || e.key !== 'Enter') return;
    e.preventDefault();
    if (seekTimer) { // typed a moment ago: search first, that is the "next" the user means
      clearTimeout(seekTimer);
      seekTimer = 0;
      search(root, { select: true });
    } else step(root, e.shiftKey ? -1 : 1);
  });
  input.addEventListener('esc-cancel', () => closeFind(root, { refocus: true }));

  bar.addEventListener('click', (e) => {
    const o = e.target.closest('[data-opt]');
    if (o) {
      state.opts[o.dataset.opt] = !state.opts[o.dataset.opt];
      o.setAttribute('aria-pressed', String(state.opts[o.dataset.opt]));
      storageSet(OPTS_KEY, JSON.stringify(state.opts));
      search(root, { select: true });
      input.focus({ preventScroll: true });
      return;
    }
    const b = e.target.closest('[data-find]');
    if (!b) return;
    if (b.dataset.find === 'close') closeFind(root, { refocus: true });
    else {
      step(root, b.dataset.find === 'prev' ? -1 : 1);
      input.focus({ preventScroll: true });
    }
  });
  return bar;
}

function paintCount(root) {
  const bar = barOf(root);
  if (!bar) return;
  const out = bar.querySelector('.fb-find-n');
  const n = state.matches.length;
  const input = bar.querySelector('.fb-find-q');
  let text = '';
  if (state.invalid) text = 'Invalid pattern';
  else if (state.query) text = n ? `${state.idx + 1} of ${n}${state.capped ? '+' : ''}` : 'No results';
  out.textContent = text;
  bar.classList.toggle('is-empty', !!state.query && !n);
  input.setAttribute('aria-invalid', String(state.invalid));
  bar.querySelectorAll('[data-find="prev"], [data-find="next"]').forEach((b) => { b.disabled = !n; });
}

/** Search the text again; `select` also lands on the match nearest the caret (and scrolls to it). */
function search(root, { select = false } = {}) {
  const ta = taOf(root);
  if (!ta) return;
  state.matches = findAll(ta.value);
  state.idx = nearest(select ? Math.min(ta.selectionStart, ta.selectionEnd) : ta.selectionStart);
  paintMarks(root);
  paintCount(root);
  if (select) land(root);
}

/** Select the current match in the textarea and bring it into view. */
function land(root) {
  const ta = taOf(root);
  const m = state.matches[state.idx];
  if (!ta || !m) return;
  ta.setSelectionRange(m[0], m[1]);
  reveal(root, markCurrent(root));
  paintCount(root);
  hooks.onMove();
}

function step(root, dir) {
  const n = state.matches.length;
  if (!n) return;
  state.idx = (state.idx + dir + n) % n;
  land(root);
}

/** Open the bar (or focus it again): the selected text, when it is one line, becomes the query. */
export function openFind(root) {
  const ta = taOf(root);
  if (!root || !ta) return;
  const picked = ta.value.slice(ta.selectionStart, ta.selectionEnd);
  if (picked && picked.length <= 200 && !picked.includes('\n')) state.query = picked;
  state.open = true;
  const bar = barOf(root) || build(root);
  bar.hidden = false;
  const input = bar.querySelector('.fb-find-q');
  input.value = state.query;
  ta.setAttribute('data-esc-cancels', ''); // Esc in the editor closes the bar before it closes the drawer
  search(root, { select: !!picked });
  input.focus({ preventScroll: true });
  input.select();
  hooks.onToggle(true);
}

export function closeFind(root, { refocus = false } = {}) {
  clearTimeout(refreshTimer);
  clearTimeout(seekTimer);
  seekTimer = 0;
  state.open = false;
  state.matches = [];
  state.idx = -1;
  const bar = barOf(root);
  bar?.remove();
  const layer = root?.querySelector('.fb-fm-in');
  if (layer) layer.textContent = '';
  const ta = taOf(root);
  if (ta && !hooks.escHeld()) ta.removeAttribute('data-esc-cancels');
  if (refocus) ta?.focus({ preventScroll: true });
  hooks.onToggle(false);
}

/** ⌘G / F3 / ⇧ variants: next or previous match, opening the bar first when it is shut. */
export function stepFind(root, dir) {
  if (!state.open) {
    openFind(root);
    return;
  }
  if (!state.matches.length) search(root, { select: true });
  else step(root, dir);
}

/** The editor was rebuilt (tab switch, disk reload): put the bar back if it was open. */
export function restoreFind(root) {
  if (!state.open || !root) return;
  const ta = taOf(root);
  if (!ta) return;
  const bar = barOf(root) || build(root);
  bar.querySelector('.fb-find-q').value = state.query;
  ta.setAttribute('data-esc-cancels', '');
  search(root);
}

/** The editor shows another view (diff, preview): the bar steps aside; `edit` brings it back. */
export function showFindFor(root, mode) {
  const bar = barOf(root);
  if (bar) bar.hidden = mode !== 'edit';
}

/** The text changed: the painted matches are stale at once, the new ones follow shortly. */
export function refreshFind(root) {
  if (!state.open || !root) return;
  const layer = root.querySelector('.fb-fm-in');
  if (layer) layer.textContent = '';
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    if (state.open && taOf(root)) search(root);
  }, REFRESH_MS);
}
