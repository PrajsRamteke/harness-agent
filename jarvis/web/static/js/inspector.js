/** The right-hand panel's shell: open / close, tabs, width, the top-bar button.
 *
 * What goes inside lives in changes.js (files + diffs) and activity.js. On a
 * wide screen the panel is docked and open by default; below 1280px it floats
 * over the page, and on a phone it is a sheet you can swipe away.
 */
import { $, storageGet, storageSet, countTo, animateEl, reducedMotion, haptic, SPRING } from './utils.js';
import { icon } from './icons.js';
import { topModal } from './modal.js';

const OPEN_KEY = 'jarvis-inspector';
const WIDTH_KEY = 'jarvis-inspector-w';
const WIDE_KEY = 'jarvis-inspector-wide';
const TAB_KEY = 'jarvis-inspector-tab';

const TABS = ['changes', 'activity'];
const DEFAULT_W = 400;
const MIN_W = 340;
const ROOMY_W = 620;

const dockedQuery = window.matchMedia('(min-width: 1280px)');
const listeners = new Set();

let tab = 'changes';
let width = DEFAULT_W;
let wide = false;
let roomy = false;

const panel = () => $('inspector');

/** Docked layouts remember open/closed; floating ones always start closed. */
export const isDocked = () => dockedQuery.matches;
export const isInspectorOpen = () => document.body.classList.contains('insp-open');
export const activeTab = () => tab;
export const isRoomy = () => roomy;

/** `fn({ open, tab, roomy })` on every open / close / tab / width-class change. */
export function onInspectorChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  const state = { open: isInspectorOpen(), tab, roomy };
  for (const fn of listeners) {
    try { fn(state); } catch (err) { console.error('inspector listener failed', err); }
  }
}

// ─── Open / close ─────────────────────────────────────────────────────────

function setOpen(open, { persist = false, focus = false } = {}) {
  const was = isInspectorOpen();
  document.body.classList.toggle('insp-open', open);
  const el = panel();
  if (el) {
    el.setAttribute('aria-hidden', String(!open));
    el.toggleAttribute('inert', !open);
  }
  $('insp-btn')?.setAttribute('aria-expanded', String(open));
  if (open) document.body.classList.remove('side-open');
  if (persist && isDocked()) storageSet(OPEN_KEY, open ? 'open' : 'closed');
  if (open && focus && !was) {
    // After the slide starts, so the focus ring doesn't jump around mid-move.
    setTimeout(() => $(`tab-${tab}`)?.focus({ preventScroll: true }), 60);
  }
  if (!open && was && el?.contains(document.activeElement)) $('insp-btn')?.focus({ preventScroll: true });
  if (was !== open) emit();
}

export function openInspector(name) {
  if (name) selectTab(name);
  setOpen(true, { persist: true, focus: true });
}

export function closeInspector() {
  setOpen(false, { persist: true });
}

export function toggleInspector() {
  if (isInspectorOpen()) closeInspector();
  else openInspector();
}

// ─── Tabs ─────────────────────────────────────────────────────────────────

export function selectTab(name, { focus = false } = {}) {
  if (!TABS.includes(name)) return;
  const changed = name !== tab;
  tab = name;
  TABS.forEach((t) => {
    const on = t === name;
    const btn = $(`tab-${t}`);
    btn?.classList.toggle('is-active', on);
    btn?.setAttribute('aria-selected', String(on));
    btn?.setAttribute('tabindex', on ? '0' : '-1');
    const pane = $(`pane-${t}`);
    pane?.classList.toggle('is-active', on);
    pane?.toggleAttribute('inert', !on);
  });
  $('insp-tabs')?.style.setProperty('--i', String(TABS.indexOf(name)));
  storageSet(TAB_KEY, name);
  if (focus) $(`tab-${name}`)?.focus({ preventScroll: true });
  if (changed) emit();
}

/** The little number next to a tab's name. */
export function setTabCount(name, n) {
  const el = $(`tab-count-${name}`);
  if (!el) return;
  const text = n > 99 ? '99+' : String(n);
  const grew = !el.hidden && el.textContent !== text;
  el.hidden = n < 1;
  el.textContent = text;
  if (grew) {
    el.classList.remove('is-pop');
    requestAnimationFrame(() => el.classList.add('is-pop'));
  }
}

