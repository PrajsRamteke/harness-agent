/** Local models — the web /local-models: models running on your own hardware.
 *
 *   [🔍 Filter models ……………………………]  [↻]  [+ Add server]
 *   ● 2 running · 5 models            Context window [8K 16K 32K 64K 128K]
 *   ┌ Ol  Ollama · on this computer · v0.12.3                  ● Running ┐
 *   │  qwen3:8b      8.2B · Q4_K_M · 5.2 GB   Tools Think 32K   In use   │
 *   │  gpt-oss:20b   …                                          Use →    │
 *   └────────────────────────────────────────────────────────────────────┘
 *   NOT RUNNING — start one and it shows up here
 *   [LM Studio  Desktop app …  $ lms server start ⎘   Get it ↗   ⋯]
 *
 * Detection is live: while the dialog is open it asks the server to look
 * again every few seconds, so starting Ollama makes it appear. A row's Use
 * switches the session (on the terminal's thread) and closes the dialog.
 * "Add server" is a sub-view (address, name, optional key → test → save).
 * Server: jarvis/web/local_api.py → jarvis/auth/local_models.py.
 */
import { $, escapeHtml, showToast, haptic, copyText } from './utils.js';
import { icon } from './icons.js';
import { openModal, closeModal, isModalOpen } from './modal.js';
import { setView, setHomeSub, toolbar, section, footer, empty, arrowRows } from './dialog.js';
import { patch, spin, plural, field, msg, openMenu, closeMenu } from './extui.js';
import { fetchLocal, localPost } from './api.js';

const ID = 'local';
const SEARCH_ID = 'lm-q';
const POLL_MS = 4000;
const HOME_SUB = 'Running on your own hardware · no key, no cost';
const TONES = { ollama: 'slate', lmstudio: 'indigo', llamacpp: 'clay', vllm: 'mint', jan: 'accent', gpt4all: 'mint', koboldcpp: 'chili', docker: 'indigo', textgen: 'clay' };
const EXAMPLES = [
  { label: 'Ollama on another computer', url: 'http://192.168.1.20:11434' },
  { label: 'LM Studio on a custom port', url: 'http://localhost:1235/v1' },
  { label: 'vLLM / LocalAI', url: 'http://gpu-box.local:8000/v1' },
];

let data = null; // last /api/local
let loadError = false;
let query = '';
let view = null; // null | 'add'
let busy = ''; // "use:<id>::<model>" | "ctx" | "scan" | "add" | "detect:<id>"
let pollTimer = 0;
let scanning = false;
let flashId = ''; // a server just added
const draft = { url: '', name: '', key: '' };
let addError = '';
let addFailed = false; // offer "Add without testing"
let revealKey = false;
let moreOpen = false; // the folded "Other apps" list

// ─── Data ─────────────────────────────────────────────────────────────────

function setData(next) {
  if (!next || !Array.isArray(next.servers)) return;
  data = next;
  loadError = false;
}

async function load({ scan = false } = {}) {
  scanning = scan;
  paintStatus();
  try {
    setData(await fetchLocal(scan));
  } catch {
    if (!data) loadError = true;
  }
  scanning = false;
  if (isModalOpen(ID)) render();
}

async function op(payload) {
  const res = await localPost(payload);
  if (res.local) setData(res.local);
  return res;
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    if (!isModalOpen(ID)) return stopPolling();
    if (view || busy || document.hidden) return;
    load({ scan: true });
  }, POLL_MS);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = 0;
}

// ─── Small parts ──────────────────────────────────────────────────────────

const k = (n) => (!n ? '' : n >= 1024 ? `${Math.round(n / 1024)}K` : String(n));

function tile(s, { on = false } = {}) {
  const tone = TONES[s.server] || (s.custom ? 'accent' : 'slate');
  return `<span class="pv-mark lm-mark${on ? ' is-on' : ''}" data-tone="${tone}" aria-hidden="true">${escapeHtml(s.mark || '•')}</span>`;
}

function chip(ic, label, { cls = '', title = '' } = {}) {
  return `<span class="lm-chip${cls ? ` ${cls}` : ''}"${title ? ` title="${escapeHtml(title)}"` : ''}>${ic ? icon(ic) : ''}<span>${escapeHtml(label)}</span></span>`;
}

