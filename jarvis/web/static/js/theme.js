/** Appearance: colour themes, light / dark / system mode, soft contrast, accent colour, device preferences */
import { $, escapeHtml, storageGet, storageSet, reducedMotion, haptic } from './utils.js';
import { icon } from './icons.js';
import { patchStore } from './store.js';
import { openModal } from './modal.js';
import { api, hasToken } from './api.js';
import { notifyLocal, pushActive } from './pwa.js';

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

const THEME_COLOR = { light: '#fafafa', soft: '#f4f1ea', dim: '#18181b', dark: '#0a0a0b' };
export const THEME_LABEL = { light: 'Light', soft: 'Soft light', dim: 'Soft dark', dark: 'Dark' };

/**
 * Colour themes (Appearance → Theme). Each one is a palette in tokens.css
 * (`data-palette`) with a fixed scheme, so it replaces mode + soft contrast;
 * accent, glass and layout still apply on top. bg / surface / accent only
 * draw the preview card and the browser's theme-color. Keep in step with the
 * SCHEMES table in index.html's boot script.
 */
export const PALETTES = [
  { id: 'midnight', label: 'Midnight', scheme: 'dark', bg: '#0b1020', surface: '#151d3b', accent: '#60a5fa' },
  { id: 'nord', label: 'Nord', scheme: 'dark', bg: '#2e3440', surface: '#3b4252', accent: '#88c0d0' },
  { id: 'dracula', label: 'Dracula', scheme: 'dark', bg: '#1e1f29', surface: '#2b2d3b', accent: '#bd93f9' },
  { id: 'forest', label: 'Forest', scheme: 'dark', bg: '#0c130f', surface: '#16231b', accent: '#4ade80' },
  { id: 'ember', label: 'Ember', scheme: 'dark', bg: '#15100d', surface: '#241c16', accent: '#fb923c' },
  { id: 'rose', label: 'Rosé', scheme: 'dark', bg: '#170f16', surface: '#271b26', accent: '#f472b6' },
  { id: 'claude', label: 'Claude Code', scheme: 'dark', bg: '#262624', surface: '#3d3b38', accent: '#d97757' },
  { id: 'opencode', label: 'OpenCode', scheme: 'dark', bg: '#0a0a0a', surface: '#242424', accent: '#fab283' },
  { id: 'vscode', label: 'VS Code', scheme: 'dark', bg: '#1e1e1e', surface: '#37373d', accent: '#007acc' },
  { id: 'solar', label: 'Solar', scheme: 'light', bg: '#fdf6e3', surface: '#eee8d5', accent: '#1f6fb0' },
  { id: 'sky', label: 'Sky', scheme: 'light', bg: '#f3f8fe', surface: '#dde9f7', accent: '#1d62c9' },
];

function paletteById(id) {
  return PALETTES.find((p) => p.id === id) || null;
}

/** Themes that use the light palette (tokens.css keys off data-theme). */
const LIGHT_THEMES = new Set(['light', 'soft']);

export const ACCENTS = [
  { id: 'violet', label: 'Violet', dark: '#8b7cff', light: '#6d5dfc' },
  { id: 'green', label: 'Green', dark: '#4ade80', light: '#16a34a' },
  { id: 'blue', label: 'Blue', dark: '#60a5fa', light: '#2563eb' },
  { id: 'teal', label: 'Teal', dark: '#2dd4bf', light: '#0d9488' },
  { id: 'orange', label: 'Orange', dark: '#fb923c', light: '#ea580c' },
  { id: 'rose', label: 'Rose', dark: '#fb7185', light: '#e11d48' },
  { id: 'mono', label: 'Mono', dark: '#f5f5f5', light: '#171717' },
];

const systemLight = window.matchMedia('(prefers-color-scheme: light)');
/** System "Reduce transparency": frosted glass keeps its palette, panels go solid (tokens.css). */
const lessTransparency = window.matchMedia('(prefers-reduced-transparency: reduce)');

const GLASS_SUB = 'Translucent panels; messages scroll under them';
const GLASS_SUB_SOLID = 'Your system asks for less transparency, so panels stay solid';

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

