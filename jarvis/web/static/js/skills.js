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
const RECOMMENDED = [
  { repo: 'anthropics/skills', label: 'Anthropic skills', desc: 'Official collection for documents, design, and development.', count: '20 skills' },
  { repo: 'vercel-labs/agent-skills', label: 'Vercel agent skills', desc: 'React, web design, and frontend engineering.', count: '9 skills' },
  { repo: 'obra/superpowers', label: 'Superpowers', desc: 'Software development workflows and coding practices.', count: '15 skills' },
  { repo: 'openai/skills', label: 'OpenAI skills', desc: 'Official skills for coding and OpenAI tools.', count: '44 skills' },
  { repo: 'github/awesome-copilot', label: 'GitHub Awesome Copilot', desc: 'Community collection of coding and productivity skills.', count: '445 skills' },
  { repo: 'microsoft/skills', label: 'Microsoft skills', desc: 'Official collection covering Microsoft developer tools.', count: '210 skills' },
  { repo: 'wshobson/agents', label: 'Wshobson agents', desc: 'Curated development workflows and specialist skills.', count: '184 skills' },
  { repo: 'K-Dense-AI/claude-scientific-skills', label: 'Scientific skills', desc: 'Research, science, and data-focused skills.', count: '177 skills' },
  { repo: 'ComposioHQ/awesome-claude-skills', label: 'Composio skills', desc: 'Broad collection of reusable agent skills.', count: '864 skills' },
  { repo: 'alirezarezvani/claude-skills', label: 'Claude skills', desc: 'Large multi-topic collection for software and business.', count: '846 skills' },
];
// A collection this big gets a search box above its checklist.
const FILTER_MIN = 6;

function recommendedHtml() {
  return `<section class="ex-recommend" aria-labelledby="skills-recommend-title">
    <div class="ex-recommend-head"><h3 id="skills-recommend-title">Recommended collections</h3><span>Browse a repository and choose its skills</span></div>
    <div class="ex-recommend-list">${RECOMMENDED.map((s, i) => {
      const text = `https://github.com/${s.repo}`;
      return `<button type="button" class="ex-recommend-item" data-act="chip" data-text="${escapeHtml(text)}" title="Browse ${escapeHtml(s.label)}">
        <span class="ex-recommend-num">${String(i + 1).padStart(2, '0')}</span>
        <span class="ex-recommend-copy"><strong>${escapeHtml(s.label)}</strong><span>${escapeHtml(s.desc)}</span></span>
        <span class="ex-recommend-source">${escapeHtml(s.count)}</span>
      </button>`;
    }).join('')}</div>
  </section>`;
}

let data = null; // last /api/skills
let loadError = false;
let built = false;
let view = null; // { name, content?, loading?, error? } — the SKILL.md reader (a sub-view)
let mode = 'list'; // 'list' | 'add' — "Add a skill" is a sub-view too
let query = '';
let barBuilt = false;
let previewSeq = 0;
let skillFilter = '';
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
    return `<p class="pv-hint ex-idle">Skills are instructions Jarvis follows — only add ones you trust.</p>`;
  }
  if (!p || p.loading) return `<p class="pv-hint ex-loading">${spin('Reading it… a big repository can take a few seconds')}</p>`;
  if (p.error) return msg(p.error, 'error');
  return '';
}

// A found collection is a shell built once (title, search, list) whose parts are
// patched on their own — so the search box keeps focus, caret and IME state while typing.
function filterWords() {
  return skillFilter.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

function visibleSkills() {
  const skills = add.preview?.skills || [];
  const words = filterWords();
  if (!words.length) return skills;
  return skills.filter((s) => {
    const hay = `${s.name} ${s.description || ''}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
}

function pickShellHtml(p) {
  const search = p.skills.length >= FILTER_MIN
    ? `<label class="dlg-search ex-sk-search">
        <span class="dlg-search-ic">${icon('search')}</span>
        <input id="skills-filter" type="search" value="${escapeHtml(skillFilter)}" placeholder="Search ${p.skills.length} skills by name or description"
          aria-label="Search skills in this collection" aria-controls="skills-match-list" aria-describedby="skills-filter-count"
          autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="search"
          data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other">
        <span class="ex-sk-count" id="skills-filter-count" aria-live="polite"></span>
        <button type="button" class="ex-sk-clear" data-act="clear-filter" aria-label="Clear search" title="Clear (esc)">${icon('x')}</button>
      </label>`
    : '';
  return `<div class="ex-sk-head" id="skills-pick-head"></div>${search}<div class="ex-sk-list" id="skills-match-list" role="group" aria-label="Skills found"></div>`;
}

