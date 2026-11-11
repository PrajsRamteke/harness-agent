/** Settings — the web /settings: one two-pane dialog.
 *
 *   ┌ ⚙ Settings ·················································· × ┐
 *   │ [🔍 Search ]  │  Chat                                          │
 *   │  Model        │  ┌──────────────────────────────────────────┐  │
 *   │ ▌Chat         │  │ Show thinking                        [●] │  │
 *   │  Agents …     │  │ Auto-approve commands                [ ] │  │
 *   │  Terminal     │  └──────────────────────────────────────────┘  │
 *   │  ─ This browser                                                │
 *   │  Appearance   │                                                │
 *   │  Keyboard     │                                                │
 *   ├───────────────┴────────────────────────────────────────────────┤
 *   │ 📄 Saved for every chat · ~/.config/harness-agent/settings.json │
 *   └────────────────────────────────────────────────────────────────┘
 *
 * The left nav picks a section: Model (session state — model / provider /
 * agent / thinking open the existing pickers), every section of
 * `GET /api/settings/all` (jarvis/web/settings_api.py: switches, steppers,
 * choices that save one key at a time on the terminal's thread, which repaints
 * what it shows — theme, pet, QR …), then, under "This browser", Appearance
 * (theme.js) and Keyboard shortcuts (keymap_editor.js), whose markup lives in
 * the dialog as panes (#appearance, #keymap) and which paint themselves when
 * shown. Typing in the search shows matching rows from every section instead.
 * Show thinking and auto-approve go through `setSetting`, like the sidebar
 * switches, so the transcript and every switch follow at once. Opened by
 * /settings, /theme, ⌘K, the gear in the sidebar header; `openSettings('chat')`
 * picks the section, a bare call reopens the last one.
 */
import { $, escapeHtml, showToast, storageGet, storageSet } from './utils.js';
import { icon } from './icons.js';
import { brandSlug } from './brands.js';
import { store, subscribe } from './store.js';
import { openModal, closeModal, isModalOpen } from './modal.js';
import { fetchSettingsAll, settingsPost } from './api.js';
import { patch } from './extui.js';
import { toolbar, section, footer, empty, arrowRows } from './dialog.js';
import { effortRows } from './effort.js';
import { setSetting, setEffort, runAction } from './actions.js';
import { paintAppearance, setAppearanceOpener } from './theme.js';
import { paintKeymap, leaveKeymap, setKeymapHost } from './keymap_editor.js';
import { customizedCount, onKeymapChange } from './keymap.js';

let data = null; // last /api/settings/all
let loadError = false;
let query = '';
let active = 'model'; // the section on the right
let shownPane = null; // 'body' | 'appearance' | 'keymap': what is on screen now
let openPicker = () => {};
const saving = new Set(); // keys with a request in flight

// Keys the session state holds: saved like the sidebar switches (setSetting).
const SESSION_KEYS = { 'trace.on': 'show_internal', 'session.auto_approve': 'auto_approve' };

// ─── Data ─────────────────────────────────────────────────────────────────

async function load() {
  try {
    data = await fetchSettingsAll();
    loadError = false;
  } catch {
    if (!data) loadError = true;
  }
  // The section asked for before the list arrived (the page was just loaded and
  // the last one used was a server-driven one): go there now.
  const want = pending;
  pending = null;
  if (isModalOpen('settings') && want && sectionOf(want)) showSection(want);
  else if (isModalOpen('settings')) render();
}

/** Another page or the terminal changed a setting. */
export function handleSettingsEvent(next) {
  if (next && Array.isArray(next.sections)) {
    data = next;
    if (isModalOpen('settings')) render();
  } else if (isModalOpen('settings')) {
    load();
  }
}

function valueOf(it) {
  const k = SESSION_KEYS[it.key];
  return k ? !!store.session[k] : it.value;
}

// ─── Rows ─────────────────────────────────────────────────────────────────

const shortModel = (m) => String(m || '').split('/').pop() || 'Not set';

function currentEffort() {
  return store.session.think_mode ? store.session.think_effort || 'high' : 'none';
}

