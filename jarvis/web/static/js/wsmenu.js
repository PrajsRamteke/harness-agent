/** The workspace's menus and dialogs (wsprojects.js draws the rows).
 *
 *   row ⋯ / right-click   Rename · Colour (swatches) · Links… · Start / Stop ·
 *                         Open terminal here · Remove from workspace
 *   a master group's ⋯   Open master chat · Edit members… · Rename · Colour ·
 *                         Write policy · Start / Stop · Open terminal here · Remove group
 *   New master group…   name + member checkboxes + write policy (openCreateGroup)
 *   Links…                which projects this one's agent may read, the write
 *                         policy (Ask / Allow / Deny) and the start-up mode
 *   Workspace settings    warm projects, idle stop, link all, delegation
 *
 * One dialog shell (`#wsdlg`, dialog.js shaped: header · body · footer) serves
 * all of them. Changes that widen what Jarvis can reach are refused for a
 * remote visit — wsPost shows the server's message then.
 */
import { $, escapeHtml, showToast } from './utils.js';
import { icon } from './icons.js';
import { openModal, closeModal } from './modal.js';
import { section } from './dialog.js';
import { createWorkspaceGroup } from './api.js';
import {
  wsPost, workspaceRows, workspaceProject, workspaceSettings, openWorkspaceProject, openTerminalIn,
  isGroupRow, workspacePlainRows, loadWorkspace, refreshWorkspace,
} from './wsprojects.js';
import { openFolders } from './folders.js';

const DLG = 'wsdlg';
export const SWATCHES = ['#7c5cff', '#2fb344', '#f59f00', '#e8590c', '#d6336c', '#1c7ed6', '#0ca678', '#ae3ec9'];
const SWATCH_NAMES = ['Violet', 'Green', 'Amber', 'Orange', 'Pink', 'Blue', 'Teal', 'Purple'];

// ─── The ⋯ menu ───────────────────────────────────────────────────────────

let menu = null;
let menuAnchor = null;
let menuOff = [];

export function closeMenu({ refocus = false } = {}) {
  menuOff.forEach((off) => off());
  menuOff = [];
  menu?.remove();
  menu = null;
  if (refocus && menuAnchor?.isConnected) menuAnchor.focus?.({ preventScroll: true });
  menuAnchor = null;
}

function menuItem(act, ic, label, { danger = false } = {}) {
  return `<button type="button" class="ws-mi${danger ? ' is-danger' : ''}" role="menuitem" data-m="${act}">${icon(ic)}<span>${label}</span></button>`;
}

