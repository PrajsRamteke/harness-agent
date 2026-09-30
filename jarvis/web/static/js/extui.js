/** Small building blocks shared by the MCP and Skills dialogs (mcp.js, skills.js):
 * buttons, fields, the Project / Global control, monogram tiles and the ⋯ menu. */
import { escapeHtml } from './utils.js';
import { icon } from './icons.js';

export const canPaste = () => !!(navigator.clipboard?.readText && window.isSecureContext);

export const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

export function hostOf(url) {
  try {
    return new URL(url).host.replace(/^www\./, '');
  } catch {
    return String(url || '');
  }
}

export function spin(label) {
  return `<span class="spinner" aria-hidden="true"></span>${label ? `<span>${escapeHtml(label)}</span>` : ''}`;
}

/** A `.btn` wired by `data-act` (plus any extra `data-*` in `data`). */
export function btn(act, label, { cls = '', ic = '', busy = false, disabled = false, data = {}, title = '' } = {}) {
  const extra = Object.entries(data).map(([k, v]) => ` data-${k}="${escapeHtml(v)}"`).join('');
  const inner = busy ? spin(label) : `${ic ? icon(ic) : ''}<span>${escapeHtml(label)}</span>`;
  return `<button type="button" class="btn${cls ? ` ${cls}` : ''}" data-act="${act}"${extra}${title ? ` title="${escapeHtml(title)}"` : ''}${busy || disabled ? ' disabled' : ''}>${inner}</button>`;
}

/** Same as `btn` but the compact pill used in card headers. */
export function rowBtn(act, label, { cls = '', busy = false, disabled = false, data = {}, title = '', ic = '' } = {}) {
  const extra = Object.entries(data).map(([k, v]) => ` data-${k}="${escapeHtml(v)}"`).join('');
  const inner = busy ? spin('') : `${ic ? icon(ic) : ''}<span>${escapeHtml(label)}</span>`;
  return `<button type="button" class="row-btn${cls ? ` ${cls}` : ''}" data-act="${act}"${extra}${title ? ` title="${escapeHtml(title)}"` : ''}${busy || disabled ? ' disabled' : ''}>${inner}</button>`;
}

/** The ⋯ button. */
export function moreBtn(name, what) {
  return `<button type="button" class="row-btn is-icon" data-act="menu" data-name="${escapeHtml(name)}" aria-haspopup="menu" aria-label="More actions for ${escapeHtml(what || name)}" title="More">${icon('ellipsis')}</button>`;
}

/** Project / Global (or any two-way) control: buttons carry `data-val`. */
export function seg(options, value, { label = '', act = 'seg', group = '' } = {}) {
  return `<div class="seg" role="group"${label ? ` aria-label="${escapeHtml(label)}"` : ''}>${options.map((o) => `
    <button type="button" data-act="${act}"${group ? ` data-group="${group}"` : ''} data-val="${escapeHtml(o.value)}" aria-pressed="${String(o.value) === String(value)}"${o.title ? ` title="${escapeHtml(o.title)}"` : ''}>${escapeHtml(o.label)}</button>`).join('')}</div>`;
}

/** A tinted monogram tile (`pv-mark`); `on` adds the green "live" dot. */
export function mark(text, tone = 'slate', { on = false, sm = false } = {}) {
  const ch = String(text || '•').trim().charAt(0).toUpperCase() || '•';
  return `<span class="pv-mark${on ? ' is-on' : ''}${sm ? ' is-sm' : ''}" data-tone="${tone}" aria-hidden="true">${escapeHtml(ch)}</span>`;
}

/** Swap `el`'s markup only when it changed (typing and folds aren't reset for nothing). */
export function patch(el, html) {
  if (el && el._html !== html) {
    el.innerHTML = html;
    el._html = html;
  }
}

/** Text or password field with the inline show / paste buttons (`pv-field`).
 * `value` is the draft typed so far, so a re-render doesn't lose it. */
export function field(id, { value = '', placeholder = '', label = '', secret = false, shown = false, lead = '', mono = true } = {}) {
  const reveal = secret
    ? `<button type="button" class="pv-field-btn" data-act="reveal" data-for="${id}" aria-label="${shown ? 'Hide' : 'Show'} value" title="${shown ? 'Hide' : 'Show'}">${icon(shown ? 'eye-off' : 'eye')}</button>`
    : '';
  const paste = canPaste()
    ? `<button type="button" class="pv-field-btn" data-act="paste" data-for="${id}" aria-label="Paste" title="Paste">${icon('clipboard-paste')}</button>`
    : '';
  return `
    <div class="pv-field${lead ? ' has-lead' : ''}">
      ${lead ? `<span class="pv-field-ic">${icon(lead)}</span>` : ''}
      <input id="${id}" class="pv-input ex-in${secret ? ' is-secret' : ''}${mono ? '' : ' is-plain'}" type="${secret && !shown ? 'password' : 'text'}"
        value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" aria-label="${escapeHtml(label || placeholder)}"
        autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
        data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other" enterkeyhint="done">
      ${reveal}${paste}
    </div>`;
}

