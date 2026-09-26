/** Pickers: sessions, models, agents, skills, MCP servers.
 *
 * One dialog, one keyboard model (↑ ↓ Enter, Esc) — each kind only supplies
 * its rows and what picking a row does.
 */
import { $, escapeHtml, showToast, debounce } from './utils.js';
import { icon } from './icons.js';
import { store } from './store.js';
import { openModal, closeModal, isModalOpen, listNav } from './modal.js';
import { runAction } from './actions.js';
import { renderMarkdown, applyMarkdownLinks } from './markdown.js';
import {
  pickerAction,
  fetchSessions,
  fetchModels,
  fetchAgents,
  fetchSkills,
  fetchSkill,
  fetchMcpServers,
} from './api.js';

let current = null; // { kind, spec, rows }
let nav = null;
let loadSeq = 0;

// ─── Shell ────────────────────────────────────────────────────────────────

function rowHtml(row, idx) {
  if (row.section) return `<div class="list-section" role="presentation">${escapeHtml(row.section)}</div>`;
  return `
    <div class="list-row${row.current ? ' is-current' : ''}" role="option" data-idx="${idx}" aria-selected="false"${row.disabled ? ' aria-disabled="true"' : ''}>
      <span class="lr-icon">${row.emoji ? escapeHtml(row.emoji) : icon(row.icon || 'circle')}</span>
      <span class="lr-body">
        <span class="lr-title">${escapeHtml(row.title)}</span>
        ${row.sub ? `<span class="lr-sub${row.wrap ? ' lr-sub-wrap' : ''}">${escapeHtml(row.sub)}</span>` : ''}
      </span>
      ${row.meta || ''}
    </div>`;
}

function paintRows(rows, emptyHtml) {
  const list = $('picker-list');
  current.rows = rows;
  if (!rows.some((r) => !r.section)) {
    list.innerHTML = `<div class="list-empty">${emptyHtml}</div>`;
    return;
  }
  list.innerHTML = rows.map(rowHtml).join('');
  const cur = rows.filter((r) => !r.section && !r.disabled).findIndex((r) => r.current);
  nav.reset(0);
  if (cur > 0) nav.setCursor(cur);
  current.spec.bindRows?.(list);
}

function setLoading() {
  $('picker-list').innerHTML = `<div class="list-loading">${'<div class="skeleton"></div>'.repeat(4)}</div>`;
}

async function reload() {
  if (!current) return;
  const seq = ++loadSeq;
  const q = ($('picker-search')?.value || '').trim();
  if (!current.rows) setLoading();
  try {
    const { rows, empty } = await current.spec.load(q);
    if (seq !== loadSeq || !current) return;
    paintRows(rows, empty || '<strong>Nothing here yet</strong>');
  } catch {
    if (seq !== loadSeq || !current) return;
    current.rows = [];
    $('picker-list').innerHTML = '<div class="list-empty"><strong>Could not load this list</strong>Check that Jarvis is still running, then try again.</div>';
  }
}

const reloadSoon = debounce(reload, 160);

function renderChips() {
  const box = $('picker-chips');
  box.innerHTML = current.spec.chips ? current.spec.chips() : '';
  current.spec.bindChips?.(box);
}

function renderFoot() {
  const foot = $('picker-foot');
  foot.innerHTML = current.spec.foot ? current.spec.foot() : '';
  current.spec.bindFoot?.(foot);
}

function showDetail(html) {
  const detail = $('picker-detail');
  $('picker-list').hidden = true;
  document.querySelector('#picker .search-row').hidden = true;
  detail.hidden = false;
  detail.innerHTML = html;
  detail.scrollTop = 0;
  detail.querySelector('.detail-back')?.addEventListener('click', hideDetail);
  applyMarkdownLinks(detail);
  detail.querySelector('.detail-back')?.focus();
}

function hideDetail() {
  $('picker-detail').hidden = true;
  $('picker-list').hidden = false;
  document.querySelector('#picker .search-row').hidden = false;
  $('picker-search')?.focus();
}

