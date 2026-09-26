/** Sidebar: model + agent, reasoning and transcript switches, recent sessions, usage */
import { $, escapeHtml, formatCount, debounce } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe } from './store.js';
import { fetchSessions } from './api.js';
import { EFFORTS, EFFORT_LABELS, EFFORT_HINTS } from './effort.js';
import {
  newChat,
  onSessionChange,
  runAction,
  setEffort,
  toggleSetting,
} from './actions.js';
import { toggleTheme, resolvedTheme, openAppearance } from './theme.js';

const PROVIDER_LABELS = {
  anthropic: 'Anthropic',
  openrouter: 'OpenRouter',
  opencode: 'OpenCode Go',
  opencode_zen: 'OpenCode Zen',
  openai_codex: 'ChatGPT (Codex)',
  kimchi: 'Kimchi',
};

let openPicker = () => {};
let recentSig = '';
let lastSessionId = null;
let lastTitle = null;

// ─── Drawer (narrow screens) ──────────────────────────────────────────────

export function openSidebar() {
  document.body.classList.add('side-open');
  $('sidebar')?.querySelector('button, input')?.focus({ preventScroll: true });
}

export function closeSidebar() {
  document.body.classList.remove('side-open');
}

function closeOnNarrow() {
  if (window.matchMedia('(max-width: 960px)').matches) closeSidebar();
}

// ─── Rendering ────────────────────────────────────────────────────────────

function renderEffort(s) {
  const box = $('effort');
  if (!box) return;
  const on = !!s.session.think_mode;
  const current = s.session.think_effort;
  if (!box.childElementCount) {
    box.innerHTML = EFFORTS.filter((e) => e !== 'none').map((e) => `
      <button type="button" class="effort-opt" role="radio" data-effort="${e}" title="${escapeHtml(EFFORT_HINTS[e])}" aria-checked="false">${escapeHtml(EFFORT_LABELS[e])}</button>`).join('');
    box.querySelectorAll('.effort-opt').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.dataset.effort !== store.session.think_effort || !store.session.think_mode) setEffort(btn.dataset.effort);
      });
    });
  }
  box.classList.toggle('is-off', !on);
  box.querySelectorAll('.effort-opt').forEach((btn) => {
    btn.setAttribute('aria-checked', String(on && btn.dataset.effort === current));
    btn.disabled = s.pendingToggle === 'think_effort';
  });
}

function renderSwitches(s) {
  const set = (id, checked, pendingKey, disabled = false) => {
    const el = $(id);
    if (!el) return;
    el.checked = !!checked;
    el.disabled = disabled;
    el.classList.toggle('is-pending', !!pendingKey && s.pendingToggle === pendingKey);
    el.closest('.switch-row')?.classList.toggle('is-disabled', disabled);
  };
  set('sw-think', s.session.think_mode, 'think_mode');
  set('sw-trace', s.session.show_internal, 'show_internal');
  set('sw-thoughts', s.showThoughts && s.session.show_internal, null, !s.session.show_internal);
  set('sw-auto', s.session.auto_approve, 'auto_approve');
  const sub = $('thoughts-sub');
  if (sub) sub.textContent = s.session.show_internal ? 'Only on this device' : 'Turn on tool trace first';
}

function renderCards(s) {
  const model = s.session.model || '—';
  $('model-name').textContent = model;
  $('model-name').title = model;
  $('model-provider').textContent = PROVIDER_LABELS[s.session.provider] || s.session.provider || '';

  const agent = s.session.agent;
  $('agent-name').textContent = agent || 'No agent';
  $('agent-sub').textContent = agent ? 'Active profile' : 'Base system prompt';
}

function renderUsage(s) {
  $('use-in').textContent = formatCount(s.session.tokens_in);
  $('use-out').textContent = formatCount(s.session.tokens_out);
  $('use-tools').textContent = formatCount(s.session.tool_calls);
  $('use-in').title = `${s.session.tokens_in} input tokens`;
  $('use-out').title = `${s.session.tokens_out} output tokens`;
}

function renderThemeButton() {
  const btn = $('theme-btn');
  if (!btn) return;
  const light = resolvedTheme() === 'light';
  if (btn.dataset.theme === String(light)) return;
  btn.dataset.theme = String(light);
  btn.innerHTML = icon(light ? 'moon' : 'sun');
  btn.setAttribute('aria-label', light ? 'Switch to dark theme' : 'Switch to light theme');
  btn.title = btn.getAttribute('aria-label');
}

