/** Folders — see where Jarvis works, browse to another folder, and open it.
 *
 *   top bar      [📁 ~/Desktop/harness-buddy/ harness ▾]   ← click: the picker, here
 *
 *   ┌ Open a folder ─────────────────────────────────────────────── × ┐
 *   │ [↑]  ~ › Desktop › harness-buddy › harness                       │
 *   │ [🔍 Filter folders, or type a path like ~/code/app            ]  │
 *   │ JUMP TO   (● billing-api) (Recent…) (Desktop) (Documents) …      │
 *   │ FOLDERS IN HARNESS · 12                         Show hidden (3)  │
 *   │  📁 docs                                                  Open › │
 *   │  ⎇  jarvis            git · Python        ● running       Open › │
 *   ├──────────────────────────────────────────────────────────────────┤
 *   │ 📁 harness  ~/Desktop/harness-buddy/harness   [Move this chat here] [Open in new session] │
 *   └──────────────────────────────────────────────────────────────────┘
 *
 * "Open in new session" starts another Jarvis in that folder — no terminal
 * needed, so it works from a phone through the "Anywhere" link — and switches
 * to it once it's ready; the two run side by side (the Projects list). A
 * folder that already has a Jarvis offers "Switch to it". "Move this chat
 * here" keeps the current chat and points it at the folder (the /cd command).
 *
 * New chat while a reply is running (`newChatBeside`) uses the same launch:
 * one Jarvis holds one chat, so the new chat gets its own Jarvis in this
 * folder and the running reply keeps going in the old one (under Projects).
 */
import { $, escapeHtml, showToast, debounce, haptic } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe } from './store.js';
import { openModal, closeModal, isModalOpen } from './modal.js';
import { setView, section, empty } from './dialog.js';
import { fetchDirs, fetchFsStart, openProjectAt, fetchLaunch, moveChat } from './api.js';
import { switchProject, refreshProjects, currentProjectId, findProject } from './projects.js';
import { runAction } from './actions.js';

const ID = 'folders';
const POLL_MS = 350;

const F = {
  path: '',          // the folder on screen
  data: null,        // its listing (/api/fs/dirs)
  error: null,       // { code, error, path } when it couldn't be shown
  loading: false,
  seq: 0,
  query: '',
  hidden: false,     // show .folders
  start: null,       // { recent, shortcuts, home }
  launch: null,      // the launch being followed
  launchTimer: 0,
  acting: '',        // 'open' | 'move' | 'switch' while a footer action runs
};

// ─── Small helpers ────────────────────────────────────────────────────────

const samePath = (a, b) => !!a && !!b && a.replace(/\/+$/, '') === b.replace(/\/+$/, '');
const looksLikePath = (q) => /^(~|\/|\.\.?\/)/.test(q);

/** "~/Desktop/harness-buddy/harness" → { parent: "~/…/harness-buddy/", name: "harness" } */
export function splitDisplay(display) {
  const text = String(display || '');
  if (!text || text === '~' || text === '/') return { parent: '', name: text || '' };
  const parts = text.split('/');
  const name = parts.pop() || text;
  let parent = parts;
  if (parent.length > 3) parent = [parent[0], '…', ...parent.slice(-2)];
  return { parent: parent.length ? `${parent.join('/')}/` : '', name };
}

function markerChips(markers = [], max = 3) {
  return markers.slice(0, max).map((m) => `<span class="fd-mark${m === 'git' ? ' is-git' : ''}">${escapeHtml(m)}</span>`).join('');
}

function folderIcon(markers = []) {
  if (markers.includes('git')) return 'git-branch';
  return 'folder';
}

// ─── Top-bar chip ─────────────────────────────────────────────────────────

let chipSig = '';

