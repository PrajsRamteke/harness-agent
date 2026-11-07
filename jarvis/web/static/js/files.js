/** Files drawer (the git card's Files row, ⌥F, the command palette): browse the
 * project, open a file, edit it, save it — without leaving the page.
 *
 * Slides in from the right, like the Git changes drawer. Wide screens: a lazy
 * folder tree (or name-search results) on the left, the open file on the right.
 * Narrow screens / phones: the tree, and a file opens as a sub-view (back
 * button / Esc returns to the tree).
 *
 * The editor is a plain <textarea> with a line-number gutter: no library, so it
 * loads offline on the LAN. Tab / Shift+Tab indent, Enter keeps the indent,
 * ⌘/Ctrl+S saves, ⌘/ comments lines and the other line commands (editor_cmds.js)
 * follow keymap.js. Native undo is kept (edits go through execCommand).
 * Its text is coloured by a copy laid exactly underneath (syntax.js): the
 * textarea's own glyphs are transparent, the caret line gets a quiet band.
 *
 * Nothing is ever lost quietly:
 *   - saves carry the file's etag; if Jarvis (or anything) changed the file
 *     meanwhile the save asks first (overwrite / load the disk version);
 *   - a clean open file follows the disk (Jarvis's edits appear as they land);
 *   - unsaved edits survive closing the drawer and are guarded when you open
 *     another file or leave the page;
 *   - delete moves to the Trash.
 * Server side: jarvis/web/files_api.py via POST /api/files {op}. The diff view
 * reuses POST /api/git {op: diff} and diffview.js. */
import { $, escapeHtml, showToast, storageGet, storageSet, copyText, debounce, isMac, BASE } from './utils.js';
import { icon } from './icons.js';
import { api } from './api.js';
import { store, subscribe } from './store.js';
import { openModal, closeModal, isModalOpen } from './modal.js';
import { setView, setHomeSub, toolbar, section, footer, empty, arrowRows } from './dialog.js';
import { renderDiff, gutterWidth } from './diffview.js';
import { renderMarkdown } from './markdown.js';
import { diffLines } from './linediff.js';
import { highlight as colorize, modeFor } from './syntax.js';
import { initFind, openFind, closeFind, stepFind, restoreFind, refreshFind, syncFind, showFindFor, isFindOpen, pinMatch } from './findbar.js';
import { popOpen, closePop, showPop, popKey, wirePop } from './branchpop.js';
import { dockTerminal, undockTerminal, terminalState, toggleTerminal } from './terminal.js';
import { matches, matchAny, withHint } from './keymap.js';
import { commentFor, toggleLineComment, toggleBlockComment, shiftLines, moveLines, copyLines, deleteLines, insertLine, selectLine } from './editor_cmds.js';

const ID = 'files';
const STATE_KEY = 'jarvis-files';
const WRAP_KEY = 'jarvis-files-wrap';
const WIDE = '(min-width: 900px)';
const POLL_MS = 4000;
const TREE_EVERY = 3;            // disk checks between tree refreshes
const SEARCH_MS = 140;
const MAX_REMEMBERED_DIRS = 60;
const MAX_TABS = 40;             // open-file tabs remembered per project
const MOD = isMac ? '⌘' : 'Ctrl';
const LINE_PX = 20;              // the editor's line height (css .fb-text): the gutter bars are placed by it
const EDITOR_PAD_PX = 12;        // the editor's top padding (same)
const HL_MAX_CHARS = 300_000;    // bigger files stay plain: colouring runs on every keystroke
const CHANGES_MS = 140;          // wait this long after a keystroke before comparing with git
const PEEK_MAX_LINES = 30;
const DIFF_VIEW_KEY = 'jarvis-files-diffview'; // the Diff view: 'unified' | 'split' (side by side, wide screens)
const CONFIRM_ID = 'files-confirm';
const FULL_KEY = 'jarvis-files-full';
const BR_ID = 'files-br';        // the footer branch chip's switcher (index.html)
const PR_ID = 'files-pr';        // the Create pull request modal (index.html)
// The left pane: 'search' (Explorer, searching code in files — the default) | 'files' (Explorer, searching names) | 'scm' (Source Control).
// -v2: the first version had no code search, so a saved 'files' would have hidden the new default.
const PANE_KEY = 'jarvis-files-pane-v2';
const PANES = ['files', 'search', 'scm'];
const PANE_FOCUS = { files: `${ID}-search`, search: `${ID}-gq`, scm: `${ID}-msg` }; // the field each tab starts in
const GREP_OPTS_KEY = 'jarvis-files-grep-opts'; // { cs: match case, word: whole word, re: regex }
const GREP_MS = 220;               // wait after a keystroke before searching file contents
const GREP_SEED_MAX = 200;         // a selection longer than this doesn't become the query
const SCM_STATUS = {
  modified: ['M', 'Modified'], added: ['A', 'Added'], untracked: ['U', 'New file'], deleted: ['D', 'Deleted'],
  renamed: ['R', 'Renamed'], copied: ['C', 'Copied'], conflict: ['!', 'Conflict'],
};
const DIFF_NOTE = {
  local: 'Compared with what git has — your unsaved edits aren’t included.',
  staged: 'Staged — what the next commit gets, compared with the last commit.',
  unstaged: 'Not staged yet — the file on disk compared with what is staged. Unsaved edits aren’t included.',
};
const RESTART_NOTE = 'Restart Jarvis to finish updating — it still runs older code without the file browser';

const GIT_LABEL = { M: 'Modified', A: 'Added', U: 'New file', D: 'Deleted', R: 'Renamed', '!': 'Conflict' };
const LANG = {
  py: 'Python', js: 'JavaScript', mjs: 'JavaScript', cjs: 'JavaScript', jsx: 'JSX', ts: 'TypeScript', tsx: 'TSX',
  json: 'JSON', md: 'Markdown', mdx: 'MDX', html: 'HTML', htm: 'HTML', css: 'CSS', scss: 'SCSS', less: 'Less',
  yml: 'YAML', yaml: 'YAML', toml: 'TOML', ini: 'INI', cfg: 'Config', env: 'Env', sh: 'Shell', zsh: 'Shell',
  bash: 'Shell', rs: 'Rust', go: 'Go', java: 'Java', kt: 'Kotlin', swift: 'Swift', rb: 'Ruby', php: 'PHP',
  c: 'C', h: 'C header', cpp: 'C++', hpp: 'C++ header', cs: 'C#', sql: 'SQL', txt: 'Plain text', xml: 'XML',
  svg: 'SVG', vue: 'Vue', svelte: 'Svelte', lua: 'Lua', r: 'R', dart: 'Dart', gradle: 'Gradle', lock: 'Lockfile',
};
const NAMED_LANG = { Dockerfile: 'Dockerfile', Makefile: 'Makefile', '.gitignore': 'Git ignore', '.env': 'Env' };
const TEXT_EXTS = new Set(['md', 'mdx', 'txt', 'rst', 'log']);
const SHEET_EXTS = new Set(['csv', 'tsv', 'xlsx', 'xls']);
const ARCHIVE_EXTS = new Set(['zip', 'tar', 'gz', 'tgz', '7z', 'rar']);
const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'avif', 'svg']);
const TWO_SPACE_EXTS = new Set(['js', 'mjs', 'cjs', 'jsx', 'ts', 'tsx', 'json', 'yml', 'yaml', 'css', 'scss', 'less', 'html',
  'htm', 'vue', 'svelte', 'md', 'mdx', 'xml', 'rb', 'dart']);
const TAB_EXTS = new Set(['go']);

// ─── State ────────────────────────────────────────────────────────────────

let tree = new Map();            // folder path ('' = project) → { entries, loading, error, truncated, total }
let expanded = new Set();        // open folders
let cursorPath = '';             // the tree row last clicked / focused
let query = '';
let results = null;              // null (tree) | { items, loading, truncated }
let file = null;                 // the shown file's document (see openPath / applyRead) — one of `docs`
let docs = [];                   // the open tabs, left to right (each keeps its own unsaved edits)
let mounted = null;              // the document the editor <textarea> was built for
let pendingTabs = [];            // tabs of this project, restored on open
let prompt = null;               // side prompt: new file / folder, rename, delete
let banner = null;               // top bar: unsaved / conflict / changed / gone / revert
let reading = false;             // narrow screens: the file sub-view is open
let saving = false;
let shownFor = null;             // the project (BASE + folder) the drawer shows
let pendingFile = '';            // last open file of this project, restored on open
let loadingAll = false;
let pollTimer = null;
let ticks = 0;
let wasBusy = false;
let caretFrame = 0;
let curLineTop = 0;              // the caret line's offset in the text (px), for the band and the gutter
let hlResize = null;             // ResizeObserver keeping the coloured layer clear of the scrollbars
let noticeTimer = 0;
let searchSeq = 0;
let diffSeq = 0;
const dirSeq = new Map();
let pane = PANES.includes(storageGet(PANE_KEY, 'search')) ? storageGet(PANE_KEY, 'search') : 'search';
let explorer = pane === 'scm' ? 'search' : pane; // the Explorer mode the Explorer tab returns to
let gq = '';                      // the content search's text (kept while you switch tabs)
let gopts = loadGrepOpts();      // its toggles
let grep = null;                 // null | { loading?, query, files, matches, truncated, error?, bad? } — the last {op: grep} answer
let grepSeq = 0;
let grepTimer = 0;
let grepCur = '';                // 'path:line' of the match last opened (marked in the list)
const grepFolded = new Set();    // files whose matches are folded away
let scm = null;                  // last {op: scm} answer (Source Control tab)
let scmSel = null;               // { group, path } — the change whose diff is open
let scmMsg = '';                 // the commit message being written
let scmBusy = '';                // a git op the tab is running
let scmSeq = 0;
let scmMenuOpen = false;         // the commit button's ⌄ menu
let brOpen = false;              // the branch switcher
let brList = null;               // its {op: branches} answer
let brSeq = 0;
let prBranches = null;           // the PR modal's {op: branches} answer
let prBusy = '';                 // 'generate' | 'create'
let prUrl = '';                  // the last PR opened from here (link under the commit box)
const scmCollapsed = new Set();  // folded groups
let host = { openInEditor: null, editorLabel: () => 'your editor' };

const num = (n) => Number(n) || 0;
const wide = () => window.matchMedia(WIDE).matches;
const cardEl = () => $(ID)?.querySelector('.modal-card');
const listEl = () => $(`${ID}-list`);
const mainEl = () => $(`${ID}-main`);
const projectKey = () => `${BASE}|${store.session?.cwd || ''}`;
const docDirty = (d) => d?.kind === 'text' && d.text !== d.saved;
const isDirty = () => docDirty(file);
const dirtyDocs = () => docs.filter(docDirty);
/** A tab with unsaved edits (the shown one first), or null. */
const unsavedDoc = () => (isDirty() ? file : dirtyDocs()[0] || null);
const docFor = (path) => docs.find((d) => !d.virtual && d.path === path) || null;
const under = (path, root) => path === root || path.startsWith(`${root}/`);
export const isFilesDirty = () => dirtyDocs().length > 0;

function splitPath(p) {
  const i = p.lastIndexOf('/');
  return i < 0 ? ['', p] : [p.slice(0, i), p.slice(i + 1)];
}
const dirOf = (p) => splitPath(p)[0];
const baseOf = (p) => splitPath(p)[1];
const join = (dir, name) => (dir ? `${dir}/${name}` : name);

/** "a/b/c.py" → ["a", "a/b"]: the folders above a path, outermost first. */
function ancestorsOf(path) {
  const out = [];
  let cur = '';
  for (const part of dirOf(path).split('/').filter(Boolean)) {
    cur = join(cur, part);
    out.push(cur);
  }
  return out;
}

/** A button's icon turns into a check for a moment (Copy path). */
function flashCheck(btn) {
  const prev = btn.innerHTML;
  btn.classList.add('is-done');
  btn.innerHTML = icon('check');
  setTimeout(() => {
    btn.classList.remove('is-done');
    btn.innerHTML = prev;
  }, 1200);
}

function extOf(name) {
  const i = name.lastIndexOf('.');
  return i > 0 ? name.slice(i + 1).toLowerCase() : '';
}

function fileIcon(name) {
  const ext = extOf(name);
  if (IMAGE_EXTS.has(ext)) return 'image';
  if (TEXT_EXTS.has(ext)) return 'file-text';
  if (SHEET_EXTS.has(ext)) return 'file-spreadsheet';
  if (ARCHIVE_EXTS.has(ext)) return 'file-archive';
  if (LANG[ext]) return 'file-code';
  return 'file';
}

const languageOf = (name) => NAMED_LANG[name] || LANG[extOf(name)] || (extOf(name) ? extOf(name).toUpperCase() : 'Plain text');

