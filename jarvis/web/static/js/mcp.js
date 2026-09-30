/** MCP servers — the web /mcp: add one by pasting a link, sign in, connect, remove.
 *
 * One dialog: an "Add a server" panel on top (paste a hosted address, an
 * `npx …` command, a `claude mcp add …` line, JSON, a GitHub link or a name
 * like "linear"; Jarvis previews what it found), then one card per server with
 * its status and the one thing to do next — Authenticate, Enter key, Connect,
 * Retry or Disconnect. A hosted server that needs a browser sign-in also puts a
 * banner above the composer and a dot on the sidebar's MCP button, so the
 * button is one click away even with the dialog shut (the agent may have added
 * the server). Server side: jarvis/web/extensions_api.py.
 *
 * Sign-in: the click opens a blank tab *synchronously* (pop-up blockers allow
 * that), the server hands back the provider's link, the tab is pointed at it,
 * and the page then watches until the server is live. On another device the
 * last page won't load — its address can be pasted instead.
 */
import { $, escapeHtml, showToast, haptic, debounce, copyText } from './utils.js';
import { icon } from './icons.js';
import { store, loadSnapshot } from './store.js';
import { openModal, closeModal, isModalOpen } from './modal.js';
import { fetchMcpServers, fetchMcpAuth, extPost, pickerAction } from './api.js';
import { btn, rowBtn, moreBtn, seg, mark, patch, field, autosize, msg, spin, plural, tildify, openMenu, closeMenu } from './extui.js';

const TONE = { live: 'accent', auth: 'amber', key: 'amber', warn: 'amber', failed: 'chili', connecting: 'slate', idle: 'slate' };
const RANK = { auth: 0, key: 1, failed: 2, warn: 3, connecting: 4, live: 5, idle: 6 };
const SCOPES = [
  { value: 'project', label: 'This project', title: 'Only in this folder (.mcp.json)' },
  { value: 'global', label: 'Global', title: 'Every project on this computer' },
];

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
let built = false;
let chipsOpen = false;
const CHIPS_SHOWN = 6;

const add = {
  text: '',
  scope: 'global',
  scopeTouched: false,
  preview: null, // { loading } | { servers } | { error }
  adding: false,
  wantAdd: false,
  result: null, // { servers: [...] } | { error }
};

const listeners = new Set();
/** Others (sidebar) get the server list whenever it changes. */
export function onMcpChange(fn) {
  listeners.add(fn);
  if (data) fn(data);
}

const onThisComputer = () => /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);

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

const scopeLabel = (s) => (s.scope === 'project' ? 'Project' : s.source === 'jarvis' || !s.source ? 'Global' : s.source_label || 'Global');

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