function pickHeadHtml(p, visible) {
  const filtering = filterWords().length > 0;
  const usable = visible.filter((s) => s.usable);
  const all = usable.length > 0 && usable.every((s) => selected.has(s.name));
  const toggle = p.skills.filter((s) => s.usable).length > 1 && usable.length
    ? `<button type="button" class="link-btn" data-act="select-all">${filtering
      ? `${all ? 'Deselect' : 'Select'} ${usable.length} shown`
      : all ? 'Select none' : 'Select all'}</button>`
    : '';
  return `<span class="ex-sk-title"><strong>${escapeHtml(tildify(p.label))}</strong> · ${plural(p.skills.length, 'skill')}${selected.size ? ` · <span class="ex-sk-picked">${selected.size} selected</span>` : ''}</span>${toggle}`;
}

function pickListHtml(visible) {
  if (visible.length) return visible.map(skillCheck).join('');
  return `<div class="ex-sk-none">${icon('search')}<span>No skills match “${escapeHtml(skillFilter.trim())}”</span>
    <button type="button" class="link-btn" data-act="clear-filter">Clear search</button></div>`;
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
      ${recommendedHtml()}
      <div class="ex-preview" id="skills-preview" aria-live="polite"></div>
    </section>`;
  const ta = $('skills-src');
  ta.value = add.text;
  autosize(ta);
  built = 'add';
}

function renderPreview() {
  const box = $('skills-preview');
  if (!box) return;
  const p = add.preview;
  if (!add.result && add.text.trim() && p?.skills) {
    if (box._shell !== p) {
      box.innerHTML = pickShellHtml(p);
      box._shell = p;
      box._html = null;
    }
    const visible = visibleSkills();
    patch($('skills-pick-head'), pickHeadHtml(p, visible));
    const list = $('skills-match-list');
    const top = list.scrollTop; // ticking a box must not jump the list back to the top
    patch(list, pickListHtml(visible));
    list.scrollTop = top;
    patch($('skills-filter-count'), filterWords().length ? `${visible.length} of ${p.skills.length}` : '');
  } else {
    box._shell = null;
    patch(box, previewHtml());
  }
  if (mode === 'add' && !view) renderFoot();
}

function renderAddPanel() {
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
  skillFilter = '';
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
      // Acts on what the search shows; picks hidden by the search stay as they were.
      const usable = visibleSkills().filter((s) => s.usable);
      const all = usable.every((s) => selected.has(s.name));
      usable.forEach((s) => (all ? selected.delete(s.name) : selected.add(s.name)));
      renderPreview();
      break;
    }
    case 'clear-filter': {
      skillFilter = '';
      const f = $('skills-filter');
      if (f) { f.value = ''; f.focus({ preventScroll: true }); }
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
  if (el.id === 'skills-filter') {
    skillFilter = el.value;
    renderPreview();
    const list = $('skills-match-list');
    if (list) list.scrollTop = 0;
    return;
  }
  if (el.id === 'skills-q') {
    query = el.value;
    if (data) renderList();
    return;
  }
  if (el.id !== 'skills-src') return;
  skillFilter = '';
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
  // In a found collection: ↑ ↓ walk search ↔ checkboxes, Space ticks, ↵ in the search ticks a lone match.
  if (mode === 'add' && arrowRows(e, $('skills-filter'), [...($('skills-match-list')?.querySelectorAll('.ex-check') || [])])) return;
  if (e.key === 'Enter' && !e.isComposing && e.target.id === 'skills-filter') {
    e.preventDefault();
    const only = visibleSkills().filter((s) => s.usable);
    if (only.length === 1) {
      if (selected.has(only[0].name)) selected.delete(only[0].name);
      else selected.add(only[0].name);
      renderPreview();
    }
    return;
  }
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
