/** The queue panel above the composer — messages sent while Jarvis works.
 *
 *   ≡ Queued 2 · Send in order when Jarvis finishes ................ Clear
 *   1  fix the failing auth test …            [↳ Send now] [✎] [×]
 *   •  then update the docs  Sending at the next step   [Undo] [×]
 *
 * Send now: the running turn hands the message to Jarvis at its next step
 * (between tool calls) instead of after the reply. Edit happens in place
 * (Enter saves, Esc cancels); × drops it. Same queue as the terminal's bar
 * (jarvis/prompt_queue.py) — a row being edited there is read-only here.
 */
import { $, escapeHtml, showToast, haptic, isMac } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe, patchStore } from './store.js';
import { queuePost } from './api.js';

const FOLD_AT = 3; // rows shown before "Show N more"

let editing = null; // { id, draft }
let expanded = false;
let busyRow = ''; // id with a request in flight
let lastSig = '';
let fillComposer = null; // composer.js: (text) => put text in the message box

const panel = () => $('queue-panel');

function rowSig(it, i) {
  return [it.id, i, it.text, it.label, it.steer, it.steerable, it.editing, (it.files || []).length, busyRow === it.id].join('\u0001');
}

function filesHtml(it) {
  const files = it.files || [];
  if (!files.length) return '';
  const names = files.slice(0, 3).map((f) => escapeHtml(f.name || 'file')).join(' · ');
  const more = files.length > 3 ? ` +${files.length - 3}` : '';
  return `<span class="qp-files">${icon('paperclip')}<span>${names}${more}</span></span>`;
}

function textHtml(it) {
  const text = (it.text || '').trim();
  if (!text) return '';
  const cmd = it.command ? ' is-command' : '';
  return `<span class="qp-text${cmd}">${escapeHtml(text)}</span>`;
}

function actionsHtml(it) {
  const preview = escapeHtml((it.text || it.label || '').slice(0, 60));
  if (it.editing) {
    return `<span class="qp-state is-held">${icon('pencil')}<span>Editing in the terminal</span></span>`;
  }
  const pending = busyRow === it.id;
  const remove = `<button type="button" class="qp-btn is-icon is-remove" data-act="remove" aria-label="Remove “${preview}” from the queue" title="Remove from the queue"${pending ? ' disabled' : ''}>${icon('x')}</button>`;
  if (it.steer) {
    return `<button type="button" class="qp-btn" data-act="unsteer" title="Wait for the reply to finish instead"${pending ? ' disabled' : ''}>${icon('undo-2')}<span>Undo</span></button>${remove}`;
  }
  const send = it.steerable
    ? `<button type="button" class="qp-btn is-send" data-act="steer" title="Jarvis reads it at its next step instead of after this reply"${pending ? ' disabled' : ''}>${pending ? '<span class="spinner" aria-hidden="true"></span>' : icon('corner-down-right')}<span>Send now</span></button>`
    : '';
  const edit = `<button type="button" class="qp-btn is-icon" data-act="edit" aria-label="Edit “${preview}”" title="Edit"${pending ? ' disabled' : ''}>${icon('pencil')}</button>`;
  return `${send}${edit}${remove}`;
}

function rowInner(it, i) {
  const lead = it.steer
    ? '<span class="qp-num is-steer" aria-hidden="true"><span class="qp-pulse"></span></span>'
    : `<span class="qp-num" aria-hidden="true">${i + 1}</span>`;
  const note = it.steer ? '<span class="qp-note">Sending at the next step</span>' : '';
  return `${lead}
    <span class="qp-main">${textHtml(it)}${filesHtml(it)}${note}</span>
    <span class="qp-acts">${actionsHtml(it)}</span>`;
}

function editorInner(it) {
  const key = isMac ? '⌘↵' : 'Ctrl+↵';
  return `<span class="qp-num" aria-hidden="true">${icon('pencil')}</span>
    <span class="qp-edit">
      <textarea class="qp-input" rows="1" aria-label="Edit the queued message" spellcheck="true"></textarea>
      <span class="qp-edit-foot">
        <span class="qp-edit-hint"><kbd>↵</kbd> save · <kbd>Shift</kbd><kbd>↵</kbd> new line · <kbd>esc</kbd> cancel</span>
        <span class="qp-edit-acts">
          <button type="button" class="btn btn-sm btn-quiet" data-act="cancel">Cancel</button>
          <button type="button" class="btn btn-sm btn-primary" data-act="save" title="Save (${key})">${icon('check')}<span>Save</span></button>
        </span>
      </span>
    </span>`;
}

