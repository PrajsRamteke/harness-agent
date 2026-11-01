/** Git changes drawer (the git card's Changes row): what differs, file by file —
 * commits a pull would bring in ("Incoming", upstream vs the merge base) and
 * your uncommitted work ("Uncommitted", vs HEAD, new files included).
 *
 * Slides in from the right. Wide screens: files on the left, the selected
 * file's diff on the right (the first file opens by itself; ↑ ↓ / ← → step
 * through files). Narrow screens / phones: the list, and a file opens as a
 * sub-view (back button / Esc returns to the list).
 * Server side: git_api.changed_files / file_diff via POST /api/git
 * {op: files | diff}. Diffs are drawn by diffview.js, like the Changes tab. */
import { $, escapeHtml, storageGet, storageSet } from './utils.js';
import { icon } from './icons.js';
import { api } from './api.js';
import { store } from './store.js';
import { openModal, isModalOpen } from './modal.js';
import { setView, setHomeSub, toolbar, section, footer, empty } from './dialog.js';
import { renderDiff, gutterWidth } from './diffview.js';

const ID = 'gitchanges';
const VIEW_KEY = 'jarvis-gitdiff-view';
const WRAP_KEY = 'jarvis-gitdiff-wrap';
const COMMITS_SHOWN = 4;
const WIDE = '(min-width: 900px)';

let data = null;          // last {op: files} answer
let loading = false;
let query = '';
let file = null;          // { side, path } — the file whose diff is shown
let diff = null;          // its answer
let reading = false;      // narrow screens: the diff sub-view is open
let listSeq = 0;
let diffSeq = 0;
let showAllCommits = false;
let onPull = null;        // git.js's pull (the same one the card runs)
let focusTimer = null;
const cache = new Map();  // side|path → diff answer, until the list reloads

const num = (n) => Number(n) || 0;
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const wide = () => window.matchMedia(WIDE).matches;
const listEl = () => $(`${ID}-list`);
const mainEl = () => $(`${ID}-main`);
const cardEl = () => $(ID)?.querySelector('.modal-card');
const keyOf = (side, path) => `${side}|${path}`;
const STATUS = {
  modified: ['M', 'Modified'],
  added: ['A', 'Added'],
  untracked: ['U', 'New file'],
  deleted: ['D', 'Deleted'],
  renamed: ['R', 'Renamed'],
  copied: ['C', 'Copied'],
  conflict: ['!', 'Conflict'],
};

function splitPath(p) {
  const i = p.lastIndexOf('/');
  return i < 0 ? ['', p] : [p.slice(0, i + 1), p.slice(i + 1)];
}

function numsHtml(r) {
  if (r.binary) return '<em>binary</em>';
  return `${num(r.added) ? `<b class="num-add">+${num(r.added)}</b>` : ''}${num(r.removed) ? `<b class="num-del">−${num(r.removed)}</b>` : ''}`;
}

// ─── Data ─────────────────────────────────────────────────────────────────

async function load() {
  const mine = ++listSeq;
  loading = true;
  paintList();
  let res;
  try {
    res = await api('/api/git', 'POST', { op: 'files' });
  } catch (err) {
    res = { ok: false, error: err?.message || 'Could not read the changes' };
  }
  if (mine !== listSeq) return;
  loading = false;
  if (!res.ok && /unknown git op/.test(res.error || '')) {
    res.error = 'Restart Jarvis to finish updating — it still runs older git code without this view';
  }
  data = res;
  cache.clear();
  if (!isModalOpen(ID)) return;
  paintHead();
  paintList();
  const files = allFiles();
  const still = file && files.some((f) => f.side === file.side && f.row.path === file.path);
  if (still) openFile(file.side, file.path, { quiet: true });
  else if (wide() && files.length) openFile(files[0].side, files[0].row.path);
  else {
    file = null;
    diff = null;
    if (reading) backToList();
    paintMain();
  }
}

/** Every file in list order: [{ side, row }]. */
function allFiles() {
  if (!data?.ok) return [];
  const match = (r) => !query || r.path.toLowerCase().includes(query) || (r.old_path || '').toLowerCase().includes(query);
  return [
    ...(data.incoming?.files || []).filter(match).map((row) => ({ side: 'incoming', row })),
    ...(data.local || []).filter(match).map((row) => ({ side: 'local', row })),
  ];
}