function caps(m) {
  const out = [];
  if (m.tools === false) out.push(chip('ban', 'Chat only', { cls: 'is-warn', title: 'Can’t call tools: it can talk, but not run commands or edit files' }));
  else if (m.tools) out.push(chip('wrench', 'Tools', { title: 'Can run commands and edit files' }));
  if (m.think) out.push(chip('brain', 'Thinks', { title: 'Reasons before answering' }));
  if (m.vision) out.push(chip('image', 'Vision', { title: 'Can see images you attach' }));
  const ctx = m.served_ctx || m.ctx;
  if (ctx) {
    const capped = m.served_ctx && m.ctx && m.served_ctx < m.ctx;
    out.push(chip('gauge', k(ctx), { cls: 'is-ctx', title: capped ? `Uses ${k(m.served_ctx)} of its ${k(m.ctx)} context window` : `${k(ctx)} context window` }));
  }
  return out.join('');
}

function where(s) {
  if (s.remote) return s.host;
  return 'on this computer';
}

// ─── Markup ───────────────────────────────────────────────────────────────

function statusHtml() {
  if (!data) return '';
  const on = data.online || 0;
  const head = on
    ? `<span class="lm-live is-on" aria-hidden="true"></span><strong>${plural(on, 'server')} running</strong><span class="lm-dot">·</span><span>${plural(data.model_count || 0, 'model')}</span>`
    : `<span class="lm-live" aria-hidden="true"></span><strong>Nothing running yet</strong><span class="lm-dot">·</span><span>watching for one</span>`;
  const choices = data.context_choices || [];
  const segHtml = `<div class="seg lm-seg" role="group" aria-label="Context window">${choices.map((n) => `
    <button type="button" data-act="ctx" data-n="${n}" aria-pressed="${n === data.context}"${busy === 'ctx' ? ' disabled' : ''}>${k(n)}</button>`).join('')}</div>`;
  return `<div class="lm-stat">${head}</div>
    <div class="lm-ctx" title="The window Ollama is asked for. Bigger remembers more of the chat but uses more memory; other servers set their own when they load a model.">
      <span class="lm-ctx-lbl">${icon('gauge')}<span>Context</span></span>${segHtml}
    </div>`;
}

function modelRow(s, m, i) {
  const key = `use:${s.id}::${m.id}`;
  const meta = [m.params, m.quant, m.size || (m.cloud ? 'cloud' : '')].filter(Boolean).join(' · ');
  const side = m.active
    ? '<span class="badge is-live">In use</span>'
    : busy === key
      ? `<span class="lm-use is-busy">${spin('')}</span>`
      : `<span class="lm-use">Use ${icon('arrow-right')}</span>`;
  const loaded = m.loaded ? '<span class="lm-loaded" title="Loaded in memory — answers right away"></span>' : '';
  return `<button type="button" class="lm-model${m.active ? ' is-active' : ''}${m.usable ? '' : ' is-limited'}" data-act="use" data-id="${escapeHtml(s.id)}" data-model="${escapeHtml(m.id)}"
      data-hay="${escapeHtml(`${m.id} ${m.family} ${s.name}`.toLowerCase())}" style="--i:${i}"${busy && busy !== key ? ' aria-disabled="true"' : ''}
      title="${m.active ? 'In use now' : `Use ${escapeHtml(m.id)}`}">
    <span class="lm-model-main">
      <span class="lm-model-name">${loaded}${escapeHtml(m.id)}</span>
      ${meta ? `<span class="lm-model-meta">${escapeHtml(meta)}</span>` : ''}
    </span>
    <span class="lm-caps">${caps(m)}</span>
    <span class="lm-side">${side}</span>
  </button>`;
}

