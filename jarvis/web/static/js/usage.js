/** OpenRouter spend: the sidebar card and the `/usage` chat card's markup.
 *
 * Data is `store.openrouter` (server: jarvis/auth/openrouter_usage.py
 * `public()`, pushed with every `state`), null while OpenRouter isn't the
 * provider — then the card hides. Click the card to fold out the details.
 */
import { $, escapeHtml, storageGet, storageSet, showToast } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe, patchStore } from './store.js';
import { api } from './api.js';

const OPEN_KEY = 'jarvis-or-open';
const PERIOD_LABEL = { daily: 'daily', weekly: 'weekly', monthly: 'monthly' };

// ─── Formatting (mirrors openrouter_usage.money / when) ───────────────────

/** 20 → "$20", 17.897 → "$17.90", 0.004 → "<$0.01", null → "—". */
export function money(v) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  if (n === 0) return '$0';
  if (Math.abs(n) < 0.01) return '<$0.01';
  if (Math.abs(n - Math.round(n)) < 0.005) return `$${Math.round(n).toLocaleString()}`;
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** ISO date → { day: "Nov 1", rel: "in 3 days" } in this device's time zone. */
export function resetWhen(iso) {
  const at = iso ? new Date(iso) : null;
  if (!at || Number.isNaN(at.getTime())) return null;
  const day = at.toLocaleDateString([], { month: 'short', day: 'numeric' });
  const secs = (at.getTime() - Date.now()) / 1000 - (store.skew || 0);
  let rel = '';
  if (secs > 0) {
    if (secs < 3600) rel = `in ${Math.max(1, Math.floor(secs / 60))}m`;
    else if (secs < 86400) rel = `in ${Math.floor(secs / 3600)}h`;
    else {
      const days = Math.round(secs / 86400);
      rel = days === 1 ? 'tomorrow' : `in ${days} days`;
    }
  }
  return { day, rel };
}

function updatedAgo(ts) {
  if (!ts) return '';
  const now = Date.now() / 1000 + (store.skew || 0);
  const s = Math.max(0, Math.floor(now - ts));
  if (s < 10) return 'Updated just now';
  if (s < 60) return `Updated ${s}s ago`;
  if (s < 3600) return `Updated ${Math.floor(s / 60)}m ago`;
  return `Updated ${Math.floor(s / 3600)}h ago`;
}

/** ok · warn · low for what's left (key limit, else account credit). */
export function leftLevel(d) {
  if (d.limit && d.remaining != null) {
    const share = d.remaining / d.limit;
    return share >= 0.4 ? 'ok' : share >= 0.15 ? 'warn' : 'low';
  }
  const bal = d.credits?.balance;
  if (bal != null) return bal >= 5 ? 'ok' : bal >= 1 ? 'warn' : 'low';
  return 'ok';
}

/** Share of the limit spent, 0–100 (any spend shows at least a sliver). */
function spentPct(d) {
  const f = Math.max(0, Math.min(1, Number(d.fraction) || 0));
  return f > 0 ? Math.max(1.5, f * 100) : 0;
}

/** "label value" rows shared by the sidebar details and the chat card. */
function detailRows(d) {
  const rows = [];
  const credits = d.credits;
  if (credits) {
    rows.push({
      ic: 'credit-card',
      label: 'Account credit',
      value: money(credits.balance),
      sub: `${money(credits.used)} of ${money(credits.total)} used`,
      tone: credits.balance < 1 ? 'low' : credits.balance < 5 ? 'warn' : '',
    });
  }
  const free = d.free_requests;
  if (free && (free.used || d.free_tier)) {
    rows.push({ ic: 'zap', label: 'Free requests', value: `${free.used.toLocaleString()} / ${free.limit.toLocaleString()}`, sub: 'today' });
  }
  const byok = d.byok;
  if (byok) {
    rows.push({ ic: 'key-round', label: 'Your own keys', value: money(byok.month), sub: `this month · ${money(byok.total)} all time` });
  }
  if (d.expires_at) {
    const w = resetWhen(d.expires_at);
    rows.push({ ic: 'hourglass', label: 'Key expires', value: w ? w.day : String(d.expires_at), sub: w?.rel || '', tone: 'warn' });
  }
  return rows;
}

// ─── Sidebar card ─────────────────────────────────────────────────────────

let lastSig = '';

