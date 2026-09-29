/** Activity tab: what Jarvis is doing right now, what it has done, and the
 * background jobs it started.
 *
 * Everything here is derived from what the page already receives: the store
 * (busy / status / prompts), `tool_start` / `tool_done` events, the snapshot's
 * tool rows and the `jobs` list of the session state. Rows are keyed and
 * updated in place, so a busy turn changes numbers and statuses instead of
 * rebuilding the tab.
 */
import { $, formatElapsed, showToast, reducedMotion } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe } from './store.js';
import { setTabLive, isInspectorOpen, activeTab, onInspectorChange, closeInspector, isDocked } from './inspector.js';

const KINDS = {
  read: { label: 'Read', icon: 'file-text' },
  search: { label: 'Search', icon: 'search' },
  edit: { label: 'Edit', icon: 'file-pen' },
  shell: { label: 'Shell', icon: 'terminal' },
  web: { label: 'Web', icon: 'globe' },
  git: { label: 'Git', icon: 'git-branch' },
  other: { label: 'Other', icon: 'wrench' },
};
const ORDER = Object.keys(KINDS);
const MAX_TOOLS = 300;
const SHOWN = 20;

export function kindOf(name) {
  const n = String(name || '');
  if (/^(read_file|read_document|read_bundle|resolve_context|list_dir)$/.test(n)) return 'read';
  if (/^(search_code|glob_files|fast_find|rank_files)$/.test(n)) return 'search';
  if (/^(write_file|edit_file|multi_edit)$/.test(n)) return 'edit';
  if (/^(run_bash|run_bg|bg_output|bg_kill)$/.test(n)) return 'shell';
  if (/^(web_search|verified_search|fetch_url|open_url)$/.test(n)) return 'web';
  if (/^git_/.test(n)) return 'git';
  return 'other';
}

const A = {
  tools: [],                 // oldest first: { key, id, name, title, args, status, t0, t1, isNew }
  byId: new Map(),
  jobs: [],
  turnTools: 0,
  lastTurn: null,            // { secs, tools, stopped }
  expanded: false,
  dirty: true,
  raf: 0,
};
let seq = 0;
const rows = new Map();      // timeline key → <li>
const jobRows = new Map();   // job id → <li>
const ui = {};

const visible = () => isInspectorOpen() && activeTab() === 'activity';
const serverNow = () => Date.now() / 1000 + (store.skew || 0);

function fmtMs(ms) {
  if (ms < 950) return `${Math.max(1, Math.round(ms))}ms`;
  const s = ms / 1000;
  return s < 10 ? `${s.toFixed(1)}s` : formatElapsed(s);
}

// ─── Feeding it ───────────────────────────────────────────────────────────

function remember(tool) {
  tool.key = tool.id || `x${(seq += 1)}`;
  A.tools.push(tool);
  A.byId.set(tool.key, tool);
  while (A.tools.length > MAX_TOOLS) {
    const old = A.tools.shift();
    A.byId.delete(old.key);
  }
  return tool;
}

function touch() {
  A.dirty = true;
  if (visible() && !A.raf) A.raf = requestAnimationFrame(render);
}

export function noteToolStart(data) {
  const id = String(data?.id || '');
  const known = id && A.byId.get(id);
  if (known) {
    if (known.status === 'pending') Object.assign(known, { status: 'running', t0: Date.now(), t1: 0 });
  } else {
    remember({
      id, name: data.name || '', title: data.title || data.name || 'Tool', args: data.args || '',
      status: 'running', t0: Date.now(), t1: 0, isNew: true,
    });
    A.turnTools += 1;
  }
  touch();
}

export function noteToolDone(data) {
  const id = String(data?.id || '');
  const tool = (id && A.byId.get(id)) || remember({
    id, name: data.name || '', title: data.title || data.name || 'Tool', args: data.args || '',
    status: 'done', t0: 0, t1: 0, isNew: true,
  });
  tool.status = data.error ? 'error' : 'done';
  tool.t1 = Date.now();
  if (data.title) tool.title = data.title;
  if (data.args) tool.args = data.args;
  touch();
}

