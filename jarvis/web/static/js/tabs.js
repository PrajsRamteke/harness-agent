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
 *
 * Master groups (state_fields.master / .masters): a group is a virtual main
 * project (the hub) whose members are other workspace projects, with ONE
 * shared master chat. In the hub that chat is the pinned first tab
 * (🌐 Master: not closable, not renamable). In a member project there is one
 * pinned chip per group (🌐 Master · <group>) before the normal tabs; it is
 * not a tab of this Jarvis, so it is never counted in the tab-number hints,
 * and clicking it opens the group's hub. Without groups nothing changes.
 */
import { $, escapeHtml, showToast, haptic } from './utils.js';
import { icon } from './icons.js';
import { api } from './api.js';
import { renameInline } from './rename.js';
import { announceActiveTab } from './tabkey.js';

let tabs = [];
let lastSig = '';
let pending = false; // a request in flight: ignore double clicks
// null | { id, name, color, session_id, members: [{ id, name, color }] }: this Jarvis is a group hub
let master = null;
// [{ id, name, color, session_id }]: the groups this project belongs to
let masters = [];
const openingGroups = new Set(); // group ids whose hub is starting / being switched to

const strip = () => $('chat-tabs');

export const tabsPost = (op, id = 0, extra = {}) => api('/api/tabs', 'POST', { op, id, ...extra });

const groupSig = (g) => [g.id, g.name, g.color, g.session_id].join('\u0001');

function sig(list) {
  return list.map((t) => [t.id, t.title, t.active, t.busy, t.unread, t.needs_input].join('\u0001')).join('\u0002')
    + '\u0003' + (master ? groupSig(master) : '')
    + '\u0003' + masters.map(groupSig).join('\u0002')
    + '\u0003' + [...openingGroups].join(',');
}

/** The master-group data this page last got (terminal.js: Projects strip, Master shell menu entries). */
export const groupInfo = () => ({ master, masters });

/** The hub's pinned tab id: the tab on the master chat's session, else the first tab. */
function hubTabId() {
  if (!master || !tabs.length) return null;
  const hit = master.session_id ? tabs.find((t) => t.session_id === master.session_id) : null;
  return (hit || tabs[0]).id;
}

