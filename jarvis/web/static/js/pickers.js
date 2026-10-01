/** Pickers: sessions, models, agents.
 *
 * One dialog, one keyboard model (↑ ↓ Enter, Esc) — each kind only supplies
 * its rows and what picking a row does. Providers, Skills and MCP servers are
 * dialogs of their own (forms, add-by-link, sign-in) — `open` hands them off.
 */
import { $, escapeHtml, showToast, debounce, originBadge, storageGet, storageSet } from './utils.js';
import { icon } from './icons.js';
import { store } from './store.js';
import { openModal, closeModal, isModalOpen, listNav } from './modal.js';
import { runAction } from './actions.js';
import { openProviders } from './providers.js';
import { openMcp } from './mcp.js';
import { openSkills } from './skills.js';
import {
  pickerAction,
  fetchSessions,
  fetchModels,
  fetchAgents,
} from './api.js';

let current = null; // { kind, spec, rows }
let nav = null;
let loadSeq = 0;

// ─── Shell ────────────────────────────────────────────────────────────────

function rowHtml(row, idx) {
  if (row.section) return `<div class="list-section" role="presentation">${escapeHtml(row.section)}</div>`;
  // `node`: rows that hang off a drawer's rail (models) show a dot on it instead of an icon tile.
  const lead = row.node
    ? '<span class="lr-node" aria-hidden="true"></span>'
    : `<span class="lr-icon">${row.emoji ? escapeHtml(row.emoji) : icon(row.icon || 'circle')}</span>`;
  return `
    <div class="list-row${row.current ? ' is-current' : ''}${row.node ? ' has-node' : ''}" role="option" data-idx="${idx}" aria-selected="false"${row.disabled ? ' aria-disabled="true"' : ''}>
      ${lead}
      <span class="lr-body">
        <span class="lr-title">${escapeHtml(row.title)}</span>
        ${row.sub ? `<span class="lr-sub${row.wrap ? ' lr-sub-wrap' : ''}">${escapeHtml(row.sub)}</span>` : ''}
      </span>
      ${row.meta || ''}
    </div>`;
}

/** Rows the keyboard can reach (not inside a closed drawer) — same rule as listNav. */
function reachableRows(list) {
  return [...list.querySelectorAll('.list-row:not([aria-disabled="true"])')]
    .filter((el) => !el.closest('.mdrawer:not(.is-open)'));
}

function paintRows(rows, emptyHtml) {
  const list = $('picker-list');
  current.rows = rows;
  if (!rows.some((r) => !r.section || r.group)) {
    list.innerHTML = `<div class="list-empty">${emptyHtml}</div>`;
    return;
  }
  // A kind may lay its rows out itself (the model list's provider drawers).
  list.innerHTML = current.spec.renderRows ? current.spec.renderRows(rows) : rows.map(rowHtml).join('');
  const cur = reachableRows(list).findIndex((el) => el.classList.contains('is-current'));
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
  // Small controls at the end of the search bar (the model list's "Collapse all").
  const extra = $('picker-search-extra');
  if (extra) {
    extra.innerHTML = current.spec.searchExtra ? current.spec.searchExtra() : '';
    current.spec.bindSearchExtra?.(extra);
  }
}

function renderFoot() {
  const foot = $('picker-foot');
  foot.innerHTML = current.spec.foot ? current.spec.foot() : '';
  current.spec.bindFoot?.(foot);
}

function hideDetail() {
  $('picker-detail').hidden = true;
  $('picker-list').hidden = false;
  document.querySelector('#picker .search-row').hidden = false;
  $('picker-search')?.focus();
}