function fmtSize(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Tab or the file's own number of spaces: what Tab inserts and Enter keeps. */
function detectIndent(text, name) {
  let tabs = 0;
  let spaces = 0;
  let odd = 0;
  for (const line of text.slice(0, 20000).split('\n')) {
    if (line[0] === '\t') {
      tabs += 1;
      continue;
    }
    const m = line.match(/^ +(?=\S)/);
    if (m) {
      spaces += 1;
      if (m[0].length % 4 !== 0) odd += 1;
    }
  }
  if (tabs > spaces) return '\t';
  if (spaces) return odd > spaces * 0.2 ? '  ' : '    ';
  const ext = extOf(name);
  if (TAB_EXTS.has(ext) || name === 'Makefile') return '\t';
  return TWO_SPACE_EXTS.has(ext) ? '  ' : '    ';
}

// ─── Persistence (per project) ────────────────────────────────────────────

function readSaved() {
  try {
    const v = JSON.parse(storageGet(`${STATE_KEY}:${projectKey()}`, '') || '{}');
    const strings = (a) => (Array.isArray(a) ? a.filter((p) => typeof p === 'string') : []);
    return { open: strings(v.open), file: typeof v.file === 'string' ? v.file : '', tabs: strings(v.tabs) };
  } catch {
    return { open: [], file: '', tabs: [] };
  }
}

function persist() {
  if (shownFor !== projectKey()) return;
  storageSet(`${STATE_KEY}:${shownFor}`, JSON.stringify({
    open: [...expanded].slice(0, MAX_REMEMBERED_DIRS),
    file: file && !file.virtual ? file.path : '',
    tabs: docs.filter((d) => !d.virtual).map((d) => d.path).slice(0, MAX_TABS),
  }));
}

function resetProject(key) {
  shownFor = key;
  tree = new Map();
  expanded = new Set();
  file = null;
  docs = [];
  mounted = null;
  banner = null;
  prompt = null;
  query = '';
  results = null;
  grep = null;
  grepSeq += 1;
  grepCur = '';
  grepFolded.clear();
  cursorPath = '';
  reading = false;
  scm = null;
  scmSel = null;
  scmMsg = '';
  prUrl = '';
  closeBranchMenu();
  const saved = readSaved();
  saved.open.forEach((p) => expanded.add(p));
  pendingFile = saved.file;
  pendingTabs = saved.tabs;
}

// ─── Server ───────────────────────────────────────────────────────────────

async function call(op, data = {}) {
  try {
    return await api('/api/files', 'POST', { op, ...data });
  } catch (err) {
    if (err?.status === 404 || err?.status === 405) return { ok: false, code: 'old_server', error: RESTART_NOTE };
    return { ok: false, error: err?.message || 'That did not work' };
  }
}

// ─── Tree ─────────────────────────────────────────────────────────────────

async function loadDir(path, { quiet = false } = {}) {
  const mine = (dirSeq.get(path) || 0) + 1;
  dirSeq.set(path, mine);
  const slot = tree.get(path);
  if (!quiet && !slot?.entries) {
    tree.set(path, { ...slot, loading: true, error: '' });
    paintTree();
  }
  const key = shownFor;
  const res = await call('list', { path });
  if (mine !== dirSeq.get(path) || key !== shownFor) return;
  if (res.ok) {
    tree.set(path, { entries: res.entries, truncated: num(res.truncated), total: num(res.total) });
  } else if (res.code === 'not_found' && path) {
    tree.delete(path);
    expanded.delete(path);
  } else {
    tree.set(path, { entries: tree.get(path)?.entries, error: res.error || 'Could not read this folder' });
  }
  paintTree();
  syncFileMark();
}

/** Root + every open folder, in parallel (open, after a change, on a timer). */
async function refreshTree({ quiet = true } = {}) {
  await Promise.all([loadScm(), ...['', ...expanded].map((p) => loadDir(p, { quiet }))]);
  persist();
}

async function loadAll() {
  loadingAll = true;
  paintTree();
  await refreshTree({ quiet: false });
  loadingAll = false;
  paintTree();
}

export function refreshFiles() {
  if (!isModalOpen(ID)) return;
  refreshTree();
  refreshBase();
}

/** Open every folder above `path` and bring its row into view. */
async function reveal(path) {
  const need = [];
  for (const dir of ancestorsOf(path)) {
    expanded.add(dir);
    if (!tree.get(dir)?.entries) need.push(dir);
  }
  paintTree();
  await Promise.all(need.map((d) => loadDir(d)));
  persist();
  listEl()?.querySelector('.fb-row.is-current')?.scrollIntoView({ block: 'nearest' });
}

function toggleDir(path) {
  if (expanded.has(path)) expanded.delete(path);
  else {
    expanded.add(path);
    if (!tree.get(path)?.entries) loadDir(path);
  }
  persist();
  paintTree();
}

function entryAt(path) {
  return tree.get(dirOf(path))?.entries?.find((e) => e.path === path) || null;
}

/** Where "New file" lands: the folder you are on, else the open file's, else the root. */
function targetDir() {
  const cur = cursorPath ? entryAt(cursorPath) : null;
  if (cur) return cur.dir ? cur.path : dirOf(cur.path);
  return file ? dirOf(file.path) : '';
}

function visibleRows() {
  const rows = [];
  const walk = (dir, level) => {
    const slot = tree.get(dir);
    for (const e of slot?.entries || []) {
      rows.push({ e, level });
      if (!e.dir || !expanded.has(e.path)) continue;
      const child = tree.get(e.path);
      if (child?.loading && !child.entries) rows.push({ note: 'Loading…', level: level + 1, spin: true });
      else if (child?.error && !child.entries) rows.push({ note: child.error, level: level + 1, bad: true });
      else if (child?.entries && !child.entries.length) rows.push({ note: 'Empty folder', level: level + 1 });
      walk(e.path, level + 1);
      if (child?.truncated) rows.push({ note: `${child.truncated} more not shown`, level: level + 1 });
    }
  };
  walk('', 0);
  return rows;
}

function gitBadge(git) {
  if (!git) return '';
  if (git === 'dirty') return '<span class="fb-git is-dirty" title="Contains changes"></span>';
  return `<span class="fb-git is-${escapeHtml(git === '!' ? 'conflict' : git)}" title="${escapeHtml(GIT_LABEL[git] || '')}">${escapeHtml(git)}</span>`;
}

function rowHtml({ e, note, level, spin, bad }) {
  if (note) {
    return `<div class="fb-note${bad ? ' is-bad' : ''}" style="--lvl:${level}">${spin ? '<span class="spinner"></span>' : ''}<span>${escapeHtml(note)}</span></div>`;
  }
  const open = e.dir && expanded.has(e.path);
  const on = !!file && file.path === e.path;
  const cls = ['fb-row', e.dir ? 'is-dir' : '', on ? 'is-current' : '', e.ignored ? 'is-ignored' : '', cursorPath === e.path ? 'is-cursor' : '']
    .filter(Boolean).join(' ');
  const dot = !e.dir && docDirty(docFor(e.path)) ? '<span class="fb-unsaved" title="Unsaved changes"></span>' : '';
  return `<button type="button" class="${cls}" role="treeitem" aria-level="${level + 1}"${e.dir ? ` aria-expanded="${open}"` : ''} aria-selected="${on}"
    data-path="${escapeHtml(e.path)}" data-dir="${e.dir ? 1 : 0}" tabindex="-1" style="--lvl:${level}" title="${escapeHtml(e.path)}${e.link ? ' (link)' : ''}">
    <span class="fb-chev">${e.dir ? icon('chevron-right') : ''}</span>
    <span class="fb-ic">${icon(e.dir ? (open ? 'folder-open' : 'folder') : fileIcon(e.name))}</span>
    <span class="fb-name">${escapeHtml(e.name)}</span>${dot}${gitBadge(e.git)}
  </button>`;
}

function paintTree() {
  const el = listEl();
  if (!el) return;
  if (pane === 'scm') {
    paintScm();
    return;
  }
  if (pane === 'search' && gq) { // an empty code search shows the tree, like the Explorer always did
    paintGrep();
    return;
  }
  if (results) {
    paintResults();
    return;
  }
  const root = tree.get('');
  if (!root?.entries) {
    el.innerHTML = root?.error
      ? empty('Couldn’t read the project', escapeHtml(root.error), {
        ic: 'triangle-alert', action: '<button type="button" class="btn" data-act="reload">Try again</button>',
      })
      : `<div class="gx-loading"><span class="spinner"></span><span>${loadingAll ? 'Reading files…' : 'Loading…'}</span></div>`;
    return;
  }
  const active = document.activeElement;
  const refocus = active && el.contains(active) ? active.dataset?.path : undefined;
  const scroll = el.scrollTop;
  const rows = visibleRows();
  el.innerHTML = rows.length
    ? `<div class="fb-tree" role="tree" aria-label="Project files">${rows.map(rowHtml).join('')}</div>`
    : empty('This folder is empty', 'Create a file with the + button above.', { ic: 'folder-open' });
  el.scrollTop = scroll;
  const stop = el.querySelector('.fb-row.is-cursor') || el.querySelector('.fb-row.is-current') || el.querySelector('.fb-row');
  stop?.setAttribute('tabindex', '0');
  if (refocus !== undefined) {
    [...el.querySelectorAll('.fb-row')].find((r) => r.dataset.path === refocus)?.focus({ preventScroll: true });
  }
}

/** A changed file's marker follows the tree (a save, a commit, Jarvis's edits). */
function syncFileMark() {
  if (!file || file.loading || file.error || file.virtual) return;
  const e = entryAt(file.path);
  if (!e) return;
  const git = e.dir ? '' : e.git || '';
  if (git === file.git) return;
  file.git = git;
  if (file.view === 'diff' && !git && !file.diffSide) file.view = 'edit';
  paintHead();
  if (file.kind === 'text') applyMode();
}

// ─── Search ───────────────────────────────────────────────────────────────

const runSearch = debounce(async () => {
  const q = query;
  if (!q) return;
  const mine = ++searchSeq;
  const res = await call('search', { query: q });
  if (mine !== searchSeq || q !== query) return;
  results = res.ok
    ? { items: res.results || [], truncated: !!res.truncated }
    : { items: [], error: res.error || 'Search failed' };
  paintResults();
}, SEARCH_MS);

function highlight(name) {
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  const low = name.toLowerCase();
  const hit = tokens.map((t) => [low.indexOf(t), t.length]).find(([i]) => i >= 0);
  if (!hit) return escapeHtml(name);
  const [i, n] = hit;
  return `${escapeHtml(name.slice(0, i))}<mark>${escapeHtml(name.slice(i, i + n))}</mark>${escapeHtml(name.slice(i + n))}`;
}

function paintResults() {
  const el = listEl();
  if (!el || !results) return;
  if (results.error) {
    el.innerHTML = empty('Search failed', escapeHtml(results.error), { ic: 'triangle-alert' });
    return;
  }
  if (!results.items) {
    el.innerHTML = '<div class="gx-loading"><span class="spinner"></span><span>Searching…</span></div>';
    return;
  }
  if (!results.items.length) {
    el.innerHTML = empty('No files match', `Nothing in this project is named like “${escapeHtml(query)}”.`, { ic: 'search' });
    return;
  }
  const rows = results.items.map((r, i) => {
    const on = !!file && file.path === r.path;
    return `<button type="button" class="gx-row fb-hit${on ? ' is-current' : ''}" data-act="hit" data-i="${i}" title="${escapeHtml(r.path)}">
      <span class="fb-ic">${icon(fileIcon(r.name))}</span>
      <span class="gx-path"><span class="gx-name">${highlight(r.name)}</span>${r.dir ? `<span class="gx-dir">${escapeHtml(r.dir)}</span>` : ''}</span>
      ${gitBadge(r.git)}
    </button>`;
  }).join('');
  el.innerHTML = section('Files', { count: results.items.length })
    + `<div class="gx-files">${rows}</div>`
    + (results.truncated ? '<p class="gx-none">Very large project — only the first files are searched.</p>' : '');
}

function setQuery(value) {
  query = value.trim();
  if (!query) {
    searchSeq += 1;
    results = null;
    paintTree();
    return;
  }
  results = { items: results?.items, truncated: false };
  paintResults();
  runSearch();
}

// ─── Search in files (the Explorer's contents mode) ──────────────────────────────────────────────────
// The text inside every file, not just the names. The server (files_api `grep`)
// runs ripgrep when it is installed, else scans from Python, and answers with the
// matches grouped by file and the ranges to highlight already worked out — the
// page only paints them. A click opens the file with the match selected.

function loadGrepOpts() {
  try {
    const o = JSON.parse(storageGet(GREP_OPTS_KEY, '{}'));
    return { cs: !!o.cs, word: !!o.word, re: !!o.re };
  } catch {
    return { cs: false, word: false, re: false };
  }
}

function scheduleGrep(now = false) {
  clearTimeout(grepTimer);
  grepTimer = setTimeout(grepNow, now ? 0 : GREP_MS);
}

async function grepNow() {
  clearTimeout(grepTimer);
  const q = gq;
  if (!q) return;
  const mine = ++grepSeq;
  const key = shownFor;
  grep = { ...(grep?.files ? grep : null), loading: true }; // the old answer stays (dimmed) until the new one lands
  paintGrep();
  const res = await call('grep', { query: q, ...gopts });
  if (mine !== grepSeq || key !== shownFor) return;
  if (res.ok) {
    grep = { query: q, files: res.files || [], matches: num(res.matches), truncated: !!res.truncated };
  } else {
    const old = /unknown files op/.test(res.error || '');
    grep = { query: q, error: old ? RESTART_NOTE : res.error || 'Search failed', bad: res.code === 'bad_pattern' };
  }
  paintGrep();
}

function setGrepQuery(value) {
  gq = value;
  grepSeq += 1; // an answer still on its way is for an older query
  clearTimeout(grepTimer);
  if (!gq) {
    grep = null;
  } else {
    grep = { ...(grep?.files ? grep : null), loading: true };
    scheduleGrep();
  }
  paintGrep();
}

/** `text` with the server's highlight ranges (UTF-16 offsets, like JS strings) wrapped in <mark>. */
function markedText(text, hl) {
  let out = '';
  let at = 0;
  for (const [s, e] of hl || []) {
    if (s < at || e <= s || e > text.length) continue;
    out += `${escapeHtml(text.slice(at, s))}<mark>${escapeHtml(text.slice(s, e))}</mark>`;
    at = e;
  }
  return out + escapeHtml(text.slice(at));
}

function grepMatchHtml(f, m) {
  const cur = grepCur === `${f.path}:${m.line}`;
  return `<button type="button" class="fb-gm${cur ? ' is-current' : ''}" data-path="${escapeHtml(f.path)}" data-line="${num(m.line)}"
    data-col="${num(m.col)}" data-len="${num(m.len)}" title="${escapeHtml(`${f.path}:${m.line}`)}"><span class="fb-gl">${num(m.line)}</span><span class="fb-gt">${markedText(m.text, m.hl)}</span></button>`;
}

function grepFileHtml(f) {
  const open = !grepFolded.has(f.path);
  const n = `${f.matches.length}${f.more ? '+' : ''}`;
  return `<div class="fb-gf">
    <button type="button" class="fb-gh" data-act="g-fold" data-path="${escapeHtml(f.path)}" aria-expanded="${open}" title="${escapeHtml(f.path)}">
      <span class="fb-chev">${icon('chevron-right')}</span><span class="fb-ic">${icon(fileIcon(f.name))}</span>
      <span class="fb-gname">${escapeHtml(f.name)}</span>${f.dir ? `<span class="fb-gdir">${escapeHtml(f.dir)}</span>` : ''}
      <b class="fb-gn" title="${n} match${f.matches.length === 1 && !f.more ? '' : 'es'}">${n}</b>
    </button>${open ? f.matches.map((m) => grepMatchHtml(f, m)).join('') : ''}</div>`;
}

function paintGrep() {
  const el = listEl();
  if (!el || pane !== 'search') return;
  el.classList.toggle('is-loading', !!gq && !!grep?.loading && !!grep.files);
  if (!gq) {
    paintTree();
    return;
  }
  if (grep?.error) {
    el.innerHTML = empty(grep.bad ? 'Can’t search for that' : 'Search failed', escapeHtml(grep.error), { ic: 'triangle-alert' });
    return;
  }
  if (!grep?.files) {
    el.innerHTML = '<div class="gx-loading"><span class="spinner"></span><span>Searching…</span></div>';
    return;
  }
  if (!grep.files.length) {
    el.innerHTML = empty('No results', `No file in this project contains “${escapeHtml(grep.query)}”.`, { ic: 'search' });
    return;
  }
  const plus = grep.truncated ? '+' : '';
  const folded = grep.files.every((f) => grepFolded.has(f.path));
  const nFiles = grep.files.length;
  const active = document.activeElement;
  const refocus = active && el.contains(active) ? active.matches('.fb-gm') ? `.fb-gm[data-path="${CSS.escape(active.dataset.path)}"][data-line="${active.dataset.line}"]` : active.matches('.fb-gh') ? `.fb-gh[data-path="${CSS.escape(active.dataset.path)}"]` : '' : '';
  const scroll = el.scrollTop;
  el.innerHTML = section('Results', {
    count: `${grep.matches}${plus} in ${nFiles}${plus} file${nFiles === 1 && !plus ? '' : 's'}`,
    right: `<button type="button" class="dlg-section-btn" data-act="g-fold-all">${folded ? 'Expand all' : 'Collapse all'}</button>`,
  }) + grep.files.map(grepFileHtml).join('')
    + (grep.truncated ? '<p class="gx-none">Only the first matches are shown — type more, or turn on Match case / Whole word, to narrow it down.</p>' : '');
  el.scrollTop = scroll;
  if (refocus) el.querySelector(refocus)?.focus({ preventScroll: true });
}

/** Switch to the Search tab; `seed`, else the editor's one-line selection, becomes the query. */
function startGrep(seed = '') {
  const ta = findAvailable() ? editorEl() : null;
  const picked = seed || (ta ? ta.value.slice(ta.selectionStart, ta.selectionEnd) : '');
  if (picked.trim() && picked.length <= GREP_SEED_MAX && !picked.includes('\n')) {
    gq = picked;
    grep = null;
  }
  setPane('search');
  const input = $(`${ID}-gq`);
  if (input) {
    input.value = gq;
    input.focus({ preventScroll: true });
    input.select();
  }
  if (gq) scheduleGrep(true);
  paintGrep();
}

/** Names: switch the Explorer to the file-name search (the code search's text comes along) and focus it. */
function startNameSearch() {
  if (pane === 'search') toggleContentSearch();
  else setPane('files');
  const input = $(`${ID}-search`);
  input?.focus({ preventScroll: true });
  input?.select();
}

/** A match was clicked (or Enter on it): open its file, the match selected. */
function openMatch(btn, focus = false) {
  const { path } = btn.dataset;
  const line = num(btn.dataset.line);
  grepCur = `${path}:${line}`;
  scmSel = null;
  listEl()?.querySelector('.fb-gm.is-current')?.classList.remove('is-current');
  btn.classList.add('is-current');
  openPath(path, { focus: false });
  revealLineWhenShown(path, line, { col: num(btn.dataset.col), len: num(btn.dataset.len), focus, pin: true });
}

// ─── Opening a file ───────────────────────────────────────────────────────

function applyRead(doc, res) {
  Object.assign(doc, {
    kind: res.kind, size: num(res.size), etag: res.etag, writable: res.writable !== false, git: res.git || '',
    repo: !!res.repo, prefix: res.prefix || '', ext: res.ext || '', limit: num(res.limit), reason: res.reason || '',
    data_url: res.data_url || '', error: '', code: '', gone: false, diff: null,
  });
  if (res.kind === 'text') {
    Object.assign(doc, {
      text: res.content, saved: res.content, eol: res.eol, bom: !!res.bom, mixed: !!res.mixed_eol,
      indent: detectIndent(res.content, doc.name),
    });
  }
}

/** A tab that isn't read yet (`pending`: restored from the last visit, read when shown). */
const newDoc = (path, extra = {}) => ({ path, name: baseOf(path), view: 'edit', diffSide: '', hunks: [], peek: null, pending: true, ...extra });

/** A new tab goes right after the shown one. */
function addTab(doc) {
  const i = docs.indexOf(file);
  docs.splice(i < 0 ? docs.length : i + 1, 0, doc);
}

/** Show this tab's document (its edits, caret and scroll come back with it). */
function showDoc(doc, { focus = false } = {}) {
  if (file && file !== doc) {
    saveViewState();
    closePeek();
  }
  file = doc;
  banner = null;
  paintBanner();
  paintMain();
  persist();
  paintTree();
  if (!doc.virtual) reveal(doc.path);
  if (focus) focusEditor();
}

/** Switch to a tab: read it if it never was, else catch up with the disk. */
function activate(doc, { focus = false } = {}) {
  if (!doc) return;
  if (doc === file) {
    syncReading();
    if (focus) focusEditor();
    return;
  }
  if (doc.pending) doc.loading = true;
  showDoc(doc, { focus });
  if (doc.pending) loadDoc(doc, { focus });
  else if (!doc.loading && !doc.error) checkDisk();
}

async function loadDoc(mine, { focus = false, restoring = false } = {}) {
  const restored = mine.restored || restoring;
  mine.pending = false;
  mine.loading = true;
  const seq = (mine.seq || 0) + 1;
  mine.seq = seq;
  const res = await call('read', { path: mine.path });
  if (mine.seq !== seq || !docs.includes(mine)) return;
  mine.loading = false;
  mine.restored = false;
  if (!res.ok) {
    if (restored) { // remembered from the last visit, gone since: drop it quietly
      dropTab(mine);
      return;
    }
    mine.error = res.error || 'Could not open this file';
    mine.code = res.code || '';
    if (file === mine) paintMain();
    else paintFileTabs();
    return;
  }
  applyRead(mine, res);
  loadBase(mine);
  if (file !== mine) {
    paintFileTabs();
    return;
  }
  paintMain();
  paintTree();
  if (focus) focusEditor();
}

/** Open a file in its tab (a new one if it has none) and show it.
 * `view: 'diff'` + `diffSide` ('staged' | 'unstaged'): opened from the Source Control tab. */
async function openPath(path, { focus = false, restoring = false, view = 'edit', diffSide = '' } = {}) {
  const have = docFor(path);
  if (have && !have.error && !have.pending) {
    activate(have, { focus });
    return;
  }
  const mine = have || newDoc(path);
  Object.assign(mine, { view, diffSide, loading: true, error: '', code: '' });
  if (!have) addTab(mine);
  showDoc(mine);
  await loadDoc(mine, { focus, restoring });
}

/** Take a tab away (no questions); the neighbour on the right, else the left, is shown. */
function dropTab(doc) {
  const i = docs.indexOf(doc);
  if (i < 0) return;
  docs.splice(i, 1);
  doc.seq = (doc.seq || 0) + 1; // a read still on its way is dropped
  if (mounted === doc) mounted = null;
  if (doc !== file) {
    paintFileTabs();
    paintTree();
    persist();
    return;
  }
  file = null;
  const next = docs[i] || docs[i - 1] || null;
  if (next) {
    activate(next);
    return;
  }
  banner = null;
  paintBanner();
  persist();
  paintMain();
  paintTree();
  if (reading) leaveFile();
}

/** The tab's × (or middle click, ⌥W): unsaved edits ask first. */
function closeTab(doc) {
  if (!doc) return;
  if (docDirty(doc)) {
    activate(doc);
    banner = { kind: 'unsaved', closing: true, then: () => dropTab(doc) };
    paintBanner();
    return;
  }
  dropTab(doc);
}

function focusEditor() {
  const ta = mainEl()?.querySelector('.fb-text');
  if (ta && !ta.closest('[hidden]')) ta.focus({ preventScroll: true });
}

async function reloadFile({ notice = '' } = {}) {
  const mine = file;
  if (!mine) return;
  const res = await call('read', { path: mine.path });
  if (file !== mine) return;
  if (!res.ok) {
    if (res.code === 'not_found') {
      mine.gone = true;
      banner = { kind: 'gone' };
      paintBanner();
      paintHead();
    } else showToast(res.error, true);
    return;
  }
  const kind = mine.kind;
  const ta = mainEl()?.querySelector('.fb-text');
  const keep = ta ? { top: ta.scrollTop, left: ta.scrollLeft, s: ta.selectionStart, e: ta.selectionEnd } : null;
  applyRead(mine, res);
  banner = null;
  paintBanner();
  mine.notice = notice;
  clearTimeout(noticeTimer);
  if (notice) {
    noticeTimer = setTimeout(() => {
      if (file === mine) {
        mine.notice = '';
        paintHead();
      }
    }, 5000);
  }
  if (kind !== mine.kind || !ta || mine.kind !== 'text') {
    paintMain();
  } else {
    ta.value = mine.text;
    ta.scrollTop = keep.top;
    ta.scrollLeft = keep.left;
    ta.setSelectionRange(Math.min(keep.s, ta.value.length), Math.min(keep.e, ta.value.length));
    paintGutter(true);
    paintHighlight();
    recomputeChanges();
    paintHead();
    applyMode();
    paintStatus();
  }
  paintTree();
}

/** Has the open file changed on disk? Clean ones follow along; edited ones ask. */
async function checkDisk() {
  const mine = file;
  if (!isModalOpen(ID) || !mine || mine.virtual || mine.loading || mine.error || saving) return;
  const res = await call('stat', { path: mine.path });
  if (file !== mine || saving || !res.ok) return;
  if (!res.exists) {
    if (!mine.gone) {
      mine.gone = true;
      banner = { kind: 'gone' };
      paintBanner();
      paintHead();
    }
    return;
  }
  if (mine.gone) {
    mine.gone = false;
    if (banner?.kind === 'gone') banner = null;
    paintBanner();
  }
  if (res.etag === mine.etag) return;
  if (!isDirty()) {
    await reloadFile({ notice: store.busy ? 'Updated by Jarvis' : 'Updated on disk' });
  } else if (banner?.kind !== 'changed' || banner.diskEtag !== res.etag) {
    banner = { kind: 'changed', diskEtag: res.etag };
    paintBanner();
  }
}

// ─── Saving ───────────────────────────────────────────────────────────────

async function save({ force = false } = {}) {
  const mine = file;
  if (!mine || mine.kind !== 'text' || saving || mine.writable === false) return false;
  if (!isDirty() && !force) return true;
  saving = true;
  paintHead();
  const sent = mine.text;
  const res = await call('write', { path: mine.path, content: sent, etag: mine.etag, eol: mine.eol, bom: mine.bom, force });
  saving = false;
  if (res.ok) {
    mine.etag = res.etag;
    mine.saved = sent;
    mine.size = num(res.size);
    mine.diff = null;
    mine.gone = false;
    if (banner && banner.kind !== 'unsaved') banner = null;
    paintBanner();
    paintHead();
    paintStatus();
    paintTree();
    if (mine.view === 'diff') applyMode();
    showToast(`Saved ${mine.name}`);
    refreshTree();
    return true;
  }
  paintHead();
  if (res.code === 'conflict') {
    banner = { kind: 'conflict', then: banner?.then };
    paintBanner();
  } else showToast(res.error || 'Could not save', true);
  return false;
}

function discardEdits() {
  if (!file || file.kind !== 'text') return;
  file.text = file.saved;
  const ta = mainEl()?.querySelector('.fb-text');
  if (ta) {
    ta.value = file.saved;
    paintGutter(true);
    paintHighlight();
    closePeek();
    recomputeChanges();
  }
  paintHead();
  paintStatus();
  paintTree();
  if (file.view === 'preview') applyMode();
}

// ─── Painting: main pane ──────────────────────────────────────────────────

function syncReading() {
  if (wide() || !file) return;
  if (!reading) {
    reading = true;
    cardEl()?.classList.add('is-reading');
  }
  const [dir] = splitPath(file.path);
  setView(ID, { title: file.name, sub: isDirty() ? 'unsaved changes' : dir || 'project root', back: leaveFile, backLabel: 'the files' });
  paintFoot();
}

function leaveFile() {
  reading = false;
  cardEl()?.classList.remove('is-reading');
  setView(ID, null);
  paintFoot();
  requestAnimationFrame(() => (listEl()?.querySelector('.fb-row.is-current, .fb-hit.is-current, .fb-scm-row.is-current .fb-scm-main')
    || $(`${ID}-search`) || $(`${ID}-msg`))?.focus({ preventScroll: true }));
}

function paintMain() {
  const el = mainEl();
  if (!el) return;
  saveViewState();
  const strip = `<div class="fb-ftabs" id="${ID}-ftabs" role="tablist" aria-label="Open files"></div>`;
  if (!file) {
    el.innerHTML = `${strip}<div class="gx-pick">${icon('folder-open')}<span>Pick a file to view or edit it</span><small>Each file opens in its own tab · ${MOD} S saves · esc closes</small></div>`;
    paintFileTabs();
    paintFoot();
    return;
  }
  el.innerHTML = `${strip}<header class="fb-head" id="files-head"></header><div class="fb-view" id="files-view"></div><footer class="fb-status" id="files-status"></footer>`;
  paintHead();
  paintView();
  paintStatus();
  syncReading();
  paintFoot();
}

/** The open-file tabs above the header: click to switch, × / middle click / ⌥W to close. */
function paintFileTabs() {
  const el = $(`${ID}-ftabs`);
  if (!el) return;
  const seen = new Map();
  docs.forEach((d) => seen.set(d.name, (seen.get(d.name) || 0) + 1));
  el.innerHTML = docs.map((d, i) => {
    const on = d === file;
    const dirty = docDirty(d);
    const dir = dirOf(d.path);
    // Two tabs with the same name: say which folder each is in.
    const hint = seen.get(d.name) > 1 ? `<span class="fb-ftab-dir">${escapeHtml(dir ? baseOf(dir) : '/')}</span>` : '';
    const cls = ['fb-ftab', on ? 'is-on' : '', dirty ? 'is-dirty' : '', d.error ? 'is-bad' : '', d.virtual ? 'is-virtual' : ''].filter(Boolean).join(' ');
    const tip = `${d.path}${d.virtual ? ' (diff)' : ''}${dirty ? ' — unsaved changes' : ''}`;
    return `<div class="${cls}" role="tab" aria-selected="${on}" tabindex="${on ? 0 : -1}" data-act="tab" data-i="${i}" title="${escapeHtml(tip)}">
      <span class="fb-ftab-ic">${icon(d.virtual ? 'file-diff' : fileIcon(d.name))}</span>
      <span class="fb-ftab-name">${escapeHtml(d.name)}</span>${hint}
      <button type="button" class="fb-ftab-x" data-act="tab-close" data-i="${i}" tabindex="-1" title="${escapeHtml(withHint('Close', 'files.closeTab'))}" aria-label="Close ${escapeHtml(d.name)}">${dirty ? '<i class="fb-ftab-dot" aria-hidden="true"></i>' : ''}${icon('x')}</button>
    </div>`;
  }).join('');
  el.querySelector('.fb-ftab.is-on')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function chipsHtml() {
  const chips = [];
  if (file.gone) chips.push('<span class="fb-chip is-danger">Deleted on disk</span>');
  if (file.notice) chips.push(`<span class="fb-chip is-ok">${escapeHtml(file.notice)}</span>`);
  if (file.kind === 'text' && file.writable === false) chips.push('<span class="fb-chip">Read-only</span>');
  if (file.git && file.git !== 'dirty') chips.push(`<span class="fb-chip is-git is-${escapeHtml(file.git === '!' ? 'conflict' : file.git)}">${escapeHtml(GIT_LABEL[file.git] || '')}</span>`);
  if (file.outside) chips.push('<span class="fb-chip">Outside this folder</span>');
  if (file.diffSide && file.view === 'diff') {
    chips.push(file.diffSide === 'staged' ? '<span class="fb-chip is-ok">Staged</span>' : '<span class="fb-chip">Not staged</span>');
  }
  return chips.join('');
}

function tool(act, ic, label, { pressed = null, disabled = false } = {}) {
  return `<button type="button" class="tool-icon" data-act="${act}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}"${pressed === null ? '' : ` aria-pressed="${pressed}"`}${disabled ? ' disabled' : ''}>${icon(ic)}</button>`;
}

function paintHead() {
  const el = $(`${ID}-head`);
  if (!el || !file) return;
  const [dir, name] = splitPath(file.path);
  const text = file.kind === 'text';
  const dirty = isDirty();
  file.shownDirty = dirty;
  const isMd = text && ['md', 'mdx'].includes(extOf(name));
  const changed = text && ((!!file.git && file.repo) || !!file.diffSide);
  const modes = [['edit', 'pencil', 'Edit']];
  if (changed) modes.push(['diff', 'file-diff', 'Diff']);
  if (isMd) modes.push(['preview', 'eye', 'Preview']);
  const seg = modes.length > 1
    ? `<span class="fb-seg" role="group" aria-label="View">${modes.map(([m, ic, label]) => `<button type="button" class="fb-seg-btn" data-act="mode" data-mode="${m}" aria-pressed="${file.view === m}" title="${label}">${icon(ic)}<span>${label}</span></button>`).join('')}</span>`
    : '';
  const editorName = host.editorLabel();
  const busy = file.loading || !!file.error || !!file.virtual;
  el.innerHTML = `
    <span class="fb-fic">${icon(fileIcon(name))}</span>
    <div class="fb-titles">
      <strong title="${escapeHtml(file.path)}">${escapeHtml(name)}${dirty ? '<i class="fb-dot" title="Unsaved changes"></i>' : ''}</strong>
      <span>${dir ? `<span class="fb-dir">${escapeHtml(dir)}</span>` : '<span class="fb-dir">project root</span>'}${chipsHtml()}</span>
    </div>
    <span class="fb-actions" role="toolbar" aria-label="File actions">
      ${dirty ? `
        <button type="button" class="btn btn-sm btn-quiet" data-act="revert" ${saving ? 'disabled' : ''} title="Throw away your edits">${icon('undo-2')}<span>Revert</span></button>
        <button type="button" class="btn btn-sm btn-primary" data-act="save" ${saving ? 'disabled' : ''} title="Save (${MOD} S)">${saving ? '<span class="spinner"></span>' : ''}<span>${saving ? 'Saving…' : 'Save'}</span></button>` : ''}
      ${scmHeadBtn()}
      <span class="fb-chg" id="${ID}-chg" role="group" aria-label="Changes in this file" hidden></span>
      ${seg}
      ${text && file.view === 'edit' ? tool('find', 'search', `Find in file (${MOD} F)`, { pressed: isFindOpen() }) : ''}
      ${text && file.view === 'edit' ? tool('wrap', 'wrap-text', 'Wrap long lines', { pressed: storageGet(WRAP_KEY, '') === '1' }) : ''}
      ${file.view === 'diff' ? splitToggle() : ''}
      <span class="gx-tsep" aria-hidden="true"></span>
      ${tool('copy', 'copy', 'Copy path', { disabled: file.loading || !!file.error })}
      ${host.openInEditor ? tool('editor', 'external-link', `Open in ${editorName}`, { disabled: busy || file.gone }) : ''}
      ${tool('rename', 'pencil', 'Rename or move', { disabled: busy || file.gone })}
      ${tool('delete', 'trash-2', 'Move to the Trash', { disabled: busy || file.gone })}
    </span>`;
  paintChgChip();
  paintFileTabs();
  syncReading();
}

function nonTextView() {
  const open = host.openInEditor
    ? `<button type="button" class="btn" data-act="editor">${icon('external-link')}<span>Open in ${escapeHtml(host.editorLabel())}</span></button>`
    : '';
  if (file.kind === 'image') {
    return `<div class="fb-image"><img alt="${escapeHtml(file.name)}" src="${escapeHtml(file.data_url)}"><span>${escapeHtml(fmtSize(file.size))}</span></div>`;
  }
  if (file.kind === 'large') {
    return empty('Too large to open here', `${escapeHtml(fmtSize(file.size))} — over the ${escapeHtml(fmtSize(file.limit))} limit for this viewer.`, { ic: 'file', action: open });
  }
  return empty('Not a text file', escapeHtml(file.reason ? `It is ${file.reason}, so it can’t be edited here.` : 'It can’t be shown as text.'), { ic: 'file', action: open });
}

function paintView() {
  const el = $(`${ID}-view`);
  if (!el || !file) return;
  if (file.loading) {
    el.innerHTML = '<div class="dv-skel"><i></i><i></i><i></i><i></i></div>';
    return;
  }
  if (file.error) {
    el.innerHTML = empty(file.code === 'not_found' ? 'This file is gone' : 'Couldn’t open this file', escapeHtml(file.error), {
      ic: 'file-x', action: file.code === 'old_server' ? '' : '<button type="button" class="btn" data-act="retry">Try again</button>',
    });
    return;
  }
  if (file.kind === 'change') { // a deleted file, or one outside this folder: its diff only
    el.innerHTML = '<div class="fb-alt"></div>';
    paintDiff(el.querySelector('.fb-alt'));
    return;
  }
  if (file.kind !== 'text') {
    el.innerHTML = `<div class="fb-alt">${nonTextView()}</div>`;
    return;
  }
  mountEditor(el);
  applyMode();
}

function mountEditor(el) {
  const wrap = storageGet(WRAP_KEY, '') === '1';
  el.innerHTML = `
    <div class="fb-editor${wrap ? ' is-wrap' : ''}">
      <pre class="fb-gutter" aria-hidden="true"></pre>
      <div class="fb-marks" role="group" aria-label="Changes compared with git"><div class="fb-marks-in"></div></div>
      <div class="fb-code">
        <pre class="fb-fm" aria-hidden="true"><code class="fb-fm-in"></code></pre>
        <pre class="fb-hl" aria-hidden="true"><span class="fb-curline" hidden></span><code class="fb-hl-in syn"></code></pre>
        <textarea class="fb-text" spellcheck="false" autocomplete="off" autocapitalize="off" autocorrect="off" wrap="${wrap ? 'soft' : 'off'}"
          aria-label="Editing ${escapeHtml(file.path)}" data-gramm="false" data-1p-ignore data-lpignore="true"></textarea>
      </div>
    </div>
    <div class="fb-alt" hidden></div>
    <div class="fb-peek" role="dialog" aria-label="Change compared with git" hidden></div>`;
  const ta = el.querySelector('.fb-text');
  mounted = file;
  ta.value = file.text;
  ta.readOnly = file.writable === false;
  ta.style.tabSize = file.indent === '\t' ? '4' : String(file.indent.length);
  el.querySelector('.fb-hl-in').style.tabSize = ta.style.tabSize;
  paintGutter(true);
  paintHighlight();
  ta.scrollTop = file.scroll || 0;
  ta.scrollLeft = file.scrollX || 0;
  if (file.sel) ta.setSelectionRange(Math.min(file.sel[0], ta.value.length), Math.min(file.sel[1], ta.value.length));
  syncHighlight();
  paintCurLine();
  hlResize?.disconnect();
  hlResize = typeof ResizeObserver === 'function' ? new ResizeObserver(() => syncHighlight()) : null;
  hlResize?.observe(ta);
  ta.addEventListener('input', onEdit);
  ta.addEventListener('keydown', onEditorKey);
  ta.addEventListener('scroll', () => {
    const g = el.querySelector('.fb-gutter');
    if (g) g.scrollTop = ta.scrollTop;
    syncHighlight();
    syncMarks();
    positionPeek();
  }, { passive: true });
  ta.addEventListener('esc-cancel', () => {
    // Esc steps out one layer: the change peek, then the find bar, then (next press) the drawer.
    if (file?.peek != null) closePeek();
    else if (isFindOpen()) closeFind(viewEl(), { refocus: true });
    else closePeek();
    ta.focus({ preventScroll: true });
  });
  // keydown too: its rAF runs after the caret moved, so held arrow keys move the line band.
  ['keydown', 'keyup', 'click', 'select', 'focus'].forEach((t) => ta.addEventListener(t, scheduleCaret));
  file.peek = null;
  recomputeChanges();
  restoreFind(el);
}

/** Show the editor, the diff or the Markdown preview (the editor stays mounted: undo + caret survive). */
function applyMode() {
  const el = $(`${ID}-view`);
  const editor = el?.querySelector('.fb-editor');
  const alt = el?.querySelector('.fb-alt');
  if (!editor || !alt || !file || file.kind !== 'text') return;
  const mode = file.view;
  if (mode !== 'edit') closePeek();
  editor.hidden = mode !== 'edit';
  alt.hidden = mode === 'edit';
  showFindFor(el, mode);
  if (mode === 'preview') {
    alt.innerHTML = `<div class="fb-preview"><div class="md">${renderMarkdown(file.text) || '<p class="fb-muted">Nothing to preview yet.</p>'}</div></div>`;
  } else if (mode === 'diff') {
    paintDiff(alt);
  }
  paintStatus();
}

function setMode(mode) {
  if (!file || file.view === mode) return;
  saveViewState();
  file.view = mode;
  paintHead();
  applyMode();
  if (mode === 'edit') {
    const ta = mainEl()?.querySelector('.fb-text');
    if (ta) {
      ta.scrollTop = file.scroll || 0;
      ta.focus({ preventScroll: true });
    }
  }
}

const splitOn = () => wide() && storageGet(DIFF_VIEW_KEY, 'unified') === 'split';

/** Redraw the open diff in the current layout (from the cached answer: no request). */
function applyDiffLayout() {
  const alt = $(`${ID}-view`)?.querySelector('.fb-alt');
  if (alt && file?.view === 'diff') paintDiff(alt);
}

/** One icon that flips the Diff view between unified and side by side (side by side needs a wide screen). */
function splitToggle() {
  const on = storageGet(DIFF_VIEW_KEY, 'unified') === 'split';
  const label = on ? 'Unified view' : 'Side by side';
  return `<button type="button" class="tool-icon gx-only-wide" data-act="diff-view" data-view="${on ? 'unified' : 'split'}" aria-pressed="${on}" title="${label}" aria-label="${label}">${icon(on ? 'rows-2' : 'columns-2')}</button>`;
}

async function paintDiff(alt) {
  const d = file.diff;
  if (!d) {
    alt.innerHTML = '<div class="dv-skel"><i></i><i></i><i></i><i></i></div>';
    const mine = file;
    const seq = ++diffSeq;
    let res;
    try {
      res = await api('/api/git', 'POST', { op: 'diff', side: file.diffSide || 'local', path: file.repoPath || `${file.prefix}${file.path}` });
    } catch (err) {
      res = { ok: false, error: err?.message || 'Could not read the diff' };
    }
    if (file !== mine || seq !== diffSeq) return;
    mine.diff = res;
    if (mine.view === 'diff') paintDiff(alt);
    return;
  }
  if (!d.ok) {
    alt.innerHTML = empty(d.code === 'gone' ? 'No changes against git' : 'Couldn’t read the diff', escapeHtml(d.error || ''), { ic: 'file-diff' });
  } else if (d.binary) {
    alt.innerHTML = empty('Too large or binary to compare', '', { ic: 'file' });
  } else if (!d.hunks?.length) {
    alt.innerHTML = empty('No line changes', 'Only the file mode or whitespace at the end changed.', { ic: 'file-diff' });
  } else {
    const view = splitOn() ? 'split' : 'unified';
    const { html } = renderDiff(d.hunks, { view });
    alt.innerHTML = `<div class="gx-diff fb-diff"><div class="dv gx-dv${view === 'split' ? ' is-wrap' : ''}" data-view="${view}" style="--gw:${gutterWidth(d.hunks)}px">${html}</div>`
      + (d.hidden ? `<div class="dv-cut">${d.hidden} more changed lines not shown — very large diffs are capped.</div>` : '')
      + `<p class="fb-diff-note">${DIFF_NOTE[file.diffSide] || DIFF_NOTE.local}</p></div>`;
  }
}

/** Remember where the editor was — for the document it shows (the tab may have just changed). */
function saveViewState() {
  const ta = mainEl()?.querySelector('.fb-text');
  const doc = mounted;
  if (!ta || !doc || doc.kind !== 'text') return;
  doc.scroll = ta.scrollTop;
  doc.scrollX = ta.scrollLeft;
  doc.sel = [ta.selectionStart, ta.selectionEnd];
}

function paintGutter(force = false) {
  const ed = mainEl()?.querySelector('.fb-editor');
  const ta = ed?.querySelector('.fb-text');
  const g = ed?.querySelector('.fb-gutter');
  if (!ta || !g || !file) return;
  let lines = 1;
  for (let i = ta.value.indexOf('\n'); i !== -1; i = ta.value.indexOf('\n', i + 1)) lines += 1;
  if (!force && lines === file.gutterLines) return;
  file.gutterLines = lines;
  ed.style.setProperty('--gd', String(Math.max(3, String(lines).length)));
  g.textContent = Array.from({ length: lines }, (_, i) => i + 1).join('\n');
  g.insertAdjacentHTML('beforeend', '<span class="fb-ln-cur" hidden></span>');
  g.scrollTop = ta.scrollTop;
  paintCurLine();
}

/** Colour the text under the textarea (syntax.js). Huge files stay plain so typing never lags. */
function paintHighlight() {
  const ed = mainEl()?.querySelector('.fb-editor');
  const ta = ed?.querySelector('.fb-text');
  const out = ed?.querySelector('.fb-hl-in');
  if (!ta || !out || !file) return;
  const on = ta.value.length <= HL_MAX_CHARS;
  ed.classList.toggle('has-hl', on);
  out.innerHTML = on ? colorize(ta.value, modeFor(file.name)) : '';
  syncHighlight();
  refreshFind(viewEl());
}

/** Keep the coloured copy (and the caret band) under the textarea's scroll, clear of its scrollbars. */
function syncHighlight() {
  syncFind(viewEl());
  const ed = mainEl()?.querySelector('.fb-editor');
  const ta = ed?.querySelector('.fb-text');
  const code = ed?.querySelector('.fb-code');
  if (!ta || !code || !ed.classList.contains('has-hl')) return;
  code.style.setProperty('--sbw', `${Math.max(0, ta.offsetWidth - ta.clientWidth)}px`);
  code.style.setProperty('--sbh', `${Math.max(0, ta.offsetHeight - ta.clientHeight)}px`);
  const inner = code.querySelector('.fb-hl-in');
  if (inner) inner.style.transform = `translate(${-ta.scrollLeft}px, ${-ta.scrollTop}px)`;
  const band = code.querySelector('.fb-curline');
  if (band) band.style.transform = `translateY(${curLineTop - ta.scrollTop}px)`;
}

/** The caret's line: a band across the text and its number lit in the gutter. */
function paintCurLine() {
  const ed = mainEl()?.querySelector('.fb-editor');
  const ta = ed?.querySelector('.fb-text');
  if (!ta) return;
  const pos = ta.selectionDirection === 'backward' ? ta.selectionStart : ta.selectionEnd;
  let line = 0;
  for (let i = ta.value.indexOf('\n'); i !== -1 && i < pos; i = ta.value.indexOf('\n', i + 1)) line += 1;
  curLineTop = EDITOR_PAD_PX + line * LINE_PX;
  const band = ed.querySelector('.fb-curline');
  if (band) {
    band.hidden = false;
    band.style.transform = `translateY(${curLineTop - ta.scrollTop}px)`;
  }
  const num = ed.querySelector('.fb-ln-cur');
  if (num) {
    num.hidden = false;
    num.style.top = `${curLineTop}px`;
    num.textContent = String(line + 1);
  }
}

function caretText(ta) {
  const before = ta.value.slice(0, ta.selectionStart);
  const ln = before.split('\n').length;
  const col = before.length - before.lastIndexOf('\n');
  const picked = ta.selectionEnd - ta.selectionStart;
  return `Ln ${ln}, Col ${col}${picked ? ` (${picked} selected)` : ''}`;
}

function scheduleCaret() {
  if (caretFrame) return;
  caretFrame = requestAnimationFrame(() => {
    caretFrame = 0;
    const out = $(`${ID}-caret`);
    const ta = mainEl()?.querySelector('.fb-text');
    if (out && ta) out.textContent = caretText(ta);
    paintCurLine();
  });
}

function paintStatus() {
  const el = $(`${ID}-status`);
  if (!el || !file) return;
  if (file.loading || file.error || file.virtual) {
    el.innerHTML = '';
    return;
  }
  const ta = mainEl()?.querySelector('.fb-text');
  const bits = [escapeHtml(languageOf(file.name))];
  if (file.kind === 'text') {
    bits.push(file.eol === 'crlf' ? 'CRLF' : 'LF');
    bits.push(file.bom ? 'UTF-8 BOM' : 'UTF-8');
    bits.push(file.indent === '\t' ? 'Tabs' : `Spaces: ${file.indent.length}`);
    if (file.mixed) bits.push('<span class="fb-warn" title="This file mixes line endings; saving makes them all the same">Mixed line endings</span>');
  }
  bits.push(escapeHtml(fmtSize(file.kind === 'text' ? new Blob([file.text]).size : file.size)));
  el.innerHTML = `<span id="${ID}-caret" class="fb-caret">${file.kind === 'text' && file.view === 'edit' && ta ? escapeHtml(caretText(ta)) : ''}</span><span class="fb-meta">${bits.join('<i aria-hidden="true">·</i>')}</span>`;
}

function paintFoot() {
  const foot = $(`${ID}-foot`);
  if (!foot) return;
  foot.innerHTML = branchChip() + termChip() + footer({
    note: { scm: 'Stage with +, then commit — discarded text goes to the Trash', search: 'Searches the text of every file — ignored and binary files are skipped' }[pane]
      || 'Edits save straight to your project files',
    hints: wide() || reading
      ? [[`${MOD} ⇧ P`, 'file'], [`${MOD} ⇧ F`, 'code'], [`${MOD} S`, 'save'], ['esc', wide() ? 'close' : 'back']]
      : [['↵', 'open'], ['esc', 'close']],
  });
}

/** The current branch (and commits waiting to push), bottom left; opens the branch switcher. */
function branchChip() {
  if (!scm?.ok || !scm.branch) return '';
  const ahead = num(scm.ahead);
  const where = scm.detached ? 'detached HEAD' : scm.upstream ? `tracking ${scm.upstream}` : 'not published yet';
  const tip = `On branch ${scm.branch} · ${where}${ahead ? ` · ${ahead} commit${ahead === 1 ? '' : 's'} to push` : ''} — switch or create a branch`;
  return `<button type="button" class="fb-branch" id="${BR_ID}-btn" data-act="br-menu" aria-haspopup="menu" aria-controls="${BR_ID}"
    aria-expanded="${brOpen}" title="${escapeHtml(tip)}" aria-label="${escapeHtml(tip)}">`
    + `${scmBusy === 'checkout' ? '<span class="spinner"></span>' : icon('git-branch')}<span>${escapeHtml(scm.branch)}</span>`
    + `${ahead ? `<em>↑${ahead}</em>` : ''}${icon('chevron-up', 'fb-branch-chev')}</button>`;
}

/** Bottom left, next to the branch: opens the terminal docked above the footer. */
function termChip() {
  const t = terminalState();
  if (!t.allowed) return '';
  const tip = escapeHtml(termTip(t.open));
  return `<button type="button" class="fb-branch fb-term" id="${ID}-term" data-act="terminal" aria-controls="term-panel"
    aria-pressed="${t.open}" title="${tip}" aria-label="${tip}">${icon('terminal')}<span>Terminal</span></button>`;
}

const termTip = (on) => `${on ? 'Hide' : 'Open'} the terminal (Ctrl + \`) — shells keep running`;

/** The terminal opened, hid, or became (un)available: the chip follows in place. */
function onTerminalState() {
  if (!isModalOpen(ID)) return;
  const t = terminalState();
  const b = $(`${ID}-term`);
  if (!b !== !t.allowed) { paintFoot(); return; }
  if (!b) return;
  b.setAttribute('aria-pressed', String(t.open));
  b.title = termTip(t.open);
  b.setAttribute('aria-label', b.title);
}

// ─── Branch switcher (the footer chip's menu) ──────────────────────────────────
// A filter field, then local branches (newest first, the current one ticked),
// remote ones not checked out yet ("track"), and "Create branch “x”" for a
// new name. Server: git_api branches / checkout {branch, create} via POST
// /api/git — uncommitted changes come along, as with `git switch`.

/** Why switching can't happen now ('' = it can). */
function branchBlock() {
  if (store.busy) return 'Jarvis is working in these files — switch branches when it finishes';
  if (unsavedDoc()) return `Save or revert your edits to ${unsavedDoc().name} first`;
  if (scmBusy) return 'Wait for the current git action to finish';
  return '';
}

function brItem(act, ic, label, { branch = '', hint = '', checked = false, why = '' } = {}) {
  return `<button type="button" class="gc-item${checked ? ' is-current' : ''}" role="menuitem" data-act="${act}" data-branch="${escapeHtml(branch)}"
    data-esc-cancels ${why || checked ? 'disabled' : ''} title="${escapeHtml(why || (checked ? 'The current branch' : label))}">${icon(ic)}<span>${escapeHtml(label)}</span>${hint ? `<em>${escapeHtml(hint)}</em>` : ''}${checked ? icon('check', 'gc-check') : ''}</button>`;
}

function paintBranchList() {
  const box = $(BR_ID)?.querySelector('.gc-branches');
  if (!box) return;
  if (!brList) {
    box.innerHTML = '<div class="gc-loading"><span class="spinner"></span><span>Loading branches…</span></div>';
    return;
  }
  if (!brList.ok) {
    box.innerHTML = `<p class="gc-empty">${escapeHtml(brList.error || 'Could not read the branches')}</p>`;
    return;
  }
  const q = ($(`${BR_ID}-q`)?.value || '').trim();
  const ql = q.toLowerCase();
  const match = (n) => !ql || n.toLowerCase().includes(ql);
  const why = branchBlock();
  const html = [];
  if (why) html.push(`<p class="gc-empty fb-br-why">${icon('triangle-alert')}<span>${escapeHtml(why)}</span></p>`);
  const known = [...brList.local, ...brList.remote];
  if (q && !known.includes(q)) html.push(brItem('br-new', 'plus', `Create branch “${q}”`, { branch: q, why }));
  brList.local.filter(match).slice(0, 80).forEach((n) => html.push(brItem('br-go', 'git-branch', n, {
    branch: n, checked: n === brList.current, why,
  })));
  const remote = brList.remote.filter(match).slice(0, 50);
  if (remote.length) {
    html.push('<div class="gc-sec">Remote</div>');
    remote.forEach((n) => html.push(brItem('br-go', 'cloud-download', n, { branch: n, hint: 'track', why })));
  }
  if (!html.length) html.push(`<p class="gc-empty">${q ? 'No branches match — type a new name to create it' : 'No branches'}</p>`);
  box.innerHTML = html.join('');
}

async function openBranchMenu() {
  const el = $(BR_ID);
  const foot = $(`${ID}-foot`);
  if (!el || !foot || !scm?.ok) return;
  closeScmMenu();
  brOpen = true;
  brList = null;
  el.innerHTML = `
    <label class="gc-search">${icon('search')}<input type="text" id="${BR_ID}-q" placeholder="Switch to or create a branch" autocomplete="off"
      spellcheck="false" aria-label="Branch name" data-esc-cancels></label>
    <div class="gc-branches" role="menu" aria-label="Branches"></div>`;
  el.querySelectorAll('[data-esc-cancels]').forEach((b) => b.addEventListener('esc-cancel', () => closeBranchMenu(true)));
  el.style.bottom = `${foot.offsetHeight + 4}px`; // just above the footer
  el.hidden = false;
  $(`${BR_ID}-btn`)?.setAttribute('aria-expanded', 'true');
  paintBranchList();
  $(`${BR_ID}-q`)?.focus({ preventScroll: true });
  const mine = ++brSeq;
  const res = await gitCall('branches');
  if (mine !== brSeq || !brOpen) return;
  brList = res.ok ? res : { ok: false, error: res.error };
  paintBranchList();
}

function closeBranchMenu(refocus = false) {
  if (!brOpen) return;
  brOpen = false;
  brSeq += 1;
  const el = $(BR_ID);
  if (el) {
    el.hidden = true;
    el.innerHTML = '';
  }
  const btn = $(`${BR_ID}-btn`);
  btn?.setAttribute('aria-expanded', 'false');
  if (refocus) btn?.focus({ preventScroll: true });
}

async function switchBranch(branch, create) {
  const why = branchBlock();
  if (why) {
    showToast(why, true);
    return;
  }
  const remote = !create && !!brList?.remote?.includes(branch);
  const shown = remote ? branch.split('/').slice(1).join('/') : branch; // origin/x → x
  closeBranchMenu(true);
  scmBusy = 'checkout';
  syncCommitBtn();
  paintFoot();
  const res = await gitCall('checkout', { branch, create });
  scmBusy = '';
  if (res.ok) showToast(create ? `Created and switched to ${branch}` : `Switched to ${shown}`);
  else showToast(res.error || 'Could not switch branches', true);
  syncCommitBtn();
  scmSel = null;
  await afterGitChange();
}

/** ↑ ↓ Home End and Enter in the switcher (the field + its rows). */
function branchKey(e) {
  const el = $(BR_ID);
  const items = [...(el?.querySelectorAll('.gc-item:not(:disabled)') || [])];
  if (e.key === 'Tab') {
    closeBranchMenu();
    return;
  }
  if (e.target.id === `${BR_ID}-q` && e.key === 'Enter') {
    e.preventDefault();
    items[0]?.click();
    return;
  }
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
  e.preventDefault();
  const at = items.indexOf(document.activeElement);
  if (e.key === 'ArrowUp' && at <= 0) {
    $(`${BR_ID}-q`)?.focus({ preventScroll: true });
    return;
  }
  if (!items.length) return;
  let next = 0;
  if (e.key === 'End') next = items.length - 1;
  else if (e.key === 'ArrowDown') next = at < 0 ? 0 : Math.min(items.length - 1, at + 1);
  else if (e.key === 'ArrowUp') next = at - 1;
  items[next].focus({ preventScroll: true });
  items[next].scrollIntoView({ block: 'nearest' });
}

// ─── Create pull request (#files-pr, stacked on the drawer) ──────────────
// From → Into, title, description, draft. POST /api/git {op: pr} →
// git_api.create_pr: a From branch that doesn't exist yet is made from your
// current work; uncommitted changes on it are committed with the title as the
// message; it is pushed; then `gh pr create`. ✦ Generate fills it in.

function prState() {
  if (!scm?.ok) return { why: 'Not a git repository' };
  if (scm.gh === false) return { why: 'Needs the GitHub CLI: brew install gh, then gh auth login' };
  if (!scm.remotes) return { why: 'This repository has no remote to open a pull request on' };
  if (scm.detached) return { why: 'You’re not on a branch — switch to one first' };
  return { tip: `Open a pull request on GitHub from ${scm.branch} or a new branch` };
}

const prEl = (part) => $(`${PR_ID}-${part}`);
const PR_HEAD_SUGGESTIONS = 30; // branches listed under From while you type
const PR_BASE_FILTER_AT = 8;    // Into gets a filter box past this many branches

/** The Into branch (a custom dropdown button, not a <select>). */
const prBase = () => prEl('base')?.dataset.value || '';

function setPrBase(value, label = '') {
  const btn = prEl('base');
  if (!btn) return;
  btn.dataset.value = value || '';
  btn.innerHTML = `<span class="gc-select-v">${escapeHtml(label || value || 'Pick a branch')}</span>${icon('chevron-down')}`;
  paintPrPlan();
}

const prBases = () => (prBranches?.bases?.length ? prBranches.bases : prBranches?.local || []);

function showPrHeads() {
  const head = prEl('head');
  const pop = prEl('pop-head');
  if (!prBranches || !head || !pop) return;
  const q = head.value.trim().toLowerCase();
  const items = (prBranches.local || [])
    .filter((b) => b !== prBase() && (!q || b.toLowerCase().includes(q)))
    .slice(0, PR_HEAD_SUGGESTIONS);
  if (!items.length) {
    closePop(pop); // a new name: nothing to suggest, the plan line says what happens
    return;
  }
  showPop(pop, items, {
    current: head.value.trim(),
    preselect: false,
    onPick: (v) => {
      head.value = v;
      closePop(pop);
      paintPrPlan();
    },
  });
}

function showPrBases(q = '') {
  const pop = prEl('pop-base');
  if (!pop) return;
  const all = prBases();
  showPop(pop, all.filter((b) => !q || b.toLowerCase().includes(q.toLowerCase())), {
    current: prBase(),
    filter: all.length > PR_BASE_FILTER_AT,
    onPick: (v) => {
      setPrBase(v);
      closePop(pop);
      prEl('base').focus({ preventScroll: true });
    },
  });
  escClosesPop(pop.querySelector('.gc-pop-search input'), pop, prEl('base'));
}

/**
 * The modal stack handles Esc first (capture phase) and would close the whole
 * dialog; fields marked data-esc-cancels get an 'esc-cancel' event instead, so
 * Esc closes an open list and only closes the dialog when none is open.
 */
function escClosesPop(field, pop, refocus = null) {
  if (!field || field.hasAttribute('data-esc-cancels')) return;
  field.setAttribute('data-esc-cancels', '');
  field.addEventListener('esc-cancel', () => {
    if (!popOpen(pop)) {
      closeModal(PR_ID, 'esc');
      return;
    }
    closePop(pop);
    refocus?.focus({ preventScroll: true });
  });
}

function closePrPops() {
  closePop(prEl('pop-head'));
  closePop(prEl('pop-base'));
}

function prError(msg = '') {
  const el = prEl('err');
  if (!el) return;
  el.textContent = msg;
  el.hidden = !msg;
}

function syncPrButtons() {
  const gen = prEl('gen');
  const ok = prEl('ok');
  if (gen) {
    gen.disabled = !!prBusy;
    gen.innerHTML = prBusy === 'generate' ? '<span class="spinner"></span><span>Writing…</span>' : `${icon('wand-sparkles')}<span>Generate</span>`;
  }
  if (ok) {
    ok.disabled = !!prBusy;
    ok.innerHTML = prBusy === 'create' ? '<span class="spinner"></span><span>Creating…</span>' : `${icon('brand:github')}<span>Create PR</span>`;
  }
}

/** One line saying what Create PR will do with these two branches. */
function paintPrPlan() {
  const el = prEl('plan');
  if (!el) return;
  const head = prEl('head')?.value.trim() || '';
  const base = prBase();
  const n = num(scm?.total);
  const files = n ? `commits your ${n} changed file${n === 1 ? '' : 's'} (the title is the commit message), ` : '';
  const b = (s) => `<strong>${escapeHtml(s)}</strong>`;
  let text;
  if (!prBranches) text = 'Loading branches…';
  else if (!head) text = 'Name a new branch for this work — it is made from what you have now.';
  else if (head === base) text = `Pick a branch other than ${b(base)} to open the pull request from.`;
  else if (!(prBranches.local || []).includes(head)) text = `Creates ${b(head)} from your current work, ${files}pushes it and opens a pull request into ${b(base)}.`;
  else if (head === scm?.branch) text = `${files ? files[0].toUpperCase() + files.slice(1) : ''}${files ? 'pushes' : 'Pushes'} ${b(head)} and opens a pull request into ${b(base)}.`;
  else text = `Pushes ${b(head)} as it is (your uncommitted changes stay here) and opens a pull request into ${b(base)}.`;
  el.innerHTML = text;
}

async function openPr() {
  const s = prState();
  if (s.why) {
    showToast(s.why, true);
    return;
  }
  if (unsavedDoc()) {
    showToast(`Save or revert your edits to ${unsavedDoc().name} first — a pull request only takes what is on disk`, true);
    return;
  }
  if (!$(PR_ID)) return;
  prBranches = null;
  prBusy = '';
  prError('');
  syncPrButtons();
  prEl('head').value = scm.branch || '';
  prEl('name').value = scmMsg.split('\n')[0].trim();
  prEl('body').value = '';
  prEl('draft').checked = false;
  closePrPops();
  const base = prEl('base');
  setPrBase('', 'Loading…');
  base.disabled = true;
  prEl('sub').textContent = `${scm.branch} · GitHub`;
  paintPrPlan();
  openModal(PR_ID, { focus: `${PR_ID}-name` });
  const res = await gitCall('branches');
  if (!isModalOpen(PR_ID)) return;
  if (!res.ok) {
    prError(res.error || 'Could not read the branches');
    return;
  }
  prBranches = res;
  const bases = prBases();
  base.disabled = false;
  setPrBase(bases.includes(res.base) ? res.base : bases[0] || '');
  const head = prEl('head');
  if (head.value.trim() === prBase()) { // on the base branch: the work needs a new branch
    head.value = '';
    head.focus();
  }
  paintPrPlan();
}

async function generatePr() {
  if (prBusy) return;
  prError('');
  prBusy = 'generate';
  syncPrButtons();
  const res = await gitCall('generate', { kind: 'pr' });
  prBusy = '';
  syncPrButtons();
  if (!isModalOpen(PR_ID)) return;
  if (!res.ok) {
    prError(res.error || 'Could not write a description');
    return;
  }
  if (res.title) prEl('name').value = res.title;
  if (res.body) prEl('body').value = res.body;
  const head = prEl('head');
  if (res.branch && (!head.value.trim() || head.value.trim() === prBase())) head.value = res.branch;
  paintPrPlan();
}

async function createPr() {
  if (prBusy || !prBranches) return;
  const head = prEl('head').value.trim();
  const base = prBase();
  const title = prEl('name').value.trim();
  const local = prBranches.local || [];
  const commits = num(scm?.total) && (head === scm?.branch || !local.includes(head));
  let why = '';
  if (!head) why = 'Name the branch this pull request comes from';
  else if (head === base) why = `Pick a branch other than ${base} to open it from`;
  else if (commits && !title) why = 'Add a title — it is also the commit message for your changes';
  if (why) {
    prError(why);
    (head && head !== base ? prEl('name') : prEl('head')).focus();
    return;
  }
  prError('');
  prBusy = 'create';
  syncPrButtons();
  const res = await gitCall('pr', { head, base, title, body: prEl('body').value, draft: prEl('draft').checked });
  prBusy = '';
  syncPrButtons();
  if (!res.ok) {
    if (isModalOpen(PR_ID)) prError(res.error || 'Could not create the pull request');
    else showToast(res.error || 'Could not create the pull request', true);
    return;
  }
  closeModal(PR_ID);
  prUrl = res.url || '';
  paintPrDone();
  showToast(res.existing ? 'That branch already has a pull request'
    : res.created_branch ? `Created ${res.head}, pushed it and opened the pull request` : 'Pull request created');
  await afterGitChange();
}

/** The link to the PR just opened, under the commit box. */
function paintPrDone() {
  const bar = $(`${ID}-bar`);
  let el = $(`${ID}-prdone`);
  if (!prUrl || pane !== 'scm' || !bar) {
    el?.remove();
    return;
  }
  if (!el) {
    el = document.createElement('div');
    el.id = `${ID}-prdone`;
    el.className = 'fb-prdone';
    bar.appendChild(el);
  }
  el.innerHTML = `${icon('brand:github')}<a href="${escapeHtml(prUrl)}" target="_blank" rel="noopener" title="Open the pull request">${escapeHtml(prUrl.replace(/^https?:\/\//, ''))}</a>`
    + `<button type="button" class="tool-icon" data-act="pr-dismiss" title="Dismiss" aria-label="Dismiss">${icon('x')}</button>`;
}

/** From (typed, with branch suggestions) → Into (custom dropdown). */
function initPrRoute(el) {
  const head = prEl('head');
  const base = prEl('base');
  const headPop = prEl('pop-head');
  const basePop = prEl('pop-base');
  wirePop(headPop);
  wirePop(basePop);
  escClosesPop(head, headPop);
  escClosesPop(base, basePop);

  head.addEventListener('focus', showPrHeads);
  head.addEventListener('input', showPrHeads);
  head.addEventListener('keydown', (e) => {
    if (popKey(headPop, e)) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      showPrHeads();
    }
  });
  head.addEventListener('blur', () => setTimeout(() => closePop(headPop), 120));

  base.addEventListener('click', () => {
    if (popOpen(basePop)) {
      closePop(basePop);
      return;
    }
    closePop(headPop);
    showPrBases();
    basePop.querySelector('.gc-pop-search input')?.focus({ preventScroll: true });
  });
  base.addEventListener('keydown', (e) => {
    if (popKey(basePop, e)) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      showPrBases();
    }
  });
  basePop.addEventListener('input', (e) => {
    if (e.target.matches('.gc-pop-search input')) showPrBases(e.target.value);
  });
  basePop.addEventListener('keydown', (e) => {
    if (e.target.matches('.gc-pop-search input')) popKey(basePop, e);
  });
  // Clicks elsewhere in the dialog close both lists.
  el.addEventListener('pointerdown', (e) => {
    if (!e.target.closest(`#${PR_ID}-pop-head, #${PR_ID}-head`)) closePop(headPop);
    if (!e.target.closest(`#${PR_ID}-pop-base, #${PR_ID}-base`)) closePop(basePop);
  });
}

