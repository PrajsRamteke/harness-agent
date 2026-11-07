/** Settings → Keyboard shortcuts: every shortcut (keymap.js), grouped, and a way to
 * change it. Saved in this browser (localStorage), like Appearance.
 *
 *   [🔍 Search shortcuts or keys                                   ]
 *   CHAT
 *     New chat                                   [⌥ N ×]  [+]  ↺
 *     Send the message                           [↵]  🔒
 *   ─────────────────────────────────────────────────────────────────
 *   2 changed · saved in this browser          [Reset all]   esc close
 *
 * Click a key (or +) and press the new shortcut: Esc cancels. A key already
 * used where this one works asks before it moves over; keys the browser keeps
 * (⌘W, ⌘T …) and plain letters (they would type) are refused with a reason.
 * It also paints the shortcut hints in index.html ([data-kbd]) and keeps them
 * current when a key changes.
 */
import { $, escapeHtml } from './utils.js';
import { icon } from './icons.js';
import { openModal, closeModal, isModalOpen, topModal } from './modal.js';
import { toolbar, section, footer, empty, setView } from './dialog.js';
import { patch } from './extui.js';
import {
  ACTIONS, GROUPS, IS_MAC, actionOf, bindingsOf, defaultsOf, isCustomized, customizedCount, setBindings, resetBindings,
  resetAll, conflictsFor, problemWith, eventToBinding, keyCaps, display, hint, normalize, scopeOf, onKeymapChange,
} from './keymap.js';

const ID = 'keymap';
const SCOPE_NOTE = { editor: 'In the Files editor', files: 'In Files', commit: 'In the commit message', terminal: 'Inside a shell' };

let query = '';
let rec = null;        // { id, i (index, -1 = add), msg, pending, conflicts, held }
let onBack = null;     // opened from Settings: the back button returns there

// ─── Markup ───────────────────────────────────────────────────────────────

const caps = (b) => keyCaps(b).map((k) => `<kbd>${escapeHtml(k)}</kbd>`).join('');

function chipHtml(a, b, i) {
  if (rec?.id === a.id && rec.i === i) return recHtml(a);
  return `<span class="km-key">
    <button type="button" class="km-chip" data-act="edit" data-i="${i}" title="Change" aria-label="${escapeHtml(`${a.label}: ${display(b)}. Change`)}">${caps(b)}</button>
    <button type="button" class="km-x" data-act="remove" data-i="${i}" title="Remove this shortcut" aria-label="${escapeHtml(`Remove ${display(b)} from ${a.label}`)}">${icon('x')}</button>
  </span>`;
}

function recHtml() {
  // Modifiers held so far, then a blank for the key still to come.
  const held = rec.held ? `${keyCaps(`${rec.held}+X`).slice(0, -1).map((k) => `<kbd>${escapeHtml(k)}</kbd>`).join('')}<kbd class="km-blank"></kbd>` : '';
  return `<span class="km-rec" role="status" aria-live="polite">${held || '<span>Press keys\u2026</span>'}</span>`;
}

function controlsHtml(a) {
  if (a.fixed) {
    return `<span class="km-keys km-fixed" title="Built in \u2014 can\u2019t be changed">${bindingsOf(a.id).map((b) => `<span class="km-cap">${caps(b)}</span>`).join('<span class="km-or">or</span>')}${icon('lock')}</span>`;
  }
  const list = bindingsOf(a.id);
  const chips = list.map((b, i) => chipHtml(a, b, i)).join('');
  const adding = rec?.id === a.id && rec.i === -1;
  const none = !list.length && !adding ? '<span class="km-none">None</span>' : '';
  const add = adding
    ? recHtml(a)
    : `<button type="button" class="row-btn is-icon km-add" data-act="add" title="Add a shortcut" aria-label="${escapeHtml(`Add a shortcut for ${a.label}`)}">${icon('plus')}</button>`;
  const reset = isCustomized(a.id)
    ? `<button type="button" class="row-btn is-icon set-reset" data-act="reset" title="Back to the default (${escapeHtml(defaultsOf(a.id).map(display).join(', ') || 'none')})" aria-label="${escapeHtml(`Reset ${a.label}`)}">${icon('rotate-ccw')}</button>`
    : '';
  return `<span class="km-keys">${none}${chips}${add}</span>${reset}`;
}

