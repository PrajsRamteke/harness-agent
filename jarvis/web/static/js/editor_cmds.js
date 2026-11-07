/** Line commands for the Files editor (a plain <textarea>): comment / uncomment,
 * block comment, indent, move / copy / delete lines, insert a line, select a line.
 *
 * Pure: each command takes the text and the selection and returns one edit
 *   { from, to, text, selStart, selEnd }
 * — replace value[from, to) with `text`, then select [selStart, selEnd) in the
 * new text — or null when there is nothing to do. files.js applies it through
 * execCommand so the browser's undo keeps working. Runs under Node for tests.
 */

// ─── Comment syntax ───────────────────────────────────────────────────────

const SLASH = { line: '//', block: ['/*', '*/'] };
const HASH = { line: '#', block: null };
const DASH = { line: '--', block: null };
const MARKUP = { line: null, block: ['<!--', '-->'] };
const CSS = { line: null, block: ['/*', '*/'] };

const BY_EXT = {
  js: SLASH, mjs: SLASH, cjs: SLASH, jsx: SLASH, ts: SLASH, tsx: SLASH, mts: SLASH, cts: SLASH,
  json: SLASH, jsonc: SLASH, json5: SLASH,
  c: SLASH, h: SLASH, cc: SLASH, cpp: SLASH, cxx: SLASH, hpp: SLASH, hh: SLASH, m: SLASH, mm: SLASH,
  java: SLASH, kt: SLASH, kts: SLASH, cs: SLASH, swift: SLASH, dart: SLASH, php: SLASH, scala: SLASH,
  groovy: SLASH, gradle: SLASH, go: SLASH, rs: SLASH, zig: { line: '//', block: null }, proto: SLASH,
  scss: SLASH, sass: { line: '//', block: null }, less: SLASH,
  css: CSS,
  py: { line: '#', block: ['"""', '"""'] }, pyi: HASH, pyw: HASH,
  sh: HASH, bash: HASH, zsh: HASH, fish: HASH, ksh: HASH, env: HASH, rb: { line: '#', block: ['=begin', '=end'] },
  rake: HASH, gemspec: HASH, pl: HASH, pm: HASH, r: HASH, ex: HASH, exs: HASH, ps1: { line: '#', block: ['<#', '#>'] },
  yml: HASH, yaml: HASH, toml: HASH, cfg: HASH, conf: HASH, properties: HASH, editorconfig: HASH, gitignore: HASH,
  dockerignore: HASH, tf: { line: '#', block: ['/*', '*/'] }, nix: { line: '#', block: ['/*', '*/'] },
  ini: { line: ';', block: null },
  sql: { line: '--', block: ['/*', '*/'] }, psql: { line: '--', block: ['/*', '*/'] },
  lua: { line: '--', block: ['--[[', ']]'] }, hs: { line: '--', block: ['{-', '-}'] }, elm: { line: '--', block: ['{-', '-}'] },
  html: MARKUP, htm: MARKUP, xhtml: MARKUP, xml: MARKUP, svg: MARKUP, vue: MARKUP, svelte: MARKUP, astro: MARKUP,
  plist: MARKUP, md: MARKUP, mdx: MARKUP, markdown: MARKUP,
  tex: { line: '%', block: null }, erl: { line: '%', block: null }, clj: { line: ';;', block: null },
  lisp: { line: ';', block: null }, el: { line: ';', block: null }, vim: { line: '"', block: null },
  bat: { line: 'REM', block: null }, cmd: { line: 'REM', block: null },
};
const BY_NAME = {
  Dockerfile: HASH, Makefile: HASH, makefile: HASH, Gemfile: HASH, Rakefile: HASH, Procfile: HASH, Brewfile: HASH,
  '.env': HASH, '.gitignore': HASH, '.dockerignore': HASH, '.npmrc': HASH, '.zshrc': HASH, '.bashrc': HASH,
  '.bash_profile': HASH, '.profile': HASH, '.gitattributes': HASH, '.editorconfig': HASH,
};

/** `{ line, block }` comment tokens for a file name, or null when it has none (plain text). */
export function commentFor(name) {
  const n = String(name || '').split('/').pop();
  if (!n) return null;
  if (BY_NAME[n]) return BY_NAME[n];
  if (/^\.env\./.test(n) || /^Dockerfile\./.test(n)) return HASH;
  const dot = n.lastIndexOf('.');
  return dot >= 0 ? BY_EXT[n.slice(dot + 1).toLowerCase()] || null : null;
}

// ─── Helpers ──────────────────────────────────────────────────────────────

// p <= 0 is the first line: lastIndexOf('\n', -1) would still look at index 0 (a leading blank line).
const lineStart = (v, p) => (p <= 0 ? 0 : v.lastIndexOf('\n', p - 1) + 1);
function lineEnd(v, p) {
  const i = v.indexOf('\n', p);
  return i < 0 ? v.length : i;
}

/** The whole lines the selection touches: [a, b) (b at the end of the last line, before its \n).
 *  A selection that ends at the start of a line does not take that line. */