/** The Model section's rows, built from the session state: `{ label, desc, icon, control, keys }`. */
function modelRows() {
  const s = store.session;
  const go = (picker, label = 'Change') => goBtn(picker, label);
  return [
    { label: 'Model', desc: shortModel(s.model), icon: 'cpu', control: go('model'), keys: 'model llm switch' },
    { label: 'Provider', desc: s.provider || 'Not set', icon: brandSlug(s.provider) ? `brand:${s.provider}` : 'plug', control: go('provider'), keys: 'provider login key api account' },
    { label: 'Agent', desc: s.agent || 'No agent', icon: 'sparkles', control: go('agent'), keys: 'agent persona mode' },
    {
      label: 'Thinking effort', desc: s.think?.summary || 'How hard the model thinks', icon: 'brain',
      control: pickHtml('effort', 'Thinking effort', effortChoices().find((o) => o.value === currentEffort())),
      keys: 'thinking effort reasoning think level',
    },
    {
      label: 'Commit text model',
      desc: sideModelDesc(s),
      icon: 'git-branch',
      control: pickHtml('side_model', 'Commit text model', { label: sideModelLabel(s) }),
      keys: 'commit git stash pr title description branch text side model cheap fast',
    },
  ];
}

const goBtn = (picker, label = 'Change') => `<button type="button" class="row-btn set-go" data-act="open" data-picker="${picker}">${escapeHtml(label)}${icon('chevron-right')}</button>`;

// ─── Sections ─────────────────────────────────────────────────────────────

const MODEL_SECTION = { id: 'model', title: 'Model', icon: 'cpu', sub: 'The model, provider and agent for this chat' };
// Sections the server sends (settings_api.SECTIONS), drawn before the first answer arrives.
const SERVER_FALLBACK = [
  { id: 'chat', title: 'Chat', icon: 'message-circle' },
  { id: 'catalogs', title: 'Catalogs & caching', icon: 'database' },
  { id: 'sources', title: 'Agents, skills & tools', icon: 'sparkles' },
  { id: 'subagents', title: 'Parallel agents', icon: 'users' },
  { id: 'notifications', title: 'Notifications', icon: 'bell' },
  { id: 'uploads', title: 'Uploads', icon: 'paperclip' },
  { id: 'terminal', title: 'Terminal', icon: 'terminal' },
  { id: 'web', title: 'Web remote', icon: 'globe' },
];
const SECTION_SUBS = {
  chat: 'How replies, approvals and pinned context behave',
  catalogs: 'Where model lists come from, and how requests are cached',
  notifications: 'Alerts on your phone or this computer, even with the page closed',
  uploads: 'Attachments you add to a message',
  sources: 'Where agents, skills, commands and MCP servers are read from',
  subagents: 'Let Jarvis split big tasks across helpers',
  terminal: 'The terminal app: theme, pet and notifications',
  web: 'The connect QR and the Anywhere link',
};
// "This browser": panes of this dialog, saved in localStorage rather than settings.json.
const BROWSER_SECTIONS = [
  { id: 'appearance', title: 'Appearance', icon: 'palette', sub: 'Theme, accent colour, glass', keys: 'appearance theme dark light accent colour color glass' },
  { id: 'keymap', title: 'Keyboard shortcuts', icon: 'keyboard', keys: 'keyboard shortcuts hotkeys keys keybindings bindings remap shortcut key comment editor' },
];
const LAST_SECTION = 'jarvis-settings-section';
let pending = null; // a server-driven section asked for before its list arrived

function serverSections() {
  return (data?.sections || SERVER_FALLBACK).map((x) => ({ id: x.id, title: x.title, icon: x.icon || 'settings', sub: SECTION_SUBS[x.id] || '' }));
}

const allSections = () => [MODEL_SECTION, ...serverSections(), ...BROWSER_SECTIONS];
const sectionOf = (id) => allSections().find((x) => x.id === id) || null;

function keymapDesc() {
  const n = customizedCount();
  return n ? `${n} changed · see or change every key` : 'See or change every key';
}

