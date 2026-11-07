/** Keyboard shortcuts — one table of every key the web remote answers to, and
 * the user's own changes to it (saved in this browser).
 *
 *   matches(e, 'chat.new')       → does this keydown trigger "New chat"?
 *   matchAny(e, ['a', 'b'])      → the first of those ids it triggers, or ''
 *   bindingsOf(id)               → ['Alt+N']   (the user's keys, or the defaults)
 *   keyCaps('Mod+Shift+P')       → ['⌘', '⇧', 'P']   (for <kbd>s)
 *   hint(id)                     → '⌥N' / 'Alt+N' — for titles and menus ('' when unbound)
 *   setBindings / resetBindings / resetAll / onKeymapChange
 *
 * A binding is "Mod+Shift+P": modifiers then one key. `Mod` is ⌘ on a Mac and
 * Ctrl elsewhere; `Ctrl`, `Meta`, `Alt` and `Shift` mean exactly that key. Keys
 * are named after the physical key (e.code: `KeyN` → N, `Slash` → /), because
 * ⌥ on a Mac turns letters into symbols (⌥N → ˜); a key that only exists on the
 * user's layout still matches by the character it types.
 *
 * Pure module (no DOM besides localStorage / navigator, both optional), so it
 * runs under Node for tests.
 */

const STORE_KEY = 'jarvis-keymap';
const STORE_VERSION = 1;

export const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');

// ─── The table ────────────────────────────────────────────────────────────
// scope: where the key works — 'global' (anywhere on the page), 'editor' (the
//   Files editor), 'files' (the Files drawer), 'commit' (its commit message),
//   'terminal' (inside a shell).
// fixed: shown in the list, not changeable (typing keys, Esc, Enter …).
// plain: may be a key without ⌘ / Ctrl / ⌥ (it only fires outside text fields).

export const GROUPS = ['General', 'Chat', 'Panels', 'Files', 'Editor', 'Terminal'];

