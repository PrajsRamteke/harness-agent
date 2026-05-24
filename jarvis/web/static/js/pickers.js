/** Interactive web pickers — sessions, models, agents, skills, MCP */
import { $, escapeHtml, icons, showToast } from './utils.js';
import { loadSnapshot, store } from './store.js';
import { applySessionData } from './stream.js';
import { renderMetaBar } from './drawer.js';
import {
  pickerAction,
  fetchSessions,
  fetchModels,
  fetchAgents,
  fetchSkills,
  fetchSkill,
  fetchMcpServers,
} from './api.js';

let ctx = null;
let searchTimer = null;

function applyActionState(res) {
  if (!res?.state) return;
  loadSnapshot(res.state);
  applySessionData(res.state);
  renderMetaBar(store);
}

export { applyActionState };

function closePicker() {
  $('picker-overlay')?.classList.remove('open');
  $('picker-detail')?.classList.add('hidden');
  $('picker-list')?.classList.remove('hidden');
  ctx = null;
}

function setLoading() {
  const list = $('picker-list');
  if (list) list.innerHTML = '<div class="picker-loading">Loading…</div>';
}

function setTitle(title, sub, iconName) {
  $('picker-title') && ($('picker-title').textContent = title);
  $('picker-sub') && ($('picker-sub').textContent = sub || '');
  const icon = $('picker-icon');
  if (icon) icon.innerHTML = `<i data-lucide="${iconName || 'list'}"></i>`;
}

function renderFoot(buttons) {
  const foot = $('picker-foot');
  if (!foot) return;
  foot.innerHTML = (buttons || [])
    .map(
      (b) =>
        `<button type="button" class="btn ${b.className || 'btn-ghost'}" data-foot="${b.id}">${escapeHtml(b.label)}</button>`,
    )
    .join('');
  buttons?.forEach((b) => {
    foot.querySelector(`[data-foot="${b.id}"]`)?.addEventListener('click', b.onClick);
  });
}

function renderToolbar(chips) {
  const bar = $('picker-toolbar');
  if (!bar) return;
  bar.innerHTML = (chips || [])
    .map(
      (c) =>
        `<button type="button" class="picker-chip${c.active ? ' active' : ''}" data-chip="${c.id}">${escapeHtml(c.label)}</button>`,
    )
    .join('');
  chips?.forEach((c) => {
    bar.querySelector(`[data-chip="${c.id}"]`)?.addEventListener('click', c.onClick);
  });
}

function bindSearch(onSearch) {
  const input = $('picker-search');
  if (!input) return;
  input.value = '';
  input.oninput = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => onSearch(input.value.trim()), 180);
  };
}

async function runAction(action, data, successMsg) {
  const res = await pickerAction(action, data);
  if (res.ok) {
    applyActionState(res);
    if (successMsg) showToast(successMsg);
    closePicker();
    return res;
  }
  if (res.state) applyActionState(res);
  showToast(res.error || 'Action failed', true);
  if (res.state) closePicker();
  return null;
}

function openPickerShell({ title, sub, icon, foot, toolbar, onSearch, load }) {
  ctx = { load, onSearch };
  $('picker-overlay')?.classList.add('open');
  $('picker-detail')?.classList.add('hidden');
  $('picker-list')?.classList.remove('hidden');
  setTitle(title, sub, icon);
  renderFoot(foot);
  renderToolbar(toolbar);
  bindSearch(onSearch || (() => load()));
  setLoading();
  load();
  icons();
  setTimeout(() => $('picker-search')?.focus(), 80);
}

// ─── Sessions ───────────────────────────────────────────────────────────

export function openSessionPicker() {
  openPickerShell({
    title: 'Sessions',
    sub: 'Select a session to resume',
    icon: 'history',
    foot: [
      {
        id: 'new',
        label: 'New session',
        className: 'btn-ok',
        onClick: () => runAction('session_new', {}, 'New session started'),
      },
    ],
    onSearch: () => loadSessions(),
    load: loadSessions,
  });
}