function controlFor(it) {
  const v = valueOf(it);
  const busy = saving.has(it.key);
  const off = it.locked ? ' disabled' : '';
  if (it.kind === 'switch') {
    return `<input type="checkbox" class="switch${busy ? ' is-pending' : ''}" role="switch" data-key="${escapeHtml(it.key)}" aria-label="${escapeHtml(it.label)}"${v ? ' checked' : ''}${off}>`;
  }
  if (it.kind === 'number') {
    const step = it.step || 1;
    return `<span class="set-num">
      <button type="button" class="set-step" data-act="step" data-key="${escapeHtml(it.key)}" data-step="${-step}" aria-label="Less"${v <= it.min || it.locked ? ' disabled' : ''}>${icon('minus')}</button>
      <input type="number" inputmode="numeric" class="set-num-in" data-key="${escapeHtml(it.key)}" value="${escapeHtml(String(v))}" min="${it.min}" max="${it.max}" step="${step}" aria-label="${escapeHtml(it.label)}"${off}>
      <button type="button" class="set-step" data-act="step" data-key="${escapeHtml(it.key)}" data-step="${step}" aria-label="More"${v >= it.max || it.locked ? ' disabled' : ''}>${icon('plus')}</button>
      ${it.unit ? `<span class="set-unit">${escapeHtml(it.unit)}</span>` : ''}
    </span>`;
  }
  return pickHtml(it.key, it.label, choicesFor(it).find((o) => o.value === v), busy);
}

// ─── Choice menus (theme, Anywhere link, thinking effort) ─────────────────
// A pill button + one menu drawn inside the dialog, never a native <select>.

/** `{ value, label, hint, swatch, disabled }` rows for a choice setting. */
function choicesFor(it) {
  return (it.options || []).map(([value, label, swatch]) => ({ value, label, swatch: swatch || null }));
}

function effortChoices() {
  return effortRows(store.session).map((r) => ({ value: r.value, label: r.label, hint: r.hint, disabled: !r.available }));
}

const sideModelLabel = (s) => (!s?.side_model || s.side_model.auto ? 'Auto' : s?.side_model?.model_label || s?.side_model?.label || 'Auto');

const sideModelDesc = (s) =>
  !s?.side_model || s.side_model.auto
    ? `Commit messages, PR text and branch names · now the chat\u2019s model (${shortModel(s.model)})`
    : `Commit messages, PR text and branch names · ${s.side_model.source_label || 'own model'}`;

function choicesOf(key) {
  if (key === 'effort') return { list: effortChoices(), value: currentEffort(), label: 'Thinking effort' };
  if (key === 'side_model') {
    const side = store.session.side_model;
    const auto = !side || side.auto;
    const list = [{ value: '', label: 'Auto', hint: `Same as the chat (${shortModel(store.session.model)})` }];
    if (!auto) list.push({ value: '__current', label: side.label, hint: 'Writes commit messages, PR titles and branch names' });
    list.push({ value: '__change', label: auto ? 'Choose a model…' : 'Choose another model…', hint: 'Any provider you have set up, just for git text' });
    return { list, value: auto ? '' : '__current', label: 'Commit text model' };
  }
  const it = findItem(key);
  return it ? { list: choicesFor(it), value: valueOf(it), label: it.label } : null;
}

function swatchHtml(sw) {
  return sw ? `<span class="set-swatch" style="--sw-bg:${escapeHtml(sw.bg)};--sw-a:${escapeHtml(sw.accent)};--sw-b:${escapeHtml(sw.alt || sw.accent)}" aria-hidden="true"></span>` : '';
}

function pickHtml(key, label, cur, busy = false) {
  const open = menu?.key === key;
  return `<button type="button" class="set-pick${busy ? ' is-pending' : ''}" data-act="pick" data-pick="${escapeHtml(key)}" aria-haspopup="listbox" aria-expanded="${open}" aria-label="${escapeHtml(label)}: ${escapeHtml(cur?.label || '')}">
    ${swatchHtml(cur?.swatch)}<span class="sp-label">${escapeHtml(cur?.label || 'Choose')}</span>${icon('chevron-down')}
  </button>`;
}

let menu = null; // { key, el, trigger }