function initPr() {
  const el = $(PR_ID);
  if (!el) return;
  prEl('ic').innerHTML = icon('brand:github');
  syncPrButtons();
  prEl('ok').addEventListener('click', createPr);
  prEl('gen').addEventListener('click', generatePr);
  initPrRoute(el);
  el.addEventListener('input', (e) => {
    if (e.target.id === `${PR_ID}-head`) paintPrPlan();
    prError('');
  });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !e.isComposing) {
      e.preventDefault();
      createPr();
    }
  });
}

// ─── Painting: banner + prompt ────────────────────────────────────────────

function bannerBtn(act, label, cls = 'btn-quiet') {
  return `<button type="button" class="btn btn-sm ${cls}" data-act="${act}">${escapeHtml(label)}</button>`;
}

function paintBanner() {
  const el = $(`${ID}-banner`);
  if (!el) return;
  if (!banner || !file) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  const name = `<strong>${escapeHtml(file.name)}</strong>`;
  let text = '';
  let actions = '';
  switch (banner.kind) {
    case 'unsaved':
      text = banner.closing ? `Close ${name}? It has unsaved changes.` : `You have unsaved changes in ${name}.`;
      actions = bannerBtn('b-save', banner.closing ? 'Save and close' : 'Save and continue', 'btn-primary')
        + bannerBtn('b-discard', banner.closing ? 'Close without saving' : 'Discard', 'btn-danger') + bannerBtn('b-keep', 'Keep editing');
      break;
    case 'revert':
      text = `Throw away your edits to ${name}?`;
      actions = bannerBtn('b-discard-here', 'Discard edits', 'btn-danger') + bannerBtn('b-keep', 'Keep editing');
      break;
    case 'conflict':
      text = `${name} changed on disk since you opened it — saving now would overwrite that.`;
      actions = bannerBtn('b-overwrite', 'Overwrite', 'btn-danger') + bannerBtn('b-reload', 'Load the disk version') + bannerBtn('b-keep', 'Keep editing');
      break;
    case 'changed':
      text = `${name} changed on disk while you were editing it.`;
      actions = bannerBtn('b-acknowledge', 'Keep mine') + bannerBtn('b-reload', 'Load the disk version', 'btn-danger');
      break;
    default:
      text = `${name} was deleted or moved on disk.`;
      actions = (isDirty() ? bannerBtn('b-recreate', 'Recreate it', 'btn-primary') : '') + bannerBtn('b-close', 'Close the file');
  }
  el.hidden = false;
  el.innerHTML = `<span class="fb-banner-ic">${icon('triangle-alert')}</span><p>${text}</p><span class="fb-banner-act">${actions}</span>`;
}