function serverCard(s, idx) {
  const ver = s.version ? ` · v${escapeHtml(s.version)}` : '';
  const menu = s.custom ? `<button type="button" class="row-btn is-icon" data-act="menu" data-id="${escapeHtml(s.id)}" aria-haspopup="menu" aria-label="More for ${escapeHtml(s.name)}" title="More">${icon('ellipsis')}</button>` : '';
  const models = s.models.length
    ? s.models.map((m, i) => modelRow(s, m, idx * 4 + i)).join('')
    : `<div class="lm-nomodels">${icon('download')}<span>No chat models on ${escapeHtml(s.name)} yet.${s.pull ? ` Get one: <code>${escapeHtml(s.pull)}</code> ${copyBtn(s.pull)}` : ''}</span></div>`;
  return `<section class="lm-server${flashId === s.id ? ' just-added' : ''}" data-server="${escapeHtml(s.id)}" style="--i:${idx}">
    <header class="lm-server-head">
      ${tile(s, { on: true })}
      <span class="lm-server-titles">
        <span class="lm-server-name">${escapeHtml(s.name)}</span>
        <span class="lm-server-sub">${icon(s.remote ? 'wifi' : 'monitor')}<span>${escapeHtml(where(s))}${ver}</span></span>
      </span>
      <span class="badge is-live lm-run"><span class="lm-live is-on" aria-hidden="true"></span>Running</span>
      ${menu}
    </header>
    <div class="lm-models">${models}</div>
  </section>`;
}

function copyBtn(text) {
  return `<button type="button" class="lm-copy" data-act="copy" data-text="${escapeHtml(text)}" aria-label="Copy command" title="Copy">${icon('copy')}</button>`;
}

/** A runtime that isn't running: one line — what it is, how to start it, where to get it. */
function runtimeRow(s, i) {
  const start = s.start
    ? `<span class="lm-cmd is-row" title="${escapeHtml(s.how ? `${s.start} — ${s.how}` : s.start)}"><code>${escapeHtml(s.start)}</code>${copyBtn(s.start)}</span>`
    : s.how ? `<span class="lm-how">${escapeHtml(s.how)}</span>` : '';
  return `<div class="lm-rt" data-server="${escapeHtml(s.id)}" data-hay="${escapeHtml(`${s.name} ${s.blurb}`.toLowerCase())}" style="--i:${i}">
    ${tile(s)}
    <span class="lm-rt-titles"><span class="lm-rt-name">${escapeHtml(s.name)}</span>
      <span class="lm-rt-sub">${escapeHtml(s.blurb)} · <code>${escapeHtml(s.host)}</code></span></span>
    ${start}
    <span class="lm-rt-acts">
      ${s.site ? `<a class="row-btn lm-get" href="${escapeHtml(s.site)}" target="_blank" rel="noopener noreferrer" title="Get ${escapeHtml(s.name)}">Get ${icon('external-link')}</a>` : ''}
      <button type="button" class="row-btn is-icon" data-act="rt-menu" data-id="${escapeHtml(s.id)}" data-server="${escapeHtml(s.server)}" aria-haspopup="menu" aria-label="More for ${escapeHtml(s.name)}" title="More">${icon('ellipsis')}</button>
    </span>
  </div>`;
}

function offlineServer(s) {
  const seen = s.last_seen ? `last seen ${ago(s.last_seen)}` : (s.error || 'not answering');
  const n = s.models.length;
  return `<div class="lm-off" data-server="${escapeHtml(s.id)}" data-hay="${escapeHtml(s.name.toLowerCase())}">
    ${tile(s)}
    <span class="lm-off-titles"><span class="lm-off-name">${escapeHtml(s.name)}</span>
      <span class="lm-off-sub">${escapeHtml(s.host)} · ${escapeHtml(seen)}${n ? ` · ${plural(n, 'model')} remembered` : ''}</span></span>
    <span class="badge is-warn">${s.status === 'auth' ? 'Needs key' : 'Offline'}</span>
    <button type="button" class="row-btn" data-act="retry" data-id="${escapeHtml(s.id)}"${busy === 'scan' ? ' disabled' : ''}>${icon('refresh-cw')}<span>Retry</span></button>
    <button type="button" class="row-btn is-icon" data-act="menu" data-id="${escapeHtml(s.id)}" aria-haspopup="menu" aria-label="More for ${escapeHtml(s.name)}" title="More">${icon('ellipsis')}</button>
  </div>`;
}

