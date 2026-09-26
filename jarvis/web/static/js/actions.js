/** Session actions + settings shared by the sidebar, palette and pickers */
import { showToast, storageSet } from './utils.js';
import { store, patchSession, patchStore, loadSnapshot } from './store.js';
import { pickerAction, updateSettings } from './api.js';
import { renderSnapshot, syncThoughtsVisibility } from './chat.js';

const listeners = new Set();

/** Notified after any successful session-changing action (sidebar refreshes recents). */
export function onSessionChange(fn) {
  listeners.add(fn);
}

function emitSessionChange() {
  for (const fn of listeners) fn();
}

export function applyState(state) {
  if (!state || typeof state !== 'object' || !('messages' in state)) return;
  loadSnapshot(state);
  renderSnapshot(state);
}

/** Run a picker/session action. Returns the response (ok or not). */
export async function runAction(action, data = {}, successMsg = '') {
  const res = await pickerAction(action, data);
  if (res.state) applyState(res.state);
  if (res.ok) {
    if (successMsg) showToast(successMsg);
    emitSessionChange();
  } else {
    showToast(res.error || 'That did not work', true);
  }
  return res;
}

export function newChat() {
  return runAction('session_new', {}, 'New chat started');
}

let pending = 0;

/** Optimistically change a server setting; reverts if the request fails. */
export async function setSetting(patch, key) {
  const prev = { ...store.session };
  patchSession(patch);
  patchStore({ pendingToggle: key });
  pending += 1;
  try {
    const res = await updateSettings(patch);
    const snap = res?.settings;
    if (snap && typeof snap === 'object') {
      loadSnapshot(snap);
      // Trace adds or removes thinking entries from the transcript.
      if ('show_internal' in patch) renderSnapshot(snap);
    }
    syncThoughtsVisibility();
    return true;
  } catch {
    patchSession(prev);
    syncThoughtsVisibility();
    showToast('Setting not saved — check the connection', true);
    return false;
  } finally {
    pending -= 1;
    if (!pending) patchStore({ pendingToggle: null });
  }
}

export function toggleSetting(which) {
  const s = store.session;
  switch (which) {
    case 'think_mode':
      return setSetting({ think_mode: !s.think_mode }, 'think_mode');
    case 'show_internal':
      return setSetting({ show_internal: !s.show_internal }, 'show_internal');
    case 'auto_approve':
      return setSetting({ auto_approve: !s.auto_approve }, 'auto_approve');
    case 'showThoughts':
      toggleThoughts();
      return Promise.resolve(true);
    default:
      return Promise.resolve(false);
  }
}

export function setEffort(effort) {
  return setSetting({ think_effort: effort }, 'think_effort');
}

export function toggleThoughts(next = !store.showThoughts) {
  patchStore({ showThoughts: next });
  storageSet('jarvis-show-thinking', next ? '1' : '0');
  syncThoughtsVisibility();
}
