/** Appearance: light / dark / system mode, soft contrast, accent colour, device preferences */
import { $, escapeHtml, storageGet, storageSet, reducedMotion, haptic } from './utils.js';
import { icon } from './icons.js';
import { patchStore } from './store.js';
import { openModal } from './modal.js';

export const MODES = [
  { id: 'system', label: 'System', icon: 'monitor' },
  { id: 'light', label: 'Light', icon: 'sun' },
  { id: 'dark', label: 'Dark', icon: 'moon' },
];

/** Soft is a switch on top of the mode: light → warm paper, dark → charcoal. */
const SOFT_SUB = {
  system: 'Warm paper in light, charcoal in dark',
  light: 'Warm paper instead of bright white',
  dark: 'Charcoal instead of pure black',
};

const THEME_COLOR = { light: '#f6f6f5', soft: '#f4f1ea', dim: '#1c1d21', dark: '#000000' };
export const THEME_LABEL = { light: 'Light', soft: 'Soft light', dim: 'Soft dark', dark: 'Dark' };

/** Themes that use the light palette (tokens.css keys off data-theme). */
const LIGHT_THEMES = new Set(['light', 'soft']);

export const ACCENTS = [
  { id: 'green', label: 'Green', dark: '#4ade80', light: '#16a34a' },
  { id: 'blue', label: 'Blue', dark: '#60a5fa', light: '#2563eb' },
  { id: 'teal', label: 'Teal', dark: '#2dd4bf', light: '#0d9488' },
  { id: 'orange', label: 'Orange', dark: '#fb923c', light: '#ea580c' },
  { id: 'rose', label: 'Rose', dark: '#fb7185', light: '#e11d48' },
  { id: 'mono', label: 'Mono', dark: '#f5f5f5', light: '#171717' },
];

const systemLight = window.matchMedia('(prefers-color-scheme: light)');

// ─── Custom accent: any colour, from the hue bar or the exact picker ─────

const CUSTOM_DEFAULT = '#a78bfa';

function validHex(v) {
  const s = String(v || '').trim().toLowerCase();
  return /^#[0-9a-f]{6}$/.test(s) ? s : '';
}

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [n >> 16, (n >> 8) & 255, n & 255];
}

function rgbToHex(rgb) {
  return `#${rgb.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')).join('')}`;
}

/** [r,g,b] 0–255 → [h 0–360, s 0–100, l 0–100] */
function rgbToHsl([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l * 100];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return [h * 60, s * 100, l * 100];
}

function hslToRgb(h, s, l) {
  s /= 100; l /= 100;
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [f(0) * 255, f(8) * 255, f(4) * 255];
}

