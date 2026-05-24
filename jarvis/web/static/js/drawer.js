/** Session info drawer + compact mobile summary */
import { $, escapeHtml, icons } from './utils.js';
import { store, subscribe } from './store.js';

function shortModel(name) {
  if (!name) return '';
  if (name.length <= 22) return name;
  return `${name.slice(0, 20)}…`;
}

function sessionSummary(s) {
  const parts = [];
  if (s.session.model) parts.push(shortModel(s.session.model));
  if (s.session.agent) parts.push(s.session.agent);
  if (s.session.session_id) parts.push(`#${s.session.session_id}`);
  return parts.length ? parts.join(' · ') : 'Connected to your session';
}

export function initDrawer() {
  $('info-btn')?.addEventListener('click', openDrawer);
  $('drawer-close')?.addEventListener('click', closeDrawer);
  $('drawer-backdrop')?.addEventListener('click', closeDrawer);
  $('session-sub')?.addEventListener('click', openDrawer);

  subscribe(renderMetaBar);
  renderMetaBar(store);
}

function openDrawer() {
  $('drawer')?.classList.add('open');
  $('drawer-backdrop')?.classList.add('open');
  syncDrawer(store);
  icons();
}

function closeDrawer() {
  $('drawer')?.classList.remove('open');
  $('drawer-backdrop')?.classList.remove('open');
}

function syncDrawer(s) {
  const status = !s.connected ? 'Offline' : s.busy ? 'Working' : 'Ready';
  $('drawer-status') && ($('drawer-status').textContent = status);
  $('drawer-model') && ($('drawer-model').textContent = s.session.model || '—');
  $('drawer-agent') && ($('drawer-agent').textContent = s.session.agent || 'default');
  $('drawer-session') && ($('drawer-session').textContent = s.session.session_id ? `#${s.session.session_id}` : '—');
  $('drawer-tokens') && ($('drawer-tokens').textContent = `${s.session.tokens_in} / ${s.session.tokens_out} / ${s.session.tokens_total}`);
  $('drawer-tools') && ($('drawer-tools').textContent = String(s.session.tool_calls || 0));
}

export function renderMetaBar(s) {
  syncDrawer(s);

  const sub = $('session-sub');
  if (sub) {
    sub.textContent = sessionSummary(s);
    sub.classList.add('session-summary');
    sub.title = 'Tap for session details';
  }

  const bar = $('meta-bar');
  if (!bar) return;

  const tags = [];
  if (s.session.model) {
    tags.push(`<span class="meta-tag" title="${escapeHtml(s.session.model)}"><i data-lucide="cpu"></i>${escapeHtml(shortModel(s.session.model))}</span>`);
  }
  if (s.session.agent) {
    tags.push(`<span class="meta-tag"><i data-lucide="sparkles"></i>${escapeHtml(s.session.agent)}</span>`);
  }
  if (s.session.session_id) {
    tags.push(`<span class="meta-tag"><i data-lucide="hash"></i>${escapeHtml(String(s.session.session_id))}</span>`);
  }
  if (s.session.think_mode) {
    tags.push(`<span class="meta-tag"><i data-lucide="brain"></i>${escapeHtml(s.session.think_effort)}</span>`);
  }
  if (s.session.show_internal) {
    tags.push(`<span class="meta-tag meta-tag-accent"><i data-lucide="list-tree"></i>trace</span>`);
  }
  if (s.session.auto_approve) {
    tags.push(`<span class="meta-tag meta-tag-warn"><i data-lucide="shield"></i>auto</span>`);
  }
  bar.innerHTML = tags.join('');
  icons();
}

export { openDrawer, closeDrawer };
