/** MCP servers — the web /mcp: a searchable marketplace, sign in, connect, remove.
 *
 * Two tabs — *Your servers* first (shown when you have any), then the
 * *Marketplace* (shown when you don't). **Marketplace**: a search field over every well-known server
 * (Slack, Notion, Linear, GitHub…) with category chips and one row per server —
 * brand tile, what it does, how it signs in (Sign in · No account · API key ·
 * Your app · Desktop app · Runs locally) and one button. Hosted ones connect
 * and open their sign-in page straight from that click; key / app / desktop
 * ones first unfold a short guided setup (steps, a link that opens the
 * vendor's page — for Slack already filled in —, the fields it needs).
 * Anything else — a link, `npx …`, a `claude mcp add …` line, JSON, a GitHub
 * repo — goes through *Custom server* (Jarvis previews what it found).
 * **Your servers**: one card per server with its status and the one thing to
 * do next — Authenticate, Enter key, Connect, Retry, Disconnect, or use your
 * own OAuth app for a host that only lets approved apps sign in.
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

const TONE = { live: 'accent', auth: 'amber', key: 'amber', warn: 'amber', failed: 'chili', connecting: 'slate', idle: 'slate' };
const RANK = { auth: 0, key: 1, failed: 2, warn: 3, connecting: 4, live: 5, idle: 6 };
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
const appOpen = new Set(); // cards showing the "your own OAuth app" form
const dismissed = new Set(); // banner closed for a server (until it stops needing sign-in)
const seen = new Set(); // server names already listed once (only new ones animate in)
let pollTimer = 0;
let previewSeq = 0;
let built = false;
let tab = ''; // 'market' | 'servers' ('' = pick for the user)

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
  open: '', // catalog id whose setup / result is unfolded
  active: 0, // keyboard row in the list
  custom: false, // the "Custom server" panel instead of the list
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

function statusOf(s) {
  const h = s.health || {};
  if (h.connected) return 'live';
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
    default: return 'Off';
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

function sideHtml(s) {
  const st = statusOf(s);
  const name = s.name;
  const b = busy[name];
  let primary = '';
  if (b && b !== 'connect') primary = `<span class="pv-side-busy">${spin('')}</span>`;
  else if (st === 'auth') {
    const a = auth[name];
    if (a?.starting) primary = rowBtn('auth', 'Opening', { busy: true, data: { name } });
    else if (a?.url) primary = rowBtn('auth', 'Open again', { data: { name } });
    else primary = rowBtn('auth', 'Authenticate', { cls: 'is-primary', ic: 'log-in', data: { name } });
  } else if (st === 'key') primary = rowBtn('open-key', 'Enter key', { cls: 'is-primary', ic: 'key-round', data: { name } });
  else if (st === 'connecting') primary = `<span class="pv-side-busy">${spin('')}</span>`;
  else if (st === 'live') primary = rowBtn('disconnect', 'Disconnect', { data: { name } });
  else if (st === 'failed') primary = rowBtn('connect', 'Retry', { ic: 'refresh-cw', data: { name } });
  else primary = rowBtn('connect', 'Connect', { cls: 'is-go', data: { name } });
  return `${primary}${moreBtn(name)}`;
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
function appPanel(s) {
  const idKey = `mcp-app-${s.name}-id`;
  const secKey = `mcp-app-${s.name}-secret`;
  const redirect = data?.oauth_redirect_url || 'http://localhost:33418/callback';
  return `<div class="mk-setup is-card">
    <p class="mk-why"><strong>Use your own OAuth app</strong><span>Register an app with ${escapeHtml(displayName(s))}'s developer settings, add the redirect URL below, then paste its Client ID (and secret, if it has one).</span></p>
    <div class="mk-redirect"><span>Redirect URL</span><code>${escapeHtml(redirect)}</code>
      <button type="button" class="ex-copy" data-act="copy-text" data-text="${escapeHtml(redirect)}" aria-label="Copy the redirect URL" title="Copy">${icon('copy')}</button></div>
    <label class="ex-lbl is-friendly" for="${idKey}">Client ID</label>
    ${field(idKey, { value: drafts[idKey] || '', placeholder: 'Paste the Client ID', label: 'Client ID' })}
    <label class="ex-lbl is-friendly" for="${secKey}">Client Secret <em>optional</em></label>
    ${field(secKey, { value: drafts[secKey] || '', placeholder: 'Paste the Client Secret', label: 'Client Secret', secret: true, shown: revealed.has(secKey) })}
    <div class="pv-actions">
      ${btn('save-app', 'Save and sign in', { cls: 'btn-primary', ic: 'log-in', data: { name: s.name }, busy: busy[s.name] === 'app' })}
      ${btn('close-app', 'Cancel', { cls: 'btn-quiet', data: { name: s.name } })}
    </div>
  </div>`;
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
  if (appOpen.has(s.name)) return `${line}${appPanel(s)}`;
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

function panelHtml(s) {
  const st = statusOf(s);
  let mid = '';
  if (appOpen.has(s.name) && st !== 'failed' && st !== 'live') mid = appPanel(s);
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
  return `pv-row ex-row is-${st}${openName === s.name ? ' is-open' : ''}${st === 'live' ? ' is-connected' : ''}`;
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
  if (add.result) {
    return `${resultHtml(add.result)}<div class="pv-actions"><button type="button" class="btn btn-quiet btn-sm" data-act="dismiss-result">Done</button></div>`;
  }
  const p = add.preview;
  if (!add.text.trim()) {
    return '<p class="pv-hint ex-idle">Paste a hosted address (https://…/mcp), an install command (npx -y … / uvx …), a <code>claude mcp add …</code> line, a JSON config or a GitHub repo. Jarvis shows what it will run or connect to before adding anything.</p>';
  }
  if (!p || p.loading) return `<p class="pv-hint ex-loading">${spin('Reading that…')}</p>`;
  if (p.error) return msg(p.error, 'error');
  return p.servers.map(previewCard).join('');
}

function addBtnHtml() {
  const ok = add.preview?.servers?.length;
  const n = add.preview?.servers?.length || 0;
  const label = add.adding ? 'Adding…' : n > 1 ? `Add ${n} servers` : 'Add server';
  return `<button type="button" class="btn btn-primary ex-add-btn" data-act="add"${ok && !add.adding && !add.result ? '' : ' disabled'}>${add.adding ? spin(label) : `${icon('plus')}<span>${label}</span>`}</button>`;
}

// ─── Markup: marketplace ──────────────────────────────────────────────────

const catalogRows = () => data?.catalog || [];
const catalogRow = (id) => catalogRows().find((r) => r.id === id) || null;

/** Every word must match (same rule as jarvis/mcp/catalog.matches). */
function matches(r, q) {
  const words = String(q || '').toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = [r.id, r.label, r.desc, r.category, r.auth_label, AUTH[r.auth]?.label, r.keywords, hostOf(r.endpoint)]
    .filter(Boolean).join(' ').toLowerCase();
  return words.every((w) => hay.includes(w));
}