/** The snapshot's tool rows: the whole history, without timings. */
export function loadActivity(messages) {
  const next = [];
  for (const m of messages || []) {
    if (m.role !== 'tool') continue;
    const id = String(m.id || '');
    const old = id && A.byId.get(id);
    next.push({
      key: id || `x${(seq += 1)}`, id, name: m.name || '', title: m.title || m.name || 'Tool', args: m.args || '',
      status: m.status || 'done', t0: old?.t0 || 0, t1: old?.t1 || 0, isNew: false,
    });
  }
  A.tools = next.slice(-MAX_TOOLS);
  A.byId = new Map(A.tools.map((t) => [t.key, t]));
  touch();
}

/** A turn began or ended (status.js:setBusy — synchronous, so tool counts are exact). */
export function noteBusy(busy, { secs = 0, stopped = false } = {}) {
  if (busy) {
    A.turnTools = 0;
  } else {
    for (const t of A.tools) if (t.status === 'running') t.status = 'pending';
    A.lastTurn = { secs, tools: A.turnTools, stopped };
  }
  touch();
}

export function setJobs(jobs) {
  A.jobs = Array.isArray(jobs) ? jobs : [];
  touch();
}

// ─── Rendering ────────────────────────────────────────────────────────────

function renderNow(s) {
  const waiting = s.connected && !!s.activePrompt;
  const state = !s.connected ? 'offline' : waiting ? 'waiting' : s.busy ? 'busy' : 'idle';
  ui.now.className = `act-now is-${state}`;
  let title = 'Ready';
  let sub = 'Send a message to get started';
  if (state === 'offline') {
    title = s.everConnected ? 'Offline' : 'Connecting';
    sub = 'Trying to reach Jarvis…';
  } else if (state === 'waiting') {
    title = 'Waiting for you';
    sub = 'Jarvis asked a question or needs approval';
  } else if (state === 'busy') {
    title = s.statusLabel || 'Working';
    const names = A.tools.filter((t) => t.status === 'running').map((t) => t.title);
    sub = names.length ? [...new Set(names)].slice(0, 3).join(' · ')
      : `${A.turnTools} tool call${A.turnTools === 1 ? '' : 's'} this turn`;
    if (s.queue?.length) sub += ` · ${s.queue.length} queued`;
  } else if (A.lastTurn) {
    const t = A.lastTurn;
    title = t.stopped ? 'Stopped' : 'Ready';
    sub = `Last turn: ${formatElapsed(t.secs)} · ${t.tools} tool call${t.tools === 1 ? '' : 's'}`;
  } else if (A.tools.length) {
    sub = `${A.tools.length} tool call${A.tools.length === 1 ? '' : 's'} so far in this session`;
  }
  if (ui.nowTitle.textContent !== title) ui.nowTitle.textContent = title;
  if (ui.nowSub.textContent !== sub) ui.nowSub.textContent = sub;
  tickTime();
}

function tickTime() {
  if (ui.nowTime) ui.nowTime.textContent = store.busy && store.busySince ? formatElapsed((Date.now() - store.busySince) / 1000) : '';
  for (const t of A.tools) {
    if (t.status !== 'running') continue;
    const el = rows.get(t.key)?.querySelector('.tl-time');
    if (el && t.t0) el.textContent = fmtMs(Date.now() - t.t0);
  }
  for (const j of A.jobs) {
    if (j.status !== 'running') continue;
    const el = jobRows.get(j.id)?.querySelector('.job-meta');
    if (el) el.textContent = formatElapsed(Math.max(0, serverNow() - j.started));
  }
}

function renderMix() {
  const counts = Object.fromEntries(ORDER.map((k) => [k, 0]));
  for (const t of A.tools) counts[kindOf(t.name)] += 1;
  const total = A.tools.length;
  ui.mixTotal.textContent = total ? `${total} call${total === 1 ? '' : 's'}` : '';
  ui.mixEmpty.hidden = total > 0;
  ui.mixBar.hidden = ui.mixLegend.hidden = total < 1;
  for (const k of ORDER) {
    const seg = ui.mixBar.querySelector(`[data-k="${k}"]`);
    const item = ui.mixLegend.querySelector(`[data-k="${k}"]`);
    seg.hidden = item.hidden = !counts[k];
    seg.style.setProperty('--n', String(counts[k]));
    seg.title = `${KINDS[k].label}: ${counts[k]}`;
    item.querySelector('b').textContent = String(counts[k]);
  }
}