function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function quickstartHtml() {
  const ollama = data.servers.find((s) => s.server === 'ollama');
  return `<div class="lm-start">
    <div class="lm-start-art" aria-hidden="true"><span class="lm-start-ring"></span><span class="lm-start-ic">${icon('cpu')}</span></div>
    <h3>Run models on this computer</h3>
    <p>Free, private, works offline. Install a model app, start it, and Jarvis finds it — this page is watching.</p>
    <ol class="lm-steps">
      <li><span class="pv-n">1</span><span class="lm-step">Install <a href="https://ollama.com/download" target="_blank" rel="noopener noreferrer">Ollama</a> <span class="lm-or">or</span> <a href="https://lmstudio.ai" target="_blank" rel="noopener noreferrer">LM Studio</a></span></li>
      <li><span class="pv-n">2</span><span class="lm-step">Get a model that can use tools <span class="lm-cmd is-inline"><code>ollama pull qwen3</code>${copyBtn('ollama pull qwen3')}</span></span></li>
      <li><span class="pv-n">3</span><span class="lm-step">Start it${ollama ? ' — open the app, or run <code>ollama serve</code>' : ''}. It shows up here by itself.</span></li>
    </ol>
  </div>`;
}

function bodyHtml() {
  if (loadError) {
    return empty('Couldn’t load local models', 'Check that Jarvis is still running.', {
      ic: 'circle-alert', action: '<button type="button" class="btn" data-act="reload">Try again</button>',
    });
  }
  if (!data) return `<div class="lm-loading">${spin('Looking for local models')}</div>`;
  if (!data.enabled) {
    return empty('Local models are turned off', 'Jarvis was started with HARNESS_LOCAL_MODELS=0.', { ic: 'circle-off' });
  }
  const running = data.servers.filter((s) => s.status === 'online');
  const down = data.servers.filter((s) => s.custom && s.status !== 'online');
  const idle = data.servers.filter((s) => !s.custom && s.status !== 'online' && s.status !== 'off');
  const skipped = data.servers.filter((s) => s.status === 'off');
  const out = [`<div class="lm-status" id="lm-status">${statusHtml()}</div>`];
  if (running.length) out.push(`<div class="lm-servers">${running.map(serverCard).join('')}</div>`);
  else out.push(quickstartHtml());
  if (down.length) {
    out.push(`<section class="lm-group">${section('Your servers', { count: down.length, right: 'Not answering right now' })}<div class="lm-offs">${down.map(offlineServer).join('')}</div></section>`);
  }
  if (idle.length) {
    // With something running, the rest folds away (remembered while the page lives).
    const rows = `<div class="lm-rts">${idle.map(runtimeRow).join('')}</div>`;
    if (running.length) {
      out.push(`<section class="lm-group"><details class="lm-more"${moreOpen ? ' open' : ''}>
        <summary>${icon('chevron-right')}<span>Other apps Jarvis looks for</span><em>${idle.length}</em><span class="lm-more-sub">Start one and it shows up above</span></summary>
        ${rows}</details></section>`);
    } else {
      out.push(`<section class="lm-group">${section('Jarvis looks for', { count: idle.length, right: 'On their usual ports' })}${rows}</section>`);
    }
  }
  if (skipped.length) {
    out.push(`<p class="lm-skipped">${icon('eye-off')}<span>Not looking for ${skipped.map((s) => `<button type="button" class="lm-skip" data-act="detect-on" data-server="${escapeHtml(s.server)}" title="Look for ${escapeHtml(s.name)} again">${escapeHtml(s.name)}</button>`).join(', ')}</span></p>`);
  }
  out.push('<div id="lm-empty" hidden></div>');
  return out.join('');
}

function addHtml() {
  return `<form class="lm-add" id="lm-add-form" autocomplete="off">
    <p class="lm-add-lead">Add a model server Jarvis can’t find by itself — another computer on your network, a different port, or one behind an API key. Works with anything that speaks Ollama’s API or OpenAI’s.</p>
    <label class="lm-label" for="lm-url">Address</label>
    ${field('lm-url', { value: draft.url, placeholder: 'http://192.168.1.20:11434', label: 'Server address', lead: 'globe' })}
    <div class="lm-examples">${EXAMPLES.map((e, i) => `<button type="button" class="pv-chip" data-act="example" data-i="${i}"><span>${escapeHtml(e.label)}</span></button>`).join('')}</div>
    <label class="lm-label" for="lm-name">Name <em>optional</em></label>
    ${field('lm-name', { value: draft.name, placeholder: 'Studio Mac', label: 'Name', lead: 'pencil', mono: false })}
    <label class="lm-label" for="lm-key">API key <em>only if the server asks for one</em></label>
    ${field('lm-key', { value: draft.key, placeholder: 'Leave empty for most local servers', label: 'API key', lead: 'key-round', secret: true, shown: revealKey })}
    <div id="lm-add-msg">${addError ? msg(addError) : ''}</div>
    <p class="lm-add-note">${icon('lock')}<span>Saved on the computer running Jarvis. The key is never sent back to this page.</span></p>
  </form>`;
}

