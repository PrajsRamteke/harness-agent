/** Usage — every model request Jarvis made, on every provider, as a dashboard.
 *
 *   [Today | 7 days | 30 days | All time]
 *   ┌ REQUESTS ┐ ┌ TOKENS ┐ ┌ SPENT ┐ ┌ PLAN VALUE ┐       ← stat tiles
 *   This chat · 34 requests · 1.2M tokens (94% cached) · $3.12
 *   ▁▃▅▂▇▁▁  per day
 *   By provider   requests · tokens · cost
 *   By model      requests · tokens · cost
 *
 * Data: GET /api/usage/summary?days=N (server: jarvis/storage/usage_log.py
 * `summary`). "Spent" is pay-per-use money (API key, OpenRouter, OpenCode …);
 * "Plan value" is what requests covered by a subscription (Claude Code CLI on a
 * plan, Codex, Cursor …) would have cost on the API — not billed. Opened from
 * the sidebar's usage counters, the Claude plan card, `/cost` or ⌘K.
 */
import { $, escapeHtml, formatCount, storageGet, storageSet } from './utils.js';
import { icon } from './icons.js';
import { openModal, isModalOpen } from './modal.js';
import { api } from './api.js';
import { patch, seg } from './extui.js';
import { footer, empty, section } from './dialog.js';

const ID = 'usage-stats';
const RANGE_KEY = 'jarvis-usage-days';
const RANGES = [
  { value: '1', label: 'Today' },
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '0', label: 'All time' },
];
const POLL_MS = 8000; // a turn running beside the dialog keeps adding requests
const MODELS_SHOWN = 12;
const DAY_MS = 86400000;
const STALE_SERVER = 'This Jarvis started before usage tracking was added. Restart it (stop it in Projects and open it again) to see usage.';

let days = 7;
let data = null;
let error = '';
let loading = false;
let seq = 0; // the latest request: an older answer (another period) is dropped
let poll = 0;
let allModels = false;

// ─── Formatting ──────────────────────────────────────────────────────────