/** The actions for one workspace project, anchored to its ⋯ button (or at a click `point`). */
export function openProjectMenu(anchor, row, point) {
  closeMenu();
  const running = row.status === 'running';
  const missing = row.status === 'missing';
  const swatches = SWATCHES.map((c, i) => `<button type="button" class="ws-swatch${c.toLowerCase() === String(row.color).toLowerCase() ? ' is-on' : ''}" data-color="${c}"
    style="--c:${c}" role="menuitemradio" aria-checked="${c.toLowerCase() === String(row.color).toLowerCase()}" aria-label="${SWATCH_NAMES[i]}" title="${SWATCH_NAMES[i]}"></button>`).join('');
  const el = document.createElement('div');
  el.className = 'ws-menu glass-strong';
  el.setAttribute('role', 'menu');
  el.setAttribute('aria-label', `${row.name} actions`);
  const group = isGroupRow(row);
  const colour = `<div class="ws-mi-colour" role="group" aria-label="Colour"><span class="ws-mi-lbl">Colour</span><span class="ws-swatches">${swatches}</span></div>`;
  const policy = `<div class="ws-mi-colour" role="group" aria-label="Write policy for members"><span class="ws-mi-lbl" title="What the master chat may do to member projects">Edits</span><span class="ws-pols">${
    POLICIES.map(([v, label, , short]) => `<button type="button" class="ws-pol${v === row.link_write ? ' is-on' : ''}" data-policy="${v}" role="menuitemradio" aria-checked="${v === row.link_write}" title="${escapeHtml(label)}">${short}</button>`).join('')}</span></div>`;
  el.innerHTML = group ? `
    ${missing ? '' : menuItem('open', 'globe', 'Open master chat')}
    ${menuItem('members', 'users', 'Edit members…')}
    ${menuItem('rename', 'pencil', 'Rename')}
    ${colour}
    ${policy}
    ${missing ? menuItem('locate', 'folder-open', 'Locate…')
    : running ? menuItem('stop', 'power', 'Stop') : menuItem('start', 'play', 'Start')}
    ${missing ? '' : menuItem('terminal', 'terminal', 'Open terminal here')}
    <div class="ws-mi-sep" role="separator"></div>
    ${menuItem('remove', 'trash-2', 'Remove group', { danger: true })}` : `
    ${menuItem('rename', 'pencil', 'Rename')}
    ${colour}
    ${menuItem('links', 'link', 'Links…')}
    ${missing ? menuItem('locate', 'folder-open', 'Locate…')
    : running ? menuItem('stop', 'power', 'Stop') : menuItem('start', 'play', 'Start')}
    ${missing ? '' : menuItem('terminal', 'terminal', 'Open terminal here')}
    <div class="ws-mi-sep" role="separator"></div>
    ${menuItem('remove', 'trash-2', 'Remove from workspace', { danger: true })}`;
  document.body.appendChild(el);
  menu = el;
  menuAnchor = anchor;
  // Place it under the button (or at the pointer), inside the window.
  const r = anchor.getBoundingClientRect();
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  let x = point ? point.x : r.right - w;
  let y = point ? point.y : r.bottom + 4;
  x = Math.max(8, Math.min(x, window.innerWidth - w - 8));
  if (y + h > window.innerHeight - 8) y = Math.max(8, (point ? point.y : r.top - 4) - h);
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
  anchor.setAttribute('aria-expanded', 'true');

  const onDown = (e) => { if (!el.contains(e.target)) closeMenu(); };
  const onKey = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      closeMenu({ refocus: true });
      return;
    }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const items = [...el.querySelectorAll('.ws-mi, .ws-swatch, .ws-pol')];
    const at = items.indexOf(document.activeElement);
    const next = e.key === 'ArrowDown' ? (at + 1) % items.length : (at <= 0 ? items.length - 1 : at - 1);
    items[next]?.focus();
  };
  document.addEventListener('pointerdown', onDown, true);
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('resize', closeMenu);
  window.addEventListener('blur', closeMenu);
  menuOff = [
    () => document.removeEventListener('pointerdown', onDown, true),
    () => document.removeEventListener('keydown', onKey, true),
    () => window.removeEventListener('resize', closeMenu),
    () => window.removeEventListener('blur', closeMenu),
    () => anchor.removeAttribute('aria-expanded'),
  ];
  el.addEventListener('click', (e) => {
    const sw = e.target.closest('[data-color]');
    if (sw) {
      closeMenu({ refocus: true });
      wsPost('update', { id: row.id, color: sw.dataset.color });
      return;
    }
    const pol = e.target.closest('[data-policy]');
    if (pol) {
      closeMenu({ refocus: true });
      if (pol.dataset.policy !== row.link_write) {
        wsPost('update', { id: row.id, link_write: pol.dataset.policy }).then((res) => {
          if (res.ok) showToast(`Master edits: ${POLICIES.find(([v]) => v === pol.dataset.policy)?.[1] || pol.dataset.policy}`);
        });
      }
      return;
    }
    const it = e.target.closest('[data-m]');
    if (!it) return;
    closeMenu();
    runMenu(it.dataset.m, workspaceProject(row.id) || row);
  });
  el.querySelector('.ws-mi')?.focus({ preventScroll: true });
}

function runMenu(act, row) {
  if (act === 'rename') openRename(row);
  else if (act === 'links') openLinks(row);
  else if (act === 'open') openWorkspaceProject(row.id);
  else if (act === 'members') openEditMembers(row);
  else if (act === 'start') openWorkspaceProject(row.id, { background: true });
  else if (act === 'stop') {
    if (row.instances.some((i) => i.busy || i.needs_approval)) confirmStop(row);
    else stopProject(row);
  } else if (act === 'locate') openFolders({ workspace: true, locate: { id: row.id, name: row.name } });
  else if (act === 'terminal') openTerminalIn(row);
  else if (act === 'remove') confirmRemove(row);
}

async function stopProject(row) {
  const res = await wsPost('stop', { id: row.id });
  if (res.ok) showToast(`${row.name} stopped — its chats are in Recent sessions`);
}

