"""MCP auto-connect at startup reports status in the sidebar, not the transcript."""
import asyncio

import pytest


class _FakeConfig:
    def __init__(self, names):
        self._servers = {n: {"type": "stdio", "command": "true"} for n in names}

    def get_auto_connect(self):
        return list(self._servers)

    def get_server(self, name):
        return self._servers.get(name)

    def list_servers(self):
        return dict(self._servers)


@pytest.fixture()
def fake_servers(monkeypatch):
    """Two auto-connect servers: ``good`` connects, ``bad`` fails."""
    import jarvis.mcp.config as mcp_config
    from jarvis.mcp.registry import mcp_registry

    monkeypatch.setattr(mcp_config, "get_config", lambda: _FakeConfig(["good", "bad"]))
    monkeypatch.setattr(mcp_registry, "_health", {})
    monkeypatch.setattr(mcp_registry, "_connecting", set())
    seen: dict[str, str] = {}

    def fake_connect(name, cfg):
        # Status while this server (and the ones after it) are still pending.
        seen[name] = mcp_registry.get_server_health(name)["status"]
        seen["bad-while-good"] = mcp_registry.get_server_health("bad")["status"]
        if name == "bad":
            mcp_registry._record_connect_error(name, "boom")
            return "boom"
        return None

    monkeypatch.setattr(mcp_registry, "connect", fake_connect)
    return seen


def test_auto_connect_marks_queued_servers_connecting(fake_servers):
    from jarvis.mcp.registry import auto_connect_servers, mcp_registry

    changes: list[int] = []
    auto_connect_servers(on_change=lambda: changes.append(1))

    assert fake_servers["good"] == "connecting"
    assert fake_servers["bad"] == "connecting"
    assert len(changes) == 3  # queued, good done, bad done
    assert mcp_registry.get_server_health("bad")["status"] == "failed"
    assert not mcp_registry._connecting


def test_auto_connect_still_prints_failures_when_asked(fake_servers):
    from jarvis.mcp.registry import auto_connect_servers

    printed: list[str] = []
    auto_connect_servers(console_print=printed.append)  # legacy REPL path
    assert printed == ["[red]mcp: failed to connect 'bad': boom[/]"]


def test_tui_shows_mcp_failures_in_sidebar_not_transcript(fake_servers, monkeypatch):
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    import jarvis.updater as updater
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings
    import jarvis.tui.prompt_history as prompt_history
    from jarvis.tui.app import JarvisTUI
    from jarvis.tui.sidebar import SidebarBody

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)

    async def run() -> tuple[str, str]:
        app = JarvisTUI()
        async with app.run_test(size=(170, 45)) as pilot:
            await pilot.pause(0.6)
            await app.workers.wait_for_complete()
            await pilot.pause(0.2)
            transcript = app._transcript().plain_text()
            sidebar = app.query_one(SidebarBody).render().plain
            return transcript, sidebar

    transcript, sidebar = asyncio.run(run())
    assert "failed to connect" not in transcript and "boom" not in transcript
    assert "bad failed" in sidebar
    assert "good" in sidebar
