/** Syntax colouring for the Files editor and chat code blocks — no library, so it
 * works offline on the LAN like the rest of the remote.
 *
 *   highlight(text, mode) → escaped HTML with <span class="t-…"> tokens
 *   modeFor(fileName)     → a mode for a file ('' when there's nothing to colour)
 *   modeForLang(fence)    → a mode for a Markdown fence label (```py, ```tsx …)
 *
 * It is a fast, forgiving scanner, not a parser: it gets comments, strings,
 * numbers, keywords, calls, tags and CSS right for the common languages, and
 * never throws — unknown input simply stays plain text. Token classes (css:
 * tokens.css "Syntax colours"):
 *   t-c comment · t-s string · t-n number · t-k keyword · t-l literal
 *   t-f function · t-t type · t-p property/key · t-v variable · t-m meta
 *   t-g tag · t-a attribute · t-h heading · t-b bold · t-add / t-del / t-hunk diff
 */

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;' };
const esc = (s) => s.replace(/[&<>]/g, (c) => ESC[c]);
const span = (cls, s) => (s ? `<span class="t-${cls}">${esc(s)}</span>` : '');
const words = (s) => new Set(s.split(/\s+/).filter(Boolean));

// ─── Keyword sets ─────────────────────────────────────────────────────────
const JS_KW = words(`break case catch class const continue debugger default delete do else export extends finally for
  from function if import in instanceof let new of return super switch this throw try typeof var void while with yield
  async await static get set as type interface enum implements private protected public readonly declare namespace
  abstract keyof satisfies infer is`);
const JS_LIT = words('true false null undefined NaN Infinity');
const PY_KW = words(`and as assert async await break class continue def del elif else except finally for from global if
  import in is lambda nonlocal not or pass raise return try while with yield match case print`);
const PY_LIT = words('True False None self cls');
const GO_KW = words(`break case chan const continue default defer else fallthrough for func go goto if import interface
  map package range return select struct switch type var`);
const GO_LIT = words('true false nil iota');
const RS_KW = words(`as async await break const continue crate dyn else enum extern fn for if impl in let loop match mod
  move mut pub ref return static struct super trait type unsafe use where while`);
const RS_LIT = words('true false self Self Some None Ok Err');
const C_KW = words(`abstract auto bool boolean break byte case catch char class const constexpr continue default delete do
  double else enum explicit export extends extern final finally float for friend fun func goto if implements import
  include inline int interface internal let long mutable namespace new noexcept object operator override package private
  protected public record return sealed short signed sizeof static struct super switch template this throw throws try
  typedef typename union unsigned using val var virtual void volatile when where while guard defer protocol extension
  init deinit echo function use async await yield in is as out ref lateinit companion data suspend`);
const C_LIT = words('true false null nil NULL nullptr self');
const SH_KW = words(`if then else elif fi case esac for while until do done in function return local export readonly
  declare unset shift exit set source alias echo printf cd test eval exec trap break continue`);
const RB_KW = words(`def end class module if elsif else unless while until for in do return yield begin rescue ensure
  raise require require_relative include extend attr_accessor attr_reader attr_writer then case when and or not
  lambda proc puts private protected public`);
const RB_LIT = words('true false nil self');
const SQL_KW = words(`select from where and or not insert into values update set delete create table drop alter add index
  primary key foreign references join left right inner outer full cross on as group by order having limit offset union
  all distinct case when then else end is like ilike in exists between default unique view returning with begin commit
  rollback if asc desc constraint check column database schema grant revoke trigger function procedure language returns`);
const SQL_LIT = words('true false null');
const NO_WORDS = new Set();

// ─── Generic scanner (C-like, hash-comment and SQL-style languages) ───────
const RX = {
  block: /\/\*[\s\S]*?(?:\*\/|$)/y,
  slash: /\/\/.*/y,
  hash: /#.*/y,
  dash: /--.*/y,
  semi: /;.*/y,
  triple: /"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)/y,
  tpl: /`(?:\\[\s\S]|[^\\`])*`?/y,
  dq: /"(?:\\.|[^"\\\n])*"?/y,
  sq: /'(?:\\.|[^'\\\n])*'?/y,
  char: /'(?:\\.|[^'\\\n])'/y,
  num: /(?:0[xX][\da-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:\d[\d_]*\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)[a-zA-Z]*/y,
  ident: /[A-Za-z_$][\w$]*/y,
  meta: /@[A-Za-z_][\w.]*/y,
  dollar: /\$\{[^}\n]*\}?|\$[A-Za-z_]\w*|\$[\d@#?*!$-]/y,
  space: /\s+/y,
  other: /[^\w\s$"'`\/#@;-]+|[\s\S]/y,
};

