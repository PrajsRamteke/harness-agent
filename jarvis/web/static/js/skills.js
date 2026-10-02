/** Skills — the web /skill: see what's installed, add more from a link.
 *
 * "Add a skill" takes a GitHub repo or folder link, `owner/repo`, a SKILL.md
 * link, an archive or a folder path. Jarvis reads it first (`/api/skills/inspect`)
 * and lists what it found, so a repo with twenty skills becomes a checklist
 * rather than an all-or-nothing install. Project / Global picks where they go.
 * Server side: jarvis/web/extensions_api.py → jarvis/storage/skill_install.py.
 */
import { $, escapeHtml, showToast, haptic, debounce, originBadge } from './utils.js';
import { icon } from './icons.js';
import { loadSnapshot } from './store.js';
import { openModal, closeModal, isModalOpen } from './modal.js';
import { fetchSkills, fetchSkill, extPost, pickerAction } from './api.js';
import { renderMarkdown, applyMarkdownLinks } from './markdown.js';
import { btn, rowBtn, moreBtn, seg, mark, patch, autosize, msg, spin, plural, tildify, openMenu, closeMenu } from './extui.js';
import { setView, setHomeSub, toolbar, section, footer, empty, arrowRows } from './dialog.js';

const SCOPES = [
  { value: 'project', label: 'This project', title: 'Only in this folder (.harness/skills)' },
  { value: 'global', label: 'Global', title: 'Every project on this computer' },
];
const FEATURED = [
  { text: 'anthropics/skills', label: 'Anthropic skills', desc: 'PDF, Word, Excel, PowerPoint, design and more' },
];

let data = null; // last /api/skills
let loadError = false;
let built = false;
let view = null; // { name, content?, loading?, error? } — the SKILL.md reader (a sub-view)
let mode = 'list'; // 'list' | 'add' — "Add a skill" is a sub-view too
let query = '';
let barBuilt = false;
let previewSeq = 0;
const busy = {};
const notes = {};
const selected = new Set();

const add = {
  text: '',
  scope: 'global',
  scopeTouched: false,
  preview: null, // { loading } | { ok, label, skills } | { error }
  installing: false,
  result: null, // { ok, installed, skipped, error, scope_note }
};

const kb = (n) => (n < 1024 ? `${n} B` : n < 1_048_576 ? `${Math.round(n / 1024)} KB` : `${(n / 1_048_576).toFixed(1)} MB`);

// ─── Markup ───────────────────────────────────────────────────────────────

function chipsHtml() {
  return `<div class="ex-chips" role="group" aria-label="Where to find skills">${FEATURED.map((c) => `
    <button type="button" class="pv-chip ex-chip" data-act="chip" data-text="${escapeHtml(c.text)}" title="${escapeHtml(c.desc)}">${mark(c.label, 'clay', { sm: true })}<span>${escapeHtml(c.label)}</span></button>`).join('')}</div>`;
}

function skillCheck(s) {
  const dis = !s.usable;
  const on = selected.has(s.name);
  return `<label class="ex-sk${dis ? ' is-off' : ''}${on ? ' is-on' : ''}">
    <input type="checkbox" class="ex-check" data-act="pick" data-name="${escapeHtml(s.name)}"${on ? ' checked' : ''}${dis ? ' disabled' : ''} aria-label="Install ${escapeHtml(s.name)}">
    <span class="ex-checkbox" aria-hidden="true">${icon('check')}</span>
    <span class="ex-sk-text">
      <span class="ex-sk-top"><strong class="ex-name">${escapeHtml(s.name)}</strong>
        ${s.installed ? `<span class="badge" title="Already installed">Installed · ${escapeHtml(s.installed)}</span>` : ''}</span>
      <span class="ex-sk-desc">${escapeHtml(s.description || 'No description')}</span>
      ${(s.problems || []).length ? `<span class="ex-sk-warn">${icon('circle-alert')}<span>${escapeHtml(s.problems.join('; '))}</span></span>` : ''}
    </span>
    <span class="ex-sk-meta">${plural(s.files, 'file')} · ${kb(s.size)}</span>
  </label>`;
}

