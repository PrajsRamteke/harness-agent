/** Claude plan usage: the sidebar card while the Claude Code CLI is the provider.
 *
 * Data is `store.claude_usage` (server: jarvis/auth/claude_agent_usage.py
 * `public()`, pushed with every `state`) — what Claude Code's own /usage shows,
 * asked of the user's `claude` (never with Jarvis holding a credential). Null
 * while another provider is in use: the card hides. Same look as the
 * OpenRouter card (usage.js); click to fold out the weekly limits.
 */
import { $, escapeHtml, storageGet, storageSet, showToast } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe, patchStore } from './store.js';
import { api } from './api.js';

const OPEN_KEY = 'jarvis-cc-usage-open';

/** ISO → "3:20 pm" today, "Thu 5:00 am" this week, else "Nov 6". */
export function resetLabel(iso) {
  const at = iso ? new Date(iso) : null;
  if (!at || Number.isNaN(at.getTime())) return '';
  const secs = (at.getTime() - Date.now()) / 1000 - (store.skew || 0);
  const time = at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (secs <= 0) return 'resetting';
  if (secs < 3600) return `in ${Math.max(1, Math.floor(secs / 60))}m`;
  if (secs < 20 * 3600 && at.getDate() === new Date().getDate()) return time;
  if (secs < 6 * 86400) return `${at.toLocaleDateString([], { weekday: 'short' })} ${time}`;
  return at.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function updatedAgo(ts) {
  if (!ts) return '';
  const s = Math.max(0, Math.floor(Date.now() / 1000 + (store.skew || 0) - ts));
  if (s < 10) return 'Updated just now';
  if (s < 60) return `Updated ${s}s ago`;
  if (s < 3600) return `Updated ${Math.floor(s / 60)}m ago`;
  return `Updated ${Math.floor(s / 3600)}h ago`;
}

const pctWidth = (p) => (p > 0 ? Math.max(1.5, Math.min(100, p)) : 0);

function money(v, currency) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  try {
    return Number(v).toLocaleString(undefined, { style: 'currency', currency: currency || 'USD' });
  } catch {
    return `$${Number(v).toFixed(2)}`;
  }
}

let lastSig = '';

function render(s) {
  const card = $('cc-card');
  if (!card) return;
  const d = s.claude_usage;
  card.hidden = !d;
  if (!d) {
    lastSig = '';
    return;
  }
  $('cc-updated').textContent = d.status === 'ok' && d.fetched_at ? updatedAgo(d.fetched_at) : '';
  card.classList.toggle('is-loading', !!d.loading);
  const sig = JSON.stringify([d.status, d.error, d.plan_label, d.limits, d.extra, d.available]);
  if (sig === lastSig) return;
  lastSig = sig;

  $('cc-name').textContent = d.plan_label || 'Claude plan';
  const sess = d.session;
  const left = $('cc-left');
  const sub = $('cc-sub');
  const reset = $('cc-reset');
  const bar = $('cc-bar');
  const fill = $('cc-fill');
  card.dataset.state = d.status === 'ok' || sess ? 'ok' : d.status;
  card.dataset.level = sess?.level || '';

  if (!sess) {
    if (d.status === 'ok' && !d.available) {
      left.innerHTML = '';
      sub.textContent = 'No plan limits — billed per use';
      bar.hidden = true;
    } else {
      left.textContent = d.status === 'error' ? 'Unavailable' : 'Checking…';
      sub.textContent = d.status === 'error' ? (d.error || 'Couldn’t read usage') : 'Asking Claude Code';
      bar.hidden = false;
      fill.style.width = '0%';
    }
    reset.textContent = '';
  } else {
    left.innerHTML = `<b>${escapeHtml(String(sess.percent ?? '—'))}%</b> used`;
    sub.textContent = '5-hour session';
    const r = resetLabel(sess.resets_at);
    reset.textContent = r ? `Resets ${r}` : '';
    reset.title = sess.resets_at ? new Date(sess.resets_at).toLocaleString() : '';
    bar.hidden = false;
    fill.style.width = `${pctWidth(sess.percent || 0).toFixed(2)}%`;
    bar.setAttribute('aria-label', `${sess.percent}% of the current session used`);
  }

  const rows = (d.limits || []).filter((l) => l.kind !== 'session').map((l) => {
    const r = resetLabel(l.resets_at);
    return `<div class="cc-limit" data-level="${escapeHtml(l.level || 'ok')}">
        <div class="cc-limit-top"><dt>${escapeHtml(l.label)}</dt><dd>${l.percent ?? '—'}%</dd></div>
        <span class="or-bar" role="img" aria-label="${escapeHtml(`${l.label}: ${l.percent}% used`)}"><i style="width:${pctWidth(l.percent || 0).toFixed(2)}%"></i></span>
        ${r ? `<span class="cc-limit-sub">Resets ${escapeHtml(r)}</span>` : ''}
      </div>`;
  });
  const x = d.extra;
  if (x) {
    rows.push(`<div class="cc-limit" data-level="${x.percent >= 90 ? 'low' : x.percent >= 70 ? 'warn' : 'ok'}">
        <div class="cc-limit-top"><dt>${icon('credit-card')} Extra usage</dt><dd>${escapeHtml(money(x.used, x.currency))}${x.limit != null ? ` / ${escapeHtml(money(x.limit, x.currency))}` : ''}</dd></div>
        ${x.percent != null ? `<span class="or-bar"><i style="width:${pctWidth(x.percent).toFixed(2)}%"></i></span>` : ''}
      </div>`);
  }
  if (d.error && sess) rows.push(`<p class="cc-note">${escapeHtml(d.error)} — showing the last numbers.</p>`);
  if (!rows.length) rows.push('<p class="cc-note">No weekly limits reported for this plan.</p>');
  $('cc-grid').innerHTML = rows.join('');
}

async function refreshNow(btn) {
  btn.classList.add('is-spinning');
  btn.disabled = true;
  try {
    const res = await api('/api/usage?refresh=1&provider=claude');
    if (res?.claude && store.claude_usage) patchStore({ claude_usage: res.claude });
    if (res?.claude?.error) showToast(res.claude.error, true);
  } catch (err) {
    showToast(err.message || 'Couldn’t refresh usage', true);
  } finally {
    btn.classList.remove('is-spinning');
    btn.disabled = false;
  }
}

function setOpen(open) {
  const card = $('cc-card');
  card.classList.toggle('is-open', open);
  $('cc-main').setAttribute('aria-expanded', String(open));
  storageSet(OPEN_KEY, open ? '1' : '0');
}

export function initClaudeUsageCard() {
  const card = $('cc-card');
  if (!card) return;
  setOpen(storageGet(OPEN_KEY, '0') === '1');
  $('cc-main').addEventListener('click', () => setOpen(!card.classList.contains('is-open')));
  $('cc-refresh').addEventListener('click', (e) => { e.stopPropagation(); refreshNow(e.currentTarget); });
  subscribe(render);
  render(store);
  // Reset times ("in 12m") and "Updated 2m ago" age between pushes.
  setInterval(() => {
    if (card.hidden) return;
    lastSig = '';
    render(store);
  }, 30000);
}