function startPrompt(kind, extra = {}) {
  prompt = { kind, error: '', busy: false, ...extra };
  paintPrompt();
  const input = $(`${ID}-prompt-input`);
  if (input) {
    input.focus({ preventScroll: true });
    if (kind === 'rename') {
      const dot = input.value.lastIndexOf('.');
      input.setSelectionRange(0, dot > 0 ? dot : input.value.length);
    }
  } else $(`${ID}-prompt`)?.querySelector('[data-act="p-confirm"]')?.focus({ preventScroll: true });
}

function cancelPrompt() {
  prompt = null;
  paintPrompt();
  (listEl()?.querySelector('.fb-row.is-cursor, .fb-row.is-current') || $(`${ID}-search`))?.focus({ preventScroll: true });
}

function paintPrompt() {
  const el = $(`${ID}-prompt`);
  if (!el) return;
  if (!prompt) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  const p = prompt;
  el.hidden = false;
  const err = p.error ? `<p class="fb-prompt-err" role="alert">${escapeHtml(p.error)}</p>` : '';
  if (p.kind === 'discard') {
    const fresh = p.rows.filter((r) => r.status === 'untracked').length;
    const one = p.rows.length === 1 ? p.rows[0] : null;
    const text = one
      ? (one.status === 'untracked'
        ? `Move <strong>${escapeHtml(baseOf(one.path))}</strong>, a new file, to the Trash?`
        : `Discard your changes to <strong>${escapeHtml(baseOf(one.path))}</strong>? It goes back to its ${scmRow('staged', one.path) ? 'staged' : 'last committed'} version — a copy of the current one is put in the Trash.`)
      : `Discard all ${p.rows.length} changes? Changed files go back to their staged or committed version (a copy of each goes to the Trash)${fresh ? `; ${fresh} new file${fresh === 1 ? '' : 's'} move to the Trash` : ''}.`;
    el.innerHTML = `<p class="fb-prompt-text">${text}</p>${err}
      <div class="fb-prompt-actions">
        <button type="button" class="btn btn-sm btn-danger" data-act="p-confirm" data-esc-cancels ${p.busy ? 'disabled' : ''}>${p.busy ? '<span class="spinner"></span>' : icon('undo-2')}<span>${one?.status === 'untracked' ? 'Move to Trash' : 'Discard'}</span></button>
        <button type="button" class="btn btn-sm btn-quiet" data-act="p-cancel" data-esc-cancels>Cancel</button>
      </div>`;
    el.querySelectorAll('[data-esc-cancels]').forEach((b) => b.addEventListener('esc-cancel', cancelPrompt));
    return;
  }
  if (p.kind === 'delete') {
    const lose = docs.some((d) => !d.virtual && under(d.path, p.path) && docDirty(d)) ? ' Your unsaved edits will be lost.' : '';
    el.innerHTML = `<p class="fb-prompt-text">Move <strong>${escapeHtml(p.path)}</strong> to the Trash${p.isDir ? ' with everything inside it' : ''}?${lose}</p>${err}
      <div class="fb-prompt-actions">
        <button type="button" class="btn btn-sm btn-danger" data-act="p-confirm" data-esc-cancels ${p.busy ? 'disabled' : ''}>${icon('trash-2')}<span>Move to Trash</span></button>
        <button type="button" class="btn btn-sm btn-quiet" data-act="p-cancel" data-esc-cancels>Cancel</button>
      </div>`;
    el.querySelectorAll('[data-esc-cancels]').forEach((b) => b.addEventListener('esc-cancel', cancelPrompt));
    return;
  }
  const label = p.kind === 'rename'
    ? `Rename or move <strong>${escapeHtml(baseOf(p.path))}</strong>`
    : `New ${p.kind === 'new-folder' ? 'folder' : 'file'} in <strong>${escapeHtml(p.dir || 'the project folder')}</strong>`;
  const verb = p.kind === 'rename' ? 'Rename' : 'Create';
  el.innerHTML = `<label class="fb-prompt-text" for="${ID}-prompt-input">${label}</label>
    <input id="${ID}-prompt-input" class="fb-input" type="text" value="${escapeHtml(p.value || '')}" placeholder="${p.kind === 'rename' ? 'New name' : 'name  or  folder/name'}"
      autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" data-esc-cancels data-1p-ignore data-lpignore="true">
    ${err}
    <div class="fb-prompt-actions">
      <button type="button" class="btn btn-sm btn-primary" data-act="p-confirm" ${p.busy ? 'disabled' : ''}>${p.busy ? '<span class="spinner"></span>' : ''}<span>${verb}</span></button>
      <button type="button" class="btn btn-sm btn-quiet" data-act="p-cancel">Cancel</button>
    </div>`;
  const input = $(`${ID}-prompt-input`);
  input.addEventListener('esc-cancel', cancelPrompt);
  input.addEventListener('input', () => { p.value = input.value; });
}

