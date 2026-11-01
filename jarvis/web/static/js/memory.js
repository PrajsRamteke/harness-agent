/** Memory — the web /memory and /lesson: what Jarvis remembers, as a dashboard.
 *
 *   [ ◆ Memory  12 ]  [ 🎓 Lessons  34 ]                  ← tabs
 *   ┌ FACTS ───┐ ┌ ADDED THIS WEEK ┐ ┌ CHARACTERS ┐        ← stat tiles
 *   [🔍 Search ………………………………………]  [+ Add fact]
 *   [All 34] [#git 9] [#css 4] …              Newest | Most used
 *   N  name: Prajwal                          2d ago  ⧉ ✎ 🗑
 *   ──────────────────────────────────────────────────────────────────
 *   ~/.config/harness-agent/memory.json                      Clear
 *
 * Two tabs over two stores: *Memory* (short facts about you — one row each,
 * "key: value" facts get a key tag) and *Lessons* (what the agent learned from
 * past tasks — a card each, with its topics, how often it was used, and a
 * "show more" for long ones). Both: search, sort, add, edit in place (Enter or
 * ⌘↵ saves, Esc cancels), copy, delete — and Undo for a delete or a Clear.
 * Lessons also filter by topic chip. Same files as the terminal's /memory and
 * /lesson (jarvis/web/memory_api.py → storage/memory.py, storage/lessons.py);
 * what the agent saves mid-chat shows up here within a few seconds.
 * Opened by /memory, /lesson, or ⌘K — no sidebar entry.
 */
import { $, escapeHtml, showToast, haptic, copyText, isMac } from './utils.js';
import { icon } from './icons.js';
import { openModal, isModalOpen } from './modal.js';
import { fetchMemory, memoryPost } from './api.js';
import { patch, spin, plural, tildify, seg, mark } from './extui.js';
import { setView, setHomeSub, toolbar, footer, empty } from './dialog.js';

const KIND = { facts: 'fact', lessons: 'lesson' };
const SUBTITLE = {
  facts: 'Facts about you, loaded when Jarvis needs them',
  lessons: 'Lessons from past tasks, looked up when a similar one comes',
};
const FACT_STARTERS = [
  'name: ',
  'prefers TypeScript over JavaScript',
  'works at ',
  'timezone: IST',
];
const SORTS = {
  facts: [{ value: 'new', label: 'Newest' }, { value: 'old', label: 'Oldest' }],
  lessons: [{ value: 'new', label: 'Newest' }, { value: 'used', label: 'Most used' }],
};
const TONES = ['accent', 'indigo', 'amber', 'slate'];
const KEY_VALUE = /^([A-Za-z][\w\/-]{0,23}(?: [\w\/-]{1,23})?):\s+(\S[\s\S]*)$/; // "name: Prajwal" · "home city: Pune" (one or two words)
const LONG_LESSON_CHARS = 200;
const LONG_LESSON_LINES = 3;
const CHIPS_SHOWN = 12;
const WEEK_S = 7 * 24 * 3600;
const POLL_MS = 6000; // the agent saves facts and lessons mid-chat
const UNDO_MS = 9000;
const CLEAR_ARM_MS = 3000;

let data = null; // last /api/memory
let loadError = false;
let tab = 'facts'; // 'facts' | 'lessons'
let query = '';
let tagFilter = '';
const sortBy = { facts: 'new', lessons: 'new' };
let editing = null; // { kind, id (null = new), draft, original, built }
let saving = false;
let flash = null; // { kind, id } to highlight after a change
let undo = null; // { kind, items, label, timer }
let built = false;
let poll = 0;
let clearArmed = 0;
const expanded = new Set(); // lesson ids shown in full

// ─── Data ─────────────────────────────────────────────────────────────────

const bucket = (t = tab) => (t === 'facts' ? data?.memory : data?.lessons);
const itemsOf = (t = tab) => bucket(t)?.items || [];