function renderChip(s) {
  const chip = $('cwd-chip');
  if (!chip) return;
  const display = s.session.cwd_display || '';
  const sig = `${display}|${s.session.headless}`;
  if (sig === chipSig) return;
  chipSig = sig;
  chip.hidden = !display;
  const { parent, name } = splitDisplay(display);
  $('cwd-parent').textContent = parent;
  $('cwd-name').textContent = name;
  chip.title = `${s.session.cwd || display}${s.session.headless ? ' · opened from the web' : ''} — change folder`;
  chip.setAttribute('aria-label', `Working folder ${display}. Change folder`);
}

// ─── Data ─────────────────────────────────────────────────────────────────

async function go(path, { focusSearch = false } = {}) {
  const seq = ++F.seq;
  F.loading = true;
  $(`${ID}-card`)?.classList.add('is-loading');
  let data;
  try {
    data = await fetchDirs(path, F.hidden);
  } catch (err) {
    data = { ok: false, code: err?.status === 401 ? 'expired' : 'network', error: err?.status === 401
      ? 'This link has expired — open the new one from your terminal.'
      : 'Jarvis is not reachable right now. Check the connection and try again.' };
  }
  if (seq !== F.seq) return; // a newer navigation won
  F.loading = false;
  $(`${ID}-card`)?.classList.remove('is-loading');
  if (data?.ok) {
    F.data = data;
    F.path = data.path;
    F.error = null;
    F.query = '';
    const input = $(`${ID}-q`);
    if (input) input.value = '';
  } else {
    F.error = { code: data?.code || 'error', error: data?.error || 'Could not open that folder.', path };
  }
  renderCrumbs();
  renderBody();
  renderFoot();
  const body = $(`${ID}-body`);
  if (body) body.scrollTop = 0;
  if (focusSearch) $(`${ID}-q`)?.focus({ preventScroll: true });
}

async function loadStart() {
  try {
    const data = await fetchFsStart();
    if (data?.ok) {
      F.start = data;
      if (isModalOpen(ID) && !F.launch) renderBody();
    }
  } catch { /* the jump strip just stays empty */ }
}

// ─── Rendering ────────────────────────────────────────────────────────────

function renderBar() {
  const bar = $(`${ID}-bar`);
  if (!bar) return;
  bar.innerHTML = `
    <div class="fd-crumbs-row">
      <button type="button" class="icon-btn fd-up" data-act="up" aria-label="Up one folder" title="Up one folder (⌫)">${icon('arrow-up')}</button>
      <nav class="fd-crumbs" id="${ID}-crumbs" aria-label="Folder path"></nav>
    </div>
    <div class="dlg-toolbar">
      <label class="dlg-search">
        <span class="dlg-search-ic">${icon('search')}</span>
        <input id="${ID}-q" type="search" placeholder="Filter folders, or type a path like ~/code/app"
          aria-label="Filter folders or type a path" autocomplete="off" autocapitalize="off" autocorrect="off"
          spellcheck="false" enterkeyhint="go" data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other">
        <span class="dlg-kbd" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd><kbd>↵</kbd></span>
      </label>
    </div>
    <div class="fd-progress" aria-hidden="true"></div>`;
}

function renderCrumbs() {
  const nav = $(`${ID}-crumbs`);
  const up = $(`${ID}-bar`)?.querySelector('.fd-up');
  const segs = F.data?.segments || [];
  if (up) up.disabled = !F.data?.parent;
  if (!nav) return;
  nav.innerHTML = segs.map((s, i) => {
    const last = i === segs.length - 1;
    return `${i ? `<span class="fd-sep" aria-hidden="true">${icon('chevron-right')}</span>` : ''}<button type="button" class="fd-crumb${last ? ' is-here' : ''}"
      data-act="go" data-path="${escapeHtml(s.path)}" ${last ? 'aria-current="location"' : ''}${s.name === '~' ? ' aria-label="Home" title="Home"' : ''}>${s.name === '~' ? icon('house') : escapeHtml(s.name)}</button>`;
  }).join('');
  // The folder you're in stays in view on narrow screens.
  requestAnimationFrame(() => { nav.scrollLeft = nav.scrollWidth; });
}