export const ACTIONS = [
  // General
  { id: 'app.palette', group: 'General', label: 'Command palette', def: IS_MAC ? ['Mod+K', 'Ctrl+K'] : ['Mod+K'] },
  { id: 'app.shortcuts', group: 'General', label: 'Keyboard shortcuts list', def: ['?'], plain: true },
  { id: 'app.settings', group: 'General', label: 'Settings', def: ['Alt+,'] },
  { id: 'app.theme', group: 'General', label: 'Light or dark mode', def: ['Alt+T'] },
  { id: 'app.fullscreen', group: 'General', label: 'Full screen', def: ['Alt+Shift+Enter'] },
  { id: 'app.stop', group: 'General', label: 'Stop Jarvis, or close a dialog', def: ['Esc'], fixed: true },
  { id: 'app.project', group: 'General', label: 'Switch to workspace project 1–9 (starts it if it is stopped)', def: ['Alt+1…9'], fixed: true },
  { id: 'app.projectPick', group: 'General', label: 'Pick a project (⌘K, projects only)', def: ['Alt+0'], fixed: true },

  // Chat
  { id: 'chat.new', group: 'Chat', label: 'New chat', def: ['Alt+N'] },
  { id: 'chat.newTab', group: 'Chat', label: 'New chat tab', def: ['Alt+Shift+N'] },
  { id: 'chat.nextTab', group: 'Chat', label: 'Next chat tab', def: ['Alt+]'] },
  { id: 'chat.prevTab', group: 'Chat', label: 'Previous chat tab', def: ['Alt+['] },
  { id: 'chat.rename', group: 'Chat', label: 'Rename this chat', def: [] },
  { id: 'chat.focus', group: 'Chat', label: 'Focus the message box', def: ['Alt+/'] },
  { id: 'chat.attach', group: 'Chat', label: 'Attach files', def: [] },
  { id: 'chat.enhance', group: 'Chat', label: 'Enhance: fix spelling and grammar of your message (again to undo)', def: ['Alt+E'] },
  { id: 'chat.model', group: 'Chat', label: 'Switch model', def: ['Alt+M'] },
  { id: 'chat.sessions', group: 'Chat', label: 'Sessions', def: ['Alt+S'] },
  { id: 'chat.agents', group: 'Chat', label: 'Agents', def: ['Alt+A'] },
  { id: 'chat.skills', group: 'Chat', label: 'Skills', def: [] },
  { id: 'chat.commands', group: 'Chat', label: 'Custom commands', def: [] },
  { id: 'chat.mcp', group: 'Chat', label: 'MCP servers', def: [] },
  { id: 'chat.memory', group: 'Chat', label: 'Memory', def: [] },
  { id: 'chat.providers', group: 'Chat', label: 'Providers', def: [] },
  { id: 'chat.send', group: 'Chat', label: 'Send the message', def: ['Enter'], fixed: true },
  { id: 'chat.newline', group: 'Chat', label: 'New line', def: ['Shift+Enter'], fixed: true },
  { id: 'chat.history', group: 'Chat', label: 'Previous message (empty box)', def: ['Up'], fixed: true },
  { id: 'chat.slash', group: 'Chat', label: 'Slash commands (type in the message box)', def: ['/'], fixed: true },

  // Panels
  { id: 'panel.sidebar', group: 'Panels', label: 'Show or hide the sidebar', def: ['Alt+B'] },
  { id: 'panel.changes', group: 'Panels', label: 'Show or hide the changes panel', def: ['Alt+D'] },
  { id: 'panel.activity', group: 'Panels', label: 'Activity panel', def: [] },
  { id: 'panel.usage', group: 'Panels', label: 'Usage stats', def: [] },

  // Files (the drawer)
  { id: 'files.open', group: 'Files', label: 'Browse and edit the project\u2019s files', def: ['Alt+F'] },
  { id: 'files.searchCode', group: 'Files', label: 'Search code in every file', def: ['Alt+Shift+F'] },
  { id: 'files.findFile', group: 'Files', label: 'Find a file by name', def: ['Mod+Shift+P'] },
  { id: 'files.searchInDrawer', group: 'Files', label: 'Search code (inside Files; the selection is the query)', def: ['Mod+Shift+F'], scope: 'files' },
  { id: 'files.save', group: 'Files', label: 'Save the file', def: ['Mod+S'], scope: 'files' },
  { id: 'files.closeTab', group: 'Files', label: 'Close the file tab', def: ['Alt+W'], scope: 'files' },
  { id: 'files.find', group: 'Files', label: 'Find in the file', def: ['Mod+F'], scope: 'files' },
  { id: 'files.findNext', group: 'Files', label: 'Next match', def: ['Mod+G', 'F3'], scope: 'files' },
  { id: 'files.findPrev', group: 'Files', label: 'Previous match', def: ['Mod+Shift+G', 'Shift+F3'], scope: 'files' },
  { id: 'files.commit', group: 'Files', label: 'Commit', def: ['Mod+Enter'], scope: 'commit' },

  // Editor (the Files editor)
  { id: 'edit.comment', group: 'Editor', label: 'Comment or uncomment lines', def: ['Mod+/'], scope: 'editor' },
  { id: 'edit.blockComment', group: 'Editor', label: 'Block comment', def: ['Alt+Shift+A'], scope: 'editor' },
  { id: 'edit.indent', group: 'Editor', label: 'Indent lines', def: ['Mod+]'], scope: 'editor' },
  { id: 'edit.outdent', group: 'Editor', label: 'Outdent lines', def: ['Mod+['], scope: 'editor' },
  { id: 'edit.moveUp', group: 'Editor', label: 'Move line up', def: ['Alt+Up'], scope: 'editor' },
  { id: 'edit.moveDown', group: 'Editor', label: 'Move line down', def: ['Alt+Down'], scope: 'editor' },
  { id: 'edit.copyUp', group: 'Editor', label: 'Copy line up', def: ['Alt+Shift+Up'], scope: 'editor' },
  { id: 'edit.copyDown', group: 'Editor', label: 'Copy line down', def: ['Alt+Shift+Down'], scope: 'editor' },
  { id: 'edit.deleteLine', group: 'Editor', label: 'Delete line', def: ['Mod+Shift+K'], scope: 'editor' },
  { id: 'edit.lineBelow', group: 'Editor', label: 'Insert line below', def: ['Mod+Enter'], scope: 'editor' },
  { id: 'edit.lineAbove', group: 'Editor', label: 'Insert line above', def: ['Mod+Shift+Enter'], scope: 'editor' },
  { id: 'edit.selectLine', group: 'Editor', label: 'Select line (again to add the next)', def: [], scope: 'editor' },
  { id: 'edit.nextChange', group: 'Editor', label: 'Next change', def: ['Alt+F3'], scope: 'editor' },
  { id: 'edit.prevChange', group: 'Editor', label: 'Previous change', def: ['Alt+Shift+F3'], scope: 'editor' },
  { id: 'edit.tab', group: 'Editor', label: 'Indent (selection) or insert a tab', def: ['Tab'], scope: 'editor', fixed: true },
  { id: 'edit.untab', group: 'Editor', label: 'Outdent the selection', def: ['Shift+Tab'], scope: 'editor', fixed: true },

  // Terminal
  { id: 'term.toggle', group: 'Terminal', label: 'Show, focus or hide the terminal', def: ['Ctrl+`'] },
  { id: 'term.new', group: 'Terminal', label: 'New shell', def: ['Ctrl+Shift+`'] },
  { id: 'term.all', group: 'Terminal', label: 'All terminals', def: ['Alt+Shift+T'] },
  { id: 'term.scripts', group: 'Terminal', label: 'Run a package.json script (npm, pnpm, yarn, bun) in the terminal', def: ['Alt+Shift+R'] },
  { id: 'term.max', group: 'Terminal', label: 'Maximize or restore the terminal', def: [] },
  { id: 'term.split', group: 'Terminal', label: 'Split the terminal', def: [] },
  { id: 'term.clear', group: 'Terminal', label: 'Clear the terminal', def: [], scope: 'terminal' },
  { id: 'term.find', group: 'Terminal', label: 'Find in the terminal', def: IS_MAC ? ['Mod+F'] : [], scope: 'terminal' },
  { id: 'term.copy', group: 'Terminal', label: 'Copy the selection', def: IS_MAC ? ['Mod+Shift+C', 'Ctrl+Shift+C'] : ['Ctrl+Shift+C'], scope: 'terminal' },
  { id: 'term.paste', group: 'Terminal', label: 'Paste', def: IS_MAC ? ['Mod+Shift+V', 'Ctrl+Shift+V'] : ['Ctrl+Shift+V'], scope: 'terminal' },
  { id: 'term.ai', group: 'Terminal', label: 'Write a command with AI', def: IS_MAC ? ['Mod+K'] : ['Ctrl+Shift+K'], scope: 'terminal' },
];

