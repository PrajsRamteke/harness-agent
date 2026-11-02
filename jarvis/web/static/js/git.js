/** Git card at the top of the inspector: Open in (Cursor, Finder…), switch /
 * create a branch, Commit & push (with Commit · Push · Create PR · Stash in its
 * menu), a model-written message, and the +/− of the working tree.
 * Server side: jarvis/web/git_api.py via GET/POST /api/git (the project shown). */
import { $, escapeHtml, showToast, storageGet, storageSet, BASE } from './utils.js';
import { icon } from './icons.js';
import { brand } from './brands.js';
import { api } from './api.js';
import { store, subscribe } from './store.js';
import { isInspectorOpen, onInspectorChange } from './inspector.js';
import { openGitChanges, refreshGitChanges, initGitChanges } from './gitchanges.js';

const EDITOR_KEY = 'jarvis-git-editor';
/** The git_api.py this page was written for (GIT_API_VERSION; 3 = pull + changed files). */
const NEEDS_API = 3;
const RESTART_NOTE = 'Restart Jarvis to finish updating — it still runs older git code that can only open a PR from the branch you are on';
const RESTART_PULL = 'Restart Jarvis to finish updating — it still runs older git code without Pull';
const serverCurrent = () => num(st?.api) >= NEEDS_API;
const EDITOR_ICON = { finder: 'folder', terminal: 'terminal' };
/** An editor's logo (Cursor, VS Code, Antigravity, Windsurf), else a plain icon. */
const editorIcon = (id) => brand(id) || icon(EDITOR_ICON[id] || 'code');
const POLL_MS = 15000;

let st = null;            // last /api/git answer
let form = null;          // null | 'commit' | 'commit-push' | 'pr' | 'stash'
let working = '';         // the op running right now ('' = idle)
let pullFrom = '';        // 'primary' | 'icon': which control started the pull (only it spins)
let menu = null;          // { kind, anchor }
let branchList = null;    // { current, local, remote }
let lastPr = '';          // URL of the PR just created
let pollTimer = null;
let wasBusy = false;
let seq = 0;              // newest /api/git request; older answers are dropped
let shownFor = null;      // the project (BASE + folder) the card shows

const card = () => $('git-card');
const num = (n) => Number(n) || 0;
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// ─── Data ─────────────────────────────────────────────────────────────────

/** Which project the page is on right now (switching projects changes both). */
const projectKey = () => `${BASE}|${store.session?.cwd || ''}`;

export async function refreshGit() {
  const mine = ++seq;
  const forKey = projectKey();
  let res;
  try {
    res = await api('/api/git');
  } catch {
    return; // keep what we had (a reconnect fixes it)
  }
  if (mine !== seq) return; // a newer request is on its way
  // The page moved to another project meanwhile: this answer is for another folder.
  if (forKey !== projectKey()) {
    refreshGit();
    return;
  }
  st = res;
  shownFor = forKey;
  render();
}

/** The page moved to another project / folder: show it at once, then load its git. */
function projectChanged() {
  menu = null;
  form = null;
  branchList = null;
  lastPr = '';
  st = { loading: true, repo: false, display: store.session?.cwd_display || store.session?.cwd || '', editors: [] };
  shownFor = projectKey();
  card()?.querySelectorAll('.gc-menu, .gc-form, .gc-result').forEach((n) => n.remove());
  render();
  refreshGit();
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
    if (data.cwd && st?.cwd && data.cwd !== st.cwd) return; // another project's news
    seq += 1; // newer than any request still on its way
    st = data;
    render();
    refreshGitChanges();
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
  if (canPull() && num(st.behind) > 0) {
    return { op: 'pull', label: `Pull ${plural(num(st.behind), 'commit')}`, icon: 'cloud-download' };
  }
  return { op: '', label: 'Up to date', icon: 'check', disabled: true, why: 'No changes to commit and nothing to push' };
}

/** The branch tracks a remote branch, so there's something to pull from. */
const canPull = () => !!(st?.repo && st.upstream && !st.upstream_gone && !st.detached);

/** The pull button on the branch row: only while commits are actually waiting
 * (or a pull is running, so its spinner stays put). Idle branches keep the row
 * clean — Pull is still in the ⋯ menu. */
