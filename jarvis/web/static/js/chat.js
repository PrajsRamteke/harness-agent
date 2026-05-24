/** Chat transcript rendering */
import { $, escapeHtml, icons } from './utils.js';
import { store } from './store.js';
import { renderMarkdown, renderPlainWithLinks, applyMarkdownLinks } from './markdown.js';

const ROLE_ICONS = {
  you: 'user',
  assistant: 'bot',
  thinking: 'brain',
  log: 'terminal',
  system: 'info',
};

export function normalizeRole(role) {
  const r = (role || 'assistant').toLowerCase();
  if (r === 'user') return 'you';
  if (r === 'assistant' || r === 'you' || r === 'thinking' || r === 'log' || r === 'system') return r;
  return 'assistant';
}

let userScrolledUp = false;
let streamEl = null;
let streamKind = null;

export function initChatScroll() {
  const chat = $('chat');
  const fab = $('scroll-fab');
  if (!chat || !fab) return;

  chat.addEventListener('scroll', () => {
    const dist = chat.scrollHeight - chat.scrollTop - chat.clientHeight;
    userScrolledUp = dist > 80;
    fab.classList.toggle('show', userScrolledUp);
  });

  fab.addEventListener('click', () => scrollToBottom(true));
}

export function scrollToBottom(force = false) {
  const chat = $('chat');
  if (!chat) return;
  if (!force && userScrolledUp) return;
  chat.scrollTop = chat.scrollHeight;
  $('scroll-fab')?.classList.remove('show');
  userScrolledUp = false;
}

function updateEmptyState() {
  const chat = $('chat');
  const empty = $('empty-state');
  if (!chat || !empty) return;
  empty.classList.toggle('hidden', chat.children.length > 0);
}

function isDuplicateThinking(chat, text) {
  if (!chat) return false;
  const last = chat.lastElementChild;
  if (!last?.classList.contains('thinking')) return false;
  const body = last.querySelector('.msg-body');
  return body?.textContent?.trim() === text;
}

function setBodyContent(body, role, text) {
  if (role === 'assistant' || role === 'stream') {
    body.innerHTML = renderMarkdown(text);
    applyMarkdownLinks(body);
  } else if (role === 'you') {
    body.innerHTML = renderPlainWithLinks(text);
    applyMarkdownLinks(body);
  } else if (role === 'thinking') {
    body.textContent = String(text);
  } else {
    body.textContent = text;
  }
}

function shouldShowThinking() {
  return store.session.show_internal !== false && store.showThinkingUi;
}

export function appendMessage(role, text, title) {
  if (!text || !String(text).trim()) return;
  role = normalizeRole(role);
  const trimmed = String(text).trim();
  if (role === 'thinking' && !shouldShowThinking()) return;
  removeStreamBubble();

  const chat = $('chat');
  if (role === 'thinking' && isDuplicateThinking(chat, trimmed)) return;
  const wrap = document.createElement('div');
  wrap.className = `msg ${role}`;
  if (role === 'thinking' && !shouldShowThinking()) {
    wrap.classList.add('hidden-think');
  }

  const card = document.createElement('div');
  card.className = 'msg-card';

  if (role === 'thinking') {
    card.classList.add('thinking-toggle');
    card.addEventListener('click', () => {
      card.classList.toggle('expanded');
      const hint = card.querySelector('.expand-hint span');
      if (hint) hint.textContent = card.classList.contains('expanded') ? 'Tap to collapse' : 'Tap to expand';
    });
  }

  if (role !== 'log' || title) {
    const label = document.createElement('div');
    label.className = 'msg-label';
    const icon = ROLE_ICONS[role] || 'message-circle';
    const labelText = title || (role === 'you' ? 'You' : role === 'assistant' ? 'Jarvis' : role);
    label.innerHTML = `<i data-lucide="${icon}"></i> ${escapeHtml(labelText)}`;
    card.appendChild(label);
  }

  const body = document.createElement('div');
  body.className = 'msg-body';
  setBodyContent(body, role, text);
  card.appendChild(body);

  if (role === 'thinking') {
    const hint = document.createElement('div');
    hint.className = 'expand-hint';
    hint.innerHTML = '<i data-lucide="chevrons-down"></i><span>Tap to expand</span>';
    card.appendChild(hint);
  }

  wrap.appendChild(card);
  chat.appendChild(wrap);
  icons();
  updateEmptyState();
  scrollToBottom(true);
}

export function clearChat() {
  const chat = $('chat');
  if (chat) chat.innerHTML = '';
  removeStreamBubble();
  updateEmptyState();
}

let streamBuffer = '';

export function ensureStreamBubble(kind, title) {
  if (kind === 'thinking' && !shouldShowThinking()) return null;
  if (streamEl && streamKind === kind) {
    return streamEl.querySelector('.msg-body');
  }
  removeStreamBubble();
  streamKind = kind;
  streamBuffer = '';

  const chat = $('chat');
  const wrap = document.createElement('div');
  wrap.className = `msg stream ${kind}`;
  wrap.id = 'live-stream';

  const card = document.createElement('div');
  card.className = 'msg-card';
  if (kind === 'thinking') card.classList.add('thinking-toggle', 'expanded');
  const label = document.createElement('div');
  label.className = 'msg-label';
  const icon = kind === 'thinking' ? 'brain' : 'bot';
  label.innerHTML = `<i data-lucide="${icon}"></i> ${kind === 'thinking' ? 'Thinking' : escapeHtml(title || 'Assistant')}`;
  card.appendChild(label);

  const body = document.createElement('div');
  body.className = 'msg-body';
  card.appendChild(body);
  wrap.appendChild(card);
  chat.appendChild(wrap);
  streamEl = wrap;
  icons();
  updateEmptyState();
  return body;
}

export function pushStreamDelta(kind, chunk) {
  if (kind === 'thinking' && !shouldShowThinking()) return;
  const body = ensureStreamBubble(kind);
  if (!body || !chunk) return;
  if (kind === 'assistant') {
    streamBuffer += chunk;
    body.innerHTML = renderMarkdown(streamBuffer);
    applyMarkdownLinks(body);
  } else {
    body.textContent += chunk;
  }
  scrollToBottom();
}

export function removeStreamBubble() {
  document.getElementById('live-stream')?.remove();
  streamEl = null;
  streamKind = null;
  streamBuffer = '';
}

export function refreshThinkingVisibility() {
  const show = shouldShowThinking();
  document.querySelectorAll('.msg.thinking').forEach((el) => {
    el.classList.toggle('hidden-think', !show);
  });
  if (!show && streamKind === 'thinking') {
    removeStreamBubble();
  }
}

export function bindQuickCommands(onPick) {
  document.querySelectorAll('.quick-cmd').forEach((btn) => {
    btn.addEventListener('click', () => onPick(btn.dataset.cmd || ''));
  });
}