function resultHtml(r) {
  if (!r.ok) return msg(r.error || 'Nothing was installed.', 'error');
  const names = (r.installed || []).map((i) => i.name).join(', ');
  const where = r.scope === 'project' ? 'this project' : 'global';
  const skipped = (r.skipped || []).map((s) => `<li><strong>${escapeHtml(s.name)}</strong> — ${escapeHtml(s.reason)}</li>`).join('');
  return `${msg(`Installed ${names} (${where}). Jarvis loads ${(r.installed || []).length === 1 ? 'it' : 'them'} when a task matches.`, 'ok')}
    ${r.scope_note ? `<p class="pv-hint">${escapeHtml(r.scope_note)}</p>` : ''}
    ${skipped ? `<ul class="ex-hints">${skipped}</ul>` : ''}
    <div class="pv-actions"><button type="button" class="btn btn-sm" data-act="done">${icon('check')}<span>Done</span></button><button type="button" class="btn btn-quiet btn-sm" data-act="dismiss-result">Add another</button></div>`;
}

function previewHtml() {
  if (add.result) return resultHtml(add.result);
  const p = add.preview;
  if (!add.text.trim()) {
    return '<p class="pv-hint ex-idle">Skills are instructions Jarvis follows — only add ones you trust.</p>';
  }
  if (!p || p.loading) return `<p class="pv-hint ex-loading">${spin('Reading it… a big repository can take a few seconds')}</p>`;
  if (p.error) return msg(p.error, 'error');
  const usable = p.skills.filter((s) => s.usable);
  const all = usable.length > 0 && usable.every((s) => selected.has(s.name));
  return `<div class="ex-sk-head">
      <span class="ex-sk-title"><strong>${escapeHtml(tildify(p.label))}</strong> · ${plural(p.skills.length, 'skill')}</span>
      ${usable.length > 1 ? `<button type="button" class="link-btn" data-act="select-all">${all ? 'Select none' : 'Select all'}</button>` : ''}
    </div>
    <div class="ex-sk-list">${p.skills.map(skillCheck).join('')}</div>`;
}

function installBtnHtml() {
  const n = selected.size;
  const again = (add.preview?.skills || []).some((s) => selected.has(s.name) && s.installed);
  const label = add.installing ? 'Installing…' : n ? `${again ? 'Install / update' : 'Install'} ${plural(n, 'skill')}` : 'Install';
  return `<button type="button" class="btn btn-primary ex-add-btn" data-act="install"${n && !add.installing && !add.result ? '' : ' disabled'}>${add.installing ? spin(label) : `${icon('download')}<span>${label}</span>`}</button>`;
}

// Where it comes from: "Project" for this folder's skills, then the tool (Claude Code, Cursor …).
function skillBadges(s) {
  const where = s.scope === 'project' ? 'Project' : 'Global';
  return `${s.scope === 'project' ? '<span class="badge">Project</span>' : ''}${originBadge(s.tool_label, s.also_labels, `${where} · ${tildify(s.source_dir || '')}`)}`;
}

function skillRow(s, i) {
  const b = busy[s.name];
  const why = s.active ? '' : ' — Jarvis can’t see it while global skills are hidden';
  return `<div class="ex-skill${s.active ? '' : ' is-inactive'}${b ? ' is-busy' : ''}" data-name="${escapeHtml(s.name)}" style="--i:${i}">
    <button type="button" class="ex-skill-main" data-act="view" data-name="${escapeHtml(s.name)}" aria-label="Read ${escapeHtml(s.name)}${escapeHtml(why)}">
      ${mark(s.name, s.scope === 'project' ? 'accent' : 'indigo')}
      <span class="pv-text">
        <span class="pv-title"><span class="ex-name">${escapeHtml(s.name)}</span>${skillBadges(s)}${s.active ? '' : '<span class="badge is-warn" title="Global skills are switched off">Hidden</span>'}</span>
        <span class="ex-skill-desc">${escapeHtml(s.description || '')}</span>
        ${s.origin ? `<span class="ex-skill-from" title="${escapeHtml(s.origin)}">from ${escapeHtml(tildify(s.origin))}</span>` : ''}
        ${notes[s.name]?.text ? `<span class="ex-skill-note${notes[s.name].error ? ' is-error' : ''}">${escapeHtml(notes[s.name].text)}</span>` : ''}
      </span>
    </button>
    <div class="pv-side">${b ? `<span class="pv-side-busy">${spin('')}</span>` : rowBtn('view', 'View', { data: { name: s.name }, disabled: !s.active, title: s.active ? '' : 'Hidden while global skills are off' })}${moreBtn(s.name)}</div>
  </div>`;
}

