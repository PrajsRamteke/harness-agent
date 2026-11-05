/** "Linked: api, infra" chip above the composer.
 *
 * Shows which other workspace projects this chat may read and edit (the project's `links`,
 * storage/workspace.py). Hidden when nothing is linked. Clicking it asks the sidebar to open
 * its Links dialog:
 *
 *   window.dispatchEvent(new CustomEvent('jarvis:open-links', { detail: { projectId } }))
 *
 * `projectId` is the current project's workspace id (`workspace_project` in the state), or
 * null when this folder isn't in the workspace.
 *
 * Where the data comes from: `links` / `workspace_project` of GET /api/state (state_api
 * `_cwd_fields`). The store keeps only the keys it knows, so when it doesn't carry these (it
 * doesn't, today) the chip asks /api/state itself — on start, when the folder or chat changes,
 * and on `jarvis:links-changed` / `jarvis:workspace-changed` (fire either after saving links).
 */
import { $, escapeHtml } from './utils.js';
import { icon } from './icons.js';
import { store, subscribe } from './store.js';
import { fetchState } from './api.js';
import { loadWorkspaceInfo, projectName } from './projinfo.js';

const SLOT_ID = 'link-chip';
const MAX_NAMES = 3;

let slot = null;
let links = [];
let projectId = null;
let sig = '';
let timer = 0;
let seq = 0;

const cleanIds = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === 'string' && x) : []);
const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

function label() {
  const names = links.map(projectName);
  const shown = names.slice(0, MAX_NAMES).join(', ');
  return `Linked: ${shown}${names.length > MAX_NAMES ? ` +${names.length - MAX_NAMES}` : ''}`;
}

function render() {
  if (!slot) return;
  if (!links.length) {
    slot.innerHTML = '';
    return;
  }
  let chip = slot.firstElementChild;
  if (!chip) {
    chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'dock-chip is-links';
    chip.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('jarvis:open-links', { detail: { projectId } }));
    });
    slot.appendChild(chip);
  }
  const text = label();
  const full = `Linked projects: ${links.map(projectName).join(', ')} — click to manage`;
  chip.title = full;
  chip.setAttribute('aria-label', full);
  chip.innerHTML = `${icon('link')}<span class="dock-chip-text">${escapeHtml(text)}</span>`;
}

function apply(nextLinks, nextProject) {
  const l = cleanIds(nextLinks);
  const p = typeof nextProject === 'string' && nextProject ? nextProject : null;
  if (same(l, links) && p === projectId) return;
  links = l;
  projectId = p;
  render();
  if (links.length) loadWorkspaceInfo();
}

async function refresh() {
  const mine = ++seq;
  try {
    const st = await fetchState();
    if (mine !== seq || !st) return;
    apply(st.links, st.workspace_project);
  } catch {
    /* keep what is shown; the next state change tries again */
  }
}

function schedule(delay = 250) {
  clearTimeout(timer);
  timer = setTimeout(refresh, delay);
}

export function initLinkChip() {
  const dock = $('dock-chips');
  if (!dock || $(SLOT_ID)) return;
  slot = document.createElement('div');
  slot.className = 'chip-slot';
  slot.id = SLOT_ID;
  dock.appendChild(slot);

  subscribe((s) => {
    const sess = s.session || store.session;
    // If the store ever carries the fields, use them without a request.
    if (Array.isArray(sess.links)) {
      apply(sess.links, sess.workspace_project);
      return;
    }
    const next = `${sess.cwd || ''}|${sess.session_id || ''}`;
    if (next === sig) return;
    sig = next;
    schedule();
  });
  window.addEventListener('jarvis:links-changed', () => schedule(0));
  window.addEventListener('jarvis:workspace-changed', () => schedule(0));
  // Names / colours arrive after the ids do.
  window.addEventListener('jarvis:workspace-info', render);

  schedule(0);
}