/** A breathing dot on a tab while something is going on in it. */
export function setTabLive(name, live) {
  const el = $(`tab-dot-${name}`);
  if (el) el.hidden = !live;
}

// ─── Top-bar button ───────────────────────────────────────────────────────

const fmtAdd = (n) => `+${n}`;
const fmtDel = (n) => `−${n}`;

/** Files / added / removed on the top-bar button (and its badge on small screens). */
export function setChangeStat({ files = 0, added = 0, removed = 0 } = {}, { animate = true } = {}) {
  const stat = $('insp-stat');
  const badge = $('insp-badge');
  if (stat) {
    stat.hidden = files < 1;
    countTo($('insp-add'), added, fmtAdd, { animate });
    countTo($('insp-del'), removed, fmtDel, { animate });
  }
  if (badge) {
    const text = files > 99 ? '99+' : String(files);
    const grew = !badge.hidden && badge.textContent !== text;
    badge.hidden = files < 1;
    badge.textContent = text;
    if (grew) {
      badge.classList.remove('is-pop');
      requestAnimationFrame(() => badge.classList.add('is-pop'));
    }
  }
  $('insp-btn')?.setAttribute('aria-label', files ? `Changes and activity — ${files} file${files === 1 ? '' : 's'} changed` : 'Changes and activity');
}

/** A soft ring around the button: "something landed over here". */
export function pulseInspectorButton() {
  const btn = $('insp-btn');
  if (!btn || isInspectorOpen()) return;
  btn.classList.remove('is-pulse');
  requestAnimationFrame(() => btn.classList.add('is-pulse'));
  clearTimeout(btn._pulseTimer);
  btn._pulseTimer = setTimeout(() => btn.classList.remove('is-pulse'), 1900);
}

// ─── Width ────────────────────────────────────────────────────────────────

const maxWidth = () => Math.max(MIN_W, Math.min(760, Math.round(window.innerWidth * 0.56)));
const clampWidth = (w) => Math.max(MIN_W, Math.min(maxWidth(), Math.round(w)));

function applyWidth() {
  const w = wide ? maxWidth() : clampWidth(width);
  document.documentElement.style.setProperty('--insp-w', `${w}px`);
  const handle = $('insp-resize');
  if (handle) {
    handle.setAttribute('aria-valuemin', String(MIN_W));
    handle.setAttribute('aria-valuemax', String(maxWidth()));
    handle.setAttribute('aria-valuenow', String(w));
  }
  const btn = $('insp-expand');
  if (btn) {
    btn.setAttribute('aria-pressed', String(wide));
    const label = wide ? 'Back to the normal width' : 'Make the panel wider';
    btn.setAttribute('aria-label', label);
    btn.title = wide ? 'Narrower' : 'Wider';
    btn.innerHTML = `<span data-icon="${wide ? 'minimize-2' : 'maximize-2'}">${icon(wide ? 'minimize-2' : 'maximize-2')}</span>`;
  }
}

function watchRoomy() {
  const el = panel();
  if (!el || typeof ResizeObserver === 'undefined') return;
  const ro = new ResizeObserver(([entry]) => {
    const now = entry.contentRect.width >= ROOMY_W;
    if (now === roomy) return;
    roomy = now;
    el.toggleAttribute('data-roomy', now);
    emit();
  });
  ro.observe(el);
}

function initResize() {
  const handle = $('insp-resize');
  if (!handle) return;
  let startRight = 0;

  const move = (e) => {
    wide = false;
    width = clampWidth(startRight - e.clientX);
    applyWidth();
  };
  const stop = () => {
    document.body.classList.remove('is-resizing');
    handle.removeEventListener('pointermove', move);
    handle.removeEventListener('pointerup', stop);
    handle.removeEventListener('pointercancel', stop);
    storageSet(WIDTH_KEY, String(width));
    storageSet(WIDE_KEY, '0');
  };
  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    startRight = panel().getBoundingClientRect().right;
    handle.setPointerCapture(e.pointerId);
    document.body.classList.add('is-resizing');
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
  });
  handle.addEventListener('dblclick', () => {
    wide = false;
    width = DEFAULT_W;
    applyWidth();
    storageSet(WIDTH_KEY, String(width));
    storageSet(WIDE_KEY, '0');
  });
  handle.addEventListener('keydown', (e) => {
    const step = e.shiftKey ? 64 : 24;
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const base = wide ? maxWidth() : width;
    wide = false;
    width = clampWidth(base + (e.key === 'ArrowLeft' ? step : -step));   // ← widens: the handle is on the left edge
    applyWidth();
    storageSet(WIDTH_KEY, String(width));
    storageSet(WIDE_KEY, '0');
  });
}

