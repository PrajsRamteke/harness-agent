/** Pinned context — the web /pin: standing instructions sent with every message.
 *
 *   [📌 Add an instruction Jarvis should always follow…        ] [+ Pin]
 *   1  Use pnpm, never npm or yarn.                        ✎  ×
 *   2  Answer briefly; show code, not essays.               ✎  ×
 *   ──────────────────────────────────────────────────────────────────
 *   [● Send with every message]   3 lines · 120 chars    Edit as text · Clear
 *
 * One line = one pin: edit it in place (Enter saves, Esc cancels), × removes
 * it (with Undo). "Edit as text" opens the whole text for bigger changes.
 * Same text and file as the terminal's /pin (jarvis/web/pin_api.py →
 * storage/pin.py). Opened by /pin or ⌘K "Pinned context" — no sidebar entry.
 */
import { $, escapeHtml, showToast, haptic, isMac } from './utils.js';
import { icon } from './icons.js';
import { store, patchStore, subscribe } from './store.js';
import { openModal, isModalOpen } from './modal.js';
import { fetchPin, pinPost } from './api.js';
import { patch, spin, plural } from './extui.js';
import { setView, setHomeSub, footer, empty } from './dialog.js';

const STARTERS = [
  'Use pnpm, never npm or yarn.',
  'Answer briefly. Show code, not essays.',
  'Never commit or push without asking me first.',
  'Write tests for new code and run them.',
];
const HOME_SUB = 'Sent with every message, in every chat';

let data = null; // last /api/pin
let loadError = false;
let editing = null; // { line, original, draft } — one pin being edited in place
let raw = null; // { draft, original } — "Edit as text"
let saving = false;
let flash = 0; // line to highlight after a change
let undo = null; // { before, label, timer } — the text before a removal
let added = ''; // the add box's text (kept across re-renders)

// ─── Data ─────────────────────────────────────────────────────────────────

function setData(next) {
  if (!next || !Array.isArray(next.items)) return;
  data = next;
  loadError = false;
  patchStore({ pin: { lines: next.lines, enabled: !!next.enabled, chars: next.chars } });
}

/** Pins changed in the terminal (/pin, its dialog): the session state says so — reload an open list. */
function syncFromState(s) {
  if (!data || !isModalOpen('pin') || saving || raw || editing) return;
  const p = s.pin || {};
  if (p.lines !== data.lines || p.chars !== data.chars || !!p.enabled !== !!data.enabled) refreshPin();
}

export async function refreshPin() {
  try {
    setData(await fetchPin());
  } catch {
    if (!data) loadError = true;
  }
  if (isModalOpen('pin')) render();
}

async function op(payload, { quiet = false } = {}) {
  saving = true;
  render();
  const res = await pinPost(payload);
  saving = false;
  if (res.pin) setData(res.pin);
  if (!res.ok && !quiet) showToast(res.error || 'Could not save the pin', true);
  render();
  return res;
}

// ─── Markup ───────────────────────────────────────────────────────────────

function addBar() {
  return `<form class="dlg-toolbar pin-add" data-act="add-form" autocomplete="off">
    <label class="dlg-search pin-add-field">
      <span class="dlg-search-ic">${icon('pin')}</span>
      <input id="pin-new" type="text" placeholder="Add an instruction Jarvis should always follow" aria-label="New pinned instruction"
        maxlength="2000" autocomplete="off" spellcheck="true" enterkeyhint="done" data-1p-ignore data-lpignore="true" data-form-type="other">
      <span class="dlg-kbd" aria-hidden="true"><kbd>↵</kbd></span>
    </label>
    <button type="submit" class="btn btn-primary dlg-action" id="pin-add-btn" disabled>${icon('plus')}<span>Pin</span></button>
  </form>`;
}