function renderCard(s) {
  const card = $('or-card');
  if (!card) return;
  const d = s.openrouter;
  card.hidden = !d;
  if (!d) {
    lastSig = '';
    return;
  }
  const sig = JSON.stringify([d.status, d.error, d.loading, d.fetched_at, d.limit, d.remaining, d.spent, d.usage, d.credits, d.free_requests, d.byok]);
  // "Updated 2m ago" still ages while nothing else changes.
  $('or-updated').textContent = d.status === 'ok' ? updatedAgo(d.fetched_at) : '';
  card.classList.toggle('is-loading', !!d.loading);
  if (sig === lastSig) return;
  lastSig = sig;

  const left = $('or-left');
  const spent = $('or-spent');
  const reset = $('or-reset');
  const bar = $('or-bar');
  const fill = $('or-fill');
  card.dataset.state = d.status;
  card.dataset.level = d.status === 'ok' ? leftLevel(d) : '';

  if (d.status !== 'ok') {
    left.textContent = d.status === 'loading' ? 'Checking…' : 'Unavailable';
    spent.textContent = d.status === 'error' ? (d.error || 'Couldn’t read usage') : 'Asking OpenRouter';
    reset.textContent = '';
    bar.hidden = false;
    fill.style.width = '0%';
    $('or-grid').innerHTML = '';
    return;
  }

  const usage = d.usage || {};
  if (d.limit) {
    left.innerHTML = `<b>${escapeHtml(money(d.remaining))}</b> left`;
    spent.textContent = `${money(d.spent)} of ${money(d.limit)}${d.reset ? ` · ${PERIOD_LABEL[d.reset]}` : ''}`;
    const w = d.reset ? resetWhen(d.resets_at) : null;
    reset.textContent = w ? `Resets ${w.day}` : '';
    reset.title = w?.rel ? `Resets ${w.day} (${w.rel})` : '';
    bar.hidden = false;
    fill.style.width = `${spentPct(d).toFixed(2)}%`;
    bar.setAttribute('aria-label', `${money(d.spent)} of ${money(d.limit)} spent`);
  } else {
    left.innerHTML = `<b>${escapeHtml(money(usage.total))}</b> spent`;
    spent.textContent = 'No spending limit on this key';
    reset.textContent = '';
    bar.hidden = true;
  }

  const cell = (label, v) => `<div class="or-cell"><dt>${label}</dt><dd>${escapeHtml(money(v))}</dd></div>`;
  const rows = detailRows(d).map((r) => `
    <div class="or-row${r.tone ? ` is-${r.tone}` : ''}">
      <dt>${icon(r.ic)}${escapeHtml(r.label)}</dt>
      <dd><b>${escapeHtml(r.value)}</b>${r.sub ? `<span>${escapeHtml(r.sub)}</span>` : ''}</dd>
    </div>`).join('');
  $('or-grid').innerHTML = `
    <div class="or-cells">
      ${cell('Today', usage.day)}${cell('Week', usage.week)}${cell('Month', usage.month)}${cell('All time', usage.total)}
    </div>
    ${rows}`;
}

async function refreshNow(btn) {
  btn.classList.add('is-spinning');
  btn.disabled = true;
  try {
    const res = await api('/api/usage?refresh=1');
    if (res?.openrouter && store.openrouter) patchStore({ openrouter: res.openrouter });
    if (res?.openrouter?.error) showToast(res.openrouter.error, true);
  } catch (err) {
    showToast(err.message || 'Couldn’t refresh usage', true);
  } finally {
    btn.classList.remove('is-spinning');
    btn.disabled = false;
  }
}

function setOpen(open) {
  const card = $('or-card');
  card.classList.toggle('is-open', open);
  $('or-main').setAttribute('aria-expanded', String(open));
  storageSet(OPEN_KEY, open ? '1' : '0');
}

export function initUsageCard() {
  const card = $('or-card');
  if (!card) return;
  setOpen(storageGet(OPEN_KEY, '0') === '1');
  $('or-main').addEventListener('click', () => setOpen(!card.classList.contains('is-open')));
  $('or-refresh').addEventListener('click', (e) => { e.stopPropagation(); refreshNow(e.currentTarget); });
  subscribe(renderCard);
  renderCard(store);
  // Keep "Updated 2m ago" honest between pushes.
  setInterval(() => {
    const d = store.openrouter;
    if (d?.status === 'ok' && !card.hidden) $('or-updated').textContent = updatedAgo(d.fetched_at);
  }, 15000);
}

// ─── /usage chat card ─────────────────────────────────────────────────────

