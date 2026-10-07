/** Thinking effort levels (mirrors jarvis.constants.THINK_EFFORTS, highest first).
 *  Which of them the current model takes comes from the server: `session.think`
 *  (jarvis.repl.thinking.public) — models differ, so nothing here assumes. */
export const EFFORTS = ['ultra', 'max', 'xhigh', 'high', 'medium', 'low', 'minimal', 'none'];
/** What a model nothing is known about is offered (the pre-discovery list). */
export const LEGACY_LEVELS = ['xhigh', 'high', 'medium', 'low', 'minimal'];

/** Short labels: the segmented control in the sidebar. */
export const EFFORT_LABELS = {
  ultra: 'Ultra',
  max: 'Max',
  xhigh: 'XHigh',
  high: 'High',
  medium: 'Med',
  low: 'Low',
  minimal: 'Min',
  none: 'Off',
  on: 'On',
};

/** Full names: the composer menu and chip. */
export const EFFORT_NAMES = {
  ultra: 'Ultra',
  max: 'Max',
  xhigh: 'XHigh',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  minimal: 'Minimal',
  none: 'Off',
  on: 'On',
};

export const EFFORT_HINTS = {
  ultra: 'Maximum reasoning plus delegation',
  max: 'Deepest reasoning, slowest',
  xhigh: 'Extra-high reasoning',
  high: 'Strong reasoning',
  medium: 'Balanced reasoning',
  low: 'Light thinking',
  minimal: 'Brief passes only',
  none: 'Thinking off',
  on: 'Thinking on (this model has no levels)',
};

/** The server's description of what this model takes, or null on an old server. */
export function thinkInfo(session) {
  const t = session?.think;
  return t && typeof t === 'object' && Array.isArray(t.choices) ? t : null;
}

/** Rows for the composer menu: `{value, label, hint, available, why}`. Falls
 *  back to the legacy list when the server doesn't describe the model. */
export function effortRows(session) {
  const info = thinkInfo(session);
  if (!info) {
    return [...LEGACY_LEVELS, 'none'].map((value) => ({
      value, label: EFFORT_NAMES[value], hint: EFFORT_HINTS[value], available: true, why: '',
    }));
  }
  return info.choices.map((c) => ({
    value: c.value,
    label: EFFORT_NAMES[c.value] || c.value,
    hint: c.available ? (c.detail || EFFORT_HINTS[c.value] || '') : (c.why || 'Not supported by this model'),
    available: c.available !== false,
    why: c.why || '',
  }));
}

/** Levels the sidebar's segmented control shows, highest first ([] = no levels). */
export function effortLevels(session) {
  const info = thinkInfo(session);
  if (!info) return [...LEGACY_LEVELS];
  if (info.mode === 'unknown') return [...LEGACY_LEVELS];
  return EFFORTS.filter((e) => e !== 'none' && (info.levels || []).includes(e));
}

/** What the current model will really use: `{on, level, label, adjusted, note}`. */
export function effortNow(session) {
  const info = thinkInfo(session);
  if (!info) {
    const on = !!session.think_mode && session.think_effort !== 'none';
    return { on, level: on ? session.think_effort : '', label: on ? session.think_effort : 'none', adjusted: false, note: '' };
  }
  return {
    on: !!info.on,
    level: info.effort || '',
    label: info.on ? (info.effort || 'on') : 'none',
    adjusted: !!info.note,
    note: info.note || '',
  };
}

/** The model has no thinking, or thinking can't be turned off (the switch). */
export function thinkSwitchState(session) {
  const info = thinkInfo(session);
  if (!info || info.mode === 'unknown') return { disabled: false, why: '' };
  if (info.mode === 'none') return { disabled: true, why: "This model doesn't support thinking" };
  if (!info.can_off) return { disabled: true, why: "This model always thinks — it can't be switched off" };
  return { disabled: false, why: '' };
}
