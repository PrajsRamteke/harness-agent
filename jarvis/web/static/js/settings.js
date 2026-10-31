/** Settings — the web /settings: every Jarvis setting as a switch, stepper or choice.
 *
 *   [🔍 Search settings                                    ]
 *   MODEL          Model      gpt-6-luna                    Change ›
 *                  Thinking   [ High ▾ ]
 *   CHAT           Show thinking                              [●]
 *   …              At once    [ − 20 + ]                       ↺
 *   ──────────────────────────────────────────────────────────────────
 *   📄 ~/.config/harness-agent/settings.json                    esc close
 *
 * Model / provider / agent / thinking / appearance come from the session state
 * and open the existing pickers; everything else is `GET /api/settings/all`
 * (jarvis/web/settings_api.py) and saves one key at a time on the terminal's
 * thread, which repaints what it shows (theme, pet, QR …). Show thinking and
 * auto-approve go through `setSetting`, like the sidebar switches, so the
 * transcript and every switch follow at once. Opened by /settings, ⌘K
 * "Settings" or the gear in the sidebar header.
 */
import { $, escapeHtml, showToast } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe } from './store.js';
import { openModal, closeModal, isModalOpen } from './modal.js';
import { fetchSettingsAll, settingsPost } from './api.js';
import { patch } from './extui.js';
import { toolbar, section, footer, empty } from './dialog.js';
import { effortRows } from './effort.js';
import { setSetting, setEffort } from './actions.js';
import { openAppearance } from './theme.js';

let data = null; // last /api/settings/all
let loadError = false;
let query = '';
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
  if (isModalOpen('settings')) render();
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

/** Rows built from the session state: `{ label, desc, icon, control, keys }`. */
function clientSections() {
  const s = store.session;
  const go = (picker, label = 'Change') => `<button type="button" class="row-btn set-go" data-act="open" data-picker="${picker}">${escapeHtml(label)}${icon('chevron-right')}</button>`;
  return [
    {
      title: 'Model',
      rows: [
        { label: 'Model', desc: shortModel(s.model), icon: 'cpu', control: go('model'), keys: 'model llm switch' },
        { label: 'Provider', desc: s.provider || 'Not set', icon: 'plug', control: go('provider'), keys: 'provider login key api account' },
        { label: 'Agent', desc: s.agent || 'No agent', icon: 'sparkles', control: go('agent'), keys: 'agent persona mode' },
        {
          label: 'Thinking effort', desc: s.think?.summary || 'How hard the model thinks', icon: 'brain',
          control: pickHtml('effort', 'Thinking effort', effortChoices().find((o) => o.value === currentEffort())),
          keys: 'thinking effort reasoning think level',
        },
      ],
    },
    {
      title: 'This browser',
      rows: [
        { label: 'Appearance', desc: 'Theme, accent colour, glass', icon: 'palette', control: go('appearance', 'Open'), keys: 'appearance theme dark light accent colour color glass' },
      ],
    },
  ];
}

function controlFor(it) {
  const v = valueOf(it);
  const busy = saving.has(it.key);
  if (it.kind === 'switch') {
    return `<input type="checkbox" class="switch${busy ? ' is-pending' : ''}" role="switch" data-key="${escapeHtml(it.key)}" aria-label="${escapeHtml(it.label)}"${v ? ' checked' : ''}>`;
  }
  if (it.kind === 'number') {
    const step = it.step || 1;
    return `<span class="set-num">
      <button type="button" class="set-step" data-act="step" data-key="${escapeHtml(it.key)}" data-step="${-step}" aria-label="Less"${v <= it.min ? ' disabled' : ''}>${icon('minus')}</button>
      <input type="number" inputmode="numeric" class="set-num-in" data-key="${escapeHtml(it.key)}" value="${escapeHtml(String(v))}" min="${it.min}" max="${it.max}" step="${step}" aria-label="${escapeHtml(it.label)}">
      <button type="button" class="set-step" data-act="step" data-key="${escapeHtml(it.key)}" data-step="${step}" aria-label="More"${v >= it.max ? ' disabled' : ''}>${icon('plus')}</button>
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

function choicesOf(key) {
  if (key === 'effort') return { list: effortChoices(), value: currentEffort(), label: 'Thinking effort' };
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

function rowHtml({ label, desc, ic, control, extra = '', changed = '' }) {
  return `<div class="set-row">
    <span class="set-ic">${ic ? icon(ic) : ''}</span>
    <span class="set-text"><strong>${escapeHtml(label)}</strong>${desc ? `<span>${escapeHtml(desc)}</span>` : ''}</span>
    <span class="set-ctl">${changed}${extra}${control}</span>
  </div>`;
}

function serverRow(it) {
  const v = valueOf(it);
  const changed = it.kind !== 'switch' && it.default !== null && v !== it.default
    ? `<button type="button" class="row-btn is-icon set-reset" data-act="reset" data-key="${escapeHtml(it.key)}" title="Back to the default (${escapeHtml(String(it.default))})" aria-label="Reset ${escapeHtml(it.label)}">${icon('rotate-ccw')}</button>`
    : '';
  const extra = it.link
    ? `<button type="button" class="row-btn set-go" data-act="open" data-picker="${escapeHtml(it.link.picker)}">${escapeHtml(it.link.label)}${icon('chevron-right')}</button>`
    : '';
  return rowHtml({ label: it.label, desc: it.desc, ic: it.icon || '', control: controlFor(it), extra, changed });
}

const matches = (...texts) => !query || texts.join(' ').toLowerCase().includes(query);

function bodyHtml() {
  const groups = [];
  for (const g of clientSections()) {
    const rows = g.rows.filter((r) => matches(g.title, r.label, r.desc, r.keys));
    if (rows.length) groups.push({ title: g.title, html: rows.map((r) => rowHtml({ label: r.label, desc: r.desc, ic: r.icon, control: r.control })).join('') });
  }
  if (data) {
    for (const sec of data.sections) {
      const rows = sec.items.filter((it) => matches(sec.title, it.label, it.desc, it.key));
      if (rows.length) groups.push({ title: sec.title, html: rows.map(serverRow).join('') });
    }
  }
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

function render() {
  const keep = focusKey();
  patch($('settings-body'), bodyHtml());
  if (keep && !$('settings-body').contains(document.activeElement)) {
    $('settings-body').querySelector(keep)?.focus({ preventScroll: true });
  }
  const file = data?.file ? escapeHtml(data.file.replace(/^\/(Users|home)\/[^/]+/, '~')) : 'settings.json';
  patch($('settings-foot'), footer({ note: `Saved for every chat · <code>${file}</code>`, noteIc: 'file', hints: [['esc', 'close']] }));
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
  const opt = e.target.closest('.set-opt');
  if (opt && menu) {
    if (opt.getAttribute('aria-disabled') !== 'true') pickValue(menu.key, opt.dataset.val);
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
    closeModal('settings');
    if (picker === 'appearance') openAppearance();
    else openPicker(picker);
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

export function openSettings() {
  query = '';
  const search = $('settings-search');
  if (search) search.value = '';
  render();
  openModal('settings', { focus: 'settings-search', onClose: () => closeMenu({ focus: false }) });
  load();
}

export function initSettings({ onOpenPicker } = {}) {
  if (onOpenPicker) openPicker = onOpenPicker;
  const bar = $('settings-bar');
  if (bar) bar.innerHTML = toolbar({ id: 'settings-search', placeholder: 'Search settings', kbd: false });
  $('settings-search')?.addEventListener('input', (e) => {
    query = e.target.value.trim().toLowerCase();
    closeMenu({ focus: false });
    render();
  });
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
}
