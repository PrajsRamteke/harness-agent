/** Connection, busy/activity state, queue chips, the top bar, favicon */
import { $, escapeHtml, formatElapsed, truncate, animateEl } from './utils.js';
import { icon } from './icons.js';
import { patchStore, store, subscribe } from './store.js';
import { settleTools, syncTyping, markTurnDone } from './chat.js';
import { replyReady, isAlertTitle } from './theme.js';
import { noteBusy } from './activity.js';

let elapsedTimer = 0;
let flashTimer = 0;
let backTimer = 0;

export function setStatusLabel(text) {
  const label = String(text || '').trim();
  if (!label || label === store.statusLabel) return;
  patchStore({ statusLabel: label });
}

/** After a turn the pill says how it went for a few seconds, then "Ready". */
function flashOutcome(since) {
  const secs = since ? (Date.now() - since) / 1000 : 0;
  const stopped = store.stopRequested;
  clearTimeout(flashTimer);
  if (!stopped && secs < 1) {
    patchStore({ doneFlash: null, stopRequested: false });
    return;
  }
  patchStore({
    doneFlash: stopped
      ? { label: 'Stopped', kind: 'stopped' }
      : { label: `Done in ${formatElapsed(secs)}`, kind: 'done' },
    stopRequested: false,
  });
  flashTimer = setTimeout(() => patchStore({ doneFlash: null }), 4200);
}

export function setBusy(next) {
  const busy = !!next;
  if (busy === store.busy) return;
  const since = store.busySince;
  const stopped = store.stopRequested;
  patchStore({
    busy,
    busySince: busy ? Date.now() : 0,
    statusLabel: busy ? 'Thinking' : '',
    ...(busy ? { doneFlash: null, stopRequested: false } : {}),
  });
  noteBusy(busy, { secs: since ? (Date.now() - since) / 1000 : 0, stopped });
  if (!busy) {
    settleTools();
    flashOutcome(since);
    markTurnDone();
    const replies = document.querySelectorAll('#chat .agent-text');
    const last = replies[replies.length - 1]?.dataset.raw || '';
    replyReady(truncate(last.replace(/\s+/g, ' '), 120));
  }
  syncTyping();
}

export function setConnected(next) {
  const connected = !!next;
  const wasDown = store.everConnected && !store.connected;
  patchStore({ connected, ...(connected ? { everConnected: true } : {}) });
  if (connected && wasDown) showBackOnline();
  syncTyping();
}

/** The red "reconnecting" banner turns green for a moment, then slides away. */
function showBackOnline() {
  const banner = $('conn-banner');
  if (!banner) return;
  clearTimeout(backTimer);
  banner.classList.remove('is-leaving');
  banner.classList.add('is-back');
  $('conn-banner-text').textContent = 'Back online';
  banner.hidden = false;
  backTimer = setTimeout(() => {
    banner.classList.add('is-leaving');
    backTimer = setTimeout(() => {
      banner.hidden = true;
      banner.classList.remove('is-back', 'is-leaving');
    }, 260);
  }, 1500);
}

export function setQueue(items) {
  patchStore({ queue: items || [] });
}

function statusText(s) {
  if (!s.connected) return s.everConnected ? 'Offline' : 'Connecting';
  if (s.activePrompt) return 'Waiting for you';
  if (s.busy) return s.statusLabel || 'Working';
  if (s.doneFlash) return s.doneFlash.label;
  return 'Ready';
}

/** Swap a label with a short slide so changes read as changes. */
function swapText(el, text) {
  if (!el || el.textContent === text) return;
  el.textContent = text;
  animateEl(el, [
    { opacity: 0, transform: 'translateY(5px)' },
    { opacity: 1, transform: 'none' },
  ], { duration: 240, easing: 'ease-out' });
}

// ─── Favicon: the tab shows what Jarvis is doing ──────────────────────────

let lastFavicon = '';

