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

// The server sends a ping every 8 s; this much silence means the socket is
// dead even though the browser never fired an error (sleep, lock, Wi-Fi).
const STALE_MS = 20_000;
// Background tabs let go of their stream so they don't hold one of the
// browser's ~6 connections per host (new tabs / sends would queue behind them).
const PAUSE_HIDDEN_MS = 45_000;

function openStream() {
  eventSource?.close();
  clearTimeout(reconnectTimer);
  lastEventAt = Date.now();

  eventSource = new EventSource(`/api/events?token=${encodeURIComponent(token())}`);
  eventSource.onopen = () => {
    attempt = 0;
    lastEventAt = Date.now();
    handlers.onConnect?.();
  };
  eventSource.onmessage = (ev) => {
    lastEventAt = Date.now();
    let evt;
    try {
      evt = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (evt?.type === 'ping') return;
    try {
      handlers.onMessage(evt);
    } catch (err) {
      console.error('event handler failed', evt?.type, err);
    }
  };
  eventSource.onerror = () => {
    dropStream();
    scheduleReconnect();
  };
}

function dropStream() {
  eventSource?.close();
  eventSource = null;
  handlers.onDisconnect?.();
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
    if (!paused) openStream();
  }, delay);
}

/** Drop whatever we have and reconnect now (fresh snapshot included). */
export function reconnectNow() {
  if (paused || !handlers) return;
  attempt = 0;
  dropStream();
  openStream();
}

function checkStale() {
  if (!eventSource || paused) return;
  if (Date.now() - lastEventAt > STALE_MS) reconnectNow();
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
    openStream();
  } else if (away > 5_000 || Date.now() - lastEventAt > 12_000) {
    // Timers are throttled in the background: we may have missed events.
    reconnectNow();
  }
}

/**
 * Open the SSE stream and keep it open: reconnects with backoff, restarts a
 * silent stream, pauses in long-hidden tabs and resyncs when they come back.
 * Stops when the server rejects the token (a new `jarvis --web` run issues a new one).
 */
export function connectEvents(h) {
  handlers = h;
  if (!hasToken()) {
    h.onUnauthorized?.();
    return;
  }
  openStream();
  clearInterval(watchdog);
  watchdog = setInterval(() => {
    checkStale();
    if (document.hidden && hiddenAt && !paused && Date.now() - hiddenAt > PAUSE_HIDDEN_MS) {
      paused = true;
      dropStream();
    }
  }, 4_000);
  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('online', reconnectNow);
  window.addEventListener('pageshow', (e) => { if (e.persisted) reconnectNow(); });
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