function at(re, text, i) {
  re.lastIndex = i;
  const m = re.exec(text);
  return m && m[0] ? m[0] : '';
}

function nextNonSpace(text, i) {
  while (i < text.length && (text[i] === ' ' || text[i] === '\t')) i += 1;
  return text[i] || '';
}

/** A spec describes one language family; scan() turns text into tokens with it. */
function scan(text, spec) {
  let out = '';
  let i = 0;
  let lineStart = true;
  const ci = spec.caseInsensitive;
  while (i < text.length) {
    let m;
    const c = text[i];
    if (spec.block && c === '/' && (m = at(RX.block, text, i))) out += span('c', m);
    else if (spec.line === '//' && c === '/' && (m = at(RX.slash, text, i))) out += span('c', m);
    else if (spec.line === '#' && c === '#' && (spec.hashAnywhere || lineStart || /\s/.test(text[i - 1] || '\n')) && (m = at(RX.hash, text, i))) out += span('c', m);
    else if (spec.line === '--' && c === '-' && (m = at(RX.dash, text, i))) out += span('c', m);
    else if (spec.semiComment && c === ';' && lineStart && (m = at(RX.semi, text, i))) out += span('c', m);
    else if (spec.triple && (c === '"' || c === "'") && (m = at(RX.triple, text, i))) out += span('s', m);
    else if (spec.tpl && c === '`' && (m = at(RX.tpl, text, i))) out += span('s', m);
    else if (c === '"' && (m = at(RX.dq, text, i))) out += span(spec.keyStrings && nextNonSpace(text, i + m.length) === ':' ? 'p' : 's', m);
    else if (c === "'" && (m = at(spec.charOnly ? RX.char : RX.sq, text, i))) out += span('s', m);
    else if (spec.dollar && c === '$' && (m = at(RX.dollar, text, i))) out += span('v', m);
    else if (spec.meta && c === '@' && (m = at(RX.meta, text, i))) out += span('m', m);
    else if (/\d/.test(c) || (c === '.' && /\d/.test(text[i + 1] || ''))) {
      m = at(RX.num, text, i);
      out += span('n', m);
    } else if ((m = at(RX.ident, text, i))) {
      const key = ci ? m.toLowerCase() : m;
      const after = nextNonSpace(text, i + m.length);
      if (spec.kw.has(key)) out += span('k', m);
      else if (spec.lit.has(key)) out += span('l', m);
      else if (spec.colonKeys && lineStart && after === ':') out += span('p', m);
      else if (spec.eqKeys && lineStart && after === '=') out += span('p', m);
      else if (after === '(') out += span('f', m);
      else if (spec.types !== false && /^[A-Z][a-z0-9]\w*$/.test(m)) out += span('t', m);
      else out += esc(m);
    } else if ((m = at(RX.space, text, i))) {
      out += m;
      if (m.includes('\n')) lineStart = true;
      i += m.length;
      continue;
    } else {
      m = at(RX.other, text, i) || c;
      if (spec.listDash && lineStart && m === '-') out += span('k', m);
      else out += esc(m);
    }
    // Indentation and list dashes ("- key: v") keep a YAML line at its start.
    lineStart = c === '\n' || (spec.listDash && lineStart && m === '-');
    i += m.length;
  }
  return out;
}