function matches(s, q) {
  const hay = `${s.name} ${s.description || ''} ${s.tool_label || ''} ${s.scope || ''}`.toLowerCase();
  return q.split(/\s+/).every((w) => hay.includes(w));
}

function listHtml() {
  const skills = data?.skills || [];
  if (!skills.length) {
    const featured = FEATURED[0];
    return empty('No skills yet', 'Skills teach Jarvis a repeatable job — review a PR, fill a PDF, write in your voice.', {
      ic: 'book-open',
      action: `${btn('add', 'Add a skill', { cls: 'btn-primary', ic: 'plus' })}
        <button type="button" class="btn" data-act="chip" data-text="${escapeHtml(featured.text)}" title="${escapeHtml(featured.desc)}">${icon('sparkles')}<span>Browse ${escapeHtml(featured.label)}</span></button>`,
    });
  }
  const q = query.trim().toLowerCase();
  const shown = q ? skills.filter((s) => matches(s, q)) : skills;
  if (!shown.length) return empty('No skills match', `Nothing installed matches “${escapeHtml(query.trim())}”.`, { ic: 'search', action: btn('add', 'Add a skill', { ic: 'plus' }) });
  return shown.map(skillRow).join('');
}

// ─── Render ───────────────────────────────────────────────────────────────

function buildList(body) {
  body.innerHTML = `
    <div id="skills-notice"></div>
    <section class="ex-list-sec" aria-label="Installed skills">
      <div id="skills-count"></div>
      <div class="ex-list" id="skills-list"></div>
    </section>`;
  built = 'list';
}

function buildAdd(body) {
  body.innerHTML = `
    <section class="ex-add is-flat" aria-label="Add a skill">
      <p class="dlg-lead">Paste a GitHub repo or folder link, <code>owner/repo</code>, a SKILL.md link, an archive or a folder path. Jarvis reads it first and lists what it found.</p>
      <div class="ex-src">
        <span class="pv-field-ic">${icon('link')}</span>
        <textarea id="skills-src" class="ex-src-input" rows="1" placeholder="Paste a GitHub link, owner/repo, or a SKILL.md link"
          aria-label="Skill to add" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
          data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other"></textarea>
      </div>
      <div id="skills-chips"></div>
      <div class="ex-preview" id="skills-preview" aria-live="polite"></div>
    </section>`;
  const ta = $('skills-src');
  ta.value = add.text;
  autosize(ta);
  built = 'add';
}

function renderPreview() {
  patch($('skills-preview'), previewHtml());
  if (mode === 'add' && !view) renderFoot();
}

function renderAddPanel() {
  patch($('skills-chips'), chipsHtml());
  renderPreview();
}

function renderList() {
  const skills = data.skills || [];
  const hidden = data.hidden_global_count || 0;
  const project = skills.filter((s) => s.scope === 'project').length;
  setHomeSub('skills', skills.length ? `${plural(skills.length, 'skill')} · ${project} in this project` : 'Jarvis loads a skill when a task matches it');
  const q = query.trim().toLowerCase();
  const shown = q ? skills.filter((s) => matches(s, q)).length : skills.length;
  patch($('skills-count'), skills.length ? section(q ? 'Matching' : 'Installed', { count: q ? `${shown} of ${skills.length}` : skills.length }) : '');
  patch($('skills-notice'), hidden
    ? `<p class="pv-msg is-warn" role="status">${icon('circle-alert')}<span>${plural(hidden, 'global skill')} ${hidden === 1 ? 'is' : 'are'} hidden from Jarvis.</span>
        <button type="button" class="btn btn-sm" data-act="show-global">Show ${hidden === 1 ? 'it' : 'them'}</button></p>`
    : '');
  patch($('skills-list'), listHtml());
}