function rowHtml(it, i) {
  if (editing?.line === it.line) {
    return `<li class="pin-row is-editing" data-line="${it.line}">
      <span class="pin-num" aria-hidden="true">${icon('pencil')}</span>
      <span class="pin-edit">
        <textarea class="pin-input" rows="1" aria-label="Edit pin ${i + 1}" spellcheck="true"></textarea>
        <span class="pin-edit-foot">
          <span class="pin-edit-hint"><kbd>↵</kbd> save · <kbd>esc</kbd> cancel</span>
          <span class="pin-edit-acts">
            <button type="button" class="btn btn-sm btn-quiet" data-act="cancel-edit">Cancel</button>
            <button type="button" class="btn btn-sm btn-primary" data-act="save-edit"${saving ? ' disabled' : ''}>${saving ? spin('Saving') : `${icon('check')}<span>Save</span>`}</button>
          </span>
        </span>
      </span>
    </li>`;
  }
  const text = escapeHtml(it.text);
  return `<li class="pin-row${flash === it.line ? ' just-added' : ''}" data-line="${it.line}" style="--i:${i}">
    <span class="pin-num" aria-hidden="true">${i + 1}</span>
    <button type="button" class="pin-text" data-act="edit" title="Click to edit">${text}</button>
    <span class="pin-acts">
      <button type="button" class="row-btn is-icon" data-act="edit" aria-label="Edit “${text.slice(0, 50)}”" title="Edit">${icon('pencil')}</button>
      <button type="button" class="row-btn is-icon pin-del" data-act="remove" aria-label="Unpin “${text.slice(0, 50)}”" title="Unpin">${icon('x')}</button>
    </span>
  </li>`;
}

function emptyHtml() {
  return `<div class="pin-empty">
    ${empty('Nothing pinned yet', 'Pin rules Jarvis should follow in every message of every chat: your tools, your style, things to avoid.', { ic: 'pin' })}
    <div class="pin-starters-label">Start from an example</div>
    <div class="pin-starters">${STARTERS.map((s, i) => `<button type="button" class="pin-starter" data-act="starter" data-i="${i}" style="--i:${i}">${icon('plus')}<span>${escapeHtml(s)}</span></button>`).join('')}</div>
  </div>`;
}

function noticeHtml() {
  const out = [];
  if (data && !data.enabled && data.items.length) {
    out.push(`<p class="pv-msg is-warn pin-paused" role="status">${icon('pin-off')}<span><strong>Paused.</strong> Your pins are saved but not sent to Jarvis.</span>
      <button type="button" class="btn btn-sm" data-act="enable">Turn on</button></p>`);
  }
  if (undo) {
    const what = undo.cleared ? `Cleared ${escapeHtml(undo.label)}.` : `Unpinned “${escapeHtml(undo.label)}”.`;
    out.push(`<p class="pv-msg is-ok pin-undo" role="status">${icon('circle-check')}<span>${what}</span>
      <button type="button" class="btn btn-sm" data-act="undo">${icon('undo-2')}<span>Undo</span></button></p>`);
  }
  return out.join('');
}

function listHtml() {
  if (loadError) {
    return empty('Couldn’t load your pins', 'Check that Jarvis is still running.', {
      ic: 'circle-alert', action: '<button type="button" class="btn" data-act="reload">Try again</button>',
    });
  }
  if (!data) return `<div class="pin-loading">${spin('Loading')}</div>`;
  if (!data.items.length) return emptyHtml();
  return `<ol class="pin-list${data.enabled ? '' : ' is-paused'}" aria-label="Pinned instructions">${data.items.map(rowHtml).join('')}</ol>`;
}

function footHtml() {
  if (!data) return '';
  const on = !!data.enabled;
  const sw = `<label class="pin-switch" title="${on ? 'Pins go to Jarvis with every message' : 'Paused: pins are saved but not sent'}">
    <input type="checkbox" class="switch" role="switch" data-act="toggle"${on ? ' checked' : ''}${data.items.length ? '' : ' disabled'}>
    <span>Send with every message</span></label>`;
  const n = data.items.length;
  const note = n ? `${plural(n, 'pin')} · ${data.chars.toLocaleString()} chars` : '';
  const acts = n
    ? `<button type="button" class="btn btn-sm btn-quiet" data-act="raw">${icon('file-pen')}<span>Edit as text</span></button>
       <button type="button" class="btn btn-sm btn-quiet pin-clear" data-act="clear">${icon('trash-2')}<span>Clear</span></button>`
    : '';
  return `${sw}${note ? `<span class="dlg-note"><span>${note}</span></span>` : ''}<span class="dlg-spacer"></span><span class="pin-foot-acts">${acts}</span>`;
}