function pullButton(busy) {
  const n = num(st.behind);
  const pulling = working === 'pull' && pullFrom !== 'primary';
  if (!canPull() || (!n && !pulling)) return '';
  const why = busy ? 'Jarvis is working — pull when it finishes'
    : `Pull ${plural(n, 'commit')} from ${st.upstream}`;
  return `<button type="button" class="gc-pull${n ? ' has-new' : ''}" data-act="pull" ${busy || pulling ? 'disabled' : ''} title="${escapeHtml(why)}" aria-label="${escapeHtml(why)}">
    ${pulling ? '<span class="spinner"></span>' : icon('cloud-download')}${n ? `<span>${n}</span>` : ''}
  </button>`;
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
  const parts = [pathHeader()];
  if (st.loading) {
    parts.push(`<p class="gc-note"><span class="spinner"></span><span>Reading git…</span></p>`);
    rows.innerHTML = parts.join('');
    return;
  }
  if (ed) {
    parts.push(`
      <div class="gc-split">
        <button type="button" class="gc-main" data-act="open" title="Open this folder in ${escapeHtml(ed.label)}">
          ${editorIcon(ed.id)}<span>Open in ${escapeHtml(ed.label)}</span>
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
  const pullBtn = pullButton(busy);
  const sync = [
    num(st.ahead) ? `<span title="${plural(num(st.ahead), 'commit')} to push">↑${num(st.ahead)}</span>` : '',
    // The pull button shows the ↓ count itself.
    num(st.behind) && !pullBtn ? `<span title="${plural(num(st.behind), 'commit')} to pull">↓${num(st.behind)}</span>` : '',
  ].join('');
  parts.push(`
    <div class="gc-line">
      <button type="button" class="gc-row" data-menu="branch" aria-haspopup="menu" ${busy ? 'disabled title="Jarvis is working — switch branches when it finishes"' : `title="Switch or create a branch"`}>
        ${icon('git-branch')}<span class="gc-branch">${escapeHtml(st.branch || 'no branch')}${st.detached ? ' <em>detached</em>' : ''}</span>
        ${sync ? `<span class="gc-sync">${sync}</span>` : ''}
        <span class="gc-chev">${icon('chevron-down')}</span>
      </button>${pullBtn}
    </div>`);
  const main = primaryAction();
  const mainBusy = ['commit', 'push', 'pr'].includes(working) || (working === 'pull' && pullFrom === 'primary');
  const pullBlocked = main.op === 'pull' && busy;
  parts.push(`
    <div class="gc-split">
      <button type="button" class="gc-main" data-act="primary" data-op="${main.op}" ${main.disabled || mainBusy || pullBlocked ? 'disabled' : ''} title="${escapeHtml(pullBlocked ? 'Jarvis is working — pull when it finishes' : main.why || main.label)}">
        ${mainBusy ? '<span class="spinner"></span>' : icon(main.icon)}<span>${escapeHtml(main.label)}</span>
      </button>
      <button type="button" class="gc-more" data-menu="commit" aria-haspopup="menu" aria-label="More git actions">${icon('chevron-down')}</button>
    </div>`);
  parts.push(`
    <button type="button" class="gc-row gc-changes" data-act="changes" title="See what changed, file by file">
      ${icon('file-diff')}<span>Changes</span>
      <span class="gc-nums">${num(st.files) ? `<b class="num-add">+${num(st.added)}</b><b class="num-del">−${num(st.removed)}</b>` : num(st.behind) ? `<em class="gc-incoming">${num(st.behind)} incoming</em>` : '<em>clean</em>'}</span>
    </button>`);
  rows.innerHTML = parts.join('');
  paintForm();
}

/** Which project this card is for: the folder's name (full path on hover). */
function pathHeader() {
  const full = String(st.cwd || st.display || '').replace(/\/+$/, '');
  const name = full.split('/').pop() || full;
  if (!name) return '';
  return `
    <div class="gc-path" title="${escapeHtml(st.display || full)}">
      <span class="gc-path-ic">${icon('folder')}</span>
      <span class="gc-path-name">${escapeHtml(name)}</span>
    </div>`;
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
      <label class="gc-label">Pull request</label>
      <div class="gc-pr-route">
        <div class="gc-field" title="The branch with your work">
          <span class="gc-field-k">From</span>
          <input id="gc-pr-head" class="gc-input gc-mono" type="text" placeholder="new branch, e.g. feat/…" autocomplete="off" spellcheck="false"
            role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="gc-pop-head" aria-label="Branch to open the PR from">
          <button type="button" class="gc-suggest" data-act="gen-branch" title="Suggest a branch name from your changes" aria-label="Suggest a branch name">${icon('wand-sparkles')}</button>
          <div class="gc-pop" id="gc-pop-head" role="listbox" aria-label="Branches" hidden></div>
        </div>
        <div class="gc-field" title="Where the changes should land">
          <span class="gc-field-k">Into</span>
          <button type="button" id="gc-pr-base" class="gc-input gc-mono gc-select" data-value="" aria-haspopup="listbox" aria-expanded="false" aria-controls="gc-pop-base" aria-label="Branch to merge into">
            <span class="gc-select-v">Loading…</span>${icon('chevron-down')}
          </button>
          <div class="gc-pop" id="gc-pop-base" role="listbox" aria-label="Branches to merge into" hidden></div>
        </div>
      </div>
      <p class="gc-plan" id="gc-pr-plan"></p>
      <input id="gc-pr-title" class="gc-input" type="text" placeholder="Title (empty = from the commits)" autocomplete="off">
      <textarea id="gc-pr-body" class="gc-input gc-textarea" rows="5" placeholder="Description (Markdown)"></textarea>
      <div class="gc-actions">
        <button type="button" class="btn btn-quiet gc-gen" data-act="generate-pr" title="Write the title and description with ${escapeHtml(genModelName())}">${icon('wand-sparkles')}<span>Generate</span></button>
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
        <button type="button" class="btn btn-quiet gc-gen" data-act="generate" title="Write the message with ${escapeHtml(genModelName())}">${icon('wand-sparkles')}<span>Generate</span></button>
        <span class="gc-spacer"></span>
        <button type="button" class="btn btn-quiet" data-act="cancel">Cancel</button>
        <button type="button" class="btn btn-primary" data-act="do-commit">${icon(push ? 'cloud-upload' : 'git-commit-horizontal')}<span>${push ? 'Commit & push' : 'Commit'}</span></button>
      </div>`;
  }
  el.appendChild(box);
  if (want === 'pr') initPrRoute(box);
  else box.querySelector('.gc-input')?.focus({ preventScroll: true });
  syncFormButtons(box);
  paintResult();
}

// ─── PR route: From (head) → Into (base) ─────────────────────────────────

/** "feat(web): Add the git card" → "feat/add-the-git-card" (git_api.slug_branch's twin). */
function slugBranch(text) {
  let t = String(text || '').trim();
  let prefix = '';
  const m = t.match(/^(feat|fix|chore|docs|refactor|perf|test|build|ci|style)(?:\([^)]*\))?!?:\s*(.*)$/i);
  if (m) {
    prefix = m[1].toLowerCase();
    t = m[2];
  } else if (/^(fixes|fixed|fix)\b/i.test(t)) {
    prefix = 'fix';
    t = t.replace(/^(fixes|fixed|fix)\s*/i, '');
  }
  const slug = t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
    .split('-').slice(0, 7).join('-').slice(0, 48).replace(/-+$/, '');
  return slug ? `${prefix || 'feat'}/${slug}` : '';
}

