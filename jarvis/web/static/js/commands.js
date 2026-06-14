/** Command hub — full slash-command palette for the web remote */
import { $, escapeHtml, icons, showToast } from './utils.js';
import { store, patchSession, applySettingsResponse, subscribe, loadSnapshot } from './store.js';
import { submitPrompt, fillPrompt } from './composer.js';
import { updateSettings, pickerAction } from './api.js';
import { applySessionData } from './stream.js';
import { openPickerByKind, applyActionState } from './pickers.js';
import { refreshThinkingVisibility } from './chat.js';
import { renderMetaBar } from './drawer.js';
import { EFFORTS, effortHint, effortOptionsHtml, bindEffortOptions, syncEffortOptions } from './effort.js';

let cmdEffortOpen = false;

/** @type {{ id: string, label: string, icon: string, items: object[] }[]} */
export const COMMAND_CATEGORIES = [
  {
    id: 'pickers',
    label: 'Open',
    icon: 'layout-grid',
    items: [
      { picker: 'session', label: 'Sessions', desc: 'Switch, resume, or delete sessions', icon: 'history' },
      { picker: 'model', label: 'Model', desc: 'Pick AI model or provider tier', icon: 'cpu' },
      { picker: 'agent', label: 'Agent', desc: 'Browse and activate agents', icon: 'sparkles' },
      { picker: 'skill', label: 'Skills', desc: 'Browse skill packs', icon: 'book-open' },
      { picker: 'mcp', label: 'MCP', desc: 'Connect and manage MCP servers', icon: 'plug' },
      { cmd: '/provider', label: 'Provider', desc: 'Switch auth provider', icon: 'server' },
      { cmd: '/settings', label: 'Settings', desc: 'View and edit preferences', icon: 'sliders-horizontal' },
      { cmd: '/theme', label: 'Theme', desc: 'Red or purple theme', icon: 'palette' },
      { cmd: '/local', label: 'Local commands', desc: 'Run shell/file/git without LLM', icon: 'terminal' },
    ],
  },
  {
    id: 'session',
    label: 'Session',
    icon: 'messages-square',
    items: [
      { cmd: '/new', label: 'New chat', desc: 'Fresh conversation (keeps pin)', icon: 'plus', action: 'session_new' },
      { cmd: '/reset', label: 'Reset', desc: 'Clear conversation history', icon: 'rotate-ccw' },
      { cmd: '/retry', label: 'Retry', desc: 'Re-send last message', icon: 'refresh-cw' },
      { cmd: '/history', label: 'History', desc: 'Message summary', icon: 'list' },
      { cmd: '/stats', label: 'Stats', desc: 'Session time, msgs, tools', icon: 'bar-chart-3' },
      { cmd: '/tokens', label: 'Tokens', desc: 'Token usage so far', icon: 'hash' },
      { cmd: '/cost', label: 'Cost', desc: 'Estimated USD cost', icon: 'dollar-sign' },
      { cmd: '/clear', label: 'Clear screen', desc: 'Clear terminal view', icon: 'eraser' },
      { cmd: '/search ', label: 'Search', desc: 'Search conversation', icon: 'search', fillOnly: true },
      { cmd: '/export ', label: 'Export', desc: 'Export as markdown', icon: 'download', fillOnly: true },
    ],
  },
  {
    id: 'memory',
    label: 'Memory',
    icon: 'brain',
    items: [
      { cmd: '/memory', label: 'Memory', desc: 'Personal facts — list, add, delete', icon: 'database' },
      { cmd: '/lesson', label: 'Lessons', desc: 'Agent lesson memory', icon: 'graduation-cap' },
      { cmd: '/scan', label: 'Scan', desc: 'Deep scan identity & docs', icon: 'scan' },
      { cmd: '/pin', label: 'Pinned context', desc: 'View or edit pinned context', icon: 'pin' },
      { cmd: '/notes', label: 'Notes', desc: 'Show notes file', icon: 'sticky-note' },
      { cmd: '/note ', label: 'Add note', desc: 'Append to notes', icon: 'pen-line', fillOnly: true },
    ],
  },
  {
    id: 'control',
    label: 'Control',
    icon: 'toggle-left',
    items: [
      { action: 'toggle-think', label: 'Extended thinking', desc: 'Toggle think mode', icon: 'brain', toggle: true },
      { action: 'toggle-trace', label: 'Tool trace', desc: 'Show internal tool logs', icon: 'list-tree', toggle: true },
      { action: 'toggle-auto', label: 'Auto-approve shell', desc: 'Skip shell prompts', icon: 'shield', toggle: true },
      { action: 'toggle-show-think', label: 'Show thinking UI', desc: 'Thinking bubbles in chat', icon: 'eye', toggle: true },
      { cmd: '/think', label: 'Think effort', desc: 'Open effort picker on laptop', icon: 'gauge' },
      { cmd: '/verbose', label: 'Verbose', desc: 'Toggle trace via slash', icon: 'file-text' },
      { cmd: '/multi', label: 'Multiline', desc: 'Enter multiline message', icon: 'align-left' },
    ],
  },
  {
    id: 'auth',
    label: 'Auth',
    icon: 'key-round',
    items: [
      { cmd: '/provider', label: 'Provider setup', desc: 'OAuth login, API keys, provider switch — all in one place', icon: 'settings-2' },
    ],
  },
  {
    id: 'agents',
    label: 'Agents & skills',
    icon: 'bot',
    items: [
      { cmd: '/agent init', label: 'Scaffold .harness/', desc: 'Create project agent tree', icon: 'folder-plus' },
      { cmd: '/agent refresh', label: 'Refresh agents', desc: 'Re-scan agent files', icon: 'refresh-cw' },
      { cmd: '/agent off', label: 'Deactivate agent', desc: 'Use base system prompt', icon: 'circle-off', action: 'agent_off' },
    ],
  },
  {
    id: 'other',
    label: 'More',
    icon: 'ellipsis',
    items: [
      { cmd: '/help', label: 'Help', desc: 'Full command reference', icon: 'circle-help' },
      { cmd: '/version', label: 'Version', desc: 'Jarvis version', icon: 'info' },
      { cmd: '/upgrade', label: 'Upgrade', desc: 'Update to latest', icon: 'arrow-up-circle' },
      { cmd: '/copy', label: 'Copy reply', desc: 'Copy last assistant response', icon: 'copy' },
      { cmd: '/paste', label: 'Paste', desc: 'Send clipboard as message', icon: 'clipboard' },
      { cmd: '/aliases', label: 'Aliases', desc: 'List command shortcuts', icon: 'link' },
      { cmd: '/alias ', label: 'New alias', desc: 'Create shortcut', icon: 'link-2', fillOnly: true },
    ],
  },
];