function jumpStrip() {
  if (!F.start) return '';
  const cur = currentProjectId();
  const chips = [];
  const seen = new Set();
  for (const r of F.start.recent || []) {
    if (seen.has(r.path) || samePath(r.path, F.path)) continue;
    seen.add(r.path);
    const live = (r.running || []).length;
    const here = (r.running || []).some((x) => x.id === cur);
    chips.push(`<button type="button" class="fd-jump${live ? ' is-live' : ''}" data-act="go" data-path="${escapeHtml(r.path)}" title="${escapeHtml(r.display)}${live ? (here ? ' · this chat' : ' · running') : ''}">
      ${live ? '<span class="fd-live" aria-hidden="true"></span>' : icon(folderIcon(r.markers))}<span>${escapeHtml(r.name)}</span></button>`);
    if (chips.length >= 8) break;
  }
  const places = (F.start.shortcuts || []).filter((s) => !seen.has(s.path) && !samePath(s.path, F.path)).map((s) =>
    `<button type="button" class="fd-jump is-place" data-act="go" data-path="${escapeHtml(s.path)}" title="${escapeHtml(s.display)}">
      ${icon(s.name === 'Home' ? 'house' : 'folder')}<span>${escapeHtml(s.name)}</span></button>`);
  if (!chips.length && !places.length) return '';
  return `<div class="fd-jumps" role="group" aria-label="Jump to">
    ${chips.length ? `<span class="fd-jumps-lbl">Recent</span>${chips.join('')}` : ''}
    ${places.length ? `${chips.length ? '<span class="fd-jumps-gap" aria-hidden="true"></span>' : ''}<span class="fd-jumps-lbl">Places</span>${places.join('')}` : ''}
  </div>`;
}

function row(d, i) {
  const cur = currentProjectId();
  const running = d.running || [];
  const here = running.some((x) => x.id === cur);
  const badge = running.length
    ? `<span class="fd-run${here ? ' is-here' : ''}"><span class="fd-live" aria-hidden="true"></span>${here ? 'This chat' : running.length > 1 ? `${running.length} running` : 'Running'}</span>`
    : '';
  const disabled = d.readable === false;
  return `
    <div class="fd-row${disabled ? ' is-locked' : ''}${d.hidden ? ' is-hidden' : ''}" style="--i:${Math.min(i, 16)}">
      <button type="button" class="fd-main" data-act="enter" data-path="${escapeHtml(d.path)}" ${disabled ? 'disabled title="Jarvis isn\'t allowed to open this folder"' : ''}>
        <span class="fd-ic${d.markers?.length ? ' is-project' : ''}">${icon(disabled ? 'lock' : folderIcon(d.markers))}</span>
        <span class="fd-name">${escapeHtml(d.name)}${d.link ? `<span class="fd-link" title="Shortcut (symlink)">${icon('link')}</span>` : ''}</span>
        <span class="fd-meta">${markerChips(d.markers)}${badge}</span>
        <span class="fd-chev" aria-hidden="true">${icon('chevron-right')}</span>
      </button>
      ${disabled ? '' : `<button type="button" class="fd-open" data-act="${running.length ? 'switch' : 'open'}" data-path="${escapeHtml(d.path)}"
        ${running.length ? `data-id="${escapeHtml(here ? cur : running[0].id)}"` : ''} title="${running.length ? (here ? 'This chat works here' : 'Switch to the Jarvis running here') : 'Open in a new session'}">
        ${running.length ? (here ? 'Here' : 'Switch') : 'Open'}</button>`}
    </div>`;
}