/** Put a branch name in From (unless you already chose a real one there). */
function fillHead(name) {
  const head = $('gc-pr-head');
  if (!head || !name) return;
  const cur = head.value.trim();
  if (!cur || cur === $('gc-pr-base')?.dataset.value) head.value = name;
}

// A small custom dropdown (no native <select> / <datalist>): a list of
// branches under its field, ↑ ↓ to move, Enter to pick, Esc to close.

function popOpen(pop) {
  return pop && !pop.hidden;
}

function closePop(pop) {
  if (!pop || pop.hidden) return;
  pop.hidden = true;
  pop.parentElement.querySelector('[aria-expanded]')?.setAttribute('aria-expanded', 'false');
}

function showPop(pop, items, { current = '', onPick, filter = false, empty = 'No branches match', preselect = true } = {}) {
  pop._items = items;
  pop._onPick = onPick;
  // A typed field (From) starts with nothing highlighted: Enter keeps what you typed.
  pop._i = preselect ? Math.max(0, items.indexOf(current)) : items.indexOf(current);
  const list = items.length
    ? items.map((it, i) => `
      <button type="button" class="gc-opt${it === current ? ' is-current' : ''}" role="option" data-i="${i}" aria-selected="${it === current}" tabindex="-1">
        ${icon('git-branch')}<span>${escapeHtml(it)}</span>${it === current ? icon('check', 'gc-check') : ''}
      </button>`).join('')
    : `<p class="gc-empty">${escapeHtml(empty)}</p>`;
  const search = filter ? pop.querySelector('.gc-pop-search') : null;
  if (filter && !search) {
    pop.innerHTML = `<label class="gc-pop-search">${icon('search')}<input type="text" placeholder="Filter branches" autocomplete="off" spellcheck="false" aria-label="Filter branches"></label><div class="gc-pop-list"></div>`;
  } else if (!filter) {
    pop.innerHTML = '<div class="gc-pop-list"></div>';
  }
  pop.querySelector('.gc-pop-list').innerHTML = list;
  pop.hidden = false;
  pop.parentElement.querySelector('[aria-expanded]')?.setAttribute('aria-expanded', 'true');
  markPop(pop);
}