function descOf(a) {
  const bits = [];
  if (SCOPE_NOTE[scopeOf(a)]) bits.push(SCOPE_NOTE[scopeOf(a)]);
  if (isCustomized(a.id)) bits.push(`Default: ${defaultsOf(a.id).map(display).join(' or ') || 'none'}`);
  return bits.join(' \u00b7 ');
}

function msgHtml(a) {
  if (rec?.id !== a.id) return '';
  if (rec.pending) {
    const names = rec.conflicts.map((c) => `\u201c${escapeHtml(c.label)}\u201d`).join(', ');
    return `<div class="km-msg is-warn" role="alert">${icon('triangle-alert')}
      <span>${escapeHtml(display(rec.pending))} is used by ${names}.</span>
      <span class="km-msg-acts">
        <button type="button" class="btn btn-sm" data-act="replace">Use here</button>
        <button type="button" class="btn btn-sm btn-quiet" data-act="cancel">Cancel</button>
      </span></div>`;
  }
  const text = rec.msg || `Press the new shortcut \u00b7 ${IS_MAC ? 'esc' : 'Esc'} cancels`;
  return `<div class="km-msg${rec.msg ? ' is-warn' : ''}" role="${rec.msg ? 'alert' : 'note'}">${rec.msg ? icon('triangle-alert') : icon('keyboard')}<span>${escapeHtml(text)}</span></div>`;
}

function rowHtml(a) {
  const desc = descOf(a);
  const on = rec?.id === a.id;
  return `<div class="set-row km-row${on ? ' is-rec' : ''}${a.fixed ? ' is-fixed' : ''}" data-id="${escapeHtml(a.id)}">
    <span class="set-text"><strong>${escapeHtml(a.label)}</strong>${desc ? `<span>${escapeHtml(desc)}</span>` : ''}</span>
    <span class="set-ctl km-ctl">${controlsHtml(a)}</span>
    ${msgHtml(a)}
  </div>`;
}