const SPECS = {
  js: { line: '//', block: true, tpl: true, meta: true, keyStrings: true, kw: JS_KW, lit: JS_LIT },
  json: { line: '//', block: true, keyStrings: true, kw: NO_WORDS, lit: words('true false null'), types: false },
  py: { line: '#', hashAnywhere: true, triple: true, meta: true, kw: PY_KW, lit: PY_LIT },
  go: { line: '//', block: true, tpl: true, kw: GO_KW, lit: GO_LIT },
  rs: { line: '//', block: true, charOnly: true, meta: false, kw: RS_KW, lit: RS_LIT },
  c: { line: '//', block: true, meta: true, dollar: true, kw: C_KW, lit: C_LIT },
  sh: { line: '#', dollar: true, kw: SH_KW, lit: words('true false'), types: false },
  rb: { line: '#', dollar: false, kw: RB_KW, lit: RB_LIT },
  sql: { line: '--', block: true, caseInsensitive: true, kw: SQL_KW, lit: SQL_LIT, types: false },
  yaml: { line: '#', colonKeys: true, keyStrings: true, listDash: true, kw: NO_WORDS, lit: words('true false null yes no on off ~'), types: false },
  toml: { line: '#', semiComment: true, eqKeys: true, triple: true, kw: NO_WORDS, lit: words('true false'), types: false },
  lua: { line: '--', kw: words('and break do else elseif end for function goto if in local not or repeat return then until while'), lit: words('true false nil self') },
};

// ─── CSS / SCSS / Less ─────────────────────────────────────────────────────
const CSS_RX = {
  block: /\/\*[\s\S]*?(?:\*\/|$)/y,
  line: /\/\/.*/y,
  str: /"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?/y,
  at: /@[\w-]+/y,
  cvar: /--[\w-]+|\$[\w-]+/y,
  num: /[+-]?(?:\d*\.\d+|\d+)(?:[a-zA-Z%]+)?/y,
  hex: /#[\da-fA-F]{3,8}\b/y,
  word: /-?[A-Za-z_][\w-]*/y,
  pseudo: /::?[\w-]+/y,
  cls: /[.#][\w-]+/y,
  imp: /!important\b/y,
  space: /\s+/y,
};

function css(text, scss = false) {
  let out = '';
  let i = 0;
  let depth = 0;
  let ctx = null; // 'sel' | 'prop' | 'val' | 'at' — decided at each statement start
  const decide = (from) => {
    if (depth === 0) return 'sel';
    for (let j = from; j < text.length; j += 1) {
      const ch = text[j];
      if (ch === '{') return 'sel';
      if (ch === ';' || ch === '}') return 'prop';
    }
    return 'prop';
  };
  while (i < text.length) {
    const c = text[i];
    let m;
    if (c === '/' && (m = at(CSS_RX.block, text, i))) out += span('c', m);
    else if (scss && c === '/' && text[i + 1] === '/' && (m = at(CSS_RX.line, text, i))) out += span('c', m);
    else if ((c === '"' || c === "'") && (m = at(CSS_RX.str, text, i))) out += span('s', m);
    else if ((m = at(CSS_RX.space, text, i))) {
      out += m;
    } else if (c === '{') {
      depth += 1; ctx = null; m = c; out += c;
    } else if (c === '}') {
      depth = Math.max(0, depth - 1); ctx = null; m = c; out += c;
    } else if (c === ';') {
      ctx = null; m = c; out += c;
    } else {
      if (!ctx) ctx = c === '@' ? 'at' : decide(i);
      if (c === '@' && (m = at(CSS_RX.at, text, i))) out += span('k', m);
      else if ((c === '-' || c === '$') && (m = at(CSS_RX.cvar, text, i))) out += span(ctx === 'prop' ? 'p' : 'v', m);
      else if (ctx === 'prop') {
        if ((m = at(CSS_RX.word, text, i))) out += span('p', m);
        else { m = c; out += esc(c); if (c === ':') ctx = 'val'; }
      } else if (ctx === 'sel') {
        if ((c === '.' || c === '#') && (m = at(CSS_RX.cls, text, i))) out += span('a', m);
        else if (c === ':' && (m = at(CSS_RX.pseudo, text, i))) out += span('m', m);
        else if ((m = at(CSS_RX.word, text, i))) out += span('g', m);
        else { m = c; out += esc(c); }
      } else { // 'val' and at-rule preludes
        if (c === '#' && (m = at(CSS_RX.hex, text, i))) out += span('n', m);
        else if (c === '!' && (m = at(CSS_RX.imp, text, i))) out += span('k', m);
        else if ((/\d/.test(c) || (/[.+-]/.test(c) && /[\d.]/.test(text[i + 1] || ''))) && (m = at(CSS_RX.num, text, i))) out += span('n', m);
        else if ((m = at(CSS_RX.word, text, i))) out += span(nextNonSpace(text, i + m.length) === '(' ? 'f' : 'l', m);
        else { m = c; out += esc(c); }
      }
    }
    i += m.length;
  }
  return out;
}

// ─── HTML / XML / Vue / Svelte (with <style> and <script> inside) ─────────
const TAG_RX = {
  attr: /[^\s"'>\/=]+/y,
  str: /"[^"]*"?|'[^']*'?/y,
  space: /\s+/y,
};

function markup(text) {
  let out = '';
  let i = 0;
  while (i < text.length) {
    const lt = text.indexOf('<', i);
    if (lt === -1) { out += esc(text.slice(i)); break; }
    out += esc(text.slice(i, lt));
    i = lt;
    if (text.startsWith('<!--', i)) {
      const end = text.indexOf('-->', i + 4);
      const stop = end === -1 ? text.length : end + 3;
      out += span('c', text.slice(i, stop));
      i = stop;
      continue;
    }
    if (text[i + 1] === '!' || text[i + 1] === '?') { // <!doctype …>, <?xml …?>
      const end = text.indexOf('>', i);
      const stop = end === -1 ? text.length : end + 1;
      out += span('m', text.slice(i, stop));
      i = stop;
      continue;
    }
    const open = /<\/?([A-Za-z][\w:.-]*)/y;
    open.lastIndex = i;
    const t = open.exec(text);
    if (!t) { out += '&lt;'; i += 1; continue; }
    const closing = t[0][1] === '/';
    out += `${esc(closing ? '</' : '<')}${span('g', t[1])}`;
    i += t[0].length;
    // Attributes up to the closing > (strings may hold a >).
    while (i < text.length && text[i] !== '>') {
      let m;
      if ((m = at(TAG_RX.space, text, i))) out += m;
      else if ((text[i] === '"' || text[i] === "'") && (m = at(TAG_RX.str, text, i))) out += span('s', m);
      else if (text[i] === '<') break; // an unclosed tag: let the next one start
      else if ((m = at(TAG_RX.attr, text, i))) out += span('a', m);
      else { m = text[i]; out += esc(m); }
      i += m.length;
    }
    if (text[i] === '>') { out += '&gt;'; i += 1; }
    const name = t[1].toLowerCase();
    if (!closing && (name === 'style' || name === 'script')) {
      const end = text.toLowerCase().indexOf(`</${name}`, i);
      const stop = end === -1 ? text.length : end;
      const body = text.slice(i, stop);
      out += name === 'style' ? css(body) : scan(body, SPECS.js);
      i = stop;
    }
  }
  return out;
}

// ─── Markdown (line based) ────────────────────────────────────────────────
const MD_INLINE = /(`+)[^`\n]*?\1|\*\*[^*\n]+\*\*|__[^_\n]+__|\[[^\]\n]*\]\([^)\n]*\)|<!--[\s\S]*?-->/g;

