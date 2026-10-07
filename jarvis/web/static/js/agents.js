/** Parallel agents card — the web twin of the TUI board (tui/agents_block.py).
 *
 * One card per `spawn_agents` call: a header (title, counts, clock, Stop all),
 * a progress bar, and one row per subagent (status, name, what it's doing
 * now, tool count, time). A row opens to show the agent's brief, its latest
 * steps, the files it changed and — once finished — its report.
 *
 * Live `agents` events re-send the whole board up to 4× a second; rows are
 * keyed by agent index and patched in place (only changed text is written),
 * so an open row, a scroll position or a hover never resets. Reports are
 * sent once (when an agent finishes) and cached on the card; a card that
 * missed one (opened mid-run, reconnected) fetches the full board.
 */
import { escapeHtml, showToast } from './utils.js';
import { icon } from './icons.js';
import { renderMarkdown, applyMarkdownLinks } from './markdown.js';
import { fetchSubagents, stopSubagent } from './api.js';

const STATUS_ICON = {
  done: 'check', error: 'x', stopped: 'square', cancelled: 'ban', timeout: 'timer', queued: 'circle-dashed',
};
const STATUS_LABEL = {
  queued: 'Queued', running: 'Running', done: 'Done', error: 'Failed',
  stopped: 'Stopped', cancelled: 'Cancelled', timeout: 'Timed out',
};
const LOG_SHOW = 6;

export const isAgentsTool = (name) => name === 'spawn_agents';

