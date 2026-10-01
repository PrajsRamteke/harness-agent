/** Providers and login: sign in, paste API keys, switch provider — the web /provider.
 *
 * One dialog lists every provider with its status and the next step. Rows
 * open in place: an API-key form, or two-step sign-in (open the provider's
 * page on this device, paste what it shows). A key pasted at the top goes to
 * the right provider by itself. Keys are sent to Jarvis once and never come
 * back; rows only show the last four characters. Server: providers_api.py.
 *
 * Rendering: the first paint builds the rows; later paints patch each row in
 * place (class, head, panel) so the open/close fold animates and an input
 * that isn't being replaced keeps its text and caret.
 */
import { $, escapeHtml, showToast, haptic, debounce } from './utils.js';
import { icon } from './icons.js';
import { subscribe, loadSnapshot } from './store.js';
import { openModal, closeModal, isModalOpen } from './modal.js';
import { fetchProviders, fetchOAuthStatus, providerPost } from './api.js';

const TONES = {
  anthropic: 'clay',
  anthropic_api: 'clay',
  openai_codex: 'mint',
  openrouter: 'indigo',
  opencode: 'slate',
  opencode_zen: 'slate',
  harness_agent: 'accent',
};

const GROUPS = [
  { kind: 'oauth', title: 'Subscriptions', sub: 'Use a plan you already pay for' },
  // Only providers that have a key (saved here or from an env var).
  { kind: 'key', title: 'API keys', sub: 'Providers you’ve added a key for', only: (p) => p.connected },
  // Everything else: the built-ins first, then models.dev. Searchable.
  { kind: 'key', more: true, title: 'More providers', sub: 'Paste a key from the provider’s site to add one', only: (p) => !p.connected },
  { kind: 'free', title: 'Free', sub: 'Works without an account' },
];

/** Shown in "More providers" before anything is typed (when present),
 * after the built-in providers, which are always shown. */
const POPULAR = [
  'md:openai', 'md:google', 'md:groq', 'md:deepseek', 'md:mistral', 'md:xai',
  'md:togetherai', 'md:fireworks-ai', 'md:moonshotai', 'md:zai', 'md:cerebras', 'md:deepinfra',
];
const MORE_Q = 'pv-more-q';

const OAUTH_COPY = {
  anthropic: {
    page: 'Claude',
    plan: 'Claude Pro or Max',
    paste: 'Paste the code it shows',
    pasteHint: 'Once you approve, Claude shows a code like <code>abc…#xyz…</code>. Copy the whole thing.',
    placeholder: 'Paste the code',
    missing: 'Paste the code first.',
  },
  openai_codex: {
    page: 'ChatGPT',
    plan: 'ChatGPT Plus or Pro',
    paste: 'Signing in on another device?',
    pasteHint: 'The last page won’t load there. Copy its address (it starts with <code>localhost:1455</code>) and paste it here.',
    placeholder: 'Paste the page address',
    missing: 'Paste the address of the last sign-in page.',
  },
};

let data = null; // last /api/providers response
let loadError = false;
let openId = null; // expanded row
let busyId = null; // row with a request in flight
let confirmId = null; // row asking "Remove?" / "Sign out?"
let replaceId = null; // key row showing the "new key" form
const flows = {}; // card id → { flow, url, listening, status, opened, pending, finishing }
const drafts = {}; // input id → text typed so far (survives re-renders)
const revealed = new Set(); // secret inputs switched to plain text
const notes = {}; // card id → { text, error }
let quickBusy = false;
let quickNote = null; // { text, error, suggest }
let pollTimer = 0;
let openPicker = () => {};
const listeners = new Set();

const canPaste = () => !!(navigator.clipboard?.readText && window.isSecureContext);
const onThisComputer = () => /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);

/** Sidebar and others: called with the provider list whenever it changes. */
export function onProvidersChange(fn) {
  listeners.add(fn);
  if (data) fn(data);
}

function rowById(id) {
  return data?.providers?.find((p) => p.id === id) || null;
}

function matchRow(want) {
  const w = String(want || '').trim().toLowerCase();
  if (!w) return null;
  return (data?.providers || []).find((p) => p.id === w || p.id.startsWith(w) || p.label.toLowerCase().includes(w)) || null;
}

// ─── Key text (mirrors providers_api.clean_key / detect_key) ─────────────

