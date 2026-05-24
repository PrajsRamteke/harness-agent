/** Shared DOM + string helpers */
export const $ = (id) => document.getElementById(id);

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function icons() {
  if (window.lucide) window.lucide.createIcons();
}

export function showToast(msg, isError = false) {
  const toast = $('toast');
  if (!toast) return;
  toast.textContent = msg;
  toast.classList.toggle('error', isError);
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2800);
}

export function readToken() {
  return new URLSearchParams(location.search).get('token') || '';
}

export function truncate(str, max = 72) {
  const s = String(str || '');
  return s.length > max ? s.slice(0, max - 1) + '…' : s;
}

export function debounce(fn, ms = 120) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}
