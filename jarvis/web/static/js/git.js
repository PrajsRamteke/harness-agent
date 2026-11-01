/** Git card at the top of the inspector: Open in (Cursor, Finder…), switch /
 * create a branch, Commit & push (with Commit · Push · Create PR · Stash in its
 * menu), a model-written message, and the +/− of the working tree.
 * Server side: jarvis/web/git_api.py via GET/POST /api/git (the project shown). */
import { $, escapeHtml, showToast, storageGet, storageSet } from './utils.js';
import { icon } from './icons.js';
import { api } from './api.js';
import { store, subscribe } from './store.js';
import { isInspectorOpen, onInspectorChange, selectTab } from './inspector.js';

const EDITOR_KEY = 'jarvis-git-editor';
const EDITOR_ICON = { finder: 'folder', terminal: 'terminal' };
const POLL_MS = 15000;

let st = null;            // last /api/git answer
let form = null;          // null | 'commit' | 'commit-push' | 'pr' | 'stash'
let working = '';         // the op running right now ('' = idle)
let menu = null;          // { kind, anchor }
let branchList = null;    // { current, local, remote }
let lastPr = '';          // URL of the PR just created
let pollTimer = null;
let wasBusy = false;

const card = () => $('git-card');
const num = (n) => Number(n) || 0;
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// ─── Data ─────────────────────────────────────────────────────────────────

export async function refreshGit() {
  try {
    st = await api('/api/git');
  } catch {
    return; // keep what we had (a reconnect fixes it)
  }
  render();
}

async function post(op, data = {}) {
  working = op;
  render();
  try {
    return await api('/api/git', 'POST', { op, ...data });
  } catch (err) {
    return { ok: false, error: err?.message || 'That did not work' };
  } finally {
    working = '';
  }
}

/** `git` event: a commit / push / switch happened (this page, another one). */
export function handleGitEvent(data) {
  if (data && typeof data === 'object' && 'repo' in data) {
    st = data;
    render();
  }
}

// ─── Labels ───────────────────────────────────────────────────────────────

function editorChoice() {
  const list = st?.editors || [];
  const saved = storageGet(EDITOR_KEY, '');
  return list.find((e) => e.id === saved) || list.find((e) => e.id !== 'finder' && e.id !== 'terminal') || list[0] || null;
}

/** What the big button does now: commit something, push commits, or nothing. */
function primaryAction() {
  if (!st?.repo) return null;
  const remote = (st.remotes || []).length > 0;
  if (num(st.files) > 0) {
    return remote
      ? { op: 'commit-push', label: 'Commit & push', icon: 'cloud-upload' }
      : { op: 'commit', label: 'Commit', icon: 'git-commit-horizontal' };
  }
  if (remote && !st.detached && (num(st.ahead) > 0 || !st.upstream || st.upstream_gone)) {
    return {
      op: 'push',
      label: st.upstream && !st.upstream_gone ? `Push ${plural(num(st.ahead), 'commit')}` : 'Publish branch',
      icon: 'cloud-upload',
    };
  }
  return { op: '', label: 'Up to date', icon: 'check', disabled: true, why: 'No changes to commit and nothing to push' };
}

// ─── Render ───────────────────────────────────────────────────────────────

