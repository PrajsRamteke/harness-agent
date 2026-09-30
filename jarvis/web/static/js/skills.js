/** Skills — the web /skill: see what's installed, add more from a link.
 *
 * "Add a skill" takes a GitHub repo or folder link, `owner/repo`, a SKILL.md
 * link, an archive or a folder path. Jarvis reads it first (`/api/skills/inspect`)
 * and lists what it found, so a repo with twenty skills becomes a checklist
 * rather than an all-or-nothing install. Project / Global picks where they go.
 * Server side: jarvis/web/extensions_api.py → jarvis/storage/skill_install.py.
 */
import { $, escapeHtml, showToast, haptic, debounce } from './utils.js';
import { icon } from './icons.js';
import { loadSnapshot } from './store.js';
import { openModal, closeModal, isModalOpen } from './modal.js';
import { fetchSkills, fetchSkill, extPost, pickerAction } from './api.js';
import { renderMarkdown, applyMarkdownLinks } from './markdown.js';
import { btn, rowBtn, moreBtn, seg, mark, patch, autosize, msg, spin, plural, tildify, openMenu, closeMenu } from './extui.js';

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
let view = null; // { name, content?, loading?, error? } — the SKILL.md reader
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

function pathHint() {
  if (!data) return '';
  return add.scope === 'project'
    ? `Installed into <code>${escapeHtml(tildify(data.project_dir) || '.harness/skills')}</code> — only in this folder.`
    : `Installed into <code>${escapeHtml(tildify(data.global_dir) || '~/.harness/skills')}</code> — every project.`;
}

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
    <div class="pv-actions"><button type="button" class="btn btn-quiet btn-sm" data-act="dismiss-result">Done</button></div>`;
}

function previewHtml() {
  if (add.result) return resultHtml(add.result);
  const p = add.preview;
  if (!add.text.trim()) {
    return '<p class="pv-hint ex-idle">Paste a GitHub link (a repo or one folder), <code>owner/repo</code>, a SKILL.md link, a .zip, or a folder path. Skills are instructions Jarvis follows — only add ones you trust.</p>';
  }
  if (!p || p.loading) return `<p class="pv-hint ex-loading">${spin('Reading it… a big repository can take a few seconds')}</p>`;
  if (p.error) return msg(p.error, 'error');
  const usable = p.skills.filter((s) => s.usable);
  const all = usable.length > 0 && usable.every((s) => selected.has(s.name));
  return `<div class="ex-sk-head">
      <span><strong>${escapeHtml(p.label)}</strong> · ${plural(p.skills.length, 'skill')}</span>
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

function skillRow(s, i) {
  const b = busy[s.name];
  const why = s.active ? '' : ' — Jarvis can’t see it while global skills are hidden';
  return `<div class="ex-skill${s.active ? '' : ' is-inactive'}${b ? ' is-busy' : ''}" data-name="${escapeHtml(s.name)}" style="--i:${i}">
    <button type="button" class="ex-skill-main" data-act="view" data-name="${escapeHtml(s.name)}" aria-label="Read ${escapeHtml(s.name)}${escapeHtml(why)}">
      ${mark(s.name, s.scope === 'project' ? 'accent' : 'indigo')}
      <span class="pv-text">
        <span class="pv-title"><span class="ex-name">${escapeHtml(s.name)}</span><span class="badge">${s.scope === 'project' ? 'Project' : 'Global'}</span>${s.active ? '' : '<span class="badge is-warn" title="Global skills are switched off">Hidden</span>'}</span>
        <span class="ex-skill-desc">${escapeHtml(s.description || '')}</span>
        ${s.origin ? `<span class="ex-skill-from">from ${escapeHtml(s.origin)}</span>` : ''}
        ${notes[s.name]?.text ? `<span class="ex-skill-note${notes[s.name].error ? ' is-error' : ''}">${escapeHtml(notes[s.name].text)}</span>` : ''}
      </span>
    </button>
    <div class="pv-side">${b ? `<span class="pv-side-busy">${spin('')}</span>` : rowBtn('view', 'View', { data: { name: s.name }, disabled: !s.active, title: s.active ? '' : 'Hidden while global skills are off' })}${moreBtn(s.name)}</div>
  </div>`;
}

function listHtml() {
  const skills = data?.skills || [];
  if (!skills.length) {
    return `<div class="list-empty ex-empty">${icon('book-open')}<strong>No skills yet</strong>Skills teach Jarvis a repeatable job — review a PR, fill a PDF, write in your voice. Paste a link above to add one.</div>`;
  }
  return skills.map(skillRow).join('');
}

// ─── Render ───────────────────────────────────────────────────────────────

