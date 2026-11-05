/** Workspace project look-ups shared by the transcript, the Changes drawer and the link chip.
 *
 * A "linked" project (storage/workspace.py) can be read or edited from another project's
 * chat. Wherever that shows up — a tool row, a diff, a change — it gets a small badge: a
 * colour dot plus the project's name, taken from GET /api/workspace (jarvis/web/workspace_api.py)
 * and falling back to the raw id while that hasn't loaded (or the id isn't in the workspace).
 *
 * Badges are plain `<span class="pbadge" data-pid>` nodes; when the workspace data arrives
 * (or changes) every badge already on the page is repainted in place.
 */
import { escapeHtml, readToken } from './utils.js';

/** `api:src/x.ts` — the project id, then the path. `://` is a URL, never a project. */
const QUALIFIED = /^([a-z0-9][a-z0-9_-]{0,31}):(?!\/\/)(?=[^\s\\])(.+)$/;
/** `api › src/x.ts` — what the server's one-line tool summary (tui/tool_format.py) leads with. */
const LEAD_ID = /^([a-z0-9][a-z0-9_-]{0,31}) › /;
/** Tools whose arguments are paths: a qualified path anywhere in the line is a project path. */
const PATH_TOOLS = /^(read_file|read_document|read_bundle|resolve_context|write_file|edit_file|multi_edit|list_dir|glob_files|search_code|rank_files|fast_find|read_image_text|read_images_text|git_status|git_diff|git_log)$/;
const MID_ID = /(^|, |\s{2}in )([a-z0-9][a-z0-9_-]{0,31}) › /g;
const STALE_MS = 10_000;

/** id → `{ id, name, color }` */
let byId = new Map();
let primaryId = null;
let loadedAt = 0;
let inflight = null;

export const isProjectId = (v) => typeof v === 'string' && /^[a-z0-9][a-z0-9_-]{0,31}$/.test(v);

/** `'api:src/x'` → `{ id: 'api', path: 'src/x' }`; a plain path → `{ id: '', path }`. */
export function splitQualified(path) {
  const m = QUALIFIED.exec(String(path || '').trim());
  return m ? { id: m[1], path: m[2] } : { id: '', path: String(path || '') };
}

/**
 * Read the projects a tool row's headline names.
 * Returns `{ ids, rest }`: the project ids (in order, no repeats) and the headline with the
 * `api › ` prefixes taken off (the badge says it instead). `[api, infra] …` and
 * `[all projects] …` lead forms (a `project` argument) are understood too.
 */
export function parseToolArgs(name, args) {
  const text = String(args || '');
  if (!text) return { ids: [], rest: text };
  const ids = [];
  const add = (id) => { if (id && !ids.includes(id)) ids.push(id); };
  let rest = text;
  const list = /^\[([^\]]+)\] /.exec(rest);
  if (list) {
    const parts = list[1].split(',').map((s) => s.trim()).filter(Boolean);
    if (parts.length === 1 && parts[0] === 'all projects') {
      add('*');
      rest = rest.slice(list[0].length);
    } else if (parts.length && parts.every(isProjectId)) {
      parts.forEach(add);
      rest = rest.slice(list[0].length);
    }
  }
  const lead = LEAD_ID.exec(rest);
  if (lead) {
    add(lead[1]);
    rest = rest.slice(lead[0].length);
  } else {
    const q = QUALIFIED.exec(rest);
    if (q && PATH_TOOLS.test(String(name || ''))) {
      add(q[1]);
      rest = q[2];
    }
  }
  if (ids.length && PATH_TOOLS.test(String(name || ''))) {
    // `api › a, web › b` — the rest of a multi-path line.
    rest = rest.replace(MID_ID, (_m, pre, id) => { add(id); return pre; });
  }
  return { ids, rest };
}

// ─── Workspace data ───────────────────────────────────────────────────────

export function projectInfo(id) {
  return byId.get(id) || null;
}

