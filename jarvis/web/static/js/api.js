/** HTTP + SSE client for the Jarvis web remote */
import { readToken, BASE } from './utils.js';

const token = readToken;

export function hasToken() {
  return !!token();
}

function authHeaders() {
  const t = token();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export async function api(path, method = 'GET', payload) {
  const opts = { method, headers: { ...authHeaders() } };
  if (payload !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(payload);
  }
  const res = await fetch(BASE + path, opts);
  const ct = res.headers.get('content-type') || '';
  const data = ct.includes('application/json') ? await res.json() : await res.text();
  if (!res.ok) {
    const msg = typeof data === 'object' && data?.error ? data.error : res.statusText;
    const err = new Error(msg || `HTTP ${res.status}`);
    err.status = res.status;
    if (typeof data === 'object') err.payload = data;
    throw err;
  }
  return data;
}

let eventSource = null;
let reconnectTimer = null;
let attempt = 0;
let handlers = null;
let lastEventAt = 0;
let hiddenAt = 0;
let paused = false;
let watchdog = 0;
let sseHeardAnything = false;

// Transport: Server-Sent Events normally; long-polling where a tunnel holds
// streams back (Cloudflare quick tunnels do), or once SSE proves silent.
let mode = /\.trycloudflare\.com$/i.test(location.hostname) ? 'poll' : 'sse';
let pollGen = 0;
let pollAbort = null;
let pollConnected = false;
let cursor = '';
const clientId = Math.random().toString(36).slice(2) + Date.now().toString(36);

// SSE: the server pings every 8 s, so this much silence means a dead socket
// (sleep, lock, Wi-Fi switch). Polls return at least every 20 s.
const STALE_MS = { sse: 20_000, poll: 40_000 };
// Background tabs let go of their stream so they don't hold one of the
// browser's ~6 connections per host (new tabs / sends would queue behind them).
const PAUSE_HIDDEN_MS = 45_000;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function deliver(evt) {
  lastEventAt = Date.now();
  if (!evt || evt.type === 'ping') return;
  try {
    handlers.onMessage(evt);
  } catch (err) {
    console.error('event handler failed', evt?.type, err);
  }
}

// ── SSE ──────────────────────────────────────────────────────────────────
function openSse() {
  sseHeardAnything = false;
  eventSource = new EventSource(`${BASE}/api/events?token=${encodeURIComponent(token())}`);
  eventSource.onopen = () => {
    attempt = 0;
    lastEventAt = Date.now();
    handlers.onConnect?.();
  };
  eventSource.onmessage = (ev) => {
    sseHeardAnything = true;
    let evt;
    try {
      evt = JSON.parse(ev.data);
    } catch {
      return;
    }
    deliver(evt);
  };
  eventSource.onerror = () => {
    // Ask the server first: a project that was closed is not a lost connection.
    eventSource?.close();
    eventSource = null;
    scheduleReconnect({ reportDown: true });
  };
}

// ── Long polling ─────────────────────────────────────────────────────────
async function pollLoop(gen) {
  while (gen === pollGen && !paused) {
    try {
      pollAbort = new AbortController();
      const url = `${BASE}/api/poll?cid=${clientId}&cursor=${encodeURIComponent(cursor)}`;
      const res = await fetch(url, { headers: authHeaders(), signal: pollAbort.signal, cache: 'no-store' });
      if (gen !== pollGen) return;
      if (res.status === 401) {
        handlers.onUnauthorized?.();
        return;
      }
      if (projectGone(res)) {
        handlers.onGone?.();
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      if (gen !== pollGen) return;
      if (body.closed) throw new Error('remote stopped');
      if (!pollConnected) {
        pollConnected = true;
        attempt = 0;
        handlers.onConnect?.();
      }
      lastEventAt = Date.now();
      if (body.resync) {
        // Fell behind the server's log: start over from a fresh snapshot.
        cursor = '';
        handlers.onResync?.();
        continue;
      }
      for (const evt of body.events || []) deliver(evt);
      cursor = String(body.cursor ?? cursor);
    } catch (err) {
      if (gen !== pollGen) return;
      if (pollConnected) {
        pollConnected = false;
        handlers.onDisconnect?.();
      }
      cursor = '';
      attempt += 1;
      await sleep(Math.min(10_000, 500 * 2 ** Math.min(attempt, 4)));
    }
  }
}

function openPoll() {
  pollGen += 1;
  pollConnected = false;
  cursor = '';
  lastEventAt = Date.now();
  pollLoop(pollGen);
}

// ── Shared lifecycle ─────────────────────────────────────────────────────
function openTransport() {
  dropTransport({ quiet: true });
  clearTimeout(reconnectTimer);
  lastEventAt = Date.now();
  if (mode === 'poll') openPoll();
  else openSse();
}

function dropTransport({ quiet = false } = {}) {
  const wasOpen = !!eventSource || pollConnected;
  eventSource?.close();
  eventSource = null;
  pollGen += 1;
  pollAbort?.abort();
  pollAbort = null;
  pollConnected = false;
  if (!quiet || wasOpen) handlers.onDisconnect?.();
}

// Requests that never reached the server in a row (it is gone, not just slow).
let unreachable = 0;

/** The shown project closed (its terminal quit): this server says so instead of answering. */
const projectGone = (res) => !!BASE && (res.status === 404 || res.status === 502);

async function scheduleReconnect({ reportDown = false } = {}) {
  clearTimeout(reconnectTimer);
  const base = BASE;
  const down = reportDown;
  try {
    const res = await fetch(`${base}/api/state`, { headers: authHeaders() });
    if (base !== BASE) return; // switched project meanwhile: that switch reconnected
    unreachable = 0;
    if (res.status === 401) {
      handlers.onUnauthorized?.();
      return;
    }
    if (projectGone(res)) {
      handlers.onGone?.();
      return;
    }
  } catch {
    // The server this page came from is down (its terminal closed?). Keep
    // retrying — it may come back — and let the page look for another Jarvis.
    if (base !== BASE) return;
    unreachable += 1;
    if (unreachable === 2) handlers.onServerLost?.();
  }
  if (down) handlers.onDisconnect?.();
  attempt += 1;
  const delay = Math.min(10_000, 500 * 2 ** Math.min(attempt, 4));
  reconnectTimer = setTimeout(() => {
    if (!paused) openTransport();
  }, delay);
}

/**
 * Another project is now shown (`BASE` changed): close the old stream without
 * reporting a lost connection and open one to the new project.
 */
export function switchTransport() {
  if (!handlers) return;
  attempt = 0;
  unreachable = 0;
  clearTimeout(reconnectTimer);
  eventSource?.close();
  eventSource = null;
  pollGen += 1;
  pollAbort?.abort();
  pollAbort = null;
  pollConnected = false;
  if (!paused) openTransport();
}

/** Drop whatever we have and reconnect now (fresh snapshot included). */
export function reconnectNow() {
  if (paused || !handlers) return;
  attempt = 0;
  dropTransport();
  openTransport();
}

function checkStale() {
  if (paused || !handlers) return;
  const open = mode === 'sse' ? !!eventSource : true;
  if (!open || Date.now() - lastEventAt <= STALE_MS[mode]) return;
  if (mode === 'sse' && !sseHeardAnything) {
    // Connected but not even the first snapshot came through: something in
    // between (a tunnel / proxy) buffers streams. Long-poll from now on.
    mode = 'poll';
  }
  reconnectNow();
}

function onVisibility() {
  if (document.hidden) {
    hiddenAt = Date.now();
    return;
  }
  const away = hiddenAt ? Date.now() - hiddenAt : 0;
  hiddenAt = 0;
  if (paused) {
    paused = false;
    openTransport();
  } else if (away > 5_000 || Date.now() - lastEventAt > 12_000) {
    // Timers are throttled in the background: we may have missed events.
    reconnectNow();
  }
}

/**
 * Open the live connection and keep it open: reconnects with backoff,
 * restarts a silent stream, falls back to long-polling when streams are
 * buffered, pauses in long-hidden tabs and resyncs when they come back.
 * Stops when the server rejects the token (a new `jarvis --web` run issues a new one).
 */
export function connectEvents(h) {
  handlers = h;
  if (!hasToken()) {
    h.onUnauthorized?.();
    return;
  }
  openTransport();
  clearInterval(watchdog);
  watchdog = setInterval(() => {
    checkStale();
    if (document.hidden && hiddenAt && !paused && Date.now() - hiddenAt > PAUSE_HIDDEN_MS) {
      paused = true;
      dropTransport();
    }
  }, 4_000);
  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('online', reconnectNow);
  window.addEventListener('pageshow', (e) => { if (e.persisted) reconnectNow(); });
}

/** Which transport is live ("sse" | "poll") — for diagnostics. */
export function transportMode() {
  return mode;
}

/** `attachments`: ids of files already uploaded (media.js) — the server checks they still exist. */
export const sendPrompt = (text, attachments = []) =>
  api('/api/prompt', 'POST', attachments.length ? { text, attachments } : { text });
export const cancelTurn = () => api('/api/cancel', 'POST', {});
/** Fix spelling / grammar with the current model → `{ ok, text, changed }` or `{ ok: false, error }`. */
export const enhancePrompt = (text) => api('/api/enhance', 'POST', { text });
export const respondPrompt = (id, result) => api('/api/respond', 'POST', { id, result });
export const updateSettings = (patch) => api('/api/settings', 'POST', patch);
export const fetchState = () => api('/api/state');

/** Picker mutation. Always resolves to `{ok, error?, state?}`. */
export async function pickerAction(action, data = {}) {
  try {
    const res = await fetch(`${BASE}/api/action`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, data }),
    });
    const body = await res.json().catch(() => null);
    if (!body || typeof body !== 'object') return { ok: false, error: res.statusText || 'Invalid response' };
    return body;
  } catch {
    return { ok: false, error: 'Jarvis is not reachable' };
  }
}