function mdInline(line) {
  let out = '';
  let last = 0;
  MD_INLINE.lastIndex = 0;
  for (let m = MD_INLINE.exec(line); m; m = MD_INLINE.exec(line)) {
    out += esc(line.slice(last, m.index));
    const s = m[0];
    if (s[0] === '`') out += span('s', s);
    else if (s[0] === '[') {
      const cut = s.indexOf('](');
      out += span('a', s.slice(0, cut + 1)) + span('v', s.slice(cut + 1));
    } else if (s[0] === '<') out += span('c', s);
    else out += span('b', s);
    last = m.index + s.length;
  }
  return out + esc(line.slice(last));
}

function markdown(text) {
  let fence = '';
  return text.split('\n').map((line) => {
    const f = line.match(/^\s*(`{3,}|~{3,})/);
    if (fence) {
      if (f && f[1][0] === fence[0] && f[1].length >= fence.length) fence = '';
      return span(f && !fence ? 'm' : 's', line);
    }
    if (f) { fence = f[1]; return span('m', line); }
    if (/^#{1,6}\s/.test(line)) return span('h', line);
    if (/^\s*>/.test(line)) return span('c', line);
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) return span('m', line);
    const li = line.match(/^(\s*)([-*+]|\d+[.)])(\s+)/);
    if (li) return `${li[1]}${span('k', li[2])}${li[3]}${mdInline(line.slice(li[0].length))}`;
    return mdInline(line);
  }).join('\n');
}

// ─── Diff / patch ─────────────────────────────────────────────────────────
function diff(text) {
  return text.split('\n').map((line) => {
    if (line.startsWith('@@')) return span('hunk', line);
    if (/^(\+\+\+|---|diff |index )/.test(line)) return span('m', line);
    if (line[0] === '+') return span('add', line);
    if (line[0] === '-') return span('del', line);
    return esc(line);
  }).join('\n');
}

// ─── Picking a mode ───────────────────────────────────────────────────────
const EXT_MODE = {
  js: 'js', mjs: 'js', cjs: 'js', jsx: 'js', ts: 'js', tsx: 'js', mts: 'js', cts: 'js',
  json: 'json', jsonc: 'json', json5: 'json', webmanifest: 'json', ipynb: 'json',
  py: 'py', pyi: 'py', pyw: 'py',
  go: 'go', rs: 'rs',
  c: 'c', h: 'c', cc: 'c', cpp: 'c', cxx: 'c', hpp: 'c', hh: 'c', m: 'c', mm: 'c', java: 'c', kt: 'c', kts: 'c',
  cs: 'c', swift: 'c', dart: 'c', php: 'c', scala: 'c', groovy: 'c', gradle: 'c', zig: 'c',
  sh: 'sh', bash: 'sh', zsh: 'sh', fish: 'sh', ksh: 'sh', env: 'sh', rb: 'rb', rake: 'rb', gemspec: 'rb',
  sql: 'sql', psql: 'sql', lua: 'lua',
  yml: 'yaml', yaml: 'yaml', toml: 'toml', ini: 'toml', cfg: 'toml', conf: 'toml', properties: 'toml', editorconfig: 'toml',
  css: 'css', scss: 'scss', sass: 'scss', less: 'scss',
  html: 'html', htm: 'html', xhtml: 'html', xml: 'html', svg: 'html', vue: 'html', svelte: 'html', astro: 'html', plist: 'html',
  md: 'md', mdx: 'md', markdown: 'md',
  diff: 'diff', patch: 'diff',
};
const NAME_MODE = {
  Dockerfile: 'sh', Makefile: 'sh', makefile: 'sh', Gemfile: 'rb', Rakefile: 'rb', Procfile: 'sh',
  '.env': 'sh', '.gitignore': 'sh', '.dockerignore': 'sh', '.npmrc': 'toml', '.zshrc': 'sh', '.bashrc': 'sh',
  '.prettierrc': 'json', '.eslintrc': 'json', '.babelrc': 'json',
};
const LANG_MODE = {
  javascript: 'js', typescript: 'js', node: 'js', python: 'py', python3: 'py', golang: 'go', rust: 'rs',
  shell: 'sh', console: 'sh', terminal: 'sh', shellscript: 'sh', ruby: 'rb', postgres: 'sql', postgresql: 'sql',
  mysql: 'sql', sqlite: 'sql', 'c++': 'c', csharp: 'c', 'c#': 'c', kotlin: 'c', objc: 'c', 'objective-c': 'c',
  docker: 'sh', dockerfile: 'sh', make: 'sh', makefile: 'sh', yml: 'yaml', jsonl: 'json', html5: 'html',
  markup: 'html', xml: 'html', vue: 'html', svelte: 'html', md: 'md', markdown: 'md', patch: 'diff', diff: 'diff',
};

export function modeFor(name) {
  if (!name) return '';
  if (NAME_MODE[name]) return NAME_MODE[name];
  if (/^\.env\./.test(name) || /^Dockerfile\./.test(name)) return 'sh';
  const dot = name.lastIndexOf('.');
  return dot > 0 ? EXT_MODE[name.slice(dot + 1).toLowerCase()] || '' : '';
}

export function modeForLang(lang) {
  const l = String(lang || '').trim().toLowerCase();
  if (!l) return '';
  return LANG_MODE[l] || EXT_MODE[l] || '';
}

/** Escaped, coloured HTML for `text` in `mode` (plain escaped text for '' / unknown). */
export function highlight(text, mode) {
  const src = String(text ?? '');
  try {
    if (mode === 'html') return markup(src);
    if (mode === 'css') return css(src);
    if (mode === 'scss') return css(src, true);
    if (mode === 'md') return markdown(src);
    if (mode === 'diff') return diff(src);
    if (SPECS[mode]) return scan(src, SPECS[mode]);
  } catch (err) {
    console.warn('syntax: highlight failed, showing plain text', err);
  }
  return esc(src);
}