// ─── The dialog shell ─────────────────────────────────────────────────────

let dlg = null;   // { onAct(act, el), onChange(e), onSubmit() } of the dialog on screen

function showDialog({ title, sub = '', ic = 'folder', body, foot, focus, onAct, onChange, onSubmit }) {
  $(`${DLG}-title`).textContent = title;
  $(`${DLG}-sub`).textContent = sub;
  $(`${DLG}-ic`).innerHTML = icon(ic);
  $(`${DLG}-body`).innerHTML = body;
  $(`${DLG}-foot`).innerHTML = foot;
  dlg = { onAct, onChange, onSubmit };
  closeDd({ focus: false });
  openModal(DLG, { focus, onClose: () => { closeDd({ focus: false }); dlg = null; } });
}

// ─── Dropdown (a button + one menu drawn inside the dialog, never a native <select>) ───
// The value lives in a hidden <input id=…> the caller writes next to ddHtml(); picking fires a bubbling `change` on it, so a
// dialog's onChange reads `e.target.id` / `e.target.value` like it would for a <select>.

const ddSpecs = {}; // id → [[value, label, hint], …]
let dd = null;      // { el, trigger, input, id, off }

function ddHtml(id, label, options, value) {
  ddSpecs[id] = options;
  const cur = options.find(([v]) => v === value) || options[0];
  return `<button type="button" class="ws-dd" id="${id}-btn" data-act="dd" data-dd="${id}" aria-haspopup="listbox" aria-expanded="false" aria-label="${escapeHtml(label)}: ${escapeHtml(cur[1])}">
      <span class="ws-dd-label">${escapeHtml(cur[1])}</span>${icon('chevron-down')}
    </button>`;
}

function closeDd({ focus = true } = {}) {
  if (!dd) return;
  const { el, trigger, off } = dd;
  dd = null;
  off();
  el.hidden = true;
  el.innerHTML = '';
  trigger.setAttribute('aria-expanded', 'false');
  if (focus && trigger.isConnected) trigger.focus({ preventScroll: true });
}

function pickDd(value) {
  if (!dd) return;
  const { input, trigger, id } = dd;
  const opt = ddSpecs[id]?.find(([v]) => v === value);
  closeDd();
  if (!opt || input.value === value) return;
  input.value = value;
  trigger.querySelector('.ws-dd-label').textContent = opt[1];
  trigger.setAttribute('aria-label', `${trigger.getAttribute('aria-label').split(':')[0]}: ${opt[1]}`);
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

function openDd(trigger) {
  if (dd?.trigger === trigger) {
    closeDd();
    return;
  }
  closeDd({ focus: false });
  const id = trigger.dataset.dd;
  const options = ddSpecs[id];
  const input = $(id);
  const card = $(`${DLG}-card`);
  if (!options || !input || !card) return;
  let el = $(`${DLG}-dd`);
  if (!el) {
    el = document.createElement('div');
    el.id = `${DLG}-dd`;
    el.className = 'ws-dd-menu';
    el.setAttribute('role', 'listbox');
    el.hidden = true;
    card.append(el);
  }
  el.setAttribute('aria-labelledby', `${id}-label`);
  el.innerHTML = options.map(([v, label, hint]) => `
    <button type="button" class="ws-dd-opt" role="option" data-val="${escapeHtml(v)}" aria-selected="${v === input.value}" data-esc-cancels>
      <span class="ws-dd-check">${v === input.value ? icon('check') : ''}</span>
      <span class="ws-dd-body"><strong>${escapeHtml(label)}</strong>${hint ? `<span>${escapeHtml(hint)}</span>` : ''}</span>
    </button>`).join('');
  el.querySelectorAll('.ws-dd-opt').forEach((b) => b.addEventListener('esc-cancel', () => closeDd()));
  const onDown = (e) => {
    if (!el.contains(e.target) && !trigger.contains(e.target)) closeDd({ focus: false });
  };
  const onScroll = () => closeDd({ focus: false });
  const body = $(`${DLG}-body`);
  document.addEventListener('pointerdown', onDown, true);
  body?.addEventListener('scroll', onScroll, { passive: true });
  dd = {
    el, trigger, input, id,
    off: () => {
      document.removeEventListener('pointerdown', onDown, true);
      body?.removeEventListener('scroll', onScroll);
    },
  };
  trigger.setAttribute('aria-expanded', 'true');

  // Under the button, or over it when the viewport has no room below (the menu floats over the
  // dialog's footer like a native select popover, so the card never clips it).
  el.hidden = false;
  const tr = trigger.getBoundingClientRect();
  const vh = window.innerHeight;
  const vw = window.innerWidth;
  const below = vh - tr.bottom - 12;
  const above = tr.top - 12;
  const want = Math.min(el.scrollHeight || 240, 320);
  const up = below < want && above > below;
  el.style.maxHeight = `${Math.max(140, Math.min(320, (up ? above : below) - 4))}px`;
  el.style.width = `${tr.width}px`;
  el.style.left = `${Math.max(8, Math.min(tr.left, vw - tr.width - 8))}px`;
  el.style.top = up ? '' : `${tr.bottom + 6}px`;
  el.style.bottom = up ? `${vh - tr.top + 6}px` : '';
  el.classList.toggle('is-up', up);
  el.classList.remove('is-in');
  void el.offsetWidth; // restart the entrance
  el.classList.add('is-in');
  (el.querySelector('[aria-selected="true"]') || el.querySelector('.ws-dd-opt'))?.focus({ preventScroll: true });
}

function onDdKey(e) {
  if (!dd) {
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && e.target.matches?.('[data-dd]')) {
      e.preventDefault();
      openDd(e.target);
    }
    return;
  }
  const opts = [...dd.el.querySelectorAll('.ws-dd-opt')];
  const i = opts.indexOf(document.activeElement);
  let next = -1;
  if (e.key === 'ArrowDown') next = Math.min(opts.length - 1, i + 1);
  else if (e.key === 'ArrowUp') next = Math.max(0, i - 1);
  else if (e.key === 'Home') next = 0;
  else if (e.key === 'End') next = opts.length - 1;
  else if (e.key === 'Tab') {
    e.preventDefault();
    closeDd();
    return;
  }
  if (next < 0) return;
  e.preventDefault();
  opts[next].focus();
  opts[next].scrollIntoView({ block: 'nearest' });
}

