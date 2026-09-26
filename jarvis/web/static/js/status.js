/** Connection, busy/activity state, queue chips and the top bar */
import { $, escapeHtml, formatElapsed, truncate } from './utils.js';
import { icon } from './icons.js';
import { patchStore, store, subscribe } from './store.js';
import { settleTools, syncTyping } from './chat.js';
import { replyReady, isAlertTitle } from './theme.js';

let elapsedTimer = 0;

export function setStatusLabel(text) {
  const label = String(text || '').trim();
  if (!label || label === store.statusLabel) return;
  patchStore({ statusLabel: label });
}

export function setBusy(next) {
  const busy = !!next;
  if (busy === store.busy) return;
  patchStore({
    busy,
    busySince: busy ? Date.now() : 0,
    statusLabel: busy ? 'Thinking' : '',
  });
  if (!busy) {
    settleTools();
    const replies = document.querySelectorAll('#chat .agent-text');
    const last = replies[replies.length - 1]?.dataset.raw || '';
    replyReady(truncate(last.replace(/\s+/g, ' '), 120));
  }
  syncTyping();
}

export function setConnected(next) {
  patchStore({ connected: !!next });
  syncTyping();
}

export function setQueue(items) {
  patchStore({ queue: items || [] });
}

function statusText(s) {
  if (!s.connected) return 'Offline';
  if (s.busy) return s.statusLabel || 'Working';
  return 'Ready';
}

function sessionTitle(s) {
  const ss = s.session;
  if (ss.session_title) return ss.session_title;
  if (ss.message_count) return ss.session_id ? `Session ${ss.session_id}` : 'Untitled session';
  return 'New session';
}

function renderTop(s) {
  const pill = $('status-pill');
  if (pill) {
    pill.classList.toggle('is-busy', s.connected && s.busy);
    pill.classList.toggle('is-offline', !s.connected);
    pill.title = statusText(s);
  }
  const text = $('status-text');
  if (text) text.textContent = statusText(s);

  const title = $('session-title');
  if (title) title.textContent = sessionTitle(s);
  const meta = $('session-meta');
  if (meta) {
    const parts = [];
    if (s.session.project) parts.push(`<span>${escapeHtml(s.session.project)}</span>`);
    if (s.session.session_id) parts.push(`<span>Session ${escapeHtml(String(s.session.session_id))}</span>`);
    meta.innerHTML = parts.join('');
  }
  if (!isAlertTitle()) document.title = s.busy ? `● ${sessionTitle(s)} — Jarvis` : `${sessionTitle(s)} — Jarvis`;

  const conn = $('conn-line');
  if (conn) {
    conn.textContent = s.connected
      ? (s.session.project ? `Connected to ${s.session.project}` : 'Connected')
      : 'Reconnecting…';
    conn.classList.toggle('is-live', s.connected);
    conn.classList.toggle('is-down', !s.connected);
  }
  const banner = $('conn-banner');
  if (banner) banner.hidden = s.connected;

  document.body.classList.toggle('is-busy', s.connected && s.busy);
  document.body.classList.toggle('is-offline', !s.connected);
}

function renderActivity(s) {
  const bar = $('activity');
  if (!bar) return;
  const show = s.connected && s.busy;
  bar.hidden = !show;
  $('activity-text').textContent = s.statusLabel || 'Working';
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