async function afterMutation() {
  await refreshTree();
  if (query) runSearch();
}

async function submitDiscard(p) {
  p.busy = true;
  p.error = '';
  paintPrompt();
  const res = await gitCall('discard', p.all ? { all: true } : { paths: p.paths });
  if (prompt !== p) return;
  p.busy = false;
  if (!res.ok) {
    p.error = res.error || 'That did not work';
    paintPrompt();
    return;
  }
  prompt = null;
  paintPrompt();
  const n = num(res.discarded);
  showToast(n === 1 ? 'Discarded — the old text is in the Trash' : `Discarded ${n} changes — copies are in the Trash`);
  await afterGitChange();
  listEl()?.querySelector('.fb-scm-main')?.focus({ preventScroll: true });
}

async function submitPrompt() {
  const p = prompt;
  if (!p || p.busy) return;
  if (p.kind === 'discard') {
    submitDiscard(p);
    return;
  }
  const raw = p.kind === 'delete' ? '' : ($(`${ID}-prompt-input`)?.value || '').trim().replace(/^\/+|\/+$/g, '');
  if (p.kind !== 'delete' && !raw) {
    p.error = 'Give it a name.';
    paintPrompt();
    $(`${ID}-prompt-input`)?.focus();
    return;
  }
  p.busy = true;
  p.value = raw;
  p.error = '';
  paintPrompt();
  let res;
  if (p.kind === 'delete') res = await call('delete', { path: p.path });
  else if (p.kind === 'rename') res = await call('rename', { path: p.path, to: join(dirOf(p.path), raw) });
  else res = await call('create', { path: join(p.dir, raw), kind: p.kind === 'new-folder' ? 'dir' : 'file' });
  if (prompt !== p) return;
  p.busy = false;
  if (!res.ok) {
    p.error = res.error || 'That did not work';
    paintPrompt();
    $(`${ID}-prompt-input`)?.focus();
    return;
  }
  prompt = null;
  paintPrompt();
  await finishPrompt(p, res);
}

async function finishPrompt(p, res) {
  if (p.kind === 'delete') {
    // Its tabs close (the shown one last, so the next tab shown is one that stays).
    const gone = docs.filter((d) => !d.virtual && under(d.path, p.path));
    gone.filter((d) => d !== file).forEach(dropTab);
    if (gone.includes(file)) dropTab(file);
    expanded.delete(p.path);
    showToast(`Moved ${baseOf(p.path)} to the Trash`);
  } else if (p.kind === 'rename') {
    const moved = docs.filter((d) => !d.virtual && under(d.path, p.path));
    moved.forEach((d) => {
      d.path = res.path + d.path.slice(p.path.length);
      d.name = baseOf(d.path);
      d.diff = null;
    });
    if (moved.length) {
      persist();
      if (file) paintHead();
      else paintFileTabs();
    }
    for (const d of [...expanded]) {
      if (d === p.path || d.startsWith(`${p.path}/`)) {
        expanded.delete(d);
        expanded.add(res.path + d.slice(p.path.length));
      }
    }
    showToast(`Renamed to ${res.path}`);
  } else {
    ancestorsOf(res.path).forEach((dir) => expanded.add(dir));
    if (res.dir) expanded.add(res.path);
    showToast(`Created ${res.path}`);
  }
  cursorPath = p.kind === 'delete' ? '' : res.path;
  await afterMutation();
  if (p.kind !== 'delete' && !res.dir && p.kind !== 'rename') {
    await openPath(res.path, { focus: true });
  } else if (p.kind === 'rename' && file && file.path === res.path) {
    reveal(res.path);
  }
}

// ─── Changes against git (gutter bars, peek, next / previous) ─────────────
// Like VS Code: the text you are editing is compared with what git has (HEAD),
// live, unsaved edits included. Green = added lines, amber = changed lines,
// red triangle = lines removed here. Click a bar to see what was there and
// revert just that change; Alt+F3 / Shift+Alt+F3 step through the changes.
// The bars are placed by line number (LINE_PX), so they are hidden while long
// lines wrap (a wrapped line is taller than one row).

const KIND_LABEL = { added: 'Added', modified: 'Modified', deleted: 'Removed' };

const wrapOn = () => storageGet(WRAP_KEY, '') === '1';
const editorEl = () => mainEl()?.querySelector('.fb-text') || null;
const viewEl = () => $(`${ID}-view`);

/** "line 3" / "lines 3–5" (1-based) for a hunk of the current text. */
function rangeLabel(h) {
  if (h.kind === 'deleted') return `before line ${h.bStart + 1}`;
  return h.bLen === 1 ? `line ${h.bStart + 1}` : `lines ${h.bStart + 1}–${h.bStart + h.bLen}`;
}

/** What git has for the open file (once per open, again after a commit). */
async function loadBase(doc) {
  if (!doc || doc.kind !== 'text') return;
  if (!doc.repo) {
    doc.baseLines = null;
    if (file === doc) recomputeChanges();
    return;
  }
  const res = await call('base', { path: doc.path });
  if (!docs.includes(doc)) return;
  doc.baseLines = res.ok && typeof res.base === 'string' ? res.base.split('\n') : null;
  if (file === doc) recomputeChanges(); // a background tab compares when it is shown (mountEditor)
}

/** Git moved on (a commit, a pull, a turn ended): compare with the new HEAD. */
function refreshBase() {
  if (isModalOpen(ID) && file && !file.loading && file.kind === 'text') loadBase(file);
}

function recomputeChanges() {
  if (!file || file.kind !== 'text') return;
  if (file.baseLines) {
    file.curLines = file.text.split('\n');
    file.hunks = diffLines(file.baseLines, file.curLines);
  } else {
    file.curLines = null;
    file.hunks = [];
  }
  if (file.peek != null && !file.hunks[file.peek]) closePeek();
  paintMarks();
  paintChgChip();
  if (file.peek != null) paintPeek();
}

const scheduleChanges = debounce(recomputeChanges, CHANGES_MS);

function syncMarks() {
  const ta = editorEl();
  const inner = mainEl()?.querySelector('.fb-marks-in');
  if (ta && inner) inner.style.transform = `translateY(${-ta.scrollTop}px)`;
}

function paintMarks() {
  const inner = mainEl()?.querySelector('.fb-marks-in');
  if (!inner || !file) return;
  inner.innerHTML = (file.hunks || []).map((h, i) => {
    const top = EDITOR_PAD_PX + h.bStart * LINE_PX;
    const n = h.kind === 'deleted' ? h.aLen : h.bLen;
    const title = `${KIND_LABEL[h.kind]} ${rangeLabel(h)} — click to see the change`;
    const size = h.kind === 'deleted' ? '' : `height:${h.bLen * LINE_PX}px;`;
    return `<button type="button" class="fb-mark is-${h.kind}" data-hunk="${i}" tabindex="-1" style="top:${top}px;${size}" title="${escapeHtml(title)}" aria-label="${escapeHtml(`${KIND_LABEL[h.kind]}, ${n} line${n === 1 ? '' : 's'}, ${rangeLabel(h)}`)}"></button>`;
  }).join('');
  syncMarks();
}

/** "3 changes ⌃ ⌄" in the file header. */
function paintChgChip() {
  const el = $(`${ID}-chg`);
  if (!el || !file) return;
  const n = file.hunks?.length || 0;
  const show = n > 0 && file.kind === 'text' && file.view === 'edit' && !wrapOn();
  el.hidden = !show;
  if (!show) {
    el.innerHTML = '';
    return;
  }
  const btn = (act, ic, label) => `<button type="button" class="tool-icon" data-act="${act}" title="${label}" aria-label="${label}">${icon(ic)}</button>`;
  el.innerHTML = `${btn('chg-prev', 'chevron-up', 'Previous change (Shift+Alt+F3)')}<span class="fb-chg-n" title="Compared with what git has — unsaved edits included">${n} change${n === 1 ? '' : 's'}</span>${btn('chg-next', 'chevron-down', 'Next change (Alt+F3)')}`;
}

function peekRows(lines, cls, sign) {
  const rows = lines.slice(0, PEEK_MAX_LINES).map((l) => `<div class="fb-pl ${cls}"><i>${sign}</i><span>${escapeHtml(l) || '&nbsp;'}</span></div>`);
  if (lines.length > PEEK_MAX_LINES) rows.push(`<div class="fb-pl is-more"><i></i><span>… ${lines.length - PEEK_MAX_LINES} more lines</span></div>`);
  return rows.join('');
}

function paintPeek() {
  const peek = mainEl()?.querySelector('.fb-peek');
  const ta = editorEl();
  const h = file?.hunks?.[file.peek];
  if (!peek || !ta || !h || !file.baseLines || !file.curLines) {
    closePeek();
    return;
  }
  const was = file.baseLines.slice(h.aStart, h.aStart + h.aLen);
  const now = file.curLines.slice(h.bStart, h.bStart + h.bLen);
  const total = file.hunks.length;
  const btn = (act, ic, label, extra = '') => `<button type="button" class="tool-icon${extra}" data-act="${act}" title="${label}" aria-label="${label}">${icon(ic)}</button>`;
  peek.innerHTML = `
    <div class="fb-peek-head">
      <strong>${KIND_LABEL[h.kind]} <span>${escapeHtml(rangeLabel(h))}</span></strong>
      <span class="fb-peek-pos">${file.peek + 1} of ${total}</span>
      <span class="fb-peek-tools">
        ${btn('peek-prev', 'chevron-up', 'Previous change')}${btn('peek-next', 'chevron-down', 'Next change')}
        ${ta.readOnly ? '' : `<button type="button" class="btn btn-sm btn-quiet" data-act="peek-revert" title="Put back what git has for these lines">${icon('undo-2')}<span>Revert</span></button>`}
        ${btn('peek-close', 'x', 'Close (Esc)')}
      </span>
    </div>
    <div class="fb-peek-body">${peekRows(was, 'is-del', '−')}${peekRows(now, 'is-add', '+')}</div>`;
  peek.hidden = false;
  ta.setAttribute('data-esc-cancels', '');
  positionPeek();
}

/** Under the change (or above it when there isn't room), clear of the gutter. */
function positionPeek() {
  const peek = mainEl()?.querySelector('.fb-peek');
  const ta = editorEl();
  const marks = mainEl()?.querySelector('.fb-marks');
  const h = file?.hunks?.[file.peek];
  if (!peek || peek.hidden || !ta || !marks || !h) return;
  const view = peek.parentElement;
  peek.style.left = `${marks.offsetLeft + marks.offsetWidth + 8}px`;
  const top = EDITOR_PAD_PX + h.bStart * LINE_PX - ta.scrollTop;
  const bottom = EDITOR_PAD_PX + (h.bStart + Math.max(h.bLen, 1)) * LINE_PX - ta.scrollTop;
  const height = peek.offsetHeight;
  let y = bottom + 4;
  if (y + height > view.clientHeight - 6) y = top - height - 4;
  peek.style.top = `${Math.max(6, Math.min(y, view.clientHeight - height - 6))}px`;
}

function openPeek(i) {
  if (!file?.hunks?.[i]) return;
  file.peek = i;
  paintPeek();
}

function closePeek() {
  if (file) file.peek = null;
  const peek = mainEl()?.querySelector('.fb-peek');
  if (peek) {
    peek.hidden = true;
    peek.innerHTML = '';
  }
  if (!isFindOpen()) editorEl()?.removeAttribute('data-esc-cancels'); // the find bar still needs the editor's Esc
}

/** Character position of the start of line `n` (0-based) in `text`. */
function lineStart(text, n) {
  let pos = 0;
  for (let k = 0; k < n; k += 1) {
    const nl = text.indexOf('\n', pos);
    if (nl < 0) return text.length;
    pos = nl + 1;
  }
  return pos;
}

/** Put the caret on a change, bring it into view and show it. */
function showChange(i) {
  const h = file?.hunks?.[i];
  const ta = editorEl();
  if (!h || !ta) return;
  const pos = lineStart(ta.value, h.bStart);
  ta.setSelectionRange(pos, pos);
  const y = EDITOR_PAD_PX + h.bStart * LINE_PX;
  if (y < ta.scrollTop + LINE_PX * 2 || y > ta.scrollTop + ta.clientHeight - LINE_PX * 8) {
    ta.scrollTop = Math.max(0, y - ta.clientHeight / 3);
  }
  ta.focus({ preventScroll: true });
  openPeek(i);
  scheduleCaret();
}

/** Next / previous change from the caret, wrapping around at the ends. */
function gotoChange(dir) {
  const hs = file?.hunks;
  const ta = editorEl();
  if (!hs?.length || !ta) return;
  const line = ta.value.slice(0, ta.selectionStart).split('\n').length - 1;
  let i;
  if (dir > 0) {
    i = hs.findIndex((h) => h.bStart > line);
    if (i < 0) i = 0;
  } else {
    i = -1;
    hs.forEach((h, k) => { if (h.bStart < line) i = k; });
    if (i < 0) i = hs.length - 1;
  }
  showChange(i);
}

/** Step from the open peek (its own change, not the caret). */
function stepPeek(dir) {
  const n = file?.hunks?.length || 0;
  if (n && file.peek != null) showChange((file.peek + dir + n) % n);
}