function setData(next) {
  if (!next?.memory || !next?.lessons) return;
  data = next;
  loadError = false;
}

async function load() {
  try {
    setData(await fetchMemory());
  } catch {
    if (!data) loadError = true;
  }
  if (isModalOpen('memory') && !editing) render();
}

async function op(payload) {
  saving = true;
  render();
  const res = await memoryPost(payload);
  saving = false;
  if (res.data) setData(res.data);
  if (!res.ok) showToast(res.error || 'Could not save that', true);
  render();
  return res;
}

/** Another page changed a fact or lesson. An open editor keeps its draft; the list catches up after. */
export function handleMemoryEvent(next) {
  if (next?.memory && next?.lessons) {
    setData(next);
    if (isModalOpen('memory') && !editing) render();
  } else if (isModalOpen('memory')) {
    load();
  }
}

// ─── Small helpers ────────────────────────────────────────────────────────

function ago(ts) {
  if (!ts) return '';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d ago`;
  const old = s > 300 * 86400;
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...(old ? { year: 'numeric' } : {}) });
}

const dateTitle = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : '');
const short = (s, n = 48) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const splitTags = (s) => [...new Set(String(s || '').split(',').map((t) => t.trim().replace(/^#/, '').toLowerCase()).filter(Boolean))];

function toneOf(s) {
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return TONES[h % TONES.length];
}

function findItem(id, kind = KIND[tab]) {
  return itemsOf(kind === 'fact' ? 'facts' : 'lessons').find((x) => x.id === id) || null;
}

function grow(ta, max = 220) {
  if (!ta) return;
  ta.style.height = 'auto';
  if (ta.scrollHeight) ta.style.height = `${Math.min(ta.scrollHeight + 2, max)}px`;
}

function copyOf(it) {
  if (KIND[tab] === 'fact') return it.text;
  return [it.task, it.lesson, it.tags.map((t) => `#${t}`).join(' ')].filter(Boolean).join('\n');
}

// ─── Filter + sort ────────────────────────────────────────────────────────

function matches(it, q) {
  if (!q) return true;
  const hay = tab === 'facts' ? it.text : `${it.task} ${it.lesson} ${it.tags.join(' ')}`;
  return q.split(/\s+/).every((w) => hay.toLowerCase().includes(w));
}

function visible() {
  const q = query.trim().toLowerCase();
  const keep = (it) => it.id === editing?.id; // the one being edited never drops out of the list
  let list = itemsOf().filter((it) => keep(it) || matches(it, q));
  if (tab === 'lessons' && tagFilter) list = list.filter((it) => keep(it) || it.tags.includes(tagFilter));
  const by = sortBy[tab];
  return list.sort((a, b) => {
    if (by === 'old') return a.id - b.id;
    if (by === 'used') return (b.hits - a.hits) || (b.ts - a.ts) || (b.id - a.id);
    return (b.ts - a.ts) || (b.id - a.id);
  });
}

// ─── Markup: tabs, stats, filters ─────────────────────────────────────────

function tabsHtml() {
  const tabBtn = (id, label, ic, n) => `<button type="button" role="tab" class="mk-tab" data-act="tab" data-val="${id}" aria-selected="${tab === id}" tabindex="${tab === id ? 0 : -1}"${editing ? ' disabled' : ''}>${icon(ic)}<span>${label}</span>${n ? `<em class="mk-count">${n}</em>` : ''}</button>`;
  return `<div class="mk-tabs" role="tablist" aria-label="Memory">
    ${tabBtn('facts', 'Memory', 'database', data?.memory.count)}
    ${tabBtn('lessons', 'Lessons', 'graduation-cap', data?.lessons.count)}
  </div>`;
}

const stat = (label, value, sub = '', extra = '') => `<div class="mem-stat">
  <span class="ms-label">${escapeHtml(label)}</span>
  <strong class="ms-val">${value}</strong>
  ${extra}<span class="ms-sub">${escapeHtml(sub)}</span>
</div>`;