const CANCEL = `<button type="button" class="btn btn-quiet" data-close="${DLG}">Cancel</button>`;

function onDialogClick(e) {
  const opt = e.target.closest('.ws-dd-opt');
  if (opt) {
    pickDd(opt.dataset.val);
    return;
  }
  const btn = e.target.closest('[data-act]');
  if (btn?.dataset.act === 'dd') {
    openDd(btn);
    return;
  }
  if (!btn || btn.disabled) return;
  dlg?.onAct?.(btn.dataset.act, btn);
}

export function initWsMenu() {
  const card = $(`${DLG}-card`);
  card?.addEventListener('click', onDialogClick);
  card?.addEventListener('change', (e) => dlg?.onChange?.(e));
  card?.addEventListener('keydown', (e) => {
    onDdKey(e);
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.target.matches?.('input[type="text"], input[type="number"]')) {
      e.preventDefault();
      dlg?.onSubmit?.();
    }
  });
}

// ─── Rename · remove · stop ───────────────────────────────────────────────

function openRename(row) {
  const save = async () => {
    const name = $('ws-name')?.value.trim();
    if (!name) {
      showToast('A project needs a name', true);
      return;
    }
    if (name === row.name) {
      closeModal(DLG);
      return;
    }
    const res = await wsPost('update', { id: row.id, name });
    if (res.ok) closeModal(DLG);
  };
  showDialog({
    title: isGroupRow(row) ? 'Rename group' : 'Rename project', sub: row.display, ic: 'pencil',
    body: `<label class="ws-field"><span>Name in the sidebar</span>
      <input id="ws-name" type="text" maxlength="60" value="${escapeHtml(row.name)}" autocomplete="off" autocapitalize="off" spellcheck="false" data-1p-ignore></label>
      <p class="ws-note">${isGroupRow(row) ? 'Only the label changes — the master chat and members stay.' : 'Only the label changes — the folder keeps its name.'}</p>`,
    foot: `<span class="dlg-spacer"></span>${CANCEL}<button type="button" class="btn btn-primary" data-act="save">Save</button>`,
    focus: 'ws-name',
    onAct: (act) => { if (act === 'save') save(); },
    onSubmit: save,
  });
  $('ws-name')?.select();
}