function markPop(pop) {
  pop.querySelectorAll('.gc-opt').forEach((b) => b.classList.toggle('is-hi', Number(b.dataset.i) === pop._i));
  pop.querySelector('.gc-opt.is-hi')?.scrollIntoView({ block: 'nearest' });
}

/** Arrow keys / Enter / Esc for an open dropdown. True when the key was used. */
function popKey(pop, e) {
  if (!popOpen(pop)) return false;
  const n = pop._items?.length || 0;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (n) pop._i = pop._i < 0 ? (e.key === 'ArrowDown' ? 0 : n - 1) : (pop._i + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
    markPop(pop);
    return true;
  }
  if (e.key === 'Enter' && n && pop._i >= 0) {
    e.preventDefault();
    pop._onPick?.(pop._items[pop._i]);
    return true;
  }
  if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation(); // closes the list, not the form
    closePop(pop);
    return true;
  }
  return false;
}

function wirePop(pop) {
  // pointerdown: pick before the field loses focus.
  pop.addEventListener('pointerdown', (e) => {
    const opt = e.target.closest('.gc-opt');
    if (!opt) return;
    e.preventDefault();
    pop._onPick?.(pop._items[Number(opt.dataset.i)]);
  });
}

function setBase(box, value) {
  const btn = box.querySelector('#gc-pr-base');
  btn.dataset.value = value || '';
  btn.querySelector('.gc-select-v').textContent = value || 'Pick a branch';
  paintPrPlan(box);
}

