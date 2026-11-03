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
 * ⌘/Ctrl+S saves. Native undo is kept (edits go through execCommand).
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

const ID = 'files';
const STATE_KEY = 'jarvis-files';
const WRAP_KEY = 'jarvis-files-wrap';
const WIDE = '(min-width: 900px)';
const POLL_MS = 4000;
const TREE_EVERY = 3;            // disk checks between tree refreshes
const SEARCH_MS = 140;
const MAX_REMEMBERED_DIRS = 60;
const MOD = isMac ? '⌘' : 'Ctrl';
const LINE_PX = 20;              // the editor's line height (css .fb-text): the gutter bars are placed by it
const EDITOR_PAD_PX = 12;        // the editor's top padding (same)
const CHANGES_MS = 140;          // wait this long after a keystroke before comparing with git
const PEEK_MAX_LINES = 30;
const DIFF_VIEW_KEY = 'jarvis-files-diffview'; // the Diff view: 'unified' | 'split' (side by side, wide screens)
const CONFIRM_ID = 'files-confirm';
const PANE_KEY = 'jarvis-files-pane';  // the left pane's tab: 'files' (Explorer) | 'scm' (Source Control)
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
let file = null;                 // the open file's document (see openPath / applyRead)
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
let noticeTimer = 0;
let searchSeq = 0;
let openSeq = 0;
let diffSeq = 0;
const dirSeq = new Map();
let pane = storageGet(PANE_KEY, 'files') === 'scm' ? 'scm' : 'files';
let scm = null;                  // last {op: scm} answer (Source Control tab)
let scmSel = null;               // { group, path } — the change whose diff is open
let scmMsg = '';                 // the commit message being written
let scmBusy = '';                // a git op the tab is running
let scmSeq = 0;
let scmMenuOpen = false;         // the commit button's ⌄ menu
const scmCollapsed = new Set();  // folded groups
let host = { openInEditor: null, editorLabel: () => 'your editor' };

const num = (n) => Number(n) || 0;
const wide = () => window.matchMedia(WIDE).matches;
const cardEl = () => $(ID)?.querySelector('.modal-card');
const listEl = () => $(`${ID}-list`);
const mainEl = () => $(`${ID}-main`);
const projectKey = () => `${BASE}|${store.session?.cwd || ''}`;
const isDirty = () => file?.kind === 'text' && file.text !== file.saved;
export const isFilesDirty = () => isDirty();

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
    return { open: Array.isArray(v.open) ? v.open.filter((p) => typeof p === 'string') : [], file: typeof v.file === 'string' ? v.file : '' };
  } catch {
    return { open: [], file: '' };
  }
}

function persist() {
  if (shownFor !== projectKey()) return;
  storageSet(`${STATE_KEY}:${shownFor}`, JSON.stringify({ open: [...expanded].slice(0, MAX_REMEMBERED_DIRS), file: file && !file.virtual ? file.path : '' }));
}