function statsHtml() {
  if (!data) return '';
  if (tab === 'facts') {
    const m = data.memory;
    const newest = m.items.reduce((t, f) => Math.max(t, f.ts), 0);
    const week = m.items.filter((f) => Date.now() / 1000 - f.ts < WEEK_S).length;
    return stat('Facts', m.count, m.count ? `last added ${ago(newest)}` : 'nothing yet')
      + stat('This week', week, week ? 'learned in the last 7 days' : 'nothing new')
      + stat('Characters', m.chars.toLocaleString(), 'across every fact');
  }
  const l = data.lessons;
  const top = [...l.items].sort((a, b) => b.hits - a.hits)[0];
  const pct = l.limit ? Math.min(100, Math.round((l.count / l.limit) * 100)) : 0;
  const bar = `<span class="ms-bar" role="img" aria-label="${pct}% of the ${l.limit} lesson limit"><i style="width:${pct}%"></i></span>`;
  return stat('Lessons', `${l.count}<small>of ${l.limit}</small>`, l.count ? 'least used are dropped at the limit' : 'nothing yet', bar)
    + stat('Times used', l.hits.toLocaleString(), top?.hits ? `most: ${short(top.task, 30)}` : 'not used yet')
    + stat('Topics', l.tags.length, l.tags[0] ? `top: #${l.tags[0][0]}` : 'no tags yet');
}

function chipsHtml() {
  const tags = (data?.lessons.tags || []).slice(0, CHIPS_SHOWN);
  if (tagFilter && !tags.some(([t]) => t === tagFilter)) tagFilter = '';
  if (!tags.length) return '<span class="mem-chips"></span>';
  const all = `<button type="button" class="mem-chip" data-act="tag" data-tag="" aria-pressed="${!tagFilter}">All<em>${data.lessons.count}</em></button>`;
  const rest = tags.map(([t, n]) => `<button type="button" class="mem-chip" data-act="tag" data-tag="${escapeHtml(t)}" aria-pressed="${tagFilter === t}">#${escapeHtml(t)}<em>${n}</em></button>`).join('');
  return `<span class="mem-chips" role="group" aria-label="Filter by topic">${all}${rest}</span>`;
}

function filtersHtml() {
  if (!bucket()?.count) return '';
  return `${tab === 'lessons' ? chipsHtml() : '<span class="mem-chips"></span>'}${seg(SORTS[tab], sortBy[tab], { label: 'Sort', act: 'sort' })}`;
}

// ─── Markup: rows ─────────────────────────────────────────────────────────

const actBtns = (what) => `<span class="mem-acts">
  <button type="button" class="row-btn is-icon" data-act="copy" aria-label="Copy ${what}" title="Copy">${icon('copy')}</button>
  <button type="button" class="row-btn is-icon" data-act="edit" aria-label="Edit ${what}" title="Edit">${icon('pencil')}</button>
  <button type="button" class="row-btn is-icon mem-del" data-act="remove" aria-label="Delete ${what}" title="Delete">${icon('trash-2')}</button>
</span>`;

function factBody(text) {
  const m = KEY_VALUE.exec(text);
  return m
    ? `<span class="mem-key">${escapeHtml(m[1])}</span><span class="mem-val">${escapeHtml(m[2])}</span>`
    : `<span class="mem-val">${escapeHtml(text)}</span>`;
}