function grow(ta) {
  if (!ta) return;
  ta.style.height = 'auto';
  if (ta.scrollHeight) ta.style.height = `${Math.min(ta.scrollHeight, 180)}px`;
}

function headHtml(items) {
  const steered = items.filter((it) => it.steer).length;
  const sub = steered && store.busy
    ? 'Sending at the next step, the rest when Jarvis finishes'
    : store.busy ? 'Sends in order when Jarvis finishes' : 'Sending…';
  const clear = items.filter((it) => !it.editing).length > 1
    ? `<button type="button" class="qp-clear" data-act="clear" title="Remove every queued message">Clear all</button>`
    : '';
  return `<span class="qp-head-ic">${icon('list-ordered')}</span>
    <strong>Queued</strong><span class="qp-count">${items.length}</span>
    <span class="qp-sub">${sub}</span>${clear}`;
}

function render(s = store) {
  const box = panel();
  if (!box) return;
  const items = s.queueItems || [];
  if (editing && !items.some((it) => it.id === editing.id)) lostWhileEditing();
  const sig = `${items.map(rowSig).join('\u0002')}|${expanded}|${editing?.id || ''}|${store.busy}`;
  if (sig === lastSig) return;
  lastSig = sig;

  if (!items.length) {
    if (!box.hidden && !box.classList.contains('is-leaving')) {
      box.classList.add('is-leaving');
      setTimeout(() => {
        if (!(store.queueItems || []).length) {
          box.hidden = true;
          box.innerHTML = '';
        }
        box.classList.remove('is-leaving');
      }, 220);
    }
    expanded = false;
    return;
  }
  box.classList.remove('is-leaving');
  if (box.hidden || !box.querySelector('.qp-list')) {
    box.innerHTML = '<div class="qp-head"></div><ol class="qp-list"></ol><button type="button" class="qp-more" data-act="more" hidden></button>';
    box.hidden = false;
  }
  box.querySelector('.qp-head').innerHTML = headHtml(items);

  // Keyed rows: only changed rows are rebuilt, so an open editor keeps its text and caret.
  const list = box.querySelector('.qp-list');
  const byId = new Map([...list.children].map((el) => [el.dataset.id, el]));
  const showAll = expanded || items.length <= FOLD_AT + 1;
  items.forEach((it, i) => {
    let el = byId.get(it.id);
    byId.delete(it.id);
    const isEditing = editing?.id === it.id && !it.editing;
    const sig = isEditing ? `edit:${it.id}` : rowSig(it, i);
    if (!el) {
      el = document.createElement('li');
      el.dataset.id = it.id;
      el.className = 'qp-row is-new';
      setTimeout(() => el.classList.remove('is-new'), 600);
    }
    if (el.dataset.sig !== sig) {
      el.dataset.sig = sig;
      el.innerHTML = isEditing ? editorInner(it) : rowInner(it, i);
      if (isEditing) {
        const ta = el.querySelector('.qp-input');
        ta.value = editing.draft;
        requestAnimationFrame(() => {
          grow(ta);
          ta.focus({ preventScroll: true });
          ta.setSelectionRange(ta.value.length, ta.value.length);
        });
      }
    }
    el.classList.toggle('is-steer', !!it.steer);
    el.classList.toggle('is-held', !!it.editing);
    el.classList.toggle('is-editing', isEditing);
    el.hidden = !showAll && i >= FOLD_AT && !isEditing;
    if (list.children[i] !== el) list.insertBefore(el, list.children[i] || null);
  });
  byId.forEach((el) => el.remove());

  const more = box.querySelector('.qp-more');
  const hiddenCount = showAll ? 0 : items.length - FOLD_AT;
  more.hidden = items.length <= FOLD_AT + 1;
  more.innerHTML = expanded
    ? `${icon('chevron-up')}<span>Show less</span>`
    : `${icon('chevron-down')}<span>Show ${hiddenCount} more</span>`;
  more.setAttribute('aria-expanded', String(expanded));
}