const LAPTOP_MODALS = new Set([
  '/provider', '/settings', '/setting', '/theme', '/themes', '/local',
  '/memory', '/memories', '/lesson', '/lessons', '/pin', '/think', '/think mode',
  '/provider', '/agent init',
]);

let activeCategory = 'pickers';
let searchQuery = '';
/** Flat list of items currently shown (search-filtered or active tab). */
let visibleCommandItems = [];

function isOpen() {
  return $('cmd-overlay')?.classList.contains('open');
}

export function openCommandHub() {
  const overlay = $('cmd-overlay');
  if (!overlay) return;
  searchQuery = '';
  activeCategory = 'pickers';
  const search = $('cmd-search');
  if (search) search.value = '';
  overlay.classList.add('open');
  renderCommandHub();
  icons();
  setTimeout(() => search?.focus(), 80);
}

export function closeCommandHub() {
  closeCmdEffortMenu();
  $('cmd-overlay')?.classList.remove('open');
}

function toggleValue(action) {
  const s = store.session;
  if (action === 'toggle-think') return s.think_mode;
  if (action === 'toggle-trace') return s.show_internal;
  if (action === 'toggle-auto') return s.auto_approve;
  if (action === 'toggle-show-think') return store.showThinkingUi;
  return false;
}

async function runToggle(action) {
  if (action === 'toggle-show-think') {
    store.showThinkingUi = !store.showThinkingUi;
    localStorage.setItem('jarvis-show-thinking', store.showThinkingUi ? '1' : '0');
    refreshThinkingVisibility();
    renderCommandHub();
    showToast(store.showThinkingUi ? 'Thinking visible' : 'Thinking hidden');
    return;
  }

  const patch =
    action === 'toggle-think'
      ? { think_mode: !store.session.think_mode }
      : action === 'toggle-trace'
        ? { show_internal: !store.session.show_internal }
        : { auto_approve: !store.session.auto_approve };

  try {
    const res = await updateSettings(patch);
    patchSession(patch);
    if (res.settings) applySettingsResponse(res.settings);
    if (action === 'toggle-trace' && res.settings) applySessionData(res.settings);
    else if (action === 'toggle-trace') refreshThinkingVisibility();
    renderCommandHub();
    showToast('Updated');
  } catch {
    showToast('Settings update failed', true);
  }
}