const BY_ID = new Map(ACTIONS.map((a) => [a.id, a]));

export function actionOf(id) {
  return BY_ID.get(id) || null;
}

export const scopeOf = (a) => a?.scope || 'global';

// ─── Key names ────────────────────────────────────────────────────────────

const CODE_KEY = {
  Slash: '/', Backslash: '\\', Comma: ',', Period: '.', Semicolon: ';', Quote: "'", BracketLeft: '[', BracketRight: ']',
  Minus: '-', Equal: '=', Backquote: '`', IntlBackslash: '\\',
  ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right',
  Enter: 'Enter', NumpadEnter: 'Enter', Escape: 'Esc', Tab: 'Tab', Space: 'Space', Backspace: 'Backspace', Delete: 'Delete',
  Home: 'Home', End: 'End', PageUp: 'PageUp', PageDown: 'PageDown', Insert: 'Insert',
};
const KEY_NAME = {
  ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right', Escape: 'Esc', Esc: 'Esc', ' ': 'Space',
  Enter: 'Enter', Tab: 'Tab', Backspace: 'Backspace', Delete: 'Delete', Home: 'Home', End: 'End', PageUp: 'PageUp', PageDown: 'PageDown',
};
const MODIFIER_KEYS = new Set(['Shift', 'Control', 'Alt', 'Meta', 'AltGraph', 'CapsLock', 'Fn', 'FnLock', 'Hyper', 'Super', 'OS']);
const MOD_ORDER = ['Ctrl', 'Meta', 'Alt', 'Shift'];

