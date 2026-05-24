/** Toolbar — think, trace, auto-approve, show thinking UI */
import { $, icons, showToast } from './utils.js';
import { store, patchStore, patchSession, applySettingsResponse, subscribe } from './store.js';
import { updateSettings } from './api.js';
import { refreshThinkingVisibility } from './chat.js';
import { syncComposerState, applySessionData } from './stream.js';

const EFFORTS = ['xhigh', 'high', 'medium', 'low', 'minimal', 'none'];

const EFFORT_HINTS = {
  xhigh: 'Maximum reasoning depth',
  high: 'Deep analysis',
  medium: 'Balanced default',
  low: 'Light thinking',
  minimal: 'Brief passes only',
  none: 'Thinking off',
};

const TOGGLE_IDS = {
  think: 'toggle-think',
  showThink: 'toggle-show-think',
  trace: 'toggle-trace',
  auto: 'toggle-auto',
};

let effortMenuOpen = false;

function buildEffortMenu() {
  const menu = $('effort-menu');
  if (!menu) return;
  menu.innerHTML = EFFORTS.map(
    (effort) => `
    <button type="button" class="effort-option" role="option" data-effort="${effort}" aria-selected="false" tabindex="-1">
      <i data-lucide="check" class="effort-option-check"></i>
      <span class="effort-option-body">
        <strong>${effort}</strong>
        <span>${EFFORT_HINTS[effort]}</span>
      </span>
    </button>`,
  ).join('');
  menu.querySelectorAll('.effort-option').forEach((btn) => {
    btn.addEventListener('click', () => {
      setEffort(btn.dataset.effort);
      closeEffortMenu();
    });
  });
  icons();
}

function openEffortMenu() {
  const trigger = $('effort-trigger');
  const menu = $('effort-menu');
  if (!trigger || !menu || trigger.disabled) return;
  effortMenuOpen = true;
  trigger.classList.add('open');
  trigger.setAttribute('aria-expanded', 'true');
  menu.hidden = false;
}

function closeEffortMenu() {
  const trigger = $('effort-trigger');
  const menu = $('effort-menu');
  effortMenuOpen = false;
  trigger?.classList.remove('open');
  trigger?.setAttribute('aria-expanded', 'false');
  if (menu) menu.hidden = true;
}

function syncEffortDropdown(s) {
  const trigger = $('effort-trigger');
  const valueEl = $('effort-value');
  const menu = $('effort-menu');
  const effort = s.session.think_effort || 'medium';
  const enabled = s.session.think_mode && s.pendingToggle !== 'effort';

  if (valueEl) valueEl.textContent = effort;
  if (trigger) {
    trigger.disabled = !s.session.think_mode || s.pendingToggle === 'effort';
    trigger.classList.toggle('active', !!s.session.think_mode);
    trigger.classList.toggle('pending', s.pendingToggle === 'effort');
  }
  menu?.querySelectorAll('.effort-option').forEach((btn) => {
    const selected = btn.dataset.effort === effort;
    btn.classList.toggle('selected', selected);
    btn.setAttribute('aria-selected', selected ? 'true' : 'false');
  });
  if (!enabled) closeEffortMenu();
}

export function initToolbar() {
  $('toggle-think')?.addEventListener('click', () => toggleThink());
  $('toggle-trace')?.addEventListener('click', () => toggleTrace());
  $('toggle-auto')?.addEventListener('click', () => toggleAutoApprove());
  $('toggle-show-think')?.addEventListener('click', () => toggleShowThinkingUi());

  buildEffortMenu();

  $('effort-trigger')?.addEventListener('click', (e) => {
    e.stopPropagation();
    if (effortMenuOpen) closeEffortMenu();
    else openEffortMenu();
  });

  $('effort-trigger')?.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      openEffortMenu();
      menuFocusSelected();
    }
    if (e.key === 'Escape') closeEffortMenu();
  });

  $('effort-menu')?.addEventListener('keydown', (e) => {
    const options = [...($('effort-menu')?.querySelectorAll('.effort-option') || [])];
    const idx = options.indexOf(document.activeElement);
    if (e.key === 'Escape') {
      closeEffortMenu();
      $('effort-trigger')?.focus();
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      options[Math.min(idx + 1, options.length - 1)]?.focus();
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      options[Math.max(idx - 1, 0)]?.focus();
    }
  });

  document.addEventListener('click', (e) => {
    if (!$('effort-dropdown')?.contains(e.target)) closeEffortMenu();
  });

  subscribe(syncToolbar);
  syncToolbar(store);
}