function luminance(rgb) {
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/**
 * The picked colour, nudged so it stays readable on this theme: same hue and
 * saturation, lightness walked until it contrasts like the presets do
 * (luminance ≤ 0.25 on light, ≥ 0.3 on the dark themes). Yellow on white
 * gets deeper; navy on black gets lighter.
 */
function customAccentVars(hex, theme) {
  const [h, s, l] = rgbToHsl(hexToRgb(hex));
  const light = theme === 'light' || theme === 'soft';
  let L = l;
  for (let i = 0; i < 100 && L > 5 && L < 95; i++) {
    const lum = luminance(hslToRgb(h, s, L));
    if (light ? lum <= 0.25 : lum >= 0.3) break;
    L += light ? -1 : 1;
  }
  const main = hslToRgb(h, s, L);
  const deep = hslToRgb(h, s, Math.max(4, L - (light ? 8 : 12)));
  return {
    rgb: main.map(Math.round).join(', '),
    swatch: rgbToHex(main),
    deep: rgbToHex(deep),
    on: luminance(deep) > 0.45 ? '#0a0a0a' : '#ffffff',
  };
}

/** Hue-bar position → colour, keeping the saturation / lightness already picked. */
function hueToHex(h) {
  const [, s, l] = rgbToHsl(hexToRgb(prefs.custom));
  return rgbToHex(hslToRgb(h, s < 30 ? 80 : s, Math.max(45, Math.min(70, l))));
}

// Old builds stored the soft themes as modes of their own ('dim', 'soft'):
// move them to mode + soft once, so the next save keeps the soft look.
const LEGACY_SOFT = { dim: 'dark', soft: 'light' };
let savedMode = storageGet('jarvis-theme', 'dark');
if (LEGACY_SOFT[savedMode]) {
  savedMode = LEGACY_SOFT[savedMode];
  storageSet('jarvis-theme', savedMode);
  storageSet('jarvis-soft', '1');
}

export const prefs = {
  mode: MODES.some((m) => m.id === savedMode) ? savedMode : 'dark',
  soft: storageGet('jarvis-soft', '0') === '1',
  glass: storageGet('jarvis-glass', '0') === '1',
  accent: storageGet('jarvis-accent', 'green'),
  custom: validHex(storageGet('jarvis-accent-custom', '')) || CUSTOM_DEFAULT,
  alert: storageGet('jarvis-alert', '1') !== '0',
  compact: storageGet('jarvis-compact', '0') === '1',
};

/** 'light' | 'soft' | 'dim' | 'dark' — what is actually on screen. */
export function resolvedTheme() {
  const light = prefs.mode === 'system' ? systemLight.matches : prefs.mode === 'light';
  if (light) return prefs.soft ? 'soft' : 'light';
  return prefs.soft ? 'dim' : 'dark';
}

export function isLightTheme() {
  return LIGHT_THEMES.has(resolvedTheme());
}

function apply() {
  const root = document.documentElement;
  const theme = resolvedTheme();
  // Soft light is a light theme with warmer surfaces: it wears data-theme
  // "light" (so every light rule in the CSS applies) plus data-variant="soft"
  // for the handful of token overrides in tokens.css.
  root.dataset.theme = theme === 'soft' ? 'light' : theme;
  if (theme === 'soft') root.dataset.variant = 'soft';
  else delete root.dataset.variant;
  // Frosted glass is an independent surface choice, not a theme: it rides
  // on its own attribute so it combines with any mode AND soft contrast.
  if (prefs.glass) root.dataset.surface = 'glass';
  else delete root.dataset.surface;
  const custom = prefs.accent === 'custom';
  root.dataset.accent = custom || ACCENTS.some((a) => a.id === prefs.accent) ? prefs.accent : 'green';
  // A custom accent is set inline (beats every preset rule in tokens.css).
  if (custom) {
    const v = customAccentVars(prefs.custom, theme);
    root.style.setProperty('--accent-rgb', v.rgb);
    root.style.setProperty('--accent-deep', v.deep);
    root.style.setProperty('--on-accent', v.on);
  } else {
    ['--accent-rgb', '--accent-deep', '--on-accent'].forEach((p) => root.style.removeProperty(p));
  }
  document.body.classList.toggle('is-compact', prefs.compact);
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_COLOR[theme]);
  patchStore({ theme, themeMode: prefs.mode, accent: prefs.accent });
}

/**
 * Where a theme change "starts": the pointer for a real click, else the
 * clicked control's centre, else the sidebar theme button (keyboard, palette).
 */
function originOf(src) {
  if (src && typeof src.clientX === 'number' && (src.clientX || src.clientY)) {
    return { x: src.clientX, y: src.clientY };
  }
  const el = src?.currentTarget || src?.target || (src instanceof Element ? src : null) || $('theme-btn');
  const r = el?.getBoundingClientRect?.();
  if (r && r.width && r.right > 0 && r.left < window.innerWidth) {
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }
  return { x: window.innerWidth / 2, y: 0 };
}

/** The new colours spread out in a circle from `origin` (View Transitions). */
function withReveal(src, change) {
  if (!document.startViewTransition || reducedMotion() || document.hidden) {
    change();
    return;
  }
  const { x, y } = originOf(src);
  const r = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
  try {
    const vt = document.startViewTransition(change);
    vt.ready.then(() => {
      document.documentElement.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] },
        { duration: 560, easing: 'cubic-bezier(0.22, 1, 0.36, 1)', pseudoElement: '::view-transition-new(root)' },
      );
    }).catch(() => {});
  } catch {
    change();
  }
}

/** Apply a mode / soft change; the reveal only plays when the colours change. */
function retheme(before, src) {
  const run = () => {
    apply();
    paintAppearance();
  };
  if (resolvedTheme() !== before) withReveal(src, run);
  else run();
}

export function setMode(mode, src) {
  const before = resolvedTheme();
  prefs.mode = MODES.some((m) => m.id === mode) ? mode : 'dark';
  storageSet('jarvis-theme', prefs.mode);
  retheme(before, src);
}

export function setSoft(on, src) {
  const before = resolvedTheme();
  prefs.soft = on;
  storageSet('jarvis-soft', on ? '1' : '0');
  retheme(before, src);
}