function renderBody() {
  const body = $(`${ID}-body`);
  if (!body || F.launch) return;
  if (F.error && !F.data) {
    body.innerHTML = empty('Can’t open this folder', escapeHtml(F.error.error), {
      ic: 'circle-alert',
      action: `<button type="button" class="btn" data-act="go" data-path="${escapeHtml(F.start?.home || '~')}">${icon('house')}<span>Go home</span></button>`,
    });
    return;
  }
  if (!F.data) {
    body.innerHTML = '<div class="fd-skel"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>';
    return;
  }
  const q = F.query.trim();
  const errorNote = F.error
    ? `<div class="fd-alert" role="alert">${icon('circle-alert')}<span>${escapeHtml(F.error.error)}</span></div>`
    : '';
  let html = errorNote;
  if (q && looksLikePath(q)) {
    html += `<div class="fd-row"><button type="button" class="fd-main fd-goto" data-act="go" data-path="${escapeHtml(q)}">
      <span class="fd-ic">${icon('corner-down-right')}</span><span class="fd-name">Go to <code>${escapeHtml(q)}</code></span>
      <span class="fd-meta"><kbd>↵</kbd></span></button></div>`;
  }
  if (!q) html += jumpStrip();
  const all = F.data.dirs || [];
  const needle = q.toLowerCase();
  const shown = q && !looksLikePath(q) ? all.filter((d) => d.name.toLowerCase().includes(needle)) : all;
  const hiddenBtn = F.data.hidden || F.hidden
    ? `<button type="button" class="dlg-section-btn" data-act="hidden" aria-pressed="${F.hidden}">${icon(F.hidden ? 'eye-off' : 'eye')}<span>${F.hidden ? 'Hide hidden' : `Show hidden (${F.data.hidden})`}</span></button>`
    : '';
  html += section(q && !looksLikePath(q) ? `Matching “${q}”` : `Folders in ${F.data.name}`, { count: shown.length, right: hiddenBtn });
  if (!shown.length) {
    html += q && !looksLikePath(q)
      ? `<p class="fd-none">No folder here matches “${escapeHtml(q)}”. Type a path starting with <code>~</code> or <code>/</code> to go elsewhere.</p>`
      : `<p class="fd-none">${icon('folder-open')}<span>No folders inside. You can open this one, or go up.</span></p>`;
  } else {
    html += `<div class="fd-list" role="list">${shown.map(row).join('')}</div>`;
  }
  if (F.data.truncated) html += `<p class="fd-none">${F.data.truncated} more folders not shown — type to filter.</p>`;
  body.innerHTML = html;
}

function renderFoot() {
  const foot = $(`${ID}-foot`);
  if (!foot || F.launch) return;
  const d = F.data;
  if (!d) {
    foot.innerHTML = '';
    return;
  }
  const cur = currentProjectId();
  const running = (d.running || []).filter((x) => x.id !== cur);
  const here = samePath(d.path, store.session.cwd) || (d.running || []).some((x) => x.id === cur);
  const busy = F.acting;
  const spin = (act) => (busy === act ? '<span class="spinner" aria-hidden="true"></span>' : '');
  let actions;
  if (here) {
    actions = `
      <span class="fd-here-note">${icon('circle-check')}<span>This chat works here</span></span>
      <button type="button" class="btn btn-primary" data-act="open-new" ${busy ? 'disabled' : ''}>${spin('open') || icon('plus')}<span>New session here</span></button>`;
  } else if (running.length) {
    const p = findProject(running[0].id);
    actions = `
      <button type="button" class="btn" data-act="open-new" ${busy ? 'disabled' : ''}>${spin('open') || icon('plus')}<span>New session here</span></button>
      <button type="button" class="btn btn-primary" data-act="switch" data-id="${escapeHtml(running[0].id)}" ${busy ? 'disabled' : ''}>${icon('arrow-right-left')}<span>Switch to ${escapeHtml(p?.project || d.name)}</span></button>`;
  } else {
    actions = `
      <button type="button" class="btn" data-act="move" ${busy ? 'disabled' : ''} title="Keep this chat and work in ${escapeHtml(d.display)} from now on">${spin('move') || icon('corner-down-right')}<span>Move this chat here</span></button>
      <button type="button" class="btn btn-primary" data-act="open-here" ${busy ? 'disabled' : ''}>${spin('open') || icon('play')}<span>Open in new session</span></button>`;
  }
  foot.innerHTML = `
    <div class="fd-sel">
      <span class="fd-sel-ic">${icon(folderIcon(d.markers))}</span>
      <span class="fd-sel-text"><strong>${escapeHtml(d.name)}</strong><span>${escapeHtml(d.display)}</span></span>
      <span class="fd-sel-marks">${markerChips(d.markers, 2)}</span>
    </div>
    <div class="fd-actions">${actions}</div>`;
}