export function confirmRemove(row) {
  if (isGroupRow(row)) {
    confirmRemoveGroup(row);
    return;
  }
  showDialog({
    title: `Remove ${row.name}?`, sub: 'From your workspace', ic: 'trash-2',
    body: `<p class="ws-note">${escapeHtml(row.name)} leaves the sidebar and other projects’ links to it are dropped.
      Its chats stay in Recent sessions, a running Jarvis keeps running, and the folder is not touched.</p>`,
    foot: `<span class="dlg-spacer"></span><button type="button" class="btn btn-quiet" id="ws-cancel" data-close="${DLG}">Cancel</button>
      <button type="button" class="btn btn-danger" data-act="remove">${icon('trash-2')}<span>Remove</span></button>`,
    focus: 'ws-cancel',
    onAct: async (act, btn) => {
      if (act !== 'remove') return;
      btn.disabled = true;
      const res = await wsPost('remove', { id: row.id });
      if (res.ok) {
        closeModal(DLG);
        showToast(`${row.name} removed from your workspace`);
      } else btn.disabled = false;
    },
  });
}

function confirmRemoveGroup(row) {
  showDialog({
    title: `Remove group ${row.name}?`, sub: 'Master group', ic: 'trash-2',
    body: `<p class="ws-note">Removes the group only — your projects and chats stay.
      The master chat is kept in Recent sessions and a running Jarvis keeps running.</p>`,
    foot: `<span class="dlg-spacer"></span><button type="button" class="btn btn-quiet" id="ws-cancel" data-close="${DLG}">Cancel</button>
      <button type="button" class="btn btn-danger" data-act="remove">${icon('trash-2')}<span>Remove group</span></button>`,
    focus: 'ws-cancel',
    onAct: async (act, btn) => {
      if (act !== 'remove') return;
      btn.disabled = true;
      const res = await wsPost('remove', { id: row.id });
      if (res.ok) {
        closeModal(DLG);
        showToast(`Group ${row.name} removed — your projects and chats stay`);
      } else btn.disabled = false;
    },
  });
}

function confirmStop(row) {
  showDialog({
    title: `Stop ${row.name}?`, sub: 'It is still working', ic: 'power',
    body: `<p class="ws-note">${escapeHtml(row.name)} is working or waiting for you. Stopping ends the reply in progress — the chat itself stays saved.</p>`,
    foot: `<span class="dlg-spacer"></span><button type="button" class="btn btn-quiet" id="ws-cancel" data-close="${DLG}">Keep running</button>
      <button type="button" class="btn btn-danger" data-act="stop">${icon('power')}<span>Stop</span></button>`,
    focus: 'ws-cancel',
    onAct: async (act, btn) => {
      if (act !== 'stop') return;
      btn.disabled = true;
      closeModal(DLG);
      await stopProject(row);
    },
  });
}

// ─── Links… ───────────────────────────────────────────────────────────────

const WRITE_MODES = [
  ['ask', 'Ask', 'You approve each edit in a linked project'],
  ['allow', 'Allow', 'The agent may edit linked projects on its own'],
  ['deny', 'Deny', 'Linked projects are read-only'],
];
const STARTUP_MODES = [
  ['lazy', 'Lazy', 'Starts when you open it; stopped after it has been idle a while'],
  ['warm', 'Warm', 'Started in the background when Jarvis starts'],
  ['pinned', 'Pinned', 'Always kept running'],
];

function segHtml(key, modes, value) {
  return `<div class="ws-seg" role="radiogroup" data-seg-group="${key}">${modes.map(([v, label]) =>
    `<button type="button" class="ws-seg-btn${v === value ? ' is-on' : ''}" role="radio" aria-checked="${v === value}" data-act="seg" data-seg="${key}" data-val="${v}">${label}</button>`).join('')}</div>
    <p class="ws-seg-note" id="ws-note-${key}">${escapeHtml(modes.find(([v]) => v === value)?.[2] || '')}</p>`;
}

// Master groups: who edits members, and the dialogs that make / change a group.
const POLICIES = [
  ['ask', 'Ask before editing members', 'You approve each edit the master chat makes in a member project', 'Ask'],
  ['allow', 'Allow editing members', 'The master chat may edit member projects on its own', 'Allow'],
  ['deny', 'Read-only', 'The master chat can read member projects but not change them', 'Read-only'],
];