function rawHtml() {
  return `<div class="pin-raw">
    <p class="pin-raw-help">One instruction per line. Delete a line to unpin it.</p>
    <textarea id="pin-raw" class="cm-body pin-raw-input" rows="10" spellcheck="true" aria-label="All pinned context"></textarea>
    <p class="ex-path">Saved in <code>${escapeHtml(String(data?.file || '').replace(/^\/(?:Users|home)\/[^/]+/, '~'))}</code> · the same file as /pin in the terminal</p>
  </div>`;
}

function rawFootHtml() {
  const key = isMac ? '⌘↵' : 'Ctrl+↵';
  const dirty = raw && raw.draft !== raw.original;
  return `<span class="dlg-note"><span>${dirty ? 'Unsaved changes' : 'No changes yet'}</span></span><span class="dlg-spacer"></span>
    <span class="dlg-actions">
      <button type="button" class="btn btn-quiet" data-act="raw-cancel">Cancel</button>
      <button type="button" class="btn btn-primary" data-act="raw-save" title="Save (${key})"${dirty && !saving ? '' : ' disabled'}>${saving ? spin('Saving…') : `${icon('check')}<span>Save</span>`}</button>
    </span>`;
}

function grow(ta, max = 200) {
  if (!ta) return;
  ta.style.height = 'auto';
  if (ta.scrollHeight) ta.style.height = `${Math.min(ta.scrollHeight + 2, max)}px`;
}

// ─── Render ───────────────────────────────────────────────────────────────

let built = ''; // 'list' | 'raw'