/** Same lines as the terminal panel, for Copy. */
export function usageText(d) {
  const u = d.usage || {};
  const lines = ['OpenRouter usage'];
  if (d.status === 'error' && !d.usage) return `${lines[0]}\n${d.error || 'Couldn’t read usage'}`;
  if (d.limit) {
    const w = d.reset ? resetWhen(d.resets_at) : null;
    lines.push(`Left         ${money(d.remaining)} of ${money(d.limit)} (${d.reset || 'total'} limit)`);
    lines.push(`Spent        ${money(d.spent)} ${d.period || ''}${w ? ` · resets ${w.day}${w.rel ? ` (${w.rel})` : ''}` : ''}`.trimEnd());
  } else {
    lines.push('Limit        none');
  }
  lines.push(`Spend        today ${money(u.day)} · week ${money(u.week)} · month ${money(u.month)} · all time ${money(u.total)}`);
  for (const r of detailRows(d)) lines.push(`${r.label.padEnd(12)} ${r.value}${r.sub ? ` (${r.sub})` : ''}`);
  if (d.label) lines.push(`Key          ${d.label}`);
  return lines.join('\n');
}

/** The card's inner HTML (chat.js wraps it like the /stats card). */
export function usageCardHtml(d) {
  const at = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const sub = [d.label ? `Key ${d.label}` : '', `As of ${at}`].filter(Boolean).join(' · ');
  const head = `
    <header class="stats-head">
      <span class="stats-glyph" aria-hidden="true">${icon('gauge')}</span>
      <div class="stats-title">
        <strong>OpenRouter usage</strong>
        <span>${escapeHtml(sub)}</span>
      </div>
      <a class="ou-link" href="${escapeHtml(d.activity_url || 'https://openrouter.ai/activity')}" target="_blank" rel="noopener">Activity${icon('external-link')}</a>
    </header>`;
  if (d.status === 'error' && !d.usage) {
    return `${head}<div class="ou-hero is-error"><p>${escapeHtml(d.error || 'Couldn’t read usage')}</p><span>Check the key with /provider, or open your activity on OpenRouter.</span></div>`;
  }
  const u = d.usage || {};
  const level = leftLevel(d);
  let hero;
  if (d.limit) {
    const w = d.reset ? resetWhen(d.resets_at) : null;
    const pct = Math.round((Number(d.fraction) || 0) * 100);
    hero = `
      <div class="ou-hero" data-level="${level}">
        <div class="ou-figure">
          <span class="ou-big">${escapeHtml(money(d.remaining))}</span>
          <span class="ou-of">left of ${escapeHtml(money(d.limit))} <em>${escapeHtml(d.reset ? `${PERIOD_LABEL[d.reset]} limit` : 'limit')}</em></span>
          <span class="ou-pct">${pct}% used</span>
        </div>
        <div class="ou-bar" role="img" aria-label="${escapeHtml(`${money(d.spent)} of ${money(d.limit)} spent`)}"><i style="width:${spentPct(d).toFixed(2)}%"></i></div>
        <div class="ou-legend">
          <span><b>${escapeHtml(money(d.spent))}</b> spent ${escapeHtml(d.period || '')}</span>
          ${w ? `<span>Resets <b>${escapeHtml(w.day)}</b>${w.rel ? ` · ${escapeHtml(w.rel)}` : ''}</span>` : ''}
        </div>
      </div>`;
  } else {
    hero = `
      <div class="ou-hero" data-level="${level}">
        <div class="ou-figure">
          <span class="ou-big">${escapeHtml(money(u.total))}</span>
          <span class="ou-of">spent on this key <em>no spending limit</em></span>
        </div>
      </div>`;
  }
  const tile = (ic, label, v) => `
    <div class="stat">
      <span class="stat-label">${icon(ic)}${escapeHtml(label)}</span>
      <span class="stat-value">${escapeHtml(money(v))}</span>
    </div>`;
  const rows = detailRows(d).map((r) => `
    <div class="stats-row${r.tone ? ` is-${r.tone}` : ''}">
      <dt>${icon(r.ic)}${escapeHtml(r.label)}</dt>
      <dd><b class="ou-val">${escapeHtml(r.value)}</b>${r.sub ? `<span class="stats-sub">${escapeHtml(r.sub)}</span>` : ''}</dd>
    </div>`).join('');
  return `${head}${hero}
    <div class="stats-grid">
      ${tile('timer', 'Today', u.day)}
      ${tile('history', 'This week', u.week)}
      ${tile('chart-column', 'This month', u.month)}
      ${tile('activity', 'All time', u.total)}
    </div>
    ${rows ? `<dl class="stats-meta">${rows}</dl>` : ''}
    ${d.error ? `<p class="ou-stale">${icon('triangle-alert')}${escapeHtml(d.error)} — showing the last numbers.</p>` : ''}`;
}
