/** Drag-to-reorder for the PROJECTS list.
 *
 * Every direct child of the list with a `data-key` (a `.pj-group`) can be moved:
 *   - mouse / pen: press and drag
 *   - touch: press and hold for a moment, then drag (a quick swipe still scrolls)
 *   - keyboard: Alt + ↑ / ↓ on a focused row
 *
 * The group follows the pointer while the others slide out of the way. On drop,
 * `onChange(keys)` receives the new order. `onActive(true|false)` brackets a
 * drag so the caller can hold back re-renders (they would replace the nodes
 * being dragged).
 */

const HOLD_MS = 280;      // touch: how long to press before the drag starts
const MOVE_PX = 6;        // mouse: how far to move before it counts as a drag
const DROP_MS = 140;
const ITEM = '.pj-group';

export function initReorder(list, { onChange, onActive } = {}) {
  if (!list) return;
  let st = null;          // the press / drag in progress
  let suppressClick = false;

  const items = () => [...list.querySelectorAll(`:scope > ${ITEM}`)];
  const keysNow = () => items().map((el) => el.dataset.key);
  const listY = (clientY) => clientY - list.getBoundingClientRect().top;

  /** Move `sib` in the DOM via `mutate` and slide it from where it was to where it is now. */
  function slide(sib, mutate) {
    const before = sib.offsetTop;
    mutate();
    const delta = before - sib.offsetTop;
    if (!delta) return;
    sib.style.transition = 'none';
    sib.style.transform = `translateY(${delta}px)`;
    void sib.offsetHeight; // commit the starting point
    sib.style.transition = '';
    sib.style.transform = '';
  }

  function place() {
    const el = st.el;
    const top = listY(st.y) - st.grab;
    // Swap past every neighbour whose middle the dragged group's middle has crossed.
    for (let moved = true; moved;) {
      moved = false;
      const all = items();
      const i = all.indexOf(el);
      const center = top + el.offsetHeight / 2;
      const prev = all[i - 1];
      const next = all[i + 1];
      if (prev && center < prev.offsetTop + prev.offsetHeight / 2) {
        slide(prev, () => list.insertBefore(el, prev));
        moved = true;
      } else if (next && center > next.offsetTop + next.offsetHeight / 2) {
        slide(next, () => list.insertBefore(next, el));
        moved = true;
      }
    }
    // Keep it inside the list.
    const max = list.offsetHeight - el.offsetHeight;
    const clamped = Math.min(Math.max(top, 0), Math.max(max, 0));
    el.style.transform = `translateY(${clamped - el.offsetTop}px)`;
  }

  function begin() {
    clearTimeout(st.timer);
    const el = st.el;
    st.active = true;
    st.grab = listY(st.startY) - el.offsetTop;
    st.y = st.startY;
    el.classList.add('is-dragging');
    list.classList.add('is-reordering');
    try { el.setPointerCapture(st.id); } catch { /* pointer already gone */ }
    if (typeof navigator.vibrate === 'function') navigator.vibrate(8);
    onActive?.(true);
    place();
  }

  function end(commit) {
    if (!st) return;
    clearTimeout(st.timer);
    const cur = st;
    st = null;
    if (!cur.active) return;
    const { el } = cur;
    list.classList.remove('is-reordering');
    try { el.releasePointerCapture(cur.id); } catch { /* already released */ }
    // Settle into its slot.
    el.style.transition = `transform ${DROP_MS}ms var(--ease, ease)`;
    el.style.transform = '';
    setTimeout(() => {
      el.style.transition = '';
      el.classList.remove('is-dragging');
    }, DROP_MS);
    suppressClick = true;
    setTimeout(() => { suppressClick = false; }, 60);
    if (commit) onChange?.(keysNow());
    onActive?.(false);
  }

  list.addEventListener('pointerdown', (e) => {
    if (st) end(false);
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    if (e.target.closest('.pr-stop')) return;
    const el = e.target.closest(ITEM);
    if (!el || el.parentElement !== list || items().length < 2) return;
    const touch = e.pointerType !== 'mouse';
    st = { el, id: e.pointerId, touch, startX: e.clientX, startY: e.clientY, y: e.clientY, active: false, grab: 0, timer: 0 };
    if (touch) st.timer = setTimeout(() => { if (st && !st.active) begin(); }, HOLD_MS);
  });

  window.addEventListener('pointermove', (e) => {
    if (!st || e.pointerId !== st.id) return;
    if (!st.active) {
      if (Math.hypot(e.clientX - st.startX, e.clientY - st.startY) < MOVE_PX) return;
      if (st.touch) { // moved before the hold finished: the user is scrolling
        clearTimeout(st.timer);
        st = null;
        return;
      }
      begin();
    }
    e.preventDefault();
    st.y = e.clientY;
    place();
  });
  window.addEventListener('pointerup', (e) => { if (st && e.pointerId === st.id) end(true); });
  window.addEventListener('pointercancel', (e) => { if (st && e.pointerId === st.id) end(false); });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && st?.active) end(false);
  });

  // Once the drag started, the touch must not scroll the page.
  list.addEventListener('touchmove', (e) => { if (st?.active) e.preventDefault(); }, { passive: false });
  // No link-drag ghost on mouse, no long-press menu on touch while holding a row.
  list.addEventListener('dragstart', (e) => { if (items().length > 1) e.preventDefault(); });
  list.addEventListener('contextmenu', (e) => { if (st) e.preventDefault(); });
  // The click that ends a drag must not switch project.
  list.addEventListener('click', (e) => {
    if (!suppressClick) return;
    e.preventDefault();
    e.stopPropagation();
  }, true);

  // Keyboard: Alt + ↑ / ↓ moves the focused row's group.
  list.addEventListener('keydown', (e) => {
    if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
    const el = e.target.closest(ITEM);
    if (!el || el.parentElement !== list) return;
    const sib = e.key === 'ArrowUp' ? el.previousElementSibling : el.nextElementSibling;
    if (!sib?.matches(ITEM)) return;
    e.preventDefault();
    const focused = document.activeElement;
    list.insertBefore(el, e.key === 'ArrowUp' ? sib : sib.nextElementSibling);
    if (focused instanceof HTMLElement) focused.focus();
    onChange?.(keysNow());
  });
}