// ─── Swipe the sheet away (touch, floating layouts) ───────────────────────

function initSwipe() {
  const head = $('insp-head');
  const el = panel();
  if (!head || !el) return;
  let x0 = 0;
  let y0 = 0;
  let t0 = 0;
  let dragging = false;
  let tracking = false;

  head.addEventListener('pointerdown', (e) => {
    if (e.pointerType !== 'touch' || isDocked()) return;
    tracking = true;
    dragging = false;
    x0 = e.clientX;
    y0 = e.clientY;
    t0 = performance.now();
  });
  head.addEventListener('pointermove', (e) => {
    if (!tracking) return;
    const dx = e.clientX - x0;
    const dy = e.clientY - y0;
    if (!dragging) {
      if (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) { tracking = false; return; }
      if (dx < 10 || dx < Math.abs(dy) * 1.2) return;
      dragging = true;
      el.classList.add('is-dragging');
      try { head.setPointerCapture(e.pointerId); } catch { /* not a live pointer */ }
    }
    el.style.setProperty('--drag', `${Math.max(0, dx)}px`);
  });
  const end = (e) => {
    if (!tracking) return;
    tracking = false;
    if (!dragging) return;
    dragging = false;
    const dx = Math.max(0, e.clientX - x0);
    const fast = dx / Math.max(1, performance.now() - t0) > 0.5;
    el.classList.remove('is-dragging');
    if (dx > 90 || (fast && dx > 30)) {
      haptic(6);
      closeInspector();
    }
    el.style.removeProperty('--drag');
  };
  head.addEventListener('pointerup', end);
  head.addEventListener('pointercancel', end);
}

// ─── Layout changes ───────────────────────────────────────────────────────

function syncForLayout() {
  if (isDocked()) setOpen(storageGet(OPEN_KEY, 'open') !== 'closed');
  else setOpen(false);
}

export function initInspector() {
  width = Number(storageGet(WIDTH_KEY, DEFAULT_W)) || DEFAULT_W;
  wide = storageGet(WIDE_KEY, '0') === '1';
  applyWidth();

  // First paint: no slide-in for the state we restore.
  document.body.classList.add('insp-instant');
  const saved = storageGet(TAB_KEY, 'changes');
  selectTab(TABS.includes(saved) ? saved : 'changes');
  syncForLayout();
  requestAnimationFrame(() => requestAnimationFrame(() => {
    document.body.classList.remove('insp-instant');
    $('insp-tabs')?.classList.add('is-ready');
  }));

  $('insp-btn')?.addEventListener('click', toggleInspector);
  $('insp-close')?.addEventListener('click', closeInspector);
  $('insp-scrim')?.addEventListener('click', closeInspector);
  $('insp-expand')?.addEventListener('click', () => {
    wide = !wide;
    storageSet(WIDE_KEY, wide ? '1' : '0');
    applyWidth();
  });

  $('insp-tabs')?.addEventListener('click', (e) => {
    const btn = e.target.closest('.insp-tab');
    if (btn) selectTab(btn.dataset.tab);
  });
  $('insp-tabs')?.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const next = TABS[(TABS.indexOf(tab) + (e.key === 'ArrowRight' ? 1 : -1) + TABS.length) % TABS.length];
    selectTab(next, { focus: true });
  });

  // Esc closes the floating panel when focus is inside it (docked: Esc keeps
  // its usual job of stopping Jarvis).
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || e.defaultPrevented || topModal()) return;
    if (!isInspectorOpen() || isDocked() || !panel()?.contains(document.activeElement)) return;
    e.preventDefault();
    closeInspector();
  });

  dockedQuery.addEventListener?.('change', syncForLayout);
  window.addEventListener('resize', applyWidth);
  watchRoomy();
  initResize();
  initSwipe();

  // The panel's own reveal: content rises in once it is on screen.
  onInspectorChange(({ open }) => {
    if (!open || reducedMotion()) return;
    animateEl($('insp-tabs'), [{ opacity: 0, transform: 'translateY(-6px)' }, { opacity: 1, transform: 'none' }], { duration: 380, easing: SPRING });
  });
}