function menuFocusSelected() {
  const selected = $('effort-menu')?.querySelector('.effort-option.selected');
  (selected || $('effort-menu')?.querySelector('.effort-option'))?.focus();
}

function setPending(toggleKey, pending) {
  patchStore({ pendingToggle: pending ? toggleKey : null });
  syncToolbar(store);
  syncComposerState();
}

async function patchServer(patch, toggleKey) {
  setPending(toggleKey, true);
  const prev = { ...store.session };
  patchSession(patch);
  syncToolbar(store);

  try {
    const res = await updateSettings(patch);
    if (res.settings) {
      applySettingsResponse(res.settings);
      if (toggleKey === 'trace') applySessionData(res.settings);
      else refreshThinkingVisibility();
    }
    showToast('Updated');
  } catch {
    patchSession(prev);
    showToast('Settings update failed', true);
  } finally {
    patchStore({ pendingToggle: null });
    syncToolbar(store);
    syncComposerState();
  }
}

function toggleThink() {
  patchServer({ think_mode: !store.session.think_mode }, 'think');
}

function toggleTrace() {
  patchServer({ show_internal: !store.session.show_internal }, 'trace');
}

function toggleAutoApprove() {
  patchServer({ auto_approve: !store.session.auto_approve }, 'auto');
}

function toggleShowThinkingUi() {
  store.showThinkingUi = !store.showThinkingUi;
  localStorage.setItem('jarvis-show-thinking', store.showThinkingUi ? '1' : '0');
  syncToolbar(store);
  refreshThinkingVisibility();
  showToast(store.showThinkingUi ? 'Thinking visible' : 'Thinking hidden');
}

function setEffort(value) {
  if (!EFFORTS.includes(value)) return;
  patchServer({ think_effort: value }, 'effort');
}

function setToggleState(el, { on, pending, onLabel, offLabel, warn = false }) {
  if (!el) return;
  el.classList.toggle('active', !!on && !warn);
  el.classList.toggle('active-warn', !!on && warn);
  el.classList.toggle('inactive', !on);
  el.classList.toggle('pending', !!pending);
  el.disabled = !!pending;
  el.setAttribute('aria-pressed', on ? 'true' : 'false');

  let badge = el.querySelector('.state-badge');
  if (!badge) {
    badge = document.createElement('span');
    badge.className = 'state-badge';
    el.appendChild(badge);
  }
  if (pending) badge.textContent = '…';
  else badge.textContent = on ? onLabel : offLabel;
}

function syncToolbar(s) {
  setToggleState($('toggle-think'), {
    on: s.session.think_mode,
    pending: s.pendingToggle === 'think',
    onLabel: 'ON',
    offLabel: 'OFF',
  });
  setToggleState($('toggle-trace'), {
    on: s.session.show_internal,
    pending: s.pendingToggle === 'trace',
    onLabel: 'ON',
    offLabel: 'OFF',
  });
  setToggleState($('toggle-auto'), {
    on: s.session.auto_approve,
    pending: s.pendingToggle === 'auto',
    onLabel: 'ON',
    offLabel: 'OFF',
    warn: true,
  });
  setToggleState($('toggle-show-think'), {
    on: s.showThinkingUi && s.session.show_internal,
    pending: false,
    onLabel: 'ON',
    offLabel: 'OFF',
  });
  $('toggle-show-think')?.toggleAttribute('disabled', !s.session.show_internal);

  syncEffortDropdown(s);
  icons();
}

export function loadUiPrefs() {
  store.showThinkingUi = localStorage.getItem('jarvis-show-thinking') !== '0';
}