// ─── Opening a folder ─────────────────────────────────────────────────────

const STAGES = [
  ['spawn', 'Starting Jarvis', 'A new Jarvis, no terminal needed'],
  ['boot', 'Loading the project', 'Agents, skills and settings for this folder'],
  ['ready', 'Ready', 'Switching you over'],
];

function renderLaunch() {
  const L = F.launch;
  const body = $(`${ID}-body`);
  const foot = $(`${ID}-foot`);
  if (!L || !body || !foot) return;
  const failed = L.status === 'failed';
  const at = STAGES.findIndex(([k]) => k === (L.status === 'ready' ? 'ready' : L.stage));
  const title = L.beside
    ? (failed ? 'Couldn’t start a new chat' : L.status === 'ready' ? 'New chat is ready' : 'Starting a new chat')
    : (failed ? `Couldn’t open ${escapeHtml(L.name)}` : L.status === 'ready' ? `${escapeHtml(L.name)} is ready` : `Opening ${escapeHtml(L.name)}`);
  body.innerHTML = `
    <div class="fl-wrap${failed ? ' is-failed' : ''}${L.status === 'ready' ? ' is-ready' : ''}">
      <div class="fl-hero" aria-hidden="true">
        <span class="fl-orbit"></span>
        <span class="fl-tile">${icon(failed ? 'circle-alert' : L.status === 'ready' ? 'check' : 'folder-open')}</span>
      </div>
      <h3 class="fl-title">${title}</h3>
      <p class="fl-path">${escapeHtml(L.display || '')}</p>
      ${failed ? `
        <p class="fl-error" role="alert">${escapeHtml(L.error || 'Something went wrong.')}</p>
        ${L.log ? `<details class="fl-log"><summary>Show what Jarvis printed</summary><pre>${escapeHtml(L.log)}</pre></details>` : ''}`
        : `<ol class="fl-steps">${STAGES.map(([key, label, sub], i) => `
          <li class="${i < at ? 'is-done' : i === at ? 'is-now' : ''}">
            <span class="fl-dot">${i < at ? icon('check') : i === at ? '<span class="spinner" aria-hidden="true"></span>' : ''}</span>
            <span class="fl-step"><strong>${label}</strong><span>${sub}</span></span>
          </li>`).join('')}</ol>
        <p class="fl-time">${L.elapsed ? `${Math.round(L.elapsed)} s` : ''}</p>`}
    </div>`;
  if (failed && L.beside) {
    // No second Jarvis: the old way is still on offer (stops the running reply).
    foot.innerHTML = `<div class="fd-actions is-wide"><button type="button" class="btn" data-act="launch-replace">${icon('square')}<span>Stop the reply and start here</span></button>
       <button type="button" class="btn btn-primary" data-act="launch-retry">${icon('rotate-ccw')}<span>Try again</span></button></div>`;
    return;
  }
  const note = L.beside
    ? 'The running reply keeps going — it’s under Projects.'
    : 'Keeps running if you close this — it shows up under Projects.';
  foot.innerHTML = failed
    ? `<div class="fd-actions is-wide"><button type="button" class="btn" data-act="launch-back">${icon('arrow-left')}<span>Back to folders</span></button>
       <button type="button" class="btn btn-primary" data-act="launch-retry">${icon('rotate-ccw')}<span>Try again</span></button></div>`
    : `<div class="fd-actions is-wide"><span class="fd-here-note">${icon('info')}<span>${note}</span></span>
       <button type="button" class="btn" data-act="launch-hide">Hide</button></div>`;
}