/** Whole cents for real money; more places for fractions of a cent. */
export function usageCost(v) {
  const n = Number(v) || 0;
  if (n === 0) return '$0.00';
  if (Math.abs(n) < 0.01) return `${n < 0 ? '−' : ''}<$0.01`;
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

const tokensOf = (d) => (Number(d?.input) || 0) + (Number(d?.cache_read) || 0) + (Number(d?.cache_write) || 0) + (Number(d?.output) || 0);
const promptOf = (d) => (Number(d?.input) || 0) + (Number(d?.cache_read) || 0) + (Number(d?.cache_write) || 0);
const cachePct = (d) => {
  const p = promptOf(d);
  return p ? Math.round(((Number(d?.cache_read) || 0) * 100) / p) : 0;
};
const num = (v) => (Number(v) || 0).toLocaleString();

function tokenTitle(d) {
  return [
    `${num(d.input)} input (not cached)`,
    `${num(d.cache_read)} read from the prompt cache`,
    `${num(d.cache_write)} written to the prompt cache`,
    `${num(d.output)} output`,
  ].join('\n');
}

/** The cost cell: real spend, and/or the API-equivalent value of plan requests. */
function costHtml(d) {
  const paid = Number(d.cost) || 0;
  const plan = Number(d.plan_cost) || 0;
  const parts = [];
  if (paid || !plan) parts.push(`<b>${escapeHtml(usageCost(paid))}</b>`);
  if (plan) {
    parts.push(`<span class="us-plan" title="What these requests would cost on the API — covered by your plan, not billed">${escapeHtml(usageCost(plan))}<em>plan</em></span>`);
  }
  return parts.join('');
}

function dayLabel(iso, opts = { month: 'short', day: 'numeric' }) {
  const [y, m, d] = String(iso).split('-').map(Number);
  const at = new Date(y, (m || 1) - 1, d || 1);
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleDateString([], opts);
}

const isoDay = (t) => {
  const at = new Date(t);
  return `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, '0')}-${String(at.getDate()).padStart(2, '0')}`;
};

// ─── Rendering ───────────────────────────────────────────────────────────

function tile(label, value, sub = '', title = '') {
  return `<div class="mem-stat us-stat"${title ? ` title="${escapeHtml(title)}"` : ''}>
    <span class="ms-label">${escapeHtml(label)}</span>
    <span class="ms-val">${value}</span>
    ${sub ? `<span class="ms-sub">${sub}</span>` : ''}
  </div>`;
}

function renderStats() {
  const el = $('us-stats');
  if (!el) return;
  if (!data) {
    patch(el, '');
    return;
  }
  const t = data.totals || {};
  const sub = data.sources?.subagent?.requests || 0;
  const cp = cachePct(t);
  patch(el, [
    tile('Requests', escapeHtml(num(t.requests)), sub ? `${escapeHtml(num(sub))} by subagents` : 'model calls'),
    tile('Tokens', escapeHtml(formatCount(tokensOf(t))),
      `${escapeHtml(formatCount(promptOf(t)))} in${cp ? ` · ⚡${cp}% cached` : ''} · ${escapeHtml(formatCount(t.output))} out`, tokenTitle(t)),
    tile('Spent', escapeHtml(usageCost(t.cost)), 'pay-per-use providers', 'Billed: API keys, OpenRouter, OpenCode …'),
    tile('Plan value', escapeHtml(usageCost(t.plan_cost)), 'covered by plans',
      'What requests on a subscription (Claude Code CLI, Codex, Cursor …) would have cost on the API'),
  ].join(''));
}

function chatHtml(c) {
  if (!c) return '';
  const cp = cachePct(c);
  return `<div class="us-chat" title="${escapeHtml(tokenTitle(c))}">
    <span class="us-chat-ic">${icon('message-square')}</span>
    <span class="us-chat-lbl">This chat</span>
    <span class="us-chat-val"><b>${escapeHtml(num(c.requests))}</b> requests</span>
    <span class="us-chat-val"><b>${escapeHtml(formatCount(tokensOf(c)))}</b> tokens${cp ? ` <em>⚡${cp}%</em>` : ''}</span>
    <span class="us-chat-val us-chat-cost">${costHtml(c)}</span>
  </div>`;
}

/** One bar per day of the window (days without requests too). */
function chartHtml() {
  const rows = data.by_day || [];
  if (!rows.length || days === 1) return '';
  const byDay = new Map(rows.map((r) => [r.day, r]));
  let first;
  if (days > 0) first = Date.now() - (days - 1) * DAY_MS;
  else first = new Date(`${rows[0].day}T12:00:00`).getTime();
  const list = [];
  for (let t = first, n = 0; n < 400; t += DAY_MS, n += 1) {
    const key = isoDay(t);
    list.push(byDay.get(key) || { day: key, requests: 0, input: 0, cache_read: 0, cache_write: 0, output: 0, cost: 0, plan_cost: 0 });
    if (key === isoDay(Date.now())) break;
  }
  if (list.length < 2) return '';
  const max = Math.max(1, ...list.map(tokensOf));
  const bars = list.map((d) => {
    const v = tokensOf(d);
    const h = v ? Math.max(4, (v / max) * 100) : 0;
    const tip = `${dayLabel(d.day, { weekday: 'short', month: 'short', day: 'numeric' })}\n${num(d.requests)} requests · ${num(v)} tokens\n${usageCost(d.cost)} spent${d.plan_cost ? ` · ${usageCost(d.plan_cost)} plan value` : ''}`;
    return `<span class="us-day${v ? '' : ' is-empty'}" title="${escapeHtml(tip)}" role="img" aria-label="${escapeHtml(tip.replace(/\n/g, ', '))}"><i style="height:${h.toFixed(1)}%"></i></span>`;
  }).join('');
  return `${section('Tokens per day')}
    <div class="us-chart" style="--n:${list.length}">${bars}</div>
    <div class="us-chart-axis"><span>${escapeHtml(dayLabel(list[0].day))}</span><span>${escapeHtml(dayLabel(list[list.length - 1].day))}</span></div>`;
}

function rowHtml(name, sub, d, max) {
  const v = tokensOf(d);
  const w = max ? Math.max(1.5, (v / max) * 100) : 0;
  const cp = cachePct(d);
  return `<div class="us-row" role="row">
    <span class="us-name" role="cell"><span class="us-name-main" title="${escapeHtml(name)}">${escapeHtml(name)}</span>${sub ? `<span class="us-name-sub">${escapeHtml(sub)}</span>` : ''}</span>
    <span class="us-num" role="cell" title="${escapeHtml(`${num(d.requests)} requests`)}">${escapeHtml(num(d.requests))}</span>
    <span class="us-tok" role="cell" title="${escapeHtml(tokenTitle(d))}">
      <span class="us-tok-top"><b>${escapeHtml(formatCount(v))}</b>${cp ? `<em>⚡${cp}%</em>` : ''}</span>
      <span class="us-meter"><i style="width:${w.toFixed(1)}%"></i></span>
    </span>
    <span class="us-cost" role="cell">${costHtml(d)}</span>
  </div>`;
}

function tableHtml(title, rows, nameOf, subOf, { limit = 0, more = '' } = {}) {
  if (!rows.length) return '';
  const max = Math.max(1, ...rows.map(tokensOf));
  const shown = limit && rows.length > limit ? rows.slice(0, limit) : rows;
  const right = limit && rows.length > limit
    ? `<button type="button" class="link-btn" data-act="${more}">Show all ${rows.length}</button>`
    : '';
  return `${section(title, { count: rows.length, right })}
    <div class="us-table" role="table" aria-label="${escapeHtml(title)}">
      <div class="us-row us-head" role="row"><span role="columnheader">${title === 'By model' ? 'Model' : 'Provider'}</span><span role="columnheader">Requests</span><span role="columnheader">Tokens</span><span role="columnheader">Cost</span></div>
      ${shown.map((r) => rowHtml(nameOf(r), subOf(r), r, max)).join('')}
    </div>`;
}

function renderBody() {
  const el = $('us-body');
  if (!el) return;
  if (!data) {
    patch(el, error
      ? empty(error === STALE_SERVER ? 'Restart Jarvis to see usage' : 'Couldn’t load usage', escapeHtml(error), { ic: 'circle-alert' })
      : `<div class="dlg-empty">${loading ? '<span class="spinner" aria-hidden="true"></span>' : ''}<p>Loading usage…</p></div>`);
    return;
  }
  if (!data.enabled) {
    patch(el, empty('Usage tracking is off', 'HARNESS_USAGE_LOG=0 is set, so requests aren’t recorded.', { ic: 'gauge' }));
    return;
  }
  const t = data.totals || {};
  if (!t.requests) {
    patch(el, chatHtml(data.chat) + empty('No requests in this period',
      'Every model request Jarvis makes is counted here: chats, subagents, any provider.', { ic: 'chart-column' }));
    return;
  }
  patch(el, [
    chatHtml(data.chat),
    chartHtml(),
    tableHtml('By provider', data.providers || [], (r) => r.label || r.provider, () => ''),
    tableHtml('By model', data.models || [], (r) => r.model_label || r.model || '—',
      (r) => [r.provider_label || r.provider, r.model_label && r.model_label !== r.model ? r.model : ''].filter(Boolean).join(' · '),
      { limit: allModels ? 0 : MODELS_SHOWN, more: 'all-models' }),
  ].join(''));
}

function renderBar() {
  const el = $('us-bar');
  if (!el) return;
  patch(el, `<div class="us-range">${seg(RANGES, String(days), { label: 'Period', act: 'range' })}
    <button type="button" class="icon-btn or-icon us-refresh" data-act="refresh" aria-label="Refresh" title="Refresh">${icon('refresh-cw')}</button></div>`);
}

function renderFoot() {
  const el = $('us-foot');
  if (!el) return;
  const since = data?.first_ts ? new Date(data.first_ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) : '';
  const note = `${since ? `Counted since ${escapeHtml(since)}. ` : ''}Plan value is the API price of requests a subscription covers. Claude Code’s figure is its own estimate.`;
  patch(el, footer({ note, noteIc: 'info', hints: [['esc', 'close']] }));
}

function render() {
  renderBar();
  renderStats();
  renderBody();
  renderFoot();
}

// ─── Data ────────────────────────────────────────────────────────────────

async function load() {
  const my = ++seq;
  loading = true;
  spinning(true);
  let res = null;
  let err = '';
  try {
    res = await api(`/api/usage/summary?days=${days}`);
  } catch (e) {
    // 404: the page is newer than the Jarvis serving it (static files load
    // from disk, Python routes only at start) — it needs a restart.
    err = e?.status === 404 ? STALE_SERVER : e?.message || 'Request failed';
  }
  if (my !== seq) return; // a newer request (another period) is on its way
  loading = false;
  spinning(false);
  if (res) data = res;
  error = err;
  if (isModalOpen(ID)) render();
}

/** The refresh button spins while asking (toggled in place: re-rendering the
 * period buttons would drop keyboard focus on every poll). */
function spinning(on) {
  $(ID)?.querySelector('.us-refresh')?.classList.toggle('is-spinning', on);
}

function setDays(v) {
  const n = Number(v);
  if (!RANGES.some((r) => Number(r.value) === n) || n === days) return;
  days = n;
  storageSet(RANGE_KEY, String(n));
  data = null;
  allModels = false;
  render();
  load();
}

function startPoll() {
  stopPoll();
  poll = setInterval(() => {
    if (!document.hidden && !loading && isModalOpen(ID)) load();
  }, POLL_MS);
}

function stopPoll() {
  if (poll) clearInterval(poll);
  poll = 0;
}

function onClick(e) {
  const el = e.target.closest('[data-act]');
  const card = $(ID);
  if (!el || !card?.contains(el) || el.disabled) return;
  switch (el.dataset.act) {
    case 'range': setDays(el.dataset.val); break;
    case 'refresh': load(); break;
    case 'all-models':
      allModels = true;
      renderBody();
      break;
    default: break;
  }
}

export function openUsageStats() {
  const saved = Number(storageGet(RANGE_KEY, '7'));
  days = RANGES.some((r) => Number(r.value) === saved) ? saved : 7;
  allModels = false;
  render();
  openModal(ID, { onClose: stopPoll });
  startPoll();
  load();
}

export function initUsageStats() {
  $(ID)?.addEventListener('click', onClick);
}