function factRow(f, i) {
  if (editing?.kind === 'fact' && editing.id === f.id) return factEditor(f.id);
  const key = KEY_VALUE.exec(f.text)?.[1] || f.text;
  const isNew = flash?.kind === 'fact' && flash.id === f.id;
  return `<li class="mem-item is-fact${isNew ? ' just-added' : ''}" data-id="${f.id}" style="--i:${Math.min(i, 12)}">
    ${mark(key, toneOf(key), { sm: true })}
    <button type="button" class="mem-main" data-act="edit" title="Click to edit">${factBody(f.text)}</button>
    <span class="mem-meta"><time title="${escapeHtml(dateTitle(f.ts))}">${escapeHtml(ago(f.ts))}</time><span class="mem-id">#${f.id}</span></span>
    ${actBtns(`“${escapeHtml(short(f.text, 40))}”`)}
  </li>`;
}

function lessonRow(s, i) {
  if (editing?.kind === 'lesson' && editing.id === s.id) return lessonEditor(s.id);
  const long = s.lesson.length > LONG_LESSON_CHARS || s.lesson.split('\n').length > LONG_LESSON_LINES;
  const open = expanded.has(s.id);
  const isNew = flash?.kind === 'lesson' && flash.id === s.id;
  const tags = s.tags.map((t) => `<button type="button" class="mem-tag${tagFilter === t ? ' is-on' : ''}" data-act="tag" data-tag="${escapeHtml(t)}" title="Show only #${escapeHtml(t)}">#${escapeHtml(t)}</button>`).join('');
  const used = s.hits
    ? `<span class="badge mem-hits" title="Jarvis found this lesson useful ${plural(s.hits, 'time')}">${icon('zap')}${s.hits}×</span>`
    : '';
  return `<li class="mem-item is-lesson${open ? ' is-open' : ''}${isNew ? ' just-added' : ''}" data-id="${s.id}" style="--i:${Math.min(i, 12)}">
    <div class="mem-head">
      ${mark(s.task, toneOf(s.task), { sm: true })}
      <${long ? 'button type="button"' : 'div'} class="mem-main"${long ? ` data-act="toggle" aria-expanded="${open}"` : ''}>
        <span class="mem-task">${escapeHtml(s.task)}</span>
        <span class="mem-lesson">${escapeHtml(s.lesson)}</span>
        ${long ? `<span class="mem-more">${icon(open ? 'chevron-down' : 'chevron-right')}${open ? 'Show less' : 'Show more'}</span>` : ''}
      </${long ? 'button' : 'div'}>
      ${actBtns(`“${escapeHtml(short(s.task, 40))}”`)}
    </div>
    <div class="mem-foot-row">
      <span class="mem-tags">${tags}</span>
      <span class="mem-meta">${used}<time title="${escapeHtml(dateTitle(s.ts))}">${escapeHtml(ago(s.ts))}</time><span class="mem-id">#${s.id}</span></span>
    </div>
  </li>`;
}

// ─── Markup: editors ──────────────────────────────────────────────────────

function editFoot() {
  const key = isMac ? '⌘' : 'Ctrl';
  return `<span class="mem-edit-foot">
    <span class="mem-edit-hint"><kbd>${key}</kbd><kbd>↵</kbd> save · <kbd>esc</kbd> cancel</span>
    <span class="mem-edit-acts">
      <button type="button" class="btn btn-sm btn-quiet" data-act="cancel-edit">Cancel</button>
      <button type="button" class="btn btn-sm btn-primary" data-act="save-edit"${canSave() && !saving ? '' : ' disabled'}>${saving ? spin('Saving') : `${icon('check')}<span>Save</span>`}</button>
    </span>
  </span>`;
}

function factEditor(id) {
  return `<li class="mem-item is-fact is-editing" data-id="${id ?? ''}">
    ${mark(id == null ? '+' : '✎', 'accent', { sm: true })}
    <span class="mem-edit">
      <textarea id="mem-ed-text" class="mem-in" rows="1" maxlength="2000" spellcheck="true"
        placeholder="Something Jarvis should remember, e.g. “name: Prajwal”" aria-label="Fact"></textarea>
      ${editFoot()}
    </span>
  </li>`;
}