/**
 * Frosted glass. The resolved theme name is unchanged, so retheme() would
 * skip the reveal — use withReveal directly so the switch still feels live.
 */
export function setGlass(on, src) {
  if (prefs.glass === on) return;
  prefs.glass = on;
  storageSet('jarvis-glass', on ? '1' : '0');
  withReveal(src, () => {
    apply();
    paintAppearance();
  });
}

export function setAccent(accent, src) {
  if (accent === prefs.accent) return;
  prefs.accent = accent;
  storageSet('jarvis-accent', accent);
  withReveal(src, () => {
    apply();
    paintAppearance();
  });
}

/**
 * Use `hex` as the accent. `live` (dragging the hue bar) recolours the page
 * on every move without the reveal animation.
 */
export function setCustomAccent(hex, { src, live = false } = {}) {
  const value = validHex(hex);
  if (!value) return;
  const wasCustom = prefs.accent === 'custom';
  prefs.custom = value;
  prefs.accent = 'custom';
  storageSet('jarvis-accent-custom', value);
  storageSet('jarvis-accent', 'custom');
  if (live) {
    apply();
    if (wasCustom) syncCustomAccent();
    else paintAccents();
    return;
  }
  withReveal(wasCustom ? null : src, () => {
    apply();
    paintAppearance();
  });
}

/** Quick flip between light and dark (sidebar button, palette, Alt+T); soft carries over. */
export function toggleTheme(src) {
  setMode(isLightTheme() ? 'dark' : 'light', src);
}

function notificationsUsable() {
  return 'Notification' in window && window.isSecureContext;
}

async function setAlert(on) {
  prefs.alert = on;
  storageSet('jarvis-alert', on ? '1' : '0');
  if (on && notificationsUsable() && Notification.permission === 'default') {
    try {
      await Notification.requestPermission();
    } catch { /* ignored */ }
  }
  paintAppearance();
}

function setCompact(on) {
  prefs.compact = on;
  storageSet('jarvis-compact', on ? '1' : '0');
  apply();
}

// ─── Reply-ready alert ────────────────────────────────────────────────────

let restoreTitle = null;

/** Called when a turn finishes. Flags the tab (and notifies) if nobody is looking. */
export function replyReady(summary = '') {
  if (!prefs.alert || !document.hidden) return;
  if (restoreTitle === null) restoreTitle = document.title;
  document.title = '✓ Reply ready — Jarvis';
  if (notificationsUsable() && Notification.permission === 'granted') {
    try {
      const n = new Notification('Jarvis finished', {
        body: summary || 'Your reply is ready.',
        tag: 'jarvis-reply',
      });
      n.onclick = () => {
        window.focus();
        n.close();
      };
    } catch { /* some mobile browsers only allow service-worker notifications */ }
  }
  haptic(60);
}

/**
 * Jarvis is blocked on an answer (approval, question, value). Nothing moves
 * until someone responds, so this matters even more than a finished reply.
 */
export function needsAttention(body) {
  if (!prefs.alert || !document.hidden) return;
  if (restoreTitle === null) restoreTitle = document.title;
  document.title = '(!) Jarvis needs you';
  if (notificationsUsable() && Notification.permission === 'granted') {
    try {
      const n = new Notification('Jarvis is waiting for you', {
        body: body || 'Open Jarvis to respond.',
        tag: 'jarvis-waiting',
        requireInteraction: true,
      });
      n.onclick = () => {
        window.focus();
        n.close();
      };
    } catch { /* see replyReady */ }
  }
  haptic([40, 60, 40]);
}

export function isAlertTitle() {
  return restoreTitle !== null;
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && restoreTitle !== null) {
    document.title = restoreTitle;
    restoreTitle = null;
  }
});

// ─── Appearance dialog ────────────────────────────────────────────────────

