/** Composer quick settings: model, thinking effort, agent, trace, auto-approve */
import { $, escapeHtml } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe } from './store.js';
import { EFFORTS, EFFORT_HINTS } from './effort.js';
import { setEffort, setSetting, toggleSetting } from './actions.js';

const EFFORT_NAMES = {
  xhigh: 'Max',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  minimal: 'Minimal',
  none: 'Off',
};

let menuOpen = false;

function shortModel(id) {
  const s = String(id || '');
  const tail = s.includes('/') ? s.split('/').pop() : s;
  return tail.length > 22 ? `${tail.slice(0, 21)}…` : tail || 'Model';
}

function currentEffort() {
  return store.session.think_mode ? store.session.think_effort || 'high' : 'none';
}

function paintMenu() {
  const menu = $('effort-menu');
  const cur = currentEffort();
  // Highest first, "Off" last — same order as the terminal picker.
  menu.innerHTML = EFFORTS.map((e) => `
    <button type="button" class="effort-item" role="menuitemradio" data-effort="${e}" aria-checked="${e === cur}">
      <span class="ei-check">${e === cur ? icon('check') : ''}</span>
      <span class="ei-body"><strong>${escapeHtml(EFFORT_NAMES[e])}</strong><span>${escapeHtml(EFFORT_HINTS[e])}</span></span>
    </button>`).join('');
  menu.querySelectorAll('[data-effort]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const e = btn.dataset.effort;
      closeMenu();
      if (e === currentEffort()) return;
      if (e === 'none') setSetting({ think_mode: false }, 'think_mode');
      else setEffort(e);
    });
  });
}

function openMenu() {
  paintMenu();
  $('effort-menu').hidden = false;
  $('qc-effort').setAttribute('aria-expanded', 'true');
  menuOpen = true;
  $('effort-menu').querySelector('[aria-checked="true"]')?.focus();
}

function closeMenu() {
  if (!menuOpen) return;
  $('effort-menu').hidden = true;
  $('qc-effort').setAttribute('aria-expanded', 'false');
  menuOpen = false;
}

function render(s) {
  const model = $('qc-model-text');
  if (model) {
    model.textContent = shortModel(s.session.model);
    $('qc-model').title = `Model: ${s.session.model || 'unknown'}`;
  }
  const effort = $('qc-effort-text');
  if (effort) {
    const e = s.session.think_mode ? s.session.think_effort : 'none';
    effort.textContent = EFFORT_NAMES[e] || e;
    $('qc-effort').classList.toggle('is-off', !s.session.think_mode);
  }
  const agent = $('qc-agent-text');
  if (agent) {
    agent.textContent = s.session.agent || 'No agent';
    $('qc-agent').classList.toggle('is-off', !s.session.agent);
  }
  const trace = $('qc-trace');
  if (trace) {
    trace.setAttribute('aria-pressed', String(!!s.session.show_internal));
    trace.title = s.session.show_internal ? 'Tool trace is on' : 'Tool trace is off';
  }
  const auto = $('qc-auto');
  if (auto) {
    auto.setAttribute('aria-pressed', String(!!s.session.auto_approve));
    auto.title = s.session.auto_approve ? 'Auto-approve is on: commands run without asking' : 'Auto-approve is off';
  }
  if (menuOpen) paintMenu();
}

export function initQuickbar({ onOpenPicker }) {
  $('qc-model')?.addEventListener('click', () => onOpenPicker('model'));
  $('qc-agent')?.addEventListener('click', () => onOpenPicker('agent'));
  $('qc-trace')?.addEventListener('click', () => toggleSetting('show_internal'));
  $('qc-auto')?.addEventListener('click', () => toggleSetting('auto_approve'));
  $('qc-effort')?.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menuOpen) closeMenu();
    else openMenu();
  });
  $('effort-menu')?.addEventListener('keydown', (e) => {
    const items = [...$('effort-menu').querySelectorAll('.effort-item')];
    const i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(); $('qc-effort').focus(); }
  });
  document.addEventListener('click', (e) => {
    if (menuOpen && !e.target.closest('.tc-wrap')) closeMenu();
  });
  subscribe(render);
  render(store);
}