function lessonEditor(id) {
  return `<li class="mem-item is-lesson is-editing" data-id="${id ?? ''}">
    <span class="mem-edit">
      <label class="mem-lbl" for="mem-ed-task">Task <small>what was being done</small></label>
      <input id="mem-ed-task" class="mem-in" type="text" maxlength="300" placeholder="e.g. wiring Redux Saga with TypeScript" autocomplete="off" spellcheck="true">
      <label class="mem-lbl" for="mem-ed-lesson">Lesson <small>what to do next time</small></label>
      <textarea id="mem-ed-lesson" class="mem-in" rows="3" maxlength="4000" placeholder="e.g. always wrap dispatch in put()" spellcheck="true"></textarea>
      <label class="mem-lbl" for="mem-ed-tags">Topics <small>comma-separated, optional</small></label>
      <input id="mem-ed-tags" class="mem-in" type="text" placeholder="redux, saga, typescript" autocomplete="off" spellcheck="false" autocapitalize="off">
      ${editFoot()}
    </span>
  </li>`;
}

function canSave() {
  if (!editing) return false;
  const d = editing.draft;
  return editing.kind === 'fact' ? !!d.text.trim() : !!(d.task.trim() && d.lesson.trim());
}

function fillEditor() {
  const d = editing?.draft;
  if (!d) return;
  const set = (id, v) => {
    const el = $(id);
    if (!el || v === undefined) return;
    el.value = v;
    if (el.tagName === 'TEXTAREA') grow(el);
  };
  set('mem-ed-text', d.text);
  set('mem-ed-task', d.task);
  set('mem-ed-lesson', d.lesson);
  set('mem-ed-tags', d.tags);
}

// ─── Markup: list, notices, footer ────────────────────────────────────────

function noticeHtml() {
  if (!undo) return '';
  const n = undo.items.length;
  const what = n > 1 ? `Cleared ${plural(n, undo.kind)}.` : `Deleted “${escapeHtml(undo.label)}”.`;
  return `<p class="pv-msg is-ok pin-undo" role="status">${icon('circle-check')}<span>${what}</span>
    <button type="button" class="btn btn-sm" data-act="undo">${icon('undo-2')}<span>Undo</span></button></p>`;
}

function emptyHtml() {
  if (tab === 'facts') {
    const starters = FACT_STARTERS.map((s, i) => `<button type="button" class="pin-starter" data-act="starter" data-i="${i}" style="--i:${i}">${icon('plus')}<span>${escapeHtml(s.trim())}</span></button>`).join('');
    return `<div class="pin-empty">
      ${empty('Nothing remembered yet', 'Jarvis saves facts about you as you chat: your name, tools, preferences. You can add one yourself.', { ic: 'database' })}
      <div class="pin-starters-label">Start from an example</div>
      <div class="pin-starters">${starters}</div>
    </div>`;
  }
  return empty('No lessons yet', 'Jarvis saves a lesson when it works out something non-obvious, so the next similar task goes faster. You can write one yourself.', {
    ic: 'graduation-cap', action: '<button type="button" class="btn btn-primary" data-act="add">Add a lesson</button>',
  });
}

function listHtml() {
  if (loadError) {
    return empty('Couldn’t load your memory', 'Check that Jarvis is still running.', {
      ic: 'circle-alert', action: '<button type="button" class="btn" data-act="reload">Try again</button>',
    });
  }
  if (!data) return `<div class="pin-loading">${spin('Loading')}</div>`;
  const draftNew = editing && editing.kind === KIND[tab] && editing.id == null
    ? (editing.kind === 'fact' ? factEditor(null) : lessonEditor(null)) : '';
  if (!itemsOf().length && !draftNew) return emptyHtml();
  const rows = visible();
  if (!rows.length && !draftNew) {
    const clear = tagFilter ? '<button type="button" class="btn" data-act="tag" data-tag="">Show all topics</button>' : '';
    return empty('Nothing matches', 'Try a different word.', { ic: 'search', action: clear });
  }
  const row = tab === 'facts' ? factRow : lessonRow;
  return `<ul class="mem-list" aria-label="${tab === 'facts' ? 'Remembered facts' : 'Lessons'}">${draftNew}${rows.map(row).join('')}</ul>`;
}