async function loadSessions() {
  setLoading();
  try {
    const q = ($('picker-search')?.value || '').trim().toLowerCase();
    const data = await fetchSessions();
    let sessions = data.sessions || [];
    if (q) {
      sessions = sessions.filter(
        (s) =>
          String(s.id).includes(q) ||
          (s.title || '').toLowerCase().includes(q) ||
          (s.model || '').toLowerCase().includes(q),
      );
    }
    const list = $('picker-list');
    if (!sessions.length) {
      list.innerHTML = `<p class="picker-empty">${q ? 'No matching sessions' : 'No saved sessions yet'}</p>`;
      icons();
      return;
    }
    list.innerHTML = sessions
      .map(
        (s) => `
      <button type="button" class="picker-row${s.active ? ' active' : ''}" data-sid="${s.id}">
        <div class="picker-row-body">
          <strong>${escapeHtml(s.title)}</strong>
          <span>${escapeHtml(s.model || '—')} · ${s.msg_count} msgs · ${escapeHtml(s.updated_label || '')}</span>
        </div>
        <span class="picker-row-meta">#${s.id}</span>
        ${s.active ? '<span class="picker-badge live">active</span>' : `<button type="button" class="picker-del" data-del="${s.id}" aria-label="Delete">×</button>`}
      </button>`,
      )
      .join('');

    list.querySelectorAll('[data-sid]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        if (e.target.closest('.picker-del')) return;
        runAction('session_resume', { session_id: Number(btn.dataset.sid) }, `Resumed #${btn.dataset.sid}`);
      });
    });
    list.querySelectorAll('[data-del]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (!confirm(`Delete session #${btn.dataset.del}?`)) return;
        pickerAction('session_delete', { session_id: Number(btn.dataset.del) }).then((res) => {
          if (res.ok) {
            showToast('Session deleted');
            loadSessions();
          } else showToast(res.error || 'Delete failed', true);
        });
      });
    });
    icons();
  } catch {
    $('picker-list').innerHTML = '<p class="picker-empty">Could not load sessions</p>';
  }
}

// ─── Models ─────────────────────────────────────────────────────────────

export function openModelPicker() {
  openPickerShell({
    title: 'Models',
    sub: 'Switch AI model for this session',
    icon: 'cpu',
    onSearch: () => loadModels(),
    load: loadModels,
  });
}

async function loadModels() {
  setLoading();
  try {
    const q = $('picker-search')?.value || '';
    const data = await fetchModels(q);
    const models = data.models || [];
    const list = $('picker-list');
    if (!models.length) {
      list.innerHTML = '<p class="picker-empty">No models found</p>';
      return;
    }
    list.innerHTML = models
      .map(
        (m) => `
      <button type="button" class="picker-row${m.active ? ' active' : ''}" data-oid="${escapeHtml(m.id)}">
        <div class="picker-row-body">
          <strong>${escapeHtml(m.model_id)}</strong>
          <span>${escapeHtml(m.source_label)} · ${escapeHtml(m.description || '')}</span>
        </div>
        ${m.active ? '<span class="picker-badge live">active</span>' : ''}
      </button>`,
      )
      .join('');
    list.querySelectorAll('[data-oid]').forEach((btn) => {
      btn.addEventListener('click', () =>
        runAction('model_select', { option_id: btn.dataset.oid }, 'Model updated'),
      );
    });
    icons();
  } catch {
    $('picker-list').innerHTML = '<p class="picker-empty">Could not load models</p>';
  }
}

// ─── Agents ─────────────────────────────────────────────────────────────

let agentsGlobal = true;

export function openAgentPicker() {
  agentsGlobal = store.session?.global_agents !== false;
  openPickerShell({
    title: 'Agents',
    sub: 'Activate an agent profile',
    icon: 'sparkles',
    foot: [
      {
        id: 'off',
        label: 'No agent',
        className: 'btn-ghost',
        onClick: () => runAction('agent_select', { name: '__off__' }, 'Agent deactivated'),
      },
    ],
    toolbar: buildAgentToolbar(),
    onSearch: () => loadAgents(),
    load: loadAgents,
  });
}

function buildAgentToolbar() {
  return [
    {
      id: 'scope',
      label: agentsGlobal ? 'Global + project' : 'Project only',
      active: agentsGlobal,
      onClick: async () => {
        agentsGlobal = !agentsGlobal;
        await pickerAction('agents_scope', { global_agents: agentsGlobal });
        renderToolbar(buildAgentToolbar());
        loadAgents();
      },
    },
  ];
}