function hit(a, q) {
  if (!q) return true;
  const keys = bindingsOf(a.id).flatMap((b) => [b, display(b), keyCaps(b).join(' ')]);
  // The id's words too: "app.theme" → "app theme", "chat.newTab" → "chat new tab".
  const words = a.id.replace(/\./g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2');
  return [a.label, a.group, words, SCOPE_NOTE[scopeOf(a)] || '', ...keys].join(' ').toLowerCase().includes(q);
}

function bodyHtml() {
  const q = query.trim().toLowerCase();
  const html = GROUPS.map((g) => {
    const rows = ACTIONS.filter((a) => a.group === g && (hit(a, q) || rec?.id === a.id));
    if (!rows.length) return '';
    return `${section(g, { count: rows.length })}<div class="set-group">${rows.map(rowHtml).join('')}</div>`;
  }).join('');
  return html || empty('No shortcut matches', `Nothing for \u201c${escapeHtml(query.trim())}\u201d.`, { ic: 'search' });
}

function footHtml() {
  const n = customizedCount();
  const note = `${n ? `${n} changed \u00b7 ` : ''}Saved in this browser`;
  const reset = `<button type="button" class="btn btn-sm btn-quiet km-reset-all" data-act="reset-all"${n ? '' : ' disabled'}>${icon('rotate-ccw')}<span>Reset all</span></button>`;
  return footer({ note: escapeHtml(note), noteIc: 'keyboard', hints: [['esc', rec ? 'cancel' : 'close']] }).replace('<span class="dlg-spacer"></span>', `<span class="dlg-spacer"></span>${reset}`);
}

/** Where focus was, so a repaint can give it back. */
function focusSel() {
  const el = document.activeElement;
  const row = el?.closest?.('.km-row');
  if (!row || !$(`${ID}-body`)?.contains(el)) return '';
  const act = el.dataset.act;
  return `.km-row[data-id="${CSS.escape(row.dataset.id)}"] [data-act="${act}"]${el.dataset.i ? `[data-i="${el.dataset.i}"]` : ''}`;
}

function render() {
  if (!isModalOpen(ID)) return;
  const keep = focusSel();
  patch($(`${ID}-body`), bodyHtml());
  patch($(`${ID}-foot`), footHtml());
  if (keep && !$(`${ID}-body`).contains(document.activeElement)) $(`${ID}-body`).querySelector(keep)?.focus({ preventScroll: true });
}

function focusRow(id, act = 'add') {
  const row = $(`${ID}-body`)?.querySelector(`.km-row[data-id="${CSS.escape(id)}"]`);
  (row?.querySelector(`[data-act="${act}"]`) || row?.querySelector('[data-act="edit"], [data-act="add"]'))?.focus({ preventScroll: true });
}

// ─── Recording ────────────────────────────────────────────────────────────

function startRec(id, i) {
  rec = { id, i, msg: '', pending: '', conflicts: [], held: '' };
  render();
  $(`${ID}-body`)?.querySelector(`.km-row[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest' });
  // The recorder takes the keys; keep focus inside the dialog (on the row's card).
  $(ID)?.querySelector('.modal-card')?.focus({ preventScroll: true });
}

function stopRec({ focus = true } = {}) {
  if (!rec) return;
  const { id, i } = rec;
  rec = null;
  render();
  if (focus) focusRow(id, i >= 0 ? 'edit' : 'add');
}

function commit(binding) {
  const { id, i } = rec;
  const list = [...bindingsOf(id)];
  if (i >= 0 && i < list.length) list[i] = binding;
  else list.push(binding);
  rec = null;
  setBindings(id, list); // → onKeymapChange → render
  focusRow(id, 'edit');
}

function tryBinding(binding) {
  const a = actionOf(rec.id);
  const nb = normalize(binding);
  const problem = problemWith(rec.id, nb);
  if (problem) {
    rec = { ...rec, msg: problem, pending: '', held: '' };
    render();
    return;
  }
  const same = bindingsOf(a.id).indexOf(nb);
  if (same !== -1 && same !== rec.i) {
    rec = { ...rec, msg: `${display(nb)} is already a shortcut for this.`, pending: '', held: '' };
    render();
    return;
  }
  const others = conflictsFor(a.id, nb);
  const fixed = others.find((o) => o.fixed);
  if (fixed) {
    rec = { ...rec, msg: `${display(nb)} is built in for \u201c${fixed.label}\u201d.`, pending: '', held: '' };
    render();
    return;
  }
  if (others.length) {
    rec = { ...rec, msg: '', pending: nb, conflicts: others, held: '' };
    render();
    $(`${ID}-body`)?.querySelector('[data-act="replace"]')?.focus({ preventScroll: true });
    return;
  }
  commit(nb);
}

function replaceConflicts() {
  if (!rec?.pending) return;
  const nb = rec.pending;
  for (const o of rec.conflicts) setBindings(o.id, bindingsOf(o.id).filter((b) => b !== nb));
  commit(nb);
}

const MODS = { Control: 'Ctrl', Meta: 'Meta', Alt: 'Alt', Shift: 'Shift' };

/** Window, capture phase: ahead of every other shortcut on the page while recording. */
function onRecKey(e) {
  if (!rec || topModal() !== ID) return;
  if (rec.pending) {
    // Answering "Use here / Cancel": Esc cancels, the rest is normal navigation.
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopImmediatePropagation();
      stopRec();
    }
    return;
  }
  if (e.type === 'keyup') {
    if (MODS[e.key]) {
      rec.held = heldMods(e);
      render();
    }
    return;
  }
  if (e.isComposing) return;
  if (e.key === 'Escape' && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
    e.preventDefault();
    e.stopImmediatePropagation();
    stopRec();
    return;
  }
  if (e.key === 'Tab' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    stopRec({ focus: false }); // Tab keeps moving focus
    return;
  }
  e.preventDefault();
  e.stopImmediatePropagation();
  if (MODS[e.key]) {
    rec.held = heldMods(e);
    render();
    return;
  }
  const b = eventToBinding(e);
  if (b) tryBinding(b);
}

function heldMods(e) {
  return ['Ctrl', 'Meta', 'Alt', 'Shift'].filter((m) => ({ Ctrl: e.ctrlKey, Meta: e.metaKey, Alt: e.altKey, Shift: e.shiftKey })[m]).join('+');
}

// ─── Clicks ───────────────────────────────────────────────────────────────

function onClick(e) {
  const btn = e.target.closest('[data-act]');
  if (!btn || btn.disabled || !$(ID).contains(btn)) return;
  const act = btn.dataset.act;
  if (act === 'reset-all') {
    if (customizedCount() && window.confirm('Put every keyboard shortcut back to its default?')) {
      rec = null;
      resetAll();
    }
    return;
  }
  if (act === 'replace') { replaceConflicts(); return; }
  if (act === 'cancel') { stopRec(); return; }
  const id = btn.closest('.km-row')?.dataset.id;
  if (!id) return;
  const i = Number(btn.dataset.i);
  if (act === 'edit') startRec(id, i);
  else if (act === 'add') startRec(id, -1);
  else if (act === 'remove') {
    rec = null;
    setBindings(id, bindingsOf(id).filter((_, j) => j !== i));
    focusRow(id, 'add');
  } else if (act === 'reset') {
    rec = null;
    resetBindings(id);
    focusRow(id, 'edit');
  }
}

// ─── Hints painted into the page ──────────────────────────────────────────

/** `data-kbd="id"` + `data-kbd-title="Label ({key}) — more"` → title; `data-kbd-aria` → aria-label;
 *  `data-kbd-caps="id"` → <kbd>s (hidden when the action has no key). */
export function applyKeyHints(root = document) {
  const fill = (tpl, h) => (tpl.includes('{key}') ? tpl.replace(' ({key})', h ? ` (${h})` : '').replace('{key}', h) : h ? `${tpl} (${h})` : tpl);
  root.querySelectorAll('[data-kbd]').forEach((el) => {
    const h = hint(el.dataset.kbd);
    if (el.dataset.kbdTitle) el.title = fill(el.dataset.kbdTitle, h);
    if (el.dataset.kbdAria) el.setAttribute('aria-label', fill(el.dataset.kbdAria, h));
  });
  root.querySelectorAll('[data-kbd-caps]').forEach((el) => {
    const b = bindingsOf(el.dataset.kbdCaps)[0];
    el.hidden = !b;
    const out = el.querySelector('.kbd-caps') || el;
    out.innerHTML = b ? caps(b) : '';
  });
}

// ─── Open / init ──────────────────────────────────────────────────────────

/** `from: 'settings'` shows a back button (and Esc) that returns to Settings. */
export function openKeymap({ back = null } = {}) {
  query = '';
  rec = null;
  onBack = back;
  const search = $(`${ID}-search`);
  if (search) search.value = '';
  openModal(ID, { focus: `${ID}-search`, onClose: () => { rec = null; onBack = null; } });
  if (back) {
    setView(ID, {
      title: 'Keyboard shortcuts',
      sub: 'Click a shortcut to change it',
      backLabel: 'Settings',
      back: () => {
        const fn = onBack;
        closeModal(ID);
        fn?.();
      },
    });
  }
  render();
}

export function initKeymapEditor() {
  const bar = $(`${ID}-bar`);
  if (bar) bar.innerHTML = toolbar({ id: `${ID}-search`, placeholder: 'Search shortcuts or keys', label: 'Search keyboard shortcuts', kbd: false });
  $(`${ID}-search`)?.addEventListener('input', (e) => {
    query = e.target.value;
    if (rec) rec = null;
    render();
  });
  $(ID)?.addEventListener('click', onClick);
  // A click outside the row being recorded cancels it.
  $(ID)?.addEventListener('pointerdown', (e) => {
    if (rec && !e.target.closest?.('.km-row.is-rec')) stopRec({ focus: false });
  });
  window.addEventListener('keydown', onRecKey, true);
  window.addEventListener('keyup', onRecKey, true);
  window.addEventListener('blur', () => { if (rec?.held) { rec.held = ''; render(); } });
  onKeymapChange(() => {
    applyKeyHints();
    render();
  });
  applyKeyHints();
}