function lineSpan(v, s, e) {
  const a = lineStart(v, s);
  const end = e > s && e === lineStart(v, e) ? e - 1 : e;
  return { a, b: lineEnd(v, Math.max(end, a)) };
}

const indentOf = (line) => line.match(/^[ \t]*/)[0];
const escapeRx = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// ─── Comments ─────────────────────────────────────────────────────────────

/** ⌘/ : comment the lines, or uncomment them when every non-blank line already is. */
export function toggleLineComment(v, s, e, syntax) {
  if (!syntax) return null;
  if (!syntax.line) return syntax.block ? wrapLines(v, s, e, syntax.block) : null;
  const tok = syntax.line;
  const { a, b } = lineSpan(v, s, e);
  const lines = v.slice(a, b).split('\n');
  const body = lines.filter((l) => l.trim());
  const rx = new RegExp(`^([ \\t]*)${escapeRx(tok)} ?`);
  const changes = [];
  let out;
  if (body.length && body.every((l) => rx.test(l))) {
    out = lines.map((l, i) => {
      const m = rx.exec(l);
      if (!m || !l.trim()) return l;
      changes[i] = { at: m[1].length, del: m[0].length - m[1].length, ins: 0 };
      return l.slice(0, m[1].length) + l.slice(m[0].length);
    });
  } else {
    const pad = body.length ? Math.min(...body.map((l) => indentOf(l).length)) : indentOf(lines[0]).length;
    out = lines.map((l, i) => {
      if (!l.trim() && (body.length || lines.length > 1)) return l;
      changes[i] = { at: pad, del: 0, ins: tok.length + 1 };
      return `${l.slice(0, pad)}${tok} ${l.slice(pad)}`;
    });
  }
  return finish(v, a, b, lines, out, changes, s, e);
}

/** Comment-out whole lines with a block pair (CSS, HTML, Markdown), or take it off again. */
function wrapLines(v, s, e, [open, close]) {
  const { a, b } = lineSpan(v, s, e);
  const block = v.slice(a, b);
  const m = new RegExp(`^([ \\t]*)(${escapeRx(open)} ?)([\\s\\S]*?)( ?${escapeRx(close)})([ \\t]*)$`).exec(block);
  if (m) {
    const text = `${m[1]}${m[3]}${m[5]}`;
    const at = a + m[1].length;
    const cut = m[2].length;
    const grow = text.length - block.length;
    const map = (p) => (p >= b ? p + grow : Math.min(a + text.length, p <= at ? p : p - Math.min(p - at, cut)));
    return { from: a, to: b, text, selStart: map(s), selEnd: map(e) };
  }
  const ind = indentOf(block).length;
  const text = `${block.slice(0, ind)}${open} ${block.slice(ind)} ${close}`;
  const shift = open.length + 1;
  const ranged = e > s;
  const move = (p, keep) => (p > a + ind || (p === a + ind && !keep) ? p + shift : p);
  const end = e >= b && ranged ? e + (text.length - block.length) : move(e, false);
  return { from: a, to: b, text, selStart: move(s, ranged), selEnd: end };
}

/** ⌥⇧A : wrap the selection in a block comment (or unwrap it); no selection → an empty one at the caret. */
export function toggleBlockComment(v, s, e, syntax) {
  if (!syntax) return null;
  const pair = syntax.block;
  if (!pair) return toggleLineComment(v, s, e, syntax);
  const [open, close] = pair;
  const sel = v.slice(s, e);
  // The selection is a comment (with or without its spaces): take it off.
  const inner = new RegExp(`^${escapeRx(open)} ?([\\s\\S]*?) ?${escapeRx(close)}$`).exec(sel);
  if (inner) return { from: s, to: e, text: inner[1], selStart: s, selEnd: s + inner[1].length };
  // The comment sits just around the selection.
  const before = v.slice(Math.max(0, s - open.length - 1), s);
  const after = v.slice(e, e + close.length + 1);
  const ob = before.endsWith(`${open} `) ? open.length + 1 : before.endsWith(open) ? open.length : 0;
  const ca = after.startsWith(` ${close}`) ? close.length + 1 : after.startsWith(close) ? close.length : 0;
  if (ob && ca) return { from: s - ob, to: e + ca, text: sel, selStart: s - ob, selEnd: e - ob };
  if (s === e) {
    const text = `${open}  ${close}`;
    return { from: s, to: e, text, selStart: s + open.length + 1, selEnd: s + open.length + 1 };
  }
  const text = `${open} ${sel} ${close}`;
  return { from: s, to: e, text, selStart: s + open.length + 1, selEnd: e + open.length + 1 };
}

// ─── Indent ───────────────────────────────────────────────────────────────