async function loadAgents() {
  setLoading();
  try {
    const q = ($('picker-search')?.value || '').trim().toLowerCase();
    const data = await fetchAgents(agentsGlobal);
    let agents = data.agents || [];
    if (q) {
      agents = agents.filter(
        (a) => a.name.toLowerCase().includes(q) || (a.description || '').toLowerCase().includes(q),
      );
    }
    const list = $('picker-list');
    if (!agents.length) {
      list.innerHTML = `<p class="picker-empty">${q ? 'No matching agents' : 'No agents found'}</p>`;
      return;
    }
    list.innerHTML = agents
      .map(
        (a) => `
      <button type="button" class="picker-row${a.active ? ' active' : ''}" data-agent="${escapeHtml(a.name)}">
        <div class="picker-row-body">
          <strong>${a.icon ? escapeHtml(a.icon) + ' ' : ''}${escapeHtml(a.name)}</strong>
          <span>${escapeHtml(a.description || a.scope)}</span>
        </div>
        ${a.active ? '<span class="picker-badge live">active</span>' : ''}
      </button>`,
      )
      .join('');
    list.querySelectorAll('[data-agent]').forEach((btn) => {
      btn.addEventListener('click', () =>
        runAction('agent_select', { name: btn.dataset.agent }, `Agent: ${btn.dataset.agent}`),
      );
    });
    icons();
  } catch {
    $('picker-list').innerHTML = '<p class="picker-empty">Could not load agents</p>';
  }
}

// ─── Skills ─────────────────────────────────────────────────────────────

let skillsGlobal = false;

export function openSkillPicker() {
  skillsGlobal = false;
  openPickerShell({
    title: 'Skills',
    sub: 'Browse skill packs (read-only preview)',
    icon: 'book-open',
    toolbar: buildSkillToolbar(),
    onSearch: () => loadSkills(),
    load: loadSkills,
  });
}

function buildSkillToolbar() {
  return [
    {
      id: 'scope',
      label: skillsGlobal ? 'Global + project' : 'Project only',
      active: skillsGlobal,
      onClick: async () => {
        skillsGlobal = !skillsGlobal;
        await pickerAction('skills_scope', { global_skills: skillsGlobal });
        renderToolbar(buildSkillToolbar());
        loadSkills();
      },
    },
  ];
}

async function loadSkills() {
  setLoading();
  try {
    const q = $('picker-search')?.value || '';
    const data = await fetchSkills(skillsGlobal, q);
    const skills = data.skills || [];
    const list = $('picker-list');
    if (!skills.length) {
      list.innerHTML = '<p class="picker-empty">No skills found</p>';
      return;
    }
    list.innerHTML = skills
      .map(
        (s) => `
      <button type="button" class="picker-row" data-skill="${escapeHtml(s.name)}">
        <div class="picker-row-body">
          <strong>${escapeHtml(s.name)}</strong>
          <span>${escapeHtml(s.description || s.scope)}</span>
        </div>
      </button>`,
      )
      .join('');
    list.querySelectorAll('[data-skill]').forEach((btn) => {
      btn.addEventListener('click', () => previewSkill(btn.dataset.skill));
    });
    icons();
  } catch {
    $('picker-list').innerHTML = '<p class="picker-empty">Could not load skills</p>';
  }
}

async function previewSkill(name) {
  try {
    const data = await fetchSkill(name);
    $('picker-list')?.classList.add('hidden');
    const detail = $('picker-detail');
    detail?.classList.remove('hidden');
    detail.innerHTML = `
      <p style="margin:0 0 8px;font-size:13px;color:var(--text-muted)">${escapeHtml(data.description || '')}</p>
      <pre>${escapeHtml(data.content || '')}</pre>
      <button type="button" class="btn btn-ghost" id="skill-back" style="margin-top:12px;width:100%">Back to list</button>`;
    $('skill-back')?.addEventListener('click', () => {
      detail.classList.add('hidden');
      $('picker-list')?.classList.remove('hidden');
    });
  } catch {
    showToast('Could not load skill', true);
  }
}

