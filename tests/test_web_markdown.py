"""jarvis/web/static/js/markdown.js under Node — streamed (partial) replies.

The page re-renders a reply on every frame while it streams, so every prefix
of a reply must render, and quickly. ``## `` (a heading with no text yet) once
looped forever and froze the whole page. Skipped when Node isn't installed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.web import handler as web_handler

NODE = shutil.which("node")
MARKDOWN = Path(web_handler.__file__).with_name("static") / "js" / "markdown.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

# markdown.js imports utils.js, which reads a few browser globals at load.
_SHIM = """
globalThis.location = { pathname: '/', search: '', hash: '', host: 'x' };
globalThis.window = globalThis;
globalThis.document = { documentElement: { dataset: {} }, addEventListener() {}, querySelector() { return null; },
  getElementById() { return null; } };
globalThis.localStorage = { getItem() { return null; }, setItem() {} };
globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
"""


def run_js(body: str) -> str:
    script = f"{_SHIM}\nconst md = await import({json.dumps(MARKDOWN.as_uri())});\n{body}"
    res = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=20)
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


REPLIES = [
    "## 0.4\n\n- **Chat tabs** — several chats at once\n- Faster startup",
    "# Plan\n\n1. read\n2. write\n\n> note\n\n---\n\n| a | b |\n|---|---|\n| 1 | 2 |",
    "###### deep\n#\n##   \n- \n> ",
]


def test_every_streaming_prefix_renders():
    out = run_js(f"""
        const replies = {json.dumps(REPLIES)};
        let n = 0;
        for (const text of replies) {{
          for (let k = 1; k <= text.length; k++) {{
            md.renderMarkdown(text.slice(0, k), {{ streaming: true }});
            n++;
          }}
          md.renderMarkdown(text);
        }}
        console.log(n);
    """)
    assert int(out) == sum(len(r) for r in REPLIES)


def test_a_heading_without_text_is_shown_as_text():
    out = run_js("console.log(md.renderMarkdown('## ', { streaming: true }))")
    assert "<h2" not in out and "##" in out
    assert "<h2>Plan</h2>" in run_js("console.log(md.renderMarkdown('## Plan'))")
