/** HTTP + SSE client for the Jarvis web remote */
import { readToken } from './utils.js';

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
  const res = await fetch(path, opts);
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
  eventSource = new EventSource(`/api/events?token=${encodeURIComponent(token())}`);
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
    dropTransport();
    scheduleReconnect();
  };
}

// ── Long polling ─────────────────────────────────────────────────────────
async function pollLoop(gen) {
  while (gen === pollGen && !paused) {
    try {
      pollAbort = new AbortController();
      const url = `/api/poll?cid=${clientId}&cursor=${encodeURIComponent(cursor)}`;
      const res = await fetch(url, { headers: authHeaders(), signal: pollAbort.signal, cache: 'no-store' });
      if (gen !== pollGen) return;
      if (res.status === 401) {
        handlers.onUnauthorized?.();
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

async function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  try {
    const res = await fetch('/api/state', { headers: authHeaders() });
    if (res.status === 401) {
      handlers.onUnauthorized?.();
      return;
    }
  } catch { /* server down — keep retrying */ }
  attempt += 1;
  const delay = Math.min(10_000, 500 * 2 ** Math.min(attempt, 4));
  reconnectTimer = setTimeout(() => {
    if (!paused) openTransport();
  }, delay);
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

export const sendPrompt = (text) => api('/api/prompt', 'POST', { text });
export const cancelTurn = () => api('/api/cancel', 'POST', {});
export const respondPrompt = (id, result) => api('/api/respond', 'POST', { id, result });
export const updateSettings = (patch) => api('/api/settings', 'POST', patch);
export const fetchState = () => api('/api/state');

/** Picker mutation. Always resolves to `{ok, error?, state?}`. */
export async function pickerAction(action, data = {}) {
  try {
    const res = await fetch('/api/action', {
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

export function fetchMcpServers(q = '') {
  return api(`/api/mcp${q ? `?q=${encodeURIComponent(q)}` : ''}`);
}
