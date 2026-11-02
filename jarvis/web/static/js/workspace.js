/** Workspace — "Stop all" before you leave, "Restore" when you come back.
 *
 *   PROJECTS            [⏻] [+ Open folder]      ← ⏻ opens the confirm dialog (also Ctrl+K → "Stop all")
 *   ┌ Restore your workspace ── 5 projects · Yesterday   [Restore] [×] ┐   ← only when one was saved
 *
 * Stop all ends every Jarvis that was opened from the web (the one serving this
 * page last) and closes the web terminals. Before it does, the server writes down
 * which folders and chats were open (launcher.stop_all → workspace_snapshot.json);
 * after you start Jarvis again, Restore opens each of them with its chat in place
 * (launcher.restore_workspace). Chats themselves are always saved — what comes
 * back is the set of projects, not unsent text or a reply that was mid-way.
 *
 * Closing the browser tab can't show our own dialog; the browser's generic
 * "Leave site?" prompt is raised while projects are running, so closing never
 * leaves things running by accident.
 */
import { $, escapeHtml, showToast } from './utils.js';
import { icon } from './icons.js';
import { openModal, closeModal } from './modal.js';
import { fetchProjects, stopAllProjects, fetchSavedWorkspace, restoreWorkspace, forgetSavedWorkspace } from './api.js';
import { allProjects, hostProjectId, currentProjectId, switchProject, refreshProjects } from './projects.js';
import { relTime } from './sessions.js';

const ID = 'stopall';
const RESTORE_POLL_MS = 1000;
const RESTORE_POLLS = 20;
const AFTER_STOP_MS = 1500;       // a stopping server needs a moment before it stops answering

let everythingStopped = false;   // the page said goodbye: nothing left to warn about
let saved = null;                // the snapshot the server holds, if any

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

// ─── Stop all ─────────────────────────────────────────────────────────────

const stateIcon = (p) => (p.needs_approval ? icon('shield') : '');

function badge(p) {
  if (p.needs_approval) return '<span class="sa-pill is-ask">Needs approval</span>';
  if (p.busy) return '<span class="sa-pill is-busy">Working</span>';
  return '';
}

function rowHtml(p, { stays = false } = {}) {
  const cls = p.needs_approval ? 'is-ask' : p.busy ? 'is-busy' : 'is-idle';
  const sub = stays ? 'Runs in a terminal \u2014 quit it there (Ctrl+C twice)' : (p.session_title || p.model || 'New chat');
  return `<div class="sa-row ${cls}">
    <span class="sa-ind" aria-hidden="true">${stateIcon(p)}</span>
    <span class="sa-name"><strong>${escapeHtml(p.project || 'project')}</strong><span>${escapeHtml(sub)}</span></span>
    ${stays ? '' : badge(p)}
  </div>`;
}

function sectionHtml(label, rows, opts) {
  if (!rows.length) return '';
  return `<section class="sa-sec"><h3 class="sa-label">${label}<span>${rows.length}</span></h3>${rows.map((p) => rowHtml(p, opts)).join('')}</section>`;
}

function setDialog({ title, sub, body, foot }) {
  $('stopall-title').textContent = title;
  $('stopall-sub').textContent = sub;
  $('stopall-body').innerHTML = body;
  $('stopall-foot').innerHTML = foot;
}

const CANCEL = '<button class="btn btn-quiet" type="button" id="stopall-cancel" data-close="stopall">Cancel</button>';

export function openStopAll() {
  const rows = allProjects();
  if (!rows.length) {
    showToast('Nothing is running');
    return;
  }
  const web = rows.filter((p) => p.headless);
  const stays = rows.filter((p) => !p.headless);
  const working = web.filter((p) => p.busy || p.needs_approval).length;
  const warn = working
    ? `<div class="sa-warn">${icon('triangle-alert')}<span>${working === 1 ? 'One project is' : `${working} projects are`} still working \u2014 the reply in progress will be lost.</span></div>`
    : '';
  setDialog({
    title: web.length ? 'Stop everything?' : 'Nothing to stop here',
    sub: web.length ? `${plural(web.length, 'project')} and your web terminals close \u00b7 chats stay saved` : 'Only terminal projects are running',
    body: `${warn}${sectionHtml('Will stop', web)}${sectionHtml('Stays running', stays, { stays: true })}`,
    foot: `<span class="sa-hint">${icon('rotate-ccw')}<span>Restore reopens them next time</span></span>
      ${CANCEL}${web.length ? `<button class="btn btn-danger sa-go" type="button" id="stopall-go">${icon('power')}<span>Stop all</span></button>` : ''}`,
  });
  // Cancel first: Enter must never end a running reply by accident.
  openModal(ID, { focus: 'stopall-cancel' });
}

