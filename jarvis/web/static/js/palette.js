/** ⌘K / Ctrl+K command palette */
import { $, escapeHtml, isMac } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe } from './store.js';
import { CATALOG, matchItem, rankItems } from './catalog.js';
import { openModal, closeModal, isModalOpen, listNav } from './modal.js';
import { newChat, toggleSetting } from './actions.js';
import { toggleTheme, resolvedTheme, openAppearance, THEME_LABEL } from './theme.js';
import { submitPrompt, fillPrompt } from './composer.js';
import { openShortcuts } from './shortcuts.js';

let visible = [];
let nav = null;
let openPicker = () => {};

function toggleOn(item) {
  if (item.toggle === 'showThoughts') return store.showThoughts && store.session.show_internal;
  return !!store.session[item.toggle];
}

function itemMeta(item) {
  if (item.toggle) {
    const on = toggleOn(item);
    return `<span class="lr-state${on ? (item.warn ? ' is-warn' : ' is-on') : ''}">${on ? 'On' : 'Off'}</span>`;
  }
  if (item.action === 'theme') return `<span class="lr-state">${THEME_LABEL[resolvedTheme()]}</span>`;
  if (item.laptop) return '<span class="lr-meta">on computer</span>';
  if (item.cmd) return `<span class="lr-code">${escapeHtml(item.cmd.trim())}</span>`;
  return '';
}

function render() {
  const list = $('palette-list');
  const q = ($('palette-input')?.value || '').trim().toLowerCase();
  visible = rankItems(CATALOG.filter((it) => matchItem(it, q)), q);

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
      <div class="list-row" role="option" data-idx="${i}" aria-selected="false">
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
  if (item.picker) {
    openPicker(item.picker);
    return;
  }
  if (item.action === 'session_new') {
    await newChat();
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
  if (item.action === 'appearance') {
    openAppearance();
    return;
  }
  if (item.action === 'shortcuts') {
    openShortcuts();
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
  if (item.toggle || item.action === 'theme') {
    runItem(item).then(() => {
      if (isModalOpen('palette')) render();
      nav?.setCursor(idx);
    });
    return;
  }
  closePalette();
  runItem(item);
}

export function openPalette() {
  const input = $('palette-input');
  input.value = '';
  render();
  openModal('palette', { focus: input });
  nav.reset(0);
}

export function closePalette() {
  closeModal('palette');
}

export function initPalette({ onOpenPicker }) {
  openPicker = onOpenPicker;
  const list = $('palette-list');
  nav = listNav(list, pick);
  const kbd = $('palette-kbd');
  if (kbd) kbd.textContent = isMac ? '⌘K' : 'Ctrl K';

  $('palette-btn')?.addEventListener('click', openPalette);
  const input = $('palette-input');
  input.addEventListener('input', () => {
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
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (isModalOpen('palette')) closePalette();
      else openPalette();
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

