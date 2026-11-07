/** Global keyboard shortcuts + the "?" cheat sheet.
 *
 * Which key does what lives in keymap.js (the user can change it: Settings →
 * Keyboard shortcuts, keymap_editor.js); this module answers the page-wide
 * ones. Keys that belong to one place are handled there: the palette (⌘K,
 * palette.js), the terminal (terminal.js, terminal_scripts.js), the Files
 * drawer and its editor (files.js).
 */
import { $, escapeHtml } from './utils.js';
import { openModal, closeModal, topModal } from './modal.js';
import { switchToSlot } from './wsprojects.js';
import { openProjectPalette } from './palette.js';
import { ACTIONS, GROUPS, bindingsOf, keyCaps, matchAny } from './keymap.js';
import { openKeymap } from './keymap_editor.js';
import { openSettings } from './settings.js';
import { toggleFullscreen } from './fullscreen.js';
import { newTab, stepTab } from './tabs.js';
import { renameCurrent } from './rename.js';
import { openFilePicker } from './media.js';
import { openInspector } from './inspector.js';
import { openUsageStats } from './usage_stats.js';

const caps = (b) => keyCaps(b).map((k) => `<kbd>${escapeHtml(k)}</kbd>`).join('');

/** The cheat sheet: every shortcut that has a key, in the user's keys. */
function sheetHtml() {
  return GROUPS.map((g) => {
    const rows = ACTIONS.filter((a) => a.group === g && bindingsOf(a.id).length);
    if (!rows.length) return '';
    return `<h3 class="shortcut-group">${escapeHtml(g)}</h3>${rows.map((a) => `
      <div class="shortcut-row">
        <span>${escapeHtml(a.label)}</span>
        <span class="shortcut-keys">${bindingsOf(a.id).map(caps).join('<span class="shortcut-or">or</span>')}</span>
      </div>`).join('')}`;
  }).join('');
}

export function openShortcuts() {
  const list = $('shortcut-list');
  if (list) list.innerHTML = sheetHtml(); // always fresh: keys can change in Settings
  openModal('shortcuts');
}

function typing(e) {
  return !!e.target.closest?.('input, textarea, [contenteditable="true"]');
}

/** A key without ⌘ / Ctrl / ⌥ (`?`, F-keys …) is text while typing in a field. */
const plainKey = (e) => !e.metaKey && !e.ctrlKey && !e.altKey;

/** `handlers`: { newChat, openPicker(kind), toggleTheme, enhance, toggleInspector, toggleSidebar, openFiles }.
 *  ⌥ 1…9 / ⌥ 0 (workspace projects) are wired here directly: wsprojects.js + the palette. */
export function initShortcuts(handlers) {
  const RUN = {
    'app.shortcuts': () => openShortcuts(),
    'app.settings': () => openSettings(),
    'app.theme': () => handlers.toggleTheme(),
    'app.fullscreen': () => toggleFullscreen(),
    'chat.new': () => handlers.newChat(),
    'chat.newTab': () => newTab(),
    'chat.nextTab': () => stepTab(1),
    'chat.prevTab': () => stepTab(-1),
    'chat.rename': () => renameCurrent(),
    'chat.focus': () => $('prompt')?.focus(),
    'chat.attach': () => openFilePicker(),
    'chat.enhance': () => handlers.enhance?.(),
    'chat.model': () => handlers.openPicker('model'),
    'chat.sessions': () => handlers.openPicker('session'),
    'chat.agents': () => handlers.openPicker('agent'),
    'chat.skills': () => handlers.openPicker('skill'),
    'chat.commands': () => handlers.openPicker('command'),
    'chat.mcp': () => handlers.openPicker('mcp'),
    'chat.memory': () => handlers.openPicker('memory'),
    'chat.providers': () => handlers.openPicker('provider'),
    'panel.sidebar': () => handlers.toggleSidebar?.(),
    'panel.changes': () => handlers.toggleInspector?.(),
    'panel.activity': () => openInspector('activity'),
    'panel.usage': () => openUsageStats(),
    'files.open': () => handlers.openFiles?.(),
    'files.searchCode': () => handlers.openFiles?.({ pane: 'search' }),
    // ⌘⇧P / Ctrl+Shift+P: find a file by name (inside the Files drawer the drawer handles it).
    'files.findFile': () => handlers.openFiles?.({ pane: 'files' }),
  };
  const IDS = Object.keys(RUN);

  document.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || topModal()) return;

    // ⌥ + digit: workspace projects. e.code — ⌥ rewrites the character (⌥ 1 → ¡). Without a
    // project at that number the key is left alone (it types what it always typed).
    const digit = /^Digit([0-9])$/.exec(e.code);
    if (digit && e.altKey && !e.metaKey && !e.ctrlKey && !e.shiftKey) {
      const n = Number(digit[1]);
      const done = n === 0 ? (openProjectPalette(), true) : switchToSlot(n);
      if (done) {
        e.preventDefault();
        return;
      }
    }

    const id = matchAny(e, IDS);
    if (!id) return;
    if (plainKey(e) && typing(e) && !/^F\d+$/.test(e.key)) return;
    e.preventDefault();
    RUN[id]();
  });

  $('shortcut-customize')?.addEventListener('click', () => {
    closeModal('shortcuts');
    openKeymap();
  });
}