function render() {
  const el = card();
  if (!el) return;
  if (!st) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.classList.toggle('is-working', !!working);
  const rows = el.querySelector('.gc-rows') || el.appendChild(Object.assign(document.createElement('div'), { className: 'gc-rows' }));
  const ed = editorChoice();
  const parts = [];
  if (ed) {
    parts.push(`
      <div class="gc-split">
        <button type="button" class="gc-main" data-act="open" title="Open this folder in ${escapeHtml(ed.label)}">
          ${icon(EDITOR_ICON[ed.id] || 'code')}<span>Open in ${escapeHtml(ed.label)}</span>
        </button>
        <button type="button" class="gc-more" data-menu="open" aria-haspopup="menu" aria-label="Open in another app">${icon('chevron-down')}</button>
      </div>`);
  }
  if (!st.repo) {
    parts.push(`<p class="gc-note">${icon('git-branch')}<span>Not a git repository</span></p>`);
    rows.innerHTML = parts.join('');
    paintForm();
    return;
  }
  const busy = store.busy;
  const sync = [
    num(st.ahead) ? `<span title="${plural(num(st.ahead), 'commit')} to push">↑${num(st.ahead)}</span>` : '',
    num(st.behind) ? `<span title="${plural(num(st.behind), 'commit')} to pull">↓${num(st.behind)}</span>` : '',
  ].join('');
  parts.push(`
    <button type="button" class="gc-row" data-menu="branch" aria-haspopup="menu" ${busy ? 'disabled title="Jarvis is working — switch branches when it finishes"' : `title="Switch or create a branch"`}>
      ${icon('git-branch')}<span class="gc-branch">${escapeHtml(st.branch || 'no branch')}${st.detached ? ' <em>detached</em>' : ''}</span>
      ${sync ? `<span class="gc-sync">${sync}</span>` : ''}
      <span class="gc-chev">${icon('chevron-down')}</span>
    </button>`);
  const main = primaryAction();
  const mainBusy = working && ['commit', 'push', 'pr'].includes(working);
  parts.push(`
    <div class="gc-split">
      <button type="button" class="gc-main" data-act="primary" ${main.disabled || mainBusy ? 'disabled' : ''} title="${escapeHtml(main.why || main.label)}">
        ${mainBusy ? '<span class="spinner"></span>' : icon(main.icon)}<span>${escapeHtml(main.label)}</span>
      </button>
      <button type="button" class="gc-more" data-menu="commit" aria-haspopup="menu" aria-label="More git actions">${icon('chevron-down')}</button>
    </div>`);
  parts.push(`
    <button type="button" class="gc-row gc-changes" data-act="changes" title="Show the changes">
      ${icon('file-diff')}<span>Changes</span>
      <span class="gc-nums">${num(st.files) ? `<b class="num-add">+${num(st.added)}</b><b class="num-del">−${num(st.removed)}</b>` : '<em>clean</em>'}</span>
    </button>`);
  rows.innerHTML = parts.join('');
  paintForm();
}

/** The inline form under the rows; rebuilt only when it changes (typing survives). */
function paintForm() {
  const el = card();
  let box = el.querySelector('.gc-form');
  const want = form && st?.repo ? form : null;
  if (!want) {
    box?.remove();
    paintResult();
    return;
  }
  if (box?.dataset.form === want) {
    syncFormButtons(box);
    return;
  }
  box?.remove();
  box = document.createElement('div');
  box.className = 'gc-form';
  box.dataset.form = want;
  const files = num(st.files);
  if (want === 'stash') {
    box.innerHTML = `
      <label class="gc-label" for="gc-stash-msg">Stash ${plural(files, 'changed file')} (new files too)</label>
      <input id="gc-stash-msg" class="gc-input" type="text" placeholder="Name (optional)" autocomplete="off" spellcheck="false">
      <div class="gc-actions"><span class="gc-spacer"></span>
        <button type="button" class="btn btn-quiet" data-act="cancel">Cancel</button>
        <button type="button" class="btn btn-primary" data-act="do-stash">${icon('archive')}<span>Stash</span></button>
      </div>`;
  } else if (want === 'pr') {
    box.innerHTML = `
      <label class="gc-label" for="gc-pr-title">Pull request</label>
      <input id="gc-pr-title" class="gc-input" type="text" placeholder="Title (empty = from the commits)" autocomplete="off">
      <textarea id="gc-pr-body" class="gc-input gc-textarea" rows="5" placeholder="Description (Markdown)"></textarea>
      <div class="gc-actions">
        <button type="button" class="btn btn-quiet gc-gen" data-act="generate-pr" title="Write the title and description with the current model">${icon('wand-sparkles')}<span>Generate</span></button>
        <span class="gc-spacer"></span>
        <button type="button" class="btn btn-quiet" data-act="cancel">Cancel</button>
        <button type="button" class="btn btn-primary" data-act="do-pr">${icon('git-pull-request')}<span>Create PR</span></button>
      </div>`;
  } else {
    const push = want === 'commit-push';
    box.innerHTML = `
      <label class="gc-label" for="gc-msg">Commit ${plural(files, 'changed file')}${push ? ', then push' : ''}</label>
      <textarea id="gc-msg" class="gc-input gc-textarea" rows="3" placeholder="Commit message"></textarea>
      <div class="gc-actions">
        <button type="button" class="btn btn-quiet gc-gen" data-act="generate" title="Write the message with the current model">${icon('wand-sparkles')}<span>Generate</span></button>
        <span class="gc-spacer"></span>
        <button type="button" class="btn btn-quiet" data-act="cancel">Cancel</button>
        <button type="button" class="btn btn-primary" data-act="do-commit">${icon(push ? 'cloud-upload' : 'git-commit-horizontal')}<span>${push ? 'Commit & push' : 'Commit'}</span></button>
      </div>`;
  }
  el.appendChild(box);
  box.querySelector('.gc-input')?.focus({ preventScroll: true });
  syncFormButtons(box);
  paintResult();
}