async function runItem(item) {
  closeCommandHub();

  if (item.picker) {
    openPickerByKind(item.picker);
    return;
  }

  if (item.action === 'session_new') {
    const res = await pickerAction('session_new', {});
    if (res.ok) {
      applyActionState(res);
      showToast('New session started');
    } else {
      if (res.state) applyActionState(res);
      showToast(res.error || 'Failed', true);
    }
    return;
  }

  if (item.action === 'agent_off') {
    const res = await pickerAction('agent_select', { name: '__off__' });
    if (res.ok) {
      applyActionState(res);
      showToast('Agent deactivated');
    } else {
      if (res.state) applyActionState(res);
      showToast(res.error || 'Failed', true);
    }
    return;
  }

  if (item.action?.startsWith('toggle-')) {
    await runToggle(item.action);
    return;
  }

  if (item.fillOnly) {
    fillPrompt(item.cmd);
    return;
  }

  if (item.cmd) {
    await submitPrompt(item.cmd);
    if (LAPTOP_MODALS.has(item.cmd.trim().toLowerCase())) {
      showToast('Opened on your laptop terminal');
    }
  }
}

function filterItems() {
  const q = searchQuery.trim().toLowerCase();
  if (!q) {
    const cat = COMMAND_CATEGORIES.find((c) => c.id === activeCategory);
    return cat ? [{ ...cat, items: cat.items }] : [];
  }
  return COMMAND_CATEGORIES.map((cat) => ({
    ...cat,
    items: cat.items.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        (item.cmd || '').toLowerCase().includes(q) ||
        (item.picker || '').toLowerCase().includes(q) ||
        (item.desc || '').toLowerCase().includes(q),
    ),
  })).filter((cat) => cat.items.length);
}

function renderTabs() {
  const tabs = $('cmd-tabs');
  if (!tabs) return;
  const q = searchQuery.trim();
  tabs.innerHTML = COMMAND_CATEGORIES.map((cat) => `
    <button type="button" class="cmd-tab${!q && cat.id === activeCategory ? ' active' : ''}" data-cat="${cat.id}">
      <i data-lucide="${cat.icon}"></i>
      <span>${escapeHtml(cat.label)}</span>
    </button>`).join('');

  tabs.querySelectorAll('.cmd-tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      activeCategory = btn.dataset.cat;
      searchQuery = '';
      const search = $('cmd-search');
      if (search) search.value = '';
      renderCommandHub();
    });
  });
}

function closeCmdEffortMenu() {
  const trigger = $('cmd-effort-trigger');
  const menu = $('cmd-effort-menu');
  cmdEffortOpen = false;
  trigger?.classList.remove('open');
  trigger?.setAttribute('aria-expanded', 'false');
  if (menu) menu.hidden = true;
}

function openCmdEffortMenu() {
  const trigger = $('cmd-effort-trigger');
  const menu = $('cmd-effort-menu');
  if (!trigger || !menu || trigger.disabled) return;
  cmdEffortOpen = true;
  trigger.classList.add('open');
  trigger.setAttribute('aria-expanded', 'true');
  menu.hidden = false;
}

function toggleCmdEffortMenu() {
  if (cmdEffortOpen) closeCmdEffortMenu();
  else openCmdEffortMenu();
}

async function setCmdEffort(value) {
  if (!EFFORTS.includes(value)) return;
  closeCmdEffortMenu();
  try {
    const res = await updateSettings({ think_effort: value });
    patchSession({ think_effort: value });
    if (res.settings) applySettingsResponse(res.settings);
    renderCommandHub();
    showToast(`Effort: ${value}`);
  } catch {
    showToast('Failed to update effort', true);
  }
}

function renderEffortRow() {
  const row = $('cmd-effort');
  if (!row) return;
  if (!store.session.think_mode) {
    row.classList.add('hidden');
    closeCmdEffortMenu();
    return;
  }
  row.classList.remove('hidden');

  const effort = store.session.think_effort || 'medium';
  const hint = effortHint(effort);

  row.innerHTML = `
    <div class="cmd-effort-dropdown" id="cmd-effort-dropdown">
      <button type="button" class="cmd-effort-trigger" id="cmd-effort-trigger"
        aria-haspopup="listbox" aria-expanded="${cmdEffortOpen ? 'true' : 'false'}" aria-label="Thinking effort">
        <span class="cmd-effort-trigger-main">
          <i data-lucide="gauge"></i>
          <span class="cmd-effort-copy">
            <span class="cmd-effort-label">Think effort</span>
            <span class="cmd-effort-value">${escapeHtml(effort)}</span>
          </span>
        </span>
        <span class="cmd-effort-hint">${escapeHtml(hint)}</span>
        <i data-lucide="chevron-down" class="cmd-effort-chevron"></i>
      </button>
      <div class="cmd-effort-menu" id="cmd-effort-menu" role="listbox" aria-label="Thinking effort" ${cmdEffortOpen ? '' : 'hidden'}>
        ${effortOptionsHtml({ optionClass: 'cmd-effort-option' })}
      </div>
    </div>`;

  syncEffortOptions($('cmd-effort-menu'), effort);

  $('cmd-effort-trigger')?.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleCmdEffortMenu();
  });

  bindEffortOptions($('cmd-effort-menu'), setCmdEffort);

  if (cmdEffortOpen) {
    $('cmd-effort-trigger')?.classList.add('open');
  }
}