// ─── List (left pane) ─────────────────────────────────────────────────────

function paintHead() {
  if (!data?.ok) return;
  const inc = data.incoming;
  const bits = [data.branch || ''];
  if (inc) bits.push(`${plural(num(inc.behind), 'commit')} to pull`);
  bits.push(data.local.length ? plural(data.local.length, 'uncommitted file') : 'no uncommitted changes');
  setHomeSub(ID, bits.filter(Boolean).join(' · '));
}

function fileRow(side, r, i) {
  const [letter, label] = STATUS[r.status] || STATUS.modified;
  const [dir, name] = splitPath(r.path);
  const on = file && file.side === side && file.path === r.path;
  return `
    <button type="button" class="gx-row${on ? ' is-current' : ''}" data-act="file" data-side="${side}" data-i="${i}" aria-current="${on}" title="${escapeHtml(`${label}: ${r.path}${r.old_path ? ` (was ${r.old_path})` : ''}`)}">
      <span class="gx-st is-${escapeHtml(r.status)}" aria-label="${escapeHtml(label)}">${letter}</span>
      <span class="gx-path"><span class="gx-name">${escapeHtml(name)}</span>${dir ? `<span class="gx-dir">${escapeHtml(dir.replace(/\/$/, ''))}</span>` : ''}</span>
      <span class="gx-nums">${numsHtml(r)}</span>
    </button>`;
}

function paintList() {
  const el = listEl();
  if (!el) return;
  if (!$(`${ID}-search`)) {
    $(`${ID}-bar`).innerHTML = toolbar({ id: `${ID}-search`, placeholder: 'Filter files', label: 'Filter changed files', value: query, kbd: false });
  }
  el.classList.toggle('is-loading', loading && !!data);
  if (!data) {
    el.innerHTML = '<div class="gx-loading"><span class="spinner"></span><span>Reading changes…</span></div>';
    paintFoot();
    return;
  }
  if (!data.ok) {
    el.innerHTML = empty('Couldn’t read the changes', escapeHtml(data.error || ''), {
      ic: 'triangle-alert', action: '<button type="button" class="btn" data-act="reload">Try again</button>',
    });
    paintFoot();
    return;
  }
  const files = allFiles();
  const inc = data.incoming;
  const html = [];
  const rows = (side) => files.map((f, i) => (f.side === side ? fileRow(side, f.row, i) : '')).join('');
  if (inc) {
    const n = num(inc.behind);
    html.push(`
      <div class="gx-incoming">
        <div class="gx-inc-head">
          <span class="gx-inc-ic">${icon('cloud-download')}</span>
          <div class="gx-inc-titles"><strong>${plural(n, 'new commit')} to pull</strong><span>from ${escapeHtml(inc.upstream)}</span></div>
          <button type="button" class="btn btn-primary gx-pull" data-act="pull" ${store.busy ? 'disabled title="Jarvis is working — pull when it finishes"' : `title="Pull ${plural(n, 'commit')} from ${escapeHtml(inc.upstream)} (fast-forward only)"`}>${icon('cloud-download')}<span>Pull</span></button>
        </div>
        ${query ? '' : commitsHtml(inc.commits || [])}
      </div>`);
    html.push(section('Incoming', { count: files.filter((f) => f.side === 'incoming').length || '' }));
    html.push(rows('incoming') ? `<div class="gx-files">${rows('incoming')}</div>`
      : `<p class="gx-none">${query ? 'No incoming files match' : 'No file changes'}</p>`);
  }
  if (data.local.length || !inc) {
    html.push(section('Uncommitted', { count: files.filter((f) => f.side === 'local').length || '' }));
    html.push(rows('local') ? `<div class="gx-files">${rows('local')}</div>`
      : `<p class="gx-none">${query ? 'No files match' : 'Working tree clean — nothing to commit'}</p>`);
  }
  if (!inc && !data.local.length) {
    html.length = 0;
    html.push(empty('Nothing changed', data.upstream
      ? `No uncommitted changes, and nothing new on ${escapeHtml(data.upstream)} since the last fetch. The pull button on the branch row checks for new commits.`
      : 'No uncommitted changes in this repository.', { ic: 'circle-check' }));
  }
  el.innerHTML = html.join('');
  paintFoot();
}