function open(kind, arg = '') {
  // Providers is its own dialog (rows with forms), not a pick-one list.
  if (kind === 'provider') {
    if (isModalOpen('picker')) closePicker();
    openProviders(arg);
    return;
  }
  if (kind === 'mcp') {
    if (isModalOpen('picker')) closePicker();
    openMcp(arg);
    return;
  }
  if (kind === 'skill') {
    if (isModalOpen('picker')) closePicker();
    openSkills();
    return;
  }
  const spec = SPECS[kind];
  if (!spec) return;
  current = { kind, spec, rows: null };
  spec.init?.();
  $('picker-title').textContent = spec.title;
  $('picker-sub').textContent = spec.sub;
  $('picker-icon').innerHTML = icon(spec.icon);
  const search = $('picker-search');
  // A picker that searches can open pre-filtered (the tray's "pick one that sees images").
  search.value = spec.searchArg && arg ? arg : '';
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

// Providers folded in the model list (this browser only; a search shows everything).
const CLOSED_GROUPS_KEY = 'jarvis-model-groups-closed';

function closedGroups() {
  try {
    const raw = JSON.parse(storageGet(CLOSED_GROUPS_KEY, '[]'));
    return new Set(Array.isArray(raw) ? raw.filter((x) => typeof x === 'string') : []);
  } catch {
    return new Set();
  }
}

function saveClosedGroups(set) {
  storageSet(CLOSED_GROUPS_KEY, JSON.stringify([...set]));
}

/** "Anthropic API" → "A", "Harness Agent" → "H", "md:deepseek"-style labels too. */
function monogram(label) {
  const word = String(label || '?').trim().replace(/^[^a-z0-9]+/i, '');
  return (word[0] || '?').toUpperCase();
}

/** Header text: how many models, and "In use" while a closed drawer holds the current one. */
function drawerSummary(row) {
  const count = `${row.count} model${row.count === 1 ? '' : 's'}`;
  const live = row.hasActive && row.toggle && !row.open ? '<span class="mdrawer-live">In use</span>' : '';
  return `${live}<span class="mdrawer-count">${count}</span>`;
}

/** "Nemotron 3 Ultra — 1M ctx, free" → "Nemotron 3 Ultra — 1M ctx", "Big Model Free" → "Big Model"
 * (the Free tag says it). */
function freeLess(desc) {
  return String(desc || '').replace(/(?:,\s*|\s+[—–-]\s+|\s+|^)free\s*$/i, '').trim();
}

const modelSpec = {
  searchArg: true,
  title: 'Models',
  sub: 'Used for the next message',
  icon: 'cpu',
  placeholder: 'Search models, or type “free” or “vision”',
  data: null,
  query: '',
  init() {
    this.data = null;
    this.query = '';
  },
  async load(q) {
    this.data = await fetchModels(q);
    this.query = q;
    const built = this.build();
    renderChips();
    return built;
  },
  /** Groups by provider; each header folds its models away (remembered per browser). */
  groups() {
    const groups = [];
    const bySource = new Map();
    for (const m of this.data?.models || []) {
      let g = bySource.get(m.source);
      if (!g) {
        g = { source: m.source, label: m.source_label, models: [] };
        bySource.set(m.source, g);
        groups.push(g);
      }
      g.models.push(m);
    }
    return groups;
  },
  build() {
    const groups = this.groups();
    const searching = !!this.query;
    // While searching, every match shows; folding needs more than one provider.
    const foldable = !searching && groups.length > 1;
    const closed = foldable ? closedGroups() : new Set();
    const rows = [];
    // Keep the image slot when any row has one, so Free tags and image marks line up in columns.
    const imageSlot = (this.data?.models || []).some((m) => m.images);
    for (const g of groups) {
      rows.push({
        section: g.label, group: g.source, count: g.models.length, open: !closed.has(g.source),
        toggle: foldable, hasActive: g.models.some((m) => m.active),
      });
      // Every model is rendered, folded or not, so a drawer can slide open.
      for (const m of g.models) this.pushRow(rows, m, imageSlot);
    }
    return { rows, empty: '<strong>No models match</strong>Try a provider name such as “anthropic”, or add a provider below.' };
  },
  pushRow(rows, m, imageSlot) {
    // Tags on the right, always in this order: free to use · can see images
    // (attachments reach it as pictures) · in use.
    const tags = [
      m.free ? '<span class="lr-free" title="Free to use: no cost per message">Free</span>' : '',
      m.images ? `<span class="lr-cap" title="Can see images: attached photos and screenshots reach it as pictures" aria-label="Can see images">${icon('image')}</span>`
        : imageSlot ? '<span class="lr-cap is-empty" aria-hidden="true"></span>' : '',
      m.active ? '<span class="badge is-live">In use</span>' : '',
    ].join('');
    rows.push({
      node: true,
      title: m.model_id,
      // The Free tag says it now; drop the trailing "free" from the description.
      sub: m.free ? freeLess(m.description) : m.description || '',
      current: m.active,
      meta: tags ? `<span class="lr-tags">${tags}</span>` : '',
      pick: () => (m.active ? closePicker() : pickAndClose('model_select', { option_id: m.id }, `Model: ${m.model_id}`)),
    });
  },
  /** One drawer per provider: a header that opens and closes it, and its models on a rail. */
  renderRows(rows) {
    let html = '';
    let inDrawer = false;
    const close = () => {
      if (inDrawer) html += '</div></div></div></section>';
      inDrawer = false;
    };
    rows.forEach((row, idx) => {
      if (!row.section) {
        html += rowHtml(row, idx);
        return;
      }
      close();
      const id = `mdrawer-${idx}`;
      html += `
        <section class="mdrawer${row.open ? ' is-open' : ''}${row.toggle ? '' : ' is-static'}${row.hasActive ? ' has-active' : ''}" data-drawer="${escapeHtml(row.group)}">
          <button type="button" class="mdrawer-head" data-group="${escapeHtml(row.group)}" aria-expanded="${row.open}" aria-controls="${id}"${row.toggle ? '' : ' disabled'}>
            <span class="mdrawer-mono" aria-hidden="true">${escapeHtml(monogram(row.section))}</span>
            <span class="mdrawer-name">${escapeHtml(row.section)}</span>
            <span class="mdrawer-sum">${drawerSummary(row)}</span>
            ${row.toggle ? `<span class="mdrawer-chev" aria-hidden="true">${icon('chevron-down')}</span>` : ''}
          </button>
          <div class="fold" id="${id}"><div class="fold-inner"><div class="mdrawer-body" role="group" aria-label="${escapeHtml(row.section)} models">`;
      inDrawer = true;
    });
    close();
    return html;
  },
  toggleGroup(source) {
    const closed = closedGroups();
    const opening = closed.has(source);
    if (opening) closed.delete(source);
    else closed.add(source);
    saveClosedGroups(closed);
    this.applyOpen();
  },
  setAllGroups(open) {
    saveClosedGroups(open ? new Set() : new Set(this.groups().map((g) => g.source)));
    this.applyOpen();
  },
  /** Open / close drawers in place (the fold animates); no re-render, no scroll jump. */
  applyOpen() {
    const list = $('picker-list');
    const closed = closedGroups();
    for (const row of current?.rows || []) {
      if (!row.section) continue;
      row.open = !closed.has(row.group);
      const drawer = list.querySelector(`[data-drawer="${CSS.escape(row.group)}"]`);
      if (!drawer) continue;
      drawer.classList.toggle('is-open', row.open);
      const head = drawer.querySelector('.mdrawer-head');
      head.setAttribute('aria-expanded', String(row.open));
      head.querySelector('.mdrawer-sum').innerHTML = drawerSummary(row);
    }
    // Keep the keyboard cursor on something still visible.
    const reach = reachableRows(list);
    const at = reach.findIndex((el) => el.classList.contains('is-cursor'));
    if (at === -1 && reach.length) nav.setCursor(0, false);
    else nav.paint(false);
    renderChips();
  },
  bindRows(list) {
    list.querySelectorAll('[data-group]').forEach((btn) => {
      btn.addEventListener('click', () => this.toggleGroup(btn.dataset.group));
    });
  },
  searchExtra() {
    const groups = this.groups();
    if (this.query || groups.length < 2) return '';
    const closed = closedGroups();
    const anyOpen = groups.some((g) => !closed.has(g.source));
    const label = anyOpen ? 'Collapse all' : 'Expand all';
    return `<button type="button" class="search-fold" data-fold="${anyOpen ? 'close' : 'open'}" title="${label} providers" aria-label="${label} providers">${icon(anyOpen ? 'chevron-up' : 'chevron-down')}<span>${label}</span></button>`;
  },
  bindSearchExtra(box) {
    box.querySelector('[data-fold]')?.addEventListener('click', (e) => {
      this.setAllGroups(e.currentTarget.dataset.fold === 'open');
    });
  },
  // Models only list providers that are set up — adding one is a click away.
  foot: () => `<span class="picker-foot-note">Missing a provider?</span><span class="spacer"></span>
    <button type="button" class="btn" data-foot="providers">${icon('key-round')}<span>Add a provider</span></button>`,
  bindFoot(foot) {
    foot.querySelector('[data-foot="providers"]')?.addEventListener('click', () => open('provider'));
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
        meta: originBadge(a.tool_label, a.also_labels, `${a.scope === 'global' ? 'Global' : 'Project'} · ${a.source_tag || ''}`),
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

const SPECS = {
  session: sessionSpec,
  model: modelSpec,
  agent: agentSpec,
};

export function openPickerByKind(kind, arg = '') {
  open(kind, arg);
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
