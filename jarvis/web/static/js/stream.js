/** Live stream + status bar updates from SSE */
import { $ } from './utils.js';
import { patchStore, store } from './store.js';
import {
  appendMessage,
  clearChat,
  pushStreamDelta,
  removeStreamBubble,
  normalizeRole,
  refreshThinkingVisibility,
} from './chat.js';

export function setStatusLabel(text) {
  const label = (text || '').trim();
  if (!label) return;
  patchStore({ statusLabel: label });
  syncStatusChip();
}

export function setBusy(next) {
  patchStore({ busy: !!next });
  if (!next && store.connected) {
    patchStore({ statusLabel: 'Ready' });
  } else if (next && store.statusLabel === 'Ready') {
    patchStore({ statusLabel: 'Working…' });
  }
  syncStatusChip();
  syncComposerState();
}

export function setConnected(next) {
  patchStore({ connected: !!next });
  if (!next) {
    patchStore({ statusLabel: 'Offline' });
  } else if (!store.busy) {
    patchStore({ statusLabel: 'Ready' });
  }
  syncStatusChip();
  $('reconnect-banner')?.classList.toggle('show', !next);
}

function syncStatusChip() {
  const chip = $('status-chip');
  const label = $('status-label');
  if (!chip || !label) return;

  const text = !store.connected ? 'Offline' : store.statusLabel || (store.busy ? 'Working…' : 'Ready');

  chip.classList.toggle('busy', store.busy);
  chip.classList.toggle('offline', !store.connected);
  label.textContent = text;

  const drawerStatus = $('drawer-status');
  if (drawerStatus) drawerStatus.textContent = text;

  syncComposerStatus(text);
}

function syncComposerStatus(text) {
  const bar = $('composer-status');
  const label = $('composer-status-text');
  if (!bar || !label) return;

  const show = store.busy || !store.connected;
  const displayText = text || (store.busy ? 'Working…' : 'Ready');

  bar.classList.toggle('show', show);
  bar.classList.toggle('busy', store.busy);
  bar.classList.toggle('offline', !store.connected);
  label.textContent = displayText;
}

function syncComposerState() {
  const send = $('send');
  const cancel = $('cancel');
  const prompt = $('prompt');
  if (cancel) cancel.classList.toggle('show', store.busy);
  if (send) send.disabled = store.pendingAction;
  if (prompt) prompt.disabled = store.pendingAction;
}

export function setQueue(items) {
  patchStore({ queue: items || [] });
  const banner = $('queue-banner');
  const summary = $('queue-summary');
  const list = $('queue-list');
  if (!banner || !summary || !list) return;

  if (!items?.length) {
    banner.classList.remove('show', 'expanded');
    return;
  }

  banner.classList.add('show');
  summary.textContent = `${items.length} message${items.length === 1 ? '' : 's'} queued`;
  list.innerHTML = items
    .map((t, i) => `<div class="queue-item"><strong>#${i + 1}</strong> ${escapeHtml(t)}</div>`)
    .join('');
}

export function handleStreamEvent(type, data) {
  switch (type) {
    case 'stream_start':
      removeStreamBubble();
      break;
    case 'stream_delta':
      pushStreamDelta(data.kind, data.chunk);
      break;
    case 'stream_end':
      removeStreamBubble();
      break;
    default:
      break;
  }
}

export function applySessionData(data) {
  clearChat();
  (data.messages || []).forEach((m) => {
    appendMessage(normalizeRole(m.role), m.text, m.title || m.role);
  });
  refreshThinkingVisibility();
  setBusy(!!data.busy);
  setQueue(data.queue || []);
  if (data.status) setStatusLabel(data.status);
}

export function handleSnapshot(data) {
  applySessionData(data);
}

export function bindQueueBanner() {
  $('queue-banner')?.addEventListener('click', () => {
    $('queue-banner')?.classList.toggle('expanded');
  });
}

export { syncComposerState };