function commitsHtml(commits) {
  if (!commits.length) return '';
  const shown = showAllCommits ? commits : commits.slice(0, COMMITS_SHOWN);
  return `<ol class="gx-commits">${shown.map((c) => `
    <li class="gx-commit" title="${escapeHtml(`${c.sha} · ${c.subject}\n${c.author} · ${c.when}`)}">
      <span class="gx-dot" aria-hidden="true"></span>
      <span class="gx-subj">${escapeHtml(c.subject)}</span>
      <span class="gx-meta"><span class="gx-sha">${escapeHtml(c.sha)}</span> · ${escapeHtml(c.author)} · ${escapeHtml(c.when)}</span>
    </li>`).join('')}</ol>
    ${commits.length > shown.length ? `<button type="button" class="gx-more" data-act="all-commits">Show ${plural(commits.length - shown.length, 'more commit')}</button>` : ''}`;
}

function paintFoot() {
  const foot = $(`${ID}-foot`);
  if (!foot) return;
  foot.innerHTML = footer({
    note: 'Read-only — nothing here changes your files',
    hints: wide() ? [['↑↓', 'files'], ['esc', 'close']] : reading ? [['← →', 'files'], ['esc', 'back']] : [['↵', 'open'], ['esc', 'close']],
  });
}

/** Mark the current row without redrawing the list (keeps focus + scroll). */
function markRow() {
  const files = allFiles();
  listEl()?.querySelectorAll('.gx-row').forEach((b) => {
    const f = files[Number(b.dataset.i)];
    const on = !!(f && file && f.side === file.side && f.row.path === file.path);
    b.classList.toggle('is-current', on);
    b.setAttribute('aria-current', String(on));
    if (on) b.scrollIntoView({ block: 'nearest' });
  });
}

// ─── Diff (right pane / sub-view) ─────────────────────────────────────────

async function openFile(side, path, { quiet = false } = {}) {
  file = { side, path };
  const key = keyOf(side, path);
  diff = cache.get(key) || null;
  if (!wide() && !reading) {
    reading = true;
    cardEl()?.classList.add('is-reading');
  }
  markRow();
  paintMain({ keepScroll: quiet });
  if (diff) return;
  const mine = ++diffSeq;
  let res;
  try {
    res = await api('/api/git', 'POST', { op: 'diff', side, path });
  } catch (err) {
    res = { ok: false, error: err?.message || 'Could not read the diff' };
  }
  if (res.ok) cache.set(key, res);
  if (mine !== diffSeq || !file || keyOf(file.side, file.path) !== key) return;
  diff = res;
  paintMain({ keepScroll: quiet });
}

function backToList() {
  reading = false;
  cardEl()?.classList.remove('is-reading');
  setView(ID, null);
  paintFoot();
  requestAnimationFrame(() => {
    const cur = listEl()?.querySelector('.gx-row.is-current');
    (cur || $(`${ID}-search`))?.focus({ preventScroll: true });
  });
}

function step(delta) {
  const files = allFiles();
  if (!files.length) return;
  const i = file ? files.findIndex((f) => f.side === file.side && f.row.path === file.path) : -1;
  const next = files[Math.max(0, Math.min(files.length - 1, i + delta))];
  if (next && (i < 0 || next !== files[i])) openFile(next.side, next.row.path);
}

