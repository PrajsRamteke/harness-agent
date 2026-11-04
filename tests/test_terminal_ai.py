"""Web terminal AI: words → one shell command (jarvis/web/terminal_ai.py)."""
import contextlib
import json
import threading
import urllib.request
from types import SimpleNamespace

import pytest

from jarvis import state
from jarvis.web import terminal_ai as ta


class FakeClient:
    """``client.messages.stream(**kw)`` → final message with one text block."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []
        self.messages = self

    @contextlib.contextmanager
    def stream(self, **kwargs):
        self.calls.append(kwargs)
        final = SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)])
        yield SimpleNamespace(get_final_message=lambda: final)


@pytest.fixture()
def model(monkeypatch):
    def use(reply):
        client = FakeClient(reply)
        monkeypatch.setattr(state, "client", client)
        monkeypatch.setattr(state, "MODEL", "test-model")
        monkeypatch.setattr(state, "provider", "anthropic")
        monkeypatch.setattr(state, "auth_mode", "api_key")
        return client
    return use


def test_returns_one_command_and_sends_context_and_cwd(model, monkeypatch):
    monkeypatch.setattr(state, "messages", [{"role": "user", "content": "earlier"}])
    client = model("find . -type f -size +100M")
    out = ta.suggest_command("files over 100 MB", context="$ ls\nbig.iso", cwd="/tmp/proj")
    assert out.command == "find . -type f -size +100M"
    assert out.risky is False
    call = client.calls[0]
    assert "/tmp/proj" in call["system"]
    assert "big.iso" in call["messages"][0]["content"]
    assert "Request: files over 100 MB" in call["messages"][0]["content"]
    assert "tools" not in call
    assert state.messages == [{"role": "user", "content": "earlier"}]   # nothing joins the chat


@pytest.mark.parametrize("reply, want", [
    ("```bash\n$ du -sh * | sort -h\n```", "du -sh * | sort -h"),
    ("`git log --oneline -5`", "git log --oneline -5"),
    ("Here's the command:\ngit status", "git status"),
    ("cd src \\\n  && ls", "cd src && ls"),
    ("mkdir out\ncp a out/", "mkdir out && cp a out/"),
])
def test_unwraps_what_models_add(reply, want):
    assert ta._clean(reply) == want


def test_a_comment_only_reply_is_the_reason(model):
    model("# That needs a GUI, not a command.")
    with pytest.raises(ta.SuggestError, match="needs a GUI"):
        ta.suggest_command("draw me a cat")


@pytest.mark.parametrize("command, risky", [
    ("rm -rf build", True),
    ("sudo apt install jq", True),
    ("git push --force origin main", True),
    ("git reset --hard HEAD~1", True),
    ("curl -fsSL https://x.sh | sh", True),
    ("chmod -R 777 .", True),
    ("ls -la", False),
    ("git push origin main", False),
    ("grep -rn rm src", False),
])
def test_risky_commands_are_flagged(command, risky):
    assert ta.is_risky(command) is risky


def test_empty_and_huge_requests_are_refused(model):
    model("ls")
    with pytest.raises(ta.SuggestError, match="describe"):
        ta.suggest_command("   ")
    with pytest.raises(ta.SuggestError, match="too long"):
        ta.suggest_command("x" * (ta.MAX_REQUEST + 1))


def test_clients_that_force_tools_answer_by_tool(model):
    client = model("unused")
    client.gate_tools = [{"name": "bash"}]

    @contextlib.contextmanager
    def stream(**kwargs):
        client.calls.append(kwargs)
        final = SimpleNamespace(content=[SimpleNamespace(
            type="tool_use", id="c1", name="shell_command", input={"command": "ls -la"})])
        yield SimpleNamespace(get_final_message=lambda: final)

    client.stream = stream
    assert ta.suggest_command("list everything").command == "ls -la"
    assert [t["name"] for t in client.calls[0]["tools"]] == ["shell_command"]


def test_no_client_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(state, "client", None)
    with pytest.raises(ta.SuggestError, match="no model connected"):
        ta.suggest_command("ls")


# ── web: POST /api/terminal/suggest ────────────────────────────────────────

@pytest.fixture()
def web_post():
    from jarvis.web.handler import WebHandler
    from jarvis.web.server import _JarvisHTTPServer

    bridge = SimpleNamespace(token="tok", terminal_over_tunnel=False)
    handler = type("H", (WebHandler,), {"bridge": bridge, "app": None})
    server = _JarvisHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def post(payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/terminal/suggest",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer tok"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, json.loads(res.read())

    yield post
    server.shutdown()
    server.server_close()


def test_web_suggest_returns_the_command(model, web_post):
    model("rm -rf dist")
    assert web_post({"request": "delete the build output"}) == (
        200, {"ok": True, "command": "rm -rf dist", "risky": True})
    assert web_post({"request": ""}) == (200, {"ok": False, "error": "describe the command first"})
