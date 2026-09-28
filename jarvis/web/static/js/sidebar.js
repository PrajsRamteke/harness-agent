/** Sidebar: model + agent, reasoning and transcript switches, recent sessions, usage */
import { $, escapeHtml, formatCount, debounce, countTo, animateEl, SPRING } from './utils.js';
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
import { toggleTheme, isLightTheme, openAppearance } from './theme.js';
import { onProvidersChange } from './providers.js';

const PROVIDER_LABELS = {
  anthropic: 'Anthropic',
  openrouter: 'OpenRouter',
  opencode: 'OpenCode Go',
  opencode_zen: 'OpenCode Zen',
  openai_codex: 'ChatGPT (Codex)',
  kimchi: 'Kimchi',
};
/** Filled from /api/providers: "Claude Pro / Max" rather than "Anthropic". */
let activeProviderLabel = '';

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
  const opts = [...box.querySelectorAll('.effort-opt')];
  opts.forEach((btn) => {
    btn.setAttribute('aria-checked', String(on && btn.dataset.effort === current));
    btn.disabled = s.pendingToggle === 'think_effort';
  });
  // One thumb slides between the options instead of each lighting up.
  const idx = opts.findIndex((b) => b.dataset.effort === current);
  if (idx >= 0) box.style.setProperty('--i', String(idx));
  box.classList.toggle('has-thumb', on && idx >= 0);
  if (!box.classList.contains('is-ready')) requestAnimationFrame(() => box.classList.add('is-ready'));
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
  $('model-provider').textContent = activeProviderLabel || PROVIDER_LABELS[s.session.provider] || s.session.provider || '';

  const agent = s.session.agent;
  $('agent-name').textContent = agent || 'No agent';
  $('agent-sub').textContent = agent ? 'Active profile' : 'Base system prompt';
}

function renderUsage(s) {
  // The first numbers (page load, a resumed session) just appear; only
  // changes while connected count up.
  const animate = { animate: s.connected };
  countTo($('use-in'), s.session.tokens_in, formatCount, animate);
  countTo($('use-out'), s.session.tokens_out, formatCount, animate);
  countTo($('use-tools'), s.session.tool_calls, formatCount, animate);
  $('use-in').title = `${s.session.tokens_in} input tokens`;
  $('use-out').title = `${s.session.tokens_out} output tokens`;
}

function renderThemeButton() {
  const btn = $('theme-btn');
  if (!btn) return;
  const light = isLightTheme();
  if (btn.dataset.theme === String(light)) return;
  const first = !btn.dataset.theme;
  btn.dataset.theme = String(light);
  btn.innerHTML = icon(light ? 'moon' : 'sun');
  if (!first) {
    animateEl(btn.firstElementChild, [
      { transform: 'rotate(-120deg) scale(0.4)', opacity: 0 },
      { transform: 'none', opacity: 1 },
    ], { duration: 520, easing: SPRING });
  }
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

function renderProviders(data) {
  const connected = (data.providers || []).filter((p) => p.connected && p.kind !== 'free');
  activeProviderLabel = data.active_label || '';
  $('model-provider').textContent = activeProviderLabel || PROVIDER_LABELS[store.session.provider] || '';
  const name = $('providers-name');
  const line = $('providers-line');
  if (!name || !line) return;
  if (!connected.length) {
    name.textContent = 'Add a provider';
    line.textContent = 'Free tier now · sign in or paste a key';
  } else {
    name.textContent = `${connected.length} connected`;
    line.textContent = connected.map((p) => p.label).join(', ');
  }
  $('providers-card').title = connected.length ? `Connected: ${line.textContent}` : 'Sign in or paste an API key';
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
    list.innerHTML = sessions.map((x, i) => `
      <button type="button" class="recent-row" role="listitem" data-sid="${x.id}" title="${escapeHtml(x.title)}" style="--i:${i}">
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
  $('providers-card')?.addEventListener('click', () => { closeOnNarrow(); openPicker('provider'); });
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
  onProvidersChange(renderProviders);
}