function addFootHtml() {
  const testing = busy === 'add';
  return `<span class="dlg-spacer"></span><div class="dlg-actions">
    <button type="button" class="btn btn-quiet" data-act="add-cancel">Cancel</button>
    ${addFailed ? `<button type="button" class="btn" data-act="add-force"${testing ? ' disabled' : ''}>Add anyway</button>` : ''}
    <button type="button" class="btn btn-primary" data-act="add-save"${testing || !draft.url.trim() ? ' disabled' : ''}>${testing ? spin('Checking…') : `${icon('plug-zap')}<span>Test &amp; add</span>`}</button>
  </div>`;
}

// ─── Render ───────────────────────────────────────────────────────────────

let built = ''; // 'list' | 'add'

function renderChrome() {
  const bar = $('local-bar');
  if (bar && !$(SEARCH_ID)) {
    bar.innerHTML = toolbar({
      id: SEARCH_ID,
      placeholder: 'Filter models',
      label: 'Filter local models',
      value: query,
      extra: `<button type="button" class="row-btn is-icon lm-rescan" data-act="rescan" aria-label="Look again" title="Look again">${icon('refresh-cw')}</button>`,
      action: { act: 'add', label: 'Add server', ic: 'plus', title: 'Another computer, a custom port, an API key' },
    });
  }
  if (bar) bar.hidden = view === 'add';
}

function paintStatus() {
  const el = $('lm-status');
  if (el) patch(el, statusHtml());
  $('local-bar')?.querySelector('.lm-rescan')?.classList.toggle('is-spinning', scanning);
}

function render() {
  const body = $('local-body');
  const foot = $('local-foot');
  if (!body || !foot) return;
  renderChrome();
  if (view === 'add') {
    if (built !== 'add') {
      setView(ID, { title: 'Add a server', sub: 'Ollama or any OpenAI-compatible server', back: closeAdd, backLabel: 'local models' });
      body.innerHTML = addHtml();
      body._html = '';
      built = 'add';
      requestAnimationFrame(() => $('lm-url')?.focus({ preventScroll: true }));
    } else {
      patch($('lm-add-msg'), addError ? msg(addError) : '');
    }
    patch(foot, addFootHtml());
    return;
  }
  if (built !== 'list') {
    setView(ID, null);
    built = 'list';
    body._html = '';
  }
  const n = data?.online || 0;
  setHomeSub(ID, n ? `${plural(n, 'server')} running · ${plural(data.model_count || 0, 'model')}` : HOME_SUB);
  patch(body, bodyHtml());
  filter();
  patch(foot, footer({ note: 'Nothing leaves this computer', noteIc: 'lock', hints: [['↑ ↓', 'move'], ['↵', 'use'], ['esc', 'close']] }));
  if (flashId) setTimeout(() => { flashId = ''; }, 1600);
}

/** Show the models / runtimes the filter matches — in place, no rebuild. */
function filter() {
  const body = $('local-body');
  if (!body) return;
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  let shown = 0;
  body.querySelectorAll('[data-hay]').forEach((el) => {
    const hit = !words.length || words.every((w) => el.dataset.hay.includes(w));
    el.hidden = !hit;
    if (hit) shown += 1;
  });
  body.querySelectorAll('.lm-server').forEach((card) => {
    card.hidden = !!words.length && !card.querySelector('.lm-model:not([hidden])');
  });
  body.querySelectorAll('.lm-group').forEach((g) => {
    g.hidden = !!words.length && !g.querySelector('[data-hay]:not([hidden])');
  });
  const em = $('lm-empty');
  if (em) {
    em.hidden = !words.length || shown > 0;
    patch(em, em.hidden ? '' : empty('No local models match', `Nothing called “${escapeHtml(query.trim())}”. Pull it into Ollama or LM Studio and it shows up here.`, { ic: 'search' }));
  }
}

