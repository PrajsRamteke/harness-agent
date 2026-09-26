/** Global keyboard shortcuts + the "?" cheat sheet */
import { $, escapeHtml, isMac } from './utils.js';
import { openModal, topModal } from './modal.js';

const MOD = isMac ? '⌘' : 'Ctrl';
const ALT = isMac ? '⌥' : 'Alt';

const SHEET = [
  { keys: [MOD, 'K'], label: 'Command palette' },
  { keys: ['/'], label: 'Slash commands (type in the message box)' },
  { keys: ['Enter'], label: 'Send the message' },
  { keys: ['Shift', 'Enter'], label: 'New line' },
  { keys: ['↑'], label: 'Previous message (empty box)' },
  { keys: ['Esc'], label: 'Stop Jarvis, or close a dialog' },
  { keys: [ALT, 'N'], label: 'New chat' },
  { keys: [ALT, 'M'], label: 'Switch model' },
  { keys: [ALT, 'S'], label: 'Sessions' },
  { keys: [ALT, 'A'], label: 'Agents' },
  { keys: [ALT, 'T'], label: 'Light or dark mode' },
  { keys: ['?'], label: 'This list' },
];

export function openShortcuts() {
  const list = $('shortcut-list');
  if (list && !list.childElementCount) {
    list.innerHTML = SHEET.map((s) => `
      <div class="shortcut-row">
        <span>${escapeHtml(s.label)}</span>
        <span class="shortcut-keys">${s.keys.map((k) => `<kbd>${escapeHtml(k)}</kbd>`).join('')}</span>
      </div>`).join('');
  }
  openModal('shortcuts');
}

function typing(e) {
  return !!e.target.closest?.('input, textarea, [contenteditable="true"]');
}

/** `handlers`: { newChat, openPicker(kind), toggleTheme } */
export function initShortcuts(handlers) {
  document.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || topModal()) return;

    if (e.key === '?' && !typing(e) && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      openShortcuts();
      return;
    }

    if (!e.altKey || e.metaKey || e.ctrlKey) return;
    // e.code: on macOS ⌥ turns letters into symbols (⌥N → ˜).
    const map = {
      KeyN: () => handlers.newChat(),
      KeyM: () => handlers.openPicker('model'),
      KeyS: () => handlers.openPicker('session'),
      KeyA: () => handlers.openPicker('agent'),
      KeyT: () => handlers.toggleTheme(),
    };
    const fn = map[e.code];
    if (fn) {
      e.preventDefault();
      fn();
    }
  });
}