export const SOFT_AMT_MIN = 10;
export const SOFT_AMT_MAX = 100;

/** Saved soft-contrast strength, clamped; anything unreadable means full strength. */
function validSoftAmt(v) {
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? Math.max(SOFT_AMT_MIN, Math.min(SOFT_AMT_MAX, n)) : SOFT_AMT_MAX;
}

/** Frosted glass opacity (Appearance → Frosted glass → Opacity), percent. Unset = the
 * built-in per-theme translucency (tokens.css); set = one value for every glass panel. */
export const GLASS_OP_MIN = 20;
export const GLASS_OP_MAX = 100;
export const GLASS_OP_DEFAULT = 72;

function validGlassOp(v) {
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? Math.max(GLASS_OP_MIN, Math.min(GLASS_OP_MAX, n)) : null;
}

/** Appearance → Layout. Each is a root attribute tokens.css reads; the default sets none. */
export const LAYOUT = {
  text: { key: 'jarvis-text', attr: 'text', def: 'm', opts: [['s', 'Small'], ['m', 'Default'], ['l', 'Large']] },
  width: { key: 'jarvis-width', attr: 'width', def: 'default', opts: [['narrow', 'Narrow'], ['default', 'Default'], ['wide', 'Wide']] },
  radius: { key: 'jarvis-radius', attr: 'radius', def: 'default', opts: [['sharp', 'Sharp'], ['default', 'Default'], ['round', 'Round']] },
  font: { key: 'jarvis-font', attr: 'font', def: 'geist', opts: [['geist', 'Geist'], ['system', 'System']] },
  // Appearance → Background: a faint pattern behind the chat (tokens.css); 'none' = the plain colour.
  pattern: { key: 'jarvis-pattern', attr: 'pattern', def: 'none', opts: [['none', 'None'], ['dots', 'Dots'], ['grid', 'Grid'], ['lines', 'Lines']] },
};

function savedLayout(name) {
  const spec = LAYOUT[name];
  const v = storageGet(spec.key, spec.def);
  return spec.opts.some(([id]) => id === v) ? v : spec.def;
}

export const prefs = {
  mode: MODES.some((m) => m.id === savedMode) ? savedMode : 'dark',
  palette: paletteById(storageGet('jarvis-palette', ''))?.id || '',
  soft: storageGet('jarvis-soft', '0') === '1',
  softAmt: validSoftAmt(storageGet('jarvis-soft-amt', String(SOFT_AMT_MAX))),
  glass: storageGet('jarvis-glass', '0') === '1',
  glassOp: validGlassOp(storageGet('jarvis-glass-op', '')),
  layout: Object.fromEntries(Object.keys(LAYOUT).map((k) => [k, savedLayout(k)])),
  accent: storageGet('jarvis-accent', 'violet'),
  custom: validHex(storageGet('jarvis-accent-custom', '')) || CUSTOM_DEFAULT,
  alert: storageGet('jarvis-alert', '1') !== '0',
  compact: storageGet('jarvis-compact', '0') === '1',
  /** Appearance → Theme: whether the theme cards are shown (hiding them keeps the chosen theme). */
  themePicker: storageGet('jarvis-theme-picker', '1') !== '0',
};

/** 'light' | 'soft' | 'dim' | 'dark' — what is actually on screen (a colour theme is plain light / dark). */
export function resolvedTheme() {
  const pal = paletteById(prefs.palette);
  if (pal) return pal.scheme;
  const light = prefs.mode === 'system' ? systemLight.matches : prefs.mode === 'light';
  if (light) return prefs.soft ? 'soft' : 'light';
  return prefs.soft ? 'dim' : 'dark';
}

export function isLightTheme() {
  return LIGHT_THEMES.has(resolvedTheme());
}

/** Name of the look on screen: the colour theme, else Light / Soft light / Soft dark / Dark. */
export function themeLabel() {
  return paletteById(prefs.palette)?.label || THEME_LABEL[resolvedTheme()];
}