function closeMenu({ focus = true } = {}) {
  if (!menu) return;
  const { el, key } = menu;
  menu = null;
  el.hidden = true;
  el.innerHTML = '';
  const trigger = document.querySelector(`#settings-body [data-pick="${CSS.escape(key)}"]`);
  trigger?.setAttribute('aria-expanded', 'false');
  if (focus) trigger?.focus({ preventScroll: true });
}

function openMenu(trigger) {
  const key = trigger.dataset.pick;
  const c = choicesOf(key);
  const card = $('settings')?.querySelector('.settings-card');
  const el = $('set-menu');
  if (!c || !card || !el) return;
  el.setAttribute('aria-label', c.label);
  el.classList.toggle('has-hints', c.list.some((o) => o.hint));
  el.innerHTML = c.list.map((o) => `
    <button type="button" class="set-opt" role="option" data-val="${escapeHtml(o.value)}" aria-selected="${o.value === c.value}"${o.disabled ? ' aria-disabled="true" tabindex="-1"' : ''} data-esc-cancels title="${escapeHtml(o.hint || o.label)}">
      <span class="so-check">${o.value === c.value ? icon('check') : ''}</span>
      ${swatchHtml(o.swatch)}
      <span class="so-body"><strong>${escapeHtml(o.label)}</strong>${o.hint ? `<span>${escapeHtml(o.hint)}</span>` : ''}</span>
    </button>`).join('');
  el.querySelectorAll('.set-opt').forEach((b) => b.addEventListener('esc-cancel', () => closeMenu()));
  menu = { key, el };
  trigger.setAttribute('aria-expanded', 'true');

  // Below the button, or above it when there's more room there; within the
  // list (never over the header or footer). Positioned in the card.
  el.hidden = false;
  const cr = card.getBoundingClientRect();
  const br = ($('settings-body') || card).getBoundingClientRect();
  const tr = trigger.getBoundingClientRect();
  const pad = 10;
  const below = br.bottom - tr.bottom - pad;
  const above = tr.top - br.top - pad;
  const up = below < Math.min(el.scrollHeight, 260) && above > below;
  el.style.maxHeight = `${Math.max(120, Math.min(360, up ? above : below) - 6)}px`;
  el.style.width = `${Math.max(tr.width, el.classList.contains('has-hints') ? 260 : 200)}px`;
  const width = el.offsetWidth;
  el.style.left = `${Math.max(pad, Math.min(tr.right - cr.left - width, cr.width - width - pad))}px`;
  el.style.top = up ? '' : `${tr.bottom - cr.top + 6}px`;
  el.style.bottom = up ? `${cr.bottom - tr.top + 6}px` : '';
  el.classList.toggle('is-up', up);
  el.classList.remove('is-in');
  void el.offsetWidth; // restart the entrance
  el.classList.add('is-in');
  const cur = el.querySelector('[aria-selected="true"]') || el.querySelector('.set-opt:not([aria-disabled])');
  cur?.focus({ preventScroll: true });
  cur?.scrollIntoView({ block: 'nearest' });
}

function pickValue(key, value) {
  closeMenu();
  if (key === 'effort') {
    if (value === currentEffort()) return;
    if (value === 'none') setSetting({ think_mode: false }, 'think_mode');
    else if (value === 'on') setSetting({ think_mode: true }, 'think_mode');
    else setEffort(value);
    return;
  }
  if (key === 'side_model') {
    if (value === '__current') return;
    if (value === '__change') {
      closeModal('settings');
      openPicker('git_model');
      return;
    }
    if (store.session.side_model?.auto) return;
    runAction('side_model_select', { source: '', model_id: '' }, 'Commit text model: Auto');
    return;
  }
  const it = findItem(key);
  if (it && value !== it.value) save(key, value);
}

