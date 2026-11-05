/** Global keyboard shortcuts + the "?" cheat sheet */
import { $, escapeHtml, isMac } from './utils.js';
import { openModal, topModal } from './modal.js';
import { switchToSlot } from './wsprojects.js';
import { openProjectPalette } from './palette.js';

const MOD = isMac ? '⌘' : 'Ctrl';
const ALT = isMac ? '⌥' : 'Alt';

const SHEET = [
  { keys: [MOD, 'K'], label: 'Command palette' },
  { keys: ['/'], label: 'Slash commands (type in the message box)' },
  { keys: ['Enter'], label: 'Send the message' },
  { keys: ['Shift', 'Enter'], label: 'New line' },
  { keys: ['↑'], label: 'Previous message (empty box)' },
  { keys: [ALT, 'E'], label: 'Enhance: fix spelling and grammar of your message (again to undo)' },
  { keys: ['Esc'], label: 'Stop Jarvis, or close a dialog' },
  { keys: [ALT, 'N'], label: 'New chat' },
  { keys: [ALT, 'M'], label: 'Switch model' },
  { keys: [ALT, 'S'], label: 'Sessions' },
  { keys: [ALT, 'A'], label: 'Agents' },
  { keys: [ALT, 'B'], label: 'Show or hide the sidebar' },
  { keys: [ALT, 'D'], label: 'Show or hide the changes panel' },
  { keys: [ALT, 'F'], label: 'Browse and edit the project\u2019s files' },
  { keys: [ALT, 'Shift', 'F'], label: 'Search code in every file' },
  { keys: [MOD, 'Shift', 'P'], label: 'Find a file by name' },
  { keys: [ALT, 'T'], label: 'Light or dark mode' },
  { keys: [ALT, '1…9'], label: 'Switch to workspace project 1–9 (starts it if it is stopped)' },
  { keys: [ALT, '0'], label: 'Pick a project (⌘K, projects only)' },
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

/** `handlers`: { newChat, openPicker(kind), toggleTheme, enhance, toggleInspector, toggleSidebar, openFiles }.
 *  ⌥ 1…9 / ⌥ 0 (workspace projects) are wired here directly: wsprojects.js + the palette. */
export function initShortcuts(handlers) {
  document.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || topModal()) return;

    if (e.key === '?' && !typing(e) && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      openShortcuts();
      return;
    }

    // ⌘⇧P / Ctrl+Shift+P: find a file by name (inside the Files drawer the drawer handles it).
    if (e.shiftKey && !e.altKey && e.key.toLowerCase() === 'p' && (isMac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey)) {
      e.preventDefault();
      handlers.openFiles?.({ pane: 'files' });
      return;
    }

    if (!e.altKey || e.metaKey || e.ctrlKey) return;
    // e.code: on macOS ⌥ turns letters into symbols (⌥N → ˜).
    const map = {
      KeyN: () => handlers.newChat(),
      KeyM: () => handlers.openPicker('model'),
      KeyS: () => handlers.openPicker('session'),
      KeyA: () => handlers.openPicker('agent'),
      KeyB: () => handlers.toggleSidebar?.(),
      KeyD: () => handlers.toggleInspector?.(),
      KeyF: () => (e.shiftKey ? handlers.openFiles?.({ pane: 'search' }) : handlers.openFiles?.()),
      KeyT: () => handlers.toggleTheme(),
      KeyE: () => handlers.enhance?.(),
    };
    // ⌥ + digit: workspace projects. Also e.code — ⌥ rewrites the character (⌥ 1 → ¡). Without a
    // project at that number the key is left alone (it types what it always typed).
    const digit = /^Digit([0-9])$/.exec(e.code);
    if (digit && !e.shiftKey) {
      const n = Number(digit[1]);
      const done = n === 0 ? (openProjectPalette(), true) : switchToSlot(n);
      if (done) e.preventDefault();
      return;
    }
    const fn = map[e.code];
    if (fn) {
      e.preventDefault();
      fn();
    }
  });
}