const memberRowsHtml = (plain, picked) => plain.map((o) => `<label class="ws-link-row">
    <input type="checkbox" data-member="${escapeHtml(o.id)}" ${picked.has(o.id) ? 'checked' : ''}>
    <span class="ws-dot" style="--c:${escapeHtml(o.color)}" aria-hidden="true"></span>
    <span class="ws-link-name"><strong>${escapeHtml(o.name)}</strong><span>${escapeHtml(pathTail(o.display || o.path))}</span></span>
  </label>`).join('');

/** "…/Desktop/app" — the end of a long folder path, which is the part that identifies it. */
function pathTail(path, keep = 2) {
  const parts = String(path || '').split('/').filter(Boolean);
  return parts.length <= keep + 1 ? String(path || '') : `…/${parts.slice(-keep).join('/')}`;
}

const ERR = '<p class="ws-error" id="wsg-err" role="alert" hidden></p>';
function showErr(text) {
  const el = $('wsg-err');
  if (!el) return;
  el.textContent = text || '';
  el.hidden = !text;
}

/** A sensible group name: the folder the member projects share ("Habuild"), else "Master". */
function suggestName(rows) {
  const dirs = rows.map((r) => String(r.path || '').split('/').filter(Boolean).slice(0, -1));
  if (!dirs.length) return 'Master';
  let common = dirs[0];
  for (const d of dirs) {
    let i = 0;
    while (i < common.length && i < d.length && common[i] === d[i]) i += 1;
    common = common.slice(0, i);
  }
  const last = common.length >= 3 ? common[common.length - 1] : '';
  const taken = new Set(workspaceRows().map((r) => r.name.toLowerCase()));
  const base = last ? last.replace(/[-_]+/g, ' ').replace(/^./, (c) => c.toUpperCase()) : 'Master';
  let name = base;
  for (let n = 2; taken.has(name.toLowerCase()); n += 1) name = `${base} ${n}`;
  return name;
}

const policySelect = (value) => `<div class="ws-field"><span id="wsg-policy-label">When the master chat edits member projects</span>
  <input type="hidden" id="wsg-policy" value="${escapeHtml(value)}">
  ${ddHtml('wsg-policy', 'When the master chat edits member projects', POLICIES, value)}</div>
  <p class="ws-seg-note" id="wsg-policy-note">${escapeHtml(POLICIES.find(([v]) => v === value)?.[2] || '')}</p>`;

/** 🌐 New master group… — name, members (≥ 1 plain project), write policy. Lands in the master chat. */
export function openCreateGroup() {
  const plain = workspacePlainRows();
  if (plain.length < 2) {
    showToast('Add at least two projects to the workspace before making a group', true);
    return;
  }
  const picked = new Set(plain.map((r) => r.id));
  const sync = () => {
    const btn = document.querySelector('#wsdlg-foot [data-act="create"]');
    if (btn) btn.disabled = picked.size < 1;
    const n = $('wsg-count');
    if (n) n.textContent = `${picked.size} selected`;
  };
  const create = async () => {
    const name = $('wsg-name')?.value.trim();
    if (!name) return showErr('Give the group a name.');
    if (picked.size < 1) return showErr('Pick at least one project.');
    showErr('');
    const btn = document.querySelector('#wsdlg-foot [data-act="create"]');
    if (btn) btn.disabled = true;
    let res;
    try {
      res = await createWorkspaceGroup({ name, members: [...picked] });
    } catch (err) {
      res = { ok: false, error: err?.status === 401 ? 'This link has expired.' : 'Jarvis is not reachable right now.' };
    }
    if (!res?.ok || !res.project) {
      const msg = res?.error || 'Could not create the group.';
      showErr(msg);
      if (res?.code === 'remote') showToast(msg, true);
      sync();
      return;
    }
    const policy = $('wsg-policy')?.value || 'ask';
    if (policy !== 'ask') await wsPost('update', { id: res.project.id, link_write: policy }, { quiet: true });
    closeModal(DLG);
    showToast(`Group ${res.project.name} created`);
    await loadWorkspace();
    refreshWorkspace({ now: true });
    openWorkspaceProject(res.project.id);
  };
  showDialog({
    title: 'New master group', sub: 'One shared chat over several projects', ic: 'globe',
    body: `
      <label class="ws-field"><span>Group name</span>
        <input id="wsg-name" type="text" maxlength="60" value="${escapeHtml(suggestName(plain))}" autocomplete="off" autocapitalize="off" spellcheck="false" data-1p-ignore></label>
      ${section('Projects in the group', { count: plain.length, right: '<span id="wsg-count"></span>' })}
      <div class="ws-links" role="group" aria-label="Projects in the group">${memberRowsHtml(plain, picked)}</div>
      ${policySelect('ask')}
      <p class="ws-note">${icon('info')}<span>The master chat is pinned as the first tab here and as a 🌐 tab in every member project.</span></p>
      ${ERR}`,
    foot: `<span class="dlg-spacer"></span>${CANCEL}<button type="button" class="btn btn-primary" data-act="create">${icon('globe')}<span>Create group</span></button>`,
    focus: 'wsg-name',
    onChange: (e) => {
      const id = e.target.dataset?.member;
      if (id) {
        if (e.target.checked) picked.add(id);
        else picked.delete(id);
        sync();
      } else if (e.target.id === 'wsg-policy') {
        const note = $('wsg-policy-note');
        if (note) note.textContent = POLICIES.find(([v]) => v === e.target.value)?.[2] || '';
      }
    },
    onAct: (act) => { if (act === 'create') create(); },
    onSubmit: create,
  });
  sync();
  $('wsg-name')?.select();
}