function build(body) {
  body.innerHTML = `
    <section class="ex-add" aria-label="Add a skill">
      <div class="ex-add-head"><h3>Add a skill</h3><span>From a GitHub link, a SKILL.md, an archive or a folder</span></div>
      <div class="ex-src">
        <span class="pv-field-ic">${icon('link')}</span>
        <textarea id="skills-src" class="ex-src-input" rows="1" placeholder="Paste a GitHub link, owner/repo, or a SKILL.md link"
          aria-label="Skill to add" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
          data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other"></textarea>
      </div>
      <div class="ex-add-row">
        <div class="ex-add-scope" id="skills-add-scope"></div>
        <span id="skills-add-btn"></span>
      </div>
      <p class="ex-path" id="skills-path"></p>
      <div id="skills-chips"></div>
      <div class="ex-preview" id="skills-preview" aria-live="polite"></div>
    </section>
    <section class="ex-list-sec" aria-label="Installed skills">
      <div class="ex-list-head"><h3 id="skills-count">Installed</h3><span id="skills-scope-seg"></span></div>
      <div id="skills-notice"></div>
      <div class="ex-list" id="skills-list"></div>
    </section>`;
  const ta = $('skills-src');
  ta.value = add.text;
  autosize(ta);
  built = true;
}

function renderPreview() {
  patch($('skills-preview'), previewHtml());
  patch($('skills-add-btn'), installBtnHtml());
}

function renderAddPanel() {
  patch($('skills-add-scope'), seg(SCOPES, add.scope, { label: 'Where to install it', act: 'scope' }));
  patch($('skills-path'), pathHint());
  patch($('skills-chips'), chipsHtml());
  renderPreview();
}

function renderList() {
  const skills = data.skills || [];
  const hidden = data.hidden_global_count || 0;
  const sub = $('skills-sub');
  if (sub) {
    const project = skills.filter((s) => s.scope === 'project').length;
    sub.textContent = skills.length ? `${plural(skills.length, 'skill')} · ${project} in this project` : 'Jarvis loads a skill when a task matches it';
  }
  const count = $('skills-count');
  if (count) count.textContent = skills.length ? `Installed · ${skills.length}` : 'Installed';
  patch($('skills-scope-seg'), seg([
    { value: 'false', label: 'This project', title: 'Only skills from this folder' },
    { value: 'true', label: 'Project + global', title: 'Also skills installed for every project' },
  ], String(!!data.global_skills), { label: 'Which skills Jarvis can use', act: 'global' }));
  patch($('skills-notice'), hidden
    ? `<p class="pv-msg is-warn" role="status">${icon('circle-alert')}<span>${plural(hidden, 'global skill')} ${hidden === 1 ? 'is' : 'are'} hidden from Jarvis.</span>
        <button type="button" class="btn btn-sm" data-act="show-global">Show ${hidden === 1 ? 'it' : 'them'}</button></p>`
    : '');
  patch($('skills-list'), listHtml());
}

function renderDetail() {
  const body = $('skills-body');
  built = false;
  const v = view;
  const s = data?.skills?.find((x) => x.name === v.name);
  body.innerHTML = `
    <button type="button" class="btn btn-quiet detail-back" data-act="back">${icon('arrow-left')}<span>Back to skills</span></button>
    <div class="ex-detail-head"><strong class="ex-name">${escapeHtml(v.name)}</strong>${s ? `<span class="badge">${s.scope === 'project' ? 'Project' : 'Global'}</span>` : ''}</div>
    ${v.loading ? `<div class="list-loading">${'<div class="skeleton"></div>'.repeat(3)}</div>` : v.error ? msg(v.error, 'error') : `<div class="md ex-md">${renderMarkdown(v.content || '')}</div>`}`;
  applyMarkdownLinks(body);
  body.scrollTop = 0;
  body.querySelector('.detail-back')?.focus({ preventScroll: true });
}

function render() {
  const body = $('skills-body');
  if (!body) return;
  if (view) {
    renderDetail();
    return;
  }
  if (!data) {
    built = false;
    body.innerHTML = loadError
      ? `<div class="list-empty"><strong>Could not load skills</strong>Check that Jarvis is still running, then try again.<div class="pv-actions is-center">${btn('reload', 'Try again', { ic: 'refresh-cw' })}</div></div>`
      : `<div class="list-loading">${'<div class="skeleton"></div>'.repeat(5)}</div>`;
    return;
  }
  if (!built || !$('skills-src')) build(body);
  renderAddPanel();
  renderList();
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
  const body = $('skills-body');
  if (!el || !body.contains(el)) return;
  const name = el.dataset.name || el.closest('[data-name]')?.dataset.name || '';
  switch (el.dataset.act) {
    case 'view': openSkill(name); break;
    case 'back':
      view = null;
      render();
      break;
    case 'menu': menuFor(name, el); break;
    case 'chip': setSource(el.dataset.text); $('skills-src')?.focus(); break;
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
  render();
  openModal('skills', {
    focus: $('skills-src') || undefined,
    onClose: () => {
      closeMenu();
      view = null;
      for (const n of Object.keys(notes)) delete notes[n];
    },
  });
  autosize($('skills-src'));
  refreshSkills();
}

export function closeSkills() {
  closeModal('skills');
}

export function handleSkillsEvent() {
  if (isModalOpen('skills')) refreshSoon();
}

export function initSkills() {
  const body = $('skills-body');
  body?.addEventListener('click', handleClick);
  body?.addEventListener('change', handleChange);
  body?.addEventListener('input', handleInput);
  body?.addEventListener('keydown', handleKey);
  body?.addEventListener('keydown', (e) => {
    if (e.key === 'Backspace' && view && e.target === body) {
      view = null;
      render();
    }
  });
}