function render(s) {
  renderCards(s);
  renderSwitches(s);
  renderEffort(s);
  renderUsage(s);
  renderThemeButton();
  // New session, or the current one just got its title: the list is stale.
  if (s.session.session_id !== lastSessionId || s.session.session_title !== lastTitle) {
    lastSessionId = s.session.session_id;
    lastTitle = s.session.session_title;
    refreshRecent();
  }
  markActiveRecent(s.session.session_id);
}

// ─── Recent sessions ──────────────────────────────────────────────────────

function markActiveRecent(sid) {
  document.querySelectorAll('#recent-list .recent-row').forEach((row) => {
    row.classList.toggle('is-active', row.dataset.sid === String(sid));
  });
}

async function loadRecent() {
  const list = $('recent-list');
  if (!list) return;
  if (!list.childElementCount) list.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  try {
    const data = await fetchSessions(5);
    const sessions = data.sessions || [];
    const sig = JSON.stringify(sessions.map((x) => [x.id, x.title, x.msg_count, x.updated_label]));
    if (sig === recentSig && list.querySelector('.recent-row')) return;
    recentSig = sig;
    if (!sessions.length) {
      list.innerHTML = '<p class="recent-empty">Saved sessions show up here.</p>';
      return;
    }
    list.innerHTML = sessions.map((x) => `
      <button type="button" class="recent-row" role="listitem" data-sid="${x.id}" title="${escapeHtml(x.title)}">
        <span class="rr-dot" aria-hidden="true"></span>
        <span class="rr-body">
          <span class="rr-title">${escapeHtml(x.title)}</span>
          <span class="rr-meta">${escapeHtml(x.updated_label || '')}${x.msg_count ? `, ${x.msg_count} messages` : ''}</span>
        </span>
      </button>`).join('');
    list.querySelectorAll('.recent-row').forEach((row) => {
      row.addEventListener('click', async () => {
        if (row.dataset.sid === String(store.session.session_id)) {
          closeOnNarrow();
          return;
        }
        row.classList.add('is-active');
        const res = await runAction('session_resume', { session_id: Number(row.dataset.sid) }, 'Session resumed');
        if (res.ok) closeOnNarrow();
      });
    });
    markActiveRecent(store.session.session_id);
  } catch {
    if (!list.querySelector('.recent-row')) list.innerHTML = '<p class="recent-empty">Could not load sessions.</p>';
  }
}

export const refreshRecent = debounce(loadRecent, 250);

// ─── Init ─────────────────────────────────────────────────────────────────

export function initSidebar({ onOpenPicker }) {
  openPicker = onOpenPicker;

  $('menu-btn')?.addEventListener('click', openSidebar);
  $('side-close')?.addEventListener('click', closeSidebar);
  $('scrim')?.addEventListener('click', closeSidebar);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.body.classList.contains('side-open')) closeSidebar();
  });

  $('new-chat')?.addEventListener('click', async () => {
    const res = await newChat();
    if (res.ok) {
      closeOnNarrow();
      $('prompt')?.focus();
    }
  });
  $('model-card')?.addEventListener('click', () => { closeOnNarrow(); openPicker('model'); });
  $('agent-card')?.addEventListener('click', () => { closeOnNarrow(); openPicker('agent'); });
  $('all-sessions')?.addEventListener('click', () => { closeOnNarrow(); openPicker('session'); });
  $('open-skills')?.addEventListener('click', () => { closeOnNarrow(); openPicker('skill'); });
  $('open-mcp')?.addEventListener('click', () => { closeOnNarrow(); openPicker('mcp'); });
  $('theme-btn')?.addEventListener('click', toggleTheme);
  $('appearance-btn')?.addEventListener('click', () => { closeOnNarrow(); openAppearance(); });

  // The native flip already matches the optimistic store value; a failed
  // save reverts the store, and render() puts the checkbox back.
  const wire = (id, key) => $(id)?.addEventListener('change', () => toggleSetting(key));
  wire('sw-think', 'think_mode');
  wire('sw-trace', 'show_internal');
  wire('sw-thoughts', 'showThoughts');
  wire('sw-auto', 'auto_approve');

  onSessionChange(refreshRecent);
  document.addEventListener('jarvis:sessions-changed', refreshRecent);
  subscribe(render);
  render(store);
  renderThemeButton();
}