/** Put one change back to what git has — as an edit, so undo brings it back. */
function revertChange(i) {
  const h = file?.hunks?.[i];
  const ta = editorEl();
  if (!h || !ta || ta.readOnly || !file.baseLines) return;
  const was = file.baseLines.slice(h.aStart, h.aStart + h.aLen);
  const lines = ta.value.split('\n');
  const start = lineStart(ta.value, h.bStart);
  const end = lineStart(ta.value, h.bStart + h.bLen); // start of the line after the change
  const last = h.bStart + h.bLen >= lines.length;     // the change runs to the end of the file
  let from = start;
  let to = start;
  let text = '';
  if (!h.bLen) { // removed lines: put them back
    text = h.bStart < lines.length ? `${was.join('\n')}\n` : `\n${was.join('\n')}`;
    if (h.bStart >= lines.length) from = to = ta.value.length;
  } else if (!h.aLen) { // added lines: take them out, with one line break
    if (!last) to = end;
    else {
      from = Math.max(0, start - 1);
      to = ta.value.length;
    }
  } else { // changed lines: swap in the old ones
    to = last ? ta.value.length : end - 1;
    text = was.join('\n');
  }
  closePeek();
  ta.focus({ preventScroll: true });
  ta.setSelectionRange(from, to);
  insertText(ta, text);
  const caret = Math.min(start, ta.value.length);
  ta.setSelectionRange(caret, caret);
}

// ─── Source Control tab (VS Code style) ───────────────────────────────────
// The left pane's second tab: the commit box, then Merge Changes / Staged
// Changes / Changes. A row opens its diff on the right (staged = HEAD → index,
// changes = index → disk); its hover buttons open the file, discard, stage (+)
// or unstage (−). Server: git_api source_control / stage / unstage / discard /
// scm_diff / commit {staged} via POST /api/git. Paths there are named from the
// repository root; `relOf` turns them into this folder's paths.

const SCM_GROUPS = [
  ['conflict', 'Merge Changes'],
  ['staged', 'Staged Changes'],
  ['unstaged', 'Changes'],
];

async function gitCall(op, data = {}) {
  try {
    return await api('/api/git', 'POST', { op, ...data });
  } catch (err) {
    return { ok: false, error: err?.message || 'That did not work' };
  }
}

async function loadScm() {
  const mine = ++scmSeq;
  const key = shownFor;
  const res = await gitCall('scm');
  if (mine !== scmSeq || key !== shownFor) return;
  if (!res.ok && /unknown git op/.test(res.error || '')) res.error = RESTART_NOTE;
  scm = res;
  paintTabs();
  paintFoot();
  if (pane === 'scm') {
    paintScm();
    syncCommitBtn();
    if (file?.diffSide) paintHead();
  }
}

/** A repository path → this folder's path ('' prefix = the repo root is open); null when outside it. */
function relOf(repoPath) {
  const prefix = scm?.prefix || '';
  if (!prefix) return repoPath;
  return repoPath.startsWith(prefix) ? repoPath.slice(prefix.length) : null;
}

function scmRows(group) {
  if (!scm?.ok) return [];
  return group === 'conflict' ? scm.conflicts || [] : scm[group] || [];
}

function scmRow(group, path) {
  return scmRows(group).find((r) => r.path === path) || null;
}

function scmNums(r) {
  if (r.binary) return '<em>binary</em>';
  return `${num(r.added) ? `<b class="num-add">+${num(r.added)}</b>` : ''}${num(r.removed) ? `<b class="num-del">−${num(r.removed)}</b>` : ''}`;
}

function paintTabs() {
  const el = $(`${ID}-tabs`);
  if (!el) return;
  const n = scm?.ok ? num(scm.total) : 0;
  // Searching inside files is a mode of the Explorer (its search box has the switch), not a tab of its own.
  const tab = (id, ic, label, extra = '') => `<button type="button" class="fb-tab" role="tab" data-act="pane" data-pane="${id}" aria-selected="${(pane === 'search' ? 'files' : pane) === id}" title="${label}">${icon(ic)}<span>${label}</span>${extra}</button>`;
  el.innerHTML = tab('files', 'folder-open', 'Explorer')
    + tab('scm', 'git-branch', 'Source Control', n ? `<b class="fb-tab-n" title="${n} changed file${n === 1 ? '' : 's'}">${n > 999 ? '999+' : n}</b>` : '');
}

/** The switch between searching names and searching inside files, at the right of the Explorer's search box. */
function modeSwitch(on) {
  const tip = `Searching file names — switch to code search (${isMac ? '⌘⇧F' : 'Ctrl+Shift+F'})`;
  const label = on ? `Searching code — switch to file names (${isMac ? '⌘⇧P' : 'Ctrl+Shift+P'})` : tip;
  return `<button type="button" class="tool-icon fb-gmode" data-act="g-mode" aria-pressed="${on}" title="${label}" aria-label="${label}">${icon('file-text')}</button>`;
}

function grepOptsHtml() {
  const opt = (key, label, tip) => `<button type="button" class="fb-find-opt" data-act="g-opt" data-opt="${key}" aria-pressed="${gopts[key]}" title="${tip}" aria-label="${tip}">${label}</button>`;
  return `<span class="search-extra fb-gopts">${opt('cs', 'Aa', 'Match case')}${opt('word', '<u>ab</u>', 'Whole word')}${opt('re', '.*', 'Regular expression')}${modeSwitch(true)}</span>`;
}

/** Names ⇄ contents: the text typed so far comes along. */
function toggleContentSearch() {
  if (pane === 'files') {
    if (query) {
      gq = query;
      grep = null;
    }
    results = null;
    setPane('search');
    return;
  }
  query = gq.trim();
  results = null;
  grepSeq += 1;
  clearTimeout(grepTimer);
  setPane('files');
  if (query) setQuery(query);
}

/** The left pane's top: the name search (Explorer), the text search, or the commit box (Source Control). */
function paintBar() {
  const bar = $(`${ID}-bar`);
  if (!bar) return;
  if (pane === 'files') {
    if (!$(`${ID}-search`)) {
      bar.innerHTML = toolbar({
        id: `${ID}-search`, placeholder: 'Search files by name', label: 'Search project files', kbd: false, extra: toolsHtml(), value: query,
      });
    }
    return;
  }
  if (pane === 'search') {
    if (!$(`${ID}-gq`)) {
      bar.innerHTML = toolbar({
        id: `${ID}-gq`, placeholder: 'Search code in files', label: 'Search code in every file', kbd: false, extra: grepOptsHtml(), value: gq,
      });
    }
    return;
  }
  if (!$(`${ID}-msg`)) {
    bar.innerHTML = `
      <div class="fb-commit">
        <div class="fb-msg-wrap">
          <textarea id="${ID}-msg" class="fb-msg" rows="1" spellcheck="true" aria-label="Commit message"
            data-1p-ignore data-lpignore="true">${escapeHtml(scmMsg)}</textarea>
          <button type="button" class="tool-icon fb-msg-gen" data-act="scm-generate" title="Write the message from the changes" aria-label="Write the message from the changes">${icon('wand-sparkles')}</button>
        </div>
        <div class="fb-commit-row">
          <div class="fb-split">
            <button type="button" class="btn btn-primary fb-commit-btn" data-act="scm-commit"></button>
            <button type="button" class="btn btn-primary fb-commit-more" id="${ID}-more" data-act="scm-menu" aria-haspopup="menu"
              aria-expanded="false" aria-controls="${ID}-menu" title="Commit, push or stash" aria-label="More git actions">${icon('chevron-down')}</button>
          </div>
          <button type="button" class="tool-icon" data-act="scm-refresh" title="Refresh" aria-label="Refresh">${icon('refresh-cw')}</button>
        </div>
        <div class="gc-menu fb-menu" id="${ID}-menu" role="menu" aria-label="Git actions" hidden></div>
      </div>`;
    scmMenuOpen = false;
    $(`${ID}-more`)?.addEventListener('esc-cancel', () => closeScmMenu(true)); // Esc closes the menu, not the drawer
    sizeMsg();
  }
  syncCommitBtn();
  paintPrDone();
}

function sizeMsg() {
  const ta = $(`${ID}-msg`);
  if (!ta) return;
  ta.style.height = 'auto';
  ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
}

/** Commit / Commit all, its placeholder and whether it can run. */
function syncCommitBtn() {
  const btn = cardEl()?.querySelector('.fb-commit-btn');
  const ta = $(`${ID}-msg`);
  if (!btn || !ta) return;
  const staged = scmRows('staged').length;
  const changes = scmRows('unstaged').length;
  const branch = scm?.ok ? scm.branch : '';
  ta.placeholder = `Message (${MOD}↵ to commit${branch ? ` on “${branch}”` : ''})`;
  const gen = cardEl()?.querySelector('.fb-msg-gen');
  if (gen) {
    gen.disabled = !!scmBusy || (!staged && !changes);
    gen.innerHTML = scmBusy === 'generate' ? '<span class="spinner"></span>' : icon('wand-sparkles');
  }
  const c = commitState();
  btn.disabled = !!c.why || !!scmBusy;
  btn.title = c.why || `${c.tip} (${MOD}↵)`;
  const busyText = { commit: 'Committing…', 'commit-push': 'Committing & pushing…' }[scmBusy];
  btn.innerHTML = busyText ? `<span class="spinner"></span><span>${busyText}</span>` : `${icon('check')}<span>${c.label}</span>`;
  const more = $(`${ID}-more`);
  if (more) {
    more.disabled = !scm?.ok || (!!scmBusy && !scmMenuOpen);
    more.innerHTML = scmBusy === 'push' || scmBusy === 'stash' ? '<span class="spinner"></span>' : icon('chevron-down');
  }
  if (scmMenuOpen) paintScmMenu();
}

/** What Commit would do now: its label, why it can't, a tooltip. */
function commitState() {
  const staged = scmRows('staged').length;
  const changes = scmRows('unstaged').length;
  let why = '';
  if (!scm?.ok) why = 'Not a git repository';
  else if (scmRows('conflict').length) why = 'Resolve the merge conflicts first (stage each file once it is fixed)';
  else if (!staged && !changes) why = 'Nothing to commit';
  return {
    why,
    label: staged ? 'Commit' : 'Commit all',
    tip: staged ? `Commit the ${staged} staged file${staged === 1 ? '' : 's'}` : `Nothing is staged: stage and commit all ${changes} changed file${changes === 1 ? '' : 's'}`,
  };
}

// ─── The commit button's ⌄ menu: Commit · Push · Stash ──────────────────────

/** Push: "Push N commits" / "Publish branch" (no upstream yet) / nothing to push. */
function pushState() {
  if (!scm?.ok) return { label: 'Push', why: 'Not a git repository' };
  if (!scm.upstream) {
    return scm.remotes
      ? { label: 'Publish branch', note: '', tip: `Push ${scm.branch || 'this branch'} and track it on the remote` }
      : { label: 'Push', why: 'This repository has no remote to push to' };
  }
  const n = num(scm.ahead);
  if (!n) return { label: 'Push', why: `Nothing to push — ${scm.upstream} is up to date` };
  return { label: 'Push', note: `↑${n}`, tip: `Push ${n} commit${n === 1 ? '' : 's'} to ${scm.upstream}` };
}

/** Commit & Push: whatever stops the commit, or a missing remote. */
function commitPushState(c) {
  if (c.why) return { why: c.why };
  if (!scm.remotes) return { why: 'This repository has no remote to push to' };
  return { tip: `${c.tip}, then push ${scm.upstream ? `to ${scm.upstream}` : 'and publish the branch'}` };
}

function stashState() {
  if (!scm?.ok) return { why: 'Not a git repository' };
  if (!num(scm.total)) return { why: 'Nothing to stash' };
  if (store.busy) return { why: 'Jarvis is working in these files — stash when it finishes' };
  return { tip: 'Put all uncommitted changes aside (git stash, new files included)' };
}

function menuItem(act, ic, label, { why = '', tip = '', note = '' } = {}) {
  return `<button type="button" class="gc-item" role="menuitem" data-act="${act}" data-esc-cancels
    ${why || scmBusy ? 'disabled' : ''} title="${escapeHtml(why || tip)}">${icon(ic)}<span>${escapeHtml(label)}</span>${note ? `<em>${escapeHtml(note)}</em>` : ''}</button>`;
}

function paintScmMenu() {
  const menu = $(`${ID}-menu`);
  if (!menu) return;
  const c = commitState();
  const p = pushState();
  const s = stashState();
  const focused = document.activeElement?.closest?.(`#${ID}-menu`) ? document.activeElement.dataset.act : '';
  // Nothing left to commit: the second item is just Push (the commits already made).
  const second = scm?.ok && !num(scm.total)
    ? menuItem('scm-m-push', 'cloud-upload', p.label, p)
    : menuItem('scm-m-commit-push', 'cloud-upload', `${c.label} & Push`, commitPushState(c));
  menu.innerHTML = menuItem('scm-m-commit', 'check', c.label, { why: c.why, tip: c.tip })
    + second
    + menuItem('scm-m-pr', 'brand:github', 'Create Pull Request', prState())
    + '<div class="gc-sep" role="separator"></div>'
    + menuItem('scm-m-stash', 'archive', 'Stash', s);
  menu.querySelectorAll('[data-esc-cancels]').forEach((b) => b.addEventListener('esc-cancel', () => closeScmMenu(true)));
  if (focused) menu.querySelector(`[data-act="${focused}"]`)?.focus({ preventScroll: true });
}

function openScmMenu() {
  const menu = $(`${ID}-menu`);
  const more = $(`${ID}-more`);
  if (!menu || !more) return;
  scmMenuOpen = true;
  paintScmMenu();
  menu.hidden = false;
  more.setAttribute('aria-expanded', 'true');
  more.setAttribute('data-esc-cancels', '');
  (menu.querySelector('.gc-item:not(:disabled)') || more).focus({ preventScroll: true });
}

function closeScmMenu(refocus = false) {
  if (!scmMenuOpen) return;
  scmMenuOpen = false;
  const menu = $(`${ID}-menu`);
  const more = $(`${ID}-more`);
  if (menu) {
    menu.hidden = true;
    menu.innerHTML = '';
  }
  more?.setAttribute('aria-expanded', 'false');
  more?.removeAttribute('data-esc-cancels');
  if (refocus) more?.focus({ preventScroll: true });
}