// ─── Actions ──────────────────────────────────────────────────────────────

async function useModel(id, model) {
  if (busy) return;
  const s = data?.servers.find((x) => x.id === id);
  const m = s?.models.find((x) => x.id === model);
  if (m?.active) {
    closeModal(ID);
    return;
  }
  busy = `use:${id}::${model}`;
  render();
  const res = await op({ op: 'use', id, model });
  busy = '';
  if (!res.ok) {
    render();
    showToast(res.error || 'Couldn’t switch to that model', true);
    return;
  }
  haptic(12);
  showToast(res.message || `Now using ${model}`);
  closeModal(ID);
}

async function setContext(n) {
  if (busy || n === data?.context) return;
  busy = 'ctx';
  paintStatus();
  const res = await op({ op: 'context', tokens: n });
  busy = '';
  render();
  if (res.ok) showToast(`${res.message || 'Context changed'} · the model reloads once`);
  else showToast(res.error || 'Couldn’t change it', true);
}

async function rescan() {
  if (scanning) return;
  await load({ scan: true });
  const n = data?.online || 0;
  showToast(n ? `${plural(n, 'server')} running` : 'Nothing running yet');
}

async function detect(server, on) {
  busy = `detect:${server}`;
  const res = await op({ op: 'detect', server, on });
  busy = '';
  render();
  if (!res.ok) showToast(res.error || 'Couldn’t change it', true);
}

async function removeServer(id) {
  const s = data?.servers.find((x) => x.id === id);
  const res = await op({ op: 'remove', id });
  render();
  showToast(res.ok ? (res.message || `Removed ${s?.name || 'the server'}`) : (res.error || 'Couldn’t remove it'), !res.ok);
}

async function serverMenu(anchor, id) {
  const s = data?.servers.find((x) => x.id === id);
  if (!s) return;
  const items = [
    { key: 'retry', label: 'Look again', icon: 'refresh-cw' },
    { key: 'copy', label: 'Copy address', icon: 'copy' },
    { divider: true },
    { key: 'remove', label: 'Remove server', icon: 'trash-2', danger: true, confirm: true },
  ];
  const pick = await openMenu(anchor, items);
  if (pick === 'retry') load({ scan: true });
  else if (pick === 'copy') copy(s.url);
  else if (pick === 'remove') removeServer(id);
}

async function runtimeMenu(anchor, server, id) {
  const s = data?.servers.find((x) => x.id === id);
  const items = [
    { key: 'retry', label: 'Look again', icon: 'refresh-cw' },
    ...(s?.site ? [{ key: 'site', label: `Open ${hostOf(s.site)}`, icon: 'external-link' }] : []),
    { divider: true },
    { key: 'off', label: 'Stop looking for it', icon: 'eye-off' },
  ];
  const pick = await openMenu(anchor, items);
  if (pick === 'retry') load({ scan: true });
  else if (pick === 'site' && s?.site) window.open(s.site, '_blank', 'noopener');
  else if (pick === 'off') detect(server, false);
}

function hostOf(url) {
  try {
    return new URL(url).host.replace(/^www\./, '');
  } catch {
    return url;
  }
}

async function copy(text) {
  try {
    await copyText(text);
    showToast('Copied');
  } catch {
    showToast(text);
  }
}

function openAdd() {
  view = 'add';
  addError = '';
  addFailed = false;
  built = '';
  render();
}

function closeAdd() {
  view = null;
  built = '';
  addError = '';
  addFailed = false;
  render();
  $(SEARCH_ID)?.focus({ preventScroll: true });
}

async function saveServer(force = false) {
  if (busy === 'add' || !draft.url.trim()) return;
  busy = 'add';
  addError = '';
  render();
  const res = await op({ op: 'add', url: draft.url, name: draft.name, key: draft.key, force });
  busy = '';
  if (!res.ok) {
    addError = res.error || 'Couldn’t add that server';
    addFailed = true;
    render();
    $('lm-url')?.focus({ preventScroll: true });
    return;
  }
  haptic(12);
  flashId = res.id || '';
  draft.url = '';
  draft.name = '';
  draft.key = '';
  showToast(res.message || 'Server added');
  closeAdd();
}

