/** Changes tab: every file Jarvis created, edited, renamed or deleted this
 * session, each with its diff — live, as the edits happen.
 *
 * The server keeps the ledger (jarvis/file_changes.py) and always diffs
 * "before Jarvis touched it" against "on disk now", so ten edits to a file
 * read as one change. This module renders it:
 *
 *   list     one card per file, in the order files first changed (newest on
 *            top) — a later edit never reshuffles what you are reading
 *   diff     fetched when a card opens, refreshed when the file changes; rows
 *            that just appeared flash, the rest stays put
 *   follow   on by default: the file Jarvis just changed opens by itself
 *
 * Only live changes animate; a snapshot (page load, reconnect) just appears.
 */
import {
  $, escapeHtml, showToast, copyText, flashDone, storageGet, storageSet,
  countTo, formatCount, animateEl, reducedMotion,
} from './utils.js';
import { icon } from './icons.js';
import { api } from './api.js';
import { store } from './store.js';
import {
  openInspector, isInspectorOpen, activeTab, onInspectorChange,
  setChangeStat, setTabCount, pulseInspectorButton, isRoomy,
} from './inspector.js';
import { renderDiff, rowKeys, gutterWidth } from './diffview.js';

const STATUS = {
  added: { letter: 'A', label: 'Added', verb: 'Created' },
  modified: { letter: 'M', label: 'Modified', verb: 'Edited' },
  deleted: { letter: 'D', label: 'Deleted', verb: 'Deleted' },
  renamed: { letter: 'R', label: 'Renamed', verb: 'Renamed' },
};
const KINDS = ['added', 'modified', 'deleted', 'renamed'];
const STEP_VERB = { create: 'Created', write: 'Rewrote', edit: 'Edited', delete: 'Deleted' };
/** Rows drawn before "Show N more rows". */
const MAX_ROWS = 500;
/** "Expand all" stops here: each open diff is real DOM. */
const MAX_EXPAND_ALL = 12;

const isPhone = () => window.matchMedia('(max-width: 560px)').matches;
/** Side by side only where the panel is wide enough for two columns. */
const effectiveView = () => (S.view === 'split' && isRoomy() ? 'split' : 'unified');

const S = {
  sessionId: undefined,
  files: [],
  totals: { files: 0, added: 0, removed: 0, by_status: {} },
  open: new Set(),          // expanded file ids
  auto: new Set(),          // …of those, opened by "follow" rather than by the user
  full: new Set(),          // diffs shown past MAX_ROWS
  steps: new Set(),         // edit histories unfolded
  detail: new Map(),        // id → { last, data, keys }
  loading: new Map(),       // id → request counter (latest answer wins)
  failed: new Set(),
  filter: 'all',
  follow: storageGet('jarvis-changes-follow', '1') !== '0',
  wrap: storageGet('jarvis-changes-wrap', isPhone() ? '1' : '0') === '1',
  view: storageGet('jarvis-changes-view', 'unified') === 'split' ? 'split' : 'unified',
  touched: false,           // the user opened or closed something themselves
  lastFocus: '',
  unseen: false,            // a change landed while the panel wasn't showing
  lastScroll: 0,
};

/** id → card element */
const cards = new Map();
const ui = {};
let chipTimer = 0;
let filterSig = '';

const fileById = (id) => S.files.find((f) => f.id === id);
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const fmtAdd = (n) => `+${formatCount(n)}`;
const fmtDel = (n) => `−${formatCount(n)}`;

