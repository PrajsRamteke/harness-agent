/** Agent prompts: shell approval, multiple-choice questions, text input.
 *
 * Dismissing the dialog (Esc, clicking outside) only minimises it — the
 * agent is still blocked on an answer — so a chip above the composer brings
 * it back. Answering in the terminal closes it here (`prompt_resolved`).
 */
import { $, escapeHtml, showToast } from './utils.js';
import { icon } from './icons.js';
import { store, patchStore } from './store.js';
import { respondPrompt } from './api.js';
import { openModal, closeModal, topModal } from './modal.js';
import { syncTyping } from './chat.js';

const MODAL = 'prompt-modal';
let answering = false;
let ask = null; // { qs, index, answers, picked:Set }
let keyHandler = null;

const card = () => $('prompt-card');

function setActive(prompt) {
  patchStore({ activePrompt: prompt });
  renderWaitChip();
  syncTyping();
}

function renderWaitChip() {
  const slot = $('wait-chip');
  if (!slot) return;
  const p = store.activePrompt;
  if (!p || topModal() === MODAL) {
    slot.innerHTML = '';
    return;
  }
  const what = { shell_approval: 'approve a command', ask_user: 'answer a question', text_input: 'enter a value' }[p.kind] || 'respond';
  slot.innerHTML = `
    <button type="button" class="dock-chip is-waiting" id="wait-open">
      ${icon('bell-ring')}<span class="dock-chip-text"><strong>Jarvis is waiting</strong> for you to ${escapeHtml(what)}</span>
      <span class="act-btn">Review</span>
    </button>`;
  $('wait-open').addEventListener('click', reopen);
}

function show() {
  openModal(MODAL, {
    focus: card().querySelector('[data-autofocus]') || card().querySelector('button'),
    onClose: (reason) => {
      if (reason !== 'done' && store.activePrompt) {
        // Still waiting: keep the answer one tap away.
        renderWaitChip();
      }
    },
  });
  renderWaitChip();
}

function reopen() {
  if (!store.activePrompt) return;
  show();
}

function finish() {
  answering = false;
  ask = null;
  keyHandler = null;
  setActive(null);
  closeModal(MODAL, 'done');
  renderWaitChip();
}

async function answer(result) {
  const p = store.activePrompt;
  if (!p || answering) return;
  answering = true;
  card().querySelectorAll('button').forEach((b) => { b.disabled = true; });
  try {
    await respondPrompt(p.id, result);
    finish();
  } catch (err) {
    if (err?.status === 404) {
      showToast('Already answered in the terminal');
      finish();
      return;
    }
    answering = false;
    card().querySelectorAll('button').forEach((b) => { b.disabled = false; });
    showToast('Answer not sent — check the connection', true);
  }
}

function header(kind, title, sub) {
  const ic = { warn: 'terminal', ask: 'message-circle-question', input: 'text-cursor-input' }[kind];
  return `
    <div class="prompt-head">
      <span class="prompt-icon is-${kind}">${icon(ic)}</span>
      <div class="prompt-titles">
        <h2>${escapeHtml(title)}</h2>
        ${sub ? `<p>${escapeHtml(sub)}</p>` : ''}
      </div>
    </div>`;
}

// ─── Shell approval ───────────────────────────────────────────────────────

export function renderShellApproval(data) {
  setActive({ id: data.id, kind: 'shell_approval' });
  card().innerHTML = `
    ${header('warn', 'Run this command?', 'Jarvis wants to run this in a shell on your computer.')}
    <pre class="cmd-preview">${escapeHtml(data.cmd || '')}</pre>
    <div class="prompt-actions">
      <button type="button" class="btn btn-danger" data-val="n">${icon('x')}<span>Deny</span><kbd>N</kbd></button>
      <button type="button" class="btn" data-val="a">${icon('shield-check')}<span>Always allow</span><kbd>A</kbd></button>
      <button type="button" class="btn btn-primary" data-val="y" data-autofocus>${icon('play')}<span>Run</span><kbd>Y</kbd></button>
    </div>
    <p class="prompt-note">“Always allow” skips this prompt for matching commands.</p>`;
  card().querySelectorAll('[data-val]').forEach((btn) => {
    btn.addEventListener('click', () => answer(btn.dataset.val));
  });
  keyHandler = (e) => {
    const k = e.key.toLowerCase();
    if (['y', 'n', 'a'].includes(k)) {
      e.preventDefault();
      answer(k);
    }
  };
  show();
}

// ─── Questions ────────────────────────────────────────────────────────────