function headHtml(s) {
  const st = statusOf(s);
  return `${mark(s.name, TONE[st], { on: st === 'live' })}
    <span class="pv-text">
      <span class="pv-title"><span class="ex-name">${escapeHtml(s.name)}</span><span class="badge ex-scope" title="${escapeHtml(s.source_label || '')}">${escapeHtml(scopeLabel(s))}</span></span>
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
  const inputs = vars.map((v) => {
    const id = `mcp-key-${s.name}-${v}`;
    return `<label class="ex-lbl" for="${escapeHtml(id)}">${escapeHtml(v)}</label>
      ${field(id, { value: drafts[id] || '', placeholder: 'Paste it here', label: v, secret: true, shown: revealed.has(id) })}`;
  }).join('');
  return `<div class="pv-steps is-single">${step('', `<strong>${vars.length > 1 ? 'Enter these keys' : 'Enter this key'}</strong>${inputs}
      <span class="pv-hint">Saved on this computer only (<code>~/.config/harness-agent</code>) — never written into the config file.</span>`)}</div>
    <div class="pv-actions">${btn('save-keys', 'Save and connect', { cls: 'btn-primary', data: { name: s.name }, busy: b })}</div>`;
}

function failedPanel(s) {
  const h = s.health || {};
  const err = h.last_connect_error || h.detail || 'Could not connect.';
  const tokenish = /api key|token|refused access|rejected the token/i.test(err);
  return `${msg(err, 'error')}
    ${(h.hints || []).length ? `<ul class="ex-hints">${h.hints.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : ''}
    ${tokenish ? `<p class="pv-hint">Needs a key? Add the server again from the panel above with the header, e.g. <code>claude mcp add --transport http ${escapeHtml(s.name)} ${escapeHtml(s.endpoint || 'URL')} --header "Authorization: Bearer …"</code>. Jarvis keeps the token out of the file.</p>` : ''}
    <div class="pv-actions">${btn('connect', 'Try again', { cls: 'btn-primary', ic: 'refresh-cw', data: { name: s.name }, busy: busy[s.name] === 'connect' })}</div>`;
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
  if (st === 'auth') mid = authPanel(s);
  else if (st === 'key') mid = keyPanel(s);
  else if (st === 'failed') mid = failedPanel(s);
  else if (st === 'warn') mid = warnPanel(s);
  else if (st === 'live') mid = toolsHtml(s);
  return `${mid}${noteHtml(s.name)}${factsHtml(s)}`;
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

function chipsHtml() {
  const cat = data?.catalog || [];
  if (!cat.length) return '';
  const shown = chipsOpen ? cat : cat.slice(0, CHIPS_SHOWN);
  const more = cat.length - CHIPS_SHOWN;
  return `<div class="ex-chips" role="group" aria-label="Popular servers">${shown.map((c) => `
    <button type="button" class="pv-chip ex-chip" data-act="chip" data-id="${escapeHtml(c.id)}" title="${escapeHtml(c.desc || '')}">${mark(c.label, c.kind === 'remote' ? 'indigo' : 'slate', { sm: true })}<span>${escapeHtml(c.label)}</span></button>`).join('')}
    ${more > 0 ? `<button type="button" class="pv-chip ex-chip ex-chip-more" data-act="chips-toggle" aria-expanded="${chipsOpen}">${chipsOpen ? 'Fewer' : `+${more} more`}</button>` : ''}</div>`;
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
    return '<p class="pv-hint ex-idle">Hosted servers open a sign-in page. Local ones run a command on this computer — Jarvis shows you which before adding.</p>';
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

// ─── Render ───────────────────────────────────────────────────────────────

function build(body) {
  body.innerHTML = `
    <section class="ex-add" aria-label="Add a server">
      <div class="ex-add-head"><h3>Add a server</h3><span>Paste anything — Jarvis works out the rest</span></div>
      <div class="ex-src">
        <span class="pv-field-ic">${icon('link')}</span>
        <textarea id="mcp-src" class="ex-src-input" rows="1" placeholder="Paste a link, npx …, claude mcp add …, JSON, or a name like “linear”"
          aria-label="Server to add" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
          data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other"></textarea>
      </div>
      <div class="ex-add-row">
        <div class="ex-add-scope" id="mcp-add-scope"></div>
        <span id="mcp-add-btn"></span>
      </div>
      <p class="ex-path" id="mcp-path"></p>
      <div id="mcp-chips"></div>
      <div class="ex-preview" id="mcp-preview" aria-live="polite"></div>
    </section>
    <section class="ex-list-sec" aria-label="Your servers">
      <div class="ex-list-head"><h3 id="mcp-count">Your servers</h3><span id="mcp-scope-seg"></span></div>
      <p class="pv-hint ex-scope-hint" id="mcp-scope-hint"></p>
      <div class="ex-list" id="mcp-list"></div>
    </section>
    <p class="pv-foot">${icon('lock')}<span>Keys and sign-ins stay on the computer running Jarvis. This browser never keeps them.</span></p>`;
  const ta = $('mcp-src');
  ta.value = add.text;
  autosize(ta);
  built = true;
}

function renderAddPanel() {
  patch($('mcp-add-scope'), seg(SCOPES, add.scope, { label: 'Where to add it', act: 'scope', group: 'add' }));
  patch($('mcp-add-btn'), addBtnHtml());
  patch($('mcp-path'), pathHint());
  patch($('mcp-chips'), chipsHtml());
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
    patch(list, `<div class="list-empty ex-empty">${icon('plug')}<strong>No servers yet</strong>MCP servers give Jarvis new tools — Linear, Notion, GitHub, a browser, your own. Paste one above, or pick from the popular ones.</div>`);
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
      ? 'Tool servers Jarvis can call'
      : `${live} connected${need ? ` · ${need} need${need === 1 ? 's' : ''} you` : ''}`;
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
  if (!built || !$('mcp-src')) build(body);
  renderHeader();
  renderAddPanel();
  renderList();
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
export async function authenticate(name) {
  const known = auth[name];
  if (known?.url) {
    // Already have the link: just open it again (still inside the click).
    const w = window.open(known.url, '_blank');
    known.opened = !!w;
    if (w) { try { w.opener = null; } catch { /* cross-origin */ } }
    renderAll();
    return;
  }
  if (known?.starting) return;
  let win = null;
  try { win = window.open('about:blank', '_blank'); } catch { /* blocked */ }
  auth[name] = { starting: true, win, opened: !!win, startedAt: Date.now() };
  delete notes[name];
  if (isModalOpen('mcp')) openName = name;
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
  else if (key === 'signout') await run(name, 'signout', 'mcp/signout', { name }, { okText: `Signed out of ${name}` });
  else if (key === 'remove') await run(name, 'remove', 'mcp/remove', { name, scope: s.scope }, { okText: `Removed ${name}` });
  else if (key === 'move-global') await run(name, 'move', 'mcp/move', { name, scope: 'global' }, { okText: `Moved ${name} to global` });
  else if (key === 'move-project') await run(name, 'move', 'mcp/move', { name, scope: 'project' }, { okText: `Moved ${name} to this project` });
}

async function copyEndpoint(name) {
  const s = serverByName(name);
  if (!s) return;
  if (await copyText(s.endpoint || '')) showToast(s.remote ? 'Address copied' : 'Command copied');
  else showToast('Copy failed', true);
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
    case 'chip': setSource(el.dataset.id); $('mcp-src')?.focus(); break;
    case 'chips-toggle': chipsOpen = !chipsOpen; renderAddPanel(); break;
    case 'scope':
      add.scope = el.dataset.val;
      add.scopeTouched = true;
      renderAddPanel();
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

/** `focus`: a server name to open its card. */
export function openMcp(focus = '') {
  if (focus) openName = focus;
  render();
  openModal('mcp', {
    focus: $('mcp-src') || undefined,
    onClose: () => {
      closeMenu();
      confirmName = null;
      for (const n of Object.keys(notes)) delete notes[n];
      // A finished result / unsent text stay for the next visit; an open sign-in keeps its card.
      if (!Object.keys(auth).some((n) => openName === n)) openName = null;
    },
  });
  autosize($('mcp-src'));
  refreshMcp().then(() => {
    if (isModalOpen('mcp')) {
      if (!openName) {
        const first = needsSignIn()[0] || sortedServers().find((s) => statusOf(s) === 'key');
        if (first) openName = first.name;
      }
      render();
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