function renderDetail() {
  const body = $('skills-body');
  built = 'read';
  const v = view;
  const s = data?.skills?.find((x) => x.name === v.name);
  // The header carries the name; the reader shows where it's from, then the instructions.
  const text = String(v.content || '').replace(/^---[ \t]*\n[\s\S]*?\n---[ \t]*\n?/, '').trim();
  body.innerHTML = `
    ${s ? `<div class="ex-detail-head">${skillBadges(s)}</div>` : ''}
    ${s?.description ? `<p class="ex-detail-desc">${escapeHtml(s.description)}</p>` : ''}
    ${v.loading ? `<div class="list-loading">${'<div class="skeleton"></div>'.repeat(3)}</div>` : v.error ? msg(v.error, 'error') : `<div class="md ex-md">${renderMarkdown(text)}</div>`}`;
  applyMarkdownLinks(body);
  body.scrollTop = 0;
}

/** Search + "Add skill" — only on the list (sub-views use the header's back button). */
function renderBar() {
  const bar = $('skills-bar');
  if (!bar) return;
  const onList = !view && mode === 'list' && !!data;
  bar.hidden = !onList;
  if (onList && !barBuilt) {
    bar.innerHTML = toolbar({ id: 'skills-q', placeholder: 'Search skills', value: query, action: { act: 'add', label: 'Add skill', ic: 'plus', title: 'Add skills from a link' } });
    barBuilt = true;
  }
}

function renderFoot() {
  const foot = $('skills-foot');
  if (!foot) return;
  if (view) {
    patch(foot, footer({ hints: [['esc', 'back']] }));
  } else if (mode === 'add') {
    // A form: where it goes on the left, the install button on the right.
    patch(foot, `<div class="dlg-actions">
      <div class="dlg-scope"><span class="dlg-scope-lbl">Save to</span>${seg(SCOPES, add.scope, { label: 'Where to install it', act: 'scope' })}</div>
      <span class="dlg-note" title="${escapeHtml(add.scope === 'project' ? data?.project_dir || '' : data?.global_dir || '')}"><span>${add.scope === 'project' ? 'only this folder' : 'every project'}</span></span>
      <span class="dlg-spacer"></span>${installBtnHtml()}</div>`);
  } else if (data) {
    patch(foot, footer({
      scope: seg([
        { value: 'false', label: 'This project', title: 'Only skills from this folder' },
        { value: 'true', label: 'Project + global', title: 'Also skills installed for every project' },
      ], String(!!data.global_skills), { label: 'Which skills Jarvis can use', act: 'global' }),
      hints: [['↑ ↓', 'move'], ['↵', 'read'], ['esc', 'close']],
    }));
  } else {
    patch(foot, '');
  }
}

function render() {
  const body = $('skills-body');
  if (!body) return;
  renderBar();
  renderFoot();
  if (view) {
    setView('skills', { title: view.name, sub: 'Skill', back: backToList, backLabel: 'skills' });
    renderDetail();
    return;
  }
  if (!data) {
    built = false;
    body.innerHTML = loadError
      ? empty('Could not load skills', 'Check that Jarvis is still running, then try again.', { ic: 'circle-alert', action: btn('reload', 'Try again', { ic: 'refresh-cw' }) })
      : `<div class="list-loading">${'<div class="skeleton"></div>'.repeat(5)}</div>`;
    return;
  }
  if (mode === 'add') {
    setView('skills', { title: 'Add a skill', sub: 'From a link, an archive or a folder', back: backToList, backLabel: 'skills' });
    if (built !== 'add' || !$('skills-src')) buildAdd(body);
    renderAddPanel();
    return;
  }
  setView('skills', null);
  if (built !== 'list' || !$('skills-list')) buildList(body);
  renderList();
}

function backToList() {
  view = null;
  mode = 'list';
  render();
  $('skills-q')?.focus({ preventScroll: true });
}

function openAdd(text = '') {
  mode = 'add';
  view = null;
  if (text) add.text = text;
  render();
  if (text) setSource(text);
  const ta = $('skills-src');
  ta?.focus({ preventScroll: true });
  autosize(ta);
}

export async function refreshSkills() {
  try {
    data = await fetchSkills(undefined, '');
    loadError = false;
  } catch {
    if (!data) loadError = true;
  }
  if (isModalOpen('skills')) render();
}
const refreshSoon = debounce(refreshSkills, 150);

function applyResult(res) {
  if (res?.skills) {
    data = res.skills;
    if (isModalOpen('skills')) render();
  }
}