async function initPrRoute(box) {
  const head = box.querySelector('#gc-pr-head');
  const base = box.querySelector('#gc-pr-base');
  const headPop = box.querySelector('#gc-pop-head');
  const basePop = box.querySelector('#gc-pop-base');
  wirePop(headPop);
  wirePop(basePop);

  const headItems = () => {
    const q = head.value.trim().toLowerCase();
    return (branchList?.local || []).filter((b) => b !== base.dataset.value && (!q || b.toLowerCase().includes(q))).slice(0, 30);
  };
  const showHeads = () => {
    if (!branchList) return;
    const items = headItems();
    if (!items.length) {
      closePop(headPop); // a new name: nothing to suggest, the plan line says what happens
      return;
    }
    showPop(headPop, items, {
      current: head.value.trim(),
      preselect: false,
      onPick: (v) => {
        head.value = v;
        closePop(headPop);
        paintPrPlan(box);
      },
    });
  };
  head.addEventListener('focus', showHeads);
  head.addEventListener('input', () => {
    paintPrPlan(box);
    showHeads();
  });
  head.addEventListener('keydown', (e) => {
    if (popKey(headPop, e)) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      showHeads();
    }
  });
  head.addEventListener('blur', () => setTimeout(() => closePop(headPop), 120));

  const bases = () => (branchList?.bases?.length ? branchList.bases : branchList?.local || []);
  const showBases = (q = '') => {
    const all = bases();
    const items = all.filter((b) => !q || b.toLowerCase().includes(q.toLowerCase()));
    showPop(basePop, items, {
      current: base.dataset.value,
      filter: all.length > 8,
      onPick: (v) => {
        setBase(box, v);
        closePop(basePop);
        base.focus({ preventScroll: true });
      },
    });
  };
  base.addEventListener('click', () => {
    if (popOpen(basePop)) closePop(basePop);
    else {
      closePop(headPop);
      showBases();
      basePop.querySelector('.gc-pop-search input')?.focus({ preventScroll: true });
    }
  });
  base.addEventListener('keydown', (e) => {
    if (popKey(basePop, e)) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      showBases();
    }
  });
  basePop.addEventListener('input', (e) => {
    if (e.target.matches('.gc-pop-search input')) showBases(e.target.value);
  });
  basePop.addEventListener('keydown', (e) => {
    if (e.target.matches('.gc-pop-search input')) popKey(basePop, e);
  });
  // Clicks elsewhere close both lists.
  box.addEventListener('pointerdown', (e) => {
    if (!e.target.closest('#gc-pop-head, #gc-pr-head')) closePop(headPop);
    if (!e.target.closest('#gc-pop-base, #gc-pr-base')) closePop(basePop);
  });

  const res = await post('branches');
  render();
  branchList = res.ok ? res : { current: st.branch, local: [], remote: [], bases: [], base: '' };
  const first = bases();
  setBase(box, branchList.base || first[0] || '');
  // From: the branch you're on, unless that's the one you'd merge into.
  if (!head.value) head.value = st.branch && st.branch !== base.dataset.value && !st.detached ? st.branch : '';
  paintPrPlan(box);
  if (head.value) box.querySelector('#gc-pr-title').focus({ preventScroll: true });
  else head.focus({ preventScroll: true });
}

/** One line saying what Create PR will do with these two branches. */
function paintPrPlan(box) {
  const plan = box.querySelector('#gc-pr-plan');
  const submit = box.querySelector('[data-act="do-pr"]');
  if (!plan) return;
  const head = box.querySelector('#gc-pr-head').value.trim();
  const base = box.querySelector('#gc-pr-base').dataset.value || '';
  const files = num(st.files);
  const changes = plural(files, 'changed file');
  let text = '';
  let warn = false;
  if (!serverCurrent() && head && head !== st.branch) {
    text = RESTART_NOTE;
    warn = true;
  } else if (!head) {
    text = `Name a branch for your work — or ✦ suggest one`;
    warn = true;
  } else if (head === base) {
    text = `A PR merges one branch into another — pick a branch other than ${base}`;
    warn = true;
  } else if (!branchList?.local?.includes(head)) {
    text = `Creates ${head} from your current work${files ? `, commits ${changes} with the title` : ''}, pushes it, opens the PR`;
  } else if (head === st.branch) {
    text = `${files ? `Commits ${changes} with the title, ` : ''}pushes ${head}, opens the PR`;
  } else {
    text = `Pushes ${head} as it is${files ? ` — your ${changes} stay here on ${st.branch}` : ''}`;
  }
  plan.textContent = text;
  plan.classList.toggle('is-warn', warn);
  // With changes to commit, the title is also the commit message: it's needed.
  const commits = files > 0 && head && head !== base && (head === st.branch || !branchList?.local?.includes(head));
  const title = box.querySelector('#gc-pr-title');
  if (title) title.placeholder = commits ? 'Title (also the commit message)' : 'Title (empty = from the commits)';
  if (submit) submit.dataset.blocked = warn ? '1' : '';
}

/** Who writes generated text: Settings → Commit text model, else the chat's model. */
function genModelName() {
  const side = store.session?.side_model;
  if (side && !side.auto && side.label) return side.label;
  const chat = store.session?.model;
  return chat ? `the chat's model (${String(chat).split('/').pop()})` : 'the chat\u2019s model';
}