function cleanKey(raw) {
  let s = String(raw || '').trim();
  s = s.replace(/^(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*/, '').trim();
  s = s.replace(/^['"`]+|['"`]+$/g, '').trim();
  return s.replace(/^bearer\s+/i, '').trim();
}

function detectKey(key) {
  if (key.startsWith('sk-ant-oat')) return 'anthropic';
  if (key.startsWith('sk-ant-')) return 'anthropic_api';
  if (key.startsWith('sk-or-')) return 'openrouter';
  return '';
}

function hostOf(url) {
  try {
    return new URL(url).host.replace(/^www\./, '');
  } catch {
    return url;
  }
}

// ─── Markup ───────────────────────────────────────────────────────────────

function mark(id, text, extra = '') {
  return `<span class="pv-mark${extra}" data-tone="${TONES[id] || 'slate'}" aria-hidden="true">${escapeHtml(text || '•')}</span>`;
}

function field(id, { placeholder, secret = false, label, lead = '' }) {
  const shown = secret && revealed.has(id);
  return `
    <div class="pv-field${lead ? ' has-lead' : ''}">
      ${lead ? `<span class="pv-field-ic">${icon(lead)}</span>` : ''}
      <input id="${id}" class="pv-input${secret ? ' is-secret' : ''}" type="${secret && !shown ? 'password' : 'text'}"
        value="${escapeHtml(drafts[id] || '')}" placeholder="${escapeHtml(placeholder)}" aria-label="${escapeHtml(label || placeholder)}"
        autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
        data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other" enterkeyhint="done">
      ${secret ? `<button type="button" class="pv-field-btn" data-act="reveal" data-for="${id}" aria-label="${shown ? 'Hide' : 'Show'} key" title="${shown ? 'Hide' : 'Show'}">${icon(shown ? 'eye-off' : 'eye')}</button>` : ''}
      ${canPaste() ? `<button type="button" class="pv-field-btn" data-act="paste" data-for="${id}" aria-label="Paste" title="Paste">${icon('clipboard-paste')}</button>` : ''}
    </div>`;
}

function step(n, body) {
  return `<div class="pv-step">${n === '' ? '' : `<span class="pv-n">${n}</span>`}<div class="pv-step-body">${body}</div></div>`;
}

function spin(label) {
  return `<span class="spinner" aria-hidden="true"></span>${label ? `<span>${escapeHtml(label)}</span>` : ''}`;
}

function btn(act, label, { cls = '', ic = '', use = '', busy = false, disabled = false } = {}) {
  const inner = busy ? spin(label) : `${ic ? icon(ic) : ''}<span>${escapeHtml(label)}</span>`;
  return `<button type="button" class="btn${cls ? ` ${cls}` : ''}" data-act="${act}"${use !== '' ? ` data-use="${use}"` : ''}${busy || disabled ? ' disabled' : ''}>${inner}</button>`;
}

function statusLine(row) {
  if (!row.connected || row.kind === 'free') return row.blurb;
  if (row.kind === 'oauth') return 'Signed in';
  if (row.source === 'env') return `From ${row.env_var} on your computer`;
  if (row.hint?.startsWith('$')) return `Uses ${row.hint} on your computer`;
  return row.hint ? `Key ${row.hint} saved on your computer` : 'Key saved on your computer';
}

function sideHtml(row) {
  if (busyId === row.id && openId !== row.id) return `<span class="pv-side-busy">${spin('')}</span>`;
  if (row.active) return '<span class="badge is-live">In use</span>';
  // Open: the panel below has the real buttons.
  if (openId === row.id) return '';
  if (row.connected) return '<button type="button" class="row-btn" data-act="use">Use</button>';
  return `<button type="button" class="row-btn is-go" data-act="open">${row.kind === 'oauth' ? 'Sign in' : 'Add key'}</button>`;
}

function noteHtml(id) {
  const n = notes[id];
  if (!n?.text) return '';
  return `<p class="pv-msg${n.error ? ' is-error' : ' is-ok'}" role="${n.error ? 'alert' : 'status'}">${icon(n.error ? 'circle-alert' : 'circle-check')}<span>${escapeHtml(n.text)}</span></p>`;
}

function keyForm(row, replace) {
  const id = `pv-key-${row.id}`;
  const busy = busyId === row.id;
  const hint = [
    row.key_prefix ? `Starts with <code>${escapeHtml(row.key_prefix)}</code>.` : '',
    row.local ? 'A local server: any value works if it doesn’t check keys.' : '',
    row.checked ? `Jarvis checks it with ${escapeHtml(row.label)} before saving.` : 'Saved only on your computer.',
    row.catalog && row.env_var ? `Or type <code>$${escapeHtml(row.env_var)}</code> to use that variable on your computer.` : '',
  ].filter(Boolean).join(' ');
  const needs = row.needs?.length
    ? `<p class="pv-note">${icon('terminal')}<span>Also set <code>${row.needs.map(escapeHtml).join('</code>, <code>')}</code> on the computer running Jarvis — the provider’s address needs it.</span></p>`
    : '';
  const paste = `<strong>${replace ? 'Paste the new key' : 'Paste it here'}</strong>
    ${field(id, { placeholder: row.key_prefix ? `${row.key_prefix}…` : 'Paste your key', secret: true, label: `${row.label} API key` })}
    <span class="pv-hint">${hint}</span>`;
  const getKey = row.link
    ? `<strong>Get a key</strong>
          <a class="pv-link" href="${escapeHtml(row.link)}" target="_blank" rel="noopener noreferrer"><span>${escapeHtml(hostOf(row.link))}</span>${icon('external-link')}</a>`
    : `<strong>Get a key</strong><span class="pv-hint">From your ${escapeHtml(row.label)} account.</span>`;
  const steps = replace
    ? `<div class="pv-steps is-single">${step('', paste)}</div>`
    : `${needs}<div class="pv-steps">
        ${step(1, getKey)}
        ${step(2, paste)}
      </div>`;
  let actions;
  if (replace) {
    actions = `${btn('save', 'Save key', { cls: 'btn-primary', use: row.active ? '1' : '0', busy })}${btn('cancel-replace', 'Cancel', { cls: 'btn-quiet', disabled: busy })}`;
  } else {
    actions = `${btn('save', 'Connect and use', { cls: 'btn-primary', use: '1', busy })}${btn('save', 'Just save', { cls: 'btn-quiet', use: '0', disabled: busy })}`;
  }
  return `${steps}<div class="pv-actions">${actions}</div>`;
}

function keyPanel(row) {
  const busy = busyId === row.id;
  if (!row.connected) return keyForm(row, false);
  const use = row.active ? '' : btn('use', `Use ${row.label}`, { cls: 'btn-primary', busy });
  if (row.source === 'env') {
    return `<p class="pv-note">${icon('terminal')}<span>Set by <code>${escapeHtml(row.env_var)}</code> on your computer. Change it there.</span></p>
      ${use ? `<div class="pv-actions">${use}</div>` : ''}`;
  }
  const confirming = confirmId === row.id;
  const replacing = replaceId === row.id;
  // The row's sub-line already says "Key …abcd saved on your computer".
  return `<div class="pv-actions">
      ${use}
      ${replacing ? '' : btn('replace', 'Replace key', { cls: 'btn-quiet', ic: 'key-round', disabled: busy })}
      ${btn('remove', confirming ? 'Remove now' : 'Remove', { cls: confirming ? 'btn-danger' : 'btn-quiet', ic: 'trash-2', busy: busy && confirming, disabled: busy })}
    </div>
    ${replacing ? keyForm(row, true) : ''}`;
}

function oauthPanel(row) {
  const busy = busyId === row.id;
  if (row.connected) {
    const confirming = confirmId === row.id;
    return `<div class="pv-actions">
        ${row.active ? '' : btn('use', `Use ${row.label}`, { cls: 'btn-primary', busy })}
        ${btn('signout', confirming ? 'Sign out now' : 'Sign out', { cls: confirming ? 'btn-danger' : 'btn-quiet', ic: 'log-out', busy: busy && confirming, disabled: busy })}
      </div>`;
  }
  const copy = OAUTH_COPY[row.id];
  const flow = flows[row.id];
  const opened = !!flow?.opened;
  const open = flow?.url
    ? `<a class="btn${opened ? '' : ' btn-primary'} pv-open" href="${escapeHtml(flow.url)}" target="_blank" rel="noopener noreferrer" data-act="opened">${icon('log-in')}<span>${opened ? 'Open again' : `Open ${copy.page} sign-in`}</span>${icon('external-link')}</a>`
    : `<span class="btn pv-open is-loading" aria-disabled="true">${spin('Getting a sign-in link…')}</span>`;
  const auto = row.id === 'openai_codex' && flow?.listening
    ? `<p class="pv-wait"><span class="pv-pulse" aria-hidden="true"></span><span>${onThisComputer()
      ? 'After you approve, this finishes by itself. Come back to this tab.'
      : 'Signing in on the computer running Jarvis? That finishes by itself.'}</span></p>`
    : '';
  const second = `${auto}<strong>${escapeHtml(copy.paste)}</strong><span class="pv-hint">${copy.pasteHint}</span>
    ${field(`pv-code-${row.id}`, { placeholder: copy.placeholder, label: copy.placeholder })}`;
  return `<div class="pv-steps">
      ${step(1, `<strong>Open the ${copy.page} sign-in page</strong><span class="pv-hint">Approve access with your ${escapeHtml(copy.plan)} account.</span>${open}`)}
      ${step(2, second)}
    </div>
    <div class="pv-actions">
      ${btn('finish', 'Finish sign-in', { cls: opened ? 'btn-primary' : '', busy, disabled: !flow?.flow })}
      ${btn('restart', 'New link', { cls: 'btn-quiet', ic: 'refresh-cw', disabled: busy || !flow?.flow })}
    </div>`;
}

function freePanel(row) {
  return `<p class="pv-note">${icon('zap')}<span>Free models from OpenCode Zen’s public tier. No sign-up, no key.</span></p>
    ${row.active ? '' : `<div class="pv-actions">${btn('use', 'Use Harness Agent', { cls: 'btn-primary', busy: busyId === row.id })}</div>`}`;
}

function panelHtml(row) {
  const body = row.kind === 'oauth' ? oauthPanel(row) : row.kind === 'key' ? keyPanel(row) : freePanel(row);
  return body + noteHtml(row.id);
}

function rowClass(row) {
  return `pv-row${openId === row.id ? ' is-open' : ''}${row.active ? ' is-active' : ''}${row.connected ? ' is-connected' : ''}`;
}

function headHtml(row) {
  return `${mark(row.id, row.mark, row.connected && row.kind !== 'free' ? ' is-on' : '')}
    <span class="pv-text">
      <span class="pv-title">${escapeHtml(row.label)}</span>
      <span class="pv-sub">${escapeHtml(statusLine(row))}</span>
    </span>
    <span class="pv-chev">${icon('chevron-down')}</span>`;
}

function rowHtml(row, i) {
  return `
    <div class="${rowClass(row)}" data-id="${row.id}" style="--i:${i}">
      <button type="button" class="pv-head" data-act="toggle" aria-expanded="${openId === row.id}" aria-controls="pv-panel-${row.id}">${headHtml(row)}</button>
      <div class="pv-side">${sideHtml(row)}</div>
      <div class="fold"><div class="fold-inner"><div class="pv-panel" id="pv-panel-${row.id}">${panelHtml(row)}</div></div></div>
    </div>`;
}

function nowHtml() {
  const row = rowById(data.active);
  return `${mark(data.active, row?.mark)}
    <span class="pv-now-text">
      <span class="pv-eyebrow">Now using</span>
      <strong>${escapeHtml(data.active_label || 'Unknown')}</strong>
      <span class="pv-now-model" title="${escapeHtml(data.model || '')}">${escapeHtml(data.model || '')}</span>
    </span>
    <button type="button" class="btn pv-now-btn" data-act="models">${icon('cpu')}<span>Change model</span></button>`;
}

function quickOutHtml() {
  const key = cleanKey(drafts['pv-quick']);
  if (quickNote?.text) {
    return `<span class="pv-quick-msg${quickNote.error ? ' is-error' : ' is-ok'}">${icon(quickNote.error ? 'circle-alert' : 'circle-check')}<span>${escapeHtml(quickNote.text)}</span></span>
      ${quickNote.suggest ? quickChoices([quickNote.suggest]) : ''}`;
  }
  if (!key) return '<span class="pv-hint">Jarvis works out which provider it belongs to.</span>';
  const found = detectKey(key);
  if (found === 'anthropic') {
    return `<span class="pv-hint">That’s a Claude sign-in token, not an API key.</span>
      <button type="button" class="btn btn-primary btn-sm" data-act="goto" data-id="anthropic"><span>Sign in with Claude</span>${icon('arrow-right')}</button>`;
  }
  if (found) {
    return `<span class="pv-quick-found">Looks like an <strong>${escapeHtml(rowById(found)?.label || found)}</strong> key</span>
      <button type="button" class="btn btn-primary btn-sm" data-act="quick-save" data-id="${found}"${quickBusy ? ' disabled' : ''}>${quickBusy ? spin('Checking') : `<span>Connect</span>${icon('arrow-right')}`}</button>`;
  }
  if (key.length < 16) return '<span class="pv-hint">That’s too short for a key. Copy the whole thing.</span>';
  // Built-in providers only — "More providers" below has 200 to search.
  const ids = (data?.providers || []).filter((p) => p.kind === 'key' && !p.catalog && !p.key_prefix && p.source !== 'env').map((p) => p.id);
  return `<span class="pv-hint">Which provider is it for?</span>${quickChoices(ids)}`;
}

function quickChoices(ids) {
  return `<span class="pv-chips">${ids.map((id) => {
    const row = rowById(id);
    if (!row) return '';
    return `<button type="button" class="pv-chip" data-act="quick-save" data-id="${id}"${quickBusy ? ' disabled' : ''}>${mark(id, row.mark, ' is-sm')}<span>${escapeHtml(row.label)}</span></button>`;
  }).join('')}</span>`;
}

// ─── Render ───────────────────────────────────────────────────────────────

/** Changes when a row is added, removed or moves group (adding a key moves
 * a provider up into "API keys", removing it moves it back): then rebuild. */
function sigOf(rows) {
  return rows.map((p) => (p.kind === 'key' && p.connected ? `${p.id}*` : p.id)).join(',');
}

function groupRows(g) {
  return data.providers.filter((p) => p.kind === g.kind && (!g.only || g.only(p)));
}

function build(body) {
  let i = 0;
  const groups = GROUPS.map((g) => {
    const rows = groupRows(g);
    if (!rows.length) return '';
    // Only the rows the filter shows get staggered; 200 hidden ones would
    // push the visible ones' entrance seconds back.
    const html = rows.map((r) => rowHtml(r, g.more ? 0 : i++)).join('');
    if (g.more) {
      return `<section class="pv-group is-more" aria-label="${escapeHtml(g.title)}">
        <div class="pv-group-head"><h3>${escapeHtml(g.title)}</h3><span>${escapeHtml(g.sub)}</span></div>
        <div class="pv-more-search">
          ${field(MORE_Q, { placeholder: `Search ${rows.length} providers`, label: 'Search providers', lead: 'search' })}
        </div>
        ${html}
        <p class="pv-more-foot" id="pv-more-foot"></p>
      </section>`;
    }
    return `<section class="pv-group" aria-label="${escapeHtml(g.title)}">
      <div class="pv-group-head"><h3>${escapeHtml(g.title)}</h3><span>${escapeHtml(g.sub)}</span></div>
      ${html}
    </section>`;
  }).join('');
  body.innerHTML = `
    <div class="pv-now" id="pv-now">${nowHtml()}</div>
    <div class="pv-quick">
      ${field('pv-quick', { placeholder: 'Paste any API key', secret: true, label: 'Paste any API key', lead: 'key-round' })}
      <div class="pv-quick-out" id="pv-quick-out" aria-live="polite">${quickOutHtml()}</div>
    </div>
    <div class="pv-groups is-entering" data-sig="${escapeHtml(sigOf(data.providers))}">${groups}</div>
    <p class="pv-foot">${icon('lock')}<span>Keys and sign-ins stay on the computer running Jarvis (<code>~/.config/harness-agent</code>). This browser never keeps them. Provider list from <a href="https://models.dev" target="_blank" rel="noopener noreferrer">models.dev</a>.</span></p>`;
  setTimeout(() => body.querySelector('.pv-groups')?.classList.remove('is-entering'), 700);
}

/** Show the "More providers" rows the search matches (built-ins and popular
 * ones when it's empty). Toggles `hidden` in place — no rebuild, so typing
 * stays smooth. */
function filterMore() {
  const group = $('providers-body')?.querySelector('.pv-group.is-more');
  if (!group) return;
  const words = String(drafts[MORE_Q] || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
  const rows = data.providers.filter((p) => p.kind === 'key' && !p.connected);
  const popular = new Set(POPULAR.filter((id) => rows.some((r) => r.id === id)));
  let shown = 0;
  let total = 0;
  for (const row of rows) {
    const el = group.querySelector(`.pv-row[data-id="${CSS.escape(row.id)}"]`);
    if (!el) continue;
    total += 1;
    const hay = `${row.label} ${row.catalog ? row.id.slice(3) : row.id} ${row.env_var || ''}`.toLowerCase();
    const hit = words.length
      ? words.every((w) => hay.includes(w))
      : !row.catalog || popular.has(row.id) || (!popular.size && shown < 12);
    const show = hit || row.id === openId;
    el.hidden = !show;
    if (show) shown += 1;
  }
  const foot = $('pv-more-foot');
  if (foot) {
    foot.textContent = words.length
      ? (shown ? `${shown} of ${total} providers` : `No provider matches “${drafts[MORE_Q].trim()}”`)
      : `Type to search all ${total} providers`;
  }
}

/** Swap `el`'s markup only when it changed (typing isn't reset for nothing). */
function patch(el, html) {
  if (el && el._html !== html) {
    el.innerHTML = html;
    el._html = html;
  }
}

function render() {
  const body = $('providers-body');
  if (!body) return;
  if (!data) {
    body.innerHTML = loadError
      ? `<div class="list-empty"><strong>Could not load providers</strong>Check that Jarvis is still running, then try again.<div class="pv-actions is-center">${btn('reload', 'Try again', { ic: 'refresh-cw' })}</div></div>`
      : `<div class="list-loading">${'<div class="skeleton"></div>'.repeat(6)}</div>`;
    return;
  }
  const sub = $('providers-sub');
  if (sub) {
    sub.textContent = data.connected
      ? `${data.connected} connected · saved on your computer`
      : 'Sign in or paste an API key. Saved on your computer.';
  }

  // Keep the caret where it was when an input has to be rebuilt.
  const focused = document.activeElement;
  const focusId = focused?.classList?.contains('pv-input') && body.contains(focused) ? focused.id : '';
  const sel = focusId ? [focused.selectionStart, focused.selectionEnd] : null;

  const groups = body.querySelector('.pv-groups');
  if (!groups || groups.dataset.sig !== sigOf(data.providers)) {
    build(body);
  } else {
    patch($('pv-now'), nowHtml());
    patch($('pv-quick-out'), quickOutHtml());
    for (const row of data.providers) {
      const el = groups.querySelector(`.pv-row[data-id="${row.id}"]`);
      if (!el) continue;
      const becameActive = row.active && !el.classList.contains('is-active');
      el.className = rowClass(row);
      if (becameActive) {
        el.classList.add('just-active');
        setTimeout(() => el.classList.remove('just-active'), 600);
      }
      const head = el.querySelector('.pv-head');
      head.setAttribute('aria-expanded', String(openId === row.id));
      patch(head, headHtml(row));
      patch(el.querySelector('.pv-side'), sideHtml(row));
      patch(el.querySelector('.pv-panel'), panelHtml(row));
    }
  }

  filterMore();

  if (focusId && document.activeElement !== $(focusId)) {
    const el = $(focusId);
    if (el) {
      el.focus({ preventScroll: true });
      try { el.setSelectionRange(sel[0], sel[1]); } catch { /* not a text input */ }
    }
  }
}

function paintQuick() {
  patch($('pv-quick-out'), quickOutHtml());
}

function setNote(id, text, error = false) {
  notes[id] = text ? { text, error } : null;
}

function setData(next) {
  data = next;
  loadError = false;
  for (const fn of listeners) fn(data);
  if (isModalOpen('providers')) render();
}

function applyResult(res) {
  if (res?.state) loadSnapshot(res.state);
  if (res?.providers) setData(res.providers);
}

export async function refreshProviders() {
  try {
    const next = await fetchProviders();
    const prev = data;
    setData(next);
    // A ChatGPT sign-in can finish on its own (localhost callback): say so.
    for (const [id, f] of Object.entries(flows)) {
      if (f.finishing) continue;
      const was = prev?.providers?.find((p) => p.id === id);
      const now = next.providers?.find((p) => p.id === id);
      if (now?.connected && !was?.connected) {
        delete flows[id];
        if (openId === id) openId = null;
        showToast(`Signed in to ${now.label}`);
        render();
      }
    }
    syncPolling();
  } catch {
    if (!data) {
      loadError = true;
      if (isModalOpen('providers')) render();
    }
  }
}

// ─── Actions ──────────────────────────────────────────────────────────────

async function run(id, path, payload, { onOk } = {}) {
  busyId = id;
  setNote(id, '');
  render();
  const res = await providerPost(path, payload);
  busyId = null;
  if (res.ok) {
    confirmId = null;
    onOk?.(res);
    haptic(10);
    if (res.message) showToast(res.message);
  } else {
    setNote(id, res.error || 'That did not work', true);
  }
  applyResult(res);
  render();
  if (!res.ok) {
    const msg = $('providers-body')?.querySelector(`.pv-row[data-id="${id}"] .pv-msg`);
    msg?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  return res;
}

function closeRow(id) {
  if (openId === id) openId = null;
}

function useProvider(id) {
  return run(id, 'use', { id }, { onOk: () => closeRow(id) });
}

async function saveKey(id, use) {
  const inputId = `pv-key-${id}`;
  if (!cleanKey(drafts[inputId])) {
    setNote(id, 'Paste a key first.', true);
    render();
    $(inputId)?.focus();
    return;
  }
  const res = await run(id, 'key', { id, key: drafts[inputId], use }, {
    onOk: () => {
      delete drafts[inputId];
      revealed.delete(inputId);
      replaceId = null;
      closeRow(id);
    },
  });
  const other = !res.ok && res.suggest ? rowById(res.suggest) : null;
  if (other) {
    setNote(id, `${res.error} Use the ${other.label} row instead.`, true);
    render();
  }
}

async function quickSave(id) {
  const raw = drafts['pv-quick'] || '';
  if (!cleanKey(raw) || quickBusy) return;
  quickBusy = true;
  quickNote = null;
  paintQuick();
  const res = await providerPost('key', { id, key: raw, use: true });
  quickBusy = false;
  if (res.ok) {
    delete drafts['pv-quick'];
    const input = $('pv-quick');
    if (input) input.value = '';
    quickNote = { text: res.message || 'Connected' };
    haptic(10);
    showToast(res.message || 'Connected');
    setTimeout(() => {
      if (quickNote && !quickNote.error) {
        quickNote = null;
        paintQuick();
      }
    }, 5000);
  } else {
    quickNote = { text: res.error || 'That did not work', error: true, suggest: res.suggest };
  }
  applyResult(res);
  paintQuick();
}

async function startFlow(id, { force = false } = {}) {
  const existing = flows[id];
  if (existing && !force && (existing.pending || existing.flow)) return;
  flows[id] = { pending: true };
  setNote(id, '');
  render();
  const res = await providerPost('oauth/start', { id });
  if (res.ok) {
    flows[id] = { flow: res.flow, url: res.url, listening: !!res.listening, status: res.status, opened: false };
  } else {
    delete flows[id];
    setNote(id, res.error || 'Could not start sign-in', true);
  }
  render();
  syncPolling();
}

async function finishFlow(id) {
  const flow = flows[id];
  const inputId = `pv-code-${id}`;
  const code = (drafts[inputId] || '').trim();
  if (!flow?.flow || busyId === id) return;
  if (!code) {
    setNote(id, OAUTH_COPY[id]?.missing || 'Paste the code first.', true);
    render();
    $(inputId)?.focus();
    return;
  }
  flow.finishing = true;
  const res = await run(id, 'oauth/finish', { flow: flow.flow, code }, {
    onOk: () => {
      delete flows[id];
      delete drafts[inputId];
      closeRow(id);
    },
  });
  if (!res.ok) {
    flow.finishing = false;
    if (res.expired) startFlow(id, { force: true });
  }
}

function toggleRow(id) {
  confirmId = null;
  const opening = openId !== id;
  openId = opening ? id : null;
  if (!opening && replaceId === id) replaceId = null;
  const row = rowById(id);
  if (opening && row?.kind === 'oauth' && !row.connected) startFlow(id);
  render();
  if (opening) focusRow(id);
}

function focusRow(id) {
  // After the fold has started opening, so the row scrolls to its full height.
  setTimeout(() => {
    const row = $('providers-body')?.querySelector(`.pv-row[data-id="${id}"]`);
    if (!row) return;
    const input = row.querySelector('.pv-input');
    // Phones: no keyboard jumping up until the user taps the field.
    if (input && window.matchMedia('(min-width: 561px)').matches) input.focus({ preventScroll: true });
    row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, 120);
}

async function pasteInto(inputId) {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (!text) return;
    drafts[inputId] = text;
    const el = $(inputId);
    if (el) el.value = text;
    afterInput(inputId, true);
  } catch {
    showToast('Paste with your keyboard instead', true);
  }
}

function afterInput(inputId, pasted) {
  if (inputId === 'pv-quick') {
    quickNote = null;
    paintQuick();
    return;
  }
  // The code field only ever holds one thing: finish as soon as it lands.
  const code = /^pv-code-(.+)$/.exec(inputId);
  if (code && pasted && drafts[inputId]?.trim()) finishFlow(code[1]);
}

function handleClick(e) {
  const el = e.target.closest('[data-act]');
  const body = $('providers-body');
  if (!el || !body.contains(el)) return;
  const act = el.dataset.act;
  const id = el.dataset.id || el.closest('.pv-row')?.dataset.id;
  switch (act) {
    case 'toggle':
      toggleRow(id);
      break;
    case 'open':
      if (openId !== id) toggleRow(id);
      else focusRow(id);
      break;
    case 'use':
      useProvider(id);
      break;
    case 'save':
      saveKey(id, el.dataset.use === '1');
      break;
    case 'replace':
      replaceId = id;
      confirmId = null;
      render();
      focusRow(id);
      break;
    case 'cancel-replace':
      replaceId = null;
      render();
      break;
    case 'remove':
    case 'signout':
      // Second press confirms (same as deleting a session).
      if (confirmId !== id) {
        confirmId = id;
        render();
        return;
      }
      run(id, act === 'remove' ? 'key/remove' : 'signout', { id });
      break;
    case 'opened':
      // A real link (target=_blank) so phones don't block it as a pop-up;
      // re-render after the navigation has started.
      if (flows[id]) {
        flows[id].opened = true;
        setTimeout(() => {
          render();
          syncPolling();
        }, 0);
      }
      break;
    case 'finish':
      finishFlow(id);
      break;
    case 'restart':
      startFlow(id, { force: true });
      break;
    case 'reveal': {
      const inputId = el.dataset.for;
      if (revealed.has(inputId)) revealed.delete(inputId);
      else revealed.add(inputId);
      const input = $(inputId);
      if (input) input.type = revealed.has(inputId) ? 'text' : 'password';
      el.innerHTML = icon(revealed.has(inputId) ? 'eye-off' : 'eye');
      el.setAttribute('aria-label', revealed.has(inputId) ? 'Hide key' : 'Show key');
      input?.focus({ preventScroll: true });
      break;
    }
    case 'paste':
      pasteInto(el.dataset.for);
      break;
    case 'quick-save':
      quickSave(id);
      break;
    case 'goto':
      delete drafts['pv-quick'];
      if ($('pv-quick')) $('pv-quick').value = '';
      quickNote = null;
      if (openId !== id) toggleRow(id);
      else render();
      break;
    case 'models':
      closeProviders();
      openPicker('model');
      break;
    case 'reload':
      refreshProviders();
      break;
    default:
  }
}

function handleInput(e) {
  const el = e.target;
  if (!el.classList?.contains('pv-input')) return;
  drafts[el.id] = el.value;
  if (el.id === MORE_Q) {
    filterMore();
    return;
  }
  afterInput(el.id, e.inputType === 'insertFromPaste');
}

function handleKey(e) {
  const el = e.target;
  if (e.key !== 'Enter' || e.isComposing || !el.classList?.contains('pv-input')) return;
  e.preventDefault();
  if (el.id === MORE_Q) {
    // Enter opens the first provider the search shows.
    const first = $('providers-body')?.querySelector('.pv-group.is-more .pv-row:not([hidden])');
    if (first && openId !== first.dataset.id) toggleRow(first.dataset.id);
    return;
  }
  if (el.id === 'pv-quick') {
    const found = detectKey(cleanKey(el.value));
    if (found && found !== 'anthropic') quickSave(found);
    return;
  }
  const m = /^pv-(key|code)-(.+)$/.exec(el.id);
  if (!m) return;
  if (m[1] === 'code') {
    finishFlow(m[2]);
    return;
  }
  // Enter does what the primary button says.
  const row = rowById(m[2]);
  saveKey(m[2], !row?.connected || !!row.active);
}

// ─── ChatGPT sign-in: watch for the localhost callback ────────────────────

function syncPolling() {
  const waiting = Object.values(flows).some((f) => f.flow && f.listening && f.opened && !f.finishing);
  if (waiting && !pollTimer) pollTimer = setInterval(pollFlows, 2000);
  if (!waiting && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = 0;
  }
}

async function pollFlows() {
  for (const [id, f] of Object.entries(flows)) {
    if (!f.flow || !f.listening || !f.opened || f.finishing) continue;
    let st;
    try {
      st = await fetchOAuthStatus(f.flow);
    } catch {
      continue;
    }
    if (flows[id] !== f) continue; // replaced meanwhile
    if (st.status === 'done') {
      delete flows[id];
      closeRow(id);
      showToast(st.message || 'Signed in');
      await refreshProviders();
    } else if (st.status === 'error' || st.status === 'expired') {
      f.listening = false;
      setNote(id, st.message || st.error || 'Sign-in failed. Get a new link.', true);
      if (st.status === 'expired') delete flows[id];
      render();
    }
  }
  syncPolling();
}

// ─── Open / close ─────────────────────────────────────────────────────────

function openWanted(want) {
  const row = matchRow(want);
  if (!row) return;
  if (openId !== row.id) toggleRow(row.id);
}

/** `focus`: a card id or a name ("openrouter", "claude") to open that row. */
export function openProviders(focus = '') {
  render();
  openModal('providers', {
    onClose: () => {
      confirmId = null;
      replaceId = null;
      // Reopen fresh, except in the middle of a sign-in.
      if (!flows[openId]?.opened) openId = null;
      for (const id of Object.keys(notes)) delete notes[id];
      quickNote = null;
      // A sign-in link never opened: let go of it (and the port it holds).
      for (const [id, f] of Object.entries(flows)) {
        if (f.flow && !f.opened) {
          providerPost('oauth/cancel', { flow: f.flow });
          delete flows[id];
        }
      }
      syncPolling();
    },
  });
  if (data && focus) openWanted(focus);
  refreshProviders().then(() => {
    if (!isModalOpen('providers')) return;
    if (focus && !openId) openWanted(focus);
    const row = rowById(openId);
    if (row?.kind === 'oauth' && !row.connected) startFlow(openId);
  });
}

export function closeProviders() {
  closeModal('providers');
}

export function initProviders({ onOpenPicker } = {}) {
  if (onOpenPicker) openPicker = onOpenPicker;
  const body = $('providers-body');
  body?.addEventListener('click', handleClick);
  body?.addEventListener('input', handleInput);
  body?.addEventListener('keydown', handleKey);

  // Back from the sign-in tab: check right away instead of on the next tick.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && pollTimer) pollFlows();
  });

  // The terminal (or another tab) switched provider or model: stay current.
  let sig = '';
  const refreshSoon = debounce(refreshProviders, 400);
  subscribe((s) => {
    const next = `${s.session.provider}|${s.session.model}`;
    if (next === sig) return;
    const first = !sig;
    sig = next;
    if (!first) refreshSoon();
  });
  refreshProviders();
}