function clock(sec) {
  if (sec == null || !Number.isFinite(Number(sec))) return '';
  const s = Math.max(0, Math.floor(Number(sec)));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${r}` : `${m}:${r}`;
}

function setText(el, text) {
  if (el && el.textContent !== text) el.textContent = text;
}

function setHtml(el, html) {
  if (el && el._html !== html) {
    el._html = html;
    el.innerHTML = html;
  }
}

/** Agents from the tool input — shown until the first board arrives. */
function agentsFromInput(input) {
  const list = Array.isArray(input?.agents) ? input.agents : [];
  return list.map((a, i) => ({
    i, name: String(a?.name || `Agent ${i + 1}`), task: String(a?.task || ''),
    mode: a?.mode === 'edit' ? 'edit' : 'explore', status: 'queued', activity: 'Starting…', steps: 0, log: [], files: [],
  }));
}

/** Seconds the server's clock is ahead of ours (board.now is server time). */
function skewOf(board) {
  return board?.now ? Number(board.now) - Date.now() / 1000 : 0;
}

function effectiveStatus(card, a) {
  const st = a.status || 'queued';
  const over = card.dataset.status && card.dataset.status !== 'running';
  return over && (st === 'running' || st === 'queued') ? 'cancelled' : st;
}

function agentElapsed(card, a) {
  if (a.started) {
    const live = effectiveStatus(card, a) === 'running';
    const end = a.finished || (live ? Date.now() / 1000 + (card._skew || 0) : null);
    if (end) return clock(end - a.started);
  }
  if (a.elapsed != null) return clock(a.elapsed);
  return a.elapsed_label || '';
}

function teamElapsed(card) {
  const b = card._board;
  if (b?.elapsed_label) return b.elapsed_label;
  const agents = b?.agents || [];
  const starts = agents.map((a) => a.started).filter(Boolean);
  if (!starts.length) return '';
  const live = card.dataset.status === 'running';
  const now = Date.now() / 1000 + (card._skew || 0);
  const ends = agents.filter((a) => a.started).map((a) => a.finished || (live ? now : 0)).filter(Boolean);
  return clock((ends.length ? Math.max(...ends) : now) - Math.min(...starts));
}

function activityText(card, a, st) {
  if (st === 'done') {
    const files = a.files || [];
    if (files.length) {
      const add = files.reduce((n, f) => n + (Number(f.added) || 0), 0);
      const rem = files.reduce((n, f) => n + (Number(f.removed) || 0), 0);
      return `Finished · +${add} −${rem} in ${files.length} file${files.length === 1 ? '' : 's'}`;
    }
    return a.has_report || a.report || card._reports?.has(a.i) ? 'Finished · report ready' : 'Finished';
  }
  if (st === 'running') return a.activity || 'Working…';
  if (st === 'queued') return 'Queued — waits for a free slot';
  const label = STATUS_LABEL[st] || st;
  const err = String(a.error || '');
  if (!err || err.toLowerCase() === label.toLowerCase()) return label;
  // "Stopped by the user" already says "Stopped"
  return err.toLowerCase().startsWith(label.toLowerCase().split(' ')[0]) ? err : `${label} · ${err}`;
}

// ─── Build ───────────────────────────────────────────────────────────────

/** Fill a fresh `.tool` element as an agents card. */
export function initAgentsCard(card, data) {
  card.classList.add('agents');
  card._reports = new Map();
  card._open = new Set();
  card.innerHTML = `
    <div class="ag-head">
      <span class="ag-icon">${icon('users')}</span>
      <div class="ag-titles">
        <div class="ag-title"><span>Parallel agents</span><span class="ag-total"></span></div>
        <div class="ag-sub"></div>
      </div>
      <span class="ag-clock" aria-label="Elapsed"></span>
      <button type="button" class="ag-stopall" title="Stop every agent that is still working">${icon('square')}<span>Stop all</span></button>
    </div>
    <div class="ag-goal" hidden></div>
    <div class="ag-bar" role="progressbar" aria-valuemin="0"><span class="ag-bar-done"></span><span class="ag-bar-run"></span></div>
    <ol class="ag-list"></ol>
    <div class="ag-foot"></div>`;
  card.querySelector('.ag-stopall').addEventListener('click', async (e) => {
    e.stopPropagation();
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const res = await stopSubagent(card.dataset.id, null);
      if (!res?.ok) showToast('Nothing left to stop');
    } catch {
      showToast('Could not stop the agents', true);
      btn.disabled = false;
    }
  });
  if (!card._board) card._board = data.agents || { agents: agentsFromInput(data.input) };
  paintAgents(card, data);
}

function buildRow(card, a) {
  const li = document.createElement('li');
  li.className = 'ag-row';
  li.dataset.i = String(a.i);
  li.style.setProperty('--ag', `var(--ag-${a.i % 6})`);
  li.innerHTML = `
    <div class="ag-row-line">
      <button type="button" class="ag-row-head" aria-expanded="false">
        <span class="ag-state"></span>
        <span class="ag-name"></span>
        <span class="ag-mode"></span>
        <span class="ag-act"></span>
        <span class="ag-meta"><span class="ag-steps"></span><span class="ag-time"></span></span>
        <span class="tool-chev">${icon('chevron-right')}</span>
      </button>
      <button type="button" class="ag-stop" title="Stop this agent" aria-label="Stop this agent">${icon('square')}</button>
    </div>
    <div class="fold"><div class="fold-inner"><div class="ag-detail"></div></div></div>`;
  li.querySelector('.ag-row-head').addEventListener('click', () => toggleRow(card, li));
  li.querySelector('.ag-stop').addEventListener('click', async (e) => {
    e.stopPropagation();
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const res = await stopSubagent(card.dataset.id, Number(li.dataset.i));
      if (!res?.ok) btn.disabled = false;
    } catch {
      showToast('Could not stop that agent', true);
      btn.disabled = false;
    }
  });
  return li;
}

function toggleRow(card, li) {
  const i = Number(li.dataset.i);
  const open = li.classList.toggle('is-open');
  li.querySelector('.ag-row-head').setAttribute('aria-expanded', String(open));
  if (open) card._open.add(i); else card._open.delete(i);
  const a = (card._board?.agents || []).find((x) => Number(x.i) === i);
  if (open && a) {
    paintDetail(card, li, a);
    const st = effectiveStatus(card, a);
    if (a.has_report && !card._reports.has(i) && !a.report && st !== 'running') loadFull(card);
  }
}

/** Fetch the full board (reports) once — for a card that missed some. */
async function loadFull(card) {
  if (card._loading || !card.dataset.id) return;
  card._loading = true;
  try {
    const board = await fetchSubagents(card.dataset.id);
    if (board?.agents) paintAgents(card, { agents: board });
  } catch { /* stays as is */ }
  card._loading = false;
}

// ─── Paint ───────────────────────────────────────────────────────────────

/** Apply tool-row data and/or a board (`data.agents`) to the card. */
export function paintAgents(card, data = {}) {
  if (data.id) card.dataset.id = String(data.id);
  if (data.status) {
    // A snapshot taken mid-run lists the call as "pending"; its board knows better.
    const live = data.status === 'pending' && data.agents?.status === 'running' && !data.agents.restored;
    card.dataset.status = live ? 'running' : data.status;
  }
  if (data.summary != null) card.dataset.summary = data.summary;
  if (data.agents && Array.isArray(data.agents.agents)) {
    const board = data.agents;
    for (const a of board.agents) {
      if (a.report) card._reports.set(Number(a.i), a.report);
    }
    card._board = board;
    card._skew = skewOf(board);
  }
  const status = card.dataset.status || 'running';
  const board = card._board || { agents: [] };
  const agents = board.agents || [];
  const total = agents.length;

  // header
  const counts = { running: 0, queued: 0, done: 0, failed: 0 };
  for (const a of agents) {
    const st = effectiveStatus(card, a);
    counts[st === 'running' || st === 'queued' || st === 'done' ? st : 'failed'] += 1;
  }
  setText(card.querySelector('.ag-total'), total ? String(total) : '');
  const sub = [];
  if (counts.running) sub.push(`<b class="is-run">${counts.running} running</b>`);
  if (counts.queued) sub.push(`<span>${counts.queued} queued</span>`);
  if (counts.done) sub.push(`<b class="is-ok">${counts.done} done</b>`);
  if (counts.failed) sub.push(`<b class="is-warn">${counts.failed} not finished</b>`);
  if (board.steps) sub.push(`<span>${board.steps} tool call${board.steps === 1 ? '' : 's'}</span>`);
  setHtml(card.querySelector('.ag-sub'), sub.join('<i>·</i>') || '<span>Starting…</span>');
  setText(card.querySelector('.ag-clock'), teamElapsed(card));
  const goal = board.goal || '';
  const goalEl = card.querySelector('.ag-goal');
  goalEl.hidden = !goal;
  setText(goalEl, goal);
  card.querySelector('.ag-stopall').hidden = status !== 'running' || !(counts.running + counts.queued);

  // progress
  const finished = counts.done + counts.failed;
  const bar = card.querySelector('.ag-bar');
  bar.setAttribute('aria-valuemax', String(total || 1));
  bar.setAttribute('aria-valuenow', String(finished));
  bar.querySelector('.ag-bar-done').style.width = total ? `${(finished / total) * 100}%` : '0%';
  bar.querySelector('.ag-bar-run').style.width = total ? `${(counts.running / total) * 100}%` : '0%';
  bar.classList.toggle('is-complete', total > 0 && finished === total);

  // rows (keyed by index)
  const list = card.querySelector('.ag-list');
  for (const a of agents) {
    let li = list.querySelector(`:scope > .ag-row[data-i="${a.i}"]`);
    if (!li) {
      li = buildRow(card, a);
      if (card._open.has(Number(a.i))) li.classList.add('is-open');
      list.appendChild(li);
    }
    paintRow(card, li, a);
  }

  // footer
  const foot = card.querySelector('.ag-foot');
  if (status === 'running') setText(foot, 'Open an agent to follow its steps · each works on its own part at the same time');
  else if (status === 'error') setText(foot, card.dataset.summary || 'The agents could not run');
  else setText(foot, 'Open an agent for its brief and report · Jarvis combines them below');

  syncTicker(card);
}

function paintRow(card, li, a) {
  const st = effectiveStatus(card, a);
  const prev = li.dataset.status;
  li.dataset.status = st;
  li.dataset.mode = a.mode || 'explore';
  setHtml(li.querySelector('.ag-state'), st === 'running' ? '<span class="spinner"></span>' : icon(STATUS_ICON[st] || 'circle'));
  setText(li.querySelector('.ag-name'), a.name || `Agent ${a.i + 1}`);
  setText(li.querySelector('.ag-mode'), a.mode === 'edit' ? 'edit' : 'read-only');
  li.querySelector('.ag-mode').title = a.mode === 'edit' ? 'May change files and run commands' : 'Investigates only — changes nothing';
  setText(li.querySelector('.ag-act'), activityText(card, a, st));
  const steps = Number(a.steps) || 0;
  setText(li.querySelector('.ag-steps'), steps || st !== 'queued' ? `${steps} tool${steps === 1 ? '' : 's'}` : '');
  setText(li.querySelector('.ag-time'), agentElapsed(card, a));
  li.querySelector('.ag-stop').hidden = !(card.dataset.status === 'running' && (st === 'running' || st === 'queued'));
  li.title = a.task ? `${a.name}\n\n${a.task}` : a.name;
  if (prev && prev !== st && (st === 'done' || st === 'error')) {
    li.classList.remove('just-settled');
    void li.offsetWidth;
    li.classList.add('just-settled');
  }
  if (li.classList.contains('is-open')) paintDetail(card, li, a);
}

function paintDetail(card, li, a) {
  const st = effectiveStatus(card, a);
  const parts = [];
  if (a.task) {
    parts.push(`<div class="ag-sec"><div class="ag-label">Brief</div><div class="ag-task">${escapeHtml(a.task)}</div></div>`);
  }
  const log = (a.log || []).slice(-LOG_SHOW);
  if (log.length && (st === 'running' || !card._reports.get(Number(a.i)))) {
    const rows = log.map((e) => {
      const s = e.status === 'done' ? 'done' : e.status === 'error' ? 'error' : 'running';
      const mark = s === 'running' ? '<span class="spinner"></span>' : icon(s === 'done' ? 'check' : 'x');
      return `<li data-s="${s}"><span class="ag-log-mark">${mark}</span><span class="ag-log-text">${escapeHtml(e.label || '')}</span></li>`;
    }).join('');
    parts.push(`<div class="ag-sec"><div class="ag-label">${st === 'running' ? 'Now' : 'Last steps'}</div><ol class="ag-log">${rows}</ol></div>`);
  }
  const files = a.files || [];
  if (files.length) {
    const rows = files.map((f) => `<li><span class="ag-file">${escapeHtml(f.path || '')}</span><span class="ag-add">+${Number(f.added) || 0}</span><span class="ag-del">−${Number(f.removed) || 0}</span></li>`).join('');
    parts.push(`<div class="ag-sec"><div class="ag-label">Files changed</div><ul class="ag-files">${rows}</ul></div>`);
  }
  const report = card._reports.get(Number(a.i)) || a.report || '';
  if (report) {
    parts.push(`<div class="ag-sec"><div class="ag-label">Report</div><div class="ag-report md">${renderMarkdown(report)}</div></div>`);
  } else if (st !== 'running' && st !== 'queued') {
    parts.push(`<div class="ag-sec"><div class="ag-empty">${a.has_report ? 'Loading the report…' : 'No report — this agent stopped before writing one.'}</div></div>`);
  } else if (st === 'queued') {
    parts.push('<div class="ag-sec"><div class="ag-empty">Waiting for a free slot — starts when another agent finishes.</div></div>');
  }
  const detail = li.querySelector('.ag-detail');
  setHtml(detail, parts.join(''));
  applyMarkdownLinks(detail);
}

/** Clocks tick once a second while the card runs. */
function syncTicker(card) {
  const live = card.dataset.status === 'running';
  if (live && !card._tick) {
    card._tick = setInterval(() => {
      if (!card.isConnected || card.dataset.status !== 'running') {
        clearInterval(card._tick);
        card._tick = null;
        return;
      }
      setText(card.querySelector('.ag-clock'), teamElapsed(card));
      for (const a of card._board?.agents || []) {
        const li = card.querySelector(`.ag-row[data-i="${a.i}"]`);
        if (li) setText(li.querySelector('.ag-time'), agentElapsed(card, a));
      }
    }, 1000);
  } else if (!live && card._tick) {
    clearInterval(card._tick);
    card._tick = null;
  }
  if (!live && card._board && card._board.status !== 'done' && !card._board.restored && card.dataset.id) {
    // Finished/cancelled but the final board never arrived (dropped event).
    loadFull(card);
  }
}
