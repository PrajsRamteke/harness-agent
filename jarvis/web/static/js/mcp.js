/** MCP servers — the web /mcp: a searchable marketplace, sign in, connect, remove.
 *
 * Built on the shared dialog kit (dialog.js): tabs, a toolbar (search + the
 * tab's primary action), one scrolling body, a footer (scope switch · hints).
 * Two tabs — *Your servers* first (shown when you have any), then the
 * *Marketplace* (shown when you don't). **Marketplace**: a search field over every well-known server
 * (Slack, Notion, Linear, GitHub…) with category chips and one row per server —
 * brand tile, what it does, how it signs in (Sign in · No account · API key ·
 * Your app · Desktop app · Runs locally) and one button. Hosted ones connect
 * and open their sign-in page straight from that click; key / app / desktop
 * ones first open a short guided setup (steps, a link that opens the
 * vendor's page — for Slack already filled in —, the fields it needs).
 * Anything else — a link, `npx …`, a `claude mcp add …` line, JSON, a GitHub
 * repo — goes through *Custom server* (Jarvis previews what it found).
 * Setup, Custom server and the OAuth-app form are sub-views: the header's back
 * button (and Esc) returns to the list, their buttons sit in the footer.
 * **Your servers**: one card per server with its status and the one thing to
 * do next — Authenticate, Enter key, Connect, Retry, Disconnect, or use your
 * own OAuth app for a host that only lets approved apps sign in.
 * **On / off**: every card has a switch — off keeps the server configured but
 * it never connects and Jarvis gets none of its tools —, and the top of
 * *Your servers* has one for MCP as a whole (`POST /api/mcp/enable` ·
 * `/api/mcp/power`; the terminal's `/mcp` dialog and `/mcp on|off` share it).
 *
 * A hosted server that needs a browser sign-in also puts a banner above the
 * composer and a dot on the sidebar's MCP button, so the button is one click
 * away even with the dialog shut (the agent may have added the server).
 * Server side: jarvis/web/extensions_api.py (+ jarvis/mcp/catalog.py).
 *
 * Sign-in: the click opens a blank tab *synchronously* (pop-up blockers allow
 * that), the server hands back the provider's link, the tab is pointed at it,
 * and the page then watches until the server is live. On another device the
 * last page won't load — its address can be pasted instead.
 */
import { $, escapeHtml, showToast, haptic, debounce, copyText, originBadge } from './utils.js';
import { icon } from './icons.js';
import { store, loadSnapshot } from './store.js';
import { openModal, closeModal, isModalOpen } from './modal.js';
import { fetchMcpServers, fetchMcpAuth, extPost, pickerAction } from './api.js';
import { btn, rowBtn, moreBtn, seg, mark, patch, field, autosize, msg, spin, plural, tildify, openMenu, closeMenu, hostOf } from './extui.js';
import { setView, setHomeSub, toolbar, section, footer, empty, arrowRows } from './dialog.js';