export const fetchSessions = (limit = 50) => api(`/api/sessions?limit=${limit}`);

/** Full recorded output for one tool call → `{ id, name, args, output, chars, truncated }`. */
export const fetchToolOutput = (id) => api(`/api/tool-output?id=${encodeURIComponent(id)}`);

export function fetchModels(q = '') {
  return api(`/api/models${q ? `?q=${encodeURIComponent(q)}` : ''}`);
}

export function fetchAgents(includeGlobal) {
  const qs = includeGlobal === undefined ? '' : `?include_global=${includeGlobal ? '1' : '0'}`;
  return api(`/api/agents${qs}`);
}

export function fetchSkills(includeGlobal, q = '') {
  const params = new URLSearchParams();
  if (includeGlobal !== undefined) params.set('include_global', includeGlobal ? '1' : '0');
  if (q) params.set('q', q);
  const qs = params.toString();
  return api(`/api/skills${qs ? `?${qs}` : ''}`);
}

export const fetchSkill = (name) => api(`/api/skills/${encodeURIComponent(name)}`);

/** Custom slash commands (`/api/commands`): the slash menu, palette and /command dialog. */
export const fetchCommands = () => api('/api/commands');

export function fetchMcpServers(q = '') {
  return api(`/api/mcp${q ? `?q=${encodeURIComponent(q)}` : ''}`);
}

