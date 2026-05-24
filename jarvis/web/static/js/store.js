/** Central reactive store for session + UI preferences */
const listeners = new Set();

export const store = {
  busy: false,
  connected: false,
  session: {
    model: '',
    agent: '',
    session_id: '',
    provider: '',
    think_mode: true,
    think_effort: 'medium',
    show_internal: true,
    auto_approve: false,
    tokens_in: 0,
    tokens_out: 0,
    tokens_total: 0,
    tool_calls: 0,
  },
  /** Client-side: show thinking bubbles in chat */
  showThinkingUi: true,
  queue: [],
  activePrompt: null,
  pendingAction: false,
  pendingToggle: null,
};

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

function notify() {
  for (const fn of listeners) fn(store);
}

export function loadSnapshot(data) {
  patchSession({
    model: data.model || '',
    agent: data.agent || '',
    session_id: data.session_id || '',
    provider: data.provider || '',
    think_mode: data.think_mode !== false,
    think_effort: data.think_effort || 'medium',
    show_internal: data.show_internal !== false,
    auto_approve: !!data.auto_approve,
    tokens_in: data.tokens_in || 0,
    tokens_out: data.tokens_out || 0,
    tokens_total: data.tokens_total || 0,
    tool_calls: data.tool_calls || 0,
  });
  patchStore({
    busy: !!data.busy,
    queue: data.queue || [],
  });
}

export function loadSettingsEvent(data) {
  patchSession(data);
}

export function applySettingsResponse(settings) {
  if (!settings) return;
  patchSession({
    think_mode: settings.think_mode !== false,
    think_effort: settings.think_effort || 'medium',
    show_internal: settings.show_internal !== false,
    auto_approve: !!settings.auto_approve,
    model: settings.model || store.session.model,
    agent: settings.agent || store.session.agent,
    session_id: settings.session_id || store.session.session_id,
    tokens_in: settings.tokens_in || 0,
    tokens_out: settings.tokens_out || 0,
    tokens_total: settings.tokens_total || 0,
    tool_calls: settings.tool_calls || 0,
  });
}