function renderList() {
  const list = $('cmd-list');
  if (!list) return;
  const groups = filterItems();

  if (!groups.length) {
    visibleCommandItems = [];
    list.innerHTML = `<p class="cmd-empty">No commands match “${escapeHtml(searchQuery)}”</p>`;
    return;
  }

  visibleCommandItems = groups.flatMap((cat) => cat.items);

  let itemIdx = 0;
  list.innerHTML = groups
    .map(
      (cat) => `
    <section class="cmd-section">
      ${searchQuery.trim() ? `<h3 class="cmd-section-title">${escapeHtml(cat.label)}</h3>` : ''}
      <div class="cmd-items">
        ${cat.items.map((item) => renderItem(itemIdx++, item)).join('')}
      </div>
    </section>`,
    )
    .join('');

  list.querySelectorAll('[data-cmd-item]').forEach((el) => {
    el.addEventListener('click', () => {
      const item = visibleCommandItems[Number(el.dataset.itemIdx)];
      if (item) runItem(item);
    });
  });
}

function renderItem(itemIdx, item) {
  const isToggle = !!item.toggle;
  const on = isToggle ? toggleValue(item.action) : false;
  const badge = item.cmd
    ? `<code class="cmd-badge">${escapeHtml(item.cmd.trim())}</code>`
    : item.picker
      ? `<code class="cmd-badge">${escapeHtml(item.picker)}</code>`
    : isToggle
      ? `<span class="cmd-state ${on ? 'on' : 'off'}">${on ? 'ON' : 'OFF'}</span>`
      : '';

  return `
    <button type="button" class="cmd-item" data-cmd-item data-item-idx="${itemIdx}">
      <span class="cmd-item-icon"><i data-lucide="${item.icon || 'terminal'}"></i></span>
      <span class="cmd-item-body">
        <strong>${escapeHtml(item.label)}</strong>
        <span>${escapeHtml(item.desc || '')}</span>
      </span>
      ${badge}
      <i data-lucide="chevron-right" class="cmd-item-arrow"></i>
    </button>`;
}

function renderQuickToggles() {
  const row = $('cmd-quick');
  if (!row) return;

  const items = [
    { action: 'toggle-think', icon: 'brain', label: 'Think' },
    { action: 'toggle-trace', icon: 'list-tree', label: 'Trace' },
    { action: 'toggle-auto', icon: 'shield', label: 'Auto', warn: true },
    { action: 'toggle-show-think', icon: 'eye', label: 'Show' },
  ];

  row.innerHTML = items
    .map((item) => {
      const on = toggleValue(item.action);
      const cls = item.warn && on ? 'on-warn' : on ? 'on' : '';
      return `
        <button type="button" class="cmd-quick-btn ${cls}" data-quick="${item.action}" aria-pressed="${on}">
          <i data-lucide="${item.icon}"></i>
          <span>${on ? 'ON' : 'OFF'}</span>
        </button>`;
    })
    .join('');

  row.querySelectorAll('[data-quick]').forEach((btn) => {
    btn.addEventListener('click', () => runToggle(btn.dataset.quick));
  });
}

function renderCommandHub() {
  renderQuickToggles();
  renderTabs();
  renderEffortRow();
  renderList();
  icons();
}

export function initCommandHub() {
  $('cmd-btn')?.addEventListener('click', openCommandHub);
  $('cmd-close')?.addEventListener('click', closeCommandHub);
  $('cmd-overlay')?.addEventListener('click', (e) => {
    if (e.target === $('cmd-overlay')) closeCommandHub();
  });
  $('cmd-hub')?.addEventListener('click', (e) => {
    if (!$('cmd-effort-dropdown')?.contains(e.target)) closeCmdEffortMenu();
    e.stopPropagation();
  });

  subscribe((s) => {
    if (isOpen()) renderCommandHub();
  });

  $('cmd-search')?.addEventListener('input', (e) => {
    searchQuery = e.target.value;
    renderCommandHub();
  });

  $('cmd-search')?.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeCommandHub();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen()) closeCommandHub();
  });
}