/** The launch view takes the whole dialog (no path bar / search under the header). */
function setLaunching(on) {
  $(`${ID}-card`)?.classList.toggle('is-launching', !!on);
}

function stopFollowing() {
  clearTimeout(F.launchTimer);
  F.launchTimer = 0;
}

function leaveLaunch() {
  stopFollowing();
  F.launch = null;
  setLaunching(false);
  setView(ID, null);
  renderBody();
  renderFoot();
}

async function followLaunch(misses = 0) {
  const L = F.launch;
  if (!L) return;
  let next;
  try {
    const res = await fetchLaunch(L.id);
    next = res?.launch;
    if (!next) throw new Error('gone');
  } catch {
    if (F.launch !== L) return;
    if (misses >= 20) {
      F.launch = { ...L, status: 'failed', error: 'Lost track of the new Jarvis. Check the Projects list — it may still be starting.' };
      renderLaunch();
      return;
    }
    F.launchTimer = setTimeout(() => followLaunch(misses + 1), POLL_MS * 2);
    return;
  }
  if (F.launch !== L) return;
  F.launch = { ...next, beside: L.beside };
  if (isModalOpen(ID)) renderLaunch();
  if (next.status === 'ready') return arrive(F.launch);
  if (next.status === 'failed') {
    haptic([20, 40, 20]);
    if (!isModalOpen(ID)) showToast(`Couldn’t open ${next.name}: ${next.error}`, true);
    return;
  }
  F.launchTimer = setTimeout(() => followLaunch(0), POLL_MS);
}

/** The new Jarvis is listed: switch to it (or say so, if the picker was closed meanwhile). */
async function arrive(launch) {
  stopFollowing();
  await refreshProjects();
  const open = isModalOpen(ID);
  if (!open) {
    F.launch = null;
    setLaunching(false);
    showToast(launch.beside ? 'New chat is ready — it’s under Projects' : `${launch.name} is ready — it’s under Projects`);
    return;
  }
  haptic(12);
  await new Promise((r) => setTimeout(r, 450)); // let "Ready" register
  if (F.launch?.id !== launch.id) return;
  F.launch = null;
  setLaunching(false);
  setView(ID, null);
  closeModal(ID);
  await switchProject(launch.instance_id);
  showToast(launch.beside
    ? 'New chat · the running reply keeps going under Projects'
    : `${launch.name} is open — your other projects keep running`);
  if (launch.beside) $('prompt')?.focus();
}

async function openHere(path, { reuse = true } = {}) {
  if (F.acting) return;
  F.acting = 'open';
  renderFoot();
  let res;
  try {
    res = await openProjectAt(path, reuse);
  } catch (err) {
    res = { ok: false, error: err?.status === 401 ? 'This link has expired.' : 'Jarvis is not reachable right now.' };
  }
  F.acting = '';
  if (!res?.ok) {
    renderFoot();
    showToast(res?.error || 'Could not open that folder', true);
    return;
  }
  if (res.reused) {
    renderFoot();
    closeModal(ID);
    await refreshProjects();
    await switchProject(res.instance_id);
    showToast(`${res.launch?.name || 'That folder'} was already open — switched to it`);
    return;
  }
  F.launch = { ...res.launch, display: res.launch.display };
  setLaunching(true);
  setView(ID, { title: `Opening ${res.launch.name}`, sub: res.launch.display, back: leaveLaunch, backLabel: 'folders' });
  renderLaunch();
  F.launchTimer = setTimeout(() => followLaunch(0), POLL_MS);
}

