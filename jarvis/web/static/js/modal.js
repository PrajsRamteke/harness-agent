/** Modal stack (Esc closes the top one, focus is trapped and restored) + list keyboard nav */
import { $, trapFocus } from './utils.js';

const stack = [];

export function isModalOpen(id) {
  return stack.some((m) => m.id === id);
}

export function topModal() {
  return stack[stack.length - 1]?.id || null;
}

export function openModal(id, { onClose, focus } = {}) {
  const el = $(id);
  if (!el) return;
  if (!isModalOpen(id)) {
    stack.push({ id, onClose, restore: document.activeElement });
  } else {
    const entry = stack.find((m) => m.id === id);
    entry.onClose = onClose;
  }
  el.hidden = false;
  document.body.classList.add('has-modal');
  const target = typeof focus === 'string' ? $(focus) : focus;
  // No explicit target: focus the dialog itself (Tab then walks its controls)
  // rather than ringing the close button.
  const card = el.querySelector('.modal-card');
  if (card && !card.hasAttribute('tabindex')) card.setAttribute('tabindex', '-1');
  requestAnimationFrame(() => (target || card || el)?.focus({ preventScroll: true }));
}

/** Close a modal. `reason` is passed to its onClose ('esc', 'backdrop', 'done'). */
export function closeModal(id, reason = 'done') {
  const idx = stack.findIndex((m) => m.id === id);
  if (idx === -1) return;
  const [entry] = stack.splice(idx, 1);
  const el = $(id);
  if (el) el.hidden = true;
  if (!stack.length) document.body.classList.remove('has-modal');
  try {
    entry.onClose?.(reason);
  } finally {
    if (entry.restore?.isConnected && !stack.length) entry.restore.focus?.({ preventScroll: true });
  }
}

export function initModals() {
  document.addEventListener('keydown', (e) => {
    const top = topModal();
    if (!top) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      closeModal(top, 'esc');
      return;
    }
    trapFocus($(top), e);
  }, true);

  document.addEventListener('click', (e) => {
    const btn = e.target.closest?.('[data-close]');
    if (btn) closeModal(btn.dataset.close);
  });

  document.querySelectorAll('.modal').forEach((el) => {
    el.addEventListener('mousedown', (e) => {
      if (e.target === el) closeModal(el.id, 'backdrop');
    });
  });
}

/**
 * Keyboard + hover cursor over `.list-row` children of `listEl`.
 * Rows need `data-idx`; `onPick(idx, event)` runs on Enter / click.
 */
export function listNav(listEl, onPick) {
  let cursor = 0;

  const rows = () => [...listEl.querySelectorAll('.list-row:not([aria-disabled="true"])')];

  function paint(scroll = true) {
    const all = rows();
    if (!all.length) return;
    cursor = Math.max(0, Math.min(cursor, all.length - 1));
    all.forEach((row, i) => {
      const on = i === cursor;
      row.classList.toggle('is-cursor', on);
      row.setAttribute('aria-selected', String(on));
    });
    if (scroll) all[cursor]?.scrollIntoView({ block: 'nearest' });
  }

  function reset(to = 0) {
    cursor = to;
    paint(false);
    listEl.scrollTop = 0;
  }

  function move(delta) {
    const n = rows().length;
    if (!n) return;
    cursor = (cursor + delta + n) % n;
    paint();
  }

  listEl.addEventListener('mousemove', (e) => {
    const row = e.target.closest('.list-row');
    if (!row) return;
    const i = rows().indexOf(row);
    if (i !== -1 && i !== cursor) {
      cursor = i;
      paint(false);
    }
  });

  listEl.addEventListener('click', (e) => {
    if (e.target.closest('.row-btn')) return;
    const row = e.target.closest('.list-row');
    if (!row || row.getAttribute('aria-disabled') === 'true') return;
    onPick(Number(row.dataset.idx), e);
  });

  function handleKey(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); move(1); return true; }
    if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); return true; }
    if (e.key === 'Enter' && !e.isComposing) {
      const row = rows()[cursor];
      if (row) {
        e.preventDefault();
        onPick(Number(row.dataset.idx), e);
      }
      return true;
    }
    return false;
  }

  return { reset, move, handleKey, paint, setCursor(i) { cursor = i; paint(); } };
}