/** ⌘] / ⌘[ : indent or outdent every line the selection touches (the caret's line without one). */
export function shiftLines(v, s, e, unit, outdent) {
  const { a, b } = lineSpan(v, s, e);
  const lines = v.slice(a, b).split('\n');
  const width = unit === '\t' ? 4 : unit.length;
  const changes = [];
  const out = lines.map((l, i) => {
    if (!outdent) {
      if (!l.trim() && lines.length > 1) return l;
      changes[i] = { at: 0, del: 0, ins: unit.length };
      return unit + l;
    }
    let n = 0;
    if (l[0] === '\t') n = 1;
    else n = Math.min(indentOf(l).replace(/\t.*/, '').length, width);
    if (!n) return l;
    changes[i] = { at: 0, del: n, ins: 0 };
    return l.slice(n);
  });
  return finish(v, a, b, lines, out, changes, s, e);
}

// ─── Moving lines ─────────────────────────────────────────────────────────

/** ⌥↑ / ⌥↓ : move the lines up or down past their neighbour; the selection moves along. */
export function moveLines(v, s, e, dir) {
  const { a, b } = lineSpan(v, s, e);
  const block = v.slice(a, b);
  if (dir < 0) {
    if (a === 0) return null;
    const pa = lineStart(v, a - 1);
    const prev = v.slice(pa, a - 1);
    const d = prev.length + 1;
    return { from: pa, to: b, text: `${block}\n${prev}`, selStart: s - d, selEnd: e - d };
  }
  if (b >= v.length) return null;
  const nb = lineEnd(v, b + 1);
  const next = v.slice(b + 1, nb);
  const d = next.length + 1;
  return { from: a, to: nb, text: `${next}\n${block}`, selStart: s + d, selEnd: e + d };
}

/** ⌥⇧↑ / ⌥⇧↓ : copy the lines above or below; the selection goes to the copy below / stays above. */
export function copyLines(v, s, e, dir) {
  const { a, b } = lineSpan(v, s, e);
  const block = v.slice(a, b);
  if (dir < 0) return { from: a, to: a, text: `${block}\n`, selStart: s, selEnd: e };
  const d = block.length + 1;
  return { from: b, to: b, text: `\n${block}`, selStart: s + d, selEnd: e + d };
}

/** ⌘⇧K : delete the lines; the caret keeps its column on the line that takes their place. */
export function deleteLines(v, s, e) {
  const { a, b } = lineSpan(v, s, e);
  const col = s - lineStart(v, s);
  let from = a;
  let to = b;
  if (b < v.length) to = b + 1;           // take the line break after
  else if (a > 0) from = a - 1;           // last line: take the break before
  if (from === to) return null;
  const rest = v.slice(0, from) + v.slice(to);
  const ls = from === a ? a : lineStart(rest, from);
  const le = lineEnd(rest, ls);
  const caret = Math.min(ls + col, le);
  return { from, to, text: '', selStart: caret, selEnd: caret };
}

/** ⌘↵ / ⌘⇧↵ : a new line below / above the caret's line, at its indent, wherever the caret is. */
export function insertLine(v, s, e, above) {
  if (above) {
    const a = lineStart(v, s);
    const ind = indentOf(v.slice(a, lineEnd(v, a)));
    return { from: a, to: a, text: `${ind}\n`, selStart: a + ind.length, selEnd: a + ind.length };
  }
  const end = lineEnd(v, e);
  const ind = indentOf(v.slice(lineStart(v, e), end));
  const p = end + 1 + ind.length;
  return { from: end, to: end, text: `\n${ind}`, selStart: p, selEnd: p };
}

/** Select the caret's line; with whole lines selected, take the next one too. */
export function selectLine(v, s, e) {
  const { a, b } = lineSpan(v, s, e);
  const full = s === a && (e === b + 1 || (e === b && b === v.length)) && e > s;
  if (full) {
    if (e >= v.length) return null;
    const nb = lineEnd(v, e);
    return { from: 0, to: 0, text: '', selStart: s, selEnd: Math.min(v.length, nb + 1) };
  }
  return { from: 0, to: 0, text: '', selStart: a, selEnd: Math.min(v.length, b + 1) };
}

// ─── Shared ending ────────────────────────────────────────────────────────

/** Turn per-line changes into one edit; the selection's ends shift with the text before them.
 *  `changes[i]` = { at: column in old line i, del, ins }. */
function finish(v, a, b, oldLines, newLines, changes, s, e) {
  if (!changes.some(Boolean)) return null;
  const starts = [];
  let off = a;
  for (const line of oldLines) {
    starts.push(off);
    off += line.length + 1;
  }
  // `keep`: the start of a real selection stays before text inserted right at it, so the
  // selection grows to cover it (whole lines selected → the comment markers are selected too).
  const map = (p, keep) => {
    let delta = 0;
    for (let i = 0; i < oldLines.length; i++) {
      const ch = changes[i];
      if (!ch) continue;
      const at = starts[i] + ch.at;
      if (p < at) continue;
      if (p === at) {
        if (ch.del === 0 && !keep) delta += ch.ins;
      } else if (p < at + ch.del) {
        delta += at - p; // inside removed text: snap to where it was
      } else {
        delta += ch.ins - ch.del;
      }
    }
    return p + delta;
  };
  const ranged = e > s;
  return { from: a, to: b, text: newLines.join('\n'), selStart: map(s, ranged), selEnd: map(e, false) };
}
