/** Full-screen toggle (topbar button). Uses the browser Fullscreen API on the whole page, so the
 * browser's tab strip, address bar and the OS menu bar / dock go away; a second click (or Esc,
 * which the browser handles) puts everything back. Hidden where the API is missing (iPhone Safari). */
import { $, showToast } from './utils.js';
import { icon } from './icons.js';

function supported() {
  return !!(document.fullscreenEnabled || document.webkitFullscreenEnabled);
}

function current() {
  return document.fullscreenElement || document.webkitFullscreenElement || null;
}

function sync() {
  const btn = $('fs-btn');
  if (!btn) return;
  const on = !!current();
  const label = on ? 'Exit full screen' : 'Full screen';
  btn.setAttribute('aria-pressed', String(on));
  btn.setAttribute('aria-label', label);
  btn.title = on ? 'Exit full screen (Esc)' : 'Full screen';
  btn.innerHTML = `<span data-icon="${on ? 'minimize' : 'maximize'}">${icon(on ? 'minimize' : 'maximize')}</span>`;
}

export async function toggleFullscreen() {
  try {
    if (current()) {
      await (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    } else {
      const root = document.documentElement;
      await (root.requestFullscreen || root.webkitRequestFullscreen).call(root, { navigationUI: 'hide' });
    }
  } catch (err) {
    showToast(err?.message || 'Full screen is not available here', true);
  }
}

export function initFullscreen() {
  const btn = $('fs-btn');
  if (!btn) return;
  if (!supported()) {
    btn.hidden = true;
    return;
  }
  btn.addEventListener('click', toggleFullscreen);
  document.addEventListener('fullscreenchange', sync);
  document.addEventListener('webkitfullscreenchange', sync);
  sync();
}