const STATE_ICON = { done: 'check', error: 'x', pending: 'minus' };

function makeRow(t) {
  const li = document.createElement('li');
  li.innerHTML = `
    <button type="button" class="tl${t.isNew ? ' is-new' : ''}" data-k="${kindOf(t.name)}">
      <span class="tl-kind">${icon(KINDS[kindOf(t.name)].icon)}</span>
      <span class="tl-text"><span class="tl-title"></span><span class="tl-args"></span></span>
      <span class="tl-state"></span>
      <span class="tl-time"></span>
    </button>`;
  const btn = li.firstElementChild;
  btn.addEventListener('click', () => revealTool(t.id));
  // Played once; dropped so moving the row later doesn't replay it.
  if (t.isNew) setTimeout(() => btn.classList.remove('is-new'), 800);
  return li;
}

function paintRow(li, t) {
  const btn = li.firstElementChild;
  if (btn.dataset.status !== t.status) {
    btn.dataset.status = t.status;
    btn.querySelector('.tl-state').innerHTML = t.status === 'running' ? '<span class="spinner"></span>' : icon(STATE_ICON[t.status] || 'check');
    btn.title = { running: 'Running', done: 'Done', error: 'Failed', pending: 'Not finished' }[t.status] || '';
  }
  const title = btn.querySelector('.tl-title');
  if (title.textContent !== t.title) title.textContent = t.title;
  const args = btn.querySelector('.tl-args');
  if (args.textContent !== t.args) {
    args.textContent = t.args;
    btn.setAttribute('aria-label', `${t.title} ${t.args}`.trim());
  }
  const time = btn.querySelector('.tl-time');
  const text = t.status !== 'running' && t.t0 && t.t1 ? fmtMs(t.t1 - t.t0) : t.status === 'running' && t.t0 ? fmtMs(Date.now() - t.t0) : '';
  if (time.textContent !== text) time.textContent = text;
  t.isNew = false;
}

function renderTimeline() {
  const newest = [...A.tools].reverse();
  const list = A.expanded ? newest : newest.slice(0, SHOWN);
  ui.tlEmpty.hidden = A.tools.length > 0;
  ui.tlCount.textContent = A.tools.length ? String(A.tools.length) : '';
  const more = A.tools.length - SHOWN;
  ui.tlMore.hidden = more <= 0;
  ui.tlMore.textContent = A.expanded ? 'Show fewer' : `Show all ${A.tools.length}`;

  const keep = new Set();
  let cursor = ui.tlList.firstElementChild;
  for (const t of list) {
    keep.add(t.key);
    let li = rows.get(t.key);
    if (!li) {
      li = makeRow(t);
      rows.set(t.key, li);
    }
    if (cursor === li) cursor = cursor.nextElementSibling;
    else ui.tlList.insertBefore(li, cursor);
    paintRow(li, t);
  }
  for (const [key, li] of rows) {
    if (keep.has(key)) continue;
    li.remove();
    rows.delete(key);
  }
}

function renderJobs() {
  const box = ui.jobs;
  box.hidden = !A.jobs.length;
  if (!A.jobs.length) {
    jobRows.clear();
    box.innerHTML = '';
    return;
  }
  if (!box.querySelector('.job-list')) {
    box.innerHTML = '<h3 class="act-title">Background jobs <span></span></h3><ul class="job-list"></ul>';
  }
  const running = A.jobs.filter((j) => j.status === 'running').length;
  box.querySelector('.act-title span').textContent = running ? `${running} running` : 'all finished';
  const ul = box.querySelector('.job-list');
  const keep = new Set();
  let cursor = ul.firstElementChild;
  // Running first, then newest.
  const ordered = [...A.jobs].sort((a, b) => (b.status === 'running') - (a.status === 'running') || b.id - a.id);
  for (const j of ordered) {
    keep.add(j.id);
    const status = j.status === 'done' && j.code ? 'failed' : j.status;
    let li = jobRows.get(j.id);
    if (!li) {
      li = document.createElement('li');
      li.className = 'job';
      li.innerHTML = '<span class="job-state"></span><span class="job-cmd"></span><span class="job-meta"></span>';
      jobRows.set(j.id, li);
    }
    if (cursor === li) cursor = cursor.nextElementSibling;
    else ul.insertBefore(li, cursor);
    if (li.dataset.status !== status) {
      li.dataset.status = status;
      li.querySelector('.job-state').innerHTML = status === 'running' ? '<span class="spinner"></span>'
        : icon(status === 'done' ? 'circle-check' : 'circle-alert');
    }
    const cmd = li.querySelector('.job-cmd');
    if (cmd.textContent !== j.cmd) {
      cmd.textContent = j.cmd;
      li.title = `#${j.id}  ${j.cmd}`;
    }
    li.querySelector('.job-meta').textContent = status === 'running'
      ? formatElapsed(Math.max(0, serverNow() - j.started))
      : `${status === 'killed' ? 'stopped' : status === 'failed' ? `exit ${j.code}` : 'done'} · ${formatElapsed(j.secs || 0)}`;
  }
  for (const [id, li] of jobRows) {
    if (keep.has(id)) continue;
    li.remove();
    jobRows.delete(id);
  }
}