async function moveHere(path, display) {
  if (F.acting) return;
  if (store.busy) {
    showToast('Jarvis is working — wait for it to finish, or stop it first', true);
    return;
  }
  F.acting = 'move';
  renderFoot();
  let res;
  try {
    res = await moveChat(path);
  } catch (err) {
    res = { ok: false, error: err?.status === 401 ? 'This link has expired.' : 'Jarvis is not reachable right now.' };
  }
  F.acting = '';
  if (!res?.ok) {
    renderFoot();
    showToast(res?.error || 'Could not move this chat', true);
    return;
  }
  closeModal(ID);
  showToast(res.unchanged ? 'This chat already works there' : `This chat now works in ${res.display || display}`);
  refreshProjects();
}

async function switchTo(id) {
  if (!id || id === currentProjectId()) {
    closeModal(ID);
    return;
  }
  closeModal(ID);
  await refreshProjects();
  await switchProject(id);
}

// ─── Events ───────────────────────────────────────────────────────────────

function onClick(e) {
  const btn = e.target.closest('[data-act]');
  if (!btn || btn.disabled) return;
  const { act, path, id } = btn.dataset;
  if (act === 'go' || act === 'enter') go(path);
  else if (act === 'up' && F.data?.parent) go(F.data.parent);
  else if (act === 'hidden') {
    F.hidden = !F.hidden;
    go(F.path);
  } else if (act === 'open') openHere(path);
  else if (act === 'switch') switchTo(id);
  else if (act === 'open-here') openHere(F.path);
  else if (act === 'open-new') openHere(F.path, { reuse: false });
  else if (act === 'move') moveHere(F.path, F.data?.display);
  else if (act === 'launch-back') leaveLaunch();
  else if (act === 'launch-replace') replaceChat();
  else if (act === 'launch-retry' && F.launch?.beside) newChatBeside();
  else if (act === 'launch-retry') {
    const path0 = F.launch?.path;
    leaveLaunch();
    if (path0) openHere(path0, { reuse: false });
  } else if (act === 'launch-hide') closeModal(ID);
}

const applyFilter = debounce(() => {
  if (!F.launch) renderBody();
}, 60);

function onInput(e) {
  if (e.target.id !== `${ID}-q`) return;
  F.query = e.target.value;
  applyFilter();
}

function rowButtons() {
  return [...($(`${ID}-body`)?.querySelectorAll('.fd-main:not(:disabled), .fd-jump') || [])];
}

function onKey(e) {
  const input = $(`${ID}-q`);
  if (F.launch) return;
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    e.preventDefault();
    $(`${ID}-foot`)?.querySelector('.btn-primary:not(:disabled)')?.click();
    return;
  }
  if (e.target === input) {
    if (e.key === 'Enter') {
      e.preventDefault();
      const q = input.value.trim();
      if (q && looksLikePath(q)) go(q, { focusSearch: true });
      else rowButtons().find((b) => b.classList.contains('fd-main'))?.click();
      return;
    }
    if (e.key === 'Backspace' && !input.value && F.data?.parent) {
      e.preventDefault();
      go(F.data.parent, { focusSearch: true });
      return;
    }
  } else if (e.key === 'Backspace' && F.data?.parent && !e.target.matches?.('input, textarea')) {
    e.preventDefault();
    go(F.data.parent);
    return;
  } else if (e.key === 'ArrowRight' && e.target.classList?.contains('fd-main')) {
    e.preventDefault();
    e.target.click();
    return;
  } else if (e.key === 'ArrowLeft' && e.target.classList?.contains('fd-main') && F.data?.parent) {
    e.preventDefault();
    go(F.data.parent);
    return;
  }
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  const list = rowButtons().filter((b) => b.classList.contains('fd-main'));
  if (!list.length) return;
  e.preventDefault();
  const i = list.indexOf(document.activeElement);
  const down = e.key === 'ArrowDown';
  const next = e.target === input ? (down ? 0 : list.length - 1) : i + (down ? 1 : -1);
  if (next < 0) input?.focus();
  else {
    const el = list[Math.min(next, list.length - 1)];
    el.focus();
    el.scrollIntoView({ block: 'nearest' });
  }
}

// ─── New chat beside a running one ────────────────────────────────────────