/** Rows the list shows now, in order: `{ kind: 'head' | 'row' | 'source' | 'custom', … }`. */
function marketItems() {
  const q = mk.q.trim();
  const source = looksLikeSource(q);
  const items = [];
  if (source) {
    items.push({ kind: 'source', key: 'source' });
    items.push({ kind: 'custom', key: 'custom' });
    return items;
  }
  let rows = catalogRows().filter((r) => matches(r, q));
  if (mk.cat === 'Popular') rows = rows.filter((r) => r.popular);
  else if (mk.cat !== ALL) rows = rows.filter((r) => r.category === mk.cat);
  if (q) {
    const ql = q.toLowerCase();
    const starts = (r) => (r.label.toLowerCase().startsWith(ql) || r.id.startsWith(ql) ? 0 : 1);
    rows = [...rows].sort((a, b) => starts(a) - starts(b)); // stable: popular order kept within
  }
  const grouped = !q && mk.cat === ALL;
  if (grouped) {
    const pop = rows.filter((r) => r.popular);
    const rest = rows.filter((r) => !r.popular).sort((a, b) => a.label.localeCompare(b.label));
    if (pop.length) items.push({ kind: 'head', key: 'h-pop', label: 'Popular', count: pop.length });
    pop.forEach((r) => items.push({ kind: 'row', key: r.id, row: r }));
    if (rest.length) items.push({ kind: 'head', key: 'h-all', label: 'More servers', count: rest.length });
    rest.forEach((r) => items.push({ kind: 'row', key: r.id, row: r }));
  } else {
    rows.forEach((r) => items.push({ kind: 'row', key: r.id, row: r }));
  }
  if (!rows.length) items.push({ kind: 'empty', key: 'empty' });
  items.push({ kind: 'custom', key: 'custom' });
  return items;
}