function render() {
  A.raf = 0;
  if (!visible()) return;      // rendered when the tab is shown (onInspectorChange)
  A.dirty = false;
  renderNow(store);
  renderJobs();
  renderMix();
  renderTimeline();
}

// ─── Jump to a step in the transcript ─────────────────────────────────────

function revealTool(id) {
  const row = id ? document.querySelector(`#chat .tool[data-id="${CSS.escape(id)}"]`) : null;
  if (!row) {
    showToast('That step isn’t in the transcript');
    return;
  }
  const group = row.closest('.tools');
  // Older rows of a long run are folded away: unfold before scrolling to one.
  if (group?.classList.contains('is-folded')) group.querySelector(':scope > .tools-more')?.click();
  if (!isDocked() && isInspectorOpen()) closeInspector();
  requestAnimationFrame(() => {
    row.scrollIntoView({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' });
    row.classList.remove('is-revealed');
    void row.offsetWidth;
    row.classList.add('is-revealed');
    setTimeout(() => row.classList.remove('is-revealed'), 1700);
  });
}

// ─── Init ─────────────────────────────────────────────────────────────────

export function initActivity() {
  Object.assign(ui, { now: $('act-now'), jobs: $('act-jobs'), mix: $('act-mix'), timeline: $('act-timeline') });
  if (!ui.now) return;

  ui.now.innerHTML = '<span class="now-dot" aria-hidden="true"></span><div class="now-text" role="status"><strong></strong><span></span></div><span class="now-time"></span>';
  ui.nowTitle = ui.now.querySelector('strong');
  ui.nowSub = ui.now.querySelector('.now-text > span');
  ui.nowTime = ui.now.querySelector('.now-time');

  ui.mix.innerHTML = `
    <h3 class="act-title">Work so far <span id="mix-total"></span></h3>
    <div class="mix-bar" id="mix-bar">${ORDER.map((k, i) => `<i data-k="${k}" style="--i:${i}" hidden></i>`).join('')}</div>
    <div class="mix-legend" id="mix-legend">${ORDER.map((k) => `<div class="mix-item" data-k="${k}" hidden><i></i><span>${KINDS[k].label}</span><b>0</b></div>`).join('')}</div>
    <p class="mix-empty" id="mix-empty">Tool calls are counted here as Jarvis works.</p>`;
  ui.mixTotal = $('mix-total');
  ui.mixBar = $('mix-bar');
  ui.mixLegend = $('mix-legend');
  ui.mixEmpty = $('mix-empty');

  ui.timeline.innerHTML = `
    <h3 class="act-title">Recent steps <span id="tl-count"></span></h3>
    <ul class="tl-list" id="tl-list"></ul>
    <p class="act-empty" id="tl-empty">Every tool call shows up here. Tap one to jump to it in the chat.</p>
    <button type="button" class="chip-btn tl-more" id="tl-more" hidden></button>`;
  ui.tlList = $('tl-list');
  ui.tlEmpty = $('tl-empty');
  ui.tlCount = $('tl-count');
  ui.tlMore = $('tl-more');
  ui.tlMore.addEventListener('click', () => {
    A.expanded = !A.expanded;
    touch();
  });

  subscribe((s) => {
    setTabLive('activity', s.connected && s.busy);
    touch();
  });
  onInspectorChange(() => { if (visible()) touch(); });
  setInterval(() => { if (visible()) tickTime(); }, 1000);
  touch();
}
