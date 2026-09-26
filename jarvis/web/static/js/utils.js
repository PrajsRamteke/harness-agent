/** Shared DOM + string helpers */
import { icon } from './icons.js';

export const $ = (id) => document.getElementById(id);

export function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function showToast(msg, isError = false) {
  const stack = $('toasts');
  if (!stack || !msg) return;
  const el = document.createElement('div');
  el.className = `toast${isError ? ' is-error' : ''}`;
  el.setAttribute('role', isError ? 'alert' : 'status');
  el.innerHTML = `${icon(isError ? 'circle-alert' : 'check')}<span>${escapeHtml(msg)}</span>`;
  stack.appendChild(el);
  while (stack.children.length > 3) stack.firstElementChild.remove();
  setTimeout(() => {
    el.classList.add('is-leaving');
    setTimeout(() => el.remove(), 320);
  }, isError ? 4200 : 2400);
}

export function readToken() {
  return new URLSearchParams(location.search).get('token') || '';
}

export function truncate(str, max = 72) {
  const s = String(str || '');
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export function debounce(fn, ms = 120) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

/** 1234 → "1.2k", 2_500_000 → "2.5M" */
export function formatCount(n) {
  const v = Number(n) || 0;
  if (v < 1000) return String(v);
  if (v < 1_000_000) return `${(v / 1000).toFixed(v < 10_000 ? 1 : 0).replace(/\.0$/, '')}k`;
  return `${(v / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
}

export function formatElapsed(sec) {
  const s = Math.max(0, Math.floor(sec));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${String(s % 60).padStart(2, '0')}s`;
}

export const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

export function storageGet(key, fallback = null) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v;
  } catch {
    return fallback;
  }
}

export function storageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch { /* private mode */ }
}

/** Copy text — works on LAN HTTP where the Clipboard API is blocked. */
export async function copyText(text) {
  const value = String(text ?? '');
  if (!value) return false;

  if (navigator.clipboard?.writeText && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch { /* fall through to legacy copy */ }
  }

  const ta = document.createElement('textarea');
  ta.value = value;
  ta.setAttribute('readonly', '');
  Object.assign(ta.style, { position: 'fixed', left: '-9999px', top: '0', opacity: '0' });
  document.body.appendChild(ta);
  ta.select();
  ta.setSelectionRange(0, value.length);
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  ta.remove();
  return ok;
}

/** Brief "Copied" confirmation on a button that shows an icon + label. */
export function flashDone(btn, label = 'Copied') {
  if (!btn) return;
  const prev = btn.innerHTML;
  btn.classList.add('is-done');
  btn.innerHTML = `${icon('check')}<span>${escapeHtml(label)}</span>`;
  setTimeout(() => {
    btn.classList.remove('is-done');
    btn.innerHTML = prev;
  }, 1400);
}

/** Keep the focus inside a dialog while it is open. */
export function trapFocus(container, e) {
  if (e.key !== 'Tab' || !container) return;
  const items = [...container.querySelectorAll(
    'button:not([disabled]), input:not([disabled]), textarea, [tabindex]:not([tabindex="-1"])',
  )].filter((el) => el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}
