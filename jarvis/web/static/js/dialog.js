/** One shape for every list dialog — Models, Agents, Sessions, Skills,
 * Commands, MCP servers, Providers:
 *
 *   header   [← | icon]  Title · subtitle ........................ [×]
 *   tabs     (optional, MCP: Your servers | Marketplace)
 *   toolbar  [🔍 Search ……………………………………] [+ Primary action]
 *   body     sections + rows (scrolls)
 *   footer   Show [This project | Project + global]  ·  note …  ↑↓ ↵ esc
 *
 * Sub-views (an editor, a reader, a setup form) never draw their own "Back"
 * link: the module calls `setView(id, { title, sub, back })`, the header swaps
 * its icon for a back button and shows the sub-view's title, and both that
 * button and Esc call `back()` (it decides — an editor with unsaved changes
 * may ask first) — the module calls `setView(id, null)` once it's back at its
 * list. Esc at the list itself closes the dialog (modal.js asks `goBack` first).
 */
import { $, escapeHtml } from './utils.js';
import { icon } from './icons.js';

const views = new Map(); // dialog id → { back, home: { title, sub } }

function headOf(id) {
  return $(id)?.querySelector('.picker-head') || null;
}

/** Show (`view` = { title, sub?, back }) or leave (`null`) a sub-view of dialog `id`. */
export function setView(id, view) {
  const head = headOf(id);
  if (!head) return;
  const h2 = head.querySelector('h2');
  const p = head.querySelector('.picker-titles p');
  const backBtn = head.querySelector('.dlg-back');
  const had = views.get(id);
  if (!view) {
    if (!had) return;
    views.delete(id);
    if (h2) h2.textContent = had.home.title;
    if (p) p.textContent = had.home.sub;
    head.classList.remove('is-sub');
    if (backBtn) backBtn.hidden = true;
    return;
  }
  const home = had?.home || { title: h2?.textContent || '', sub: p?.textContent || '' };
  views.set(id, { back: view.back, home });
  if (h2) h2.textContent = view.title || home.title;
  if (p) p.textContent = view.sub ?? home.title;
  head.classList.add('is-sub');
  if (backBtn) {
    backBtn.hidden = false;
    backBtn.setAttribute('aria-label', `Back to ${view.backLabel || home.title}`);
    backBtn.title = `Back to ${view.backLabel || home.title} (Esc)`;
  }
}

export function hasView(id) {
  return views.has(id);
}

/** The back button / Esc: true when a sub-view handled it. */
export function goBack(id) {
  const v = views.get(id);
  if (!v) return false;
  if (typeof v.back === 'function') v.back();
  else setView(id, null);
  return true;
}

/** Keep the header's subtitle while a sub-view is open (modules update it on render). */
export function setHomeSub(id, text) {
  const v = views.get(id);
  if (v) v.home.sub = text;
  else {
    const p = headOf(id)?.querySelector('.picker-titles p');
    if (p) p.textContent = text;
  }
}

// ─── Markup helpers ───────────────────────────────────────────────────────

const attrHtml = (attrs = {}) => Object.entries(attrs).map(([k, v]) => ` ${k}="${escapeHtml(String(v))}"`).join('');

/** The search field + primary action row. `action` = { act, label, ic, title?, id?, data? } (`data` → data-* attributes);
 * `attrs` adds attributes to the input (role="combobox", aria-controls …). */
export function toolbar({ id, placeholder = 'Search', value = '', label = '', action = null, extra = '', kbd = true, attrs = {} } = {}) {
  const data = action?.data ? attrHtml(Object.fromEntries(Object.entries(action.data).map(([k, v]) => [`data-${k}`, v]))) : '';
  const act = action
    ? `<button type="button" class="btn btn-primary dlg-action"${action.id ? ` id="${action.id}"` : ''} data-act="${action.act}"${data}${action.title ? ` title="${escapeHtml(action.title)}"` : ''}>${icon(action.ic || 'plus')}<span>${escapeHtml(action.label)}</span></button>`
    : '';
  return `<div class="dlg-toolbar">
    <label class="dlg-search">
      <span class="dlg-search-ic">${icon('search')}</span>
      <input id="${id}" type="search" placeholder="${escapeHtml(placeholder)}" aria-label="${escapeHtml(label || placeholder)}" value="${escapeHtml(value)}"${attrHtml(attrs)}
        autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="go"
        data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other">
      ${kbd ? '<span class="dlg-kbd" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd><kbd>↵</kbd></span>' : ''}
    </label>${extra}${act}
  </div>`;
}

/** A group header inside the list: label · count, optional control on the right. */
export function section(label, { count = null, right = '', id = '' } = {}) {
  return `<div class="dlg-section"${id ? ` id="${id}"` : ''}><h3>${escapeHtml(label)}${count !== null && count !== '' ? `<em>${escapeHtml(String(count))}</em>` : ''}</h3>${right ? `<span class="dlg-section-right">${right}</span>` : ''}</div>`;
}

/** The footer: the scope switch on the left, a short note, key hints on the right. */
export function footer({ scope = '', scopeLabel = 'Show', note = '', noteIc = '', hints = [['↑↓', 'move'], ['↵', 'open'], ['esc', 'close']] } = {}) {
  const sc = scope ? `<div class="dlg-scope"><span class="dlg-scope-lbl">${escapeHtml(scopeLabel)}</span>${scope}</div>` : '';
  const nt = note ? `<span class="dlg-note">${noteIc ? icon(noteIc) : ''}<span>${note}</span></span>` : '';
  const hk = hints?.length
    ? `<span class="dlg-hints" aria-hidden="true">${hints.map(([k, v]) => `<span>${k.split(' ').map((x) => `<kbd>${escapeHtml(x)}</kbd>`).join('')} ${escapeHtml(v)}</span>`).join('')}</span>`
    : '';
  return `${sc}${nt}<span class="dlg-spacer"></span>${hk}`;
}

/** An empty / error state, centred in the list. */
export function empty(title, text = '', { ic = '', action = '' } = {}) {
  return `<div class="dlg-empty">${ic ? `<span class="dlg-empty-ic">${icon(ic)}</span>` : ''}<strong>${escapeHtml(title)}</strong>${text ? `<p>${text}</p>` : ''}${action ? `<div class="dlg-empty-act">${action}</div>` : ''}</div>`;
}

/** ↑ ↓ between a dialog's search box and its rows (focusable buttons): from the
 * search ↓ goes to the first row, ↑ to the last; on a row they step, and ↑ on
 * the first returns to the search. Enter on a row is the button's own click.
 * Returns true when it handled the key. */
export function arrowRows(e, search, rows) {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return false;
  const list = (rows || []).filter((r) => !r.disabled);
  if (!list.length) return false;
  const down = e.key === 'ArrowDown';
  let next;
  if (search && e.target === search) next = down ? 0 : list.length - 1;
  else {
    const i = list.indexOf(document.activeElement);
    if (i === -1) return false;
    next = i + (down ? 1 : -1);
  }
  e.preventDefault();
  if (next < 0) search?.focus();
  else {
    const row = list[Math.min(next, list.length - 1)];
    row.focus();
    row.scrollIntoView({ block: 'nearest' });
  }
  return true;
}

/** Wire the header's back button once for every dialog. */
export function initDialogs() {
  document.addEventListener('click', (e) => {
    const b = e.target.closest?.('.dlg-back');
    if (!b) return;
    const id = b.closest('.modal')?.id;
    if (id) goBack(id);
  });
}