/** New chat while a reply runs: start it in another Jarvis in this folder and switch there. */
export async function newChatBeside() {
  const path = store.session.cwd || '';
  const display = store.session.cwd_display || path;
  if (F.launch && F.launch.status !== 'failed') {
    openFolders(); // one already starting: show it
    return { ok: true, beside: true };
  }
  stopFollowing();
  F.launch = { id: '', name: splitDisplay(display).name, display, path, stage: 'spawn', status: 'starting', beside: true };
  openModal(ID, { onClose });
  setLaunching(true);
  setView(ID, { title: 'New chat', sub: display, back: () => closeModal(ID), backLabel: 'chat' });
  renderLaunch();
  let res;
  try {
    res = await openProjectAt(path, false);
  } catch (err) {
    res = { ok: false, error: err?.status === 401 ? 'This link has expired.' : 'Jarvis is not reachable right now.' };
  }
  if (!F.launch?.beside || F.launch.id) return { ok: true, beside: true }; // superseded
  if (!res?.ok || !res.launch) {
    F.launch = { ...F.launch, status: 'failed', error: res?.error || 'Could not start another Jarvis here.' };
    if (isModalOpen(ID)) renderLaunch();
    else showToast(`Couldn’t start a new chat: ${F.launch.error}`, true);
    return { ok: false, beside: true };
  }
  F.launch = { ...res.launch, beside: true };
  if (isModalOpen(ID)) renderLaunch();
  F.launchTimer = setTimeout(() => followLaunch(0), POLL_MS);
  return { ok: true, beside: true };
}

/** Fallback when no second Jarvis can start: the old New chat (stops the running reply). */
async function replaceChat() {
  stopFollowing();
  F.launch = null;
  setLaunching(false);
  setView(ID, null);
  closeModal(ID);
  const res = await runAction('session_new', {});
  if (res.ok) showToast(res.stopped ? `New chat · ${res.stopped}` : 'New chat started');
}

// ─── Open / init ──────────────────────────────────────────────────────────

/** Open the picker at `path` (default: the folder this chat works in). */
export function openFolders({ path = '' } = {}) {
  const start = path || store.session.cwd || '';
  if (F.launch?.status === 'failed') F.launch = null; // seen already: start from the folders again
  if (F.launch) {
    // A launch is still running: show it rather than starting over.
    openModal(ID, { onClose: onClose });
    setLaunching(true);
    setView(ID, { title: `Opening ${F.launch.name}`, sub: F.launch.display, back: leaveLaunch, backLabel: 'folders' });
    renderLaunch();
    return;
  }
  F.query = '';
  F.error = null;
  if (!$(`${ID}-q`)) renderBar();
  const input = $(`${ID}-q`);
  if (input) input.value = '';
  if (!samePath(F.path, start) || !F.data) {
    F.data = null;
    renderCrumbs();
    renderBody();
    renderFoot();
  } else {
    renderBody();
    renderFoot();
  }
  openModal(ID, { onClose, focus: window.matchMedia('(min-width: 961px)').matches ? `${ID}-q` : undefined });
  go(start);
  loadStart();
}

function onClose() {
  // A launch keeps going: followLaunch toasts when it's ready.
  F.acting = '';
}

export function initFolders() {
  const card = $(ID);
  card?.addEventListener('click', onClick);
  card?.addEventListener('input', onInput);
  card?.addEventListener('keydown', onKey);
  $('cwd-chip')?.addEventListener('click', () => openFolders());
  $('new-chat-folder')?.addEventListener('click', () => {
    document.body.classList.remove('side-open');
    openFolders();
  });
  subscribe(renderChip);
  renderChip(store);
  // The project on screen changed folder (moved, or another project): the footer's choices follow.
  let footSig = '';
  subscribe((s) => {
    const sig = `${s.session.cwd}|${currentProjectId()}`;
    if (sig === footSig) return;
    footSig = sig;
    if (isModalOpen(ID) && !F.launch && F.data) renderFoot();
  });
}
