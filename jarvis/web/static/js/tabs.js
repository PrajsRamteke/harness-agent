/** Chat tabs — the same tabs as the terminal's strip (jarvis/tabs.py).
 *
 *   [⠋ Fix the login bug ×] [● Refactor auth ×] [New chat ×]  [+]
 *
 * Several chats open in this Jarvis at once: a reply keeps going in its tab
 * while you read or type in another. The page shows the active tab (the
 * terminal shows the same one); clicking a tab switches both, + opens a new
 * chat in this folder, × closes one (its session stays under Sessions).
 * The strip sits in the top bar's one row, in place of the title: the active
 * tab *is* the title — double-click it (or /rename) to rename the chat.
 * The list arrives with every `state` / `snapshot` (state_fields.tabs);
 * changes go to POST /api/tabs, and the terminal sends the new tab's
 * snapshot itself.
 */
import { $, escapeHtml, showToast, haptic } from './utils.js';
import { icon } from './icons.js';
import { api } from './api.js';
import { renameInline } from './rename.js';

let tabs = [];
let lastSig = '';
let pending = false; // a request in flight: ignore double clicks

const strip = () => $('chat-tabs');

export const tabsPost = (op, id = 0) => api('/api/tabs', 'POST', { op, id });

function sig(list) {
  return list.map((t) => [t.id, t.title, t.active, t.busy, t.unread, t.needs_input].join('\u0001')).join('\u0002');
}

function markHtml(t) {
  if (t.busy) return '<span class="ct-mark is-busy" aria-hidden="true"><span class="spinner"></span></span>';
  if (t.needs_input) return '<span class="ct-mark is-wait" aria-hidden="true"></span>';
  if (t.unread) return '<span class="ct-mark is-new" aria-hidden="true"></span>';
  return '';
}

function stateWord(t) {
  if (t.busy) return ', working';
  if (t.needs_input) return ', needs your answer';
  if (t.unread) return ', new reply';
  return '';
}

function tabHtml(t, i) {
  const title = escapeHtml(t.title || 'New chat');
  const n = i + 1;
  const hint = t.active ? ' — double-click to rename' : (n <= 9 ? ` — tab ${n}` : '');
  // A div, not a button: renaming puts an input in it.
  return `<div class="ct-tab${t.active ? ' is-active' : ''}" data-id="${t.id}" role="presentation">`
    + `<div class="ct-main" role="tab" tabindex="0" aria-selected="${t.active ? 'true' : 'false'}" `
    + `title="${title}${hint}" aria-label="${title}${stateWord(t)}">`
    + `${markHtml(t)}<span class="ct-title">${title}</span></div>`
    + `<button type="button" class="ct-close" data-close="${t.id}" aria-label="Close ${title}" title="Close tab">${icon('x')}</button>`
    + '</div>';
}

let renaming = false; // a title is being edited: don't redraw under the field

function render() {
  const el = strip();
  if (!el || renaming) return;
  const list = el.querySelector('.ct-list');
  list.innerHTML = tabs.map(tabHtml).join('');
  el.hidden = !tabs.length;
  // The tabs take the title's place in the top bar (tabs.css).
  el.closest('.topbar')?.classList.toggle('has-tabs', !!tabs.length);
  revealActive();
}

