/** Composer quick settings: model, thinking effort, agent, trace, auto-approve */
import { $, escapeHtml } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe } from './store.js';
import { EFFORT_NAMES, effortRows, effortNow, thinkInfo } from './effort.js';
import { setEffort, setSetting, toggleSetting } from './actions.js';

let menuOpen = false;

function shortModel(id) {
  const s = String(id || '');
  const tail = s.includes('/') ? s.split('/').pop() : s;
  return tail.length > 22 ? `${tail.slice(0, 21)}…` : tail || 'Model';
}

/** The menu's checked row: the level in use, or on / none for models without levels. */
function currentEffort() {
  const info = thinkInfo(store.session);
  if (info) return info.current;
  return store.session.think_mode ? store.session.think_effort || 'high' : 'none';
}

function paintMenu() {
  const menu = $('effort-menu');
  const cur = currentEffort();
  const info = thinkInfo(store.session);
  // What this model takes — levels it lacks stay listed (greyed, with the
  // reason) so a short list is explained, and can't be picked.
  const head = info
    ? `<div class="effort-note" role="presentation">${escapeHtml(info.summary || '')}${info.source ? ` <em>· ${escapeHtml(info.source)}</em>` : ''}</div>`
    : '';
  const note = info?.note ? `<div class="effort-note is-warn" role="presentation">${escapeHtml(info.note)}</div>` : '';
  menu.innerHTML = head + note + effortRows(store.session).map((r) => `
    <button type="button" class="effort-item${r.available ? '' : ' is-unavailable'}" role="menuitemradio" data-effort="${escapeHtml(r.value)}" aria-checked="${r.value === cur}" ${r.available ? '' : 'aria-disabled="true" tabindex="-1"'} title="${escapeHtml(r.available ? r.hint : r.why || r.hint)}">
      <span class="ei-check">${r.value === cur ? icon('check') : ''}</span>
      <span class="ei-body"><strong>${escapeHtml(r.label)}</strong><span>${escapeHtml(r.hint)}</span></span>
    </button>`).join('');
  menu.querySelectorAll('[data-effort]').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.getAttribute('aria-disabled') === 'true') return; // the server would refuse it anyway
      const e = btn.dataset.effort;
      closeMenu();
      if (e === currentEffort()) return;
      if (e === 'none') setSetting({ think_mode: false }, 'think_mode');
      else if (e === 'on') setSetting({ think_mode: true }, 'think_mode');
      else setEffort(e);
    });
  });
}

function openMenu() {
  paintMenu();
  $('effort-menu').hidden = false;
  $('qc-effort').setAttribute('aria-expanded', 'true');
  menuOpen = true;
  ($('effort-menu').querySelector('[aria-checked="true"]') || $('effort-menu').querySelector('.effort-item:not(.is-unavailable)'))?.focus();
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
    const now = effortNow(s.session);
    const word = now.on ? (EFFORT_NAMES[now.level || 'on'] || now.level) : EFFORT_NAMES.none;
    // A * says the model uses a different level than the one asked for.
    effort.textContent = `${word}${now.adjusted ? '*' : ''}`;
    const info = thinkInfo(s.session);
    $('qc-effort').classList.toggle('is-off', !now.on);
    $('qc-effort').title = now.note
      || (info ? `Thinking: ${info.summary}` : 'Thinking effort');
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
    const items = [...$('effort-menu').querySelectorAll('.effort-item:not(.is-unavailable)')];
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