const pickable = (items) => items.filter((it) => it.kind !== 'head' && it.kind !== 'empty');

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
    if (st === 'key') return rowBtn('mk-manage', 'Enter key', { cls: 'is-primary', ic: 'key-round', data: { id } });
    if (st === 'failed') return rowBtn('mk-manage', 'Fix', { cls: 'is-warn', ic: 'circle-alert', data: { id } });
    return rowBtn('connect', 'Connect', { cls: 'is-go', data: { name: s.name } });
  }
  const guided = ['key', 'app', 'desktop'].includes(r.auth);
  if (guided) return rowBtn('mk-toggle', mk.open === id ? 'Close' : 'Set up', { cls: mk.open === id ? '' : 'is-go', data: { id } });
  return rowBtn('mk-connect', 'Connect', { cls: 'is-go', ic: r.auth === 'local' ? 'plug-zap' : r.auth === 'open' ? 'zap' : 'log-in', data: { id } });
}

/** The unfolded part of a row: guided setup, sign-in progress or what went wrong. */
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
    return note;
  }
  if (mk.open !== id) return note;
  return setupHtml(r) + note;
}

function setupHtml(r) {
  const id = r.id;
  const su = r.setup || {};
  const vars = r.credentials || [];
  const steps = (su.steps || []).map((t, i) => `<li><span class="pv-n">${i + 1}</span><span>${escapeHtml(t)}</span></li>`).join('');
  const link = su.link
    ? `<a class="btn ${vars.length || r.auth === 'desktop' ? 'btn-sm' : 'btn-primary'} mk-link" href="${escapeHtml(su.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(su.link_label || 'Open')}${icon('external-link')}</a>`
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
  const title = su.title || (vars.length ? `${r.label} needs ${vars.length > 1 ? 'a few details' : 'one thing'}` : `Connect ${r.label}`);
  const why = su.why || (vars.length ? 'Kept on the computer running Jarvis — never written into a config file, never shown to the model.' : '');
  return `<div class="mk-setup">
    <p class="mk-why"><strong>${escapeHtml(title)}</strong>${why ? `<span>${escapeHtml(why)}</span>` : ''}</p>
    ${steps ? `<ol class="mk-steps">${steps}</ol>` : ''}
    ${link || redirect ? `<div class="mk-setup-links">${link}${redirect}</div>` : ''}
    ${inputs ? `<div class="mk-fields">${inputs}</div>` : ''}
    ${su.note ? `<p class="pv-hint mk-note">${icon('info')}<span>${escapeHtml(su.note)}</span></p>` : ''}
    <div class="pv-actions">
      ${btn('mk-connect', 'Connect', { cls: 'btn-primary', ic: r.auth === 'app' ? 'log-in' : 'plug-zap', data: { id }, busy: !!mk.busy[id] })}
      ${btn('mk-toggle', 'Cancel', { cls: 'btn-quiet', data: { id } })}
    </div>
  </div>`;
}

function mkRowClass(r, active) {
  const s = installedOf(r);
  const st = s ? statusOf(s) : '';
  const panel = mkPanelHtml(r);
  return `pv-row mk-row${panel ? ' is-open' : ''}${active ? ' is-active' : ''}${st ? ` is-${st}` : ''}${st === 'live' ? ' is-connected' : ''}`;
}

function mkRowHtml(r, active, i) {
  const s = installedOf(r);
  const st = s ? statusOf(s) : '';
  const sub = s && st !== 'live' && st !== 'idle'
    ? `<span class="pv-sub ex-st is-${st}"><i class="ex-dot" aria-hidden="true"></i>${escapeHtml(statusText(s, st))}</span>`
    : `<span class="pv-sub mk-desc">${escapeHtml(r.desc || '')}</span>`;
  return `<div class="${mkRowClass(r, active)}" data-id="${escapeHtml(r.id)}" id="mk-opt-${escapeHtml(r.id)}" role="option" aria-selected="${active}" style="--i:${i}">
    <button type="button" class="pv-head mk-head" data-act="mk-pick" data-id="${escapeHtml(r.id)}" tabindex="-1">
      ${tile(r, { on: st === 'live' })}
      <span class="pv-text">
        <span class="pv-title mk-title"><span class="ex-name">${escapeHtml(r.label)}</span>${badgeHtml(r)}</span>
        ${sub}
      </span>
    </button>
    <div class="pv-side">${mkSideHtml(r)}</div>
    <div class="fold"><div class="fold-inner"><div class="pv-panel mk-panel">${mkPanelHtml(r)}</div></div></div>
  </div>`;
}