/** The message went to Jarvis (or was removed elsewhere) while being edited here. */
function lostWhileEditing() {
  const draft = editing?.draft || '';
  editing = null;
  if (draft.trim() && fillComposer) {
    fillComposer(draft);
    showToast('That message already went to Jarvis. Your edit is in the message box.');
  }
}

async function post(data, id = '') {
  busyRow = id;
  render();
  const res = await queuePost(data);
  busyRow = '';
  if (res.entries) patchStore({ queueItems: res.entries, queue: res.items || [] });
  else render();
  if (!res.ok && res.code !== 'gone') showToast(res.error || 'Could not change the queue', true);
  return res;
}

function startEdit(id) {
  const it = (store.queueItems || []).find((x) => x.id === id);
  if (!it || it.editing) return;
  if (editing && editing.id !== id) saveEdit();
  editing = { id, draft: it.text || '' };
  expanded = expanded || (store.queueItems || []).indexOf(it) >= FOLD_AT;
  render();
}

function cancelEdit() {
  if (!editing) return;
  const { id } = editing;
  editing = null;
  render();
  panel()?.querySelector(`[data-id="${CSS.escape(id)}"] [data-act="edit"]`)?.focus({ preventScroll: true });
}

async function saveEdit() {
  if (!editing) return;
  const { id, draft } = editing;
  const it = (store.queueItems || []).find((x) => x.id === id);
  editing = null;
  if (!it) {
    render();
    return;
  }
  if (draft.trim() === (it.text || '').trim()) {
    render();
    return;
  }
  if (!draft.trim() && !(it.files || []).length) {
    await post({ op: 'remove', id }, id);
    return;
  }
  const res = await post({ op: 'edit', id, text: draft }, id);
  if (res.ok) haptic(8);
  else if (res.code === 'gone' && draft.trim() && fillComposer) {
    fillComposer(draft);
    showToast('That message already went to Jarvis. Your edit is in the message box.');
  }
}

function removeRow(id) {
  const el = panel()?.querySelector(`[data-id="${CSS.escape(id)}"]`);
  if (editing?.id === id) editing = null;
  if (el) el.classList.add('is-leaving');
  haptic(8);
  setTimeout(() => post({ op: 'remove', id }, id), el ? 160 : 0);
}

function onClick(e) {
  const btn = e.target.closest('[data-act]');
  if (!btn || !panel()?.contains(btn) || btn.disabled) return;
  const id = btn.closest('.qp-row')?.dataset.id || '';
  switch (btn.dataset.act) {
    case 'steer':
      haptic(12);
      post({ op: 'steer', id }, id).then((res) => {
        if (res.ok) showToast(store.busy ? 'Sent — Jarvis reads it at its next step' : 'Sending now');
      });
      break;
    case 'unsteer': post({ op: 'unsteer', id }, id); break;
    case 'edit': startEdit(id); break;
    case 'remove': removeRow(id); break;
    case 'save': saveEdit(); break;
    case 'cancel': cancelEdit(); break;
    case 'clear': {
      if (btn.classList.contains('is-asking')) {
        editing = null;
        post({ op: 'clear' });
      } else {
        btn.classList.add('is-asking');
        btn.textContent = 'Clear all?';
        setTimeout(() => {
          if (btn.isConnected) {
            btn.classList.remove('is-asking');
            btn.textContent = 'Clear all';
          }
        }, 3000);
      }
      break;
    }
    case 'more':
      expanded = !expanded;
      render();
      break;
    default:
  }
}

function onInput(e) {
  if (!e.target.matches('.qp-input') || !editing) return;
  editing.draft = e.target.value;
  grow(e.target);
}

function onKey(e) {
  if (!e.target.matches('.qp-input')) return;
  if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();
    cancelEdit();
  } else if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    saveEdit();
  }
}

/** ↑ in an empty message box while Jarvis works: edit the newest queued message. */
export function editLastQueued() {
  const items = (store.queueItems || []).filter((it) => !it.editing);
  const last = items[items.length - 1];
  if (!last) return false;
  startEdit(last.id);
  return true;
}

export function initQueue({ onFill } = {}) {
  fillComposer = onFill || null;
  const box = panel();
  box?.addEventListener('click', onClick);
  box?.addEventListener('input', onInput);
  box?.addEventListener('keydown', onKey);
  subscribe(render);
  render();
}