/** Scroll the strip so the shown tab is in view (after layout settles). */
function revealActive() {
  requestAnimationFrame(() => {
    strip()?.querySelector('.ct-tab.is-active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  });
}

/** From `state` / `snapshot`: the terminal's tabs, as they are now. */
export function setTabs(list) {
  if (!Array.isArray(list)) return;
  const s = sig(list);
  if (s === lastSig) return;
  lastSig = s;
  tabs = list;
  render();
}

async function run(op, id, label) {
  if (pending) return;
  pending = true;
  strip()?.classList.add('is-pending');
  try {
    const res = await tabsPost(op, id);
    if (!res || res.ok === false) {
      showToast((res && res.error) || `Couldn't ${label}`, true);
      return;
    }
    if (Array.isArray(res.tabs)) setTabs(res.tabs);
  } catch (err) {
    showToast(`Couldn't ${label}: ${err.message || err}`, true);
  } finally {
    pending = false;
    strip()?.classList.remove('is-pending');
  }
}

export function newTab() {
  haptic();
  return run('new', 0, 'open a new tab');
}

export function switchTab(id) {
  const t = tabs.find((x) => x.id === id);
  if (!t || t.active) return;
  // Show the pick at once; the snapshot of that chat follows.
  tabs = tabs.map((x) => ({ ...x, active: x.id === id, unread: x.id === id ? false : x.unread }));
  lastSig = '';
  render();
  run('switch', id, 'switch tabs');
}

/** Rename the chat on screen in place, in its tab. False when no tab is shown. */
export async function renameActiveTab() {
  const el = strip()?.querySelector('.ct-tab.is-active .ct-title');
  const t = tabs.find((x) => x.active);
  if (!el || !t || strip().hidden) return false;
  renaming = true;
  try {
    const title = await renameInline(el, {
      sessionId: t.session_id || null,
      value: t.title === 'New chat' && !t.messages ? '' : t.title,
      label: 'New title for this chat',
      className: 'ct-rename',
    });
    if (title) t.title = title;
  } finally {
    renaming = false;
    lastSig = '';
    render();
  }
  return true;
}

export function stepTab(step) {
  if (tabs.length < 2) return;
  const i = Math.max(0, tabs.findIndex((t) => t.active));
  switchTab(tabs[(i + step + tabs.length) % tabs.length].id);
}

function closeTab(id) {
  const t = tabs.find((x) => x.id === id);
  if (!t) return;
  if (t.busy && !window.confirm(`“${t.title}” is still working. Stop the reply and close the tab?`)) return;
  run('close', id, 'close the tab');
}

export function initTabs() {
  const el = strip();
  if (!el) return;
  el.innerHTML = `<div class="ct-list" role="tablist" aria-label="Chat tabs"></div>`
    + `<button type="button" class="ct-new" id="ct-new" aria-label="New chat tab" title="New chat in a new tab — this one keeps going">${icon('plus')}</button>`;
  el.addEventListener('click', (e) => {
    const close = e.target.closest('[data-close]');
    if (close) {
      e.stopPropagation();
      closeTab(Number(close.dataset.close));
      return;
    }
    if (e.target.closest('#ct-new')) {
      newTab();
      return;
    }
    const tab = e.target.closest('.ct-tab');
    if (tab) switchTab(Number(tab.dataset.id));
  });
  // Middle click closes, like a browser tab.
  el.addEventListener('auxclick', (e) => {
    if (e.button !== 1) return;
    const tab = e.target.closest('.ct-tab');
    if (tab) {
      e.preventDefault();
      closeTab(Number(tab.dataset.id));
    }
  });
  if (window.ResizeObserver) new ResizeObserver(revealActive).observe(el);
  el.addEventListener('dblclick', (e) => {
    if (e.target.closest('.ct-tab.is-active .ct-main')) renameActiveTab();
  });
  // ↵ / space show a tab, ← → move between them, F2 renames.
  el.addEventListener('keydown', (e) => {
    const main = e.target.closest('.ct-main');
    if (!main || e.target.closest('input')) return;
    const id = Number(main.closest('.ct-tab').dataset.id);
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      switchTab(id);
      return;
    }
    if (e.key === 'F2') {
      e.preventDefault();
      renameActiveTab();
      return;
    }
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const btns = [...el.querySelectorAll('.ct-main')];
    const i = btns.indexOf(e.target.closest('.ct-main'));
    btns[(i + (e.key === 'ArrowRight' ? 1 : -1) + btns.length) % btns.length]?.focus();
  });
}