function open(kind) {
  const spec = SPECS[kind];
  if (!spec) return;
  current = { kind, spec, rows: null };
  spec.init?.();
  $('picker-title').textContent = spec.title;
  $('picker-sub').textContent = spec.sub;
  $('picker-icon').innerHTML = icon(spec.icon);
  const search = $('picker-search');
  search.value = '';
  search.placeholder = spec.placeholder || 'Search';
  hideDetail();
  renderChips();
  renderFoot();
  setLoading();
  openModal('picker', { focus: search, onClose: () => { current = null; } });
  reload();
}

export function closePicker() {
  closeModal('picker');
}

async function pickAndClose(action, data, msg) {
  const res = await runAction(action, data, msg);
  if (res.ok && isModalOpen('picker')) closePicker();
  return res;
}

function seg(options, value) {
  return `<div class="seg" role="group">${options.map((o) => `
    <button type="button" data-val="${o.value}" aria-pressed="${o.value === value}">${escapeHtml(o.label)}</button>`).join('')}</div>`;
}

function bindSeg(box, onChange) {
  box.querySelectorAll('.seg button').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (btn.getAttribute('aria-pressed') === 'true') return;
      onChange(btn.dataset.val === 'true');
    });
  });
}

const SCOPES = [
  { value: 'false', label: 'This project' },
  { value: 'true', label: 'Project + global' },
];

// ─── Kinds ────────────────────────────────────────────────────────────────

let sessionsCache = [];
let confirmDelete = null;

const sessionSpec = {
  title: 'Sessions',
  sub: 'Resume a saved conversation',
  icon: 'history',
  placeholder: 'Search by title, model or number',
  init() { sessionsCache = []; confirmDelete = null; },
  async load(q) {
    if (!sessionsCache.length) sessionsCache = (await fetchSessions(100)).sessions || [];
    const ql = q.toLowerCase();
    const list = sessionsCache.filter((s) => !ql
      || String(s.id).includes(ql)
      || (s.title || '').toLowerCase().includes(ql)
      || (s.model || '').toLowerCase().includes(ql));
    return {
      rows: list.map((s) => ({
        id: s.id,
        icon: s.active ? 'message-square-dot' : 'message-square',
        title: s.title,
        sub: `${s.model || 'unknown model'}, ${s.msg_count} messages, ${s.updated_label || ''}`,
        current: s.active,
        meta: s.active
          ? '<span class="badge is-live">Open now</span>'
          : `<button type="button" class="row-btn is-icon" data-del="${s.id}" aria-label="Delete session ${s.id}" title="Delete">${icon('trash-2')}</button>`,
        pick: () => pickAndClose('session_resume', { session_id: s.id }, 'Session resumed'),
      })),
      empty: q ? '<strong>No sessions match</strong>Try a different word or the session number.' : '<strong>No saved sessions yet</strong>Conversations are saved as you chat.',
    };
  },
  bindRows(list) {
    list.querySelectorAll('[data-del]').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const sid = Number(btn.dataset.del);
        if (confirmDelete !== sid) {
          confirmDelete = sid;
          list.querySelectorAll('[data-del]').forEach((b) => {
            b.classList.remove('is-danger');
            b.classList.add('is-icon');
            b.innerHTML = icon('trash-2');
          });
          btn.classList.add('is-danger');
          btn.classList.remove('is-icon');
          btn.textContent = 'Delete';
          return;
        }
        btn.disabled = true;
        const res = await pickerAction('session_delete', { session_id: sid });
        if (res.ok) {
          showToast('Session deleted');
          sessionsCache = sessionsCache.filter((s) => s.id !== sid);
          confirmDelete = null;
          reload();
          document.dispatchEvent(new Event('jarvis:sessions-changed'));
        } else {
          btn.disabled = false;
          showToast(res.error || 'Could not delete the session', true);
        }
      });
    });
  },
  foot: () => '<span class="spacer"></span><button type="button" class="btn btn-primary" data-foot="new">New chat</button>',
  bindFoot(foot) {
    foot.querySelector('[data-foot="new"]')?.addEventListener('click', () => pickAndClose('session_new', {}, 'New chat started'));
  },
};

