"""jarvis/web/static/js/keymap.js + editor_cmds.js — keyboard shortcuts and the Files editor's
line commands, run under Node (both are pure modules). Skipped without Node."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.web import handler as web_handler

NODE = shutil.which("node")
STATIC = Path(web_handler.__file__).with_name("static")
JS = STATIC / "js"
KEYMAP = JS / "keymap.js"
EDITOR = JS / "editor_cmds.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

PRELUDE = """
const ev = (o) => ({ key: '', code: '', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, isComposing: false, ...o });
const mod = K.IS_MAC ? { metaKey: true } : { ctrlKey: true };
const out = {};
"""


def run_js(body: str):
    script = (
        f"import * as K from {json.dumps(KEYMAP.as_uri())};\n"
        f"import * as C from {json.dumps(EDITOR.as_uri())};\n"
        f"{PRELUDE}\n{body}\nconsole.log(JSON.stringify(out));"
    )
    res = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_default_keys_match_like_before():
    out = run_js("""
      out.palette = K.matches(ev({ key: 'k', code: 'KeyK', ...mod }), 'app.palette');
      // ⌥ on a Mac makes ⌥N a dead key: the physical key still matches.
      out.altN = K.matches(ev({ key: 'Dead', code: 'KeyN', altKey: true }), 'chat.new');
      out.altShiftN = [K.matches(ev({ key: 'N', code: 'KeyN', altKey: true, shiftKey: true }), 'chat.new'),
                       K.matches(ev({ key: 'N', code: 'KeyN', altKey: true, shiftKey: true }), 'chat.newTab')];
      out.help = K.matches(ev({ key: '?', code: 'Slash', shiftKey: true }), 'app.shortcuts');
      out.helpOtherLayout = K.matches(ev({ key: '?', code: 'Minus', shiftKey: true }), 'app.shortcuts');
      out.comment = K.matches(ev({ key: '/', code: 'Slash', ...mod }), 'edit.comment');
      out.commentOtherLayout = K.matches(ev({ key: '/', code: 'Digit7', shiftKey: true, ...mod }), 'edit.comment');
      out.term = K.matches(ev({ key: '`', code: 'Backquote', ctrlKey: true }), 'term.toggle');
      out.f3 = [K.matchAny(ev({ key: 'F3', code: 'F3' }), ['files.findPrev', 'files.findNext']),
                K.matchAny(ev({ key: 'F3', code: 'F3', shiftKey: true }), ['files.findPrev', 'files.findNext'])];
      out.composing = K.matches(ev({ key: 'k', code: 'KeyK', isComposing: true, ...mod }), 'app.palette');
      out.fixedNeverMatch = K.matches(ev({ key: 'Enter', code: 'Enter' }), 'chat.send');
    """)
    assert out["palette"] and out["altN"] and out["help"] and out["helpOtherLayout"]
    assert out["altShiftN"] == [False, True]
    assert out["comment"] and out["commentOtherLayout"] and out["term"]
    assert out["f3"] == ["files.findNext", "files.findPrev"]
    assert not out["composing"] and not out["fixedNeverMatch"]


def test_rebinding_reset_and_validation():
    out = run_js("""
      K.setBindings('chat.new', ['Mod+Shift+O']);
      out.rebound = [K.isCustomized('chat.new'), K.customizedCount(),
                     K.matches(ev({ key: 'Dead', code: 'KeyN', altKey: true }), 'chat.new'),
                     K.matches(ev({ key: 'o', code: 'KeyO', shiftKey: true, ...mod }), 'chat.new')];
      K.setBindings('chat.new', ['Alt+N']);
      out.backToDefault = K.isCustomized('chat.new');
      K.setBindings('chat.new', []);
      out.unbound = [K.bindingsOf('chat.new'), K.hint('chat.new'), K.withHint('New chat', 'chat.new')];
      K.resetBindings('chat.new');
      out.reset = K.bindingsOf('chat.new');
      K.setBindings('app.stop', ['Alt+X']);
      out.fixedUnchanged = K.bindingsOf('app.stop');
      out.reserved = K.problemWith('chat.new', 'Mod+W') !== '';
      out.plain = K.problemWith('chat.new', 'Shift+A') !== '';
      out.fkey = K.problemWith('chat.new', 'F6');
      out.esc = K.problemWith('chat.new', 'Esc') !== '';
      out.plainOk = K.problemWith('app.shortcuts', 'Shift+/');
      out.conflict = K.conflictsFor('chat.newTab', 'Alt+N').map((a) => a.id);
      out.otherScope = K.conflictsFor('edit.comment', 'Alt+N').map((a) => a.id);
      out.record = [K.eventToBinding(ev({ key: '˜', code: 'KeyN', altKey: true })), K.eventToBinding(ev({ key: 'Shift', code: 'ShiftLeft', shiftKey: true }))];
      K.resetAll();
      out.afterResetAll = K.customizedCount();
    """)
    assert out["rebound"] == [True, 1, False, True]
    assert out["backToDefault"] is False
    assert out["unbound"] == [[], "", "New chat"]
    assert out["reset"] == ["Alt+N"]
    assert out["fixedUnchanged"] == ["Esc"]
    assert out["reserved"] and out["plain"] and out["esc"]
    assert out["fkey"] == "" and out["plainOk"] == ""
    assert out["conflict"] == ["chat.new"] and out["otherScope"] == []
    assert out["record"] == ["Alt+N", ""]
    assert out["afterResetAll"] == 0


def test_defaults_have_unique_ids_and_no_conflicts():
    out = run_js("""
      out.unique = new Set(K.ACTIONS.map((a) => a.id)).size === K.ACTIONS.length;
      out.groups = K.ACTIONS.every((a) => K.GROUPS.includes(a.group));
      out.clashes = [];
      for (const a of K.ACTIONS) for (const b of K.bindingsOf(a.id)) {
        const c = K.conflictsFor(a.id, b).filter((o) => !o.fixed);
        if (c.length) out.clashes.push(`${a.id} ${b} ${c.map((x) => x.id)}`);
      }
    """)
    assert out["unique"] and out["groups"]
    assert out["clashes"] == []


APPLY = """
const apply = (v, ed) => (ed ? { v: v.slice(0, ed.from) + ed.text + v.slice(ed.to), s: ed.selStart, e: ed.selEnd } : null);
const sel = (r) => (r ? r.v.slice(0, r.s) + '[' + r.v.slice(r.s, r.e) + ']' + r.v.slice(r.e) : null);
"""


def test_toggle_line_comment_round_trips():
    out = run_js(APPLY + """
      const JS = C.commentFor('src/a.ts'), PY = C.commentFor('x.py'), CSS = C.commentFor('s.css'), HTML = C.commentFor('i.html');
      const v = '  const a = 1;\\n  if (x) {\\n    y();\\n  }\\n';
      let r = apply(v, C.toggleLineComment(v, 4, 4, JS));
      out.caret = sel(r);
      out.caretBack = apply(r.v, C.toggleLineComment(r.v, r.s, r.e, JS)).v === v;
      r = apply(v, C.toggleLineComment(v, 0, 26, JS));
      out.lines = sel(r);
      out.linesBack = apply(r.v, C.toggleLineComment(r.v, r.s, r.e, JS)).v === v;
      const p = 'def f():\\n\\n    return 1\\n';
      r = apply(p, C.toggleLineComment(p, 0, p.length - 1, PY));
      out.py = r.v;
      out.pyBack = apply(r.v, C.toggleLineComment(r.v, r.s, r.e, PY)).v === p;
      const c = 'a {\\n  color: red;\\n}';
      r = apply(c, C.toggleLineComment(c, 6, 6, CSS));
      out.css = r.v;
      out.cssBack = apply(r.v, C.toggleLineComment(r.v, r.s, r.e, CSS)).v === c;
      const h = '<div>\\n  <p>hi</p>\\n</div>';
      r = apply(h, C.toggleLineComment(h, 8, 12, HTML));
      out.html = r.v;
      out.htmlBack = apply(r.v, C.toggleLineComment(r.v, r.s, r.e, HTML)).v === h;
      out.plainText = C.commentFor('notes.txt');
      out.names = [C.commentFor('Dockerfile').line, C.commentFor('a/b.rs').line, C.commentFor('q.sql').line];
    """)
    assert out["caret"] == "  // co[]nst a = 1;\n  if (x) {\n    y();\n  }\n"
    assert out["lines"] == "[  // const a = 1;\n  // if (x) {\n]    y();\n  }\n"
    assert out["caretBack"] and out["linesBack"]
    assert out["py"] == "# def f():\n\n#     return 1\n" and out["pyBack"]
    assert out["css"] == "a {\n  /* color: red; */\n}" and out["cssBack"]
    assert out["html"] == "<div>\n  <!-- <p>hi</p> -->\n</div>" and out["htmlBack"]
    assert out["plainText"] is None
    assert out["names"] == ["#", "//", "--"]


def test_block_comment_and_line_commands():
    out = run_js(APPLY + """
      const JS = C.commentFor('a.js');
      const v = 'const a = 1;';
      let r = apply(v, C.toggleBlockComment(v, 6, 7, JS));
      out.block = sel(r);
      out.blockBack = sel(apply(r.v, C.toggleBlockComment(r.v, r.s, r.e, JS)));
      const m = 'one\\ntwo\\nthree';
      out.up = sel(apply(m, C.moveLines(m, 5, 5, -1)));
      out.down = sel(apply(m, C.moveLines(m, 5, 5, 1)));
      out.downAtEnd = C.moveLines(m, 9, 9, 1);
      out.upAtTop = C.moveLines(m, 1, 1, -1);
      out.copyDown = sel(apply(m, C.copyLines(m, 5, 5, 1)));
      out.copyUp = sel(apply(m, C.copyLines(m, 5, 5, -1)));
      out.delMid = sel(apply(m, C.deleteLines(m, 5, 5)));
      out.delLast = sel(apply(m, C.deleteLines(m, 10, 10)));
      const i = '  a {\\n    b;\\n  }';
      out.below = sel(apply(i, C.insertLine(i, 3, 3, false)));
      out.above = sel(apply(i, C.insertLine(i, 9, 9, true)));
      out.indent = sel(apply(m, C.shiftLines(m, 0, 7, '  ', false)));
      out.outdent = apply(i, C.shiftLines(i, 0, i.length, '  ', true)).v;
      out.selectLine = sel(apply(m, C.selectLine(m, 5, 5)));
      const s1 = apply(m, C.selectLine(m, 5, 5));
      out.selectMore = sel(apply(m, C.selectLine(m, s1.s, s1.e)));
      // A file that starts with a blank line: down then up gives the text back.
      const b = 'import x;\\n\\ndescribe();';
      const d1 = apply(b, C.moveLines(b, 0, 0, 1));
      const u1 = apply(d1.v, C.moveLines(d1.v, d1.s, d1.e, -1));
      out.blankFirst = [d1.v, u1.v, u1.s];
      out.blankLineStart = sel(apply('\\nabc', C.toggleLineComment('\\nabc', 0, 0, C.commentFor('a.js'))));
    """)
    assert out["block"] == "const /* [a] */ = 1;"
    assert out["blockBack"] == "const [a] = 1;"
    assert out["up"] == "t[]wo\none\nthree" and out["down"] == "one\nthree\nt[]wo"
    assert out["downAtEnd"] is None and out["upAtTop"] is None
    assert out["copyDown"] == "one\ntwo\nt[]wo\nthree" and out["copyUp"] == "one\nt[]wo\ntwo\nthree"
    assert out["delMid"] == "one\nt[]hree" and out["delLast"] == "one\ntw[]o"
    assert out["below"] == "  a {\n  []\n    b;\n  }"
    assert out["above"] == "  a {\n    []\n    b;\n  }"
    assert out["indent"] == "[  one\n  two]\nthree"
    assert out["outdent"] == "a {\n  b;\n}"
    assert out["selectLine"] == "one\n[two\n]three" and out["selectMore"] == "one\n[two\nthree]"
    assert out["blankFirst"] == ["\nimport x;\ndescribe();", "import x;\n\ndescribe();", 0]
    assert out["blankLineStart"] == "// []\nabc"


def test_page_wires_the_keymap():
    html = (STATIC / "index.html").read_text()
    assert 'id="keymap"' in html and "/static/css/keymap.css" in html and 'id="shortcut-customize"' in html
    # the editor is a pane of the Settings modal, not a dialog of its own
    start = html.index('<div class="modal" id="settings"')
    assert start < html.index('id="keymap"') < html.index('<div class="modal"', start + 1)
    assert '<div class="modal" id="keymap"' not in html
    assert (STATIC / "css" / "keymap.css").is_file()
    app = (JS / "app.js").read_text()
    assert "initKeymapEditor" in app
    settings = (JS / "settings.js").read_text()
    assert "setKeymapHost(" in settings and "openSettings('keymap')" in settings
    files = (JS / "files.js").read_text()
    assert "./editor_cmds.js" in files and "'edit.comment'" in files
    # Every id the code asks keymap.js about exists in its table.
    ids = set(re.findall(r"id: '([a-z]+\.[A-Za-z]+)'", KEYMAP.read_text()))
    used: set[str] = set()
    for f in JS.glob("*.js"):
        if f.name in {"keymap.js"}:
            continue
        text = f.read_text()
        for call in re.findall(r"(?:matches|hint|withHint|bindingsOf)\([^)]*?'([a-z]+\.[A-Za-z]+)'", text):
            used.add(call)
        for group in re.findall(r"matchAny\([^,]+,\s*\[([^\]]*)\]", text):
            used |= set(re.findall(r"'([a-z]+\.[A-Za-z]+)'", group))
    used |= set(re.findall(r'data-kbd(?:-caps)?="([a-z]+\.[A-Za-z]+)"', html))
    assert used and used <= ids, sorted(used - ids)