/** Changes whenever the colours on screen change (palette or resolved theme), so reveals still play. */
function lookKey() {
  return `${prefs.palette}|${resolvedTheme()}`;
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
  // A colour theme rides on its own attribute (tokens.css), over the dark / light scheme above.
  if (prefs.palette) root.dataset.palette = prefs.palette;
  else delete root.dataset.palette;
  // How far the soft palette is blended in (tokens.css reads --soft-amt).
  root.style.setProperty('--soft-amt', String(prefs.softAmt));
  // Frosted glass is an independent surface choice, not a theme: it rides
  // on its own attribute so it combines with any mode AND soft contrast.
  if (prefs.glass) root.dataset.surface = 'glass';
  else delete root.dataset.surface;
  if (prefs.glassOp !== null) {
    root.dataset.glassOp = '';
    root.style.setProperty('--glass-op', `${prefs.glassOp}%`);
  } else {
    delete root.dataset.glassOp;
    root.style.removeProperty('--glass-op');
  }
  for (const [name, spec] of Object.entries(LAYOUT)) {
    const v = prefs.layout[name];
    if (v === spec.def) delete root.dataset[spec.attr];
    else root.dataset[spec.attr] = v;
  }
  const custom = prefs.accent === 'custom';
  root.dataset.accent = custom || ACCENTS.some((a) => a.id === prefs.accent) ? prefs.accent : 'violet';
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
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', paletteById(prefs.palette)?.bg || THEME_COLOR[theme]);
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
  if (lookKey() !== before) withReveal(src, run);
  else run();
}

/** Back to the plain Light / Dark look (picking a mode, soft contrast or the Default card). */
function clearPalette() {
  prefs.palette = '';
  storageSet('jarvis-palette', '');
}

/** A colour theme, or '' for the default Light / Dark look. */
export function setPalette(id, src) {
  const next = paletteById(id)?.id || '';
  if (next === prefs.palette) return;
  const before = lookKey();
  prefs.palette = next;
  storageSet('jarvis-palette', next);
  queuePush();
  retheme(before, src);
}

export function setMode(mode, src) {
  const before = lookKey();
  clearPalette();
  prefs.mode = MODES.some((m) => m.id === mode) ? mode : 'dark';
  storageSet('jarvis-theme', prefs.mode);
  queuePush();
  retheme(before, src);
}

export function setSoft(on, src) {
  const before = lookKey();
  prefs.soft = on;
  storageSet('jarvis-soft', on ? '1' : '0');
  queuePush();
  retheme(before, src);
}

/** Soft-contrast strength from the slider; recolours live, no reveal animation. */
export function setSoftAmount(amount) {
  prefs.softAmt = validSoftAmt(amount);
  storageSet('jarvis-soft-amt', String(prefs.softAmt));
  queuePush();
  apply();
  paintSoftAmount();
}

/**
 * Frosted glass. The resolved theme name is unchanged, so retheme() would
 * skip the reveal — use withReveal directly so the switch still feels live.
 */
export function setGlass(on, src) {
  if (prefs.glass === on) return;
  prefs.glass = on;
  storageSet('jarvis-glass', on ? '1' : '0');
  queuePush();
  withReveal(src, () => {
    apply();
    paintAppearance();
  });
}

/** Glass opacity from the slider (live); `null` goes back to the built-in look. */
export function setGlassOpacity(value) {
  prefs.glassOp = value === null ? null : validGlassOp(value);
  if (prefs.glassOp === null) storageSet('jarvis-glass-op', '');
  else storageSet('jarvis-glass-op', String(prefs.glassOp));
  queuePush();
  apply();
  paintGlassOpacity();
}

/** Text size, chat width, corners, font (Appearance → Layout). */
export function setLayout(name, value) {
  const spec = LAYOUT[name];
  if (!spec || !spec.opts.some(([id]) => id === value) || prefs.layout[name] === value) return;
  prefs.layout[name] = value;
  storageSet(spec.key, value);
  queuePush();
  apply();
  paintLayout();
}

export function isGlass() {
  return prefs.glass;
}

export function toggleGlass(src) {
  setGlass(!prefs.glass, src);
}

