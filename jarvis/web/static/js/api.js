/** HTTP + SSE client for Jarvis web remote */
import { readToken } from './utils.js';

function token() {
  return readToken();
}

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
  const isJson = ct.includes('application/json');
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    const msg = typeof data === 'object' && data?.error ? data.error : (typeof data === 'string' ? data : res.statusText);
    const err = new Error(msg || res.statusText);
    if (typeof data === 'object') err.payload = data;
    throw err;
  }
  return data;
}

let eventSource = null;
let reconnectTimer = null;

export function connectEvents(onMessage, onConnect, onDisconnect) {
  if (!hasToken()) {
    onDisconnect?.();
    return null;
  }

  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  const url = `/api/events?token=${encodeURIComponent(token())}`;
  eventSource = new EventSource(url);

  eventSource.onopen = () => onConnect?.();
  eventSource.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch (_) { /* ignore malformed */ }
  };
  eventSource.onerror = () => {
    onDisconnect?.();
    eventSource?.close();
    eventSource = null;
    reconnectTimer = setTimeout(
      () => connectEvents(onMessage, onConnect, onDisconnect),
      2000,
    );
  };

  return eventSource;
}

export async function sendPrompt(text) {
  return api('/api/prompt', 'POST', { text });
}

export async function cancelTurn() {
  return api('/api/cancel', 'POST', {});
}

export async function respondPrompt(id, result) {
  return api('/api/respond', 'POST', { id, result });
}

export async function updateSettings(patch) {
  return api('/api/settings', 'POST', patch);
}

export async function fetchState() {
  return api('/api/state');
}

export async function pickerAction(action, data = {}) {
  const opts = {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, data }),
  };
  const res = await fetch('/api/action', opts);
  let body = {};
  try {
    body = await res.json();
  } catch {
    body = { ok: false, error: res.statusText || 'Invalid response' };
  }
  if (!body || typeof body !== 'object') {
    return { ok: false, error: 'Invalid response' };
  }
  return body;
}

export async function fetchSessions() {
  return api('/api/sessions');
}

export async function fetchModels(q = '') {
  const qs = q ? `?q=${encodeURIComponent(q)}` : '';
  return api(`/api/models${qs}`);
}

export async function fetchAgents(includeGlobal) {
  const qs = includeGlobal === undefined ? '' : `?include_global=${includeGlobal ? '1' : '0'}`;
  return api(`/api/agents${qs}`);
}

export async function fetchSkills(includeGlobal, q = '') {
  const params = new URLSearchParams();
  if (includeGlobal !== undefined) params.set('include_global', includeGlobal ? '1' : '0');
  if (q) params.set('q', q);
  const qs = params.toString() ? `?${params}` : '';
  return api(`/api/skills${qs}`);
}

export async function fetchSkill(name) {
  return api(`/api/skills/${encodeURIComponent(name)}`);
}

export async function fetchMcpServers(q = '') {
  const qs = q ? `?q=${encodeURIComponent(q)}` : '';
  return api(`/api/mcp${qs}`);
}