// ─── Add ──────────────────────────────────────────────────────────────────

function setSource(text) {
  add.text = text;
  add.result = null;
  add.preview = null;
  selected.clear();
  const ta = $('skills-src');
  if (ta && ta.value !== text) {
    ta.value = text;
    autosize(ta);
  }
  renderPreview();
  schedulePreview();
}

const schedulePreview = debounce(() => runPreview(), 500);

async function runPreview() {
  const text = add.text.trim();
  const seq = ++previewSeq;
  if (!text) {
    add.preview = null;
    renderPreview();
    return;
  }
  add.preview = { loading: true };
  renderPreview();
  const res = await extPost('skills/inspect', { source: text });
  if (seq !== previewSeq) return;
  selected.clear();
  if (res.ok) {
    add.preview = { label: res.label, skills: res.skills || [] };
    const usable = add.preview.skills.filter((s) => s.usable);
    // One skill (or a link to one folder): ready to go. A whole repo: the user picks.
    if (usable.length === 1) selected.add(usable[0].name);
  } else {
    add.preview = { error: res.error || 'I couldn’t read that.' };
  }
  renderPreview();
}

async function install() {
  if (add.installing || !selected.size) return;
  add.installing = true;
  add.result = null;
  renderPreview();
  const overwrite = (add.preview?.skills || []).some((s) => selected.has(s.name) && s.installed);
  const res = await extPost('skills/install', {
    source: add.text.trim(),
    scope: add.scope,
    names: [...selected],
    overwrite,
  });
  add.installing = false;
  applyResult(res);
  add.result = res;
  if (res.ok) {
    haptic(10);
    showToast(`Installed ${(res.installed || []).map((i) => i.name).join(', ')}`);
    add.text = '';
    add.preview = null;
    selected.clear();
    const ta = $('skills-src');
    if (ta) {
      ta.value = '';
      autosize(ta);
    }
  }
  renderPreview();
}

// ─── Skill actions ────────────────────────────────────────────────────────

async function openSkill(name) {
  const s = data?.skills?.find((x) => x.name === name);
  if (s && !s.active) {
    showToast('Hidden while global skills are off', true);
    return;
  }
  view = { name, loading: true };
  render();
  try {
    const d = await fetchSkill(name);
    if (view?.name === name) view = { name, content: d.content || '' };
  } catch {
    if (view?.name === name) view = { name, error: 'Could not open that skill.' };
  }
  if (view) render();
}

async function act(name, kind, path, payload, okText) {
  busy[name] = kind;
  delete notes[name];
  render();
  const res = await extPost(path, payload);
  delete busy[name];
  applyResult(res);
  if (res.ok) {
    haptic(10);
    showToast(typeof okText === 'function' ? okText(res) : okText);
  } else {
    notes[name] = { text: res.error || 'That did not work', error: true };
    showToast(res.error || 'That did not work', true);
  }
  render();
}

async function menuFor(name, anchor) {
  const s = data?.skills?.find((x) => x.name === name);
  if (!s) return;
  const foreign = s.managed ? '' : `Lives in ${s.source_dir} — another tool’s folder`;
  const items = [
    { key: 'view', label: 'Read the skill', icon: 'book-open', disabled: !s.active, reason: s.active ? '' : 'Hidden while global skills are off' },
  ];
  if (s.origin) items.push({ key: 'update', label: 'Update from source', icon: 'refresh-cw', disabled: !s.managed, reason: foreign });
  items.push(s.scope === 'project'
    ? { key: 'to-global', label: 'Move to global', icon: 'arrow-right-left', disabled: !s.managed, reason: foreign }
    : { key: 'to-project', label: 'Move to this project', icon: 'arrow-right-left', disabled: !s.managed, reason: foreign });
  items.push({ divider: true });
  items.push({ key: 'remove', label: 'Remove', icon: 'trash-2', danger: true, confirm: true, disabled: !s.managed, reason: foreign });
  const key = await openMenu(anchor, items);
  if (!key) return;
  if (key === 'view') openSkill(name);
  else if (key === 'update') act(name, 'update', 'skills/update', { name }, `Updated ${name}`);
  else if (key === 'to-global') act(name, 'move', 'skills/move', { name, scope: 'global' }, `Moved ${name} to global`);
  else if (key === 'to-project') act(name, 'move', 'skills/move', { name, scope: 'project' }, `Moved ${name} to this project`);
  else if (key === 'remove') act(name, 'remove', 'skills/remove', { name, scope: s.scope }, `Removed ${name}`);
}