/** ↑ ↓ Home End inside the menu; Tab leaves it (and closes it). */
function menuKey(e) {
  const items = [...($(`${ID}-menu`)?.querySelectorAll('.gc-item:not(:disabled)') || [])];
  if (e.key === 'Tab') {
    closeScmMenu();
    return;
  }
  if (!items.length || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
  e.preventDefault();
  const at = items.indexOf(document.activeElement);
  let next = 0;
  if (e.key === 'End') next = items.length - 1;
  else if (e.key === 'ArrowDown') next = at < 0 ? 0 : (at + 1) % items.length;
  else if (e.key === 'ArrowUp') next = at <= 0 ? items.length - 1 : at - 1;
  items[next].focus({ preventScroll: true });
}

async function pushScm() {
  if (scmBusy || pushState().why) return;
  scmBusy = 'push';
  syncCommitBtn();
  const res = await gitCall('push');
  scmBusy = '';
  if (res.ok) showToast(res.published ? `Published ${scm?.branch || 'the branch'} to ${res.remote || 'the remote'}` : 'Pushed');
  else showToast(res.error || 'Could not push', true);
  syncCommitBtn();
  await afterGitChange();
}

function stashScm() {
  if (scmBusy || stashState().why) return;
  if (unsavedDoc()) {
    showToast(`Save or revert your edits to ${unsavedDoc().name} first — a stash only takes what is on disk`, true);
    return;
  }
  const n = num(scm.total);
  askConfirm({
    title: n === 1 ? 'Stash 1 change?' : `Stash ${n} changes?`,
    text: 'All uncommitted work — staged, not staged and new files — is put aside with <strong>git stash</strong>, and your files go back to the last commit. Bring it back with <strong>git stash pop</strong>.',
    label: 'Stash',
    ic: 'archive',
    run: async () => {
      scmBusy = 'stash';
      syncCommitBtn();
      const res = await gitCall('stash', { message: ($(`${ID}-msg`)?.value || '').trim().split('\n')[0] });
      scmBusy = '';
      if (res.ok) showToast(`Stashed “${res.message}” — git stash pop brings it back`);
      else showToast(res.error || 'Could not stash', true);
      syncCommitBtn();
      scmSel = null;
      await afterGitChange();
    },
  });
}

function scmBtn(act, ic, label, data = '') {
  return `<button type="button" class="tool-icon" data-act="${act}"${data} title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}"${scmBusy ? ' disabled' : ''}>${icon(ic)}</button>`;
}

function scmRowHtml(group, r) {
  const [letter, label] = SCM_STATUS[r.status] || SCM_STATUS.modified;
  const [dir, name] = splitPath(r.path);
  const on = !!scmSel && scmSel.group === group && scmSel.path === r.path;
  const data = ` data-group="${group}" data-path="${escapeHtml(r.path)}"`;
  const rel = relOf(r.path);
  const acts = [];
  if (rel !== null && r.status !== 'deleted') acts.push(scmBtn('scm-file', 'file-pen', 'Open the file', data));
  if (group === 'unstaged') acts.push(scmBtn('scm-discard', 'undo-2', r.status === 'untracked' ? 'Move this new file to the Trash' : 'Discard changes', data));
  if (group === 'staged') acts.push(scmBtn('scm-unstage', 'minus', 'Unstage', data));
  else acts.push(scmBtn('scm-stage', 'plus', group === 'conflict' ? 'Stage (mark as resolved)' : 'Stage', data));
  const where = rel === null ? ' (outside this folder)' : '';
  return `
    <div class="fb-scm-row${on ? ' is-current' : ''}">
      <button type="button" class="gx-row fb-scm-main" data-act="scm-open"${data} aria-current="${on}"
        title="${escapeHtml(`${label}: ${r.path}${r.old_path ? ` (was ${r.old_path})` : ''}${where}`)}">
        <span class="fb-ic">${icon(fileIcon(name))}</span>
        <span class="gx-path"><span class="gx-name${r.status === 'deleted' ? ' is-gone' : ''}">${escapeHtml(name)}</span>${dir ? `<span class="gx-dir">${escapeHtml(dir.replace(/\/$/, ''))}</span>` : ''}</span>
        <span class="gx-nums">${scmNums(r)}</span>
      </button>
      <span class="fb-scm-acts">${acts.join('')}</span>
      <span class="gx-st is-${escapeHtml(r.status)}" aria-label="${escapeHtml(label)}">${letter}</span>
    </div>`;
}

function scmGroupHtml(group, title) {
  const rows = scmRows(group);
  if (!rows.length && group !== 'unstaged') return '';
  const open = !scmCollapsed.has(group);
  let acts = '';
  if (group === 'staged') acts = scmBtn('scm-unstage-all', 'minus', 'Unstage all');
  if (group === 'unstaged' && rows.length) acts = scmBtn('scm-discard-all', 'undo-2', 'Discard all changes') + scmBtn('scm-stage-all', 'plus', 'Stage all changes');
  const body = rows.length
    ? rows.map((r) => scmRowHtml(group, r)).join('')
    : `<p class="gx-none">${scmRows('staged').length ? 'Everything is staged' : 'No changes'}</p>`;
  return `
    <div class="fb-scm-group">
      <div class="fb-scm-head">
        <button type="button" class="fb-scm-toggle" data-act="scm-toggle" data-group="${group}" aria-expanded="${open}">
          <span class="fb-chev">${icon('chevron-right')}</span><span>${escapeHtml(title)}</span>
        </button>
        <span class="fb-scm-acts">${acts}</span>
        <span class="fb-scm-count">${rows.length}</span>
      </div>
      ${open ? `<div class="fb-scm-rows">${body}</div>` : ''}
    </div>`;
}

function paintScm() {
  const el = listEl();
  if (!el) return;
  if (!scm) {
    el.innerHTML = '<div class="gx-loading"><span class="spinner"></span><span>Reading git…</span></div>';
    return;
  }
  if (!scm.ok) {
    el.innerHTML = /not a git repository/i.test(scm.error || '')
      ? empty('Not a git repository', 'This folder isn’t tracked by git, so there is nothing to stage or commit.', { ic: 'git-branch' })
      : empty('Couldn’t read git', escapeHtml(scm.error || ''), { ic: 'triangle-alert', action: '<button type="button" class="btn" data-act="scm-refresh">Try again</button>' });
    return;
  }
  if (!num(scm.total)) {
    el.innerHTML = empty('No changes', `Nothing to commit${scm.branch ? ` on ${escapeHtml(scm.branch)}` : ''} — the files match the last commit.`, { ic: 'circle-check' });
    return;
  }
  const active = document.activeElement;
  const refocus = active && el.contains(active) ? `${active.dataset?.act}|${active.dataset?.group}|${active.dataset?.path}` : '';
  const scroll = el.scrollTop;
  el.innerHTML = `<div class="fb-scm">${SCM_GROUPS.map(([g, t]) => scmGroupHtml(g, t)).join('')}</div>`
    + (num(scm.truncated) ? `<p class="gx-none">${num(scm.truncated)} more files not listed.</p>` : '');
  el.scrollTop = scroll;
  if (refocus) {
    [...el.querySelectorAll('[data-act]')].find((b) => `${b.dataset.act}|${b.dataset.group}|${b.dataset.path}` === refocus)?.focus({ preventScroll: true });
  }
}

function setPane(next) {
  if (next === pane) return;
  closeScmMenu();
  pane = next;
  if (pane !== 'scm') explorer = pane;
  storageSet(PANE_KEY, pane);
  prompt = null;
  $(`${ID}-bar`).innerHTML = '';
  listEl()?.classList.remove('is-loading'); // a search that was still loading dimmed the list
  paintTabs();
  paintBar();
  paintPrompt();
  paintTree();
  paintFoot();
  if (pane === 'scm' && !scm) loadScm();
  if (pane === 'search' && gq && !grep) scheduleGrep(true);
  requestAnimationFrame(() => $(PANE_FOCUS[pane])?.focus({ preventScroll: true }));
}

/** A change row: its diff on the right — in the editor's Diff view when the file
 * is here to edit, else (deleted, outside this folder) a read-only diff. */
function openChange(group, path, { focus = false } = {}) {
  const r = scmRow(group, path);
  if (!r) return;
  const diffSide = group === 'staged' ? 'staged' : 'unstaged';
  const rel = relOf(path);
  scmSel = { group, path };
  paintScm();
  if (rel === null || r.status === 'deleted') { // a read-only diff, in its own tab
    let doc = docs.find((d) => d.virtual && d.repoPath === path);
    if (doc) {
      if (doc.diffSide !== diffSide) Object.assign(doc, { diffSide, diff: null });
    } else {
      doc = {
        path: rel ?? path, repoPath: path, name: baseOf(path), kind: 'change', view: 'diff', diffSide, virtual: true,
        git: r.status === 'deleted' ? 'D' : '', outside: rel === null, hunks: [], peek: null, repo: true, prefix: '',
      };
      addTab(doc);
    }
    if (doc === file) paintMain();
    else showDoc(doc);
    return;
  }
  const have = docFor(rel);
  if (have && have !== file && !have.error && !have.loading && !have.pending) { // its tab, in the Diff view
    if (have.diffSide !== diffSide) Object.assign(have, { diffSide, diff: null });
    if (have.kind === 'text') have.view = 'diff';
    activate(have, { focus });
    return;
  }
  if (file && file.path === rel && !file.error && !file.loading && !file.virtual) {
    if (file.diffSide !== diffSide) {
      file.diffSide = diffSide;
      file.diff = null;
    }
    if (file.view === 'diff') {
      paintHead();
      applyMode();
    } else setMode('diff');
    syncReading();
    return;
  }
  openPath(rel, { focus, view: 'diff', diffSide });
}

/** The open file's change moved between Staged and Changes: follow it there
 * (`prefer` = where it just went, when it is now in both). */
function followChange(path, prefer) {
  const group = [prefer, 'conflict', 'unstaged', 'staged'].find((g) => g && scmRow(g, path));
  if (!group) return false;
  scmSel = { group, path };
  file.diffSide = group === 'staged' ? 'staged' : 'unstaged';
  file.diff = null;
  paintScm();
  paintHead();
  if (file.view === 'diff') applyMode();
  return true;
}

/** After git changed the files: the list, the tree, the open file's diff. */
async function afterGitChange(prefer = '') {
  await Promise.all([loadScm(), refreshTree()]);
  if (file?.diffSide && !followChange(file.repoPath || `${file.prefix}${file.path}`, prefer)) {
    file.diff = null; // committed, discarded: "no changes" now
    paintHead();
    if (file.view === 'diff') applyMode();
  }
  refreshBase();
  checkDisk();
}

async function scmOp(op, { paths = null, all = false } = {}) {
  if (scmBusy) return;
  scmBusy = op;
  paintScm();
  const res = await gitCall(op, all ? { all: true } : { paths });
  scmBusy = '';
  if (!res.ok) showToast(res.error || 'That did not work', true);
  // The open diff follows its file only when this touched it.
  const here = file?.diffSide ? (file.repoPath || `${file.prefix}${file.path}`) : '';
  const touched = all || (paths || []).includes(here);
  await afterGitChange(touched ? (op === 'stage' ? 'staged' : 'unstaged') : scmSel?.group || '');
  syncCommitBtn();
}

function stageRow(group, path) {
  if (scmRow(group, path)) scmOp('stage', { paths: [path] });
}

function unstageRow(path) {
  const r = scmRow('staged', path);
  if (!r) return;
  askConfirm({
    title: `Unstage ${baseOf(r.path)}?`,
    text: `<strong>${escapeHtml(r.path)}</strong> leaves the next commit and moves back to Changes. Nothing in the file changes.`,
    label: 'Unstage',
    run: () => scmOp('unstage', { paths: [r.path, r.old_path].filter(Boolean) }),
  });
}

function unstageAll() {
  const n = scmRows('staged').length;
  if (!n) return;
  askConfirm({
    title: n === 1 ? 'Unstage 1 file?' : `Unstage all ${n} files?`,
    text: `${n === 1 ? 'It leaves' : 'They leave'} the next commit and ${n === 1 ? 'moves' : 'move'} back to Changes. Nothing in your files changes.`,
    label: n === 1 ? 'Unstage' : 'Unstage all',
    run: () => scmOp('unstage', { all: true }),
  });
}

// ─── Confirmation modal (#files-confirm, on top of the drawer) ─────────────

let confirmRun = null;

function askConfirm({ title, text, label, run, ic = 'minus' }) {
  const title$ = $(`${CONFIRM_ID}-title`);
  const ok = $(`${CONFIRM_ID}-ok`);
  if (!title$ || !ok) { // markup from an older page: just do it
    run();
    return;
  }
  title$.textContent = title;
  $(`${CONFIRM_ID}-text`).innerHTML = text;
  ok.innerHTML = `${icon(ic)}<span>${escapeHtml(label)}</span>`;
  const head = $(CONFIRM_ID)?.querySelector('.prompt-icon');
  if (head) head.innerHTML = icon(ic === 'minus' ? 'git-branch' : ic);
  confirmRun = run;
  openModal(CONFIRM_ID, { focus: `${CONFIRM_ID}-ok`, onClose: () => { confirmRun = null; } });
}

function onConfirmOk() {
  const run = confirmRun;
  confirmRun = null;
  closeModal(CONFIRM_ID);
  run?.();
}

function startDiscard(path) {
  const r = path ? scmRow('unstaged', path) : null;
  const rows = scmRows('unstaged');
  if (path && !r) return;
  if (!path && !rows.length) return;
  startPrompt('discard', path ? { paths: [path], rows: [r] } : { all: true, rows });
}

/** Commit (what's staged, else everything); `push`: then push it too (publishing a new branch). */
async function commitScm({ push = false } = {}) {
  const ta = $(`${ID}-msg`);
  const message = (ta?.value || '').trim();
  if (scmBusy || !scm?.ok) return;
  if (!message) {
    showToast('Write a commit message first', true);
    ta?.focus();
    return;
  }
  if (unsavedDoc()) {
    showToast(`Save ${unsavedDoc().name} first — unsaved edits aren’t part of a commit`, true);
    return;
  }
  const staged = scmRows('staged').length > 0;
  scmBusy = push ? 'commit-push' : 'commit';
  syncCommitBtn();
  const res = await gitCall('commit', { message, staged, push });
  scmBusy = '';
  if (res.ok) {
    scmMsg = '';
    if (ta) {
      ta.value = '';
      sizeMsg();
    }
    const sha = res.sha || '';
    if (!push) showToast(`Committed ${sha}`.trim());
    else if (res.pushed) showToast(res.published ? `Committed ${sha} and published the branch` : `Committed ${sha} and pushed`);
    else showToast(`Committed ${sha} — the push failed: ${res.push_error || 'unknown error'}`, true); // the commit stays
    scmSel = null;
  } else showToast(res.error || 'Could not commit', true);
  syncCommitBtn();
  await afterGitChange();
}

async function generateMsg() {
  if (scmBusy || !scm?.ok) return;
  scmBusy = 'generate';
  syncCommitBtn();
  const res = await gitCall('generate', { kind: 'commit', staged: scmRows('staged').length > 0 });
  scmBusy = '';
  syncCommitBtn();
  const ta = $(`${ID}-msg`);
  if (!res.ok) {
    showToast(res.error || 'Could not write a message', true);
    return;
  }
  scmMsg = res.message || '';
  if (ta) {
    ta.value = scmMsg;
    sizeMsg();
    ta.focus();
  }
}

/** Stage / Unstage in the file header while a change's diff is open. */
function scmHeadBtn() {
  if (!file?.diffSide || !scm?.ok) return '';
  const path = file.repoPath || `${file.prefix}${file.path}`;
  if (file.diffSide === 'staged' && scmRow('staged', path)) {
    return `<button type="button" class="btn btn-sm btn-quiet" data-act="head-unstage" ${scmBusy ? 'disabled' : ''} title="Take it out of the next commit">${icon('minus')}<span>Unstage</span></button>`;
  }
  const group = scmRow('conflict', path) ? 'conflict' : scmRow('unstaged', path) ? 'unstaged' : '';
  if (file.diffSide === 'unstaged' && group) {
    return `<button type="button" class="btn btn-sm btn-quiet" data-act="head-stage" data-group="${group}" ${scmBusy ? 'disabled' : ''} title="Add it to the next commit">${icon('plus')}<span>Stage</span></button>`;
  }
  return '';
}

// ─── Editor behaviour ─────────────────────────────────────────────────────

function insertText(ta, text) {
  ta.focus({ preventScroll: true });
  // execCommand keeps the browser's undo stack; setRangeText is the fallback.
  // (insertText with '' doesn't reliably remove a selection: that's `delete`.)
  const done = text ? document.execCommand('insertText', false, text) : document.execCommand('delete');
  if (!done) {
    ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, 'end');
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
}

function onEdit(e) {
  if (!file) return;
  file.text = e.target.value;
  paintGutter();
  paintHighlight();
  scheduleCaret();
  closePeek();
  scheduleChanges();
  // The header (Save / Revert, the dot) and the tree's marker only change when dirtiness flips.
  if (isDirty() !== file.shownDirty) {
    paintHead();
    paintTree();
  }
}

function indentSelection(ta, outdent) {
  const unit = file.indent;
  const { value, selectionStart: s, selectionEnd: e } = ta;
  const multi = value.slice(s, e).includes('\n');
  if (!outdent && !multi) {
    const col = s - (value.lastIndexOf('\n', s - 1) + 1);
    insertText(ta, unit === '\t' ? '\t' : ' '.repeat(unit.length - (col % unit.length)));
    return;
  }
  const a = value.lastIndexOf('\n', s - 1) + 1;
  const endRef = e > s && value[e - 1] === '\n' ? e - 1 : e;
  let b = value.indexOf('\n', endRef);
  if (b < 0) b = value.length;
  const block = value.slice(a, b);
  const lines = block.split('\n').map((line) => {
    if (!outdent) return line ? unit + line : line;
    if (line[0] === '\t') return line.slice(1);
    const lead = line.match(/^ */)[0].length;
    return line.slice(Math.min(lead, unit === '\t' ? 4 : unit.length));
  });
  const next = lines.join('\n');
  if (next === block) return;
  ta.setSelectionRange(a, b);
  insertText(ta, next);
  const first = lines[0].length - block.split('\n')[0].length;
  ta.setSelectionRange(Math.max(a, s + first), e + (next.length - block.length));
}

/** Enter keeps the line's indent (one deeper after { [ ( or :). True when handled. */
function autoIndent(ta) {
  const { value, selectionStart: s, selectionEnd: e } = ta;
  const before = value.slice(value.lastIndexOf('\n', s - 1) + 1, s);
  const indent = before.match(/^[ \t]*/)[0];
  const last = before.trimEnd().slice(-1);
  const opens = last !== '' && '{[(:'.includes(last);
  if (!indent && !opens) return false;
  const extra = opens ? file.indent : '';
  const closer = { '{': '}', '[': ']', '(': ')' }[last];
  if (closer && value[e] === closer) {
    insertText(ta, `\n${indent}${extra}\n${indent}`);
    const pos = s + 1 + indent.length + extra.length;
    ta.setSelectionRange(pos, pos);
  } else {
    insertText(ta, `\n${indent}${extra}`);
  }
  return true;
}

// The editor's line commands (keymap.js ids → editor_cmds.js).
const EDITOR_CMDS = {
  'edit.comment': (v, s, e) => toggleLineComment(v, s, e, commentFor(file.name)),
  'edit.blockComment': (v, s, e) => toggleBlockComment(v, s, e, commentFor(file.name)),
  'edit.indent': (v, s, e) => shiftLines(v, s, e, file.indent, false),
  'edit.outdent': (v, s, e) => shiftLines(v, s, e, file.indent, true),
  'edit.moveUp': (v, s, e) => moveLines(v, s, e, -1),
  'edit.moveDown': (v, s, e) => moveLines(v, s, e, 1),
  'edit.copyUp': (v, s, e) => copyLines(v, s, e, -1),
  'edit.copyDown': (v, s, e) => copyLines(v, s, e, 1),
  'edit.deleteLine': (v, s, e) => deleteLines(v, s, e),
  'edit.lineBelow': (v, s, e) => insertLine(v, s, e, false),
  'edit.lineAbove': (v, s, e) => insertLine(v, s, e, true),
  'edit.selectLine': (v, s, e) => selectLine(v, s, e),
};
const EDITOR_IDS = Object.keys(EDITOR_CMDS);

/** Run one line command on the editor: one execCommand edit (so ⌘Z undoes it in one step). */
function runEditorCmd(ta, id) {
  const ed = EDITOR_CMDS[id](ta.value, ta.selectionStart, ta.selectionEnd);
  if (!ed) {
    if ((id === 'edit.comment' || id === 'edit.blockComment') && !commentFor(file.name)) showToast(`${file.name} has no comment syntax`);
    return;
  }
  if (ed.from !== ed.to || ed.text) {
    ta.setSelectionRange(ed.from, ed.to);
    insertText(ta, ed.text);
  }
  ta.setSelectionRange(ed.selStart, ed.selEnd);
  scheduleCaret();
}

function onEditorKey(e) {
  if (e.isComposing || !file || e.target.readOnly) return;
  const mod = e.metaKey || e.ctrlKey;
  const cmd = matchAny(e, EDITOR_IDS);
  if (cmd) {
    e.preventDefault();
    runEditorCmd(e.target, cmd);
  } else if (matches(e, 'edit.nextChange') || matches(e, 'edit.prevChange')) {
    e.preventDefault();
    gotoChange(matches(e, 'edit.prevChange') ? -1 : 1);
  } else if (e.key === 'Tab' && !mod && !e.altKey) {
    e.preventDefault();
    indentSelection(e.target, e.shiftKey);
  } else if (e.key === 'Enter' && !mod && !e.altKey && !e.shiftKey) {
    if (autoIndent(e.target)) e.preventDefault();
  }
}

// ─── Events ───────────────────────────────────────────────────────────────

function rowsInList() {
  const sel = pane === 'scm' ? '.fb-scm-main' : pane === 'search' && gq ? '.fb-gh, .fb-gm' : results ? '.gx-row' : '.fb-row';
  return [...(listEl()?.querySelectorAll(sel) || [])];
}

function startRename(path) {
  const e = entryAt(path);
  startPrompt('rename', { path, value: baseOf(path), isDir: !!e?.dir });
}

function startDelete(path) {
  const e = entryAt(path);
  startPrompt('delete', { path, isDir: !!e?.dir });
}

function onRowClick(row, ev) {
  const path = row.dataset.path;
  cursorPath = path;
  scmSel = null;
  if (row.dataset.dir === '1') {
    toggleDir(path);
    return;
  }
  // Enter / Space arrive as a click with no pointer: those go on to the editor.
  openPath(path, { focus: ev.detail === 0 });
}

function openHit(i, focus = false) {
  const r = results?.items?.[i];
  if (!r) return;
  scmSel = null;
  openPath(r.path, { focus });
}

function actBanner(name) {
  const then = banner?.then;
  switch (name) {
    case 'b-save': {
      banner = { ...banner };
      save().then((ok) => {
        if (!ok) return;
        banner = null;
        paintBanner();
        then?.();
      });
      break;
    }
    case 'b-discard':
      banner = null;
      discardEdits();
      paintBanner();
      then?.();
      break;
    case 'b-discard-here':
      banner = null;
      discardEdits();
      paintBanner();
      break;
    case 'b-keep':
      banner = null;
      paintBanner();
      focusEditor();
      break;
    case 'b-overwrite':
      banner = null;
      paintBanner();
      save({ force: true }).then((ok) => { if (ok) then?.(); });
      break;
    case 'b-reload':
      banner = null;
      paintBanner();
      reloadFile({ notice: 'Loaded from disk' }); // the disk version replaces the edits once it is read
      break;
    case 'b-acknowledge':
      if (file) file.etag = banner.diskEtag || file.etag;
      banner = null;
      paintBanner();
      break;
    case 'b-recreate':
      call('create', { path: file.path, kind: 'file' }).then((res) => {
        if (!res.ok && res.code !== 'exists') {
          showToast(res.error, true);
          return;
        }
        file.gone = false;
        banner = null;
        paintBanner();
        save({ force: true });
        refreshTree();
      });
      break;
    case 'b-close':
      if (file) dropTab(file);
      break;
    default:
  }
}

async function act(name, b) {
  switch (name) {
    case 'new-file':
      startPrompt('new-file', { dir: targetDir() });
      break;
    case 'new-folder':
      startPrompt('new-folder', { dir: targetDir() });
      break;
    case 'refresh': {
      b.classList.add('is-spinning');
      await refreshTree({ quiet: false });
      await checkDisk();
      b.classList.remove('is-spinning');
      break;
    }
    case 'terminal':
      toggleTerminal();
      break;
    case 'reload':
      tree.delete('');
      loadAll();
      break;
    case 'hit':
      openHit(Number(b.dataset.i));
      break;
    case 'retry':
      if (file && !file.virtual) {
        Object.assign(file, { loading: true, error: '', code: '' });
        paintMain();
        loadDoc(file, { focus: true });
      }
      break;
    case 'tab':
      activate(docs[Number(b.dataset.i)]);
      break;
    case 'tab-close':
      closeTab(docs[Number(b.dataset.i)]);
      break;
    case 'mode':
      setMode(b.dataset.mode);
      break;
    case 'find':
      if (isFindOpen()) closeFind(viewEl(), { refocus: true });
      else if (findAvailable()) openFind(viewEl());
      break;
    case 'wrap': {
      const on = storageGet(WRAP_KEY, '') !== '1';
      storageSet(WRAP_KEY, on ? '1' : '');
      const ed = mainEl()?.querySelector('.fb-editor');
      const ta = ed?.querySelector('.fb-text');
      ed?.classList.toggle('is-wrap', on);
      ta?.setAttribute('wrap', on ? 'soft' : 'off');
      b.setAttribute('aria-pressed', String(on));
      paintGutter(true);
      syncHighlight();
      closePeek();
      paintChgChip();
      break;
    }
    case 'copy':
      if (file && await copyText(file.path)) flashCheck(b);
      break;
    case 'editor':
      if (file) host.openInEditor?.(file.path);
      break;
    case 'rename':
      if (file) startRename(file.path);
      break;
    case 'delete':
      if (file) startDelete(file.path);
      break;
    case 'chg-prev':
      gotoChange(-1);
      break;
    case 'chg-next':
      gotoChange(1);
      break;
    case 'peek-prev':
      stepPeek(-1);
      break;
    case 'peek-next':
      stepPeek(1);
      break;
    case 'peek-revert':
      if (file?.peek != null) revertChange(file.peek);
      break;
    case 'peek-close':
      closePeek();
      focusEditor();
      break;
    case 'save':
      save();
      break;
    case 'revert':
      banner = { kind: 'revert' };
      paintBanner();
      break;
    case 'p-confirm':
      submitPrompt();
      break;
    case 'pane':
      setPane(b.dataset.pane === 'scm' ? 'scm' : explorer); // the Explorer tab: the mode it was last in
      break;
    case 'g-mode':
      toggleContentSearch();
      break;
    case 'g-opt': {
      const key = b.dataset.opt;
      gopts = { ...gopts, [key]: !gopts[key] };
      storageSet(GREP_OPTS_KEY, JSON.stringify(gopts));
      b.setAttribute('aria-pressed', String(gopts[key]));
      if (gq) {
        grep = { ...(grep?.files ? grep : null), loading: true };
        grepSeq += 1;
        scheduleGrep(true);
        paintGrep();
      }
      $(`${ID}-gq`)?.focus({ preventScroll: true });
      break;
    }
    case 'g-fold':
      if (grepFolded.has(b.dataset.path)) grepFolded.delete(b.dataset.path);
      else grepFolded.add(b.dataset.path);
      paintGrep();
      break;
    case 'g-fold-all': {
      const files = grep?.files || [];
      if (files.every((f) => grepFolded.has(f.path))) grepFolded.clear();
      else files.forEach((f) => grepFolded.add(f.path));
      paintGrep();
      break;
    }
    case 'scm-open':
      openChange(b.dataset.group, b.dataset.path, { focus: false });
      break;
    case 'scm-file': {
      const rel = relOf(b.dataset.path);
      if (rel === null) break;
      scmSel = null;
      if (file && file.path === rel && !file.virtual) {
        setMode('edit');
        focusEditor();
      } else openPath(rel, { focus: true });
      paintScm();
      break;
    }
    case 'scm-stage':
      stageRow(b.dataset.group, b.dataset.path);
      break;
    case 'scm-unstage':
      unstageRow(b.dataset.path);
      break;
    case 'scm-discard':
      startDiscard(b.dataset.path);
      break;
    case 'scm-stage-all':
      scmOp('stage', { all: true });
      break;
    case 'scm-unstage-all':
      unstageAll();
      break;
    case 'diff-view': {
      const view = b.dataset.view === 'split' ? 'split' : 'unified';
      if (storageGet(DIFF_VIEW_KEY, 'unified') === view) break;
      storageSet(DIFF_VIEW_KEY, view);
      paintHead();
      applyDiffLayout();
      break;
    }
    case 'scm-discard-all':
      startDiscard('');
      break;
    case 'scm-toggle': {
      const g = b.dataset.group;
      if (scmCollapsed.has(g)) scmCollapsed.delete(g);
      else scmCollapsed.add(g);
      paintScm();
      break;
    }
    case 'scm-refresh':
      b.classList.add('is-spinning');
      await afterGitChange(scmSel?.group || '');
      b.classList.remove('is-spinning');
      break;
    case 'scm-commit':
      commitScm();
      break;
    case 'scm-menu':
      if (scmMenuOpen) closeScmMenu(true);
      else openScmMenu();
      break;
    case 'scm-m-commit':
      closeScmMenu(true);
      commitScm();
      break;
    case 'scm-m-push':
      closeScmMenu(true);
      pushScm();
      break;
    case 'scm-m-commit-push':
      closeScmMenu(true);
      commitScm({ push: true });
      break;
    case 'scm-m-stash':
      closeScmMenu(true);
      stashScm();
      break;
    case 'scm-m-pr':
      closeScmMenu(true);
      openPr();
      break;
    case 'pr-dismiss':
      prUrl = '';
      paintPrDone();
      break;
    case 'br-menu':
      if (brOpen) closeBranchMenu(true);
      else openBranchMenu();
      break;
    case 'br-go':
    case 'br-new':
      switchBranch(b.dataset.branch || '', name === 'br-new');
      break;
    case 'scm-generate':
      generateMsg();
      break;
    case 'head-stage': {
      const path = file?.repoPath || `${file?.prefix}${file?.path}`;
      if (file) stageRow(b.dataset.group, path);
      break;
    }
    case 'head-unstage':
      if (file) unstageRow(file.repoPath || `${file.prefix}${file.path}`);
      break;
    case 'p-cancel':
      cancelPrompt();
      break;
    default:
      if (name.startsWith('b-')) actBanner(name);
  }
}

function onClick(e) {
  const mark = e.target.closest('.fb-mark');
  if (mark) {
    const i = Number(mark.dataset.hunk);
    if (file?.peek === i) closePeek();
    else openPeek(i);
    return;
  }
  if (file?.peek != null && !e.target.closest('.fb-peek')) closePeek(); // a click elsewhere puts it away
  const row = e.target.closest('.fb-row');
  if (row && listEl()?.contains(row)) {
    onRowClick(row, e);
    return;
  }
  const gm = e.target.closest('.fb-gm');
  if (gm && listEl()?.contains(gm)) {
    openMatch(gm, e.detail === 0); // Enter arrives as a click with no pointer: that one goes on to the editor
    return;
  }
  const b = e.target.closest('[data-act]');
  if (!b || b.disabled || !$(ID).contains(b)) return;
  act(b.dataset.act, b);
}

function treeKey(e, row) {
  const path = row.dataset.path;
  const isDir = row.dataset.dir === '1';
  const open = isDir && expanded.has(path);
  const rows = rowsInList();
  const at = rows.indexOf(row);
  const focusRow = (r) => {
    r?.focus({ preventScroll: true });
    r?.scrollIntoView({ block: 'nearest' });
  };
  switch (e.key) {
    case 'ArrowRight':
      e.preventDefault();
      if (isDir && !open) toggleDir(path);
      else if (open) focusRow(rows[at + 1]);
      break;
    case 'ArrowLeft':
      e.preventDefault();
      if (open) toggleDir(path);
      else focusRow(rows.find((r) => r.dataset.path === dirOf(path)));
      break;
    case 'Home':
      e.preventDefault();
      focusRow(rows[0]);
      break;
    case 'End':
      e.preventDefault();
      focusRow(rows[rows.length - 1]);
      break;
    case 'F2':
      e.preventDefault();
      startRename(path);
      break;
    case 'Delete':
    case 'Backspace':
      if (e.key === 'Delete' || e.metaKey) {
        e.preventDefault();
        startDelete(path);
      }
      break;
    default:
  }
}

/** The editor is showing text: ⌘F can search it (otherwise the browser's own find stays in charge). */
function findAvailable() {
  const ed = mainEl()?.querySelector('.fb-editor');
  return !!(file && file.kind === 'text' && file.view === 'edit' && !file.loading && ed && !ed.hidden && editorEl());
}

/** ⌘F / Ctrl+F opens find in the open file; ⌘G / Ctrl+G / F3 step through matches. True when handled. */
function findKey(e) {
  if (e.isComposing || e.altKey || !findAvailable()) return false;
  const t = e.target;
  const inEditor = t.matches?.('.fb-text') || !!t.closest?.('.fb-find');
  // Other fields (tree search, commit message, rename) keep their keys; so do the tabs and the terminal.
  if (!inEditor && t.matches?.('input, textarea, select, [contenteditable]')) return false;
  if (matches(e, 'files.find')) {
    e.preventDefault();
    openFind(viewEl());
    return true;
  }
  const step = matchAny(e, ['files.findPrev', 'files.findNext']);
  if (step) {
    e.preventDefault();
    stepFind(viewEl(), step === 'files.findPrev' ? -1 : 1);
    return true;
  }
  return false;
}

function onKey(e) {
  const t = e.target;
  // The docked terminal's keys go to its shell; ⌘S still saves the open file.
  if (t.closest?.('.term-panel')) {
    // Ctrl+S stays the shell's (XOFF); a ⌘ binding still saves.
    if (matches(e, 'files.save') && !e.ctrlKey) {
      e.preventDefault();
      save();
    }
    return;
  }
  if (matches(e, 'files.save')) {
    e.preventDefault();
    save();
    return;
  }
  // ⌘⇧F / Ctrl+Shift+F: search every file (the selected text, when there is one, is the query).
  if (matches(e, 'files.searchInDrawer')) {
    e.preventDefault();
    startGrep();
    return;
  }
  // ⌘⇧P / Ctrl+Shift+P: search files by name.
  if (matches(e, 'files.findFile')) {
    e.preventDefault();
    startNameSearch();
    return;
  }
  if (findKey(e)) return;
  // ⌥W closes the shown tab (⌘W / Ctrl+W belong to the browser). keymap.js matches e.code: ⌥ changes e.key on a Mac.
  const typing = t.matches?.('input, textarea') && !t.matches('.fb-text');
  if (matches(e, 'files.closeTab') && file && !typing) {
    e.preventDefault();
    closeTab(file);
    return;
  }
  if (t.closest?.('.fb-ftab') && !t.closest('.fb-ftab-x')) {
    tabKey(e, t.closest('.fb-ftab'));
    return;
  }
  if (brOpen && t.closest?.(`#${BR_ID}`)) {
    branchKey(e);
    return;
  }
  if (scmMenuOpen && (t.closest?.(`#${ID}-menu`) || t.id === `${ID}-more`)) {
    if (t.id === `${ID}-more` && e.key === 'ArrowDown') {
      e.preventDefault();
      $(`${ID}-menu`)?.querySelector('.gc-item:not(:disabled)')?.focus({ preventScroll: true });
    } else menuKey(e);
    return;
  }
  if (t.id === `${ID}-gq`) {
    if (e.key === 'Enter' && !e.isComposing) {
      e.preventDefault();
      scheduleGrep(true); // don't wait for the typing pause
    } else arrowRows(e, t, rowsInList());
    return;
  }
  if (pane === 'search' && gq && t.closest?.(`#${ID}-list`)) {
    if (t.matches('.fb-gh') && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
      const open = t.getAttribute('aria-expanded') === 'true';
      if ((e.key === 'ArrowRight') !== open) {
        e.preventDefault();
        t.click();
      }
      return;
    }
    arrowRows(e, $(`${ID}-gq`), rowsInList());
    return;
  }
  if (t.id === `${ID}-msg`) {
    if (matches(e, 'files.commit')) {
      e.preventDefault();
      commitScm();
    }
    return;
  }
  if (pane === 'scm' && t.closest?.('.fb-scm-main')) {
    arrowRows(e, $(`${ID}-msg`), rowsInList());
    return;
  }
  if (t.id === `${ID}-prompt-input`) {
    if (e.key === 'Enter' && !e.isComposing) {
      e.preventDefault();
      submitPrompt();
    }
    return;
  }
  if (t.matches?.('.fb-text')) return;
  const search = $(`${ID}-search`) || $(`${ID}-gq`); // the tree's ↑ goes back to whichever box is shown
  const rows = rowsInList();
  if (t === search) {
    if (e.key === 'Enter' && results?.items?.length) {
      e.preventDefault();
      openHit(0, true);
    } else arrowRows(e, search, rows);
    return;
  }
  const row = t.closest?.('.fb-row');
  if (row) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') arrowRows(e, search, rows);
    else treeKey(e, row);
  } else if (t.closest?.('.fb-hit')) {
    arrowRows(e, search, rows);
  }
}

