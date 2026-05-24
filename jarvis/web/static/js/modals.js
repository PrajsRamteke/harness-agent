/** Modal prompts: shell approval, ask_user, text input */
import { $, escapeHtml, icons, showToast } from './utils.js';
import { patchStore, store } from './store.js';
import { respondPrompt as apiRespond } from './api.js';

let askState = null;

export function closeOverlay() {
  $('overlay')?.classList.remove('open');
  const sheet = $('sheet');
  if (sheet) sheet.innerHTML = '';
  patchStore({ activePrompt: null });
  askState = null;
}

export async function respondPrompt(id, result) {
  try {
    await apiRespond(id, result);
  } catch {
    showToast('Failed to send response', true);
  }
  closeOverlay();
}

export function renderShellApproval(data) {
  patchStore({ activePrompt: data.id });
  const sheet = $('sheet');
  if (!sheet) return;

  sheet.innerHTML = `
    <div class="sheet-header">
      <div class="sheet-icon warn"><i data-lucide="terminal"></i></div>
      <div>
        <h2 class="sheet-title">Shell command approval</h2>
        <p class="sheet-sub">Jarvis wants to run this on your machine</p>
      </div>
    </div>
    <pre class="code-block">${escapeHtml(data.cmd || '')}</pre>
    <div class="sheet-actions">
      <button class="btn btn-ok" data-val="y"><i data-lucide="play"></i> Run</button>
      <button class="btn btn-danger" data-val="n"><i data-lucide="x"></i> Deny</button>
      <button class="btn btn-ghost" data-val="a"><i data-lucide="shield-check"></i> Always</button>
    </div>`;

  sheet.querySelectorAll('[data-val]').forEach((btn) => {
    btn.addEventListener('click', () => respondPrompt(data.id, btn.dataset.val));
  });

  $('overlay')?.classList.add('open');
  icons();
}

export function renderAskUser(data) {
  patchStore({ activePrompt: data.id });
  const qs = data.questions || [];
  askState = { qIndex: 0, answers: [], multi: new Set(), selected: null, qs };

  const sheet = $('sheet');
  if (!sheet) return;

  sheet.innerHTML = `
    <div class="sheet-header">
      <div class="sheet-icon ask"><i data-lucide="help-circle"></i></div>
      <div>
        <h2 class="sheet-title">Your input needed</h2>
        <p class="sheet-sub">Question <span id="q-num">1</span> of ${qs.length}</p>
      </div>
    </div>
    <div class="progress-dots" id="progress-dots"></div>
    <div id="ask-body"></div>
    <div class="sheet-actions">
      <button class="btn btn-ghost" id="ask-back" type="button" style="display:none">Back</button>
      <button class="btn btn-ok" id="ask-next" type="button">Next</button>
      <button class="btn btn-danger" id="ask-cancel" type="button">Cancel</button>
    </div>`;

  const dots = $('progress-dots');
  dots.innerHTML = qs.map((_, i) => `<div class="progress-dot" data-i="${i}"></div>`).join('');

  function paintQuestion() {
    const q = qs[askState.qIndex];
    askState.multi = new Set();
    askState.selected = null;
    $('q-num').textContent = String(askState.qIndex + 1);
    dots.querySelectorAll('.progress-dot').forEach((d, i) => {
      d.classList.toggle('done', i < askState.qIndex);
      d.classList.toggle('active', i === askState.qIndex);
    });
    $('ask-back').style.display = askState.qIndex > 0 ? 'inline-flex' : 'none';
    $('ask-next').textContent = askState.qIndex >= qs.length - 1 ? 'Submit' : 'Next';

    const body = $('ask-body');
    body.innerHTML = `<p style="margin:0 0 12px;font-size:15px">${escapeHtml((q.header ? q.header + ' — ' : '') + q.prompt)}</p><div class="choices" id="choices"></div>`;
    const choices = $('choices');
    (q.options || []).forEach((opt) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'choice';
      b.innerHTML = `<strong>${escapeHtml(opt.label)}</strong>${opt.description ? `<span>${escapeHtml(opt.description)}</span>` : ''}`;
      b.addEventListener('click', () => {
        if (q.allow_multiple) {
          if (askState.multi.has(opt.id)) askState.multi.delete(opt.id);
          else askState.multi.add(opt.id);
          choices.querySelectorAll('.choice').forEach((el, idx) => {
            el.classList.toggle('selected', askState.multi.has((q.options[idx] || {}).id));
          });
        } else {
          askState.selected = opt.id;
          choices.querySelectorAll('.choice').forEach((el) => el.classList.remove('selected'));
          b.classList.add('selected');
        }
      });
      choices.appendChild(b);
    });
  }

  $('ask-back').onclick = () => {
    if (askState.qIndex > 0) {
      askState.qIndex -= 1;
      askState.answers.pop();
      paintQuestion();
    }
  };

  $('ask-next').onclick = () => {
    const q = qs[askState.qIndex];
    const selected = q.allow_multiple
      ? [...askState.multi]
      : askState.selected
        ? [askState.selected]
        : [];
    if (!selected.length) {
      showToast('Select an option', true);
      return;
    }
    askState.answers.push({ question_id: q.id, selected });
    if (askState.qIndex >= qs.length - 1) {
      respondPrompt(data.id, { answers: askState.answers });
      return;
    }
    askState.qIndex += 1;
    paintQuestion();
  };

  $('ask-cancel').onclick = () => respondPrompt(data.id, { answers: [], cancelled: true });

  paintQuestion();
  $('overlay')?.classList.add('open');
  icons();
}

export function renderTextInput(data) {
  patchStore({ activePrompt: data.id });
  const sheet = $('sheet');
  if (!sheet) return;

  sheet.innerHTML = `
    <div class="sheet-header">
      <div class="sheet-icon input"><i data-lucide="text-cursor-input"></i></div>
      <div>
        <h2 class="sheet-title">Input required</h2>
        <p class="sheet-sub">${escapeHtml(data.prompt || 'Please provide a value')}</p>
      </div>
    </div>
    <input class="sheet-input" id="sheet-input" type="${data.password ? 'password' : 'text'}" autocomplete="off" placeholder="${data.password ? 'Password' : 'Type here…'}">
    <div class="sheet-actions">
      <button class="btn btn-ok" id="input-ok" type="button">Submit</button>
      <button class="btn btn-danger" id="input-cancel" type="button">Cancel</button>
    </div>`;

  const input = $('sheet-input');
  $('input-ok').onclick = () => respondPrompt(data.id, input.value || '');
  $('input-cancel').onclick = () => respondPrompt(data.id, null);
  input?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') respondPrompt(data.id, input.value || '');
  });

  $('overlay')?.classList.add('open');
  icons();
  setTimeout(() => input?.focus(), 100);
}

export function bindOverlayDismiss() {
  $('overlay')?.addEventListener('click', (e) => {
    if (e.target === $('overlay')) closeOverlay();
  });
}

export function handlePromptResolved(id) {
  if (store.activePrompt === id) closeOverlay();
}