/** The project this page shows (`null` until the workspace has loaded, or when it's ad hoc). */
export const primaryProjectId = () => primaryId;

export function projectName(id) {
  if (id === '*') return 'all projects';
  return byId.get(id)?.name || id;
}

function safeColor(c) {
  return typeof c === 'string' && /^(#[0-9a-f]{3,8}|[a-z]+|var\(--[a-z0-9-]+\)|(rgb|hsl)a?\([0-9 ,.%/]+\))$/i.test(c) ? c : '';
}

/** Repaint every badge already on the page from the current data. */
function repaint() {
  for (const el of document.querySelectorAll('.pbadge[data-pid]')) paintBadge(el, el.dataset.pid);
  for (const el of document.querySelectorAll('[data-pdot]')) {
    const color = safeColor(byId.get(el.dataset.pdot)?.color);
    if (color) el.style.setProperty('--pc', color);
  }
  for (const el of document.querySelectorAll('[data-pname]')) el.textContent = projectName(el.dataset.pname);
  window.dispatchEvent(new CustomEvent('jarvis:workspace-info'));
}

function paintBadge(el, id) {
  const color = safeColor(byId.get(id)?.color);
  if (color) el.style.setProperty('--pc', color);
  const label = el.querySelector('b');
  if (label) label.textContent = projectName(id);
  el.title = id === '*' ? 'Looked at every linked project' : `Project: ${projectName(id)}`;
}

/** Fetch `/api/workspace` (cached for a few seconds); never throws. */
export function loadWorkspaceInfo({ force = false } = {}) {
  if (inflight) return inflight;
  if (!force && loadedAt && Date.now() - loadedAt < STALE_MS) return Promise.resolve();
  inflight = (async () => {
    try {
      const token = readToken();
      const res = await fetch('/api/workspace', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        cache: 'no-store',
      });
      if (!res.ok) return;
      const data = await res.json();
      if (!data || !Array.isArray(data.projects)) return;
      byId = new Map(data.projects.map((p) => [p.id, { id: p.id, name: p.name || p.id, color: p.color || '' }]));
      primaryId = data.current || null;
      loadedAt = Date.now();
      repaint();
    } catch {
      /* offline / older server: ids stay as the label */
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

/** Note that `id` was seen: fetch the workspace if we don't know it yet. */
function ensureKnown(id) {
  if (id && id !== '*' && !byId.has(id)) loadWorkspaceInfo();
}

// ─── Markup ───────────────────────────────────────────────────────────────

/** `<span class="pbadge">` — colour dot + name. */
export function badgeHtml(id) {
  ensureKnown(id);
  const color = safeColor(byId.get(id)?.color);
  const style = color ? ` style="--pc:${escapeHtml(color)}"` : '';
  return `<span class="pbadge" data-pid="${escapeHtml(id)}"${style} title="${escapeHtml(id === '*' ? 'Looked at every linked project' : `Project: ${projectName(id)}`)}"><i aria-hidden="true"></i><b>${escapeHtml(projectName(id))}</b></span>`;
}

/** Badges for several ids ('' when none). */
export function badgesHtml(ids) {
  return ids.length ? `<span class="pbadges">${ids.map(badgeHtml).join('')}</span>` : '';
}

/** Hand the page's text a plain "[name] " for a title attribute / aria label. */
export function badgeText(ids) {
  return ids.length ? `[${ids.map(projectName).join(', ')}] ` : '';
}

/** Start from a snapshot that is already loaded elsewhere (tests / other modules). */
export function seedWorkspaceInfo(projects, current = null) {
  byId = new Map((projects || []).map((p) => [p.id, { id: p.id, name: p.name || p.id, color: p.color || '' }]));
  primaryId = current;
  loadedAt = Date.now();
  repaint();
}

// Re-read when the sidebar changes the workspace (it may fire this; harmless when nothing does).
window.addEventListener('jarvis:workspace-changed', () => loadWorkspaceInfo({ force: true }));