/** A group's ⋯ → Edit members… */
function openEditMembers(row) {
  const plain = workspacePlainRows();
  const picked = new Set((row.members || row.links || []).filter((id) => plain.some((p) => p.id === id)));
  const sync = () => {
    const btn = document.querySelector('#wsdlg-foot [data-act="save"]');
    if (btn) btn.disabled = picked.size < 1;
    const n = $('wsg-count');
    if (n) n.textContent = `${picked.size} selected`;
  };
  showDialog({
    title: `Members — ${row.name}`, sub: 'Projects the master chat can reach', ic: 'users',
    body: `${section('Projects in the group', { count: plain.length, right: '<span id="wsg-count"></span>' })}
      <div class="ws-links" role="group" aria-label="Projects in the group">${memberRowsHtml(plain, picked)}</div>
      ${ERR}`,
    foot: `<span class="dlg-spacer"></span>${CANCEL}<button type="button" class="btn btn-primary" data-act="save">Save</button>`,
    onChange: (e) => {
      const id = e.target.dataset?.member;
      if (!id) return;
      if (e.target.checked) picked.add(id);
      else picked.delete(id);
      sync();
    },
    onAct: async (act, btn) => {
      if (act !== 'save') return;
      if (picked.size < 1) return showErr('Pick at least one project.');
      btn.disabled = true;
      const res = await wsPost('update', { id: row.id, links: [...picked] }, { quiet: true });
      if (res.ok) {
        closeModal(DLG);
        showToast('Members saved');
      } else {
        showErr(res.error || 'Could not save the members.');
        sync();
      }
    },
  });
  sync();
}