function onMenuKey(e) {
  if (!menu) return;
  const opts = [...menu.el.querySelectorAll('.set-opt:not([aria-disabled])')];
  const i = opts.indexOf(document.activeElement);
  let next = -1;
  if (e.key === 'ArrowDown') next = Math.min(opts.length - 1, i + 1);
  else if (e.key === 'ArrowUp') next = Math.max(0, i - 1);
  else if (e.key === 'Home') next = 0;
  else if (e.key === 'End') next = opts.length - 1;
  else if (e.key === 'Tab') {
    e.preventDefault();
    closeMenu();
    return;
  } else if (e.key.length === 1 && /\S/.test(e.key)) {
    // Type a letter: jump to the next option starting with it.
    const k = e.key.toLowerCase();
    const order = [...opts.slice(i + 1), ...opts.slice(0, i + 1)];
    const hit = order.find((o) => o.textContent.trim().toLowerCase().startsWith(k));
    if (hit) next = opts.indexOf(hit);
  }
  if (next < 0) return;
  e.preventDefault();
  opts[next].focus();
  opts[next].scrollIntoView({ block: 'nearest' });
}

function rowHtml({ label, desc, ic, control, extra = '', changed = '', locked = '' }) {
  const pin = locked
    ? `<span class="set-pinned" title="Unset ${escapeHtml(locked)} to change this here">${icon('lock')}Pinned by <code>${escapeHtml(locked)}</code></span>`
    : '';
  return `<div class="set-row${locked ? ' is-locked' : ''}">
    <span class="set-ic">${ic ? icon(ic) : ''}</span>
    <span class="set-text"><strong>${escapeHtml(label)}</strong>${desc ? `<span>${escapeHtml(desc)}</span>` : ''}${pin}</span>
    <span class="set-ctl">${changed}${extra}${control}</span>
  </div>`;
}

function serverRow(it) {
  const v = valueOf(it);
  const changed = !it.locked && it.kind !== 'switch' && it.default !== null && v !== it.default
    ? `<button type="button" class="row-btn is-icon set-reset" data-act="reset" data-key="${escapeHtml(it.key)}" title="Back to the default (${escapeHtml(String(it.default))})" aria-label="Reset ${escapeHtml(it.label)}">${icon('rotate-ccw')}</button>`
    : '';
  const extra = it.link
    ? `<button type="button" class="row-btn set-go" data-act="open" data-picker="${escapeHtml(it.link.picker)}">${escapeHtml(it.link.label)}${icon('chevron-right')}</button>`
    : '';
  return rowHtml({ label: it.label, desc: it.desc, ic: it.icon || '', control: controlFor(it), extra, changed, locked: it.locked || '' });
}

const matches = (...texts) => !query || texts.join(' ').toLowerCase().includes(query);

/** The search's way into "This browser": rows that jump to the pane. */
function browserRows() {
  return BROWSER_SECTIONS.map((x) => ({
    label: x.title,
    desc: x.id === 'keymap' ? keymapDesc() : x.sub,
    icon: x.icon,
    control: goBtn(x.id, 'Open'),
    keys: x.keys,
  }));
}

const rowsOut = (rows) => rows.map((r) => rowHtml({ label: r.label, desc: r.desc, ic: r.icon, control: r.control })).join('');

function paneHead(sec) {
  return `<div class="set-pane-head"><h3 class="set-pane-title">${escapeHtml(sec.title)}</h3>${sec.sub ? `<p class="set-pane-desc">${escapeHtml(sec.sub)}</p>` : ''}</div>`;
}

/** The right pane for a Model / server section, or the cross-section results while searching. */
function bodyHtml() {
  if (query) return resultsHtml();
  const sec = sectionOf(active) || MODEL_SECTION;
  let rows = '';
  if (sec.id === 'model') rows = rowsOut(modelRows());
  else {
    const mine = data?.sections.find((x) => x.id === sec.id);
    if (mine) rows = mine.items.map(serverRow).join('');
    else if (loadError && !data) return paneHead(sec) + empty('Couldn’t load these settings', 'Check the connection to Jarvis.', { ic: 'wifi-off' });
    else return `${paneHead(sec)}<div class="skeleton"></div><div class="skeleton"></div>`;
  }
  return `${paneHead(sec)}<div class="set-group">${rows}</div>`;
}