export function timeAgo(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 + (store.skew || 0) - ts));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.floor(h / 24)}d ago`;
}

function splitPath(path) {
  const i = path.lastIndexOf('/');
  return i < 0 ? { base: path, dir: '' } : { base: path.slice(i + 1), dir: path.slice(0, i) };
}

/** Where a card's diff should sit: it can be scrolled out of view by a long list. */
function reveal(card) {
  const sc = ui.scroll;
  if (!card || !sc) return;
  const y = Math.max(0, card.offsetTop - 12);
  if (y < sc.scrollTop || y > sc.scrollTop + sc.clientHeight - 90) {
    sc.scrollTo({ top: y, behavior: reducedMotion() ? 'auto' : 'smooth' });
  }
}

function flash(card) {
  if (!card) return;
  card.classList.remove('is-fresh');
  void card.offsetWidth;
  card.classList.add('is-fresh');
  clearTimeout(card._freshTimer);
  card._freshTimer = setTimeout(() => card.classList.remove('is-fresh'), 2000);
}

const visible = () => isInspectorOpen() && activeTab() === 'changes';

// ─── Summary, filters, toolbar ────────────────────────────────────────────

function renderSummary(animate) {
  const t = S.totals;
  ui.top.hidden = t.files < 1;
  ui.files.textContent = `${plural(t.files, 'file')} changed`;
  countTo(ui.add, t.added, fmtAdd, { animate });
  countTo(ui.del, t.removed, fmtDel, { animate });
  const sum = t.added + t.removed;
  ui.ratio.hidden = !sum;
  const [a, d] = ui.ratio.children;
  a.style.setProperty('--w', `${sum ? (t.added / sum) * 100 : 0}%`);
  d.style.setProperty('--w', `${sum ? (t.removed / sum) * 100 : 0}%`);
}

function renderFilters() {
  const by = S.totals.by_status || {};
  const kinds = KINDS.filter((k) => by[k] > 0);
  const show = S.totals.files >= 2 && kinds.length >= 2;
  if (!show) S.filter = 'all';
  else if (S.filter !== 'all' && !by[S.filter]) S.filter = 'all';
  ui.filters.hidden = !show;
  const sig = show ? `${S.filter}|${kinds.map((k) => `${k}${by[k]}`).join()}|${S.totals.files}` : '';
  if (sig === filterSig) return;
  filterSig = sig;
  if (!show) {
    ui.filters.innerHTML = '';
    return;
  }
  const chips = [['all', 'All', S.totals.files], ...kinds.map((k) => [k, STATUS[k].label, by[k]])];
  ui.filters.innerHTML = chips.map(([k, label, n]) => `
    <button type="button" class="chg-filter" data-f="${k}" aria-pressed="${S.filter === k}">
      ${k === 'all' ? '' : '<i aria-hidden="true"></i>'}<span>${label}</span><b>${n}</b>
    </button>`).join('');
}

function applyFilter({ animate = false } = {}) {
  let n = 0;
  for (const f of S.files) {
    const card = cards.get(f.id);
    if (!card) continue;
    const hide = S.filter !== 'all' && f.status !== S.filter;
    card.classList.toggle('is-filtered', hide);
    if (animate && !hide) {
      animateEl(card, [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 280, delay: Math.min(n, 8) * 28, easing: 'ease-out', fill: 'backwards' });
    }
    if (!hide) n += 1;
  }
}

function syncToolbar() {
  const set = (btn, pressed) => btn?.setAttribute('aria-pressed', String(pressed));
  set(ui.follow, S.follow);
  set(ui.wrap, S.wrap);
  const split = S.view === 'split';
  set(ui.view, split);
  // Two columns can't share one sideways scroll, so they always wrap.
  ui.wrap.disabled = effectiveView() === 'split';
  ui.wrap.title = ui.wrap.disabled ? 'Side by side always wraps' : 'Wrap long lines';
  ui.view.innerHTML = icon(split ? 'rows-2' : 'columns-2');
  ui.view.title = split ? 'Unified view' : 'Side by side';
  ui.view.setAttribute('aria-label', ui.view.title);
  const visibleIds = S.files.filter((f) => S.filter === 'all' || f.status === S.filter).map((f) => f.id);
  const allOpen = visibleIds.length > 0 && visibleIds.every((id) => S.open.has(id));
  ui.fold.innerHTML = icon(allOpen ? 'chevrons-down-up' : 'chevrons-up-down');
  ui.fold.title = allOpen ? 'Collapse all' : 'Expand all';
  ui.fold.setAttribute('aria-label', ui.fold.title);
}

function tickDone(btn) {
  if (btn.classList.contains('is-done')) return;
  btn.classList.add('is-done');
  const prev = btn.innerHTML;
  btn.innerHTML = icon('check');
  clearTimeout(btn._doneTimer);
  btn._doneTimer = setTimeout(() => {
    btn.classList.remove('is-done');
    btn.innerHTML = prev;
  }, 1400);
}

/**
 * Patch text is fetched ahead of the tap: browsers only allow a copy inside the
 * tap itself, and on a phone over plain http a copy after an `await` is refused.
 * '' is "everything"; otherwise a file id.
 */
const patches = new Map();    // key → { sig, text }
const patchTimers = new Map();
const patchSig = (id) => (id ? String(fileById(id)?.last ?? '') : S.files.map((f) => `${f.id}:${f.last}`).join());

async function fetchPatch(id) {
  const sig = patchSig(id);
  const { patch } = await api(`/api/changes/patch${id ? `?id=${encodeURIComponent(id)}` : ''}`);
  patches.set(id, { sig, text: patch || '' });
  return patch || '';
}

function prefetchPatch(id = '') {
  clearTimeout(patchTimers.get(id));
  patchTimers.set(id, setTimeout(() => fetchPatch(id).catch(() => {}), 350));
}

async function copyPatch(id, btn) {
  try {
    const hit = patches.get(id);
    // Cached: copyText starts inside the tap. Not yet: fetch first (best effort).
    const text = hit && hit.sig === patchSig(id) ? hit.text : await fetchPatch(id);
    if (!text) {
      showToast('Nothing to copy yet', true);
      return false;
    }
    if (await copyText(text)) {
      if (btn?.classList.contains('tool-icon')) tickDone(btn);
      else if (btn) flashDone(btn);
      showToast(id ? 'Diff copied' : 'Patch copied — apply it with git apply');
      return true;
    }
    showToast('Copy failed', true);
  } catch {
    showToast('Could not build the patch', true);
  }
  return false;
}

// ─── Cards ────────────────────────────────────────────────────────────────

function createCard(f) {
  const card = document.createElement('article');
  card.className = 'chg-file';
  card.dataset.id = f.id;
  card.setAttribute('role', 'listitem');
  card.innerHTML = `
    <button type="button" class="chg-head" aria-expanded="false">
      <span class="chg-badge"></span>
      <span class="chg-name"><span class="chg-base"></span><span class="chg-dir"></span></span>
      <span class="chg-nums"><b class="num-add"></b><b class="num-del"></b></span>
      <span class="chg-chev">${icon('chevron-right')}</span>
    </button>
    <div class="fold"><div class="fold-inner"><div class="chg-body"></div></div></div>`;
  card.querySelector('.chg-head').addEventListener('click', () => setOpenState(f.id, !S.open.has(f.id), { user: true }));
  return card;
}

function updateCard(card, f, { animate, changed }) {
  card.dataset.status = f.status;
  const st = STATUS[f.status] || STATUS.modified;
  const badge = card.querySelector('.chg-badge');
  badge.textContent = st.letter;
  badge.title = st.label;
  const { base, dir } = splitPath(f.path);
  card.querySelector('.chg-base').textContent = base;
  card.querySelector('.chg-dir').textContent = f.from ? `← ${f.from}` : dir;
  const head = card.querySelector('.chg-head');
  head.title = f.from ? `${f.from} → ${f.path}` : f.path;
  head.setAttribute('aria-label', `${st.label}: ${f.path}, ${f.added} added, ${f.removed} removed`);
  const nums = card.querySelector('.chg-nums');
  nums.hidden = !f.added && !f.removed;
  countTo(nums.querySelector('.num-add'), f.added, fmtAdd, { animate });
  countTo(nums.querySelector('.num-del'), f.removed, fmtDel, { animate });
  if (changed) flash(card);
  refreshMeta(card, f);
}

function refreshMeta(card, f) {
  const when = card.querySelector('.chg-when');
  if (when) when.textContent = `${(STATUS[f.status] || STATUS.modified).verb} ${timeAgo(f.last)}`;
}

function ensureBody(card, f) {
  if (card._built) return;
  card._built = true;
  const body = card.querySelector('.chg-body');
  body.innerHTML = `
    <div class="chg-meta">
      <span class="chg-when"></span>
      <span class="grow"></span>
      <button type="button" class="act-btn" data-act="path" title="Copy the path">${icon('copy')}<span>Path</span></button>
      <button type="button" class="act-btn" data-act="diff" title="Copy this file's diff as a patch">${icon('copy')}<span>Diff</span></button>
    </div>
    <div class="chg-diff"><div class="dv-skel"><i></i><i></i><i></i><i></i></div></div>
    <div class="chg-steps"></div>`;
  body.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const id = card.dataset.id;
    const file = fileById(id);
    switch (btn.dataset.act) {
      case 'path':
        copyText(file?.path || '').then((ok) => ok && flashDone(btn));
        break;
      case 'diff':
        copyPatch(id, btn);
        break;
      case 'steps':
        if (S.steps.has(id)) S.steps.delete(id); else S.steps.add(id);
        paintSteps(id);
        break;
      case 'more':
        S.full.add(id);
        paintDiff(id);
        break;
      case 'retry':
        loadDetail(id, { force: true });
        break;
      default:
    }
  });
  refreshMeta(card, f);
}

function removeCard(id, card, animate) {
  cards.delete(id);
  if (!animate || reducedMotion()) {
    card.remove();
    return;
  }
  card.classList.add('is-gone');
  setTimeout(() => card.remove(), 320);
}

function setOpenState(id, open, { user = false } = {}) {
  const card = cards.get(id);
  if (!card) return;
  if (open) S.open.add(id);
  else {
    S.open.delete(id);
    S.auto.delete(id);
  }
  if (user) {
    S.touched = true;
    S.auto.delete(id);
  }
  card.classList.toggle('is-open', open);
  card.querySelector('.chg-head').setAttribute('aria-expanded', String(open));
  if (open) {
    const f = fileById(id);
    if (f) ensureBody(card, f);
    loadDetail(id);
    prefetchPatch(id);
  }
  syncToolbar();
}

// ─── Diffs ────────────────────────────────────────────────────────────────

function cacheDetail(data) {
  if (!data?.id) return;
  const prev = S.detail.get(data.id);
  // Rows of the version being replaced: what "new" means for the flash.
  S.detail.set(data.id, { last: data.last, data, keys: prev ? rowKeys(prev.data.hunks) : null });
  S.failed.delete(data.id);
}

async function loadDetail(id, { force = false } = {}) {
  const f = fileById(id);
  if (!f) return;
  const cached = S.detail.get(id);
  if (cached && cached.last === f.last && !force) {
    paintDiff(id);
    return;
  }
  const token = (S.loading.get(id) || 0) + 1;
  S.loading.set(id, token);
  if (force) {
    S.failed.delete(id);
    paintDiff(id);
  }
  try {
    const data = await api(`/api/changes/file?id=${encodeURIComponent(id)}`);
    if (S.loading.get(id) !== token) return;
    cacheDetail(data);
    paintDiff(id);
    prefetchPatch(id);
  } catch (err) {
    if (S.loading.get(id) !== token) return;
    if (err?.status === 404) {
      // Reverted since the list was built: ask for the list again.
      api('/api/changes').then((payload) => setFiles(payload, { live: true })).catch(() => {});
      return;
    }
    S.failed.add(id);
    paintDiff(id);
  }
}

function note(html) {
  return `<p class="chg-note">${html}</p>`;
}

function paintDiff(id) {
  const card = cards.get(id);
  const f = fileById(id);
  if (!card?._built || !f) return;
  const box = card.querySelector('.chg-diff');
  const cached = S.detail.get(id);

  if (S.failed.has(id)) {
    box.innerHTML = note('Couldn’t load this diff.<button type="button" class="link-btn chg-retry" data-act="retry">Retry</button>');
    return;
  }
  if (!cached) {
    box.innerHTML = '<div class="dv-skel"><i></i><i></i><i></i><i></i></div>';
    return;
  }
  const d = cached.data;
  card._painted = cached.last;

  if (d.big) {
    box.innerHTML = note('This file is too large to show a diff here.');
  } else if (!d.hunks.length) {
    box.innerHTML = note(f.status === 'renamed' ? 'Renamed without changing its contents.'
      : f.status === 'added' ? 'An empty file.' : 'No line changes to show.');
  } else {
    const old = box.querySelector('.dv');
    const scroll = old ? [old.scrollTop, old.scrollLeft] : null;
    const view = effectiveView();
    const { html, shown, total } = renderDiff(d.hunks, {
      view,
      keys: cached.keys,
      limit: S.full.has(id) ? Infinity : MAX_ROWS,
    });
    const updating = !!cached.keys;
    cached.keys = null;                       // flash once, not on every repaint
    box.innerHTML = `<div class="dv${S.wrap || view === 'split' ? ' is-wrap' : ''}" data-view="${view}" style="--gw:${gutterWidth(d.hunks)}px">${html}</div>`
      + (shown < total ? `<button type="button" class="dv-more" data-act="more">Show ${plural(total - shown, 'more row')}</button>` : '')
      + (d.hidden ? `<div class="dv-cut">${plural(d.hidden, 'more changed line')} not shown — very large diffs are capped.</div>` : '');
    const dv = box.querySelector('.dv');
    if (scroll) {
      dv.scrollTop = scroll[0];
      dv.scrollLeft = scroll[1];
    }
    // Following a live edit: bring the rows that just changed into view.
    const fresh = updating && S.follow && box.querySelector('.is-live');
    if (fresh && visible() && Date.now() - S.lastScroll > 3500) {
      const y = fresh.getBoundingClientRect().top - dv.getBoundingClientRect().top + dv.scrollTop;
      dv.scrollTo({ top: Math.max(0, y - dv.clientHeight / 3), behavior: reducedMotion() ? 'auto' : 'smooth' });
    }
  }
  paintSteps(id);
}

function paintSteps(id) {
  const card = cards.get(id);
  const box = card?.querySelector('.chg-steps');
  const steps = S.detail.get(id)?.data.steps || [];
  if (!box) return;
  if (steps.length < 2) {
    box.innerHTML = '';
    return;
  }
  const open = S.steps.has(id);
  box.classList.toggle('is-open', open);
  const time = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  box.innerHTML = `
    <button type="button" class="chg-steps-head" data-act="steps" aria-expanded="${open}">${icon('chevron-right')}<span>Edit history · ${steps.length}</span></button>
    <div class="fold"><div class="fold-inner"><ul class="chg-step-list">${[...steps].reverse().map((s) => `
      <li class="chg-step"><span>${STEP_VERB[s.action] || 'Changed'}</span><span class="grow"></span>
        ${s.added || s.removed ? `<b><span class="num-add">+${s.added}</span> <span class="num-del">−${s.removed}</span></b>` : ''}
        <time>${escapeHtml(time(s.ts))}</time></li>`).join('')}</ul></div></div>`;
}

function repaintOpen() {
  for (const id of S.open) paintDiff(id);
}

// ─── The list ─────────────────────────────────────────────────────────────

/** Order files first changed, newest on top: an edit never moves a card. */
function normalize(files) {
  return [...files].sort((a, b) => b.first - a.first || a.path.localeCompare(b.path));
}

function resetSession(sid) {
  S.sessionId = sid;
  S.files = [];
  S.totals = { files: 0, added: 0, removed: 0, by_status: {} };
  for (const set of [S.open, S.auto, S.full, S.steps, S.failed]) set.clear();
  S.detail.clear();
  S.loading.clear();
  S.touched = false;
  S.lastFocus = '';
  S.filter = 'all';
  filterSig = '';
  cards.clear();
  if (ui.list) ui.list.innerHTML = '';
}

function setFiles(payload, { live = false } = {}) {
  const files = normalize(payload?.files || []);
  if (payload?.now) store.skew = payload.now - Date.now() / 1000;
  const before = new Map(S.files.map((f) => [f.id, f]));
  const ids = new Set(files.map((f) => f.id));
  S.files = files;
  S.totals = payload?.totals || { files: files.length, added: 0, removed: 0, by_status: {} };

  for (const [id, card] of [...cards]) if (!ids.has(id)) removeCard(id, card, live);
  for (const id of [...S.detail.keys()]) if (!ids.has(id)) S.detail.delete(id);
  for (const set of [S.open, S.auto, S.full, S.steps]) for (const id of [...set]) if (!ids.has(id)) set.delete(id);

  // Reconcile in order without touching cards that are already in place.
  let cursor = ui.list.firstElementChild;
  for (const f of files) {
    let card = cards.get(f.id);
    const isNew = !card;
    if (isNew) {
      card = createCard(f);
      cards.set(f.id, card);
      if (live) {
        card.classList.add('is-new');
        setTimeout(() => card.classList.remove('is-new'), 700);
      }
    }
    while (cursor && cursor.classList.contains('is-gone')) cursor = cursor.nextElementSibling;
    if (cursor === card) cursor = cursor.nextElementSibling;
    else ui.list.insertBefore(card, cursor);
    const prev = before.get(f.id);
    updateCard(card, f, { animate: live, changed: live && (!prev || prev.last !== f.last) });
  }

  for (const id of [...patches.keys()]) if (id && !ids.has(id)) patches.delete(id);
  if (visible()) prefetchPatch('');
  renderSummary(live);
  renderFilters();
  applyFilter();
  setChangeStat(S.totals, { animate: live });
  setTabCount('changes', S.totals.files);

  // First look at a session: the newest file is already open.
  if (!live && S.follow && !S.touched && !S.open.size && files.length) {
    setOpenState(files[0].id, true);
    S.auto.add(files[0].id);
  }
  // Open diffs catch up with their file.
  for (const id of S.open) {
    const f = fileById(id);
    const cached = S.detail.get(id);
    const card = cards.get(id);
    if (!f || !card) continue;
    if (!cached || cached.last !== f.last) loadDetail(id);
    else if (card._painted !== cached.last) paintDiff(id);
  }
  syncToolbar();
}

// ─── Live changes ─────────────────────────────────────────────────────────

/** Follow the newest change: it opens, the one we opened before folds away. */
function follow(id) {
  const card = cards.get(id);
  if (!S.follow || !card) return;
  for (const other of [...S.auto]) if (other !== id) setOpenState(other, false);
  if (!S.open.has(id)) {
    setOpenState(id, true);
    S.auto.add(id);
  }
  if (visible() && Date.now() - S.lastScroll > 3500) requestAnimationFrame(() => reveal(card));
}

function hideChip() {
  const slot = $('changes-chip');
  const chip = slot?.firstElementChild;
  clearTimeout(chipTimer);
  if (!chip) return;
  chip.classList.add('is-leaving');
  chip._leave = setTimeout(() => chip.remove(), 260);
}

/** "3 files changed +18 −4 · Review" above the composer while the panel is shut. */
function showChip() {
  const slot = $('changes-chip');
  if (!slot) return;
  const t = S.totals;
  const chip = slot.firstElementChild || document.createElement('div');
  clearTimeout(chip._leave);
  chip.className = 'dock-chip is-changes';
  chip.setAttribute('role', 'button');
  chip.tabIndex = 0;
  chip.setAttribute('aria-label', `Review ${plural(t.files, 'changed file')}`);
  chip.innerHTML = `${icon('file-diff')}
    <span class="dock-chip-text"><strong>${plural(t.files, 'file')} changed</strong><span class="num-add">${fmtAdd(t.added)}</span><span class="num-del">${fmtDel(t.removed)}</span></span>
    <span class="act-btn" aria-hidden="true">Review</span>`;
  if (!chip.parentElement) {
    chip.addEventListener('click', () => {
      hideChip();
      openInspector('changes');
    });
    chip.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        chip.click();
      }
    });
    slot.appendChild(chip);
  }
  clearTimeout(chipTimer);
  chipTimer = setTimeout(hideChip, 9000);
}

/** SSE `change`: the fresh list plus the diff of the file that just changed. */
export function applyChange(evt) {
  if (!evt?.changes) return;
  if (evt.detail) cacheDetail(evt.detail);
  const focus = evt.focus || '';
  setFiles(evt.changes, { live: true });
  if (!focus || !fileById(focus)) return;
  S.lastFocus = focus;
  follow(focus);
  if (!visible()) {
    S.unseen = true;
    pulseInspectorButton();
  }
  if (!isInspectorOpen()) showChip();
}

/** A full list (snapshot on connect / reconnect / session switch). */
/** Another project is shown: forget this one's files before its snapshot lands. */
export function resetChanges() {
  resetSession(undefined);
}

export function loadChanges(payload, sessionId) {
  if (!payload) return;
  if (sessionId !== undefined && sessionId !== S.sessionId) resetSession(sessionId);
  setFiles(payload, { live: false });
}

/** From the "Panel" button on an inline diff in the transcript. */
export function openChange(id) {
  const f = fileById(id);
  openInspector('changes');
  if (!f) {
    showToast('No changes left in that file');
    return;
  }
  S.touched = true;
  if (S.filter !== 'all' && f.status !== S.filter) {
    S.filter = 'all';
    filterSig = '';
    renderFilters();
    applyFilter();
  }
  setOpenState(id, true, { user: true });
  // After the panel has started to slide in, so the scroll target is laid out.
  setTimeout(() => {
    const card = cards.get(id);
    reveal(card);
    flash(card);
  }, 120);
}

// ─── Wiring ───────────────────────────────────────────────────────────────

export function initChanges() {
  Object.assign(ui, {
    list: $('chg-list'), scroll: $('chg-scroll'), top: $('chg-top'), files: $('chg-files'),
    add: $('chg-add'), del: $('chg-del'), ratio: $('chg-ratio'), filters: $('chg-filters'),
    follow: $('chg-follow'), wrap: $('chg-wrap'), view: $('chg-view'), fold: $('chg-fold'), patch: $('chg-patch'),
  });
  if (!ui.list) return;

  ui.follow.addEventListener('click', () => {
    S.follow = !S.follow;
    storageSet('jarvis-changes-follow', S.follow ? '1' : '0');
    syncToolbar();
    showToast(S.follow ? 'Following: files open as Jarvis changes them' : 'Stopped following');
    if (S.follow && S.lastFocus) follow(S.lastFocus);
  });
  ui.wrap.addEventListener('click', () => {
    S.wrap = !S.wrap;
    storageSet('jarvis-changes-wrap', S.wrap ? '1' : '0');
    syncToolbar();
    repaintOpen();
  });
  ui.view.addEventListener('click', () => {
    S.view = S.view === 'split' ? 'unified' : 'split';
    storageSet('jarvis-changes-view', S.view);
    syncToolbar();
    repaintOpen();
  });
  ui.fold.addEventListener('click', () => {
    const shown = S.files.filter((f) => S.filter === 'all' || f.status === S.filter);
    const allOpen = shown.length > 0 && shown.every((f) => S.open.has(f.id));
    if (allOpen) {
      shown.forEach((f) => setOpenState(f.id, false, { user: true }));
      return;
    }
    shown.slice(0, MAX_EXPAND_ALL).forEach((f) => setOpenState(f.id, true, { user: true }));
    if (shown.length > MAX_EXPAND_ALL) showToast(`Opened the ${MAX_EXPAND_ALL} newest`);
  });
  ui.patch.addEventListener('click', () => copyPatch('', ui.patch));

  ui.filters.addEventListener('click', (e) => {
    const chip = e.target.closest('.chg-filter');
    if (!chip || chip.dataset.f === S.filter) return;
    S.filter = chip.dataset.f;
    filterSig = '';
    renderFilters();
    applyFilter({ animate: true });
    syncToolbar();
  });

  // Up / down move between file headers.
  ui.list.addEventListener('keydown', (e) => {
    const head = e.target.closest?.('.chg-head');
    if (!head || !['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
    const heads = [...ui.list.querySelectorAll('.chg-file:not(.is-filtered):not(.is-gone) > .chg-head')];
    const i = heads.indexOf(head);
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? heads.length - 1 : i + (e.key === 'ArrowDown' ? 1 : -1);
    if (heads[next]) {
      e.preventDefault();
      heads[next].focus();
    }
  });

  const noteScroll = () => { S.lastScroll = Date.now(); };
  ui.scroll.addEventListener('wheel', noteScroll, { passive: true });
  ui.scroll.addEventListener('touchmove', noteScroll, { passive: true });

  let wasRoomy = false;
  onInspectorChange(({ open, tab, roomy }) => {
    if (roomy !== wasRoomy) {
      wasRoomy = roomy;
      if (S.view === 'split') {
        syncToolbar();
        repaintOpen();                            // side by side needs the room
      }
    }
    if (!open || tab !== 'changes') return;
    hideChip();
    prefetchPatch('');
    for (const f of S.files) {
      const card = cards.get(f.id);
      if (card?._built) refreshMeta(card, f);
    }
    // Opened on a phone after Jarvis worked: the newest change is the one you want.
    if (S.follow && !S.touched && !S.open.size && S.files.length) follow(S.files[0].id);
    if (S.unseen && S.lastFocus && cards.has(S.lastFocus)) {
      const card = cards.get(S.lastFocus);
      setTimeout(() => { reveal(card); flash(card); }, 260);   // once the panel has slid in
    }
    S.unseen = false;
  });

  setInterval(() => {
    if (!visible()) return;
    for (const f of S.files) {
      const card = cards.get(f.id);
      if (card?._built) refreshMeta(card, f);
    }
  }, 15000);

  syncToolbar();
  setChangeStat(S.totals, { animate: false });
}