function syncFormButtons(box) {
  box.querySelectorAll('button').forEach((b) => { b.disabled = !!working; });
  const gen = box.querySelector('.gc-gen');
  if (gen) gen.innerHTML = working === 'generate'
    ? '<span class="spinner"></span><span>Writing…</span>'
    : `${icon('wand-sparkles')}<span>Generate</span>`;
}

function paintResult() {
  const el = card();
  let res = el.querySelector('.gc-result');
  if (!lastPr) {
    res?.remove();
    return;
  }
  if (!res) {
    res = document.createElement('div');
    res.className = 'gc-result';
    el.appendChild(res);
  }
  res.innerHTML = `${icon('git-pull-request')}<a href="${escapeHtml(lastPr)}" target="_blank" rel="noopener">${escapeHtml(lastPr.replace(/^https?:\/\//, ''))}</a><button type="button" class="gc-x" data-act="dismiss-pr" aria-label="Dismiss">${icon('x')}</button>`;
}

// ─── Menus ────────────────────────────────────────────────────────────────

function closeMenu() {
  menu = null;
  card()?.querySelector('.gc-menu')?.remove();
  card()?.querySelectorAll('[aria-expanded="true"]').forEach((b) => b.setAttribute('aria-expanded', 'false'));
}

function menuItem(act, iconName, label, { disabled = false, why = '', hint = '', checked = false, data = '' } = {}) {
  return `<button type="button" class="gc-item${checked ? ' is-current' : ''}" role="menuitem" data-act="${act}" ${data} ${disabled ? 'disabled' : ''} title="${escapeHtml(why || label)}">
    ${icon(iconName)}<span>${escapeHtml(label)}</span>${hint ? `<em>${escapeHtml(hint)}</em>` : ''}${checked ? icon('check', 'gc-check') : ''}
  </button>`;
}