function resultsHtml() {
  const groups = [];
  const mine = modelRows().filter((r) => matches(MODEL_SECTION.title, r.label, r.desc, r.keys));
  if (mine.length) groups.push({ title: MODEL_SECTION.title, html: rowsOut(mine) });
  if (data) {
    for (const sec of data.sections) {
      const rows = sec.items.filter((it) => matches(sec.title, it.label, it.desc, it.key));
      if (rows.length) groups.push({ title: sec.title, html: rows.map(serverRow).join('') });
    }
  }
  const jumps = browserRows().filter((r) => matches('This browser', r.label, r.desc, r.keys));
  if (jumps.length) groups.push({ title: 'This browser', html: rowsOut(jumps) });
  let html = groups.map((g) => `${section(g.title)}<div class="set-group">${g.html}</div>`).join('');
  if (!data && loadError) html += empty('Couldn’t load the other settings', 'Check the connection to Jarvis.', { ic: 'wifi-off' });
  else if (!data) html += '<div class="skeleton"></div><div class="skeleton"></div>';
  if (!groups.length && data) html = empty('No setting matches', `Nothing for “${escapeHtml(query)}”.`, { ic: 'search' });
  return html;
}

/** A selector for the control that has focus, so a re-render can give it back. */
function focusKey() {
  const el = document.activeElement;
  if (!el || !$('settings-body')?.contains(el)) return '';
  if (el.dataset.pick) return `[data-pick="${CSS.escape(el.dataset.pick)}"]`;
  if (el.dataset.key) {
    const act = el.dataset.act ? `[data-act="${el.dataset.act}"]` : ':not([data-act])';
    const step = el.dataset.step ? `[data-step="${el.dataset.step}"]` : '';
    return `[data-key="${CSS.escape(el.dataset.key)}"]${act}${step}`;
  }
  return '';
}

// ─── Nav ──────────────────────────────────────────────────────────────────

function navItemHtml(sec) {
  const on = !query && sec.id === active;
  const n = sec.id === 'keymap' ? customizedCount() : 0;
  const badge = n ? `<span class="set-nav-badge" title="${n} changed" aria-label="${n} changed">${n}</span>` : '';
  return `<button type="button" class="set-nav-item" data-section="${sec.id}"${on ? ' aria-current="page"' : ''}>${icon(sec.icon)}<span class="sn-label">${escapeHtml(sec.title)}</span>${badge}</button>`;
}

function navHtml() {
  return `${[MODEL_SECTION, ...serverSections()].map(navItemHtml).join('')}
    <div class="set-nav-sep" role="separator"><span>This browser</span></div>
    ${BROWSER_SECTIONS.map(navItemHtml).join('')}`;
}

function paintNav() {
  const list = $('settings-nav-list');
  if (!list) return;
  const keep = document.activeElement?.closest?.('.set-nav-item')?.dataset.section;
  patch(list, navHtml());
  if (keep && !list.contains(document.activeElement)) list.querySelector(`[data-section="${CSS.escape(keep)}"]`)?.focus({ preventScroll: true });
}

/** Phones show the nav as a chip strip: bring the active chip into view. */
function revealActiveNav() {
  $('settings-nav-list')?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest', inline: 'center' });
}

// ─── Panes ────────────────────────────────────────────────────────────────

/** Which pane is on screen: the search always shows the results. */
const paneNow = () => (!query && (active === 'appearance' || active === 'keymap') ? active : 'body');

const paneEl = (pane) => $(pane === 'body' ? 'settings-body' : pane);

function paintPanes() {
  const pane = paneNow();
  if (pane === shownPane) return;
  const before = shownPane;
  shownPane = pane;
  for (const id of ['body', 'appearance', 'keymap']) {
    const el = paneEl(id);
    if (el) el.hidden = id !== pane;
  }
  // Recording stops when the keyboard pane goes away; the embedded panes paint when they come.
  if (before === 'keymap') leaveKeymap();
  if (pane === 'appearance') paintAppearance();
  else if (pane === 'keymap') paintKeymap();
}

