"""jarvis/web/static/js/diffview.js — the browser-side diff renderer, run under Node.

The module is pure strings (no DOM, no imports) precisely so it can be checked
here. Skipped when Node isn't installed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis import file_changes as fc
from jarvis.web import handler as web_handler

NODE = shutil.which("node")
DIFFVIEW = Path(web_handler.__file__).with_name("static") / "js" / "diffview.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def run_js(body: str) -> str:
    script = f"import * as dv from {json.dumps(DIFFVIEW.as_uri())};\n{body}"
    res = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


def test_word_marks_highlight_only_what_changed():
    out = json.loads(run_js("""
        console.log(JSON.stringify([
          dv.wordMarks('const total = a + b;', 'const total = a + c;'),
          dv.wordMarks('x = foo(1)', 'x = foobar(1)'),
          dv.wordMarks('completely', 'different!!'),
          dv.wordMarks('<a>', '<b>'),
        ]));
    """))
    assert out[0] == ['const total = a + <mark class="wd">b</mark>;', 'const total = a + <mark class="wd">c</mark>;']
    assert out[1] == ['x = <mark class="wd">foo</mark>(1)', 'x = <mark class="wd">foobar</mark>(1)']   # whole words
    assert out[2] is None                                    # too little in common to help
    assert out[3][0].startswith("&lt;")                      # escaped


def test_renders_the_server_hunks_unified_and_split(tmp_path, monkeypatch):
    """Feed the real ledger output through the real renderer."""
    root = tmp_path.resolve()
    monkeypatch.setattr(fc, "CWD", root)
    fc.reset()
    f = root / "app.py"
    f.write_text("a\nb = 1\nc\n")
    fc.record(f, "a\nb = 0\nc\n", "a\nb = 1\nc\n", "edit")
    hunks = fc.detail(fc.file_id(f))["hunks"]

    out = json.loads(run_js(f"""
        const hunks = {json.dumps(hunks)};
        const u = dv.renderDiff(hunks);
        const s = dv.renderDiff(hunks, {{ view: 'split' }});
        const cut = dv.renderDiff(hunks, {{ limit: 2 }});
        const live = dv.renderDiff(hunks, {{ keys: dv.rowKeys([{{ rows: [['-', 2, null, 'b = 0']] }}]) }});
        console.log(JSON.stringify({{
          unifiedRows: (u.html.match(/class="dv-row[ "]/g) || []).length, shown: u.shown, total: u.total,
          marks: (u.html.match(/<mark class="wd">/g) || []).length,
          splitRows: (s.html.match(/dv-srow/g) || []).length,
          cut: [cut.shown, cut.total],
          liveRows: (live.html.match(/is-live/g) || []).length,
          hunkHead: u.html.includes('@@ −1,3 +1,3 @@'),
        }}));
    """))
    assert out["total"] == out["shown"] == 4                 # context, -, +, context
    assert out["unifiedRows"] == 4
    assert out["marks"] == 2                                 # the 0 → 1 on each side
    assert out["splitRows"] == 3                             # the -/+ pair shares one row
    assert out["cut"] == [2, 4]
    assert out["liveRows"] == 1                              # only the "+" row is new
    assert out["hunkHead"]


def test_empty_and_escaping():
    out = json.loads(run_js("""
        const r = dv.renderDiff([{ old_start: 0, old_len: 0, new_start: 1, new_len: 1, rows: [['+', null, 1, '<script>x</script>']] }]);
        console.log(JSON.stringify({ html: r.html, empty: dv.renderDiff([]).total, gw: dv.gutterWidth([{ old_start: 1200, old_len: 5, new_start: 1200, new_len: 5 }]) }));
    """))
    assert "&lt;script&gt;" in out["html"] and "<script>" not in out["html"]
    assert out["empty"] == 0
    assert out["gw"] == 42