function paintMain({ keepScroll = false } = {}) {
  const el = mainEl();
  if (!el) return;
  if (!file) {
    el.innerHTML = data?.ok && allFiles().length
      ? `<div class="gx-pick">${icon('file-diff')}<span>Pick a file to see what changed</span></div>`
      : '';
    return;
  }
  const files = allFiles();
  const i = files.findIndex((f) => f.side === file.side && f.row.path === file.path);
  const row = files[i]?.row || diff || { path: file.path, status: 'modified' };
  const [letter, label] = STATUS[row.status] || STATUS.modified;
  const [dir, name] = splitPath(file.path);
  const where = file.side === 'incoming' ? `incoming from ${data?.incoming?.upstream || 'the remote'}` : 'uncommitted';
  if (reading) {
    setView(ID, { title: name, sub: [label.toLowerCase(), where].join(' · '), back: backToList, backLabel: 'the files' });
  }
  const view = storageGet(VIEW_KEY, 'unified');
  const wrap = storageGet(WRAP_KEY, '') === '1';
  let content;
  if (!diff) {
    content = '<div class="dv-skel"><i></i><i></i><i></i><i></i></div>';
  } else if (!diff.ok) {
    content = empty(diff.code === 'gone' ? 'No changes any more' : 'Couldn’t read the diff', escapeHtml(diff.error || ''), { ic: 'file-x' });
  } else if (diff.binary) {
    content = empty(diff.too_large ? 'Too large or binary to show' : 'Binary file', 'Open it in your editor to see it.', { ic: 'file' });
  } else if (!diff.hunks?.length) {
    content = empty(row.status === 'renamed' ? 'Renamed, no edits' : 'No line changes',
      row.old_path ? `Moved from ${escapeHtml(row.old_path)}` : 'Only the file mode or whitespace at the end changed.', { ic: 'file-diff' });
  } else {
    const { html } = renderDiff(diff.hunks, { view });
    content = `<div class="dv gx-dv${wrap || view === 'split' ? ' is-wrap' : ''}" data-view="${view}" style="--gw:${gutterWidth(diff.hunks)}px">${html}</div>`
      + (diff.hidden ? `<div class="dv-cut">${plural(diff.hidden, 'more changed line')} not shown — very large diffs are capped.</div>` : '');
  }
  const scroller = el.querySelector('.gx-diff');
  const top = keepScroll && scroller ? scroller.scrollTop : 0;
  el.innerHTML = `
    <header class="gx-filehead">
      <span class="gx-st is-${escapeHtml(row.status)}" title="${escapeHtml(label)}">${letter}</span>
      <div class="gx-fh-titles">
        <strong title="${escapeHtml(file.path)}">${escapeHtml(name)}</strong>
        <span>${dir ? `<span class="gx-fh-dir">${escapeHtml(dir.replace(/\/$/, ''))}</span> · ` : ''}${escapeHtml(label.toLowerCase())}${row.old_path && row.old_path !== file.path ? ` from ${escapeHtml(row.old_path)}` : ''} · ${escapeHtml(where)}</span>
      </div>
      <span class="gx-nums">${numsHtml(row)}</span>
      <span class="gx-tools" role="toolbar" aria-label="Diff options">
        <button type="button" class="tool-icon" data-act="wrap" aria-pressed="${wrap}" title="Wrap long lines" aria-label="Wrap long lines">${icon('wrap-text')}</button>
        <button type="button" class="tool-icon gx-only-wide" data-act="split" aria-pressed="${view === 'split'}" title="Side by side" aria-label="Side by side">${icon('columns-2')}</button>
        <span class="gx-tsep" aria-hidden="true"></span>
        <button type="button" class="tool-icon" data-act="prev" ${i <= 0 ? 'disabled' : ''} title="Previous file" aria-label="Previous file">${icon('chevron-up')}</button>
        <span class="gx-pos">${i >= 0 ? `${i + 1}/${files.length}` : ''}</span>
        <button type="button" class="tool-icon" data-act="next" ${i < 0 || i >= files.length - 1 ? 'disabled' : ''} title="Next file" aria-label="Next file">${icon('chevron-down')}</button>
      </span>
    </header>
    <div class="gx-diff">${content}</div>`;
  el.querySelector('.gx-diff').scrollTop = top;
  paintFoot();
}

// ─── Events ───────────────────────────────────────────────────────────────

