/** ⌘K / Ctrl+K command palette */
import { $, escapeHtml } from './utils.js';
import { openFolders } from './folders.js';
import { openStopAll } from './workspace.js';
import { renameCurrent } from './rename.js';
import { icon } from './icons.js';
import { store, subscribe } from './store.js';
import { allItems, matchItem, rankItems, wantCustomItems } from './catalog.js';
import { openModal, closeModal, isModalOpen, listNav } from './modal.js';
import { newChat, toggleSetting } from './actions.js';
import { toggleTheme, toggleGlass, isGlass, openAppearance, themeLabel } from './theme.js';
import { submitPrompt, fillPrompt } from './composer.js';
import { openShortcuts } from './shortcuts.js';
import { openKeymap } from './keymap_editor.js';
import { promptInstall } from './pwa.js';
import { openInspector } from './inspector.js';
import { openFiles } from './files.js';
import { openFilePicker } from './media.js';
import { newTab, stepTab } from './tabs.js';
import { matches, hint, onKeymapChange } from './keymap.js';

let visible = [];
let nav = null;
let openPicker = () => {};
let onlyGroup = '';   // ⌥ 0: the palette lists one group (Projects) until it is closed

function toggleOn(item) {
  return !!store.session[item.toggle];
}

/** Palette actions → their keymap.js ids (items with a /command keep showing the command). */
const ITEM_KEYS = {
  'inspector-changes': 'panel.changes',
  'inspector-activity': 'panel.activity',
  files: 'files.open',
  'search-files': 'files.searchCode',
  'run-script': 'term.scripts',
  attach: 'chat.attach',
  tab_next: 'chat.nextTab',
  tab_prev: 'chat.prevTab',
  shortcuts: 'app.shortcuts',
};

function itemMeta(item) {
  if (item.toggle) {
    const on = toggleOn(item);
    return `<span class="lr-state${on ? (item.warn ? ' is-warn' : ' is-on') : ''}">${on ? 'On' : 'Off'}</span>`;
  }
  if (item.action === 'theme') return `<span class="lr-state">${themeLabel()}</span>`;
  if (item.action === 'glass') return `<span class="lr-state${isGlass() ? ' is-on' : ''}">${isGlass() ? 'On' : 'Off'}</span>`;
  // The item's keyboard shortcut (the user's own, keymap.js) when it has one.
  const key = ITEM_KEYS[item.action] ? hint(ITEM_KEYS[item.action]) : '';
  if (key) return `<span class="lr-code">${escapeHtml(key)}</span>`;
  if (item.hint) return `<span class="lr-code">${escapeHtml(item.hint)}</span>`;
  if (item.laptop) return '<span class="lr-meta">on computer</span>';
  if (item.cmd) return `<span class="lr-code">${escapeHtml(item.cmd.trim())}</span>`;
  return '';
}

function render() {
  const list = $('palette-list');
  const q = ($('palette-input')?.value || '').trim().toLowerCase();
  // `searchOnly` items ("New chat in …", "Terminal in …" per project) show up when you type, not in the open list.
  visible = rankItems(allItems().filter((it) => (onlyGroup ? it.group === onlyGroup : q || !it.searchOnly) && matchItem(it, q)), q);

  if (!visible.length) {
    list.innerHTML = `<div class="list-empty"><strong>No command matches “${escapeHtml(q)}”</strong>Press Enter to send it to Jarvis as a message.</div>`;
    return;
  }

  let html = '';
  let group = '';
  visible.forEach((it, i) => {
    if (!q && it.group !== group) {
      group = it.group;
      html += `<div class="list-section" role="presentation">${escapeHtml(group)}</div>`;
    }
    html += `
      <div class="list-row" role="option" data-idx="${i}" aria-selected="false" style="--i:${Math.min(i, 14)}">
        <span class="lr-icon">${icon(it.icon)}</span>
        <span class="lr-body">
          <span class="lr-title">${escapeHtml(it.label)}</span>
          <span class="lr-sub">${escapeHtml(it.desc)}</span>
        </span>
        ${itemMeta(it)}
      </div>`;
  });
  list.innerHTML = html;
}