function showDone(res) {
  const n = res.stopped.length;
  setDialog({
    title: res.self ? 'All stopped' : 'Stopped',
    sub: res.self ? 'You can close this tab now' : `${plural(n, 'project')} stopped`,
    body: `<div class="sa-done"><span class="sa-done-ic">${icon('circle-check')}</span>
      <p>${plural(n, 'project')} stopped and saved${res.skipped.length ? ` · ${plural(res.skipped.length, 'terminal project')} left running` : ''}.</p>
      <p class="sa-note">${res.self
        ? 'When you come back, start Jarvis and press <strong>Restore</strong> — every project reopens with its chat.'
        : 'Press <strong>Restore</strong> in the sidebar whenever you want them back.'}</p></div>`,
    foot: res.self ? '' : `<span class="dlg-spacer"></span><button class="btn" type="button" data-close="stopall">Done</button>`,
  });
}

function showFailed(res) {
  setDialog({
    title: 'Some projects didn’t stop',
    sub: 'The rest are stopped and saved',
    body: res.failed.map((f) => `<div class="sa-row is-ask"><span class="sa-ic" aria-hidden="true">${icon('triangle-alert')}</span>
      <span class="sa-name"><strong>${escapeHtml(f.project || 'project')}</strong><span>${escapeHtml(f.error || '')}</span></span></div>`).join(''),
    foot: `<span class="dlg-spacer"></span><button class="btn" type="button" data-close="stopall">Close</button>`,
  });
}

async function stillAnswers() {
  await new Promise((r) => setTimeout(r, AFTER_STOP_MS));
  try {
    await fetchProjects();
    return true;
  } catch {
    return false;
  }
}

async function stopAll() {
  const go = $('stopall-go');
  if (go) {
    go.disabled = true;
    go.innerHTML = '<span class="spinner" aria-hidden="true"></span><span>Stopping…</span>';
  }
  // Leave a stopping project first, so the page never points at one that is gone.
  if (currentProjectId() !== hostProjectId()) await switchProject(hostProjectId());
  let res;
  try {
    res = await stopAllProjects();
  } catch (err) {
    // No answer at all: either the serving Jarvis went away mid-reply (the expected end
    // of a full stop) or it was never reached — ask once more to tell them apart.
    if (err?.status === undefined && !(await stillAnswers())) {
      everythingStopped = true;
      showDone({ stopped: allProjects().filter((p) => p.headless), skipped: [], self: true });
      return;
    }
    showToast(err?.status === 401 ? 'This link has expired.' : 'Jarvis is not reachable right now.', true);
    closeModal(ID);
    return;
  }
  if (res.self) everythingStopped = true;
  if (res.failed?.length) showFailed(res);
  else showDone(res);
  refreshProjects();
  loadSaved();
}

// ─── Restore ──────────────────────────────────────────────────────────────

function renderBar() {
  const bar = $('restore-bar');
  if (!bar) return;
  const n = saved?.projects?.length || 0;
  bar.hidden = !n;
  if (!n) return;
  $('restore-text').innerHTML = `<strong>Restore your workspace</strong><span>${plural(n, 'project')} · ${escapeHtml(relTime(saved.saved_at))}</span>`;
  bar.title = saved.projects.map((p) => `${p.project}${p.title ? ` — ${p.title}` : ''}`).join('\n');
}

export async function loadSaved() {
  try {
    saved = (await fetchSavedWorkspace())?.saved || null;
  } catch {
    return; // an older Jarvis without it, or a blip: keep what is shown
  }
  renderBar();
}

async function restore() {
  const btn = $('restore-go');
  btn.disabled = true;
  let res;
  try {
    res = await restoreWorkspace();
  } catch {
    showToast('Jarvis is not reachable right now.', true);
    btn.disabled = false;
    return;
  }
  const n = res.restored?.length || 0;
  if (n) showToast(`Restoring ${plural(n, 'project')}…`);
  if (res.failed?.length) showToast(`${res.failed[0].project || 'A project'}: ${res.failed[0].error}`, true);
  btn.disabled = false;
  await loadSaved();
  // Starting takes a few seconds each: follow the list until they have all appeared.
  for (let i = 0; i < RESTORE_POLLS; i += 1) {
    await new Promise((r) => setTimeout(r, RESTORE_POLL_MS));
    await refreshProjects();
  }
}

async function dismiss() {
  try { await forgetSavedWorkspace(); } catch { /* shown again next time */ }
  saved = null;
  renderBar();
}

// ─── Leaving the page ─────────────────────────────────────────────────────

function guardTabClose(e) {
  if (everythingStopped) return;
  if (!allProjects().some((p) => p.headless || p.busy || p.needs_approval)) return;
  e.preventDefault();
  e.returnValue = ''; // the browser words the question itself
}

export function initWorkspace() {
  $('stop-all')?.addEventListener('click', () => {
    document.body.classList.remove('side-open');
    openStopAll();
  });
  $('stopall-foot')?.addEventListener('click', (e) => {
    if (e.target.closest('#stopall-go')) stopAll();
  });
  $('restore-go')?.addEventListener('click', restore);
  $('restore-dismiss')?.addEventListener('click', dismiss);
  window.addEventListener('beforeunload', guardTabClose);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) loadSaved(); });
  loadSaved();
}
