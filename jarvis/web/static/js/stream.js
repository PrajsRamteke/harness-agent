/** Live stream + status bar updates from SSE */
import { $, escapeHtml } from './utils.js';
import { patchStore, store } from './store.js';
import {
  appendMessage,
  clearChat,
  pushStreamDelta,
  removeStreamBubble,
  normalizeRole,
  refreshThinkingVisibility,
} from './chat.js';

export function setBusy(next) {
  patchStore({ busy: !!next });
  syncStatusChip();
  syncComposerState();
}

export function setConnected(next) {
  patchStore({ connected: !!next });
  syncStatusChip();
  $('reconnect-banner')?.classList.toggle('show', !next);
}

function syncStatusChip() {
  const chip = $('status-chip');
  const label = $('status-label');
  if (!chip || !label) return;

  chip.classList.toggle('busy', store.busy);
  chip.classList.toggle('offline', !store.connected);

  if (!store.connected) label.textContent = 'Offline';
  else if (store.busy) label.textContent = 'Working';
  else label.textContent = 'Ready';

  const drawerStatus = $('drawer-status');
  if (drawerStatus) drawerStatus.textContent = label.textContent;
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
  if (data.status) {
    const label = $('status-label');
    if (label) label.textContent = data.status;
  }
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