function mkItemHtml(it, active, i) {
  if (it.kind === 'head') return `<div class="mk-group" role="presentation"><span>${escapeHtml(it.label)}</span><em>${it.count}</em></div>`;
  if (it.kind === 'empty') {
    return `<div class="mk-empty" role="presentation">${icon('search')}<strong>No server matches “${escapeHtml(mk.q.trim())}”</strong>
      <span>Try another word${mk.cat !== ALL ? ' or <button type="button" class="link-btn" data-act="mk-cat" data-val="All">all categories</button>' : ''} — or add it yourself as a custom server.</span></div>`;
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
  if (it.kind === 'custom') {
    return `<div class="pv-row mk-row mk-custom-row${active ? ' is-active' : ''}" role="option" id="mk-opt-custom" aria-selected="${active}">
      <button type="button" class="pv-head mk-head" data-act="mk-custom" tabindex="-1">
        <span class="mk-tile is-plus" aria-hidden="true">${icon('link')}</span>
        <span class="pv-text"><span class="pv-title">Custom server</span><span class="pv-sub mk-desc">Paste a link, npx / uvx command, claude mcp add line, JSON or GitHub repo</span></span>
      </button>
      <div class="pv-side"><span class="mk-chev">${icon('chevron-right')}</span></div>
    </div>`;
  }
  return mkRowHtml(it.row, active, i);
}

function catsHtml() {
  const cats = [ALL, ...(data?.categories || [])];
  const count = (c) => {
    const rows = catalogRows();
    if (c === ALL) return rows.length;
    if (c === 'Popular') return rows.filter((r) => r.popular).length;
    return rows.filter((r) => r.category === c).length;
  };
  return cats.filter((c) => count(c) > 0).map((c) => `<button type="button" class="mk-cat" data-act="mk-cat" data-val="${escapeHtml(c)}" aria-pressed="${c === mk.cat}">${escapeHtml(CAT_LABEL[c] || c)}</button>`).join('');
}

function tabsHtml() {
  const servers = data?.servers || [];
  const need = servers.filter((s) => ['auth', 'key', 'failed'].includes(statusOf(s))).length;
  const t = currentTab();
  return `<div class="mk-tabs" role="tablist" aria-label="MCP">
    <button type="button" role="tab" class="mk-tab" data-act="tab" data-val="servers" aria-selected="${t === 'servers'}">${icon('plug')}<span>Your servers</span>${servers.length ? `<em class="mk-count${need ? ' is-attn' : ''}">${servers.length}</em>` : ''}</button>
    <button type="button" role="tab" class="mk-tab" data-act="tab" data-val="market" aria-selected="${t === 'market'}">${icon('sparkles')}<span>Marketplace</span></button>
  </div>`;
}

/** The tab shown: the user's pick, else Your servers when there are any, else the Marketplace. */
function currentTab() {
  if (tab) return tab;
  return (data?.servers || []).length ? 'servers' : 'market';
}

function mkFootHtml() {
  if (!data) return '';
  return `<span class="mk-foot-lbl">New servers go to</span>${seg(SCOPES, add.scope, { label: 'Where to add new servers', act: 'scope', group: 'add' })}
    <span class="mk-foot-path" title="${escapeHtml(add.scope === 'project' ? data.project_config_path || '' : data.global_config_path || '')}">${add.scope === 'project' ? 'only this folder' : 'every project'}</span>`;
}

// ─── Render ───────────────────────────────────────────────────────────────

function build(body) {
  body.innerHTML = `
    <div id="mcp-tabs"></div>
    <section class="mk" id="mk-sec" aria-label="MCP marketplace">
      <div class="mk-search" id="mk-search">
        <span class="mk-search-ic">${icon('search')}</span>
        <input id="mk-q" type="search" placeholder="Search servers — Slack, Notion, GitHub…" aria-label="Search MCP servers"
          role="combobox" aria-controls="mk-list" aria-expanded="true" aria-autocomplete="list"
          autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="go"
          data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other">
        <span class="mk-kbd" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd><kbd>↵</kbd></span>
      </div>
      <div class="mk-cats" id="mk-cats" role="group" aria-label="Categories"></div>
      <div class="mk-list" id="mk-list" role="listbox" aria-label="MCP servers"></div>
      <section class="ex-add mk-custom" id="mk-custom" aria-label="Custom server" hidden>
        <div class="ex-add-head"><button type="button" class="link-btn mk-back" data-act="mk-back">${icon('arrow-left')}<span>Marketplace</span></button><h3>Custom server</h3></div>
        <div class="ex-src">
          <span class="pv-field-ic">${icon('link')}</span>
          <textarea id="mcp-src" class="ex-src-input" rows="1" placeholder="Paste a link, npx …, claude mcp add …, JSON or a GitHub repo"
            aria-label="Server to add" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
            data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other"></textarea>
        </div>
        <div class="ex-add-row">
          <div class="ex-add-scope" id="mcp-add-scope"></div>
          <span id="mcp-add-btn"></span>
        </div>
        <p class="ex-path" id="mcp-path"></p>
        <div class="ex-preview" id="mcp-preview" aria-live="polite"></div>
      </section>
      <div class="mk-foot" id="mk-foot"></div>
    </section>
    <section class="ex-list-sec" id="sv-sec" aria-label="Your servers" hidden>
      <div class="ex-list-head"><h3 id="mcp-count">Your servers</h3><span id="mcp-scope-seg"></span></div>
      <p class="pv-hint ex-scope-hint" id="mcp-scope-hint"></p>
      <div class="ex-list" id="mcp-list"></div>
    </section>
    <p class="pv-foot">${icon('lock')}<span>Keys and sign-ins stay on the computer running Jarvis. This browser never keeps them.</span></p>`;
  const ta = $('mcp-src');
  ta.value = add.text;
  $('mk-q').value = mk.q;
  built = true;
}

function renderAddPanel() {
  patch($('mcp-add-scope'), seg(SCOPES, add.scope, { label: 'Where to add it', act: 'scope', group: 'add' }));
  patch($('mcp-add-btn'), addBtnHtml());
  patch($('mcp-path'), pathHint());
  renderPreview();
}

function renderPreview() {
  const el = $('mcp-preview');
  if (!el) return;
  const focused = document.activeElement;
  const focusId = focused?.classList?.contains('pv-input') && el.contains(focused) ? focused.id : '';
  const sel = focusId ? [focused.selectionStart, focused.selectionEnd] : null;
  patch(el, previewHtml());
  patch($('mcp-add-btn'), addBtnHtml());
  restoreFocus(focusId, sel);
}

function restoreFocus(id, sel) {
  if (!id || document.activeElement?.id === id) return;
  const el = $(id);
  if (!el) return;
  el.focus({ preventScroll: true });
  try { el.setSelectionRange(sel[0], sel[1]); } catch { /* not a text input */ }
}

function renderMarket({ scrollActive = false } = {}) {
  const list = $('mk-list');
  if (!list || !data) return;
  patch($('mk-cats'), catsHtml());
  patch($('mk-foot'), mkFootHtml());
  const custom = $('mk-custom');
  if (custom) custom.hidden = !mk.custom;
  list.hidden = mk.custom;
  $('mk-search').hidden = mk.custom;
  $('mk-cats').hidden = mk.custom;
  $('mk-foot').hidden = mk.custom; // the custom panel has its own Project / Global switch
  if (mk.custom) {
    renderAddPanel();
    return;
  }
  const focused = document.activeElement;
  const focusId = focused?.classList?.contains('pv-input') && list.contains(focused) ? focused.id : '';
  const sel = focusId ? [focused.selectionStart, focused.selectionEnd] : null;
  const items = marketItems();
  const picks = pickable(items);
  mk.active = Math.max(0, Math.min(mk.active, picks.length - 1));
  const activeKey = picks[mk.active]?.key;
  const sig = items.map((it) => it.key).join(',');
  if (list.dataset.sig !== sig) {
    list.innerHTML = items.map((it, i) => mkItemHtml(it, it.key === activeKey, i)).join('');
    list._html = null;
    list.dataset.sig = sig;
  } else {
    // Same rows: update each in place so an open setup keeps its typing and its fold animates.
    for (const it of items) {
      if (it.kind !== 'row') {
        const el = list.querySelector(`#mk-opt-${CSS.escape(it.key)}`);
        if (el) {
          el.classList.toggle('is-active', it.key === activeKey);
          el.setAttribute('aria-selected', String(it.key === activeKey));
        }
        continue;
      }
      const el = list.querySelector(`.mk-row[data-id="${CSS.escape(it.key)}"]`);
      if (!el) continue;
      const r = it.row;
      el.className = mkRowClass(r, it.key === activeKey);
      el.setAttribute('aria-selected', String(it.key === activeKey));
      const s = installedOf(r);
      const st = s ? statusOf(s) : '';
      const head = el.querySelector('.mk-head .pv-text');
      patch(head, `<span class="pv-title mk-title"><span class="ex-name">${escapeHtml(r.label)}</span>${badgeHtml(r)}</span>${s && st !== 'live' && st !== 'idle'
        ? `<span class="pv-sub ex-st is-${st}"><i class="ex-dot" aria-hidden="true"></i>${escapeHtml(statusText(s, st))}</span>`
        : `<span class="pv-sub mk-desc">${escapeHtml(r.desc || '')}</span>`}`);
      const t = el.querySelector('.mk-head > .mk-tile, .mk-head > .pv-mark');
      if (t) t.classList.toggle('is-on', st === 'live');
      patch(el.querySelector('.pv-side'), mkSideHtml(r));
      patch(el.querySelector('.mk-panel'), mkPanelHtml(r));
    }
  }
  const q = $('mk-q');
  if (q) q.setAttribute('aria-activedescendant', activeKey ? `mk-opt-${activeKey}` : '');
  if (scrollActive && activeKey) list.querySelector(`#mk-opt-${CSS.escape(activeKey)}`)?.scrollIntoView({ block: 'nearest' });
  restoreFocus(focusId, sel);
}

function listSig() {
  return sortedServers().map((s) => s.name).join(',');
}

function renderList() {
  const list = $('mcp-list');
  if (!list || !data) return;
  const focused = document.activeElement;
  const focusId = focused?.classList?.contains('pv-input') && list.contains(focused) ? focused.id : '';
  const sel = focusId ? [focused.selectionStart, focused.selectionEnd] : null;
  const servers = sortedServers();
  if (!servers.length) {
    list.dataset.sig = '';
    patch(list, `<div class="list-empty ex-empty">${icon('plug')}<strong>No servers yet</strong>MCP servers give Jarvis new tools — Slack, Notion, Linear, GitHub, a browser, your own.
      <div class="pv-actions is-center">${btn('tab', 'Browse the marketplace', { cls: 'btn-primary', ic: 'sparkles', data: { val: 'market' } })}</div></div>`);
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
  servers.forEach((s) => seen.add(s.name));
  restoreFocus(focusId, sel);
}

function renderHeader() {
  if (!data) return;
  const servers = data.servers || [];
  const live = servers.filter((s) => statusOf(s) === 'live').length;
  const need = servers.filter((s) => ['auth', 'key'].includes(statusOf(s))).length;
  const sub = $('mcp-sub');
  if (sub) {
    sub.textContent = !servers.length
      ? `${catalogRows().length} servers to connect in a click`
      : `${live} connected${need ? ` · ${need} need${need === 1 ? 's' : ''} you` : ''} · ${catalogRows().length} in the marketplace`;
  }
  const count = $('mcp-count');
  if (count) count.textContent = servers.length ? `Your servers · ${servers.length}` : 'Your servers';
  patch($('mcp-scope-seg'), seg([
    { value: 'false', label: 'This project', title: 'Only servers from this folder' },
    { value: 'true', label: 'Project + global', title: 'Also servers added for every project' },
  ], String(!!data.global_mcp), { label: 'Which servers to use', act: 'global' }));
  patch($('mcp-scope-hint'), data.global_mcp
    ? ''
    : 'Servers you add globally are hidden while this is on “This project”. Switch to “Project + global” to use them.');
  patch($('mcp-tabs'), tabsHtml());
  const t = currentTab();
  const ms = $('mk-sec');
  const ss = $('sv-sec');
  if (ms) ms.hidden = t !== 'market';
  if (ss) ss.hidden = t !== 'servers';
}

function render() {
  const body = $('mcp-body');
  if (!body) return;
  if (!data) {
    built = false;
    body.innerHTML = loadError
      ? `<div class="list-empty"><strong>Could not load MCP servers</strong>Check that Jarvis is still running, then try again.<div class="pv-actions is-center">${btn('reload', 'Try again', { ic: 'refresh-cw' })}</div></div>`
      : `<div class="list-loading">${'<div class="skeleton"></div>'.repeat(5)}</div>`;
    return;
  }
  if (!built || !$('mk-q')) build(body);
  renderHeader();
  if (currentTab() === 'market') renderMarket();
  else renderList();
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
  else if (key === 'app') {
    appOpen.add(name);
    openName = name;
    render();
    setTimeout(() => $(`mcp-app-${name}-id`)?.focus({ preventScroll: false }), 140);
  }
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
  appOpen.delete(name);
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

// ─── Marketplace actions ──────────────────────────────────────────────────

function setTab(t, { focus = true } = {}) {
  tab = t;
  render();
  if (!focus) return;
  setTimeout(() => {
    if (t === 'market' && !mk.custom) $('mk-q')?.focus({ preventScroll: true });
  }, 30);
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

/** The row's button (or Enter on it): connect now, or unfold its guided setup first. */
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
  if (['key', 'app', 'desktop'].includes(r.auth) && mk.open !== id) {
    toggleSetup(id);
    return;
  }
  connectMarket(id);
}

function toggleSetup(id) {
  mk.open = mk.open === id ? '' : id;
  delete mk.notes[id];
  renderMarket();
  if (mk.open === id) {
    setTimeout(() => {
      const row = $('mk-list')?.querySelector(`.mk-row[data-id="${CSS.escape(id)}"]`);
      row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      if (window.matchMedia('(min-width: 561px)').matches) row?.querySelector('.mk-panel .pv-input')?.focus({ preventScroll: true });
    }, 180);
  }
}

/** Add a marketplace server and take it as far as it goes: connected, or its sign-in page open. */
async function connectMarket(id) {
  const r = catalogRow(id);
  if (!r || mk.busy[id]) return;
  const { values, missing } = credentialsFor(r);
  if (missing.length) {
    mk.open = id;
    mk.notes[id] = { text: `Paste the ${missing.join(' and ')} first.`, error: true };
    renderMarket();
    $('mk-list')?.querySelector(`.mk-row[data-id="${CSS.escape(id)}"] .pv-input`)?.focus();
    return;
  }
  // A hosted sign-in follows: open its tab now, inside the click (pop-up blockers allow only that).
  let win = null;
  if (r.auth === 'oauth' || r.auth === 'app') {
    try { win = window.open('about:blank', '_blank'); } catch { /* blocked: the row offers the link */ }
  }
  mk.busy[id] = true;
  delete mk.notes[id];
  renderMarket();
  const res = await extPost('mcp/add', { source: id, scope: add.scope, credentials: values, connect: true });
  delete mk.busy[id];
  applyResult(res);
  const out = (res.servers || [])[0] || {};
  const name = out.name || id;
  switch (out.status) {
    case 'connected':
      closeWin(win);
      mk.open = '';
      for (const v of r.credentials || []) delete drafts[`mk-f-${id}-${v}`];
      showToast(`Connected ${r.label} · ${plural(out.tool_count || 0, 'tool')}`);
      haptic(10);
      break;
    case 'auth_required':
      mk.open = '';
      for (const v of r.credentials || []) delete drafts[`mk-f-${id}-${v}`];
      haptic(10);
      await authenticate(name, win);
      break;
    case 'needs_credentials':
      closeWin(win);
      mk.open = id;
      mk.notes[id] = { text: `${r.label} was added, but it still needs ${(out.missing || []).join(', ')}.`, error: true };
      break;
    case 'added':
      closeWin(win);
      mk.notes[id] = { text: `${r.label} added — switch on “Project + global” in Your servers to use it.`, error: false };
      break;
    case 'exists':
    case 'denied':
      closeWin(win);
      mk.notes[id] = { text: out.message || `${r.label} is already set up.`, error: false };
      break;
    default:
      closeWin(win);
      mk.open = '';
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

function openCustom(text = '') {
  mk.custom = true;
  render();
  if (text) setSource(text);
  setTimeout(() => {
    const ta = $('mcp-src');
    autosize(ta);
    ta?.focus({ preventScroll: true });
  }, 40);
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
  else if (it.kind === 'custom') openCustom();
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
    scrollToServer((first || res.servers[0])?.name);
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

/** Bring a card into view (the add panel above is tall, the new server sits below it). */
function scrollToServer(name) {
  if (!name) return;
  setTimeout(() => {
    const row = $('mcp-list')?.querySelector(`.pv-row[data-name="${CSS.escape(name)}"]`);
    row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, 260);
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
  const body = $('mcp-body');
  if (!el || !body.contains(el)) return;
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
    case 'disconnect': run(name, 'disconnect', 'mcp/disconnect', { name }, { okText: `Disconnected ${name}` }); break;
    case 'open-key': focusKey(name); break;
    case 'save-keys': saveKeys(name); break;
    case 'menu': menuFor(name, el); break;
    case 'copy': copyEndpoint(name); break;
    case 'tools':
      if (toolsOpen.has(name)) toolsOpen.delete(name); else toolsOpen.add(name);
      render();
      break;
    case 'tab': setTab(el.dataset.val); break;
    case 'mk-cat':
      mk.cat = el.dataset.val || ALL;
      mk.active = 0;
      renderMarket();
      $('mk-list')?.scrollTo({ top: 0 });
      break;
    case 'mk-pick': {
      const id = el.dataset.id;
      const picks = pickable(marketItems());
      const i = picks.findIndex((it) => it.key === id);
      if (i >= 0) mk.active = i;
      const r = catalogRow(id);
      // The row itself: guided ones unfold, installed ones go to their card, the rest connect.
      if (r && !installedOf(r) && !['key', 'app', 'desktop'].includes(r.auth)) connectMarket(id);
      else if (r && installedOf(r)) mkPrimary(id);
      else toggleSetup(id);
      break;
    }
    case 'mk-connect': connectMarket(el.dataset.id); break;
    case 'mk-toggle': toggleSetup(el.dataset.id); break;
    case 'mk-manage': manage(el.dataset.id); break;
    case 'mk-source': openCustom(mk.q.trim()); break;
    case 'mk-custom': openCustom(); break;
    case 'mk-back':
      mk.custom = false;
      render();
      setTimeout(() => $('mk-q')?.focus({ preventScroll: true }), 30);
      break;
    case 'open-app':
      appOpen.add(name);
      render();
      setTimeout(() => $(`mcp-app-${name}-id`)?.focus({ preventScroll: true }), 60);
      break;
    case 'close-app': appOpen.delete(name); render(); break;
    case 'save-app': saveApp(name); break;
    case 'copy-text':
      copyText(el.dataset.text || '').then((ok) => showToast(ok ? 'Copied' : 'Copy failed', !ok));
      break;
    case 'scope':
      add.scope = el.dataset.val;
      add.scopeTouched = true;
      renderAddPanel();
      patch($('mk-foot'), mkFootHtml());
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
    case 'dismiss-result': add.result = null; renderPreview(); break;
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
    $('mk-list')?.scrollTo({ top: 0 });
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
    } else if (e.key === 'Escape' && el.value) {
      // First Esc clears the search; the next one closes the dialog.
      e.preventDefault();
      e.stopPropagation();
      el.value = '';
      mk.q = '';
      mk.active = 0;
      renderMarket();
    }
    return;
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
  const mf = /^mk-f-(.+?)-[A-Za-z0-9_]+$/.exec(el.id);
  if (mf) {
    // Next empty field, else connect.
    const inputs = [...(el.closest('.mk-fields')?.querySelectorAll('.pv-input') || [])];
    const next = inputs.slice(inputs.indexOf(el) + 1).find((x) => !x.value.trim());
    if (next) next.focus();
    else connectMarket(mf[1]);
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
  if (m) {
    tab = 'market';
    mk.custom = false;
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
    mk.custom = false;
    mk.q = focus;
    mk.active = 0;
    focus = '';
  }
  render();
  if ($('mk-q') && $('mk-q').value !== mk.q) $('mk-q').value = mk.q;
  // Phones: no keyboard popping up over the list until the user taps the search.
  const wide = window.matchMedia('(min-width: 561px)').matches;
  openModal('mcp', {
    focus: (currentTab() === 'market' && wide ? $('mk-q') : null) || undefined,
    onClose: () => {
      closeMenu();
      confirmName = null;
      for (const n of Object.keys(notes)) delete notes[n];
      // A finished result / unsent text stay for the next visit; an open sign-in keeps its card.
      if (!Object.keys(auth).some((n) => openName === n)) openName = null;
      for (const id of Object.keys(mk.notes)) delete mk.notes[id];
      appOpen.clear();
    },
  });
  autosize($('mcp-src'));
  refreshMcp().then(() => {
    if (isModalOpen('mcp')) {
      if (!openName && currentTab() === 'servers') {
        const first = needsSignIn()[0] || sortedServers().find((s) => statusOf(s) === 'key');
        if (first) openName = first.name;
      }
      render();
      if (currentTab() === 'market' && !mk.custom && document.activeElement?.id !== 'mk-q'
        && window.matchMedia('(min-width: 561px)').matches) $('mk-q')?.focus({ preventScroll: true });
    }
  });
}

export function closeMcp() {
  closeModal('mcp');
}

export function initMcp() {
  const body = $('mcp-body');
  body?.addEventListener('click', handleClick);
  body?.addEventListener('input', handleInput);
  body?.addEventListener('keydown', handleKey);
  $('mcp-chip')?.addEventListener('click', handleBannerClick);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      if (pollTimer) pollAuth();
      refreshSoon();
    }
  });
  refreshMcp();
}