/** The physical key's name: KeyN → N, Digit1 → 1, Slash → /, F3 → F3 ('' if unknown). */
function keyFromCode(code) {
  if (!code) return '';
  let m = /^Key([A-Z])$/.exec(code);
  if (m) return m[1];
  m = /^(?:Digit|Numpad)([0-9])$/.exec(code);
  if (m) return m[1];
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code;
  return CODE_KEY[code] || '';
}

/** The typed character's name ('' for dead keys, modifiers and unknowns). */
function keyFromKey(key) {
  if (!key || key === 'Dead' || key === 'Unidentified' || MODIFIER_KEYS.has(key)) return '';
  if (KEY_NAME[key]) return KEY_NAME[key];
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(key)) return key;
  if (key.length === 1) return /[a-z]/i.test(key) ? key.toUpperCase() : key;
  return '';
}

const isSymbol = (k) => k.length === 1 && !/[A-Z0-9]/.test(k);

/** "mod+shift+p" → "Meta+Shift+P" on a Mac (canonical order, real modifiers). '' when it has no key. */
export function normalize(binding) {
  const parts = String(binding || '').split('+');
  // "Mod++" / "Shift++": the last part is the key itself.
  let key = parts.pop();
  if (key === '' && parts.length && parts[parts.length - 1] === '') { parts.pop(); key = '+'; }
  const mods = new Set();
  for (const raw of parts) {
    const p = raw.trim().toLowerCase();
    if (p === 'mod' || p === 'cmdorctrl') mods.add(IS_MAC ? 'Meta' : 'Ctrl');
    else if (p === 'ctrl' || p === 'control') mods.add('Ctrl');
    else if (p === 'meta' || p === 'cmd' || p === 'command' || p === 'super' || p === 'win') mods.add('Meta');
    else if (p === 'alt' || p === 'option' || p === 'opt') mods.add('Alt');
    else if (p === 'shift') mods.add('Shift');
    else if (p) return '';
  }
  key = key.trim();
  if (!key) return '';
  if (key.length === 1) key = key.toUpperCase();
  else {
    const lower = key.toLowerCase();
    const named = { esc: 'Esc', escape: 'Esc', up: 'Up', down: 'Down', left: 'Left', right: 'Right', enter: 'Enter', return: 'Enter', tab: 'Tab', space: 'Space', backspace: 'Backspace', delete: 'Delete', del: 'Delete', home: 'Home', end: 'End', pageup: 'PageUp', pagedown: 'PageDown', insert: 'Insert' };
    if (named[lower]) key = named[lower];
    else if (/^f([1-9]|1[0-9]|2[0-4])$/.test(lower)) key = lower.toUpperCase();
    else key = key[0].toUpperCase() + key.slice(1);
  }
  return [...MOD_ORDER.filter((m) => mods.has(m)), key].join('+');
}

/** Every binding this keydown could mean (by physical key, and by the typed character). */
export function eventCombos(e) {
  if (!e || MODIFIER_KEYS.has(e.key)) return [];
  const mods = [];
  if (e.ctrlKey) mods.push('Ctrl');
  if (e.metaKey) mods.push('Meta');
  if (e.altKey) mods.push('Alt');
  const out = new Set();
  const byCode = keyFromCode(e.code);
  if (byCode) out.add([...mods, ...(e.shiftKey ? ['Shift'] : []), byCode].join('+'));
  const byKey = keyFromKey(e.key);
  if (byKey && byKey !== byCode) {
    // A shifted symbol ("?" is ⇧/ here, ⇧, elsewhere) already says Shift.
    const shift = e.shiftKey && !isSymbol(byKey) ? ['Shift'] : [];
    out.add([...mods, ...shift, byKey].join('+'));
  }
  return [...out];
}

/** The binding a keydown records as (the shortcut editor). '' for a lone modifier. */
export function eventToBinding(e) {
  if (!e || MODIFIER_KEYS.has(e.key)) return '';
  const byCode = keyFromCode(e.code);
  const byKey = keyFromKey(e.key);
  const key = byCode || byKey;
  if (!key) return '';
  const mods = [];
  if (e.ctrlKey) mods.push('Ctrl');
  if (e.metaKey) mods.push('Meta');
  if (e.altKey) mods.push('Alt');
  if (e.shiftKey) mods.push('Shift');
  return [...mods, key].join('+');
}