function onClick(e) {
  const b = e.target.closest('[data-act]');
  if (!b || b.disabled || !$(ID).contains(b)) return;
  const a = b.dataset.act;
  if (a === 'file') {
    const f = allFiles()[Number(b.dataset.i)];
    if (f) openFile(f.side, f.row.path);
  } else if (a === 'pull') {
    b.disabled = true;
    b.innerHTML = '<span class="spinner"></span><span>Pulling…</span>';
    onPull?.();
  } else if (a === 'reload') {
    data = null;
    load();
  } else if (a === 'all-commits') {
    showAllCommits = true;
    paintList();
  } else if (a === 'prev' || a === 'next') {
    step(a === 'prev' ? -1 : 1);
  } else if (a === 'split') {
    storageSet(VIEW_KEY, storageGet(VIEW_KEY, 'unified') === 'split' ? 'unified' : 'split');
    paintMain({ keepScroll: true });
  } else if (a === 'wrap') {
    storageSet(WRAP_KEY, storageGet(WRAP_KEY, '') === '1' ? '' : '1');
    paintMain({ keepScroll: true });
  }
}

function onKey(e) {
  const inField = e.target.matches?.('input, textarea');
  const search = $(`${ID}-search`);
  const vertical = e.key === 'ArrowDown' || e.key === 'ArrowUp';
  const horizontal = e.key === 'ArrowLeft' || e.key === 'ArrowRight';
  if (reading) {
    if (horizontal && !inField) {
      e.preventDefault();
      step(e.key === 'ArrowLeft' ? -1 : 1);
    }
    return;
  }
  if (e.key === 'Enter' && e.target === search) {
    e.preventDefault();
    listEl()?.querySelector('.gx-row')?.click();
    return;
  }
  // ↑ ↓ move between files (and, on wide screens, show each one as you go).
  const inList = e.target === search || !!e.target.closest?.(`#${ID}-list`);
  if (vertical && inList) {
    const rows = [...(listEl()?.querySelectorAll('.gx-row') || [])];
    if (!rows.length) return;
    e.preventDefault();
    const at = rows.indexOf(e.target.closest?.('.gx-row'));
    const cur = at >= 0 ? at : rows.findIndex((r) => r.classList.contains('is-current'));
    const next = rows[Math.max(0, Math.min(rows.length - 1, cur + (e.key === 'ArrowDown' ? 1 : -1)))] || rows[0];
    next.focus({ preventScroll: true });
    next.scrollIntoView({ block: 'nearest' });
    if (wide()) {
      clearTimeout(focusTimer);
      focusTimer = setTimeout(() => next.click(), 90);
    }
  } else if (wide() && !inField && (vertical || horizontal) && !e.target.closest?.('.gx-diff')) {
    e.preventDefault();
    step(e.key === 'ArrowUp' || e.key === 'ArrowLeft' ? -1 : 1);
  }
}

function onInput(e) {
  if (e.target.id !== `${ID}-search`) return;
  query = e.target.value.trim().toLowerCase();
  paintList();
}

/** The git card's Changes row. */
export function openGitChanges() {
  file = null;
  diff = null;
  query = '';
  reading = false;
  showAllCommits = false;
  cardEl()?.classList.remove('is-reading');
  $(`${ID}-bar`).innerHTML = '';
  paintList();
  paintMain();
  openModal(ID, {
    focus: `${ID}-search`,
    onClose: () => {
      file = null;
      diff = null;
      reading = false;
      diffSeq += 1;
      cardEl()?.classList.remove('is-reading');
    },
  });
  load();
}

/** Something changed (pull, commit, stash, a turn ended): reload an open drawer. */
export function refreshGitChanges() {
  if (isModalOpen(ID)) load();
}

export function initGitChanges({ pull } = {}) {
  onPull = pull;
  const el = $(ID);
  if (!el) return;
  el.addEventListener('click', onClick);
  el.addEventListener('keydown', onKey);
  el.addEventListener('input', onInput);
  // Crossing the wide / narrow line while open: switch layouts in place.
  window.matchMedia(WIDE).addEventListener?.('change', () => {
    if (!isModalOpen(ID)) return;
    if (wide() && reading) {
      reading = false;
      cardEl()?.classList.remove('is-reading');
      setView(ID, null);
    }
    if (wide() && !file && allFiles().length) step(1);
    paintMain({ keepScroll: true });
  });
}