function paintFoot() {
  const foot = $('settings-foot');
  if (!foot) return;
  // The keyboard pane has its own footer (count + Reset all). Emptied as well as
  // hidden: `.dlg-foot:empty` is what hides a footer whatever its display rule.
  foot.hidden = shownPane === 'keymap';
  if (foot.hidden) {
    patch(foot, '');
    return;
  }
  const file = data?.file ? escapeHtml(data.file.replace(/^\/(Users|home)\/[^/]+/, '~')) : 'settings.json';
  let note = 'Changes apply right away';
  let noteIc = 'settings';
  if (!query) {
    if (active === 'model') {
      note = 'Applies to this chat';
      noteIc = 'message-circle';
    } else if (active === 'appearance') {
      note = 'Saved in this browser';
      noteIc = 'monitor';
    } else {
      note = `Saved for every chat · <code>${file}</code>`;
      noteIc = 'file';
    }
  }
  patch(foot, footer({ note, noteIc, hints: [['esc', 'close']] }));
}

function render() {
  const keep = focusKey();
  paintNav();
  paintPanes();
  patch($('settings-body'), bodyHtml());
  if (keep && !$('settings-body').contains(document.activeElement)) {
    $('settings-body').querySelector(keep)?.focus({ preventScroll: true });
  }
  paintFoot();
}

/** Show a section on the right. Clears a typed search; nothing closes. */
export function showSection(id, { remember = true } = {}) {
  active = sectionOf(id) ? id : 'model';
  if (remember) {
    pending = null; // a choice made now beats one waiting for the list
    storageSet(LAST_SECTION, active);
  }
  if (query) {
    query = '';
    const search = $('settings-search');
    if (search) search.value = '';
  }
  closeMenu({ focus: false });
  render();
  const pane = paneEl(paneNow());
  if (pane) {
    pane.scrollTop = 0;
    pane.classList.remove('is-in');
    void pane.offsetWidth; // restart the fade
    pane.classList.add('is-in');
  }
  revealActiveNav();
}

// ─── Saving ───────────────────────────────────────────────────────────────

function findItem(key) {
  for (const sec of data?.sections || []) {
    const it = sec.items.find((x) => x.key === key);
    if (it) return it;
  }
  return null;
}

async function save(key, value, op = 'set') {
  const it = findItem(key);
  if (!it) return;
  if (SESSION_KEYS[key]) {
    await setSetting({ [SESSION_KEYS[key]]: op === 'reset' ? it.default : !!value }, SESSION_KEYS[key]);
    render();
    return;
  }
  const before = it.value;
  if (op === 'set') it.value = value; // optimistic: the control already shows it
  saving.add(key);
  render();
  const res = await settingsPost({ op, key, value });
  saving.delete(key);
  if (res?.settings) data = res.settings;
  else it.value = before;
  if (!res?.ok) showToast(res?.error || 'Setting not saved', true);
  if (isModalOpen('settings')) render();
}

function commitNumber(input) {
  const it = findItem(input.dataset.key);
  if (!it) return;
  const n = Math.round(Number(input.value));
  if (!Number.isFinite(n) || n === it.value) {
    input.value = it.value;
    return;
  }
  save(it.key, Math.min(it.max, Math.max(it.min, n)));
}

function onClick(e) {
  if (e.target.closest('#keymap')) return; // the keyboard editor handles its own buttons
  const opt = e.target.closest('.set-opt');
  if (opt && menu) {
    if (opt.getAttribute('aria-disabled') !== 'true') pickValue(menu.key, opt.dataset.val);
    return;
  }
  const nav = e.target.closest('.set-nav-item');
  if (nav) {
    showSection(nav.dataset.section);
    return;
  }
  const btn = e.target.closest('[data-act]');
  if (!btn || btn.disabled) return;
  const act = btn.dataset.act;
  if (act === 'pick') {
    const same = menu?.key === btn.dataset.pick;
    closeMenu({ focus: false });
    if (!same) openMenu(btn);
    return;
  }
  if (act === 'open') {
    const picker = btn.dataset.picker;
    // Appearance and Keyboard shortcuts are sections of this dialog; the rest are their own dialogs.
    if (picker === 'appearance' || picker === 'keymap') {
      showSection(picker);
      return;
    }
    closeModal('settings');
    openPicker(picker);
  } else if (act === 'step') {
    const it = findItem(btn.dataset.key);
    if (it) save(it.key, Math.min(it.max, Math.max(it.min, Number(it.value) + Number(btn.dataset.step))));
  } else if (act === 'reset') {
    save(btn.dataset.key, null, 'reset');
  }
}