const modelSpec = {
  title: 'Models',
  sub: 'Used for the next message',
  icon: 'cpu',
  placeholder: 'Search models or providers',
  async load(q) {
    const data = await fetchModels(q);
    const rows = [];
    let group = null;
    for (const m of data.models || []) {
      if (m.source_label !== group) {
        group = m.source_label;
        rows.push({ section: group });
      }
      rows.push({
        icon: m.active ? 'circle-check' : 'cpu',
        title: m.model_id,
        sub: m.description || '',
        current: m.active,
        meta: m.active ? '<span class="badge is-live">In use</span>' : '',
        pick: () => (m.active ? closePicker() : pickAndClose('model_select', { option_id: m.id }, `Model: ${m.model_id}`)),
      });
    }
    return { rows, empty: '<strong>No models match</strong>Try a provider name such as “anthropic”.' };
  },
};

let agentsGlobal = false;

const agentSpec = {
  title: 'Agents',
  sub: 'Adds the agent’s instructions to every message',
  icon: 'sparkles',
  placeholder: 'Search agents',
  init() { agentsGlobal = !!store.session.global_agents; },
  async load(q) {
    const data = await fetchAgents(agentsGlobal);
    agentsGlobal = !!data.global_agents;
    const ql = q.toLowerCase();
    const agents = (data.agents || []).filter((a) => !ql || a.name.toLowerCase().includes(ql) || (a.description || '').toLowerCase().includes(ql));
    const rows = [];
    if (!ql || 'no agent off none'.includes(ql)) {
      rows.push({
        icon: 'circle-off',
        title: 'No agent',
        sub: 'Base system prompt only',
        current: !data.active,
        pick: () => pickAndClose('agent_select', { name: '__off__' }, 'Agent turned off'),
      });
    }
    for (const a of agents) {
      rows.push({
        emoji: a.icon || '',
        icon: 'sparkles',
        title: a.name,
        sub: a.description || '',
        current: a.active,
        meta: `<span class="badge">${a.scope === 'global' ? 'Global' : 'Project'}</span>`,
        pick: () => pickAndClose('agent_select', { name: a.name }, `Agent: ${a.name}`),
      });
    }
    const hidden = data.hidden_global_count ? `${data.hidden_global_count} global agents are hidden. ` : '';
    return { rows, empty: `<strong>No agents found</strong>${hidden}Add one under .harness/agents/.` };
  },
  chips: () => seg(SCOPES, String(agentsGlobal)),
  bindChips(box) {
    bindSeg(box, async (val) => {
      agentsGlobal = val;
      renderChips();
      await pickerAction('agents_scope', { global_agents: val });
      reload();
    });
  },
};

let skillsGlobal = false;

const skillSpec = {
  title: 'Skills',
  sub: 'Jarvis loads a skill when a task matches it',
  icon: 'book-open',
  placeholder: 'Search skills',
  init() { skillsGlobal = !!store.session.global_skills; },
  async load(q) {
    const data = await fetchSkills(skillsGlobal, q);
    skillsGlobal = !!data.global_skills;
    const rows = (data.skills || []).map((s) => ({
      icon: 'book-open',
      title: s.name,
      sub: s.description || '',
      wrap: true,
      meta: `<span class="badge">${s.scope === 'global' ? 'Global' : 'Project'}</span>`,
      pick: () => previewSkill(s.name),
    }));
    const hidden = data.hidden_global_count ? `${data.hidden_global_count} global skills are hidden — switch to Project + global.` : 'Add one under .harness/skills/<name>/SKILL.md.';
    return { rows, empty: `<strong>No skills found</strong>${hidden}` };
  },
  chips: () => seg(SCOPES, String(skillsGlobal)),
  bindChips(box) {
    bindSeg(box, async (val) => {
      skillsGlobal = val;
      renderChips();
      await pickerAction('skills_scope', { global_skills: val });
      reload();
    });
  },
};