function resetProject(key) {
  shownFor = key;
  tree = new Map();
  expanded = new Set();
  file = null;
  banner = null;
  prompt = null;
  query = '';
  results = null;
  cursorPath = '';
  reading = false;
  scm = null;
  scmSel = null;
  scmMsg = '';
  const saved = readSaved();
  saved.open.forEach((p) => expanded.add(p));
  pendingFile = saved.file;
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
  const dot = on && isDirty() ? '<span class="fb-unsaved" title="Unsaved changes"></span>' : '';
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

/** `view: 'diff'` + `diffSide` ('staged' | 'unstaged'): opened from the Source Control tab. */
async function openPath(path, { focus = false, restoring = false, view = 'edit', diffSide = '' } = {}) {
  if (file && file.path === path && !file.error && !file.virtual) {
    syncReading();
    if (focus) focusEditor();
    return;
  }
  if (isDirty()) {
    banner = { kind: 'unsaved', then: () => openPath(path, { focus, view, diffSide }) };
    paintBanner();
    return;
  }
  const mine = { path, name: baseOf(path), loading: true, view, diffSide, hunks: [], peek: null };
  file = mine;
  banner = null;
  paintBanner();
  paintMain();
  persist();
  paintTree();
  reveal(path);
  const seq = ++openSeq;
  const res = await call('read', { path });
  if (file !== mine || seq !== openSeq) return;
  mine.loading = false;
  if (!res.ok) {
    if (restoring) {
      file = null;
      persist();
      paintMain();
      paintTree();
      return;
    }
    mine.error = res.error || 'Could not open this file';
    mine.code = res.code || '';
    paintMain();
    return;
  }
  applyRead(mine, res);
  paintMain();
  paintTree();
  loadBase(mine);
  if (focus) focusEditor();
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
  if (!file) {
    el.innerHTML = `<div class="gx-pick">${icon('folder-open')}<span>Pick a file to view or edit it</span><small>${MOD} S saves · esc closes</small></div>`;
    paintFoot();
    return;
  }
  el.innerHTML = '<header class="fb-head" id="files-head"></header><div class="fb-view" id="files-view"></div><footer class="fb-status" id="files-status"></footer>';
  paintHead();
  paintView();
  paintStatus();
  syncReading();
  paintFoot();
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
      ${text && file.view === 'edit' ? tool('wrap', 'wrap-text', 'Wrap long lines', { pressed: storageGet(WRAP_KEY, '') === '1' }) : ''}
      ${file.view === 'diff' ? splitToggle() : ''}
      <span class="gx-tsep" aria-hidden="true"></span>
      ${tool('copy', 'copy', 'Copy path', { disabled: file.loading || !!file.error })}
      ${host.openInEditor ? tool('editor', 'external-link', `Open in ${editorName}`, { disabled: busy || file.gone }) : ''}
      ${tool('rename', 'pencil', 'Rename or move', { disabled: busy || file.gone })}
      ${tool('delete', 'trash-2', 'Move to the Trash', { disabled: busy || file.gone })}
    </span>`;
  paintChgChip();
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
      <textarea class="fb-text" spellcheck="false" autocomplete="off" autocapitalize="off" autocorrect="off" wrap="${wrap ? 'soft' : 'off'}"
        aria-label="Editing ${escapeHtml(file.path)}" data-gramm="false" data-1p-ignore data-lpignore="true"></textarea>
    </div>
    <div class="fb-alt" hidden></div>
    <div class="fb-peek" role="dialog" aria-label="Change compared with git" hidden></div>`;
  const ta = el.querySelector('.fb-text');
  ta.value = file.text;
  ta.readOnly = file.writable === false;
  ta.style.tabSize = file.indent === '\t' ? '4' : String(file.indent.length);
  paintGutter(true);
  ta.scrollTop = file.scroll || 0;
  ta.scrollLeft = file.scrollX || 0;
  if (file.sel) ta.setSelectionRange(Math.min(file.sel[0], ta.value.length), Math.min(file.sel[1], ta.value.length));
  ta.addEventListener('input', onEdit);
  ta.addEventListener('keydown', onEditorKey);
  ta.addEventListener('scroll', () => {
    const g = el.querySelector('.fb-gutter');
    if (g) g.scrollTop = ta.scrollTop;
    syncMarks();
    positionPeek();
  }, { passive: true });
  ta.addEventListener('esc-cancel', () => {
    closePeek();
    ta.focus({ preventScroll: true });
  });
  ['keyup', 'click', 'select', 'focus'].forEach((t) => ta.addEventListener(t, scheduleCaret));
  file.peek = null;
  recomputeChanges();
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

function saveViewState() {
  const ta = mainEl()?.querySelector('.fb-text');
  if (!ta || !file || file.kind !== 'text') return;
  file.scroll = ta.scrollTop;
  file.scrollX = ta.scrollLeft;
  file.sel = [ta.selectionStart, ta.selectionEnd];
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
  g.scrollTop = ta.scrollTop;
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
  foot.innerHTML = footer({
    note: pane === 'scm' ? 'Stage with +, then commit — discarded text goes to the Trash' : 'Edits save straight to your project files',
    hints: wide() || reading ? [[`${MOD} S`, 'save'], ['esc', wide() ? 'close' : 'back']] : [['↵', 'open'], ['esc', 'close']],
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
      text = `You have unsaved changes in ${name}.`;
      actions = bannerBtn('b-save', 'Save and continue', 'btn-primary') + bannerBtn('b-discard', 'Discard', 'btn-danger') + bannerBtn('b-keep', 'Keep editing');
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
    const lose = file && (file.path === p.path || file.path.startsWith(`${p.path}/`)) && isDirty() ? ' Your unsaved edits will be lost.' : '';
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
    if (file && (file.path === p.path || file.path.startsWith(`${p.path}/`))) {
      file = null;
      banner = null;
      paintBanner();
      persist();
      paintMain();
      if (reading) leaveFile();
    }
    expanded.delete(p.path);
    showToast(`Moved ${baseOf(p.path)} to the Trash`);
  } else if (p.kind === 'rename') {
    if (file && (file.path === p.path || file.path.startsWith(`${p.path}/`))) {
      file.path = res.path + file.path.slice(p.path.length);
      file.name = baseOf(file.path);
      file.diff = null;
      persist();
      paintHead();
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
    recomputeChanges();
    return;
  }
  const res = await call('base', { path: doc.path });
  if (file !== doc) return;
  doc.baseLines = res.ok && typeof res.base === 'string' ? res.base.split('\n') : null;
  recomputeChanges();
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
  editorEl()?.removeAttribute('data-esc-cancels');
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
  const tab = (id, ic, label, extra = '') => `<button type="button" class="fb-tab" role="tab" data-act="pane" data-pane="${id}" aria-selected="${pane === id}" title="${label}">${icon(ic)}<span>${label}</span>${extra}</button>`;
  el.innerHTML = tab('files', 'folder-open', 'Explorer')
    + tab('scm', 'git-branch', 'Source Control', n ? `<b class="fb-tab-n" title="${n} changed file${n === 1 ? '' : 's'}">${n > 999 ? '999+' : n}</b>` : '');
}

/** The left pane's top: the name search (Explorer) or the commit box (Source Control). */
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
  if (isDirty()) {
    showToast(`Save or revert your edits to ${file.name} first — a stash only takes what is on disk`, true);
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
  storageSet(PANE_KEY, pane);
  prompt = null;
  $(`${ID}-bar`).innerHTML = '';
  paintTabs();
  paintBar();
  paintPrompt();
  paintTree();
  paintFoot();
  if (pane === 'scm' && !scm) loadScm();
  requestAnimationFrame(() => (pane === 'scm' ? $(`${ID}-msg`) : $(`${ID}-search`))?.focus({ preventScroll: true }));
}

/** A change row: its diff on the right — in the editor's Diff view when the file
 * is here to edit, else (deleted, outside this folder) a read-only diff. */
function openChange(group, path, { focus = false } = {}) {
  const r = scmRow(group, path);
  if (!r) return;
  const diffSide = group === 'staged' ? 'staged' : 'unstaged';
  const rel = relOf(path);
  if (isDirty() && (!file || file.path !== rel)) {
    banner = { kind: 'unsaved', then: () => openChange(group, path, { focus }) };
    paintBanner();
    return;
  }
  scmSel = { group, path };
  paintScm();
  if (rel === null || r.status === 'deleted') {
    file = {
      path: rel ?? path, repoPath: path, name: baseOf(path), kind: 'change', view: 'diff', diffSide, virtual: true,
      git: r.status === 'deleted' ? 'D' : '', outside: rel === null, hunks: [], peek: null, repo: true, prefix: '',
    };
    banner = null;
    paintBanner();
    paintMain();
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
  if (isDirty()) {
    showToast(`Save ${file.name} first — unsaved edits aren’t part of a commit`, true);
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

function onEditorKey(e) {
  if (e.isComposing || !file || e.target.readOnly) return;
  const mod = e.metaKey || e.ctrlKey;
  if (e.code === 'F3' && e.altKey && !mod) {
    e.preventDefault();
    gotoChange(e.shiftKey ? -1 : 1);
  } else if (e.key === 'Tab' && !mod && !e.altKey) {
    e.preventDefault();
    indentSelection(e.target, e.shiftKey);
  } else if (e.key === 'Enter' && !mod && !e.altKey && !e.shiftKey) {
    if (autoIndent(e.target)) e.preventDefault();
  }
}

// ─── Events ───────────────────────────────────────────────────────────────

function rowsInList() {
  const sel = pane === 'scm' ? '.fb-scm-main' : results ? '.gx-row' : '.fb-row';
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
      file = null;
      banner = null;
      persist();
      paintBanner();
      paintMain();
      paintTree();
      if (reading) leaveFile();
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
    case 'reload':
      tree.delete('');
      loadAll();
      break;
    case 'hit':
      openHit(Number(b.dataset.i));
      break;
    case 'retry':
      if (file) {
        const path = file.path;
        file = null;
        openPath(path);
      }
      break;
    case 'mode':
      setMode(b.dataset.mode);
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
      setPane(b.dataset.pane === 'scm' ? 'scm' : 'files');
      break;
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

function onKey(e) {
  const t = e.target;
  if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 's') {
    e.preventDefault();
    save();
    return;
  }
  if (scmMenuOpen && (t.closest?.(`#${ID}-menu`) || t.id === `${ID}-more`)) {
    if (t.id === `${ID}-more` && e.key === 'ArrowDown') {
      e.preventDefault();
      $(`${ID}-menu`)?.querySelector('.gc-item:not(:disabled)')?.focus({ preventScroll: true });
    } else menuKey(e);
    return;
  }
  if (t.id === `${ID}-msg`) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !e.isComposing) {
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
  const search = $(`${ID}-search`);
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

function onInput(e) {
  if (e.target.id === `${ID}-search`) setQuery(e.target.value);
  else if (e.target.id === `${ID}-msg`) {
    scmMsg = e.target.value;
    sizeMsg();
  }
}

function toolsHtml() {
  const t = (act, ic, label) => `<button type="button" class="tool-icon" data-act="${act}" title="${label}" aria-label="${label}">${icon(ic)}</button>`;
  return `<span class="search-extra fb-tools">${t('new-file', 'plus', 'New file')}${t('new-folder', 'folder-plus', 'New folder')}${t('refresh', 'refresh-cw', 'Refresh')}</span>`;
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

/** The drawer. `path` opens that file (a project-relative path) right away;
 * `pane: 'scm'` starts on the Source Control tab, `'files'` on the Explorer. */
export function openFiles({ path = '', pane: want = '' } = {}) {
  const key = projectKey();
  if (shownFor !== key) resetProject(key);
  if (want === 'scm' || want === 'files') {
    pane = want;
    storageSet(PANE_KEY, pane);
  }
  query = '';
  results = null;
  reading = false;
  setHomeSub(ID, store.session?.cwd_display || store.session?.cwd || 'This project');
  paintAll();
  openModal(ID, {
    focus: pane === 'scm' ? `${ID}-msg` : `${ID}-search`,
    onClose: () => {
      saveViewState();
      closeScmMenu();
      reading = false;
      cardEl()?.classList.remove('is-reading');
      if (isDirty()) showToast(`Unsaved changes in ${file.name} are kept — reopen Files to continue`);
    },
  });
  loadAll().then(() => {
    const restore = pendingFile;
    pendingFile = '';
    if (path) openPath(path, { focus: true });
    else if (!file && restore && wide()) openPath(restore, { restoring: true });
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
  const el = $(ID);
  if (!el) return;
  el.addEventListener('click', onClick);
  el.addEventListener('keydown', onKey);
  el.addEventListener('input', onInput);
  $(`${CONFIRM_ID}-ok`)?.addEventListener('click', onConfirmOk);
  // A click anywhere else puts the commit button's menu away.
  document.addEventListener('pointerdown', (e) => {
    if (scmMenuOpen && !e.target.closest?.(`#${ID}-menu, #${ID}-more`)) closeScmMenu();
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
    if (!isDirty()) return;
    e.preventDefault();
    e.returnValue = '';
  });
  // A turn just ended: Jarvis may have added, changed or removed files.
  subscribe((s) => {
    if (wasBusy && !s.busy && isModalOpen(ID)) {
      refreshTree();
      refreshBase();
      checkDisk();
    }
    wasBusy = !!s.busy;
  });
  schedulePoll();
}