const TONE = { live: 'accent', auth: 'amber', key: 'amber', warn: 'amber', failed: 'chili', connecting: 'slate', idle: 'slate', off: 'slate' };
const RANK = { auth: 0, key: 1, failed: 2, warn: 3, connecting: 4, live: 5, idle: 6, off: 7 };
const SCOPES = [
  { value: 'project', label: 'This project', title: 'Only in this folder (.mcp.json)' },
  { value: 'global', label: 'Global', title: 'Every project on this computer' },
];
/** How a marketplace server signs in → badge (label, icon, tone). */
const AUTH = {
  oauth: { label: 'Sign in', ic: 'log-in', tone: 'accent' },
  open: { label: 'No account', ic: 'zap', tone: 'ok' },
  key: { label: 'API key', ic: 'key-round', tone: 'warn' },
  app: { label: 'Your app', ic: 'app-window', tone: 'indigo' },
  desktop: { label: 'Desktop app', ic: 'monitor', tone: 'indigo' },
  local: { label: 'Runs locally', ic: 'terminal', tone: 'slate' },
};
/** Short chip names for the categories (the server sends the full ones). */
const CAT_LABEL = { Communication: 'Chat', 'Developer tools': 'Dev tools', 'Search & web': 'Web', 'On this computer': 'Local' };
const ALL = 'All';
const SOURCE_RE = /^(https?:\/\/|npx\s|uvx\s|docker\s|claude\s+mcp\s|\{|"[^"]+"\s*:|\.\/|\/|~\/|[A-Z_][A-Z0-9_]*=)/;
const APP_ERR_RE = /registered apps|approved apps|pre-?registered|client id wasn.t accepted/i;

let data = null; // last /api/mcp
let loadError = false;
let openName = null; // expanded card
let confirmName = null;
const busy = {}; // name → what's in flight ('connect' | 'disconnect' | 'keys' | …)
const notes = {}; // name → { text, error }
const auth = {}; // name → { starting, url, opened, status, startedAt, expiresAt, redirect, finishing }
const drafts = {}; // input id → text typed so far
const revealed = new Set();
const toolsOpen = new Set();
const dismissed = new Set(); // banner closed for a server (until it stops needing sign-in)
const seen = new Set(); // server names already listed once (only new ones animate in)
let pollTimer = 0;
let previewSeq = 0;
let tab = ''; // 'market' | 'servers' ('' = pick for the user)
let svq = ''; // Your servers search
let powerBusy = false; // MCP as a whole is being switched
/** The sub-view on show: null (the tabs) | { kind: 'custom' } | { kind: 'setup', id } | { kind: 'app', name }. */
let view = null;
const HINTS = [['↑ ↓', 'move'], ['↵', 'connect'], ['esc', 'close']];
const LOCK_NOTE = `<p class="pv-foot">${icon('lock')}<span>Keys and sign-ins stay on the computer running Jarvis. This browser never keeps them.</span></p>`;

const add = {
  text: '',
  scope: 'global',
  scopeTouched: false,
  preview: null, // { loading } | { servers } | { error }
  adding: false,
  wantAdd: false,
  result: null, // { servers: [...] } | { error }
};

/** The marketplace's own state. */
const mk = {
  q: '',
  cat: ALL,
  active: 0, // keyboard row in the list
  busy: {}, // id → true while adding / connecting
  notes: {}, // id → { text, error }
};

const listeners = new Set();
/** Others (sidebar) get the server list whenever it changes. */
export function onMcpChange(fn) {
  listeners.add(fn);
  if (data) fn(data);
}

const onThisComputer = () => /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
const looksLikeSource = (text) => SOURCE_RE.test(String(text || '').trim());

// ─── Status ───────────────────────────────────────────────────────────────

/** MCP as a whole (the switch at the top of Your servers). */
const mcpOn = () => data?.mcp_enabled !== false;

function statusOf(s) {
  const h = s.health || {};
  if (h.connected) return 'live';
  if (h.status === 'off' || s.enabled === false || !mcpOn()) return 'off';
  if (auth[s.name] || h.status === 'auth') return 'auth';
  if (busy[s.name] === 'connect' || h.status === 'connecting') return 'connecting';
  if ((s.needs_credentials || []).length) return 'key';
  if (h.status === 'failed') return 'failed';
  if (h.status === 'warn') return 'warn';
  return 'idle';
}

function statusText(s, st) {
  const a = auth[s.name];
  switch (st) {
    case 'live': return `Connected · ${plural(s.tool_count || 0, 'tool')}`;
    case 'auth':
      if (a?.status === 'working') return 'Signing in…';
      return a && !a.starting ? 'Waiting for sign-in' : 'Sign-in needed';
    case 'key': return `Needs ${(s.needs_credentials || []).length > 1 ? 'keys' : 'a key'}`;
    case 'failed': return 'Couldn’t connect';
    case 'warn': return 'Check setup';
    case 'connecting': return 'Connecting…';
    case 'off': return s.enabled === false ? 'Turned off' : 'Off · MCP is off';
    default: return 'Not connected';
  }
}

const scopeLabel = (s) => (s.scope === 'project' ? 'Project' : s.source_label || 'Global');
const displayName = (s) => (s.catalog_id && s.label ? s.label : s.name);

function serverByName(name) {
  return data?.servers?.find((s) => s.name === name) || null;
}

function needsSignIn() {
  return (data?.servers || []).filter((s) => statusOf(s) === 'auth');
}

// ─── Markup: cards ────────────────────────────────────────────────────────

function step(n, body) {
  return `<div class="pv-step">${n === '' ? '' : `<span class="pv-n">${n}</span>`}<div class="pv-step-body">${body}</div></div>`;
}

function noteHtml(name) {
  const n = notes[name];
  return n?.text ? msg(n.text, n.error ? 'error' : 'ok') : '';
}

/** The brand tile of a marketplace server (falls back to a tinted monogram). */
function tile(row, { on = false, sm = false } = {}) {
  const color = /^#[0-9a-f]{6}$/i.test(row?.color || '') ? row.color : '';
  if (!color) return mark(row?.label || row?.name || '', 'slate', { on, sm });
  const text = String(row.monogram || row.label || '?').slice(0, 2);
  return `<span class="mk-tile${on ? ' is-on' : ''}${sm ? ' is-sm' : ''}${text.length > 1 ? ' is-two' : ''}" style="--brand:${color}" aria-hidden="true">${escapeHtml(text)}</span>`;
}

function headHtml(s) {
  const st = statusOf(s);
  const mk0 = s.color ? tile(s, { on: st === 'live' }) : mark(s.name, TONE[st], { on: st === 'live' });
  return `${mk0}
    <span class="pv-text">
      <span class="pv-title"><span class="ex-name">${escapeHtml(displayName(s))}</span>${originBadge(scopeLabel(s), s.also_labels, s.scope === 'project' ? 'Project · .mcp.json' : `Global · ${s.source_label || ''} config`)}</span>
      <span class="pv-sub ex-st is-${st}"><i class="ex-dot" aria-hidden="true"></i>${escapeHtml(statusText(s, st))}</span>
    </span>
    <span class="pv-chev">${icon('chevron-down')}</span>`;
}

/** A server's own on / off switch (a real checkbox: Space toggles it, screen readers say "switch"). */
function switchHtml(s) {
  const on = s.enabled !== false;
  const b = busy[s.name] === 'enable';
  const label = `${displayName(s)} ${on ? 'on' : 'off'}`;
  const title = !mcpOn()
    ? `MCP is off — ${on ? 'this server connects once MCP is back on' : 'this server stays off when MCP is back on'}`
    : on ? 'On — click to turn off (disconnects it, Jarvis loses its tools)' : 'Off — click to turn on and connect';
  return `<label class="ex-switch${b ? ' is-busy' : ''}${mcpOn() ? '' : ' is-muted'}" title="${escapeHtml(title)}">
    <input type="checkbox" class="switch" role="switch" data-act="enable" data-name="${escapeHtml(s.name)}" aria-label="${escapeHtml(label)}"${on ? ' checked' : ''}${b ? ' disabled aria-busy="true"' : ''}>
  </label>`;
}

function sideHtml(s) {
  const st = statusOf(s);
  const name = s.name;
  const b = busy[name];
  let primary = '';
  if (st === 'off') primary = '';
  else if (b && b !== 'connect') primary = `<span class="pv-side-busy">${spin('')}</span>`;
  else if (st === 'auth') {
    const a = auth[name];
    if (a?.starting) primary = rowBtn('auth', 'Opening', { busy: true, data: { name } });
    else if (a?.url) primary = rowBtn('auth', 'Open again', { data: { name } });
    else primary = rowBtn('auth', 'Authenticate', { cls: 'is-primary', ic: 'log-in', data: { name } });
  } else if (st === 'key') primary = rowBtn('open-key', 'Enter key', { cls: 'is-primary', ic: 'key-round', data: { name } });
  else if (st === 'connecting') primary = `<span class="pv-side-busy">${spin('')}</span>`;
  else if (st === 'live') primary = rowBtn('disconnect', 'Disconnect', { ic: 'plug', data: { name }, title: 'Disconnect' });
  else if (st === 'failed') primary = rowBtn('connect', 'Retry', { ic: 'refresh-cw', data: { name }, title: 'Retry' });
  else primary = rowBtn('connect', 'Connect', { cls: 'is-go', ic: 'plug-zap', data: { name }, title: 'Connect' });
  return `${primary}${switchHtml(s)}${moreBtn(name)}`;
}

function factsHtml(s) {
  const h = s.health || {};
  const where = tildify(s.scope === 'project'
    ? (data?.project_config_path || '.mcp.json')
    : s.source === 'jarvis' ? (data?.global_config_path || '') : `${s.source_label} config`);
  const kind = s.remote ? `Hosted · ${s.transport === 'sse' ? 'SSE' : 'streamable HTTP'}` : 'Runs a command on this computer';
  return `<dl class="ex-facts">
    <div><dt>${s.remote ? 'Address' : 'Command'}</dt><dd><code>${escapeHtml(s.endpoint || '—')}</code>
      <button type="button" class="ex-copy" data-act="copy" data-name="${escapeHtml(s.name)}" aria-label="Copy ${s.remote ? 'address' : 'command'}" title="Copy">${icon('copy')}</button></dd></div>
    <div><dt>Type</dt><dd>${escapeHtml(kind)}</dd></div>
    <div><dt>Saved in</dt><dd class="ex-where" title="${escapeHtml(where)}">${escapeHtml(where)}</dd></div>
    ${s.signed_in ? `<div><dt>Sign-in</dt><dd>Saved on this computer</dd></div>` : ''}
    ${h.last_tool_error ? `<div><dt>Last tool error</dt><dd class="ex-err">${escapeHtml(h.last_tool_error)}</dd></div>` : ''}
  </dl>`;
}

function expiresText(a) {
  if (!a?.expiresAt) return '';
  const min = Math.max(1, Math.round((a.expiresAt - Date.now()) / 60000));
  return `The link is good for about ${min} more minute${min === 1 ? '' : 's'}.`;
}

function authPanel(s) {
  const a = auth[s.name];
  const id = `mcp-paste-${s.name}`;
  if (!a) {
    return `<p class="pv-note">${icon('lock')}<span><strong>${escapeHtml(s.name)}</strong> needs you to sign in with your account. You approve access in your browser — Jarvis never sees your password.</span></p>
      <div class="pv-actions">${btn('auth', 'Authenticate', { cls: 'btn-primary', ic: 'log-in', data: { name: s.name } })}</div>`;
  }
  if (a.starting) return `<p class="pv-wait"><span class="pv-pulse" aria-hidden="true"></span><span>Getting the sign-in link…</span></p>`;
  const link = `<a class="btn pv-open" href="${escapeHtml(a.url)}" target="_blank" rel="noopener noreferrer" data-act="opened" data-name="${escapeHtml(s.name)}">${icon('log-in')}<span>${a.opened ? 'Open sign-in page again' : 'Open sign-in page'}</span>${icon('external-link')}</a>`;
  const working = a.status === 'working';
  const host = (() => {
    try { return new URL(a.redirect || '').host; } catch { return 'localhost'; }
  })();
  return `
    <p class="pv-wait"><span class="pv-pulse" aria-hidden="true"></span><span>${working
    ? 'Almost there — finishing the sign-in…'
    : onThisComputer()
      ? 'Approve in the browser tab that opened. This finishes by itself — come back to this tab.'
      : 'Approve in the browser tab that opened, then come back here.'}</span></p>
    <div class="pv-steps">
      ${step(1, `<strong>Approve access</strong><span class="pv-hint">Sign in to ${escapeHtml(s.name)} in the tab that opened. ${escapeHtml(expiresText(a))}</span>${link}`)}
      ${step(2, `<strong>Signed in on another device?</strong>
        <span class="pv-hint">The last page won’t load there. Copy its address (it starts with <code>${escapeHtml(host)}</code>) and paste it here.</span>
        ${field(id, { value: drafts[id] || '', placeholder: 'Paste the page address', label: 'Address of the last sign-in page', mono: true })}`)}
    </div>
    <div class="pv-actions">
      ${btn('finish-auth', 'Finish sign-in', { cls: 'btn-primary', data: { name: s.name }, busy: a.finishing })}
      ${btn('cancel-auth', 'Cancel', { cls: 'btn-quiet', data: { name: s.name }, disabled: a.finishing })}
    </div>`;
}

function keyPanel(s) {
  const vars = s.needs_credentials || [];
  const b = busy[s.name] === 'keys';
  const meta = (v) => ({ label: v, hint: '', secret: true, placeholder: '', ...((s.fields || {})[v] || {}) });
  const inputs = vars.map((v) => {
    const id = `mcp-key-${s.name}-${v}`;
    const f = meta(v);
    return `<label class="ex-lbl${f.label !== v ? ' is-friendly' : ''}" for="${escapeHtml(id)}">${escapeHtml(f.label)}${f.hint ? ` <em>${escapeHtml(f.hint)}</em>` : ''}</label>
      ${field(id, { value: drafts[id] || '', placeholder: f.placeholder || 'Paste it here', label: f.label, secret: f.secret !== false, shown: revealed.has(id), mono: true })}`;
  }).join('');
  const link = s.setup?.link
    ? `<a class="btn btn-sm mk-link" href="${escapeHtml(s.setup.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.setup.link_label || 'Where do I get it?')}${icon('external-link')}</a>`
    : '';
  const title = vars.length > 1 ? 'Enter these to finish' : `Enter ${s.fields?.[vars[0]] ? `the ${escapeHtml(meta(vars[0]).label.toLowerCase())}` : 'this key'}`;
  return `<div class="pv-steps is-single">${step('', `<strong>${title}</strong>${inputs}
      <span class="pv-hint">Saved on this computer only (<code>~/.config/harness-agent</code>) — never written into the config file.</span>`)}</div>
    <div class="pv-actions">${btn('save-keys', 'Save and connect', { cls: 'btn-primary', data: { name: s.name }, busy: b })}${link}</div>`;
}

/** Sign in through your own OAuth app — for hosts that only let approved apps sign in. */
/** The OAuth-app sub-view: for hosts that only let approved apps sign in. */
function appViewHtml(s) {
  const idKey = `mcp-app-${s.name}-id`;
  const secKey = `mcp-app-${s.name}-secret`;
  const redirect = data?.oauth_redirect_url || 'http://localhost:33418/callback';
  return `<div class="mk-view">
    ${viewHeadHtml(s, s.remote ? hostOf(s.endpoint) : '')}
    <p class="dlg-lead">Register an OAuth app in ${escapeHtml(displayName(s))}'s developer settings with the redirect URL below, then paste the app's Client ID (and its secret, if it has one). Jarvis signs in as that app.</p>
    <div class="mk-redirect"><span>Redirect URL</span><code>${escapeHtml(redirect)}</code>
      <button type="button" class="ex-copy" data-act="copy-text" data-text="${escapeHtml(redirect)}" aria-label="Copy the redirect URL" title="Copy">${icon('copy')}</button></div>
    <div class="mk-fields">
      <label class="ex-lbl is-friendly" for="${idKey}">Client ID</label>
      ${field(idKey, { value: drafts[idKey] || '', placeholder: 'Paste the Client ID', label: 'Client ID' })}
      <label class="ex-lbl is-friendly" for="${secKey}">Client Secret <em>optional</em></label>
      ${field(secKey, { value: drafts[secKey] || '', placeholder: 'Paste the Client Secret', label: 'Client Secret', secret: true, shown: revealed.has(secKey) })}
    </div>
    ${noteHtml(s.name)}
  </div>${LOCK_NOTE}`;
}

function failErr(s) {
  const h = s.health || {};
  return h.last_connect_error || h.detail || 'Could not connect.';
}

function failedPanel(s) {
  const h = s.health || {};
  const err = failErr(s);
  const appOnly = s.remote && APP_ERR_RE.test(err);
  // "Only approved apps" is said in plain words; the server's own wording stays in the tooltip.
  const line = appOnly
    ? `<p class="ex-errline" title="${escapeHtml(err)}">${icon('circle-alert')}<span>${escapeHtml(displayName(s))} only lets approved apps sign in.</span></p>`
    : `<p class="ex-errline">${icon('circle-alert')}<span>${escapeHtml(err)}</span></p>`;
  let next = '';
  if (appOnly) {
    next = `<p class="pv-hint">${s.alt_note ? escapeHtml(s.alt_note) : 'Sign in through an OAuth app of your own instead — register one in its developer settings, then paste its Client ID here.'}</p>`;
  } else if (s.remote && /api key|token|refused access|rejected the token|\b40[13]\b/i.test(err)) {
    next = '<p class="pv-hint">If it needs a token, remove it and add it again as a <em>Custom server</em> with an <code>Authorization</code> header — Jarvis keeps the token out of the file.</p>';
  }
  return `${line}
    ${(h.hints || []).length ? `<ul class="ex-hints">${h.hints.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : ''}
    ${next}
    <div class="pv-actions">
      ${btn('connect', 'Try again', { cls: appOnly ? '' : 'btn-primary', ic: 'refresh-cw', data: { name: s.name }, busy: busy[s.name] === 'connect' })}
      ${appOnly ? btn('open-app', 'Use my own OAuth app…', { cls: 'btn-primary', ic: 'app-window', data: { name: s.name } }) : ''}
    </div>`;
}

function warnPanel(s) {
  const h = s.health || {};
  const hints = h.hints || [];
  return `${msg(hints.length ? hints.join(' · ') : h.detail || 'Something looks off with this server.', 'warn')}
    <div class="pv-actions">${btn('connect', 'Connect anyway', { data: { name: s.name }, busy: busy[s.name] === 'connect' })}</div>`;
}

function toolsHtml(s) {
  const tools = s.tools || [];
  if (!tools.length) return '';
  const open = toolsOpen.has(s.name);
  const more = (s.tool_count || tools.length) - tools.length;
  return `<div class="ex-tools${open ? ' is-open' : ''}">
    <button type="button" class="ex-tools-toggle" data-act="tools" data-name="${escapeHtml(s.name)}" aria-expanded="${open}">${icon('wrench')}<span>${plural(s.tool_count || tools.length, 'tool')} Jarvis can call</span><span class="ex-tools-chev">${icon('chevron-down')}</span></button>
    ${open ? `<div class="ex-tool-list">${tools.map((t) => `<code>${escapeHtml(t)}</code>`).join('')}${more > 0 ? `<span class="pv-hint">+${more} more</span>` : ''}</div>` : ''}
  </div>`;
}

function offPanel(s) {
  if (!mcpOn()) {
    return `<p class="pv-note ex-off-note">${icon('power')}<span>MCP is off, so no server connects. ${s.enabled === false
      ? 'This one also stays off when MCP is turned back on.'
      : 'This one connects again once MCP is back on.'}</span></p>
      <div class="pv-actions">${btn('power-on', 'Turn MCP on', { cls: 'btn-primary', ic: 'power' })}</div>`;
  }
  return `<p class="pv-note ex-off-note">${icon('circle-off')}<span>Turned off. ${escapeHtml(displayName(s))} stays in your config, but Jarvis won’t connect it or use its tools until you turn it back on.</span></p>
    <div class="pv-actions">${btn('enable-on', 'Turn on', { cls: 'btn-primary', ic: 'power', data: { name: s.name }, busy: busy[s.name] === 'enable' })}</div>`;
}

function panelHtml(s) {
  const st = statusOf(s);
  let mid = '';
  if (st === 'off') mid = offPanel(s);
  else if (st === 'auth') mid = authPanel(s);
  else if (st === 'key') mid = keyPanel(s);
  else if (st === 'failed') mid = failedPanel(s);
  else if (st === 'warn') mid = warnPanel(s);
  else if (st === 'live') mid = toolsHtml(s);
  // One error, once: a note that repeats the card's own error isn't shown again.
  const n = notes[s.name];
  const dup = n?.text && st === 'failed' && (failErr(s).includes(n.text) || n.text.includes(failErr(s)));
  return `${mid}${dup ? '' : noteHtml(s.name)}${factsHtml(s)}`;
}

function rowClass(s) {
  const st = statusOf(s);
  return `pv-row ex-row is-${st}${openName === s.name ? ' is-open' : ''}${st === 'live' ? ' is-connected' : ''}${busy[s.name] === 'enable' ? ' is-switching' : ''}`;
}

function rowHtml(s, i, fresh) {
  return `
    <div class="${rowClass(s)}${fresh ? ' just-added' : ''}" data-name="${escapeHtml(s.name)}" style="--i:${i}">
      <button type="button" class="pv-head" data-act="toggle" aria-expanded="${openName === s.name}">${headHtml(s)}</button>
      <div class="pv-side">${sideHtml(s)}</div>
      <div class="fold"><div class="fold-inner"><div class="pv-panel ex-panel">${panelHtml(s)}</div></div></div>
    </div>`;
}

function sortedServers() {
  return [...(data?.servers || [])].sort((a, b) => {
    const d = RANK[statusOf(a)] - RANK[statusOf(b)];
    return d || a.name.localeCompare(b.name);
  });
}

// ─── Markup: add panel ────────────────────────────────────────────────────

function pathHint() {
  if (!data) return '';
  if (add.scope === 'project') {
    return `Saved to <code>${escapeHtml(tildify(data.project_config_path) || '.mcp.json')}</code> — only in this folder.`;
  }
  return `Saved to <code>${escapeHtml(tildify(data.global_config_path) || '~/.config/harness-agent/mcp.json')}</code> — every project.`;
}

function credFields(sv) {
  const vars = sv.credentials || [];
  if (!vars.length) return '';
  return `<div class="ex-pv-creds">
    ${vars.map((v) => {
    const id = `mcp-cred-${v}`;
    return `<label class="ex-lbl" for="${escapeHtml(id)}">${escapeHtml(v)} <em>optional now</em></label>
      ${field(id, { value: drafts[id] || '', placeholder: 'Paste it, or add it later', label: v, secret: true, shown: revealed.has(id) })}`;
  }).join('')}
    <span class="pv-hint">Saved on this computer only — never in the config file.</span>
  </div>`;
}

function previewCard(sv) {
  const here = (sv.exists || []).includes(add.scope);
  const other = (sv.exists || []).filter((x) => x !== add.scope);
  return `<div class="ex-pv">
    <div class="ex-pv-top">
      ${mark(sv.name, sv.local ? 'slate' : 'indigo', { sm: true })}
      <strong class="ex-name">${escapeHtml(sv.name)}</strong>
      <span class="badge">${sv.local ? 'Runs a command' : `Hosted · ${escapeHtml(sv.transport === 'sse' ? 'SSE' : 'HTTP')}`}</span>
    </div>
    <code class="ex-endpoint">${escapeHtml(sv.endpoint)}</code>
    ${sv.local ? `<p class="pv-hint ex-warnline">${icon('terminal')}<span>Jarvis will run this on your computer each time it connects. Only add commands you trust.</span></p>` : `<p class="pv-hint ex-warnline">${icon('lock')}<span>If it needs a sign-in you’ll get an Authenticate button.</span></p>`}
    ${(sv.notes || []).map((n) => `<p class="pv-hint">${escapeHtml(n)}</p>`).join('')}
    ${here ? `<p class="pv-hint is-warn-text">Already in the ${add.scope} config — adding again just connects it.</p>` : ''}
    ${!here && other.length ? `<p class="pv-hint">Also in your ${escapeHtml(other.join(' and '))} config.</p>` : ''}
    ${credFields(sv)}
  </div>`;
}

function resultHtml(res) {
  if (res.error && !(res.servers || []).length) return msg(res.error, 'error');
  return (res.servers || []).map((r) => {
    const sc = r.scope === 'project' ? 'this project' : 'global';
    let kind = 'ok';
    let text = '';
    let act = '';
    switch (r.status) {
      case 'connected':
        text = `${r.name} is connected · ${plural(r.tool_count || 0, 'tool')} (${sc}).`;
        break;
      case 'auth_required': {
        kind = 'warn';
        text = `${r.name} added (${sc}). Sign in to finish connecting.`;
        act = btn('auth', auth[r.name]?.url ? 'Open sign-in page again' : 'Authenticate', { cls: 'btn-primary', ic: 'log-in', data: { name: r.name }, busy: !!auth[r.name]?.starting });
        break;
      }
      case 'needs_credentials':
        kind = 'warn';
        text = `${r.name} added (${sc}). It needs ${(r.missing || []).join(', ')} before it can connect.`;
        act = btn('open-key', 'Enter key', { cls: 'btn-primary', ic: 'key-round', data: { name: r.name } });
        break;
      case 'added':
        text = `${r.name} added (${sc}), not connected yet.`;
        act = btn('connect', 'Connect', { data: { name: r.name } });
        break;
      case 'exists':
        kind = 'warn';
        text = r.message || `${r.name} already exists.`;
        break;
      case 'denied':
        kind = 'warn';
        text = r.message || 'Not added.';
        break;
      default:
        kind = 'error';
        text = `${r.name} is saved (${sc}) but couldn’t connect: ${r.error || 'unknown error'}`;
        act = btn('connect', 'Try again', { ic: 'refresh-cw', data: { name: r.name } });
    }
    const extra = [r.scope_note, ...(r.notes || [])].filter(Boolean).map((n) => `<p class="pv-hint">${escapeHtml(n)}</p>`).join('');
    return `<div class="ex-res">${msg(text, kind)}${extra}${act ? `<div class="pv-actions">${act}</div>` : ''}</div>`;
  }).join('');
}

function previewHtml() {
  if (add.result) return resultHtml(add.result);
  const p = add.preview;
  if (!add.text.trim()) {
    return '<p class="pv-hint ex-idle">Paste a hosted address (https://…/mcp), an install command (npx -y … / uvx …), a <code>claude mcp add …</code> line, a JSON config or a GitHub repo. Jarvis shows what it will run or connect to before adding anything.</p>';
  }
  if (!p || p.loading) return `<p class="pv-hint ex-loading">${spin('Reading that…')}</p>`;
  if (p.error) return msg(p.error, 'error');
  return p.servers.map(previewCard).join('');
}

function addBtnHtml() {
  if (add.result) return btn('custom-done', 'Done', { cls: 'btn-primary', ic: 'check' });
  const ok = add.preview?.servers?.length;
  const n = add.preview?.servers?.length || 0;
  const label = add.adding ? 'Adding…' : n > 1 ? `Add ${n} servers` : 'Add server';
  return `<button type="button" class="btn btn-primary ex-add-btn" data-act="add"${ok && !add.adding && !add.result ? '' : ' disabled'}>${add.adding ? spin(label) : `${icon('plus')}<span>${label}</span>`}</button>`;
}

// ─── Markup: marketplace ──────────────────────────────────────────────────

const catalogRows = () => data?.catalog || [];
const catalogRow = (id) => catalogRows().find((r) => r.id === id) || null;
const guided = (r) => ['key', 'app', 'desktop'].includes(r?.auth);

/** Every word must match (same rule as jarvis/mcp/catalog.matches). */
function matches(r, q) {
  const words = String(q || '').toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [r.id, r.label, r.desc, r.category, r.auth_label, AUTH[r.auth]?.label, r.keywords, hostOf(r.endpoint)]
    .filter(Boolean).join(' ').toLowerCase();
  return words.every((w) => hay.includes(w));
}

/** Rows the list shows now, in order: `{ kind: 'head' | 'row' | 'source' | 'empty', … }`. */
function marketItems() {
  const q = mk.q.trim();
  if (looksLikeSource(q)) return [{ kind: 'source', key: 'source' }];
  let rows = catalogRows().filter((r) => matches(r, q));
  if (mk.cat === 'Popular') rows = rows.filter((r) => r.popular);
  else if (mk.cat !== ALL) rows = rows.filter((r) => r.category === mk.cat);
  if (q) {
    const ql = q.toLowerCase();
    const starts = (r) => (r.label.toLowerCase().startsWith(ql) || r.id.startsWith(ql) ? 0 : 1);
    rows = [...rows].sort((a, b) => starts(a) - starts(b)); // stable: popular order kept within
  }
  const items = [];
  if (!q && mk.cat === ALL) {
    const pop = rows.filter((r) => r.popular);
    const rest = rows.filter((r) => !r.popular).sort((a, b) => a.label.localeCompare(b.label));
    if (pop.length) items.push({ kind: 'head', key: 'h-pop', label: 'Popular', count: pop.length });
    pop.forEach((r) => items.push({ kind: 'row', key: r.id, row: r }));
    if (rest.length) items.push({ kind: 'head', key: 'h-all', label: 'More servers', count: rest.length });
    rest.forEach((r) => items.push({ kind: 'row', key: r.id, row: r }));
  } else {
    if (rows.length) items.push({ kind: 'head', key: 'h-res', label: q ? 'Results' : CAT_LABEL[mk.cat] || mk.cat, count: rows.length });
    rows.forEach((r) => items.push({ kind: 'row', key: r.id, row: r }));
  }
  if (!rows.length) items.push({ kind: 'empty', key: 'empty' });
  return items;
}

const pickable = (items) => items.filter((it) => it.kind === 'row' || it.kind === 'source');

/** The server a catalog row was added as, with its live state (or null). */
function installedOf(r) {
  return r?.installed ? serverByName(r.installed) : null;
}

function badgeHtml(r) {
  const a = AUTH[r.auth] || { label: r.auth_label || '', ic: 'plug', tone: 'slate' };
  return `<span class="mk-badge is-${a.tone}">${icon(a.ic)}<span>${escapeHtml(r.auth_label && r.auth !== 'desktop' ? r.auth_label : a.label)}</span></span>`;
}

function mkSideHtml(r) {
  const id = r.id;
  if (mk.busy[id]) return `<span class="mk-busy">${spin('Connecting')}</span>`;
  const s = installedOf(r);
  if (s) {
    const st = statusOf(s);
    const a = auth[s.name];
    if (st === 'live') {
      return `<span class="mk-state is-live"><i class="ex-dot" aria-hidden="true"></i>${escapeHtml(plural(s.tool_count || 0, 'tool'))}</span>${rowBtn('mk-manage', 'Manage', { data: { id } })}`;
    }
    if (st === 'auth') {
      if (a?.starting) return rowBtn('auth', 'Opening', { busy: true, data: { name: s.name } });
      return rowBtn('auth', a?.url ? 'Open again' : 'Sign in', { cls: 'is-primary', ic: 'log-in', data: { name: s.name } });
    }
    if (st === 'connecting') return `<span class="mk-busy">${spin('Connecting')}</span>`;
    if (st === 'off') return `<span class="mk-state is-off">${icon('circle-off')}Off</span>${rowBtn('mk-manage', 'Manage', { data: { id } })}`;
    if (st === 'key') return rowBtn('mk-manage', 'Enter key', { cls: 'is-primary', ic: 'key-round', data: { id } });
    if (st === 'failed') return rowBtn('mk-manage', 'Fix', { cls: 'is-warn', ic: 'circle-alert', data: { id } });
    return rowBtn('connect', 'Connect', { cls: 'is-go', ic: 'plug-zap', data: { name: s.name } });
  }
  if (guided(r)) return rowBtn('mk-setup', 'Set up', { cls: 'is-go', data: { id } });
  return rowBtn('mk-connect', 'Connect', { cls: 'is-go', ic: r.auth === 'local' ? 'plug-zap' : r.auth === 'open' ? 'zap' : 'log-in', data: { id } });
}

/** The unfolded part of a row: sign-in progress or what just happened. */
function mkPanelHtml(r) {
  const id = r.id;
  const s = installedOf(r);
  const n = mk.notes[id];
  const note = n?.text ? msg(n.text, n.error ? 'error' : 'ok') : '';
  if (s) {
    const a = auth[s.name];
    if (statusOf(s) === 'auth' && a && !a.starting) {
      return `<p class="pv-wait"><span class="pv-pulse" aria-hidden="true"></span><span>${a.status === 'working'
        ? 'Almost there — finishing the sign-in…'
        : `Approve access to ${escapeHtml(r.label)} in the tab that opened — this finishes by itself.`}</span></p>
        <div class="pv-actions">
          <a class="btn btn-sm" href="${escapeHtml(a.url)}" target="_blank" rel="noopener noreferrer" data-act="opened" data-name="${escapeHtml(s.name)}">${icon('log-in')}<span>Open sign-in page</span>${icon('external-link')}</a>
          ${btn('mk-manage', 'Signed in on another device?', { cls: 'btn-quiet btn-sm', data: { id } })}
        </div>${note}`;
    }
  }
  return note;
}

/** Tile · name · badge · where it lives — the top of a sub-view. */
function viewHeadHtml(row, where = '') {
  const label = row.label || row.name;
  return `<div class="mk-vhead">
    ${row.color ? tile(row) : mark(label, 'slate')}
    <span class="pv-text">
      <span class="pv-title mk-title"><span class="ex-name">${escapeHtml(label)}</span>${row.auth ? badgeHtml(row) : ''}</span>
      <span class="pv-sub mk-desc">${escapeHtml(row.desc || where || '')}</span>
    </span>
  </div>`;
}

/** The guided setup sub-view of a key / app / desktop server. */
function setupViewHtml(r) {
  const id = r.id;
  const su = r.setup || {};
  const vars = r.credentials || [];
  const steps = (su.steps || []).map((t, i) => `<li><span class="pv-n">${i + 1}</span><span>${escapeHtml(t)}</span></li>`).join('');
  const link = su.link
    ? `<a class="btn btn-sm mk-link" href="${escapeHtml(su.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(su.link_label || 'Open')}${icon('external-link')}</a>`
    : '';
  const redirect = r.auth === 'app' && r.redirect_url
    ? `<div class="mk-redirect"><span>Redirect URL</span><code>${escapeHtml(r.redirect_url)}</code>
        <button type="button" class="ex-copy" data-act="copy-text" data-text="${escapeHtml(r.redirect_url)}" aria-label="Copy the redirect URL" title="Copy">${icon('copy')}</button>
        <em>already in the pre-filled app</em></div>`
    : '';
  const inputs = vars.map((v) => {
    const f = { label: v, hint: '', secret: true, placeholder: '', ...((r.fields || {})[v] || {}) };
    const fid = `mk-f-${id}-${v}`;
    return `<label class="ex-lbl is-friendly" for="${escapeHtml(fid)}">${escapeHtml(f.label)}${f.hint ? ` <em>${escapeHtml(f.hint)}</em>` : ''}</label>
      ${field(fid, { value: drafts[fid] || '', placeholder: f.placeholder || 'Paste it here', label: f.label, secret: f.secret !== false, shown: revealed.has(fid) })}`;
  }).join('');
  const why = su.why || (vars.length ? 'Kept on the computer running Jarvis — never written into a config file, never shown to the model.' : '');
  const n = mk.notes[id];
  return `<div class="mk-view">
    ${viewHeadHtml(r)}
    ${why ? `<p class="dlg-lead">${escapeHtml(why)}</p>` : ''}
    ${steps ? `<ol class="mk-steps">${steps}</ol>` : ''}
    ${link || redirect ? `<div class="mk-setup-links">${link}${redirect}</div>` : ''}
    ${inputs ? `<div class="mk-fields">${inputs}</div>` : ''}
    ${su.note ? `<p class="pv-hint mk-note">${icon('info')}<span>${escapeHtml(su.note)}</span></p>` : ''}
    ${n?.text ? msg(n.text, n.error ? 'error' : 'ok') : ''}
  </div>${vars.length ? LOCK_NOTE : ''}`;
}

function mkRowClass(r, active) {
  const s = installedOf(r);
  const st = s ? statusOf(s) : '';
  const panel = mkPanelHtml(r);
  return `pv-row mk-row${panel ? ' is-open' : ''}${active ? ' is-active' : ''}${st ? ` is-${st}` : ''}${st === 'live' ? ' is-connected' : ''}`;
}

function mkTextHtml(r) {
  const s = installedOf(r);
  const st = s ? statusOf(s) : '';
  const sub = s && st !== 'live' && st !== 'idle'
    ? `<span class="pv-sub ex-st is-${st}"><i class="ex-dot" aria-hidden="true"></i>${escapeHtml(statusText(s, st))}</span>`
    : `<span class="pv-sub mk-desc">${escapeHtml(r.desc || '')}</span>`;
  return `<span class="pv-title mk-title"><span class="ex-name">${escapeHtml(r.label)}</span>${badgeHtml(r)}</span>${sub}`;
}

function mkRowHtml(r, active, i) {
  const st = installedOf(r) ? statusOf(installedOf(r)) : '';
  return `<div class="${mkRowClass(r, active)}" data-id="${escapeHtml(r.id)}" id="mk-opt-${escapeHtml(r.id)}" role="option" aria-selected="${active}" style="--i:${i}">
    <button type="button" class="pv-head mk-head" data-act="mk-pick" data-id="${escapeHtml(r.id)}" tabindex="-1">
      ${tile(r, { on: st === 'live' })}
      <span class="pv-text">${mkTextHtml(r)}</span>
    </button>
    <div class="pv-side">${mkSideHtml(r)}</div>
    <div class="fold"><div class="fold-inner"><div class="pv-panel mk-panel">${mkPanelHtml(r)}</div></div></div>
  </div>`;
}

function mkItemHtml(it, active, i) {
  if (it.kind === 'head') return section(it.label, { count: it.count });
  if (it.kind === 'empty') {
    const q = mk.q.trim();
    const actions = `${btn('mk-custom', 'Add a custom server', { cls: 'btn-primary', ic: 'link' })}${mk.cat !== ALL ? btn('mk-cat', 'All categories', { data: { val: ALL } }) : ''}`;
    return empty(q ? `No server matches “${q}”` : 'Nothing here yet',
      'Try another word — or add it yourself: paste its link, an <code>npx</code> / <code>uvx</code> command or a JSON config.',
      { ic: 'search', action: actions });
  }
  if (it.kind === 'source') {
    const q = mk.q.trim();
    return `<div class="pv-row mk-row mk-src${active ? ' is-active' : ''}" role="option" id="mk-opt-source" aria-selected="${active}">
      <button type="button" class="pv-head mk-head" data-act="mk-source" tabindex="-1">
        <span class="mk-tile is-plus" aria-hidden="true">${icon('plus')}</span>
        <span class="pv-text"><span class="pv-title">Add as a custom server</span><span class="pv-sub mk-desc"><code>${escapeHtml(q.length > 90 ? `${q.slice(0, 90)}…` : q)}</code></span></span>
      </button>
      <div class="pv-side">${rowBtn('mk-source', 'Preview', { cls: 'is-go' })}</div>
    </div>`;
  }
  return mkRowHtml(it.row, active, i);
}

function catsHtml() {
  const cats = [ALL, ...(data?.categories || [])];
  const rows = catalogRows();
  const count = (c) => (c === ALL ? rows.length : c === 'Popular' ? rows.filter((r) => r.popular).length : rows.filter((r) => r.category === c).length);
  return cats.filter((c) => count(c) > 0).map((c) => `<button type="button" class="mk-cat" data-act="mk-cat" data-val="${escapeHtml(c)}" aria-pressed="${c === mk.cat}">${escapeHtml(CAT_LABEL[c] || c)}</button>`).join('');
}

function tabsHtml() {
  const servers = data?.servers || [];
  const need = servers.filter((s) => ['auth', 'key', 'failed'].includes(statusOf(s))).length;
  const t = currentTab();
  return `<div class="mk-tabs" role="tablist" aria-label="MCP">
    <button type="button" role="tab" class="mk-tab" data-act="tab" data-val="servers" aria-selected="${t === 'servers'}" tabindex="${t === 'servers' ? 0 : -1}">${icon('plug')}<span>Your servers</span>${servers.length ? `<em class="mk-count${need ? ' is-attn' : ''}">${servers.length}</em>` : ''}</button>
    <button type="button" role="tab" class="mk-tab" data-act="tab" data-val="market" aria-selected="${t === 'market'}" tabindex="${t === 'market' ? 0 : -1}">${icon('sparkles')}<span>Marketplace</span></button>
  </div>`;
}

/** The tab shown: the user's pick, else Your servers when there are any, else the Marketplace. */
function currentTab() {
  if (tab) return tab;
  return (data?.servers || []).length ? 'servers' : 'market';
}

// ─── Render ───────────────────────────────────────────────────────────────

function restoreFocus(id, sel) {
  if (!id || document.activeElement?.id === id) return;
  const el = $(id);
  if (!el) return;
  el.focus({ preventScroll: true });
  try { el.setSelectionRange(sel[0], sel[1]); } catch { /* not a text input */ }
}

/** Run `paint` without losing the field being typed in (patch() replaces markup). */
function keepFocus(root, paint) {
  const focused = document.activeElement;
  const id = focused?.id && root?.contains(focused) && /^(INPUT|TEXTAREA)$/.test(focused.tagName) ? focused.id : '';
  const sel = id ? [focused.selectionStart, focused.selectionEnd] : null;
  paint();
  restoreFocus(id, sel);
}

/** The body shows one thing at a time; its skeleton is rebuilt only when that changes. */
function bodyMode() {
  if (view?.kind === 'custom') return 'custom';
  if (view?.kind === 'setup') return `setup:${view.id}`;
  if (view?.kind === 'app') return `app:${view.name}`;
  return currentTab();
}

function ensureBody(body, mode) {
  if (body.dataset.mode === mode) return;
  body.dataset.mode = mode;
  body._html = null;
  body.scrollTop = 0;
  if (mode === 'market') {
    body.innerHTML = `<div class="mk-list" id="mk-list" role="listbox" aria-label="MCP servers"></div>${LOCK_NOTE}`;
  } else if (mode === 'servers') {
    body.innerHTML = `<div id="sv-head"></div><div class="ex-list" id="mcp-list"></div>${LOCK_NOTE}`;
  } else if (mode === 'custom') {
    body.innerHTML = `<section class="ex-add is-flat mk-custom" aria-label="Custom server">
        <p class="dlg-lead">Paste a hosted address (<code>https://…/mcp</code>), an install command (<code>npx -y …</code> / <code>uvx …</code>), a <code>claude mcp add …</code> line, a JSON config or a GitHub repo. Jarvis shows what it will run or connect to before adding anything.</p>
        <div class="ex-src">
          <span class="pv-field-ic">${icon('link')}</span>
          <textarea id="mcp-src" class="ex-src-input" rows="1" placeholder="Paste a link, npx …, claude mcp add …, JSON or a GitHub repo"
            aria-label="Server to add" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
            data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other"></textarea>
        </div>
        <p class="ex-path" id="mcp-path"></p>
        <div class="ex-preview" id="mcp-preview" aria-live="polite"></div>
      </section>${LOCK_NOTE}`;
    const ta = $('mcp-src');
    ta.value = add.text;
    requestAnimationFrame(() => autosize(ta));
  } else {
    body.innerHTML = '<div id="mk-view-body"></div>';
  }
}

/** Tabs and toolbar: the search input is built once per tab (rebuilding it would drop the caret). */
function renderBar() {
  const tabsEl = $('mcp-tabs');
  const bar = $('mcp-bar');
  if (tabsEl) {
    tabsEl.hidden = !!view;
    if (!view) patch(tabsEl, tabsHtml());
  }
  if (!bar) return;
  bar.hidden = !!view;
  if (view) return;
  const t = currentTab();
  if (bar.dataset.tab !== t) {
    bar.dataset.tab = t;
    bar._html = null;
    bar.innerHTML = t === 'market'
      ? `${toolbar({ id: 'mk-q', placeholder: `Search ${catalogRows().length || 60}+ servers — Slack, Notion, GitHub…`, label: 'Search the MCP marketplace', value: mk.q,
        attrs: { role: 'combobox', 'aria-controls': 'mk-list', 'aria-autocomplete': 'list' },
        action: { act: 'mk-custom', label: 'Custom server', ic: 'link', title: 'Add any server: a link, npx / uvx command, JSON or GitHub repo' } })}
        <div class="mk-cats" id="mk-cats" role="group" aria-label="Categories"></div>`
      : toolbar({ id: 'sv-q', placeholder: 'Search your servers', label: 'Search your MCP servers', value: svq,
        attrs: { role: 'combobox', 'aria-controls': 'mcp-list', 'aria-autocomplete': 'list' },
        action: { act: 'tab', label: 'Add server', ic: 'plus', title: 'Browse the marketplace', data: { val: 'market' } } });
  }
  if (t === 'market') patch($('mk-cats'), catsHtml());
}

function renderFoot() {
  const foot = $('mcp-foot');
  if (!foot || !data) return;
  let html = '';
  if (view?.kind === 'custom') {
    html = `<div class="dlg-actions"><div class="dlg-scope"><span class="dlg-scope-lbl">Save to</span>${seg(SCOPES, add.scope, { label: 'Where to add it', act: 'scope', group: 'add' })}</div>
      <span class="dlg-spacer"></span>${btn('back', 'Cancel', { cls: 'btn-quiet' })}${addBtnHtml()}</div>`;
  } else if (view?.kind === 'setup') {
    const r = catalogRow(view.id);
    html = `<div class="dlg-actions"><div class="dlg-scope"><span class="dlg-scope-lbl">Save to</span>${seg(SCOPES, add.scope, { label: 'Where to add it', act: 'scope', group: 'add' })}</div>
      <span class="dlg-spacer"></span>${btn('back', 'Cancel', { cls: 'btn-quiet' })}${btn('mk-connect', 'Connect', { cls: 'btn-primary', ic: r?.auth === 'app' ? 'log-in' : 'plug-zap', data: { id: view.id }, busy: !!mk.busy[view.id] })}</div>`;
  } else if (view?.kind === 'app') {
    html = `<div class="dlg-actions"><span class="dlg-spacer"></span>${btn('back', 'Cancel', { cls: 'btn-quiet' })}${btn('save-app', 'Save and sign in', { cls: 'btn-primary', ic: 'log-in', data: { name: view.name }, busy: busy[view.name] === 'app' })}</div>`;
  } else if (currentTab() === 'market') {
    html = footer({ scopeLabel: 'Save to', scope: seg(SCOPES, add.scope, { label: 'Where new servers are added', act: 'scope', group: 'add' }), hints: HINTS });
  } else {
    html = footer({
      scope: seg([
        { value: 'false', label: 'This project', title: 'Only servers from this folder' },
        { value: 'true', label: 'Project + global', title: 'Also servers added for every project' },
      ], String(!!data.global_mcp), { label: 'Which servers to use', act: 'global' }),
      hints: HINTS,
    });
  }
  keepFocus(foot, () => patch(foot, html));
}

function renderAddPanel() {
  patch($('mcp-path'), pathHint());
  renderPreview();
}

function renderPreview() {
  const el = $('mcp-preview');
  if (el) keepFocus(el, () => patch(el, previewHtml()));
  renderFoot();
}

function renderMarket({ scrollActive = false } = {}) {
  const list = $('mk-list');
  if (!list || !data) return;
  const items = marketItems();
  const picks = pickable(items);
  mk.active = Math.max(0, Math.min(mk.active, picks.length - 1));
  const activeKey = picks[mk.active]?.key;
  const sig = items.map((it) => it.key).join(',');
  keepFocus(list, () => {
    if (list.dataset.sig !== sig) {
      list.innerHTML = items.map((it, i) => mkItemHtml(it, it.key === activeKey, i)).join('');
      list.dataset.sig = sig;
      return;
    }
    // Same rows: update each in place so a fold animates and nothing flickers.
    for (const it of items) {
      if (it.kind === 'source') {
        const el = $('mk-opt-source');
        el?.classList.toggle('is-active', it.key === activeKey);
        el?.setAttribute('aria-selected', String(it.key === activeKey));
      }
      if (it.kind !== 'row') continue;
      const el = list.querySelector(`.mk-row[data-id="${CSS.escape(it.key)}"]`);
      if (!el) continue;
      const r = it.row;
      el.className = mkRowClass(r, it.key === activeKey);
      el.setAttribute('aria-selected', String(it.key === activeKey));
      patch(el.querySelector('.mk-head .pv-text'), mkTextHtml(r));
      const st = installedOf(r) ? statusOf(installedOf(r)) : '';
      el.querySelector('.mk-head > .mk-tile, .mk-head > .pv-mark')?.classList.toggle('is-on', st === 'live');
      patch(el.querySelector('.pv-side'), mkSideHtml(r));
      patch(el.querySelector('.mk-panel'), mkPanelHtml(r));
    }
  });
  $('mk-q')?.setAttribute('aria-activedescendant', activeKey ? `mk-opt-${activeKey}` : '');
  if (scrollActive && activeKey) list.querySelector(`#mk-opt-${CSS.escape(activeKey)}`)?.scrollIntoView({ block: 'nearest' });
}

function svMatches(s) {
  const words = svq.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [s.name, s.label, s.desc, s.endpoint, s.source_label, s.scope].filter(Boolean).join(' ').toLowerCase();
  return words.every((w) => hay.includes(w));
}

const visibleServers = () => sortedServers().filter(svMatches);

function listSig() {
  return visibleServers().map((s) => s.name).join(',');
}

/** The MCP master switch on top of Your servers. */
function powerHtml(all) {
  const on = mcpOn();
  const live = all.filter((s) => statusOf(s) === 'live').length;
  const off = all.filter((s) => s.enabled === false).length;
  const sub = !on
    ? 'Off — no server connects and Jarvis has no MCP tools.'
    : [live ? `${live} connected` : 'Jarvis uses the tools of every server that’s on', off ? `${off} turned off` : ''].filter(Boolean).join(' · ');
  return `<div class="mcp-power${on ? ' is-on' : ' is-off'}${powerBusy ? ' is-busy' : ''}">
    <span class="mcp-power-ic" aria-hidden="true">${icon('power')}</span>
    <span class="mcp-power-text"><strong id="mcp-power-lbl">MCP servers</strong><span>${escapeHtml(sub)}</span></span>
    ${powerBusy ? `<span class="pv-side-busy">${spin('')}</span>` : ''}
    <label class="ex-switch is-lg" title="${on ? 'Turn MCP off — disconnects every server' : 'Turn MCP on — connects your servers again'}">
      <input type="checkbox" class="switch" role="switch" data-act="power" aria-labelledby="mcp-power-lbl"${on ? ' checked' : ''}${powerBusy ? ' disabled aria-busy="true"' : ''}>
    </label>
  </div>`;
}

function renderList() {
  const list = $('mcp-list');
  if (!list || !data) return;
  const all = data.servers || [];
  const servers = visibleServers();
  const live = all.filter((s) => statusOf(s) === 'live').length;
  list.classList.toggle('is-mcp-off', !mcpOn());
  patch($('sv-head'), all.length
    ? `${powerHtml(all)}${section('Your servers', { count: svq ? `${servers.length} of ${all.length}` : all.length, right: live ? `<span class="mk-state is-live"><i class="ex-dot" aria-hidden="true"></i>${live} connected</span>` : '' })}
      ${data.global_mcp ? '' : '<p class="pv-hint ex-scope-hint">Servers added globally are hidden — choose <strong>Project + global</strong> below to use them too.</p>'}`
    : '');
  keepFocus(list, () => {
    if (!all.length) {
      list.dataset.sig = '';
      patch(list, empty('No servers yet', 'MCP servers give Jarvis new tools — Slack, Notion, Linear, GitHub, a browser, your own.',
        { ic: 'plug', action: btn('tab', 'Browse the marketplace', { cls: 'btn-primary', ic: 'sparkles', data: { val: 'market' } }) }));
      return;
    }
    if (!servers.length) {
      list.dataset.sig = '';
      patch(list, empty(`No server matches “${svq.trim()}”`, 'Try another word, or find it in the marketplace.',
        { ic: 'search', action: btn('tab', 'Search the marketplace', { ic: 'sparkles', data: { val: 'market', q: svq.trim() } }) }));
      return;
    }
    if (list.dataset.sig !== listSig()) {
      const firstPaint = !list.dataset.sig && !seen.size;
      list.innerHTML = servers.map((s, i) => rowHtml(s, i, !firstPaint && !seen.has(s.name))).join('');
      list._html = null;
      list.dataset.sig = listSig();
    } else {
      for (const s of servers) {
        const el = list.querySelector(`.pv-row[data-name="${CSS.escape(s.name)}"]`);
        if (!el) continue;
        el.className = rowClass(s);
        const head = el.querySelector('.pv-head');
        head.setAttribute('aria-expanded', String(openName === s.name));
        patch(head, headHtml(s));
        patch(el.querySelector('.pv-side'), sideHtml(s));
        patch(el.querySelector('.pv-panel'), panelHtml(s));
      }
    }
  });
  all.forEach((s) => seen.add(s.name));
}

function renderView() {
  const el = $('mk-view-body');
  if (!el) return;
  let html = '';
  if (view.kind === 'setup') {
    const r = catalogRow(view.id);
    html = r ? setupViewHtml(r) : empty('That server is gone', 'It’s no longer in the marketplace.', { ic: 'circle-alert' });
  } else if (view.kind === 'app') {
    const s = serverByName(view.name);
    html = s ? appViewHtml(s) : empty('That server is gone', 'It was removed while this was open.', { ic: 'circle-alert' });
  }
  keepFocus(el, () => patch(el, html));
}

function renderHeader() {
  if (!data) return;
  const servers = data.servers || [];
  const live = servers.filter((s) => statusOf(s) === 'live').length;
  const need = servers.filter((s) => ['auth', 'key'].includes(statusOf(s))).length;
  setHomeSub('mcp', !servers.length
    ? `${catalogRows().length} servers to connect in a click`
    : !mcpOn()
      ? `MCP is off · ${plural(servers.length, 'server')} kept`
      : `${live} connected${need ? ` · ${need} need${need === 1 ? 's' : ''} you` : ''} · ${catalogRows().length} in the marketplace`);
}

function render() {
  const body = $('mcp-body');
  if (!body) return;
  if (!data) {
    body.dataset.mode = '';
    body.innerHTML = loadError
      ? empty('Could not load MCP servers', 'Check that Jarvis is still running, then try again.', { ic: 'circle-alert', action: btn('reload', 'Try again', { ic: 'refresh-cw' }) })
      : `<div class="list-loading">${'<div class="skeleton"></div>'.repeat(5)}</div>`;
    return;
  }
  renderHeader();
  renderBar();
  ensureBody(body, bodyMode());
  if (view?.kind === 'custom') renderAddPanel();
  else if (view) renderView();
  else if (currentTab() === 'market') renderMarket();
  else renderList();
  renderFoot();
}

// ─── Banner above the composer + sidebar dot ──────────────────────────────

function renderBanner() {
  const slot = $('mcp-chip');
  const dot = $('mcp-dot');
  const need = needsSignIn();
  for (const n of [...dismissed]) if (!need.some((s) => s.name === n)) dismissed.delete(n);
  if (dot) dot.hidden = need.length === 0;
  if (!slot) return;
  const shown = need.filter((s) => !dismissed.has(s.name));
  const chips = shown.slice(0, 2).map((s) => {
    const a = auth[s.name];
    const waiting = a && !a.starting;
    const text = a?.starting
      ? 'Getting the sign-in link…'
      : a?.status === 'working' ? 'Signing in…' : waiting ? 'Waiting for you to approve in the browser' : 'needs sign-in';
    const action = a?.starting
      ? `<span class="spinner" aria-hidden="true"></span>`
      : `<button type="button" class="act-btn is-cta" data-act="auth" data-name="${escapeHtml(s.name)}">${icon('log-in')}<span>${waiting ? 'Open again' : 'Authenticate'}</span></button>`;
    return `<div class="dock-chip is-auth${waiting ? ' is-waiting-auth' : ''}" data-name="${escapeHtml(s.name)}">
      ${icon('lock')}
      <span class="dock-chip-text">${waiting || a?.starting ? '' : `<strong>${escapeHtml(s.name)}</strong> `}${escapeHtml(text)}</span>
      ${action}
      <button type="button" class="chip-x" data-act="dismiss" data-name="${escapeHtml(s.name)}" aria-label="Hide the sign-in reminder for ${escapeHtml(s.name)}" title="Hide">${icon('x')}</button>
    </div>`;
  });
  if (shown.length > 2) {
    chips.push(`<button type="button" class="dock-chip is-auth is-more" data-act="more">${icon('plug')}<span class="dock-chip-text">+${shown.length - 2} more need sign-in</span><span class="act-btn">Review</span></button>`);
  }
  patch(slot, chips.join(''));
}

function setData(next) {
  data = next;
  loadError = false;
  reconcileAuth();
  renderBanner();
  for (const fn of listeners) fn(data);
  if (isModalOpen('mcp')) render();
}

/** Every open sign-in whose server is now live: say so and let go of it. */
function reconcileAuth() {
  for (const name of Object.keys(auth)) {
    const s = serverByName(name);
    if (!s) {
      delete auth[name];
    } else if (s.health?.connected) {
      signedIn(name, s.tool_count);
    }
  }
}

function signedIn(name, tools) {
  const a = auth[name];
  delete auth[name];
  if (a?.win && !a.win.closed) {
    try { a.win.close(); } catch { /* not ours to close */ }
  }
  if (openName === name) openName = null;
  showToast(`Connected ${name}${tools ? ` · ${plural(tools, 'tool')}` : ''}`);
  haptic(10);
  syncPolling();
}

export async function refreshMcp() {
  try {
    setData(await fetchMcpServers());
  } catch {
    if (!data) {
      loadError = true;
      if (isModalOpen('mcp')) render();
    }
  }
}
const refreshSoon = debounce(refreshMcp, 120);

/** The `mcp` bridge event: a server connected / failed / needs its sign-in. */
export function handleMcpEvent(evt = {}) {
  const { event, name } = evt;
  if (event === 'auth_required' && !isModalOpen('mcp') && !auth[name]) {
    showToast(`${name} needs you to sign in`);
    haptic(20);
  }
  refreshSoon();
}

function applyResult(res) {
  if (res?.mcp) setData(res.mcp);
  if (res && 'global_mcp' in res && res.global_mcp !== store.session.global_mcp) loadSnapshot({ global_mcp: res.global_mcp });
}

// ─── Sign-in ──────────────────────────────────────────────────────────────

/** The Authenticate button (card, add result and banner all end up here). Call it straight from a click. */
export async function authenticate(name, preWin = null) {
  const known = auth[name];
  if (known?.url) {
    // Already have the link: just open it again (still inside the click).
    let w = null;
    if (preWin && !preWin.closed) {
      try { preWin.location.href = known.url; w = preWin; } catch { /* fall back to a new tab */ }
    }
    if (!w) w = window.open(known.url, '_blank');
    known.opened = !!w;
    if (w) { try { w.opener = null; } catch { /* cross-origin */ } }
    renderAll();
    return;
  }
  if (known?.starting) {
    closeWin(preWin);
    return;
  }
  let win = preWin && !preWin.closed ? preWin : null;
  if (!win) {
    try { win = window.open('about:blank', '_blank'); } catch { /* blocked */ }
  }
  auth[name] = { starting: true, win, opened: !!win, startedAt: Date.now() };
  delete notes[name];
  // From a card: keep that card open. From the marketplace the row itself shows the progress.
  if (isModalOpen('mcp') && currentTab() === 'servers') openName = name;
  renderAll();

  const res = await extPost('mcp/auth/start', { name });
  applyResult(res);
  const a = auth[name];
  if (!a) {
    if (win && !win.closed) win.close();
    return;
  }
  if (res.connected || res.status === 'done') {
    signedIn(name, res.mcp?.servers?.find((s) => s.name === name)?.tool_count);
    if (isModalOpen('mcp')) render();
    return;
  }
  if (!res.ok || !res.url) {
    if (win && !win.closed) win.close();
    delete auth[name];
    setNote(name, res.error || 'Could not start the sign-in.', true);
    showToast(res.error || 'Could not start the sign-in', true);
    renderAll();
    return;
  }
  Object.assign(a, {
    starting: false,
    url: res.url,
    status: res.status,
    redirect: res.redirect_uri,
    expiresAt: Date.now() + (Number(res.expires_in) || 600) * 1000,
  });
  a.opened = false;
  if (win && !win.closed) {
    try {
      win.location.href = res.url;
      a.opened = true;
    } catch { /* fall back to the link */ }
  }
  renderAll();
  syncPolling();
}

function syncPolling() {
  const waiting = Object.values(auth).some((a) => a.url && !a.finishing);
  if (waiting && !pollTimer) pollTimer = setInterval(pollAuth, 1500);
  if (!waiting && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = 0;
  }
}

async function pollAuth() {
  for (const [name, a] of Object.entries(auth)) {
    if (!a.url || a.finishing) continue;
    let st;
    try {
      st = await fetchMcpAuth(name);
    } catch {
      continue;
    }
    if (auth[name] !== a) continue;
    if (st.connected || st.status === 'done') {
      await refreshMcp();
      if (auth[name]) signedIn(name, st.tool_count);
      render();
    } else if (st.status === 'working') {
      if (a.status !== 'working') {
        a.status = 'working';
        renderAll();
      }
    } else if (st.status === 'error' || st.status === 'cancelled' || (st.status === 'none' && Date.now() - a.startedAt > 4000)) {
      delete auth[name];
      const text = st.status === 'none' ? 'The sign-in link expired. Start again.' : st.message || 'Sign-in didn’t finish.';
      setNote(name, text, true);
      showToast(text, true);
      await refreshMcp();
      renderAll();
    }
  }
  syncPolling();
}

async function finishAuth(name) {
  const a = auth[name];
  const id = `mcp-paste-${name}`;
  const address = (drafts[id] || '').trim();
  if (!a?.url || a.finishing) return;
  if (!address) {
    setNote(name, 'Paste the address of the last sign-in page first.', true);
    renderAll();
    $(id)?.focus();
    return;
  }
  a.finishing = true;
  setNote(name, '');
  renderAll();
  const res = await extPost('mcp/auth/paste', { name, address });
  a.finishing = false;
  if (!res.ok) {
    setNote(name, res.error || 'That didn’t work. Copy the whole address bar.', true);
    if (res.expired) delete auth[name];
    renderAll();
    return;
  }
  delete drafts[id];
  a.status = 'working';
  renderAll();
  pollAuth();
}

async function cancelAuth(name) {
  const a = auth[name];
  delete auth[name];
  if (a?.win && !a.win.closed) {
    try { a.win.close(); } catch { /* not ours */ }
  }
  syncPolling();
  renderAll();
  const res = await extPost('mcp/auth/cancel', { name });
  applyResult(res);
  renderAll();
}

// ─── Actions ──────────────────────────────────────────────────────────────

function setNote(name, text, error = false) {
  notes[name] = text ? { text, error } : null;
}

function renderAll() {
  renderBanner();
  if (isModalOpen('mcp')) {
    render();
  }
}

function closeWin(win) {
  if (win && !win.closed) {
    try { win.close(); } catch { /* not ours */ }
  }
}

async function run(name, kind, path, payload, { onOk, okText } = {}) {
  busy[name] = kind;
  setNote(name, '');
  renderAll();
  const res = await extPost(path, payload);
  delete busy[name];
  applyResult(res);
  if (res.ok) {
    confirmName = null;
    haptic(10);
    onOk?.(res);
    if (okText) showToast(typeof okText === 'function' ? okText(res) : okText);
  } else {
    setNote(name, res.error || 'That did not work', true);
    if (openName !== name) openName = name;
  }
  renderAll();
  return res;
}

async function connect(name) {
  const res = await run(name, 'connect', 'mcp/connect', { name });
  if (!res.ok) return;
  if (res.status === 'connected') {
    showToast(`Connected ${name} · ${plural(res.tool_count || 0, 'tool')}`);
    if (openName === name) openName = null;
  } else if (res.status === 'auth_required') {
    openName = name;
  } else if (res.status === 'needs_credentials') {
    openName = name;
  } else if (res.status === 'failed') {
    setNote(name, res.error || 'Could not connect.', true);
    openName = name;
  }
  renderAll();
}

/** A server's switch. Off disconnects it; on connects it (quietly — a sign-in shows its button). */
async function setEnabled(name, on) {
  if (busy[name]) return;
  const res = await run(name, 'enable', 'mcp/enable', { name, enabled: on }, { okText: (r) => r.message || `${name} ${on ? 'on' : 'off'}` });
  if (!res.ok) return;
  if (['auth_required', 'needs_credentials', 'failed'].includes(res.status)) openName = name;
  else if (openName === name && !on) openName = null;
  if (res.status === 'failed') setNote(name, res.error || 'Turned on, but it couldn’t connect.', true);
  renderAll();
}

/** MCP as a whole. */
async function setPower(on) {
  if (powerBusy) return;
  powerBusy = true;
  renderAll();
  const res = await extPost('mcp/power', { enabled: on });
  powerBusy = false;
  applyResult(res);
  if (res.ok) {
    haptic(10);
    showToast(res.message || (on ? 'MCP is on' : 'MCP is off'));
  } else {
    showToast(res.error || 'Could not switch MCP', true);
  }
  renderAll();
}

async function saveKeys(name) {
  const s = serverByName(name);
  const values = {};
  for (const v of s?.needs_credentials || []) {
    const val = (drafts[`mcp-key-${name}-${v}`] || '').trim();
    if (val) values[v] = val;
  }
  if (!Object.keys(values).length) {
    setNote(name, 'Paste the key first.', true);
    renderAll();
    return;
  }
  const res = await run(name, 'keys', 'mcp/credentials', { name, values });
  if (!res.ok) return;
  for (const v of Object.keys(values)) {
    delete drafts[`mcp-key-${name}-${v}`];
    revealed.delete(`mcp-key-${name}-${v}`);
  }
  if (res.status === 'connected') {
    showToast(`Connected ${name} · ${plural(res.tool_count || 0, 'tool')}`);
    openName = null;
  } else if (res.status === 'failed') {
    setNote(name, res.error || 'Saved, but it still couldn’t connect.', true);
  }
  renderAll();
}

async function menuFor(name, anchor) {
  const s = serverByName(name);
  if (!s) return;
  const st = statusOf(s);
  const items = [];
  if (s.remote && (s.signed_in || st === 'auth')) items.push({ key: 'signout', label: 'Sign out', icon: 'log-out' });
  if (s.remote && s.removable && s.oauth !== false) items.push({ key: 'app', label: s.oauth_app ? 'Change OAuth app…' : 'Use my own OAuth app…', icon: 'app-window' });
  items.push({ key: 'copy', label: s.remote ? 'Copy address' : 'Copy command', icon: 'copy' });
  if (s.removable) {
    items.push(s.scope === 'project'
      ? { key: 'move-global', label: 'Move to global', icon: 'arrow-right-left' }
      : { key: 'move-project', label: 'Move to this project', icon: 'arrow-right-left' });
  }
  items.push({ divider: true });
  items.push({
    key: 'remove',
    label: 'Remove',
    icon: 'trash-2',
    danger: true,
    confirm: true,
    disabled: !s.removable,
    reason: s.removable ? '' : `Comes from ${s.source_label} — remove it there`,
  });
  const key = await openMenu(anchor, items);
  if (!key) return;
  if (key === 'copy') copyEndpoint(name);
  else if (key === 'app') openApp(name);
  else if (key === 'signout') await run(name, 'signout', 'mcp/signout', { name }, { okText: `Signed out of ${name}` });
  else if (key === 'remove') await run(name, 'remove', 'mcp/remove', { name, scope: s.scope }, { okText: `Removed ${name}` });
  else if (key === 'move-global') await run(name, 'move', 'mcp/move', { name, scope: 'global' }, { okText: `Moved ${name} to global` });
  else if (key === 'move-project') await run(name, 'move', 'mcp/move', { name, scope: 'project' }, { okText: `Moved ${name} to this project` });
}

async function saveApp(name) {
  const clientId = (drafts[`mcp-app-${name}-id`] || '').trim();
  const secret = (drafts[`mcp-app-${name}-secret`] || '').trim();
  if (!clientId) {
    setNote(name, 'Paste the Client ID first.', true);
    renderAll();
    $(`mcp-app-${name}-id`)?.focus();
    return;
  }
  // A sign-in follows: open its tab now, while this is still the click.
  let win = null;
  try { win = window.open('about:blank', '_blank'); } catch { /* blocked */ }
  const res = await run(name, 'app', 'mcp/app', { name, client_id: clientId, client_secret: secret });
  if (!res.ok) {
    closeWin(win);
    return;
  }
  leaveView({ focus: false });
  tab = 'servers';
  delete drafts[`mcp-app-${name}-id`];
  delete drafts[`mcp-app-${name}-secret`];
  if (res.status === 'connected') {
    closeWin(win);
    showToast(`Connected ${name} · ${plural(res.tool_count || 0, 'tool')}`);
    openName = null;
  } else if (res.status === 'auth_required') {
    openName = name;
    await authenticate(name, win);
  } else {
    closeWin(win);
    if (res.status === 'failed') setNote(name, res.error || 'Saved, but it still couldn’t connect.', true);
  }
  renderAll();
}

async function copyEndpoint(name) {
  const s = serverByName(name);
  if (!s) return;
  if (await copyText(s.endpoint || '')) showToast(s.remote ? 'Address copied' : 'Command copied');
  else showToast('Copy failed', true);
}

// ─── Sub-views ────────────────────────────────────────────────────────────

/** Open a sub-view: header back button + title, toolbar hidden, buttons in the footer. */
function enterView(next, { title, sub, focus = '' }) {
  view = next;
  setView('mcp', { title, sub, back: () => leaveView(), backLabel: 'MCP servers' });
  render();
  $('mcp-body').scrollTop = 0;
  requestAnimationFrame(() => {
    const el = (focus && $(focus)) || $('mcp-body')?.querySelector('.pv-input, textarea') || $('mcp-foot')?.querySelector('.btn-primary');
    el?.focus({ preventScroll: true });
  });
}

/** Back to the tabs (header back button, Esc, Cancel). */
function leaveView({ focus = true } = {}) {
  const was = view;
  view = null;
  setView('mcp', null);
  if (was?.kind === 'setup') delete mk.notes[was.id];
  if (!isModalOpen('mcp')) return;
  render();
  if (!focus) return;
  requestAnimationFrame(() => {
    if (was?.kind === 'setup') {
      const row = $('mk-list')?.querySelector(`.mk-row[data-id="${CSS.escape(was.id)}"]`);
      row?.scrollIntoView({ block: 'nearest' });
    }
    $(currentTab() === 'market' ? 'mk-q' : 'sv-q')?.focus({ preventScroll: true });
  });
}

function openSetup(id) {
  const r = catalogRow(id);
  if (!r) return;
  delete mk.notes[id];
  const sub = r.setup?.title && r.setup.title.length <= 64 ? r.setup.title : 'MCP servers';
  enterView({ kind: 'setup', id }, { title: `Connect ${r.label}`, sub });
}

function openCustom(text = '') {
  enterView({ kind: 'custom' }, { title: 'Custom server', sub: 'MCP servers', focus: 'mcp-src' });
  if (text) setSource(text);
}

function openApp(name) {
  const s = serverByName(name);
  if (!s) return;
  delete notes[name];
  enterView({ kind: 'app', name }, { title: 'Use your own OAuth app', sub: `Sign in to ${displayName(s)} through an app you registered`, focus: `mcp-app-${name}-id` });
}

// ─── Marketplace actions ──────────────────────────────────────────────────

function setTab(t, { focus = true, q = null } = {}) {
  tab = t;
  if (q !== null && t === 'market') {
    mk.q = q;
    mk.active = 0;
    const input = $('mk-q');
    if (input) input.value = q;
  }
  if (view) {
    view = null;
    setView('mcp', null);
  }
  render();
  $('mcp-body').scrollTop = 0;
  if (focus) setTimeout(() => $(t === 'market' ? 'mk-q' : 'sv-q')?.focus({ preventScroll: true }), 30);
}

function credentialsFor(r) {
  const values = {};
  const missing = [];
  for (const v of r.credentials || []) {
    const val = (drafts[`mk-f-${r.id}-${v}`] || '').trim();
    if (val) values[v] = val;
    else missing.push((r.fields?.[v] || {}).label || v);
  }
  return { values, missing };
}

/** The row's button (or Enter on it): connect now, or open its guided setup first. */
function mkPrimary(id) {
  const r = catalogRow(id);
  if (!r) return;
  const s = installedOf(r);
  if (s) {
    const st = statusOf(s);
    if (st === 'auth') authenticate(s.name);
    else if (st === 'idle' || st === 'warn') connect(s.name);
    else manage(id);
    return;
  }
  if (guided(r) && view?.kind !== 'setup') {
    openSetup(id);
    return;
  }
  connectMarket(id);
}

/** Add a marketplace server and take it as far as it goes: connected, or its sign-in page open. */
async function connectMarket(id) {
  const r = catalogRow(id);
  if (!r || mk.busy[id]) return;
  const inSetup = view?.kind === 'setup' && view.id === id;
  const { values, missing } = credentialsFor(r);
  if (missing.length) {
    if (!inSetup) {
      openSetup(id);
      return;
    }
    mk.notes[id] = { text: `Paste the ${missing.join(' and ')} first.`, error: true };
    render();
    const empty0 = [...($('mcp-body')?.querySelectorAll('.mk-fields .pv-input') || [])].find((x) => !x.value.trim());
    empty0?.focus();
    return;
  }
  // A hosted sign-in follows: open its tab now, inside the click (pop-up blockers allow only that).
  let win = null;
  if (r.auth === 'oauth' || r.auth === 'app') {
    try { win = window.open('about:blank', '_blank'); } catch { /* blocked: the row offers the link */ }
  }
  mk.busy[id] = true;
  delete mk.notes[id];
  render();
  const res = await extPost('mcp/add', { source: id, scope: add.scope, credentials: values, connect: true });
  delete mk.busy[id];
  applyResult(res);
  const out = (res.servers || [])[0] || {};
  const name = out.name || id;
  const done = () => {
    for (const v of r.credentials || []) delete drafts[`mk-f-${id}-${v}`];
    if (view?.kind === 'setup' && view.id === id) leaveView();
  };
  switch (out.status) {
    case 'connected':
      closeWin(win);
      done();
      showToast(`Connected ${r.label} · ${plural(out.tool_count || 0, 'tool')}`);
      haptic(10);
      break;
    case 'auth_required':
      done();
      haptic(10);
      await authenticate(name, win);
      break;
    case 'needs_credentials':
      closeWin(win);
      mk.notes[id] = { text: `${r.label} was added, but it still needs ${(out.missing || []).join(', ')}.`, error: true };
      break;
    case 'added':
      closeWin(win);
      done();
      mk.notes[id] = { text: `${r.label} added — choose “Project + global” in Your servers to use it.`, error: false };
      break;
    case 'exists':
    case 'denied':
      closeWin(win);
      done();
      mk.notes[id] = { text: out.message || `${r.label} is already set up.`, error: false };
      break;
    default:
      closeWin(win);
      // In the setup view the error stays next to the fields; from the list, under the row.
      mk.notes[id] = { text: out.error || res.error || `Couldn’t add ${r.label}.`, error: true };
  }
  renderAll();
}

/** Show a marketplace server's card in Your servers (sign-in from another device, keys, errors). */
function manage(id) {
  const r = catalogRow(id);
  const name = r?.installed || id;
  openName = name;
  setTab('servers', { focus: false });
  setTimeout(() => {
    const row = $('mcp-list')?.querySelector(`.pv-row[data-name="${CSS.escape(name)}"]`);
    row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, 160);
}

function moveActive(delta) {
  const picks = pickable(marketItems());
  if (!picks.length) return;
  mk.active = (mk.active + delta + picks.length) % picks.length;
  renderMarket({ scrollActive: true });
}

function activateActive() {
  const it = pickable(marketItems())[mk.active];
  if (!it) return;
  if (it.kind === 'source') openCustom(mk.q.trim());
  else if (it.kind === 'row') mkPrimary(it.key);
}

// ─── Add ──────────────────────────────────────────────────────────────────

function setSource(text) {
  add.text = text;
  add.result = null;
  add.wantAdd = false;
  const ta = $('mcp-src');
  if (ta && ta.value !== text) {
    ta.value = text;
    autosize(ta);
  }
  add.preview = null;
  renderPreview();
  schedulePreview();
}

const schedulePreview = debounce(() => runPreview(), 350);

async function runPreview() {
  const text = add.text.trim();
  const seq = ++previewSeq;
  if (!text) {
    add.preview = null;
    renderPreview();
    return;
  }
  add.preview = { loading: true };
  renderPreview();
  const res = await extPost('mcp/parse', { source: text });
  if (seq !== previewSeq) return;
  if (res.ok) {
    add.preview = { servers: res.servers || [] };
    const hint = res.servers?.[0]?.scope_hint;
    if (hint && !add.scopeTouched && (hint === 'project' || hint === 'global')) add.scope = hint;
  } else {
    add.preview = { error: res.error || 'I couldn’t read that.' };
  }
  renderAddPanel();
  if (add.wantAdd && add.preview.servers?.length) addServer();
  add.wantAdd = false;
}

async function addServer() {
  if (add.adding) return;
  if (!add.preview || add.preview.loading) {
    add.wantAdd = true;
    return;
  }
  if (!add.preview.servers?.length) return;
  add.adding = true;
  add.result = null;
  const credentials = {};
  for (const sv of add.preview.servers) {
    for (const v of sv.credentials || []) {
      const val = (drafts[`mcp-cred-${v}`] || '').trim();
      if (val) credentials[v] = val;
    }
  }
  renderAddPanel();
  const res = await extPost('mcp/add', { source: add.text.trim(), scope: add.scope, credentials, connect: true });
  add.adding = false;
  applyResult(res);
  add.result = res.servers?.length ? { servers: res.servers } : { error: res.error || 'Nothing was added.' };
  const added = (res.servers || []).some((r) => ['connected', 'auth_required', 'added', 'needs_credentials'].includes(r.status));
  if (added) {
    haptic(10);
    for (const sv of add.preview?.servers || []) for (const v of sv.credentials || []) delete drafts[`mcp-cred-${v}`];
    add.text = '';
    add.preview = null;
    const ta = $('mcp-src');
    if (ta) {
      ta.value = '';
      autosize(ta);
    }
    const first = res.servers.find((r) => ['auth_required', 'needs_credentials', 'failed'].includes(r.status));
    if (first) openName = first.name;
    const ok = res.servers.filter((r) => r.status === 'connected');
    if (ok.length) showToast(`Connected ${ok.map((r) => r.name).join(', ')}`);
  }
  renderAll();
  refreshMcp();
}

// ─── Events ───────────────────────────────────────────────────────────────

function toggleRow(name) {
  confirmName = null;
  openName = openName === name ? null : name;
  render();
  if (openName === name) {
    setTimeout(() => {
      const row = $('mcp-list')?.querySelector(`.pv-row[data-name="${CSS.escape(name)}"]`);
      row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }, 120);
  }
}

function focusKey(name) {
  openName = name;
  render();
  setTimeout(() => {
    const row = $('mcp-list')?.querySelector(`.pv-row[data-name="${CSS.escape(name)}"]`);
    row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    if (window.matchMedia('(min-width: 561px)').matches) row?.querySelector('.pv-input')?.focus({ preventScroll: true });
  }, 140);
}

async function pasteInto(inputId) {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (!text) return;
    drafts[inputId] = text;
    const el = $(inputId);
    if (el) el.value = text;
  } catch {
    showToast('Paste with your keyboard instead', true);
  }
}

function handleClick(e) {
  const el = e.target.closest('[data-act]');
  const root = $('mcp');
  if (!el || !root?.contains(el)) return;
  const act = el.dataset.act;
  const name = el.dataset.name || el.closest('.pv-row')?.dataset.name || '';
  switch (act) {
    case 'toggle': toggleRow(name); break;
    case 'auth': authenticate(name); break;
    case 'opened':
      if (auth[name]) {
        auth[name].opened = true;
        setTimeout(renderAll, 0);
      }
      break;
    case 'finish-auth': finishAuth(name); break;
    case 'cancel-auth': cancelAuth(name); break;
    case 'connect': connect(name); break;
    case 'enable': setEnabled(name, el.checked); break;
    case 'enable-on': setEnabled(name, true); break;
    case 'power': setPower(el.checked); break;
    case 'power-on': setPower(true); break;
    case 'disconnect': run(name, 'disconnect', 'mcp/disconnect', { name }, { okText: `Disconnected ${name}` }); break;
    case 'open-key': focusKey(name); break;
    case 'save-keys': saveKeys(name); break;
    case 'menu': menuFor(name, el); break;
    case 'copy': copyEndpoint(name); break;
    case 'tools':
      if (toolsOpen.has(name)) toolsOpen.delete(name); else toolsOpen.add(name);
      render();
      break;
    case 'tab': setTab(el.dataset.val, { q: el.dataset.q ?? null }); break;
    case 'mk-cat':
      mk.cat = el.dataset.val || ALL;
      mk.active = 0;
      if (!view && currentTab() !== 'market') tab = 'market';
      render();
      $('mcp-body').scrollTop = 0;
      break;
    case 'mk-pick': {
      const id = el.dataset.id;
      const i = pickable(marketItems()).findIndex((it) => it.key === id);
      if (i >= 0) mk.active = i;
      mkPrimary(id);
      break;
    }
    case 'mk-connect': connectMarket(el.dataset.id); break;
    case 'mk-setup': openSetup(el.dataset.id); break;
    case 'mk-manage': manage(el.dataset.id); break;
    case 'mk-source': openCustom(mk.q.trim()); break;
    case 'mk-custom': openCustom(); break;
    case 'back': leaveView(); break;
    case 'custom-done':
      add.result = null;
      leaveView({ focus: false });
      setTab('servers');
      break;
    case 'open-app': openApp(name); break;
    case 'save-app': saveApp(el.dataset.name || name); break;
    case 'copy-text':
      copyText(el.dataset.text || '').then((ok) => showToast(ok ? 'Copied' : 'Copy failed', !ok));
      break;
    case 'scope':
      add.scope = el.dataset.val;
      add.scopeTouched = true;
      if (view?.kind === 'custom') renderAddPanel();
      else renderFoot();
      break;
    case 'global': {
      const on = el.dataset.val === 'true';
      if (on === !!data?.global_mcp) break;
      pickerAction('mcp_scope', { global_mcp: on }).then((res) => {
        if (res.state) loadSnapshot(res.state);
        if (!res.ok) showToast(res.error || 'Could not change the scope', true);
        refreshMcp();
      });
      break;
    }
    case 'add': addServer(); break;
    case 'dismiss': dismissed.add(name); renderBanner(); break;
    case 'more': closeMenu(); openMcp(); break;
    case 'reveal': {
      const id = el.dataset.for;
      if (revealed.has(id)) revealed.delete(id); else revealed.add(id);
      const input = $(id);
      if (input) input.type = revealed.has(id) ? 'text' : 'password';
      el.innerHTML = icon(revealed.has(id) ? 'eye-off' : 'eye');
      el.setAttribute('aria-label', `${revealed.has(id) ? 'Hide' : 'Show'} value`);
      input?.focus({ preventScroll: true });
      break;
    }
    case 'paste': pasteInto(el.dataset.for); break;
    case 'reload': refreshMcp(); break;
    default:
  }
}

function handleInput(e) {
  const el = e.target;
  if (el.id === 'mk-q') {
    mk.q = el.value;
    mk.active = 0;
    renderMarket();
    $('mcp-body').scrollTop = 0;
    return;
  }
  if (el.id === 'sv-q') {
    svq = el.value;
    renderList();
    return;
  }
  if (el.id === 'mcp-src') {
    add.text = el.value;
    add.result = null;
    add.wantAdd = false;
    add.preview = null;
    autosize(el);
    if (!add.text.trim()) {
      previewSeq += 1;
      renderPreview();
    } else {
      renderPreview();
      schedulePreview();
    }
    return;
  }
  if (el.classList?.contains('pv-input')) drafts[el.id] = el.value;
}

function handleKey(e) {
  const el = e.target;
  if (el.classList?.contains('mk-tab') && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
    // Tabs in visual order: ← Your servers · Marketplace →
    e.preventDefault();
    setTab(e.key === 'ArrowLeft' ? 'servers' : 'market', { focus: false });
    $('mcp-tabs')?.querySelector(`.mk-tab[data-val="${e.key === 'ArrowLeft' ? 'servers' : 'market'}"]`)?.focus();
    return;
  }
  if (el.id === 'mk-q') {
    if (e.isComposing) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      moveActive(e.key === 'ArrowDown' ? 1 : -1);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      activateActive();
    }
    return;
  }
  // Your servers: ↑ ↓ between the search and the cards; Enter in the search opens the first one.
  const heads = () => [...($('mcp-list')?.querySelectorAll('.pv-row > .pv-head') || [])];
  if (el.id === 'sv-q' || (el.classList?.contains('pv-head') && $('mcp-list')?.contains(el))) {
    if (arrowRows(e, $('sv-q'), heads())) return;
    if (el.id === 'sv-q' && e.key === 'Enter' && !e.isComposing) {
      e.preventDefault();
      heads()[0]?.click();
      return;
    }
  }
  if (e.key !== 'Enter' || e.isComposing) return;
  if (el.id === 'mcp-src') {
    if (e.shiftKey) return;
    e.preventDefault();
    addServer();
    return;
  }
  if (!el.classList?.contains('pv-input')) return;
  e.preventDefault();
  const paste = /^mcp-paste-(.+)$/.exec(el.id);
  if (paste) finishAuth(paste[1]);
  const key = /^mcp-key-(.+?)-[^-]+$/.exec(el.id);
  if (key) saveKeys(key[1]);
  if (el.id.startsWith('mcp-cred-')) addServer();
  const app = /^mcp-app-(.+)-(id|secret)$/.exec(el.id);
  if (app) saveApp(app[1]);
  if (el.id.startsWith('mk-f-') && view?.kind === 'setup') {
    // Next empty field, else connect.
    const inputs = [...(el.closest('.mk-fields')?.querySelectorAll('.pv-input') || [])];
    const next = inputs.slice(inputs.indexOf(el) + 1).find((x) => !x.value.trim());
    if (next) next.focus();
    else connectMarket(view.id);
  }
}

