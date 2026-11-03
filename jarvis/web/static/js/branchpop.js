/**
 * A small custom branch dropdown (no native <select> / <datalist>): a list of
 * branches under its field, ↑ ↓ to move, Enter to pick, Esc to close.
 * Used by the git card's PR form and the Files drawer's Create pull request.
 * Markup: `.gc-pop` inside a positioned field, next to the control that
 * carries `aria-expanded` (styles live in git.css).
 */
import { escapeHtml } from './utils.js';
import { icon } from './icons.js';

export function popOpen(pop) {
  return pop && !pop.hidden;
}

export function closePop(pop) {
  if (!pop || pop.hidden) return;
  pop.hidden = true;
  pop.parentElement.querySelector('[aria-expanded]')?.setAttribute('aria-expanded', 'false');
}

export function showPop(pop, items, { current = '', onPick, filter = false, empty = 'No branches match', preselect = true } = {}) {
  pop._items = items;
  pop._onPick = onPick;
  // A typed field (From) starts with nothing highlighted: Enter keeps what you typed.
  pop._i = preselect ? Math.max(0, items.indexOf(current)) : items.indexOf(current);
  const list = items.length
    ? items.map((it, i) => `
      <button type="button" class="gc-opt${it === current ? ' is-current' : ''}" role="option" data-i="${i}" aria-selected="${it === current}" tabindex="-1" title="${escapeHtml(it)}">
        ${icon('git-branch')}<span>${escapeHtml(it)}</span>${it === current ? icon('check', 'gc-check') : ''}
      </button>`).join('')
    : `<p class="gc-empty">${escapeHtml(empty)}</p>`;
  const search = filter ? pop.querySelector('.gc-pop-search') : null;
  if (filter && !search) {
    pop.innerHTML = `<label class="gc-pop-search">${icon('search')}<input type="text" placeholder="Filter branches" autocomplete="off" spellcheck="false" aria-label="Filter branches"></label><div class="gc-pop-list"></div>`;
  } else if (!filter) {
    pop.innerHTML = '<div class="gc-pop-list"></div>';
  }
  pop.querySelector('.gc-pop-list').innerHTML = list;
  pop.hidden = false;
  pop.parentElement.querySelector('[aria-expanded]')?.setAttribute('aria-expanded', 'true');
  markPop(pop);
}

function markPop(pop) {
  pop.querySelectorAll('.gc-opt').forEach((b) => b.classList.toggle('is-hi', Number(b.dataset.i) === pop._i));
  pop.querySelector('.gc-opt.is-hi')?.scrollIntoView({ block: 'nearest' });
}

/** Arrow keys / Enter / Esc for an open dropdown. True when the key was used. */
export function popKey(pop, e) {
  if (!popOpen(pop)) return false;
  const n = pop._items?.length || 0;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (n) pop._i = pop._i < 0 ? (e.key === 'ArrowDown' ? 0 : n - 1) : (pop._i + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
    markPop(pop);
    return true;
  }
  if (e.key === 'Enter' && n && pop._i >= 0) {
    e.preventDefault();
    pop._onPick?.(pop._items[pop._i]);
    return true;
  }
  if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation(); // closes the list, not the form
    closePop(pop);
    return true;
  }
  return false;
}

export function wirePop(pop) {
  // pointerdown: pick before the field loses focus.
  pop.addEventListener('pointerdown', (e) => {
    const opt = e.target.closest('.gc-opt');
    if (!opt) return;
    e.preventDefault();
    pop._onPick?.(pop._items[Number(opt.dataset.i)]);
  });
}