// ─── Skills and MCP: add, sign in, remove (jarvis/web/extensions_api.py) ───
/** Where a browser sign-in stands → `{ connected, status: pending|working|done|error|cancelled|none, url?, message?, expires_in? }`. */
export const fetchMcpAuth = (name) => api(`/api/mcp/auth?name=${encodeURIComponent(name)}`);

/** `path` is mcp/parse|add|remove|move|connect|disconnect|signout|credentials, mcp/auth/start|paste|cancel,
 * skills/inspect|install|remove|move|update. Always resolves to `{ok, error?, …}` (never throws);
 * the reply also carries the fresh `mcp` / `skills` list. */
export async function extPost(path, data = {}) {
  try {
    const res = await fetch(`${BASE}/api/${path}`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const body = await res.json().catch(() => null);
    if (!body || typeof body !== 'object') return { ok: false, error: res.statusText || 'Invalid response' };
    return body;
  } catch {
    return { ok: false, error: 'Jarvis is not reachable' };
  }
}

// ─── Providers and login (jarvis/web/providers_api.py) ────────────────────
export const fetchProviders = () => api('/api/providers');
export const fetchOAuthStatus = (flow) => api(`/api/providers/oauth?flow=${encodeURIComponent(flow)}`);

/** `path` is key · key/remove · use · signout · oauth/start|finish|cancel.
 * Always resolves to `{ok, error?, message?, providers?, state?}`. */
export async function providerPost(path, data = {}) {
  try {
    const res = await fetch(`${BASE}/api/providers/${path}`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const body = await res.json().catch(() => null);
    if (!body || typeof body !== 'object') return { ok: false, error: res.statusText || 'Invalid response' };
    return body;
  } catch {
    return { ok: false, error: 'Jarvis is not reachable' };
  }
}

// ─── Pinned context + the message queue ───────────────────────────────────
/** `{ text, items: [{line, text}], enabled, lines, chars, file }` (jarvis/web/pin_api.py). */
export const fetchPin = () => api('/api/pin');

/** Every running Jarvis (the project switcher). Asked of the server the page was loaded
 * from, not of the project being shown — hence no `BASE`. */
export async function fetchProjects() {
  const res = await fetch('/api/projects', { headers: authHeaders(), cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** A project's snapshot before switching to it (`base`: "" or "/p/<id>"). Errors carry `status`. */
export async function fetchStateAt(base, { signal } = {}) {
  const res = await fetch(`${base}/api/state`, { headers: authHeaders(), cache: 'no-store', signal });
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/** `op`: add · save · update · remove · toggle · clear → `{ ok, error?, code?, pin? }` (never throws). */
export const pinPost = (data) => extPost('pin', data);

/** A queued message: `op` edit · remove · steer (send now) · unsteer · clear →
 * `{ ok, error?, code?, items, entries }` (never throws). */
export const queuePost = (data) => extPost('queue', data);