function handleBannerClick(e) {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const name = el.dataset.name || '';
  if (el.dataset.act === 'auth') authenticate(name);
  else if (el.dataset.act === 'dismiss') {
    dismissed.add(name);
    renderBanner();
  } else if (el.dataset.act === 'more') openMcp();
}

// ─── Open / close ─────────────────────────────────────────────────────────

/** `focus`: a server name to open its card, `market` (or a search like `market slack`) for the marketplace. */
export function openMcp(focus = '') {
  const m = /^(?:market(?:place)?|browse|add)\b\s*(.*)$/i.exec(String(focus || '').trim());
  view = null;
  if (m) {
    tab = 'market';
    if (m[1]) {
      mk.q = m[1];
      mk.active = 0;
    }
    focus = '';
  } else if (focus && (!data || serverByName(focus))) {
    openName = focus;
    tab = 'servers';
  } else if (focus) {
    // Not one of yours: look for it in the marketplace.
    tab = 'market';
    mk.q = focus;
    mk.active = 0;
    focus = '';
  }
  render();
  if ($('mk-q') && $('mk-q').value !== mk.q) {
    $('mk-q').value = mk.q;
    renderMarket();
  }
  // Phones: no keyboard popping up over the list until the user taps the search.
  const wide = window.matchMedia('(min-width: 561px)').matches;
  openModal('mcp', {
    focus: (wide ? $(currentTab() === 'market' ? 'mk-q' : 'sv-q') : null) || undefined,
    onClose: () => {
      closeMenu();
      confirmName = null;
      for (const n of Object.keys(notes)) delete notes[n];
      // A finished result / unsent text stay for the next visit; an open sign-in keeps its card.
      if (!Object.keys(auth).some((n) => openName === n)) openName = null;
      for (const id of Object.keys(mk.notes)) delete mk.notes[id];
      view = null; // modal.js already reset the header
    },
  });
  refreshMcp().then(() => {
    if (isModalOpen('mcp')) {
      if (!openName && currentTab() === 'servers') {
        const first = needsSignIn()[0] || sortedServers().find((s) => statusOf(s) === 'key');
        if (first) openName = first.name;
      }
      render();
      const q = $(currentTab() === 'market' ? 'mk-q' : 'sv-q');
      if (!view && q && document.activeElement !== q && !$('mcp')?.contains(document.activeElement)
        && window.matchMedia('(min-width: 561px)').matches) q.focus({ preventScroll: true });
    }
  });
}

export function closeMcp() {
  closeModal('mcp');
}

export function initMcp() {
  const root = $('mcp');
  root?.addEventListener('click', handleClick);
  root?.addEventListener('input', handleInput);
  root?.addEventListener('keydown', handleKey);
  $('mcp-chip')?.addEventListener('click', handleBannerClick);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      if (pollTimer) pollAuth();
      refreshSoon();
    }
  });
  refreshMcp();
}