function render() {
  const bar = $('pin-bar');
  const body = $('pin-body');
  const foot = $('pin-foot');
  if (!bar || !body || !foot) return;
  if (raw) {
    if (built !== 'raw') {
      setView('pin', { title: 'Edit as text', sub: 'All pinned context, one instruction per line', back: leaveRaw, backLabel: 'pins' });
      bar.innerHTML = '';
      body.innerHTML = rawHtml();
      const ta = $('pin-raw');
      ta.value = raw.draft;
      requestAnimationFrame(() => {
        grow(ta, 460);
        ta.focus({ preventScroll: true });
        ta.setSelectionRange(ta.value.length, ta.value.length);
      });
      built = 'raw';
    }
    patch(foot, rawFootHtml());
    return;
  }
  if (built !== 'list') {
    if (!editing) setView('pin', null);
    bar.innerHTML = addBar();
    body.innerHTML = '<div id="pin-notice" class="pin-notice"></div><div id="pin-list"></div>';
    const input = $('pin-new');
    input.value = added;
    syncAddBtn();
    built = 'list';
  }
  const n = data?.items?.length || 0;
  setHomeSub('pin', n ? `${plural(n, 'pin')} · ${data.enabled ? 'sent with every message' : 'paused'}` : HOME_SUB);
  patch($('pin-notice'), noticeHtml());
  const list = $('pin-list');
  const before = list._html;
  patch(list, listHtml());
  if (editing && list._html !== before) {
    const ta = list.querySelector('.pin-input');
    if (ta) {
      ta.value = editing.draft;
      requestAnimationFrame(() => {
        grow(ta);
        ta.focus({ preventScroll: true });
        ta.setSelectionRange(ta.value.length, ta.value.length);
      });
    }
  }
  patch(foot, footHtml());
  if (flash) {
    const line = flash;
    flash = 0;
    requestAnimationFrame(() => list.querySelector(`[data-line="${line}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
  }
}

function syncAddBtn() {
  const btn = $('pin-add-btn');
  if (btn) btn.disabled = saving || !added.trim();
}

// ─── Actions ──────────────────────────────────────────────────────────────

async function addPin() {
  const text = added.trim();
  if (!text || saving) return;
  const res = await op({ op: 'add', text });
  if (!res.ok) return;
  haptic(10);
  added = '';
  const input = $('pin-new');
  if (input) input.value = '';
  syncAddBtn();
  flash = data?.items?.[data.items.length - 1]?.line || 0;
  clearUndo();
  render();
  input?.focus({ preventScroll: true });
}

function startEdit(line) {
  const it = data?.items?.find((x) => x.line === line);
  if (!it) return;
  if (editing && editing.line !== line) saveEdit();
  editing = { line, original: it.text, draft: it.text };
  setView('pin', { title: 'Pinned context', sub: `Editing pin ${data.items.indexOf(it) + 1}`, back: cancelEdit, backLabel: 'pins' });
  render();
}

function cancelEdit() {
  if (!editing) return;
  const { line } = editing;
  editing = null;
  setView('pin', null);
  render();
  $('pin-list')?.querySelector(`[data-line="${line}"] .pin-text`)?.focus({ preventScroll: true });
}

async function saveEdit() {
  if (!editing) return;
  const { line, original, draft } = editing;
  if (draft.trim() === original.trim()) {
    cancelEdit();
    return;
  }
  editing = null;
  setView('pin', null);
  if (!draft.trim()) {
    await removePin(line, original);
    return;
  }
  const res = await op({ op: 'update', line, expect: original, text: draft });
  if (res.ok) {
    haptic(8);
    flash = line;
    render();
  }
}

function clearUndo() {
  if (undo?.timer) clearTimeout(undo.timer);
  undo = null;
}

async function removePin(line, text) {
  const before = data?.text || '';
  const row = $('pin-list')?.querySelector(`[data-line="${line}"]`);
  row?.classList.add('is-leaving');
  haptic(8);
  const res = await op({ op: 'remove', line, expect: text });
  if (!res.ok) return;
  clearUndo();
  undo = { before, label: text.length > 48 ? `${text.slice(0, 47)}…` : text };
  undo.timer = setTimeout(() => {
    undo = null;
    if (isModalOpen('pin')) render();
  }, 8000);
  render();
}

async function undoRemove() {
  if (!undo) return;
  const { before } = undo;
  clearUndo();
  const res = await op({ op: 'save', text: before });
  if (res.ok) showToast('Pin restored');
}

async function setEnabled(on) {
  const res = await op({ op: 'toggle', enabled: on });
  if (res.ok) showToast(on ? 'Pins are sent with every message' : 'Pins paused — saved, not sent');
}

let clearArmed = 0;
async function clearAll(btn) {
  if (!clearArmed) {
    clearArmed = setTimeout(() => {
      clearArmed = 0;
      if (btn.isConnected) {
        btn.classList.remove('is-asking');
        btn.querySelector('span').textContent = 'Clear';
      }
    }, 3000);
    btn.classList.add('is-asking');
    btn.querySelector('span').textContent = 'Clear all pins?';
    return;
  }
  clearTimeout(clearArmed);
  clearArmed = 0;
  const before = data?.text || '';
  const n = data?.items?.length || 0;
  const res = await op({ op: 'clear' });
  if (!res.ok) return;
  clearUndo();
  undo = { before, label: plural(n, 'pin'), cleared: true };
  undo.timer = setTimeout(() => {
    undo = null;
    if (isModalOpen('pin')) render();
  }, 10000);
  render();
}

function openRaw() {
  editing = null;
  raw = { draft: data?.text || '', original: data?.text || '' };
  built = '';
  render();
}

function leaveRaw() {
  if (raw && raw.draft !== raw.original && !raw.armed) {
    raw.armed = true;
    const btn = document.querySelector('#pin-foot [data-act="raw-cancel"]');
    if (btn) {
      btn.textContent = 'Discard changes?';
      btn.classList.add('is-asking');
    }
    return;
  }
  raw = null;
  built = '';
  setView('pin', null);
  render();
}

async function saveRaw() {
  if (!raw || saving) return;
  const res = await op({ op: 'save', text: raw.draft });
  if (!res.ok) return;
  haptic(10);
  raw = null;
  built = '';
  setView('pin', null);
  clearUndo();
  render();
  showToast('Pinned context saved');
}

// ─── Wiring ───────────────────────────────────────────────────────────────

function onClick(e) {
  const el = e.target.closest('[data-act]');
  const card = $('pin');
  if (!el || !card?.contains(el) || el.disabled) return;
  const line = Number(el.closest('[data-line]')?.dataset.line || 0);
  switch (el.dataset.act) {
    case 'edit': startEdit(line); break;
    case 'remove': {
      const it = data?.items?.find((x) => x.line === line);
      if (it) removePin(line, it.text);
      break;
    }
    case 'save-edit': saveEdit(); break;
    case 'cancel-edit': cancelEdit(); break;
    case 'starter': {
      added = STARTERS[Number(el.dataset.i)] || '';
      const input = $('pin-new');
      if (input) {
        input.value = added;
        input.focus();
        input.setSelectionRange(added.length, added.length);
      }
      syncAddBtn();
      break;
    }
    case 'enable': setEnabled(true); break;
    case 'undo': undoRemove(); break;
    case 'raw': openRaw(); break;
    case 'raw-cancel': leaveRaw(); break;
    case 'raw-save': saveRaw(); break;
    case 'clear': clearAll(el); break;
    case 'reload': refreshPin(); break;
    default:
  }
}

function onChange(e) {
  if (e.target.matches('[data-act="toggle"]')) setEnabled(e.target.checked);
}

function onSubmit(e) {
  if (!e.target.matches('.pin-add')) return;
  e.preventDefault();
  addPin();
}

function onInput(e) {
  const el = e.target;
  if (el.id === 'pin-new') {
    added = el.value;
    syncAddBtn();
  } else if (el.matches('.pin-input') && editing) {
    editing.draft = el.value;
    grow(el);
  } else if (el.id === 'pin-raw' && raw) {
    raw.draft = el.value;
    raw.armed = false;
    patch($('pin-foot'), rawFootHtml());
  }
}

function onKey(e) {
  if (e.isComposing) return;
  const el = e.target;
  if (el.id === 'pin-new' && e.key === 'Enter') {
    e.preventDefault(); // the form's own submit is skipped while its button is disabled
    addPin();
  } else if (el.matches('.pin-input') && e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    saveEdit();
  } else if (el.id === 'pin-raw' && e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    saveRaw();
  }
}

/** `arg`: '' (the list) or 'edit' (the whole text). */
export async function openPin(arg = '') {
  built = '';
  editing = null;
  raw = null;
  render();
  openModal('pin', {
    focus: undefined,
    onClose: () => {
      editing = null;
      raw = null;
      built = '';
      clearUndo();
    },
  });
  await refreshPin();
  if (!isModalOpen('pin')) return;
  if (String(arg).trim().toLowerCase() === 'edit' && data?.items?.length) openRaw();
  else requestAnimationFrame(() => $('pin-new')?.focus({ preventScroll: true }));
}

/** Another tab or the terminal changed the pins. */
export function handlePinEvent(next) {
  if (next && Array.isArray(next.items)) {
    setData(next);
    if (isModalOpen('pin') && !raw) render();
  } else {
    refreshPin();
  }
}

export function initPin() {
  const card = $('pin');
  card?.addEventListener('click', onClick);
  card?.addEventListener('change', onChange);
  card?.addEventListener('submit', onSubmit);
  card?.addEventListener('input', onInput);
  card?.addEventListener('keydown', onKey);
  // state_fields.pin tells an open dialog that the terminal changed the pins.
  if (!store.pin) patchStore({ pin: { lines: 0, enabled: true } });
  subscribe(syncFromState);
}
