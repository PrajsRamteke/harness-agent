"""Pinned context + aliases persistence, and markdown export."""
import json, pathlib, time

from ..constants import CONFIG_DIR, PIN_FILE, ALIAS_FILE
from .. import state


def save_pin():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PIN_FILE.write_text(state.pinned_context)


def save_aliases():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ALIAS_FILE.write_text(json.dumps(state.aliases, indent=2))


def export_markdown(path: str) -> str:
    """Dump conversation as a human-readable markdown file."""
    lines = [f"# Claude session — {time.strftime('%Y-%m-%d %H:%M')}", ""]
    for m in state.messages:
        role = m["role"]
        c = m["content"]
        lines.append(f"## {role}")
        if isinstance(c, str):
            lines.append(c)
        else:
            for b in c:
                bd = b.model_dump() if hasattr(b, "model_dump") else b
                t = bd.get("type")
                if t == "text":
                    lines.append(bd.get("text", ""))
                elif t == "thinking":
                    lines.append(f"> *thinking:* {bd.get('thinking','')}")
                elif t == "tool_use":
                    lines.append(f"**🔧 tool:** `{bd.get('name')}` — `{json.dumps(bd.get('input',{}))[:400]}`")
                elif t == "tool_result":
                    body = bd.get("content", "")
                    if isinstance(body, list):
                        body = "".join(x.get("text", "") for x in body if isinstance(x, dict))
                    lines.append(f"```\n{str(body)[:2000]}\n```")
        lines.append("")
    pathlib.Path(path).write_text("\n".join(lines))
    return path
