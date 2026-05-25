/** Message composer */
import { $, showToast } from './utils.js';
import { patchStore, store } from './store.js';
import { sendPrompt, cancelTurn, hasToken } from './api.js';
import { syncComposerState } from './stream.js';

export function autoResizePrompt() {
  const el = $('prompt');
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
}

export async function submitPrompt(text) {
  if (!hasToken()) {
    showToast('Missing session token in URL', true);
    return;
  }

  const el = $('prompt');
  const value = (text ?? el?.value ?? '').trim();
  if (!value) return;

  patchStore({ pendingAction: true });
  syncComposerState();
  if (el) {
    el.value = '';
    autoResizePrompt();
  }

  try {
    await sendPrompt(value);
  } catch {
    if (el) el.value = value;
    showToast('Failed to send message', true);
  } finally {
    patchStore({ pendingAction: false });
    syncComposerState();
  }
}

export async function cancelPrompt() {
  try {
    await cancelTurn();
  } catch {
    showToast('Cancel failed', true);
  }
}

export function initComposer() {
  const el = $('prompt');
  el?.addEventListener('input', autoResizePrompt);
  el?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submitPrompt();
    }
  });

  $('send')?.addEventListener('click', (e) => {
    e.preventDefault();
    submitPrompt();
  });
  $('cancel')?.addEventListener('click', (e) => {
    e.preventDefault();
    cancelPrompt();
  });

  document.querySelector('.composer')?.addEventListener('click', (e) => {
    const sendBtn = e.target.closest('#send');
    const cancelBtn = e.target.closest('#cancel');
    if (sendBtn) {
      e.preventDefault();
      submitPrompt();
    } else if (cancelBtn) {
      e.preventDefault();
      cancelPrompt();
    }
  });

  autoResizePrompt();
}

export function fillPrompt(text) {
  const el = $('prompt');
  if (!el) return;
  el.value = text;
  autoResizePrompt();
  el.focus();
}