// ─── MCP ────────────────────────────────────────────────────────────────

let mcpGlobal = false;

export function openMcpPicker() {
  mcpGlobal = false;
  openPickerShell({
    title: 'MCP Servers',
    sub: 'Connect or disconnect tool servers',
    icon: 'plug',
    toolbar: buildMcpToolbar(),
    onSearch: () => loadMcp(),
    load: loadMcp,
  });
}

function buildMcpToolbar() {
  return [
    {
      id: 'scope',
      label: mcpGlobal ? 'Global scope on' : 'Project scope',
      active: mcpGlobal,
      onClick: async () => {
        mcpGlobal = !mcpGlobal;
        const res = await pickerAction('mcp_scope', { global_mcp: mcpGlobal });
        if (res.ok) showToast(mcpGlobal ? 'Global MCP enabled' : 'Project MCP only');
        else showToast(res.error || 'Scope change failed', true);
        renderToolbar(buildMcpToolbar());
        loadMcp();
      },
    },
  ];
}

async function loadMcp() {
  setLoading();
  try {
    const q = $('picker-search')?.value || '';
    const data = await fetchMcpServers(q);
    mcpGlobal = !!data.global_mcp;
    renderToolbar(buildMcpToolbar());
    const servers = data.servers || [];
    const list = $('picker-list');
    if (!servers.length) {
      list.innerHTML = '<p class="picker-empty">No MCP servers configured</p>';
      return;
    }
    list.innerHTML = servers
      .map((s) => {
        const h = s.health || {};
        const live = h.connected || h.status === 'live';
        const badge = live ? 'live' : h.status === 'failed' ? 'warn' : 'off';
        const label = h.summary || (live ? 'connected' : 'offline');
        return `
      <div class="picker-row" style="cursor:default">
        <div class="picker-row-body">
          <strong>${escapeHtml(s.name)}</strong>
          <span>${escapeHtml(s.endpoint || s.transport || '')}</span>
        </div>
        <span class="picker-badge ${badge}">${escapeHtml(label)}</span>
        <button type="button" class="btn btn-ok" data-mcp-connect="${escapeHtml(s.name)}" style="flex-shrink:0;padding:6px 10px;min-height:auto;font-size:12px" ${live ? 'disabled' : ''}>Connect</button>
        <button type="button" class="btn btn-ghost" data-mcp-disconnect="${escapeHtml(s.name)}" style="flex-shrink:0;padding:6px 10px;min-height:auto;font-size:12px" ${live ? '' : 'disabled'}>Off</button>
      </div>`;
      })
      .join('');

    list.querySelectorAll('[data-mcp-connect]').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const res = await pickerAction('mcp_connect', { name: btn.dataset.mcpConnect });
        if (res.ok) {
          showToast(`Connected ${btn.dataset.mcpConnect}`);
          loadMcp();
        } else showToast(res.error || 'Connect failed', true);
      });
    });
    list.querySelectorAll('[data-mcp-disconnect]').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const res = await pickerAction('mcp_disconnect', { name: btn.dataset.mcpDisconnect });
        if (res.ok) {
          showToast(`Disconnected ${btn.dataset.mcpDisconnect}`);
          loadMcp();
        } else showToast(res.error || 'Disconnect failed', true);
      });
    });
    icons();
  } catch {
    $('picker-list').innerHTML = '<p class="picker-empty">Could not load MCP servers</p>';
  }
}

// ─── Router ─────────────────────────────────────────────────────────────

const PICKER_MAP = {
  session: openSessionPicker,
  model: openModelPicker,
  agent: openAgentPicker,
  skill: openSkillPicker,
  mcp: openMcpPicker,
};

export function openPickerByKind(kind) {
  const fn = PICKER_MAP[kind];
  if (fn) fn();
}

export function initPickers() {
  $('picker-close')?.addEventListener('click', closePicker);
  $('picker-overlay')?.addEventListener('click', (e) => {
    if (e.target === $('picker-overlay')) closePicker();
  });
  $('picker-panel')?.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $('picker-overlay')?.classList.contains('open')) closePicker();
  });
}