// ─── The user's keys ──────────────────────────────────────────────────────

let overrides = load();
const listeners = new Set();
const cache = new Map(); // id → normalized bindings

function readStore() {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage.getItem(STORE_KEY);
  } catch {
    return null;
  }
}

function load(raw = readStore()) {
  if (!raw) return {};
  try {
    const data = JSON.parse(raw);
    if (!data || data.v !== STORE_VERSION || typeof data.map !== 'object' || !data.map) return {};
    const out = {};
    for (const [id, list] of Object.entries(data.map)) {
      const a = BY_ID.get(id);
      if (!a || a.fixed || !Array.isArray(list)) continue;
      out[id] = list.map(normalize).filter(Boolean);
    }
    return out;
  } catch {
    return {};
  }
}

function persist() {
  try {
    if (typeof localStorage === 'undefined') return;
    if (Object.keys(overrides).length) localStorage.setItem(STORE_KEY, JSON.stringify({ v: STORE_VERSION, map: overrides }));
    else localStorage.removeItem(STORE_KEY);
  } catch { /* private mode: changes last for this page */ }
}

function changed() {
  cache.clear();
  for (const fn of listeners) {
    try {
      fn();
    } catch (err) {
      console.warn('keymap: listener failed', err);
    }
  }
}

/** Another tab of this browser changed the keys: follow it. */
if (typeof window !== 'undefined' && window.addEventListener) {
  window.addEventListener('storage', (e) => {
    if (e.key !== STORE_KEY) return;
    overrides = load(e.newValue);
    changed();
  });
}

export function onKeymapChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** The keys that trigger `id` right now, normalized ([] when unbound). */
export function bindingsOf(id) {
  if (cache.has(id)) return cache.get(id);
  const a = BY_ID.get(id);
  if (!a) return [];
  const list = a.fixed ? a.def : (overrides[id] ?? a.def);
  const out = a.fixed ? [...list] : [...new Set(list.map(normalize).filter(Boolean))];
  cache.set(id, out);
  return out;
}

export function defaultsOf(id) {
  const a = BY_ID.get(id);
  return a ? (a.fixed ? [...a.def] : a.def.map(normalize).filter(Boolean)) : [];
}

export function isCustomized(id) {
  const a = BY_ID.get(id);
  if (!a || a.fixed || !(id in overrides)) return false;
  const now = bindingsOf(id);
  const def = defaultsOf(id);
  return now.length !== def.length || now.some((b, i) => b !== def[i]);
}

export const customizedCount = () => ACTIONS.filter((a) => isCustomized(a.id)).length;

export function setBindings(id, list) {
  const a = BY_ID.get(id);
  if (!a || a.fixed) return;
  overrides[id] = [...new Set((list || []).map(normalize).filter(Boolean))];
  cache.delete(id);
  if (!isCustomized(id)) delete overrides[id];
  persist();
  changed();
}

export function resetBindings(id) {
  if (!(id in overrides)) return;
  delete overrides[id];
  persist();
  changed();
}

export function resetAll() {
  overrides = {};
  persist();
  changed();
}

// ─── Matching ─────────────────────────────────────────────────────────────

export function matches(e, id) {
  if (!e || e.isComposing) return false;
  const a = BY_ID.get(id);
  if (!a || a.fixed) return false;
  const list = bindingsOf(id);
  if (!list.length) return false;
  const combos = eventCombos(e);
  return combos.some((c) => list.includes(c));
}

/** The first id in `ids` this keydown triggers, or ''. */
export function matchAny(e, ids) {
  if (!e || e.isComposing) return '';
  const combos = eventCombos(e);
  if (!combos.length) return '';
  for (const id of ids) {
    const a = BY_ID.get(id);
    if (!a || a.fixed) continue;
    if (bindingsOf(id).some((b) => combos.includes(b))) return id;
  }
  return '';
}

// Page-wide keys that still work with a dialog (the Files drawer) open …
const OVER_DIALOGS = new Set(['app.palette', 'term.toggle', 'term.new', 'term.all', 'term.scripts', 'term.max', 'term.split']);
// … and the ones that also win inside a shell (capture phase). The other page-wide keys
// give way there: a shell's own key (Inside a shell) is handled first and stops them.
const OVER_SHELLS = new Set(['term.new', 'term.all', 'term.scripts', 'term.max', 'term.split']);

