/** Select text in the transcript → a "Quote" pill → it lands in the composer
 * as a `>` block, so a follow-up about one line is a new message that says
 * exactly what it refers to.
 */
import { $, debounce } from './utils.js';

let onQuote = () => {};
let pointerDown = false;
let current = '';

const pill = () => $('quote-pill');
const touch = window.matchMedia('(hover: none)');

/** The selection, if it sits inside the transcript. */
function transcriptSelection() {
  const sel = window.getSelection?.();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const text = sel.toString().trim();
  if (!text) return null;
  const range = sel.getRangeAt(0);
  const node = range.commonAncestorContainer;
  const el = node.nodeType === 1 ? node : node.parentElement;
  if (!el?.closest?.('#chat')) return null;
  return { text, range };
}

function hide() {
  current = '';
  pill()?.classList.remove('is-shown');
}

/**
 * The part of the transcript you can read. With frosted glass it runs under
 * the top bar and the dock; its scroll-padding marks them (layout.css).
 */
function readableBox() {
  const sc = $('chat-scroll');
  if (!sc) return null;
  const r = sc.getBoundingClientRect();
  const cs = getComputedStyle(sc);
  return {
    top: r.top + (parseFloat(cs.scrollPaddingTop) || 0),
    bottom: r.bottom - (parseFloat(cs.scrollPaddingBottom) || 0),
  };
}

function place() {
  const btn = pill();
  const hit = transcriptSelection();
  if (!btn || !hit) {
    hide();
    return;
  }
  current = hit.text;
  const rects = hit.range.getClientRects();
  const box = hit.range.getBoundingClientRect();
  // Scrolled out of the transcript: hide rather than pin to an edge.
  const view = readableBox();
  if ((!rects.length && !box.width) || (view && (box.bottom < view.top + 8 || box.top > view.bottom - 8))) {
    hide();
    return;
  }
  const W = btn.offsetWidth || 96;
  const H = btn.offsetHeight || 34;
  // Phones: under the selection (the system menu takes the space above).
  // Desktop: centred above it, or below when there's no room.
  let x;
  let y;
  if (touch.matches) {
    const end = rects[rects.length - 1] || box;
    x = end.left + end.width / 2 - W / 2;
    y = end.bottom + 12;
  } else {
    x = box.left + box.width / 2 - W / 2;
    y = box.top - H - 10;
    if (y < (view?.top ?? 72)) y = box.bottom + 10;
  }
  x = Math.max(12, Math.min(x, window.innerWidth - W - 12));
  y = Math.max(12, Math.min(y, window.innerHeight - H - 12));
  btn.style.left = `${Math.round(x)}px`;
  btn.style.top = `${Math.round(y)}px`;
  btn.classList.add('is-shown');
}

const placeSoon = debounce(place, 160);

export function initQuote({ onQuote: handler }) {
  onQuote = handler;
  const btn = pill();
  if (!btn) return;

  // Keep the selection alive while the pill is pressed.
  btn.addEventListener('pointerdown', (e) => e.preventDefault());
  btn.addEventListener('mousedown', (e) => e.preventDefault());
  btn.addEventListener('click', () => {
    const text = current;
    hide();
    window.getSelection?.()?.removeAllRanges();
    if (text) onQuote(text);
  });

  // A mouse drag shows the pill once, on release. Touch selection (long
  // press + handles) only reports selectionchange, so it goes through there.
  const chat = $('chat-scroll');
  chat?.addEventListener('pointerdown', (e) => {
    if (e.pointerType !== 'mouse') return;
    pointerDown = true;
    hide();
  });
  const release = () => {
    if (!pointerDown) return;
    pointerDown = false;
    setTimeout(place, 10);
  };
  document.addEventListener('pointerup', release);
  document.addEventListener('pointercancel', release);
  // Debounced, and hiding goes through place(): a tap on the pill can clear
  // the selection just before its click lands, and `current` must survive that.
  document.addEventListener('selectionchange', () => {
    if (!pointerDown) placeSoon();
  });
  chat?.addEventListener('scroll', () => {
    if (current) placeSoon();
  }, { passive: true });
  window.addEventListener('resize', hide);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && current) hide();
  });
}

/** "a\nb" → "> a\n> b" (blank lines stay inside the quote). */
export function quoteLines(text) {
  return String(text || '')
    .trim()
    .split('\n')
    .map((line) => (line.trim() ? `> ${line.trimEnd()}` : '>'))
    .join('\n');
}