// ─── Wiring ───────────────────────────────────────────────────────────────

function onClick(e) {
  const el = e.target.closest('[data-act]');
  const card = $(ID);
  if (!el || !card?.contains(el) || el.disabled) return;
  switch (el.dataset.act) {
    case 'use':
      if (el.getAttribute('aria-disabled') !== 'true') useModel(el.dataset.id, el.dataset.model);
      break;
    case 'ctx': setContext(Number(el.dataset.n)); break;
    case 'rescan': rescan(); break;
    case 'retry': load({ scan: true }); break;
    case 'reload': load({ scan: true }); break;
    case 'add': openAdd(); break;
    case 'add-cancel': closeAdd(); break;
    case 'add-save': saveServer(false); break;
    case 'add-force': saveServer(true); break;
    case 'copy': e.preventDefault(); copy(el.dataset.text || ''); break;
    case 'menu': serverMenu(el, el.dataset.id); break;
    case 'rt-menu': runtimeMenu(el, el.dataset.server, el.dataset.id); break;
    case 'detect-on': detect(el.dataset.server, true); break;
    case 'example': {
      const ex = EXAMPLES[Number(el.dataset.i)];
      if (!ex) break;
      draft.url = ex.url;
      const input = $('lm-url');
      if (input) {
        input.value = ex.url;
        input.focus();
        input.select();
      }
      patch($('local-foot'), addFootHtml());
      break;
    }
    case 'reveal':
      revealKey = !revealKey;
      built = '';
      render();
      break;
    case 'paste': {
      const id = el.dataset.for;
      navigator.clipboard?.readText?.().then((t) => {
        const input = $(id);
        if (!input || !t) return;
        input.value = t.trim();
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }).catch(() => {});
      break;
    }
    default:
  }
}

function onInput(e) {
  const el = e.target;
  if (el.id === SEARCH_ID) {
    query = el.value;
    filter();
  } else if (el.id === 'lm-url' || el.id === 'lm-name' || el.id === 'lm-key') {
    draft[el.id.slice(3)] = el.value;
    if (el.id === 'lm-url' && addFailed) {
      addFailed = false;
      addError = '';
      patch($('lm-add-msg'), '');
    }
    patch($('local-foot'), addFootHtml());
  }
}

function onKey(e) {
  if (e.isComposing) return;
  const el = e.target;
  if (view === 'add' && e.key === 'Enter' && el.closest?.('#lm-add-form')) {
    e.preventDefault();
    saveServer(false);
    return;
  }
  if (view) return;
  const search = $(SEARCH_ID);
  const rows = [...($('local-body')?.querySelectorAll('.lm-model:not([hidden])') || [])];
  if (arrowRows(e, search, rows)) return;
  if (e.key === 'Enter' && el === search && rows.length === 1) {
    e.preventDefault();
    rows[0].click();
  }
}

/** Open the dialog. `arg`: '' or 'add'. */
export function openLocal(arg = '') {
  view = String(arg).trim().toLowerCase() === 'add' ? 'add' : null;
  built = '';
  query = '';
  const bar = $('local-bar');
  if (bar) bar.innerHTML = '';
  render();
  const body = $('local-body');
  body?.classList.add('is-entering');
  setTimeout(() => body?.classList.remove('is-entering'), 1400);
  openModal(ID, {
    focus: undefined,
    onClose: () => {
      stopPolling();
      closeMenu();
      view = null;
      busy = '';
      built = '';
    },
  });
  load({ scan: true }).then(() => {
    if (!isModalOpen(ID)) return;
    startPolling();
    if (!view) $(SEARCH_ID)?.focus({ preventScroll: true });
  });
}

/** Another tab or the terminal changed local servers / the model in use. */
export function handleLocalEvent() {
  if (isModalOpen(ID) && !busy && !view) load();
}

export function initLocal() {
  const card = $(ID);
  card?.addEventListener('click', onClick);
  card?.addEventListener('input', onInput);
  card?.addEventListener('keydown', onKey);
  card?.addEventListener('submit', (e) => e.preventDefault());
  card?.addEventListener('toggle', (e) => {
    if (e.target.matches?.('.lm-more')) moreOpen = e.target.open;
  }, true);
}