function footHtml() {
  if (!data) return '';
  const b = bucket();
  const note = `Saved in ${escapeHtml(tildify(b.file))}`;
  const clear = b.count
    ? `<button type="button" class="btn btn-sm btn-quiet mem-clear" data-act="clear"${editing ? ' disabled' : ''}>${icon('trash-2')}<span>Clear all</span></button>`
    : '';
  return `${footer({ note, noteIc: 'file-pen', hints: [] })}${clear}`;
}

// ─── Render ───────────────────────────────────────────────────────────────

function renderBar() {
  const bar = $('memory-bar');
  if (bar.dataset.tab !== tab) {
    bar.dataset.tab = tab;
    bar.innerHTML = `${toolbar({
      id: 'memory-search',
      placeholder: tab === 'facts' ? 'Search what Jarvis remembers' : 'Search lessons, topics or tags',
      value: query,
      kbd: false,
      action: { act: 'add', id: 'memory-add', label: tab === 'facts' ? 'Add fact' : 'Add lesson', ic: 'plus' },
    })}<div class="mem-filters" id="memory-filters"></div>`;
  }
  patch($('memory-filters'), filtersHtml());
  const add = $('memory-add');
  if (add) add.disabled = !!editing || !data;
}

function renderList() {
  const list = $('memory-list');
  if (!list) return;
  // An edit of something deleted elsewhere can't be saved: say so and drop it.
  if (editing?.id != null && data && !findItem(editing.id, editing.kind)) {
    showToast('That item was removed somewhere else', true);
    editing = null;
    setView('memory', null);
  }
  const activeId = list.contains(document.activeElement) ? document.activeElement.id : '';
  const before = list._html;
  patch(list, listHtml());
  if (editing && list._html !== before) {
    fillEditor();
    const again = activeId && $(activeId);
    if (again) {
      again.focus({ preventScroll: true });
      if (again.setSelectionRange && again.value) again.setSelectionRange(again.value.length, again.value.length);
    }
  }
  if (flash) {
    const { kind, id } = flash;
    flash = null;
    requestAnimationFrame(() => list.querySelector(`.is-${kind}[data-id="${id}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
  }
}

function render() {
  if (!$('memory-body') || !$('memory-bar')) return;
  if (!built) {
    $('memory-body').innerHTML = '<div id="memory-notice" class="pin-notice"></div><div id="memory-list"></div>';
    built = true;
  }
  patch($('memory-tabs'), tabsHtml());
  patch($('memory-stats'), statsHtml());
  renderBar();
  patch($('memory-notice'), noticeHtml());
  renderList();
  patch($('memory-foot'), footHtml());
  setHomeSub('memory', SUBTITLE[tab]);
}

// ─── Actions ──────────────────────────────────────────────────────────────

function setTab(next) {
  if (editing || next === tab || !KIND[next]) return;
  tab = next;
  query = '';
  tagFilter = '';
  clearUndo();
  render();
}

function startEdit(kind, id, preset = null) {
  if (editing) { // one edit at a time: go back to it rather than drop its draft
    $(editing.kind === 'fact' ? 'mem-ed-text' : 'mem-ed-task')?.focus({ preventScroll: true });
    return;
  }
  const it = id == null ? null : findItem(id, kind);
  if (id != null && !it) return;
  const draft = kind === 'fact'
    ? { text: preset?.text ?? it?.text ?? '' }
    : { task: it?.task ?? '', lesson: it?.lesson ?? '', tags: it ? it.tags.join(', ') : '' };
  editing = { kind, id, draft, original: JSON.stringify(draft) };
  const label = kind === 'fact' ? 'Memory' : 'Lessons';
  setView('memory', { title: label, sub: id == null ? `New ${kind}` : `Editing ${kind} #${id}`, back: cancelEdit, backLabel: label.toLowerCase() });
  render();
  $('memory-list')?.scrollTo?.({ top: 0 });
  requestAnimationFrame(() => {
    const first = $(kind === 'fact' ? 'mem-ed-text' : 'mem-ed-task');
    first?.focus({ preventScroll: true });
    first?.scrollIntoView?.({ block: 'nearest' });
    if (first?.setSelectionRange && first.value) first.setSelectionRange(first.value.length, first.value.length);
  });
}

function leaveEdit() {
  editing = null;
  setView('memory', null);
  render();
}

function cancelEdit() {
  if (!editing) return;
  const { kind, id } = editing;
  leaveEdit();
  if (id != null) $('memory-list')?.querySelector(`.is-${kind}[data-id="${id}"] .mem-main`)?.focus({ preventScroll: true });
  else $('memory-add')?.focus({ preventScroll: true });
}

async function saveEdit() {
  if (!editing || saving || !canSave()) return;
  const { kind, id, draft, original } = editing;
  if (id != null && JSON.stringify(draft) === original) {
    cancelEdit();
    return;
  }
  const body = kind === 'fact' ? { text: draft.text } : { task: draft.task, lesson: draft.lesson, tags: draft.tags };
  const res = await op({ op: id == null ? 'add' : 'update', kind, id, ...body });
  if (!res.ok) return; // the editor stays open with the draft
  haptic(10);
  flash = { kind, id: res.id };
  clearUndo();
  leaveEdit();
}

function clearUndo() {
  if (undo?.timer) clearTimeout(undo.timer);
  undo = null;
}

function armUndo(kind, items, label, ms = UNDO_MS) {
  clearUndo();
  undo = { kind, items, label };
  undo.timer = setTimeout(() => {
    undo = null;
    if (isModalOpen('memory')) render();
  }, ms);
}

async function removeItem(id) {
  const kind = KIND[tab];
  const it = findItem(id, kind);
  if (!it) return;
  $('memory-list')?.querySelector(`[data-id="${id}"]`)?.classList.add('is-leaving');
  haptic(8);
  const res = await op({ op: 'delete', kind, id });
  if (!res.ok) return;
  expanded.delete(id);
  armUndo(kind, [it], short(it.text || it.task));
  render();
}

async function undoRemove() {
  if (!undo) return;
  const { kind, items } = undo;
  clearUndo();
  const res = await op({ op: 'restore', kind, items });
  if (res.ok) showToast(items.length > 1 ? 'Restored' : 'Restored 1 item');
}

async function clearAll(btn) {
  if (!clearArmed) {
    clearArmed = setTimeout(() => {
      clearArmed = 0;
      if (btn.isConnected) {
        btn.classList.remove('is-asking');
        btn.querySelector('span').textContent = 'Clear all';
      }
    }, CLEAR_ARM_MS);
    btn.classList.add('is-asking');
    btn.querySelector('span').textContent = tab === 'facts' ? 'Forget everything?' : 'Delete all lessons?';
    return;
  }
  clearTimeout(clearArmed);
  clearArmed = 0;
  const kind = KIND[tab];
  const items = [...itemsOf()];
  const res = await op({ op: 'clear', kind });
  if (!res.ok) return;
  expanded.clear();
  armUndo(kind, items, '', UNDO_MS + 3000);
  render();
}

async function copyItem(id) {
  const it = findItem(id);
  if (it && await copyText(copyOf(it))) showToast('Copied');
}

function toggleOpen(id) {
  if (expanded.has(id)) expanded.delete(id);
  else expanded.add(id);
  renderList();
}

// ─── Wiring ───────────────────────────────────────────────────────────────

function onClick(e) {
  const el = e.target.closest('[data-act]');
  const card = $('memory');
  if (!el || !card?.contains(el) || el.disabled) return;
  const id = Number(el.closest('[data-id]')?.dataset.id || 0);
  switch (el.dataset.act) {
    case 'tab': setTab(el.dataset.val); break;
    case 'add': startEdit(KIND[tab], null); break;
    case 'edit': startEdit(KIND[tab], id); break;
    case 'copy': copyItem(id); break;
    case 'remove': removeItem(id); break;
    case 'toggle': toggleOpen(id); break;
    case 'tag':
      tagFilter = el.dataset.tag === tagFilter ? '' : el.dataset.tag;
      renderBar();
      renderList();
      break;
    case 'sort':
      sortBy[tab] = el.dataset.val;
      renderBar();
      renderList();
      break;
    case 'starter': startEdit('fact', null, { text: FACT_STARTERS[Number(el.dataset.i)] || '' }); break;
    case 'save-edit': saveEdit(); break;
    case 'cancel-edit': cancelEdit(); break;
    case 'undo': undoRemove(); break;
    case 'clear': clearAll(el); break;
    case 'reload': load(); break;
    default:
  }
}

function syncSaveButton() {
  const btn = $('memory-list')?.querySelector('[data-act="save-edit"]');
  if (btn) btn.disabled = saving || !canSave();
}

function onInput(e) {
  const el = e.target;
  if (el.id === 'memory-search') {
    query = el.value;
    renderList();
    return;
  }
  if (!editing) return;
  const field = { 'mem-ed-text': 'text', 'mem-ed-task': 'task', 'mem-ed-lesson': 'lesson', 'mem-ed-tags': 'tags' }[el.id];
  if (!field) return;
  editing.draft[field] = el.value;
  if (el.tagName === 'TEXTAREA') grow(el);
  syncSaveButton();
}

function onKey(e) {
  if (e.isComposing) return;
  const el = e.target;
  if (el.matches('.mk-tab') && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
    e.preventDefault();
    setTab(e.key === 'ArrowLeft' ? 'facts' : 'lessons');
    $('memory-tabs')?.querySelector('.mk-tab[aria-selected="true"]')?.focus();
    return;
  }
  if (!editing || !el.id?.startsWith('mem-ed-')) return;
  const mod = e.metaKey || e.ctrlKey;
  if (e.key !== 'Enter') return;
  if (mod || (el.id === 'mem-ed-text' && !e.shiftKey) || el.id === 'mem-ed-tags') {
    e.preventDefault();
    saveEdit();
  } else if (el.id === 'mem-ed-task') {
    e.preventDefault();
    $('mem-ed-lesson')?.focus();
  }
}

function startPoll() {
  stopPoll();
  poll = setInterval(() => {
    if (!document.hidden && !saving && !editing && isModalOpen('memory')) load();
  }, POLL_MS);
}

function stopPoll() {
  if (poll) clearInterval(poll);
  poll = 0;
}

/** `which`: 'facts' (Memory) or 'lessons'. */
export async function openMemory(which = 'facts') {
  tab = KIND[which] ? which : 'facts';
  query = '';
  tagFilter = '';
  editing = null;
  built = false;
  const bar = $('memory-bar');
  if (bar) delete bar.dataset.tab;
  render();
  openModal('memory', {
    focus: undefined,
    onClose: () => {
      stopPoll();
      editing = null;
      built = false;
      clearUndo();
      if (clearArmed) clearTimeout(clearArmed);
      clearArmed = 0;
    },
  });
  startPoll();
  await load();
  if (isModalOpen('memory')) requestAnimationFrame(() => $('memory-search')?.focus({ preventScroll: true }));
}

export function initMemory() {
  const card = $('memory');
  card?.addEventListener('click', onClick);
  card?.addEventListener('input', onInput);
  card?.addEventListener('keydown', onKey);
}