async function previewSkill(name) {
  try {
    const data = await fetchSkill(name);
    showDetail(`
      <button type="button" class="btn btn-quiet detail-back">${icon('arrow-left')}<span>Back to skills</span></button>
      <div class="md">${renderMarkdown(data.content || '')}</div>`);
  } catch {
    showToast('Could not open that skill', true);
  }
}

let mcpGlobal = false;

function mcpBadge(h) {
  const cls = { live: 'is-live', warn: 'is-warn', failed: 'is-bad' }[h.status] || '';
  const label = {
    live: `${h.tool_count || 0} tools`,
    idle: 'Off',
    connecting: 'Connecting',
    failed: 'Failed',
    warn: 'Check',
  }[h.status] || h.status || 'Off';
  return `<span class="badge ${cls}" title="${escapeHtml(h.detail || h.summary || '')}">${escapeHtml(label)}</span>`;
}

const mcpSpec = {
  title: 'MCP servers',
  sub: 'Tool servers Jarvis can call',
  icon: 'plug',
  placeholder: 'Search servers',
  init() { mcpGlobal = !!store.session.global_mcp; },
  async load(q) {
    const data = await fetchMcpServers(q);
    mcpGlobal = !!data.global_mcp;
    renderChips();
    const rows = (data.servers || []).map((s) => {
      const h = s.health || {};
      const live = !!h.connected;
      return {
        name: s.name,
        live,
        icon: live ? 'plug-zap' : 'plug',
        title: s.name,
        sub: h.detail || s.endpoint || s.transport || '',
        meta: `${mcpBadge(h)}<button type="button" class="row-btn" data-mcp="${escapeHtml(s.name)}" data-live="${live}">${live ? 'Disconnect' : 'Connect'}</button>`,
        pick: () => toggleMcp(s.name, live),
      };
    });
    return { rows, empty: '<strong>No MCP servers configured</strong>Add them in .mcp.json or with /mcp in the terminal.' };
  },
  bindRows(list) {
    list.querySelectorAll('[data-mcp]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleMcp(btn.dataset.mcp, btn.dataset.live === 'true', btn);
      });
    });
  },
  chips: () => seg([
    { value: 'false', label: 'Project servers' },
    { value: 'true', label: 'Include global' },
  ], String(mcpGlobal)),
  bindChips(box) {
    bindSeg(box, async (val) => {
      mcpGlobal = val;
      renderChips();
      const res = await pickerAction('mcp_scope', { global_mcp: val });
      if (!res.ok) showToast(res.error || 'Could not change the scope', true);
      reload();
    });
  },
};

async function toggleMcp(name, live, btn) {
  const target = btn || $('picker-list').querySelector(`[data-mcp="${CSS.escape(name)}"]`);
  if (target) {
    target.disabled = true;
    target.textContent = live ? 'Disconnecting' : 'Connecting';
  }
  const res = await pickerAction(live ? 'mcp_disconnect' : 'mcp_connect', { name });
  if (res.ok) showToast(live ? `Disconnected ${name}` : `Connected ${name}`);
  else showToast(res.error || `Could not ${live ? 'disconnect' : 'connect'} ${name}`, true);
  reload();
}

const SPECS = {
  session: sessionSpec,
  model: modelSpec,
  agent: agentSpec,
  skill: skillSpec,
  mcp: mcpSpec,
};

export function openPickerByKind(kind) {
  open(kind);
}

export function initPickers() {
  const list = $('picker-list');
  nav = listNav(list, (idx) => {
    const row = current?.rows?.[idx];
    row?.pick?.();
  });
  $('picker-close')?.addEventListener('click', closePicker);
  $('picker-search')?.addEventListener('input', () => {
    if (current?.kind === 'session') reload();
    else reloadSoon();
  });
  $('picker-search')?.addEventListener('keydown', (e) => nav.handleKey(e));
  $('picker-detail')?.addEventListener('keydown', (e) => {
    if (e.key === 'Backspace' && e.target === e.currentTarget) hideDetail();
  });
}