function openLinks(row) {
  const others = workspaceRows().filter((r) => r.id !== row.id && !isGroupRow(r));
  const masters = row.masters || [];
  const partOf = masters.length
    ? `<p class="ws-note ws-partof">${icon('globe')}<span>Part of: ${masters.map((m) => `<strong>🌐 ${escapeHtml(m.name)}</strong>`).join(', ')}</span></p>`
    : '';
  const linkAll = !!workspaceSettings().link_all;
  const state = { links: new Set(row.links), link_write: row.link_write, startup: row.startup };
  const list = others.length
    ? others.map((o) => `<label class="ws-link-row${linkAll ? ' is-locked' : ''}">
        <input type="checkbox" data-link="${escapeHtml(o.id)}" ${linkAll || state.links.has(o.id) ? 'checked' : ''} ${linkAll ? 'disabled' : ''}>
        <span class="ws-dot" style="--c:${escapeHtml(o.color)}" aria-hidden="true"></span>
        <span class="ws-link-name"><strong>${escapeHtml(o.name)}</strong><span>${escapeHtml(o.display)}</span></span>
      </label>`).join('')
    : '<p class="ws-note">Add another project to the workspace first — then you can let this one read it.</p>';
  showDialog({
    title: `Links — ${row.name}`, sub: 'What this project’s agent can reach', ic: 'link',
    body: `
      ${section('This project’s agent can read', { count: others.length })}
      ${linkAll ? `<p class="ws-note">${icon('info')}<span>“Every project can read every other” is on (Workspace settings).</span></p>` : ''}
      ${partOf}
      <div class="ws-links">${list}</div>
      ${section('Write into linked projects')}
      ${segHtml('link_write', WRITE_MODES, state.link_write)}
      ${section('Start-up')}
      ${segHtml('startup', STARTUP_MODES, state.startup)}`,
    foot: `<span class="dlg-spacer"></span>${CANCEL}<button type="button" class="btn btn-primary" data-act="save">Save</button>`,
    onChange: (e) => {
      const id = e.target.dataset?.link;
      if (!id) return;
      if (e.target.checked) state.links.add(id);
      else state.links.delete(id);
    },
    onAct: async (act, btn) => {
      if (act === 'seg') {
        const { seg, val } = btn.dataset;
        state[seg] = val;
        $(`${DLG}-body`).querySelectorAll(`[data-seg="${seg}"]`).forEach((b) => {
          const on = b.dataset.val === val;
          b.classList.toggle('is-on', on);
          b.setAttribute('aria-checked', String(on));
        });
        const modes = seg === 'startup' ? STARTUP_MODES : WRITE_MODES;
        const note = $(`ws-note-${seg}`);
        if (note) note.textContent = modes.find(([v]) => v === val)?.[2] || '';
      } else if (act === 'save') {
        btn.disabled = true;
        const res = await wsPost('update', {
          id: row.id, links: [...state.links], link_write: state.link_write, startup: state.startup,
        });
        btn.disabled = false;
        if (res.ok) {
          closeModal(DLG);
          showToast('Links saved');
        }
      }
    },
  });
}

// ─── Workspace settings ───────────────────────────────────────────────────

function openWorkspaceSettingsDialog() {
  const s = workspaceSettings();
  const num = (key, label, hint, max) => `<label class="ws-setting">
      <span class="ws-set-text"><strong>${label}</strong><span>${hint}</span></span>
      <input type="number" class="ws-num" data-key="${key}" data-prev="${s[key] ?? 0}" value="${s[key] ?? 0}" min="0" max="${max}" step="1" inputmode="numeric">
    </label>`;
  const sw = (key, label, hint) => `<label class="ws-setting">
      <span class="ws-set-text"><strong>${label}</strong><span>${hint}</span></span>
      <span class="ws-switch"><input type="checkbox" data-key="${key}" ${s[key] ? 'checked' : ''}><span class="ws-switch-ui" aria-hidden="true"></span></span>
    </label>`;
  showDialog({
    title: 'Workspace settings', sub: 'How your projects start, stop and reach each other', ic: 'settings',
    body: `
      ${section('Running')}
      ${num('warm_limit', 'Warm projects', 'Projects kept running in the background, ready to switch to', 16)}
      ${num('idle_stop_minutes', 'Stop idle projects after (minutes)', 'Only ones Jarvis started for you · 0 = never', 1440)}
      ${section('Across projects')}
      ${sw('link_all', 'Every project can read every other', 'Skips the per-project Links — edits still follow each project’s write policy')}
      ${sw('delegation', 'Let the agent hand work to another project', 'It can ask that project’s own Jarvis to do a task and wait for the result')}`,
    foot: `<span class="dlg-note">${icon('info')}<span>Changes apply as you make them.</span></span><span class="dlg-spacer"></span>
      <button type="button" class="btn btn-primary" data-close="${DLG}">Done</button>`,
    onChange: async (e) => {
      const el = e.target;
      const key = el.dataset?.key;
      if (!key) return;
      const isNum = el.type === 'number';
      const val = isNum ? Math.max(0, Math.min(Number(el.max) || 10000, Math.floor(Number(el.value) || 0))) : el.checked;
      if (isNum) el.value = String(val);
      el.disabled = true;
      const res = await wsPost('settings', { [key]: val });
      el.disabled = false;
      if (res.ok) {
        if (isNum) el.dataset.prev = String(val);
      } else if (isNum) el.value = el.dataset.prev ?? '0';
      else el.checked = !val;
    },
  });
}

/** The ⚙ in the Projects header and ⌘K → Workspace settings. */
export function openWorkspaceSettings() {
  openWorkspaceSettingsDialog();
}