function onChange(e) {
  const el = e.target;
  const key = el.dataset.key;
  if (!key) return;
  if (el.type === 'checkbox') save(key, el.checked);
  else if (el.type === 'number') commitNumber(el);
}

function onKey(e) {
  if (menu && menu.el.contains(e.target)) {
    onMenuKey(e);
    return;
  }
  if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && e.target.matches?.('.set-pick')) {
    e.preventDefault();
    if (menu?.key !== e.target.dataset.pick) openMenu(e.target);
    return;
  }
  if (e.key === 'Enter' && e.target.classList?.contains('set-num-in')) {
    e.preventDefault();
    commitNumber(e.target);
  }
}

// ─── Open / init ──────────────────────────────────────────────────────────

/** ↑ ↓ (← → on the chip strip) walk the nav; from the search ↓ lands on the first section. */
function onNavKey(e) {
  const items = [...($('settings-nav-list')?.querySelectorAll('.set-nav-item') || [])];
  if (arrowRows(e, $('settings-search'), items)) return;
  if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && e.target.classList?.contains('set-nav-item')) {
    const next = items[items.indexOf(e.target) + (e.key === 'ArrowRight' ? 1 : -1)];
    if (next) {
      e.preventDefault();
      next.focus();
    }
  }
}

/** `section` (a string) picks that section; anything else (no argument, a click event)
 *  reopens the last one. An unknown id falls back to Model. */
export function openSettings(section) {
  const want = typeof section === 'string' ? section : storageGet(LAST_SECTION) || 'model';
  query = '';
  const search = $('settings-search');
  if (search) search.value = '';
  active = sectionOf(want) ? want : 'model';
  // Not known yet (the settings list is still loading): wait for it instead of
  // dropping to Model and forgetting where the user was.
  pending = !sectionOf(want) && !data && want !== 'model' ? want : null;
  shownPane = null; // the embedded panes paint again on every open
  openModal('settings', {
    focus: 'settings-search',
    onClose: () => {
      closeMenu({ focus: false });
      leaveKeymap();
      shownPane = null;
    },
  });
  showSection(active, { remember: !pending });
  load();
}

export function initSettings({ onOpenPicker } = {}) {
  if (onOpenPicker) openPicker = onOpenPicker;
  // Appearance and the keyboard editor are panes of this dialog.
  setAppearanceOpener(() => openSettings('appearance'));
  setKeymapHost({
    open: () => openSettings('keymap'),
    isShown: () => isModalOpen('settings') && shownPane === 'keymap',
  });
  const bar = $('settings-bar');
  if (bar) bar.innerHTML = toolbar({ id: 'settings-search', placeholder: 'Search settings', kbd: false });
  $('settings-search')?.addEventListener('input', (e) => {
    query = e.target.value.trim().toLowerCase();
    closeMenu({ focus: false });
    render();
    $('settings-body').scrollTop = 0;
  });
  $('settings-nav')?.addEventListener('keydown', onNavKey);
  const card = $('settings');
  card?.addEventListener('click', onClick);
  card?.addEventListener('change', onChange);
  card?.addEventListener('keydown', onKey);
  // A click anywhere else, scrolling the list or typing a search closes an open menu.
  card?.addEventListener('pointerdown', (e) => {
    if (menu && !menu.el.contains(e.target) && !e.target.closest?.('[data-act="pick"]')) closeMenu({ focus: false });
  });
  $('settings-body')?.addEventListener('scroll', () => closeMenu({ focus: false }), { passive: true });
  window.addEventListener('resize', () => closeMenu({ focus: false }));
  $('settings-btn')?.addEventListener('click', openSettings);
  // Model, thinking, Show thinking … follow the session (this page, the terminal).
  subscribe(() => {
    if (isModalOpen('settings')) render();
  });
  onKeymapChange(() => {
    if (isModalOpen('settings')) render();
  });
}