function paintAppearance() {
  const modes = $('mode-grid');
  if (!modes) return;
  // Previews show what each mode looks like with the current soft setting.
  const lightLook = prefs.soft ? 'soft' : 'light';
  const darkLook = prefs.soft ? 'dim' : 'dark';
  const look = (cls) => `<span class="mode-look mode-${cls}"><i></i><i></i><i></i></span>`;
  const preview = { system: look(lightLook) + look(darkLook), light: look(lightLook), dark: look(darkLook) };
  modes.innerHTML = MODES.map((m) => `
    <button type="button" class="mode-card" role="radio" data-mode="${m.id}" aria-checked="${prefs.mode === m.id}">
      <span class="mode-preview${m.id === 'system' ? ' is-split' : ''}" aria-hidden="true">${preview[m.id]}</span>
      <span class="mode-label">${icon(m.icon)}<span>${escapeHtml(m.label)}</span></span>
    </button>`).join('');
  modes.querySelectorAll('[data-mode]').forEach((btn) => {
    btn.addEventListener('click', (e) => setMode(btn.dataset.mode, e));
  });
  const soft = $('sw-soft');
  if (soft) soft.checked = prefs.soft;
  const softSub = $('soft-sub');
  if (softSub) softSub.textContent = SOFT_SUB[prefs.mode];
  const glass = $('sw-glass');
  if (glass) glass.checked = prefs.glass;

  paintAccents();

  const alert = $('sw-alert');
  if (alert) alert.checked = prefs.alert;
  const sub = $('alert-sub');
  if (sub) {
    sub.textContent = notificationsUsable()
      ? (Notification.permission === 'denied' ? 'Notifications are blocked; the tab title still changes' : 'Notification + tab title while this tab is hidden')
      : 'Changes the tab title (browser notifications need localhost or HTTPS)';
  }
  const compact = $('sw-compact');
  if (compact) compact.checked = prefs.compact;
}

function paintAccents() {
  const accents = $('accent-grid');
  if (!accents) return;
  const light = isLightTheme();
  const custom = customAccentVars(prefs.custom, resolvedTheme());
  const chips = [...ACCENTS.map((a) => ({ ...a, swatch: light ? a.light : a.dark })), { id: 'custom', label: 'Custom', swatch: custom.swatch }];
  accents.innerHTML = chips.map((a) => `
    <button type="button" class="accent-chip${a.id === 'custom' ? ' is-custom' : ''}" role="radio" data-accent="${a.id}" aria-checked="${prefs.accent === a.id}" style="--swatch:${a.swatch}">
      <span class="accent-dot">${prefs.accent === a.id ? icon('check') : ''}</span>
      <span>${escapeHtml(a.label)}</span>
    </button>`).join('');
  accents.querySelectorAll('[data-accent]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      if (btn.dataset.accent === 'custom') setCustomAccent(prefs.custom, { src: e });
      else setAccent(btn.dataset.accent, e);
    });
  });
  syncCustomAccent();
}

/** True while the hue bar itself is the one changing the colour. */
let hueDriving = false;

/** Hue bar, exact picker and the Custom chip follow the custom colour. */
function syncCustomAccent() {
  const custom = customAccentVars(prefs.custom, resolvedTheme());
  const hue = $('accent-hue');
  if (hue) {
    const [h] = rgbToHsl(hexToRgb(prefs.custom));
    if (!hueDriving) hue.value = String(Math.round(h));
    hue.style.setProperty('--pick', custom.swatch);
  }
  const exact = $('accent-exact');
  if (exact) exact.value = prefs.custom;
  $('hue-row')?.classList.toggle('is-active', prefs.accent === 'custom');
  $('hue-row')?.style.setProperty('--pick', custom.swatch);
  const chip = document.querySelector('#accent-grid [data-accent="custom"]');
  if (chip) chip.style.setProperty('--swatch', custom.swatch);
}

export function openAppearance() {
  paintAppearance();
  openModal('appearance');
}

export function initTheme() {
  apply();
  systemLight.addEventListener?.('change', () => {
    if (prefs.mode === 'system') {
      apply();
      paintAppearance();
    }
  });
  // Dragging recolours the page live; letting go settles the dialog.
  $('accent-hue')?.addEventListener('input', (e) => {
    hueDriving = true;
    try {
      setCustomAccent(hueToHex(Number(e.target.value)), { live: true });
    } finally {
      hueDriving = false;
    }
  });
  $('accent-hue')?.addEventListener('change', () => {
    hueDriving = true;
    try {
      paintAccents();
    } finally {
      hueDriving = false;
    }
  });
  $('accent-exact')?.addEventListener('input', (e) => setCustomAccent(e.target.value, { live: true }));
  $('accent-exact')?.addEventListener('change', paintAccents);
  $('sw-soft')?.addEventListener('change', (e) => setSoft(e.target.checked, e));
  $('sw-glass')?.addEventListener('change', (e) => setGlass(e.target.checked, e));
  $('sw-alert')?.addEventListener('change', (e) => setAlert(e.target.checked));
  $('sw-compact')?.addEventListener('change', (e) => setCompact(e.target.checked));
}