function openMenu(kind, anchor) {
  if (menu?.kind === kind) {
    closeMenu();
    return;
  }
  closeMenu();
  menu = { kind, anchor };
  anchor.setAttribute('aria-expanded', 'true');
  const el = document.createElement('div');
  el.className = 'gc-menu';
  el.setAttribute('role', 'menu');
  if (kind === 'open') {
    const cur = editorChoice();
    el.innerHTML = (st.editors || []).map((e) => menuItem('open-with', EDITOR_ICON[e.id] || 'code', e.label, {
      checked: cur?.id === e.id, data: `data-editor="${escapeHtml(e.id)}"`,
    })).join('');
  } else if (kind === 'commit') {
    const remote = (st.remotes || []).length > 0;
    const files = num(st.files);
    const pushable = remote && !st.detached && (num(st.ahead) > 0 || !st.upstream || st.upstream_gone);
    el.innerHTML = [
      menuItem('commit', 'git-commit-horizontal', 'Commit', { disabled: !files, why: files ? 'Commit without pushing' : 'Nothing to commit' }),
      menuItem('push', 'cloud-upload', st.upstream && !st.upstream_gone ? 'Push' : 'Publish branch', {
        disabled: !pushable, why: !remote ? 'This repository has no remote' : pushable ? '' : 'Nothing to push',
        hint: num(st.ahead) ? `↑${num(st.ahead)}` : '',
      }),
      menuItem('pr', 'git-pull-request', 'Create PR', {
        disabled: !remote || !st.gh || st.detached,
        why: !st.gh ? 'Needs the GitHub CLI: brew install gh, then gh auth login' : !remote ? 'This repository has no remote' : 'Open a pull request for this branch',
      }),
      '<div class="gc-sep" role="separator"></div>',
      menuItem('stash', 'archive', 'Stash changes', {
        disabled: !files || store.busy, why: store.busy ? 'Jarvis is working in these files' : files ? 'Put the changes aside (git stash)' : 'Nothing to stash',
      }),
    ].join('');
  } else if (kind === 'branch') {
    el.classList.add('is-branches');
    el.innerHTML = `
      <label class="gc-search">${icon('search')}<input type="text" placeholder="Switch to or create a branch" autocomplete="off" spellcheck="false" aria-label="Branch"></label>
      <div class="gc-branches"><div class="gc-loading"><span class="spinner"></span><span>Loading branches…</span></div></div>`;
    const input = el.querySelector('input');
    input.addEventListener('input', () => paintBranches(el, input.value));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        el.querySelector('.gc-branches .gc-item:not([disabled])')?.click();
      }
    });
    setTimeout(() => input.focus({ preventScroll: true }), 0);
    post('branches').then((res) => {
      render();
      branchList = res.ok ? res : { current: st.branch, local: [], remote: [], error: res.error };
      if (menu?.kind === 'branch') paintBranches(el, input.value);
    });
  }
  // Under the row that opened it, inside the card.
  const top = anchor.closest('.gc-split, .gc-row') || anchor;
  el.style.top = `${top.offsetTop + top.offsetHeight + 4}px`;
  card().appendChild(el);
}

function paintBranches(el, query) {
  const box = el.querySelector('.gc-branches');
  if (!box || !branchList) return;
  const q = query.trim();
  const ql = q.toLowerCase();
  const match = (n) => !ql || n.toLowerCase().includes(ql);
  const local = branchList.local.filter(match);
  const remote = branchList.remote.filter(match);
  const exact = [...branchList.local, ...branchList.remote].some((n) => n === q);
  const html = [];
  if (q && !exact) html.push(menuItem('branch-new', 'plus', `Create branch “${q}”`, { data: `data-branch="${escapeHtml(q)}"` }));
  local.slice(0, 60).forEach((n) => html.push(menuItem('branch-go', 'git-branch', n, {
    checked: n === branchList.current, data: `data-branch="${escapeHtml(n)}"`,
  })));
  if (remote.length) {
    html.push('<div class="gc-sec">Remote</div>');
    remote.slice(0, 40).forEach((n) => html.push(menuItem('branch-go', 'cloud-upload', n, { data: `data-branch="${escapeHtml(n)}"`, hint: 'track' })));
  }
  if (!html.length) html.push(`<p class="gc-empty">${escapeHtml(branchList.error || 'No branches match')}</p>`);
  box.innerHTML = html.join('');
}

// ─── Actions ──────────────────────────────────────────────────────────────

async function after(res, okText) {
  if (res.ok) {
    if (okText) showToast(okText);
  } else {
    showToast(res.error || 'That did not work', true);
  }
  await refreshGit();
  return res.ok;
}