/** Tabs as drawn: the hub's master tab first, the rest in their order. */
function ordered() {
  const pin = hubTabId();
  if (pin == null) return tabs;
  const first = tabs.find((t) => t.id === pin);
  return [first, ...tabs.filter((t) => t.id !== pin)];
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

const groupStyle = (color) => (color ? ` style="--grp:${escapeHtml(color)}"` : '');
const globe = (color) => `<span class="ct-globe" aria-hidden="true"${groupStyle(color)}>${icon('globe')}</span>`;

function tabHtml(t, n, { pinned = false } = {}) {
  const title = escapeHtml(pinned ? 'Master' : (t.title || 'New chat'));
  let hint = '';
  if (pinned) hint = t.active ? ' — the master chat shared by every project in this group' : ' — master chat';
  else hint = t.active ? ' — double-click to rename' : (n <= 9 ? ` — tab ${n}` : '');
  const full = pinned && master?.name ? `Master chat of ${escapeHtml(master.name)}` : title;
  // A div, not a button: renaming puts an input in it.
  return `<div class="ct-tab${t.active ? ' is-active' : ''}${pinned ? ' ct-master' : ''}" data-id="${t.id}"${pinned ? ' data-pinned="1"' : ''}${pinned ? groupStyle(master?.color) : ''} role="presentation">`
    + `<div class="ct-main" role="tab" tabindex="0" aria-selected="${t.active ? 'true' : 'false'}" `
    + `title="${pinned ? full : title}${hint}" aria-label="${pinned ? full : title}${stateWord(t)}">`
    + `${markHtml(t)}${pinned ? globe(master?.color) : ''}<span class="ct-title">${title}</span></div>`
    + (pinned ? '' : `<button type="button" class="ct-close" data-close="${t.id}" aria-label="Close ${title}" title="Close tab">${icon('x')}</button>`)
    + '</div>';
}

/** A member project's pinned chip for one group: opens that group's hub (its master chat). */
function chipHtml(g) {
  const name = escapeHtml(g.name || 'Group');
  const busy = openingGroups.has(g.id);
  return `<div class="ct-tab ct-master ct-chip${busy ? ' is-opening' : ''}" data-master="${escapeHtml(g.id)}"${groupStyle(g.color)} role="presentation">`
    + `<div class="ct-main" role="tab" tabindex="0" aria-selected="false" `
    + `title="Master chat of ${name} — shared by every project in the group" aria-label="Master · ${name}${busy ? ', opening' : ''}">`
    + `${busy ? '<span class="ct-mark is-busy" aria-hidden="true"><span class="spinner"></span></span>' : globe(g.color)}`
    + `<span class="ct-title">Master · ${name}</span></div></div>`;
}

let renaming = false; // a title is being edited: don't redraw under the field

function render() {
  const el = strip();
  if (!el || renaming) return;
  const list = el.querySelector('.ct-list');
  const chips = masters.map(chipHtml).join('');
  let n = 0;
  const pin = hubTabId();
  const body = ordered().map((t) => {
    const pinned = t.id === pin;
    return tabHtml(t, pinned ? 0 : ++n, { pinned });
  }).join('');
  list.innerHTML = chips + (chips && body ? '<span class="ct-sep" role="presentation"></span>' : '') + body;
  const any = !!(tabs.length || masters.length);
  el.hidden = !any;
  // The tabs take the title's place in the top bar (tabs.css).
  el.closest('.topbar')?.classList.toggle('has-tabs', any);
  revealActive();
}

/** Scroll the strip so the shown tab is in view (after layout settles). */
function revealActive() {
  requestAnimationFrame(() => {
    strip()?.querySelector('.ct-tab.is-active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  });
}

/** Take the group fields of a `state` / `snapshot` payload; true when they changed. */
function takeGroups(extra) {
  if (!extra || typeof extra !== 'object') return false;
  const had = groupSigAll();
  if ('master' in extra) master = extra.master && typeof extra.master === 'object' ? extra.master : null;
  if ('masters' in extra) masters = Array.isArray(extra.masters) ? extra.masters.filter((g) => g && g.id) : [];
  const now = groupSigAll();
  if (now === had) return false;
  document.dispatchEvent(new CustomEvent('jarvis:groups-changed', { detail: groupInfo() }));
  return true;
}

const groupSigAll = () => (master ? `${groupSig(master)}|${(master.members || []).map((m) => `${m.id}:${m.name}:${m.color}`).join(',')}` : '')
  + '#' + masters.map(groupSig).join(',');

/** From `state` / `snapshot`: the terminal's tabs, as they are now. `extra` is the payload
 *  itself: its `master` / `masters` fields (master groups) ride along. */
export function setTabs(list, extra = null) {
  if (!Array.isArray(list)) return;
  takeGroups(extra);
  // The composer's draft and attachments follow the tab on screen (tabkey.js).
  announceActiveTab(list);
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
  announceActiveTab(tabs);
  lastSig = '';
  render();
  run('switch', id, 'switch tabs');
}

/** Show a group's hub and its master chat (starts the hub's Jarvis first when it is stopped). */
async function openGroup(id) {
  if (!id || openingGroups.has(id)) return;
  haptic();
  openingGroups.add(id);
  lastSig = '';
  render();
  try {
    const { openWorkspaceProject } = await import('./wsprojects.js');
    await openWorkspaceProject(id);
  } catch (err) {
    showToast(`Couldn't open the master chat: ${err.message || err}`, true);
  } finally {
    openingGroups.delete(id);
    lastSig = '';
    render();
  }
}

/** Rename the chat on screen in place, in its tab. False when no tab is shown (or it is the pinned master). */
export async function renameActiveTab() {
  const el = strip()?.querySelector('.ct-tab.is-active:not(.ct-master) .ct-title');
  const t = tabs.find((x) => x.active);
  if (!el || !t || strip().hidden || t.id === hubTabId()) return false;
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
  // Tabs of this Jarvis only: the member chips open another project, they are not stepped onto.
  const list = ordered();
  if (list.length < 2) return;
  const i = Math.max(0, list.findIndex((t) => t.active));
  switchTab(list[(i + step + list.length) % list.length].id);
}

function closeTab(id) {
  const t = tabs.find((x) => x.id === id);
  if (!t || id === hubTabId()) return; // the master chat stays
  if (t.busy && !window.confirm(`“${t.title}” is still working. Stop the reply and close the tab?`)) return;
  run('close', id, 'close the tab');
}

/** Click / Enter on a tab or a master chip. */
function activate(tab) {
  if (tab.dataset.master) openGroup(tab.dataset.master);
  else switchTab(Number(tab.dataset.id));
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
    if (tab) activate(tab);
  });
  // Middle click closes, like a browser tab (not the pinned master).
  el.addEventListener('auxclick', (e) => {
    if (e.button !== 1) return;
    const tab = e.target.closest('.ct-tab');
    if (tab && !tab.classList.contains('ct-master')) {
      e.preventDefault();
      closeTab(Number(tab.dataset.id));
    }
  });
  if (window.ResizeObserver) new ResizeObserver(revealActive).observe(el);
  el.addEventListener('dblclick', (e) => {
    if (e.target.closest('.ct-tab.is-active:not(.ct-master) .ct-main')) renameActiveTab();
  });
  // ↵ / space show a tab, ← → move between them, F2 renames.
  el.addEventListener('keydown', (e) => {
    const main = e.target.closest('.ct-main');
    if (!main || e.target.closest('input')) return;
    const tab = main.closest('.ct-tab');
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      activate(tab);
      return;
    }
    if (e.key === 'F2') {
      e.preventDefault();
      if (!tab.classList.contains('ct-master')) renameActiveTab();
      return;
    }
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const btns = [...el.querySelectorAll('.ct-main')];
    const i = btns.indexOf(main);
    btns[(i + (e.key === 'ArrowRight' ? 1 : -1) + btns.length) % btns.length]?.focus();
  });
}