/** The tab strip: ← / → / Home / End move between tabs, Delete closes, Enter goes to the editor. */
function tabKey(e, tab) {
  const i = Number(tab.dataset.i);
  let to = -1;
  if (e.key === 'ArrowRight') to = Math.min(docs.length - 1, i + 1);
  else if (e.key === 'ArrowLeft') to = Math.max(0, i - 1);
  else if (e.key === 'Home') to = 0;
  else if (e.key === 'End') to = docs.length - 1;
  else if (e.key === 'Delete' || e.key === 'Backspace') {
    e.preventDefault();
    closeTab(docs[i]);
    requestAnimationFrame(() => $(`${ID}-ftabs`)?.querySelector('.fb-ftab.is-on')?.focus({ preventScroll: true }));
    return;
  } else if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    activate(docs[i], { focus: true });
    focusEditor();
    return;
  }
  if (to < 0 || !docs[to]) return;
  e.preventDefault();
  activate(docs[to]);
  $(`${ID}-ftabs`)?.querySelector('.fb-ftab.is-on')?.focus({ preventScroll: true });
}

function onInput(e) {
  if (e.target.id === `${ID}-search`) setQuery(e.target.value);
  else if (e.target.id === `${ID}-gq`) setGrepQuery(e.target.value);
  else if (e.target.id === `${BR_ID}-q`) paintBranchList();
  else if (e.target.id === `${ID}-msg`) {
    scmMsg = e.target.value;
    sizeMsg();
  }
}

function toolsHtml() {
  const t = (act, ic, label) => `<button type="button" class="tool-icon" data-act="${act}" title="${label}" aria-label="${label}">${icon(ic)}</button>`;
  return `<span class="search-extra fb-tools">${modeSwitch(false)}${t('new-file', 'plus', 'New file')}${t('new-folder', 'folder-plus', 'New folder')}${t('refresh', 'refresh-cw', 'Refresh')}</span>`;
}

function paintAll() {
  $(`${ID}-bar`).innerHTML = '';
  paintTabs();
  paintBar();
  cardEl()?.classList.toggle('is-reading', reading && !wide());
  paintBanner();
  paintPrompt();
  paintTree();
  paintMain();
  paintFoot();
}

/** Full screen: the drawer covers the whole window instead of its right-hand strip. */
function setFull(on) {
  cardEl()?.classList.toggle('is-full', on);
  const btn = $(`${ID}-full`);
  if (btn) {
    const label = on ? 'Exit full screen' : 'Full screen';
    btn.setAttribute('aria-pressed', String(on));
    btn.setAttribute('aria-label', label);
    btn.title = label;
    btn.querySelector('span').innerHTML = icon(on ? 'minimize-2' : 'maximize-2');
  }
  storageSet(FULL_KEY, on ? '1' : '0');
}

/** Put the caret on `line` (1-based) once `path`'s editor is up — a terminal
 * link like `app.py:42`, or a search match. `col` / `len` (a match's place in the
 * line) select just that text, else the whole line is selected; `focus: false`
 * leaves the keyboard where it is (the search list). Gives up quietly if the
 * file doesn't open as text. */
function revealLineWhenShown(path, line, { col = -1, len = 0, focus = true, pin = false, tries = 40 } = {}) {
  const doc = docFor(path);
  if (!doc || doc.error || tries <= 0) return;
  const ta = $(`${ID}-view`)?.querySelector('.fb-text');
  if (doc.loading || doc.pending || mounted !== doc || !ta) {
    setTimeout(() => revealLineWhenShown(path, line, { col, len, focus, pin, tries: tries - 1 }), 75);
    return;
  }
  if (file === doc && doc.view !== 'edit') setMode('edit'); // opened from a diff: the text is what we point at
  const lines = ta.value.split('\n');
  const n = Math.max(1, Math.min(line, lines.length));
  let pos = 0;
  for (let i = 0; i < n - 1; i++) pos += lines[i].length + 1;
  const from = col >= 0 && col < lines[n - 1].length ? pos + col : pos;
  const to = col >= 0 && len > 0 ? Math.min(from + len, pos + lines[n - 1].length) : pos + lines[n - 1].length;
  if (focus) ta.focus({ preventScroll: true });
  ta.setSelectionRange(from, to);
  const lh = parseFloat(getComputedStyle(ta).lineHeight) || 18;
  ta.scrollTop = Math.max(0, (n - 1) * lh - ta.clientHeight / 3);
  ta.dispatchEvent(new Event('scroll'));   // the gutter follows
  scheduleCaret();                         // the line band and the status bar too
  if (pin && col >= 0 && len > 0) pinMatch(viewEl(), from, to); // drawn like a find match, even while the list has focus
}

/** The drawer. `path` opens that file (a project-relative path) right away,
 * at `line` when given; `pane` starts on a tab — `'scm'` Source Control,
 * `'files'` the Explorer, `'search'` the text search (with `search` as its query). */
export function openFiles({ path = '', line = 0, pane: want = '', search: seed = '' } = {}) {
  const key = projectKey();
  if (shownFor !== key) resetProject(key);
  if (PANES.includes(want)) {
    pane = want;
    if (pane !== 'scm') explorer = pane;
    storageSet(PANE_KEY, pane);
  }
  if (pane === 'search' && String(seed).trim()) {
    gq = String(seed).slice(0, GREP_SEED_MAX);
    grep = null;
  }
  query = '';
  results = null;
  reading = false;
  setHomeSub(ID, store.session?.cwd_display || store.session?.cwd || 'This project');
  paintAll();
  if (pane === 'search' && gq) scheduleGrep(true); // files may have changed since the last search
  openModal(ID, {
    focus: PANE_FOCUS[pane],
    onClose: () => {
      saveViewState();
      closeScmMenu();
      closeBranchMenu();
      reading = false;
      cardEl()?.classList.remove('is-reading');
      undockTerminal();
      const dirty = dirtyDocs();
      if (dirty.length) {
        showToast(`Unsaved changes in ${dirty.length === 1 ? dirty[0].name : `${dirty.length} files`} are kept — reopen Files to continue`);
      }
    },
  });
  // The terminal comes along: same shells, docked above the footer while Files is open.
  const card = cardEl();
  dockTerminal(card, {
    before: $(`${ID}-foot`),
    grow: card?.querySelector('.gx-split'),
    focusBack: () => card?.focus({ preventScroll: true }),
  });
  loadAll().then(() => {
    const restore = pendingFile;
    pendingFile = '';
    // Last visit's tabs come back unread; each is read when it is shown.
    if (pendingTabs.length) {
      pendingTabs.forEach((p) => { if (!docFor(p)) docs.push(newDoc(p, { restored: true })); });
      pendingTabs = [];
      paintFileTabs();
    }
    if (path) {
      openPath(path, { focus: true });
      if (line > 0) revealLineWhenShown(path, line, { focus: true });
    } else if (!file && restore && wide()) openPath(restore, { restoring: true });
    else if (file) checkDisk();
  });
}

function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(() => {
    if (isModalOpen(ID) && !document.hidden) {
      checkDisk();
      ticks += 1;
      if (ticks % TREE_EVERY === 0 && !prompt) refreshTree();
    }
    schedulePoll();
  }, POLL_MS);
}

/** `host`: { openInEditor(path), editorLabel() } — the git card owns the editor choice. */
export function initFiles(hooks = {}) {
  host = { ...host, ...hooks };
  initFind({
    onMove: scheduleCaret,
    onToggle: (on) => mainEl()?.querySelector('[data-act="find"]')?.setAttribute('aria-pressed', String(on)),
    escHeld: () => file?.peek != null,
  });
  const el = $(ID);
  if (!el) return;
  el.addEventListener('click', onClick);
  el.addEventListener('keydown', onKey);
  el.addEventListener('input', onInput);
  $(`${ID}-full`)?.addEventListener('click', () => setFull(!cardEl()?.classList.contains('is-full')));
  setFull(storageGet(FULL_KEY, '0') === '1');
  $(`${CONFIRM_ID}-ok`)?.addEventListener('click', onConfirmOk);
  initPr();
  document.addEventListener('jarvis:terminal-state', onTerminalState);
  // A click anywhere else puts the commit button's menu away.
  document.addEventListener('pointerdown', (e) => {
    if (scmMenuOpen && !e.target.closest?.(`#${ID}-menu, #${ID}-more`)) closeScmMenu();
    if (brOpen && !e.target.closest?.(`#${BR_ID}, #${BR_ID}-btn`)) closeBranchMenu();
  });
  // A middle click closes a tab (and doesn't start the browser's autoscroll).
  el.addEventListener('mousedown', (e) => {
    if (e.button === 1 && e.target.closest('.fb-ftab')) e.preventDefault();
  });
  el.addEventListener('auxclick', (e) => {
    const tab = e.button === 1 && e.target.closest('.fb-ftab');
    if (!tab) return;
    e.preventDefault();
    closeTab(docs[Number(tab.dataset.i)]);
  });
  el.addEventListener('dblclick', (e) => {
    if (e.target.closest('.fb-row[data-dir="0"]')) focusEditor();
  });
  window.matchMedia(WIDE).addEventListener?.('change', () => {
    if (!isModalOpen(ID)) return;
    if (wide() && reading) {
      reading = false;
      cardEl()?.classList.remove('is-reading');
      setView(ID, null);
    }
    paintFoot();
    applyDiffLayout(); // side by side only fits a wide screen
  });
  window.addEventListener('focus', () => {
    if (!isModalOpen(ID)) return;
    refreshTree();
    checkDisk();
  });
  // Leaving the page with unsaved edits asks first.
  window.addEventListener('beforeunload', (e) => {
    if (!isFilesDirty()) return;
    e.preventDefault();
    e.returnValue = '';
  });
  // A turn just ended: Jarvis may have added, changed or removed files.
  subscribe((s) => {
    if (wasBusy && !s.busy && isModalOpen(ID)) {
      refreshTree();
      refreshBase();
      checkDisk();
      if (pane === 'search' && gq) scheduleGrep(true); // Jarvis may have changed what the results point at
    }
    wasBusy = !!s.busy;
  });
  schedulePoll();
}