/** Grow a textarea with its content (one to six lines). */
export function autosize(ta) {
  if (!ta) return;
  ta.style.height = 'auto';
  // Hidden (dialog not open yet) → scrollHeight is 0: leave it to `rows`.
  if (ta.scrollHeight) ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`;
}

/** `/Users/me/.config/x` → `~/.config/x` (the server sends absolute paths). */
export function tildify(path) {
  return String(path || '').replace(/^\/(?:Users|home)\/[^/]+/, '~');
}

export function msg(text, kind = 'error') {
  const ic = kind === 'error' ? 'circle-alert' : kind === 'warn' ? 'circle-alert' : 'circle-check';
  return `<p class="pv-msg is-${kind}" role="${kind === 'error' ? 'alert' : 'status'}">${icon(ic)}<span>${escapeHtml(text)}</span></p>`;
}

// ─── ⋯ menu ───────────────────────────────────────────────────────────────

let menu = null;

export function closeMenu() {
  if (!menu) return;
  const { el, off, anchor } = menu;
  menu = null;
  off();
  el.remove();
  if (anchor?.isConnected) anchor.focus({ preventScroll: true });
}

/**
 * A small menu next to `anchor`. `items`: `{ key, label, icon?, danger?, confirm?, disabled?, reason? }`
 * (`confirm`: the first click asks "Click again to …", the second acts).
 * Resolves with the picked key, or `null` when it was dismissed.
 */
export function openMenu(anchor, items) {
  closeMenu();
  return new Promise((resolve) => {
    const el = document.createElement('div');
    el.className = 'ex-menu glass-strong';
    el.setAttribute('role', 'menu');
    const armed = new Set();
    const paint = () => {
      el.innerHTML = items.map((it) => {
        if (it.divider) return '<div class="ex-menu-sep" role="separator"></div>';
        const asking = armed.has(it.key);
        return `<button type="button" role="menuitem" class="ex-menu-item${it.danger ? ' is-danger' : ''}${asking ? ' is-asking' : ''}" data-key="${escapeHtml(it.key)}"${it.disabled ? ' disabled' : ''}>
          ${it.icon ? icon(it.icon) : '<span class="ex-menu-gap"></span>'}
          <span class="ex-menu-text"><span>${escapeHtml(asking ? `Click again to ${it.label.toLowerCase()}` : it.label)}</span>${it.disabled && it.reason ? `<small>${escapeHtml(it.reason)}</small>` : ''}</span>
        </button>`;
      }).join('');
    };
    paint();
    document.body.appendChild(el);

    const place = () => {
      const r = anchor.getBoundingClientRect();
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      const left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w));
      const below = r.bottom + 6;
      const top = below + h > window.innerHeight - 8 ? Math.max(8, r.top - h - 6) : below;
      el.style.left = `${left}px`;
      el.style.top = `${top}px`;
    };
    place();

    const finish = (key) => {
      if (!menu || menu.el !== el) return;
      resolve(key); // first resolve wins: before closeMenu() resolves null
      closeMenu();
    };
    const onItem = (e) => {
      const item = e.target.closest?.('.ex-menu-item');
      if (!item || !el.contains(item)) return;
      const it = items.find((x) => x.key === item.dataset.key);
      if (!it || it.disabled) return;
      if (it.confirm && !armed.has(it.key)) {
        armed.add(it.key);
        paint();
        el.querySelector(`[data-key="${CSS.escape(it.key)}"]`)?.focus();
        return;
      }
      finish(it.key);
    };
    const onOutside = (e) => {
      if (!el.contains(e.target) && !anchor.contains(e.target)) finish(null);
    };
    const onKey = (e) => {
      const list = [...el.querySelectorAll('.ex-menu-item:not(:disabled)')];
      const i = list.indexOf(document.activeElement);
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation(); // the dialog stays open
        finish(null);
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        e.stopPropagation();
        const next = list[(i + (e.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length];
        next?.focus();
      } else if (e.key === 'Tab') {
        finish(null);
      }
    };
    const openedAt = performance.now();
    const onScroll = (e) => {
      if (e?.target && el.contains(e.target)) return;
      // The list may still be settling from a smooth scroll that started before the click.
      if (e?.type === 'scroll' && performance.now() - openedAt < 450) return;
      finish(null);
    };
    const off = () => {
      document.removeEventListener('mousedown', onOutside, true);
      el.removeEventListener('click', onItem);
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('resize', onScroll);
      document.removeEventListener('scroll', onScroll, true);
      resolve(null); // closed from outside (closeMenu): dismissed
    };
    menu = { el, off, anchor };
    document.addEventListener('mousedown', onOutside, true);
    el.addEventListener('click', onItem);
    // `window` capture runs before the dialog's own Esc handler on `document`.
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('resize', onScroll);
    document.addEventListener('scroll', onScroll, true);
    el.querySelector('.ex-menu-item:not(:disabled)')?.focus({ preventScroll: true });
  });
}
