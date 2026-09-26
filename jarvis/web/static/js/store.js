/** Central reactive store for session state + UI preferences */
import { storageGet } from './utils.js';

const listeners = new Set();

export const store = {
  busy: false,
  connected: false,
  session: {
    model: '',
    agent: '',
    session_id: '',
    session_title: '',
    project: '',
    provider: '',
    think_mode: true,
    think_effort: 'high',
    show_internal: true,
    auto_approve: false,
    global_agents: false,
    global_skills: false,
    global_mcp: false,
    tokens_in: 0,
    tokens_out: 0,
    tokens_total: 0,
    tool_calls: 0,
    message_count: 0,
  },
  /** This device only: show thinking in the transcript */
  showThoughts: true,
  queue: [],
  /** Pending agent prompt (approval / question / input) awaiting an answer */
  activePrompt: null,
  pendingToggle: null,
  statusLabel: '',
  busySince: 0,
};

let scheduled = false;

export function patchStore(partial) {
  Object.assign(store, partial);
  notify();
}

export function patchSession(partial) {
  Object.assign(store.session, partial);
  notify();
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Batch listener calls into one per frame — events arrive in bursts. */
function notify() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    for (const fn of listeners) fn(store);
  });
}

const SESSION_KEYS = Object.keys(store.session);

/** Merge a snapshot / settings payload — only keys that are present. */
export function loadSnapshot(data) {
  if (!data || typeof data !== 'object') return;
  if (data.remote_url) store.remoteUrl = data.remote_url;
  const patch = {};
  for (const key of SESSION_KEYS) {
    if (key in data && data[key] !== undefined && data[key] !== null) patch[key] = data[key];
  }
  if (Object.keys(patch).length) patchSession(patch);
  if ('queue' in data) patchStore({ queue: data.queue || [] });
}

export function loadUiPrefs() {
  store.showThoughts = storageGet('jarvis-show-thinking', '1') !== '0';
}