async function setGlobal(on) {
  const res = await pickerAction('skills_scope', { global_skills: on });
  if (res.state) loadSnapshot(res.state);
  if (!res.ok) showToast(res.error || 'Could not change the scope', true);
  refreshSkills();
}

// ─── Events ───────────────────────────────────────────────────────────────

function handleClick(e) {
  const el = e.target.closest('[data-act]');
  const card = $('skills');
  if (!el || !card?.contains(el)) return;
  const name = el.dataset.name || el.closest('[data-name]')?.dataset.name || '';
  switch (el.dataset.act) {
    case 'view': openSkill(name); break;
    case 'add': openAdd(); break;
    case 'done': backToList(); break;
    case 'menu': menuFor(name, el); break;
    case 'chip':
      if (mode !== 'add') openAdd(el.dataset.text);
      else { setSource(el.dataset.text); $('skills-src')?.focus(); }
      break;
    case 'scope':
      add.scope = el.dataset.val;
      add.scopeTouched = true;
      renderAddPanel();
      break;
    case 'global': {
      const on = el.dataset.val === 'true';
      if (on !== !!data?.global_skills) setGlobal(on);
      break;
    }
    case 'show-global': setGlobal(true); break;
    case 'select-all': {
      const usable = (add.preview?.skills || []).filter((s) => s.usable);
      const all = usable.every((s) => selected.has(s.name));
      selected.clear();
      if (!all) usable.forEach((s) => selected.add(s.name));
      renderPreview();
      break;
    }
    case 'install': install(); break;
    case 'dismiss-result': add.result = null; renderPreview(); break;
    case 'reload': refreshSkills(); break;
    default:
  }
}

function handleChange(e) {
  const el = e.target;
  if (el.dataset?.act !== 'pick') return;
  if (el.checked) selected.add(el.dataset.name);
  else selected.delete(el.dataset.name);
  renderPreview();
}

function handleInput(e) {
  const el = e.target;
  if (el.id === 'skills-q') {
    query = el.value;
    if (data) renderList();
    return;
  }
  if (el.id !== 'skills-src') return;
  add.text = el.value;
  add.result = null;
  add.preview = null;
  selected.clear();
  autosize(el);
  if (!add.text.trim()) previewSeq += 1;
  renderPreview();
  if (add.text.trim()) schedulePreview();
}

function handleKey(e) {
  if (!view && mode === 'list' && arrowRows(e, $('skills-q'), [...($('skills-list')?.querySelectorAll('.ex-skill-main') || [])])) return;
  if (e.key === 'Enter' && !e.isComposing && e.target.id === 'skills-q') {
    const first = $('skills-list')?.querySelector('.ex-skill-main:not([disabled])');
    if (first) { e.preventDefault(); first.click(); }
    return;
  }
  if (e.key !== 'Enter' || e.isComposing || e.shiftKey) return;
  if (e.target.id === 'skills-src') {
    e.preventDefault();
    if (add.preview?.skills) install();
    else runPreview();
  }
}

// ─── Open / close ─────────────────────────────────────────────────────────

export function openSkills() {
  view = null;
  mode = 'list';
  query = '';
  barBuilt = false;
  render();
  openModal('skills', {
    focus: $('skills-q') || undefined,
    onClose: () => {
      closeMenu();
      view = null;
      mode = 'list';
      for (const n of Object.keys(notes)) delete notes[n];
    },
  });
  refreshSkills().then(() => {
    if (isModalOpen('skills') && !view && mode === 'list' && document.activeElement?.closest?.('#skills') && !document.activeElement.matches('input, textarea')) $('skills-q')?.focus({ preventScroll: true });
  });
}

export function closeSkills() {
  closeModal('skills');
}

export function handleSkillsEvent() {
  if (isModalOpen('skills')) refreshSoon();
}

export function initSkills() {
  const card = $('skills');
  card?.addEventListener('click', handleClick);
  card?.addEventListener('change', handleChange);
  card?.addEventListener('input', handleInput);
  card?.addEventListener('keydown', handleKey);
}