export function setAccent(accent, src) {
  if (accent === prefs.accent) return;
  prefs.accent = accent;
  storageSet('jarvis-accent', accent);
  queuePush();
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
  queuePush();
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

// ─── Shared look: every project, address and tab uses the one saved on this computer ───
//
// localStorage is per origin, and each project's Jarvis has its own port (and
// 127.0.0.1 / localhost / LAN / tunnel are different origins too), so the
// browser alone can't keep one theme. The server keeps it (appearance_api.py);
// localStorage stays as the instant copy the pre-paint script in index.html reads.

const PUSH_DELAY_MS = 350;
let pushTimer = 0;
let pushInFlight = false;

/** The saved look in a fixed key order, so two of them compare as strings. */
function lookOf(src) {
  return JSON.stringify({
    palette: src.palette,
    mode: src.mode,
    soft: !!src.soft,
    softAmt: src.softAmt,
    glass: !!src.glass,
    glassOp: src.glassOp ?? null,
    layout: Object.fromEntries(Object.keys(LAYOUT).map((k) => [k, src.layout[k]])),
    accent: src.accent,
    custom: src.custom,
  });
}

/** A look from the server, checked the way a saved one is; null when it isn't usable. */
function validLook(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const layout = raw.layout && typeof raw.layout === 'object' ? raw.layout : {};
  return {
    palette: paletteById(raw.palette)?.id || '',
    mode: MODES.some((m) => m.id === raw.mode) ? raw.mode : 'dark',
    soft: raw.soft === true,
    softAmt: validSoftAmt(raw.softAmt),
    glass: raw.glass === true,
    glassOp: validGlassOp(raw.glassOp),
    layout: Object.fromEntries(Object.entries(LAYOUT).map(([k, spec]) => [k, spec.opts.some(([id]) => id === layout[k]) ? layout[k] : spec.def])),
    accent: raw.accent === 'custom' || ACCENTS.some((a) => a.id === raw.accent) ? raw.accent : 'violet',
    custom: validHex(raw.custom) || prefs.custom,
  };
}

/** Something was chosen: tell the server shortly (a dragged slider sends once, not per tick). */
function queuePush() {
  clearTimeout(pushTimer);
  pushTimer = setTimeout(pushLook, PUSH_DELAY_MS);
}

async function pushLook() {
  pushTimer = 0;
  if (!hasToken()) return;
  pushInFlight = true;
  try {
    await api('/api/appearance', 'POST', { appearance: JSON.parse(lookOf(prefs)) });
  } catch { /* offline: the next change or visit sends it */ } finally {
    pushInFlight = false;
  }
}

/** Mirror a look from the server into prefs + localStorage and repaint — without sending it back. */
function adoptLook(look) {
  const before = lookKey();
  Object.assign(prefs, look, { layout: { ...look.layout } });
  storageSet('jarvis-palette', prefs.palette);
  storageSet('jarvis-theme', prefs.mode);
  storageSet('jarvis-soft', prefs.soft ? '1' : '0');
  storageSet('jarvis-soft-amt', String(prefs.softAmt));
  storageSet('jarvis-glass', prefs.glass ? '1' : '0');
  storageSet('jarvis-glass-op', prefs.glassOp === null ? '' : String(prefs.glassOp));
  for (const [name, spec] of Object.entries(LAYOUT)) storageSet(spec.key, prefs.layout[name]);
  storageSet('jarvis-accent', prefs.accent);
  storageSet('jarvis-accent-custom', prefs.custom);
  retheme(before, null);
}

/** A look arrived (page load, focus, another page's change). Ignored while our own change is still on its way. */
export function applyRemoteLook(raw) {
  if (pushTimer || pushInFlight) return;
  const look = validLook(raw);
  if (look && lookOf(look) !== lookOf(prefs)) adoptLook(look);
}

/**
 * Make this page wear the look saved on this computer. A computer with nothing
 * saved yet takes this browser's choice (once, if one was ever made), so the
 * theme you already picked carries over instead of being reset.
 */
export async function syncAppearance() {
  if (!hasToken() || pushTimer || pushInFlight) return;
  let saved;
  try {
    saved = (await api('/api/appearance')).appearance;
  } catch {
    return;
  }
  if (saved) {
    applyRemoteLook(saved);
  } else if (storageGet('jarvis-palette', null) !== null || storageGet('jarvis-theme', null) !== null) {
    queuePush();
  }
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
  // With push on, the computer alerts this device itself (long turns only, page or no page).
  if (!pushActive()) {
    notifyLocal('Jarvis finished', { body: summary || 'Your reply is ready.', tag: 'jarvis-reply' });
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
  if (!pushActive()) {
    notifyLocal('Jarvis is waiting for you', {
      body: body || 'Open Jarvis to respond.',
      tag: 'jarvis-waiting',
      requireInteraction: true,
    });
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

/** Theme cards: Default (plain Light / Dark, set by Mode) plus one per colour theme. */
function paintThemes() {
  const grid = $('theme-grid');
  if (!grid) return;
  const cards = [
    { id: '', label: 'Default', look: `<span class="mode-look mode-${prefs.soft ? 'dim' : 'dark'}"><i></i><i></i><i></i></span><span class="mode-look mode-${prefs.soft ? 'soft' : 'light'}"><i></i><i></i><i></i></span>`, split: true },
    ...PALETTES.map((p) => ({
      id: p.id,
      label: p.label,
      look: `<span class="mode-look theme-look" style="--pb:${p.bg};--ps:${p.surface};--pa:${p.accent}"><i></i><i></i><i></i></span>`,
    })),
  ];
  grid.innerHTML = cards.map((c) => `
    <button type="button" class="mode-card theme-card" role="radio" data-palette="${c.id}" aria-checked="${prefs.palette === c.id}">
      <span class="mode-preview${c.split ? ' is-split' : ''}" aria-hidden="true">${c.look}</span>
      <span class="mode-label"><span>${escapeHtml(c.label)}</span></span>
    </button>`).join('');
  grid.querySelectorAll('[data-palette]').forEach((btn) => {
    btn.addEventListener('click', (e) => setPalette(btn.dataset.palette, e));
  });
  const hint = $('theme-hint');
  if (hint) hint.textContent = prefs.palette ? 'A colour theme sets its own light or dark look. Pick a mode to go back.' : 'Default follows Mode above.';
  const toggle = $('sw-themes');
  if (toggle) toggle.checked = prefs.themePicker;
  const panel = $('theme-panel');
  if (panel) panel.hidden = !prefs.themePicker;
  const sub = $('themes-sub');
  if (sub) {
    const now = prefs.palette ? `${themeLabel()} is on` : 'Using the default look';
    sub.textContent = prefs.themePicker ? `${now} · switch off to hide` : `${now} · switch on to choose`;
  }
}

function setThemePicker(on) {
  prefs.themePicker = on;
  storageSet('jarvis-theme-picker', on ? '1' : '0');
  paintThemes();
}

function paintAppearance() {
  paintThemes();
  const modes = $('mode-grid');
  if (!modes) return;
  // Previews show what each mode looks like with the current soft setting.
  const lightLook = prefs.soft ? 'soft' : 'light';
  const darkLook = prefs.soft ? 'dim' : 'dark';
  const look = (cls) => `<span class="mode-look mode-${cls}"><i></i><i></i><i></i></span>`;
  const preview = { system: look(lightLook) + look(darkLook), light: look(lightLook), dark: look(darkLook) };
  modes.innerHTML = MODES.map((m) => `
    <button type="button" class="mode-card" role="radio" data-mode="${m.id}" aria-checked="${!prefs.palette && prefs.mode === m.id}">
      <span class="mode-preview${m.id === 'system' ? ' is-split' : ''}" aria-hidden="true">${preview[m.id]}</span>
      <span class="mode-label">${icon(m.icon)}<span>${escapeHtml(m.label)}</span></span>
    </button>`).join('');
  modes.querySelectorAll('[data-mode]').forEach((btn) => {
    btn.addEventListener('click', (e) => setMode(btn.dataset.mode, e));
  });
  const soft = $('sw-soft');
  if (soft) {
    soft.checked = prefs.soft && !prefs.palette;
    soft.disabled = !!prefs.palette;
    soft.closest('.soft-row')?.classList.toggle('is-off', !!prefs.palette);
  }
  const softSub = $('soft-sub');
  if (softSub) softSub.textContent = prefs.palette ? 'Not used by colour themes' : SOFT_SUB[prefs.mode];
  paintSoftAmount();
  const glass = $('sw-glass');
  if (glass) glass.checked = prefs.glass;
  const glassSub = $('glass-sub');
  if (glassSub) glassSub.textContent = lessTransparency.matches ? GLASS_SUB_SOLID : GLASS_SUB;
  paintGlassOpacity();

  paintAccents();
  paintLayout();

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

/** The strength slider only shows while soft contrast is on. */
function paintSoftAmount() {
  const wrap = $('soft-amount');
  if (wrap) wrap.hidden = !prefs.soft || !!prefs.palette;
  const range = $('soft-amt');
  if (range) {
    if (Number(range.value) !== prefs.softAmt) range.value = String(prefs.softAmt);
    range.style.setProperty('--fill', `${((prefs.softAmt - SOFT_AMT_MIN) / (SOFT_AMT_MAX - SOFT_AMT_MIN)) * 100}%`);
  }
  const out = $('soft-amt-val');
  if (out) out.textContent = `${prefs.softAmt}%`;
}

/** The opacity slider shows while frosted glass is on (and the system allows transparency). */
function paintGlassOpacity() {
  const wrap = $('glass-amount');
  if (wrap) wrap.hidden = !prefs.glass || lessTransparency.matches;
  const v = prefs.glassOp ?? GLASS_OP_DEFAULT;
  const range = $('glass-op');
  if (range) {
    if (Number(range.value) !== v) range.value = String(v);
    range.style.setProperty('--fill', `${((v - GLASS_OP_MIN) / (GLASS_OP_MAX - GLASS_OP_MIN)) * 100}%`);
  }
  const out = $('glass-op-val');
  if (out) out.textContent = prefs.glassOp === null ? 'Auto' : `${v}%`;
}

function paintLayout() {
  document.querySelectorAll('#appearance [data-pref]').forEach((seg) => {
    const current = prefs.layout[seg.dataset.pref];
    seg.querySelectorAll('[data-val]').forEach((btn) => {
      btn.setAttribute('aria-pressed', String(btn.dataset.val === current));
    });
  });
}

function paintAccents() {
  const accents = $('accent-grid');
  if (!accents) return;
  const light = isLightTheme();
  const custom = customAccentVars(prefs.custom, resolvedTheme());
  // Violet is the built-in accent: under a colour theme that slot becomes the theme's own.
  const pal = paletteById(prefs.palette);
  const chips = [
    ...ACCENTS.map((a) => (a.id === 'violet' && pal ? { ...a, label: 'Theme', swatch: pal.accent } : { ...a, swatch: light ? a.light : a.dark })),
    { id: 'custom', label: 'Custom', swatch: custom.swatch },
  ];
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
  lessTransparency.addEventListener?.('change', paintAppearance);
  // Coming back to a tab: pick up a theme chosen meanwhile in another project or tab.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) syncAppearance();
  });
  window.addEventListener('focus', syncAppearance);
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
  $('sw-themes')?.addEventListener('change', (e) => setThemePicker(e.target.checked));
  $('sw-soft')?.addEventListener('change', (e) => setSoft(e.target.checked, e));
  $('soft-amt')?.addEventListener('input', (e) => setSoftAmount(e.target.value));
  $('sw-glass')?.addEventListener('change', (e) => setGlass(e.target.checked, e));
  $('glass-op')?.addEventListener('input', (e) => setGlassOpacity(e.target.value));
  $('glass-op')?.addEventListener('dblclick', () => setGlassOpacity(null)); // back to Auto
  $('glass-op-reset')?.addEventListener('click', () => setGlassOpacity(null));
  document.querySelectorAll('#appearance [data-pref]').forEach((seg) => {
    seg.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-val]');
      if (btn) setLayout(seg.dataset.pref, btn.dataset.val);
    });
  });
  $('sw-alert')?.addEventListener('change', (e) => setAlert(e.target.checked));
  $('sw-compact')?.addEventListener('change', (e) => setCompact(e.target.checked));
}