async function act(name, btn) {
  switch (name) {
    case 'open': {
      const ed = editorChoice();
      if (ed) await after(await post('open', { editor: ed.id }), `Opened in ${ed.label}`);
      break;
    }
    case 'open-with': {
      const id = btn.dataset.editor;
      storageSet(EDITOR_KEY, id);
      closeMenu();
      const ed = (st.editors || []).find((e) => e.id === id);
      await after(await post('open', { editor: id }), ed ? `Opened in ${ed.label}` : '');
      break;
    }
    case 'primary': {
      const main = primaryAction();
      if (main?.op === 'push') await act('push');
      else if (main?.op) setForm(main.op);
      break;
    }
    case 'changes':
      selectTab('changes');
      break;
    case 'commit':
    case 'pr':
    case 'stash':
      closeMenu();
      setForm(name);
      break;
    case 'push': {
      closeMenu();
      const res = await post('push');
      await after(res, res.published ? `Published to ${res.remote || 'the remote'}` : 'Pushed');
      break;
    }
    case 'branch-go':
    case 'branch-new': {
      const branch = btn.dataset.branch;
      const isRemote = !!branchList?.remote?.includes(branch);
      const local = isRemote ? branch.split('/').slice(1).join('/') : branch; // origin/x → x
      closeMenu();
      await after(await post('checkout', { branch, create: name === 'branch-new' }),
        name === 'branch-new' ? `Created and switched to ${branch}` : `Switched to ${local}`);
      break;
    }
    case 'cancel':
      setForm(null);
      break;
    case 'generate': {
      const res = await post('generate', { kind: 'commit' });
      render();
      if (res.ok) {
        const ta = $('gc-msg');
        if (ta) {
          ta.value = res.message || '';
          ta.focus();
        }
      } else showToast(res.error || 'Could not write a message', true);
      break;
    }
    case 'generate-pr': {
      const res = await post('generate', { kind: 'pr' });
      render();
      if (res.ok) {
        if ($('gc-pr-title')) $('gc-pr-title').value = res.title || '';
        if ($('gc-pr-body')) $('gc-pr-body').value = res.body || '';
      } else showToast(res.error || 'Could not write a description', true);
      break;
    }
    case 'do-commit': {
      const message = $('gc-msg')?.value || '';
      const push = form === 'commit-push';
      const res = await post('commit', { message, push });
      if (res.ok) setForm(null);
      const text = !res.ok ? '' : push
        ? (res.pushed ? `Committed ${res.sha} and pushed` : `Committed ${res.sha} — push failed: ${res.push_error}`)
        : `Committed ${res.sha}`;
      if (res.ok && push && !res.pushed) showToast(text, true);
      await after(res, res.ok && (!push || res.pushed) ? text : '');
      break;
    }
    case 'do-pr': {
      const res = await post('pr', { title: $('gc-pr-title')?.value || '', body: $('gc-pr-body')?.value || '' });
      if (res.ok) {
        setForm(null);
        lastPr = res.url || '';
      }
      await after(res, res.ok ? (res.existing ? 'That branch already has a PR' : 'Pull request created') : '');
      break;
    }
    case 'do-stash': {
      const res = await post('stash', { message: $('gc-stash-msg')?.value || '' });
      if (res.ok) setForm(null);
      await after(res, res.ok ? `Stashed as “${res.message}”` : '');
      break;
    }
    case 'dismiss-pr':
      lastPr = '';
      paintResult();
      break;
    default:
  }
}

function setForm(name) {
  form = name;
  render();
}

// ─── Init ─────────────────────────────────────────────────────────────────

function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(() => {
    if (isInspectorOpen() && !document.hidden && !working) refreshGit();
    schedulePoll();
  }, POLL_MS);
}

export function initGit() {
  const el = card();
  if (!el) return;
  el.addEventListener('click', (e) => {
    const m = e.target.closest('[data-menu]');
    if (m && !m.disabled) {
      openMenu(m.dataset.menu, m);
      return;
    }
    const b = e.target.closest('[data-act]');
    if (b && !b.disabled) act(b.dataset.act, b);
  });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && (menu || form)) {
      e.preventDefault();
      e.stopPropagation(); // Esc here closes the menu / form, not the panel or the turn
      if (menu) closeMenu();
      else setForm(null);
    } else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && form) {
      e.preventDefault();
      el.querySelector('.gc-form .btn-primary')?.click();
    }
  });
  document.addEventListener('pointerdown', (e) => {
    if (menu && !e.target.closest('.gc-menu, [data-menu]')) closeMenu();
  });
  onInspectorChange(({ open }) => { if (open) refreshGit(); });
  // A turn just ended: Jarvis may have changed files or committed.
  subscribe((s) => {
    if (wasBusy && !s.busy) refreshGit();
    else if (s.busy !== wasBusy) render();
    wasBusy = !!s.busy;
  });
  window.addEventListener('focus', () => { if (isInspectorOpen()) refreshGit(); });
  refreshGit();
  schedulePoll();
}