function paintQuestion() {
  const q = ask.qs[ask.index];
  const multi = !!q.allow_multiple;
  const last = ask.index >= ask.qs.length - 1;
  const progress = ask.qs.length > 1
    ? `<div class="ask-progress">${ask.qs.map((_, i) => `<i class="${i < ask.index ? 'is-done' : i === ask.index ? 'is-now' : ''}"></i>`).join('')}</div>`
    : '';
  card().innerHTML = `
    ${header('ask', 'Jarvis has a question', ask.qs.length > 1 ? `Question ${ask.index + 1} of ${ask.qs.length}` : (multi ? 'Pick all that apply' : 'Pick one'))}
    ${progress}
    <p class="ask-q">${q.header ? `<small>${escapeHtml(q.header)}</small>` : ''}${escapeHtml(q.prompt || '')}</p>
    <div class="choices" role="${multi ? 'group' : 'radiogroup'}">
      ${(q.options || []).map((o, i) => `
        <button type="button" class="choice${multi ? ' is-multi' : ''}${ask.picked.has(o.id) ? ' is-selected' : ''}" data-opt="${escapeHtml(o.id)}" role="${multi ? 'checkbox' : 'radio'}" aria-checked="${ask.picked.has(o.id)}">
          <span class="choice-key">${i < 9 ? i + 1 : ''}</span>
          <span class="choice-body"><strong>${escapeHtml(o.label)}</strong>${o.description ? `<span>${escapeHtml(o.description)}</span>` : ''}</span>
        </button>`).join('')}
    </div>
    <div class="prompt-actions">
      ${ask.index > 0 ? `<button type="button" class="btn" data-act="back">${icon('arrow-left')}<span>Back</span></button>` : '<button type="button" class="btn btn-quiet" data-act="skip">Skip</button>'}
      <button type="button" class="btn btn-primary" data-act="next" ${ask.picked.size ? '' : 'disabled'}><span>${last ? 'Send answer' : 'Next'}</span><kbd>Enter</kbd></button>
    </div>`;

  card().querySelectorAll('[data-opt]').forEach((btn) => {
    btn.addEventListener('click', () => pickOption(btn.dataset.opt));
  });
  card().querySelector('[data-act="next"]')?.addEventListener('click', nextQuestion);
  card().querySelector('[data-act="back"]')?.addEventListener('click', () => {
    ask.index -= 1;
    const prev = ask.answers.pop();
    ask.picked = new Set(prev?.selected_ids || []);
    paintQuestion();
  });
  card().querySelector('[data-act="skip"]')?.addEventListener('click', () => answer({ answers: [], cancelled: true }));
  (card().querySelector('.choice.is-selected') || card().querySelector('.choice'))?.focus({ preventScroll: true });
}

function pickOption(id) {
  const q = ask.qs[ask.index];
  if (q.allow_multiple) {
    if (ask.picked.has(id)) ask.picked.delete(id);
    else ask.picked.add(id);
  } else {
    ask.picked = new Set([id]);
  }
  card().querySelectorAll('[data-opt]').forEach((btn) => {
    const on = ask.picked.has(btn.dataset.opt);
    btn.classList.toggle('is-selected', on);
    btn.setAttribute('aria-checked', String(on));
  });
  const next = card().querySelector('[data-act="next"]');
  if (next) next.disabled = !ask.picked.size;
}

function nextQuestion() {
  if (!ask || !ask.picked.size) return;
  const q = ask.qs[ask.index];
  const ids = [...ask.picked];
  const labels = (q.options || []).filter((o) => ask.picked.has(o.id)).map((o) => o.label);
  ask.answers.push({ question_id: q.id, selected_ids: ids, labels });
  if (ask.index >= ask.qs.length - 1) {
    answer({ answers: ask.answers });
    return;
  }
  ask.index += 1;
  ask.picked = new Set();
  paintQuestion();
}

export function renderAskUser(data) {
  const qs = data.questions || [];
  if (!qs.length) return;
  setActive({ id: data.id, kind: 'ask_user' });
  ask = { qs, index: 0, answers: [], picked: new Set() };
  paintQuestion();
  keyHandler = (e) => {
    const q = ask?.qs[ask.index];
    if (!q) return;
    const n = Number(e.key);
    if (n >= 1 && n <= Math.min(9, q.options.length)) {
      e.preventDefault();
      pickOption(q.options[n - 1].id);
    } else if (e.key === 'Enter' && ask.picked.size && !e.target.closest?.('[data-act="back"],[data-act="skip"]')) {
      e.preventDefault();
      nextQuestion();
    }
  };
  show();
}

// ─── Text input ───────────────────────────────────────────────────────────

export function renderTextInput(data) {
  setActive({ id: data.id, kind: 'text_input' });
  card().innerHTML = `
    ${header('input', data.password ? 'Secret needed' : 'Jarvis needs a value', data.prompt || '')}
    <input class="prompt-input" id="prompt-input" type="${data.password ? 'password' : 'text'}" autocomplete="off" spellcheck="false" placeholder="${data.password ? 'Paste the secret' : 'Type your answer'}" data-autofocus>
    <div class="prompt-actions">
      <button type="button" class="btn" data-act="cancel">Cancel</button>
      <button type="button" class="btn btn-primary" data-act="ok"><span>Send</span><kbd>Enter</kbd></button>
    </div>
    ${data.password ? '<p class="prompt-note">Sent straight to Jarvis on your computer; it is not shown in the chat.</p>' : ''}`;
  const input = $('prompt-input');
  card().querySelector('[data-act="ok"]').addEventListener('click', () => answer(input.value || ''));
  card().querySelector('[data-act="cancel"]').addEventListener('click', () => answer(null));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.isComposing) {
      e.preventDefault();
      answer(input.value || '');
    }
  });
  keyHandler = null;
  show();
}

// ─── Resolution from elsewhere ────────────────────────────────────────────

export function handlePromptResolved(id) {
  if (store.activePrompt?.id === id && !answering) finish();
}

export function initPrompts() {
  document.addEventListener('keydown', (e) => {
    if (!keyHandler || topModal() !== MODAL || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.matches?.('input, textarea')) return;
    keyHandler(e);
  });
}
