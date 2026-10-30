/** Renaming a session in place — the top-bar title and rows in the Sessions dialog.
 *
 * `renameInline(el, …)` hides `el` and puts a text field where it was: ↵ or
 * leaving the field saves (`session_rename` on the terminal's thread, same as
 * `/rename`), Esc puts the old title back. Every page learns the new title from
 * the snapshot the action broadcasts; lists listen for `jarvis:sessions-changed`.
 */
import { pickerAction } from './api.js';
import { showToast } from './utils.js';
import { store } from './store.js';

export const TITLE_MAX = 80; // SESSION_TITLE_MAX_LENGTH on the Python side

let active = null; // only one field at a time

/** → the saved title, or null when nothing changed / it was cancelled / it failed. */
export function renameInline(el, { sessionId = null, value = '', label = 'Session title', className = '' } = {}) {
  if (!el?.parentNode) return Promise.resolve(null);
  active?.cancel();
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'text';
    input.className = `title-input ${className}`.trim();
    input.value = value;
    input.maxLength = TITLE_MAX;
    input.placeholder = 'Name this chat';
    input.spellcheck = false;
    input.autocomplete = 'off';
    input.enterKeyHint = 'done';
    input.setAttribute('aria-label', label);
    input.dataset.escCancels = ''; // modal.js: Esc cancels this edit, not the dialog

    let done = false;
    const finish = (result) => {
      if (done) return;
      done = true;
      if (active?.input === input) active = null;
      input.remove();
      el.hidden = false;
      resolve(result);
    };
    const cancel = () => finish(null);
    const save = async () => {
      if (done) return;
      const title = input.value.replace(/\s+/g, ' ').trim();
      if (!title || title === value.trim()) { cancel(); return; }
      input.disabled = true;
      const res = await pickerAction('session_rename', { session_id: sessionId || 0, title });
      if (!res.ok) {
        showToast(res.error || 'Could not rename the session', true);
        cancel();
        return;
      }
      el.textContent = res.title || title;
      showToast('Session renamed');
      document.dispatchEvent(new Event('jarvis:sessions-changed'));
      finish(res.title || title);
    };

    input.addEventListener('keydown', (e) => {
      e.stopPropagation(); // not the list's ↑ ↓ ↵, not "type anywhere"
      if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); save(); }
      else if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    });
    input.addEventListener('esc-cancel', cancel);
    input.addEventListener('blur', () => { if (!input.disabled) save(); });
    // A click in the field is not a click on the row it sits in (that would resume it).
    for (const type of ['click', 'pointerdown', 'mousedown']) {
      input.addEventListener(type, (e) => e.stopPropagation());
    }

    el.hidden = true;
    el.after(input);
    active = { input, cancel };
    input.focus({ preventScroll: true });
    input.select();
  });
}

/** The top-bar title: click (or `/rename` with no title) to rename the chat on screen. */
export async function renameCurrent() {
  // With chat tabs in the top bar the active tab is the title (tabs.js).
  const tabTitle = document.querySelector('#chat-tabs:not([hidden]) .ct-tab.is-active .ct-title');
  if (tabTitle) {
    const { renameActiveTab } = await import('./tabs.js');
    if (await renameActiveTab()) return;
  }
  const h1 = document.getElementById('session-title');
  if (!h1 || h1.hidden) return;
  await renameInline(h1, {
    sessionId: store.session.session_id || null,
    value: store.session.session_title || '',
    label: 'New title for this chat',
    className: 'top-rename',
  });
}

export function initTitleRename() {
  const h1 = document.getElementById('session-title');
  if (!h1) return;
  h1.tabIndex = 0;
  h1.setAttribute('role', 'button');
  h1.title = 'Rename this chat';
  h1.addEventListener('click', renameCurrent);
  h1.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); renameCurrent(); }
  });
}