function syncFormButtons(box) {
  box.querySelectorAll('button').forEach((b) => { b.disabled = !!working; });
  const suggest = box.querySelector('.gc-suggest');
  if (suggest) suggest.innerHTML = working === 'generate' ? '<span class="spinner"></span>' : icon('wand-sparkles');
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

function menuItem(act, iconName, label, { disabled = false, why = '', hint = '', checked = false, data = '', logo = '' } = {}) {
  return `<button type="button" class="gc-item${checked ? ' is-current' : ''}" role="menuitem" data-act="${act}" ${data} ${disabled ? 'disabled' : ''} title="${escapeHtml(why || label)}">
    ${logo || icon(iconName)}<span>${escapeHtml(label)}</span>${hint ? `<em>${escapeHtml(hint)}</em>` : ''}${checked ? icon('check', 'gc-check') : ''}
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
      checked: cur?.id === e.id, data: `data-editor="${escapeHtml(e.id)}"`, logo: brand(e.id),
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
      menuItem('pull', 'cloud-download', 'Pull', {
        disabled: !canPull() || store.busy,
        why: store.busy ? 'Jarvis is working in these files' : !canPull() ? 'This branch doesn\'t track a remote branch' : `Bring in new commits from ${st.upstream} (fast-forward only)`,
        hint: num(st.behind) ? `↓${num(st.behind)}` : '',
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
  refreshGitChanges();
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
      if (main?.op === 'push' || main?.op === 'pull') await act(main.op, btn);
      else if (main?.op) setForm(main.op);
      break;
    }
    case 'changes':
      openGitChanges();
      break;
    case 'pull': {
      closeMenu();
      pullFrom = btn?.dataset?.act === 'primary' ? 'primary' : 'icon';
      const res = await post('pull');
      pullFrom = '';
      if (!res.ok && /unknown git op/.test(res.error || '')) res.error = RESTART_PULL;
      const text = !res.ok ? '' : res.commits
        ? `Pulled ${plural(res.commits, 'commit')}${res.summary ? ` · ${res.summary}` : ''}`
        : `Already up to date with ${res.upstream || 'the remote'}`;
      await after(res, text);
      break;
    }
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
        // From empty (you're on the base branch): the suggested branch, or one named after the title.
        fillHead(res.branch || slugBranch(res.title));
        const box = card().querySelector('.gc-form');
        if (box) paintPrPlan(box);
      } else showToast(res.error || 'Could not write a description', true);
      break;
    }
    case 'gen-branch': {
      // A title is written: name the branch after it right here (instant, no model).
      const title = $('gc-pr-title')?.value.trim() || '';
      const local = slugBranch(title);
      const res = local ? { ok: true, branch: local } : await post('generate', { kind: 'branch' });
      if (!local) render();
      if (res.ok && res.branch) {
        const head = $('gc-pr-head');
        if (head) {
          head.value = res.branch;
          head.focus();
        }
        const box = card().querySelector('.gc-form');
        if (box) paintPrPlan(box);
      } else showToast(res.error || 'Could not suggest a name', true);
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
      const head = $('gc-pr-head')?.value.trim() || '';
      const base = $('gc-pr-base')?.dataset.value || '';
      if (!head || head === base) {
        showToast(!head ? 'Name the branch this PR comes from' : `Pick a branch other than ${base} to open the PR from`, true);
        $('gc-pr-head')?.focus();
        break;
      }
      if (!serverCurrent() && head !== st.branch) {
        // An older server would ignore From / Into and run gh from this branch.
        showToast(RESTART_NOTE, true);
        break;
      }
      const res = await post('pr', { title: $('gc-pr-title')?.value || '', body: $('gc-pr-body')?.value || '', head, base });
      if (res.ok) {
        setForm(null);
        lastPr = res.url || '';
      }
      const done = res.existing ? 'That branch already has a PR'
        : res.created_branch ? `Created ${res.head}, pushed it and opened the PR` : 'Pull request created';
      await after(res, res.ok ? done : '');
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
  initGitChanges({ pull: () => act('pull') });
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
    if (shownFor !== null && projectKey() !== shownFor) {
      projectChanged();
      wasBusy = !!s.busy;
      return;
    }
    if (wasBusy && !s.busy) {
      refreshGit();
      refreshGitChanges();
    }
    else if (s.busy !== wasBusy) render();
    wasBusy = !!s.busy;
  });
  window.addEventListener('focus', () => { if (isInspectorOpen()) refreshGit(); });
  refreshGit();
  schedulePoll();
}