/** Run any catalog item (palette or slash menu). */
export async function runItem(item) {
  if (typeof item.run === 'function') {
    item.run();
    return;
  }
  if (item.picker) {
    openPicker(item.picker, item.pickerArg || '');
    return;
  }
  if (item.action === 'session_new') {
    await newChat();
    return;
  }
  if (item.action === 'folders') {
    openFolders();
    return;
  }
  if (item.action === 'stop_all') {
    openStopAll();
    return;
  }
  if (item.action === 'tab_new') {
    newTab();
    return;
  }
  if (item.action === 'tab_next' || item.action === 'tab_prev') {
    stepTab(item.action === 'tab_next' ? 1 : -1);
    return;
  }
  if (item.action === 'rename') {
    renameCurrent();
    return;
  }
  if (item.toggle) {
    await toggleSetting(item.toggle);
    return;
  }
  if (item.action === 'theme') {
    toggleTheme();
    return;
  }
  if (item.action === 'glass') {
    toggleGlass();
    return;
  }
  if (item.action === 'appearance') {
    openAppearance();
    return;
  }
  if (item.action === 'shortcuts') {
    openShortcuts();
    return;
  }
  if (item.action === 'keymap') {
    openKeymap();
    return;
  }
  if (item.action === 'install') {
    promptInstall();
    return;
  }
  if (item.action === 'inspector-changes' || item.action === 'inspector-activity') {
    openInspector(item.action.slice('inspector-'.length));
    return;
  }
  if (item.action === 'files') {
    openFiles();
    return;
  }
  if (item.action === 'search-files') {
    openFiles({ pane: 'search' });
    return;
  }
  if (item.action === 'attach') {
    openFilePicker();
    return;
  }
  if (item.action === 'run-script') {
    // terminal_scripts.js: opens the panel and the dropdown (after the palette lets go of the focus).
    requestAnimationFrame(() => document.dispatchEvent(new CustomEvent('jarvis:term-scripts')));
    return;
  }
  if (item.fill) {
    fillPrompt(item.cmd);
    return;
  }
  if (item.cmd) await submitPrompt(item.cmd.trim());
}

function pick(idx) {
  const item = visible[idx];
  if (!item) return;
  // Toggles keep the palette open so several can be flipped in a row.
  if (item.toggle || item.action === 'theme' || item.action === 'glass') {
    runItem(item).then(() => {
      if (isModalOpen('palette')) render();
      nav?.setCursor(idx);
    });
    return;
  }
  closePalette();
  runItem(item);
}

let openingTimer = 0;

/** Open the palette. `{ group: 'Projects' }` lists only that group (⌥ 0). */
export function openPalette({ group = '' } = {}) {
  wantCustomItems();
  onlyGroup = typeof group === 'string' ? group : '';
  const input = $('palette-input');
  const list = $('palette-list');
  input.value = '';
  input.placeholder = onlyGroup ? `Switch to a ${onlyGroup.toLowerCase().replace(/s$/, '')}…` : input.dataset.placeholder || input.placeholder;
  render();
  // Rows cascade in on open only — typing re-renders without it.
  list.classList.add('is-opening');
  clearTimeout(openingTimer);
  openingTimer = setTimeout(() => list.classList.remove('is-opening'), 700);
  openModal('palette', { focus: input });
  nav.reset(0);
}

export function closePalette() {
  closeModal('palette');
}

/** ⌥ 0 — the palette, projects only. */
export function openProjectPalette() {
  openPalette({ group: 'Projects' });
}

export function initPalette({ onOpenPicker }) {
  openPicker = onOpenPicker;
  const list = $('palette-list');
  nav = listNav(list, pick);
  // The header button's key: the user's own (Settings → Keyboard shortcuts).
  const paintKbd = () => {
    const kbd = $('palette-kbd');
    if (!kbd) return;
    kbd.textContent = hint('app.palette');
    kbd.hidden = !kbd.textContent;
  };
  paintKbd();
  onKeymapChange(paintKbd);

  $('palette-btn')?.addEventListener('click', openPalette);
  const input = $('palette-input');
  input.dataset.placeholder = input.placeholder;
  input.addEventListener('input', () => {
    list.classList.remove('is-opening');
    render();
    nav.reset(0);
  });
  input.addEventListener('keydown', (e) => {
    if (nav.handleKey(e)) return;
    if (e.key === 'Enter' && !visible.length) {
      const text = input.value.trim();
      if (!text) return;
      e.preventDefault();
      closePalette();
      submitPrompt(text);
    }
  });

  document.addEventListener('keydown', (e) => {
    // A key a shell or the editor already took (keymap.js scopes) is not the palette's.
    if (!e.defaultPrevented && matches(e, 'app.palette')) {
      e.preventDefault();
      if (isModalOpen('palette')) closePalette();
      else openPalette();   // (a leftover projects-only filter is dropped: group is '' here)
    }
  });

  subscribe(() => {
    if (isModalOpen('palette') && visible.some((it) => it.toggle)) {
      const sel = list.querySelector('.is-cursor')?.dataset.idx;
      render();
      if (sel !== undefined) nav.setCursor(Number(sel));
    }
  });
}