function syncFavicon(s) {
  const kind = !s.connected ? 'offline' : s.activePrompt ? 'wait' : s.busy ? 'busy' : 'idle';
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent-rgb').trim() || '74, 222, 128';
  const key = `${kind}|${accent}`;
  if (key === lastFavicon) return;
  lastFavicon = key;
  const dot = { offline: '#737373', wait: '#fbbf24' }[kind] || `rgb(${accent})`;
  const ring = kind === 'busy' || kind === 'wait'
    ? `<circle cx='16' cy='16' r='10' fill='none' stroke='${dot}' stroke-width='2' opacity='0.45'/>`
    : '';
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect x='1' y='1' width='30' height='30' rx='9' fill='#0b0b0b' stroke='#333'/>${ring}<circle cx='16' cy='16' r='5.5' fill='${dot}'/></svg>`;
  const link = document.querySelector('link[rel="icon"]');
  if (link) link.href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function sessionTitle(s) {
  const ss = s.session;
  if (ss.session_title) return ss.session_title;
  if (ss.message_count) return ss.session_id ? `Session ${ss.session_id}` : 'Untitled session';
  return 'New session';
}

function renderTop(s) {
  const pill = $('status-pill');
  const waiting = s.connected && !!s.activePrompt;
  const idle = s.connected && !s.busy && !waiting;
  if (pill) {
    pill.classList.toggle('is-busy', s.connected && s.busy && !waiting);
    pill.classList.toggle('is-offline', !s.connected);
    pill.classList.toggle('is-waiting', waiting);
    pill.classList.toggle('is-done', idle && s.doneFlash?.kind === 'done');
    pill.classList.toggle('is-stopped', idle && s.doneFlash?.kind === 'stopped');
    pill.title = statusText(s);
  }
  swapText($('status-text'), statusText(s));
  syncFavicon(s);

  const title = $('session-title');
  if (title) title.textContent = sessionTitle(s);
  const meta = $('session-meta');
  if (meta) {
    const parts = [];
    if (s.session.project) parts.push(`<span>${escapeHtml(s.session.project)}</span>`);
    if (s.session.session_id) parts.push(`<span>Session ${escapeHtml(String(s.session.session_id))}</span>`);
    meta.innerHTML = parts.join('');
  }
  if (!isAlertTitle()) {
    document.title = s.activePrompt
      ? `(!) Waiting for you — Jarvis`
      : s.busy ? `● ${sessionTitle(s)} — Jarvis` : `${sessionTitle(s)} — Jarvis`;
  }

  const conn = $('conn-line');
  if (conn) {
    conn.textContent = s.connected
      ? (s.session.project ? `Connected to ${s.session.project}` : 'Connected')
      : s.everConnected ? 'Reconnecting…' : 'Connecting…';
    conn.classList.toggle('is-live', s.connected);
    conn.classList.toggle('is-down', !s.connected && s.everConnected);
  }
  const banner = $('conn-banner');
  // Only once we've been connected: the first connect isn't a lost connection.
  if (banner) {
    if (!s.connected && s.everConnected) {
      clearTimeout(backTimer);
      banner.classList.remove('is-back', 'is-leaving');
      $('conn-banner-text').textContent = 'Lost connection to Jarvis. Reconnecting…';
      banner.hidden = false;
    } else if (!banner.classList.contains('is-back')) {
      banner.hidden = true;
    }
  }

  document.body.classList.toggle('is-busy', s.connected && s.busy);
  document.body.classList.toggle('is-offline', !s.connected);
}

function renderActivity(s) {
  const bar = $('activity');
  if (!bar) return;
  const show = s.connected && s.busy;
  bar.hidden = !show;
  swapText($('activity-text'), s.statusLabel || 'Working');
  tickElapsed();
  if (show && !elapsedTimer) {
    elapsedTimer = setInterval(tickElapsed, 1000);
  } else if (!show && elapsedTimer) {
    clearInterval(elapsedTimer);
    elapsedTimer = 0;
  }
}

function tickElapsed() {
  const el = $('activity-time');
  if (!el) return;
  el.textContent = store.busy && store.busySince ? formatElapsed((Date.now() - store.busySince) / 1000) : '';
}

let lastQueueSig = '';

function renderQueue(s) {
  const box = $('queue-chips');
  if (!box) return;
  const items = s.queue || [];
  const sig = items.join('\u0000');
  if (sig === lastQueueSig) return;
  lastQueueSig = sig;
  if (!items.length) {
    box.innerHTML = '';
    return;
  }
  const shown = items.slice(0, 2).map((t, i) => `
    <div class="dock-chip is-queued" title="${escapeHtml(t)}">
      <span class="dock-chip-num">${i + 1}</span>
      <span class="dock-chip-text">${escapeHtml(truncate(t, 60))}</span>
    </div>`);
  if (items.length > 2) {
    shown.push(`<div class="dock-chip is-queued"><span class="dock-chip-text">${items.length - 2} more queued</span></div>`);
  }
  box.innerHTML = `
    <div class="dock-chip is-queued" aria-label="Queued messages">
      ${icon('list-ordered')}<span class="dock-chip-text">Sends when Jarvis is free</span>
    </div>${shown.join('')}`;
}

export function initStatus() {
  subscribe((s) => {
    renderTop(s);
    renderActivity(s);
    renderQueue(s);
  });
  renderTop(store);
  renderActivity(store);
}