/** Can one keydown reach both `a` and `b`? */
function overlaps(a, b) {
  const sa = scopeOf(a);
  const sb = scopeOf(b);
  if (sa === sb) return true;
  if ((sa === 'files' && sb === 'editor') || (sa === 'editor' && sb === 'files')) return true;
  const [g, other] = sa === 'global' ? [a, sb] : sb === 'global' ? [b, sa] : [null, ''];
  if (!g) return false;
  return other === 'terminal' ? OVER_SHELLS.has(g.id) : OVER_DIALOGS.has(g.id);
}

/** Other actions that already answer to `binding` where `id` works. */
export function conflictsFor(id, binding) {
  const a = BY_ID.get(id);
  const b = normalize(binding);
  if (!a || !b) return [];
  return ACTIONS.filter((o) => o.id !== id && overlaps(a, o) && (o.fixed ? o.def.map(normalize) : bindingsOf(o.id)).includes(b));
}

// The browser keeps these whatever the page does (new tab / window, close, quit, switch tabs).
const RESERVED = new Set(['Mod+T', 'Mod+N', 'Mod+W', 'Mod+Q', 'Mod+Shift+T', 'Mod+Shift+N', 'Mod+Shift+W', 'Mod+Tab', 'Ctrl+Tab', 'Ctrl+Shift+Tab', 'Mod+L', 'Mod+R', 'Mod+Shift+R'].map(normalize));

/** Why `binding` can't be used for `id` ('' when it can). */
export function problemWith(id, binding) {
  const a = BY_ID.get(id);
  const b = normalize(binding);
  if (!a || !b) return 'That key can\u2019t be used.';
  if (RESERVED.has(b)) return `The browser keeps ${display(b)} for itself.`;
  const parts = b.split('+');
  const key = parts[parts.length - 1];
  const mods = parts.slice(0, -1);
  const strong = mods.some((m) => m === 'Ctrl' || m === 'Meta' || m === 'Alt');
  const fkey = /^F\d+$/.test(key);
  if (key === 'Esc') return 'Esc always closes and cancels.';
  if (!strong && !fkey && !a.plain) return `Add ${IS_MAC ? '⌘, ⌃ or ⌥' : 'Ctrl or Alt'} — a plain key would type text.`;
  if (key === 'Tab' && !strong) return 'Tab moves between controls.';
  return '';
}

// ─── Display ──────────────────────────────────────────────────────────────

const MAC_CAP = { Ctrl: '⌃', Meta: '⌘', Alt: '⌥', Shift: '⇧' };
const PC_CAP = { Ctrl: 'Ctrl', Meta: 'Win', Alt: 'Alt', Shift: 'Shift' };
const KEY_CAP = { Up: '↑', Down: '↓', Left: '←', Right: '→', Enter: IS_MAC ? '↵' : 'Enter', Backspace: IS_MAC ? '⌫' : 'Backspace', Delete: IS_MAC ? '⌦' : 'Del', Space: 'Space', PageUp: 'PgUp', PageDown: 'PgDn' };

/** "Meta+Shift+P" → ['⌘', '⇧', 'P'] on a Mac, ['Ctrl', 'Shift', 'P'] elsewhere. */
export function keyCaps(binding) {
  const b = /…/.test(binding) ? binding : normalize(binding) || binding;
  const parts = b.split('+');
  const key = parts.pop() || (b.endsWith('+') ? '+' : '');
  return [...parts.map((m) => (IS_MAC ? MAC_CAP : PC_CAP)[m] || m), KEY_CAP[key] || key];
}

/** One binding as text: "⌥⇧F" on a Mac, "Alt+Shift+F" elsewhere. */
export function display(binding) {
  return keyCaps(binding).join(IS_MAC ? '' : '+');
}

/** The first key of `id` as text, for titles and menus ('' when unbound). */
export function hint(id) {
  const b = bindingsOf(id)[0];
  return b ? display(b) : '';
}

/** "Label (⌥N)" — or just "Label" when the action has no key. */
export function withHint(label, id) {
  const h = hint(id);
  return h ? `${label} (${h})` : label;
}
