"""Switching MCP servers on and off — one server, or MCP as a whole.

The choice lives in settings.json (``mcp.enabled``, ``mcp.disabled``); a server
that is off never connects (whoever asks), offers no tools and is listed to
the model as switched off. Covers the service layer, ``/mcp on|off|enable|
disable``, the web routes and the terminal dialog's switches.
"""
import asyncio

import pytest

from test_tui_mcp_auth import env  # noqa: F401  (hermetic app + config fixture)

BROKEN = {"broken": {"command": "/nonexistent/jarvis-mcp-test-server"}}
TWO = {**BROKEN, "other": {"command": "/nonexistent/jarvis-mcp-test-other"}}


def _run(coro):
    asyncio.run(coro)


# ── service layer ────────────────────────────────────────────────────────


def test_switching_a_server_off_blocks_every_connect(env):  # noqa: F811
    from jarvis.mcp import install, toggle
    from jarvis.mcp.registry import as_prompt_block, mcp_registry

    env.write_servers(BROKEN)
    assert toggle.is_enabled("broken")

    res = install.set_server_enabled("broken", False)
    assert res["ok"] and res["enabled"] is False and res["status"] == "off"
    assert toggle.disabled_servers() == {"broken"}

    health = mcp_registry.get_server_health("broken", {"command": "x"})
    assert health["status"] == "off" and health["enabled"] is False
    assert "turned off" in mcp_registry.connect("broken", {"type": "stdio", "command": "x"})
    assert "turned off by the user" in as_prompt_block()
    assert install.server_status("broken")["status"] == "off"
    row = install.describe_servers()["servers"][0]
    assert row["enabled"] is False and row["health"]["status"] == "off"

    # back on: tries to connect straight away (this one can't start)
    res = install.set_server_enabled("broken", True)
    assert res["ok"] and res["enabled"] is True
    assert res["status"] == "failed"
    assert toggle.disabled_servers() == set()


def test_mcp_off_as_a_whole(env):  # noqa: F811
    from jarvis.mcp import install, toggle
    from jarvis.mcp.registry import MCPServerState, as_prompt_block, mcp_registry

    env.write_servers(TWO)
    mcp_registry._servers["other"] = MCPServerState(config={"type": "stdio"}, connected=True, tools=[])

    res = install.set_mcp_enabled(False)
    assert res["ok"] and res["mcp_enabled"] is False
    assert not toggle.mcp_enabled()
    assert not mcp_registry.is_connected("other")  # disconnected
    assert "turned OFF" in as_prompt_block()
    assert mcp_registry.get_server_health("other")["summary"] == "MCP off"
    assert install.describe_servers()["mcp_enabled"] is False
    # a server's own switch is kept, but it can't connect while MCP is off
    assert install.set_server_enabled("broken", True)["status"] == "off"
    assert "MCP is turned off" in mcp_registry.connect("broken", {"type": "stdio", "command": "x"})

    assert install.set_mcp_enabled(True)["mcp_enabled"] is True
    assert toggle.mcp_enabled()


def test_auto_connect_skips_switched_off_servers(monkeypatch):
    import jarvis.mcp.config as mcp_config
    from jarvis.mcp import registry, toggle

    class FakeConfig:
        def get_auto_connect(self):
            return ["broken", "other"]

        def get_server(self, name):
            return {"type": "stdio", "command": "x"}

    monkeypatch.setattr(mcp_config, "get_config", lambda: FakeConfig())
    tried: list[str] = []
    monkeypatch.setattr(registry.mcp_registry, "connect", lambda name, cfg, **kw: tried.append(name))
    toggle.set_server_enabled("broken", False)
    registry.auto_connect_servers()
    assert tried == ["other"]
    tried.clear()
    toggle.set_mcp_enabled(False)
    registry.auto_connect_servers()
    assert tried == []


def test_removing_a_server_forgets_its_switch(env):  # noqa: F811
    from jarvis.mcp import install, toggle

    env.write_servers(BROKEN)
    install.set_server_enabled("broken", False)
    assert install.remove_mcp("broken", scope="project")["ok"]
    assert toggle.disabled_servers() == set()


def test_settings_validate_the_switches():
    from jarvis.storage.settings import _coerce

    assert _coerce("mcp.enabled", "off") is False
    assert _coerce("mcp.disabled", ["b", "a", "a", " "]) == ["a", "b"]
    with pytest.raises(ValueError):
        _coerce("mcp.disabled", "github")


# ── /mcp commands ────────────────────────────────────────────────────────


def test_slash_commands(env):  # noqa: F811
    from rich.console import Console

    from jarvis.mcp import toggle
    from jarvis.mcp.manager import handle_mcp_command

    env.write_servers(BROKEN)
    con = Console(width=140, record=True)

    con.print(handle_mcp_command("disable broken"))
    assert not toggle.server_enabled("broken")
    con.print(handle_mcp_command("off"))
    assert not toggle.mcp_enabled()
    con.print(handle_mcp_command("status"))
    con.print(handle_mcp_command("on"))
    con.print(handle_mcp_command("enable broken"))
    out = con.export_text()
    assert "Turned broken off" in out and "MCP is off" in out and "switched off: broken" in out
    assert toggle.mcp_enabled() and toggle.server_enabled("broken")


# ── web routes ───────────────────────────────────────────────────────────


def test_web_routes(env):  # noqa: F811
    from jarvis.mcp import toggle
    from jarvis.web import extensions_api as ext

    env.write_servers(BROKEN)
    assert ext.handles("/api/mcp/enable") and ext.handles("/api/mcp/power")
    assert not ext.run_mcp("/api/mcp/enable", {"name": "broken"})["ok"]  # enabled missing
    res = ext.run_mcp("/api/mcp/enable", {"name": "broken", "enabled": False})
    assert res["ok"] and not toggle.server_enabled("broken")
    assert not ext.run_mcp("/api/mcp/enable", {"name": "nope", "enabled": False})["ok"]
    res = ext.run_mcp("/api/mcp/power", {"enabled": False})
    assert res["ok"] and ext.mcp_state()["mcp_enabled"] is False
    ext.run_mcp("/api/mcp/power", {"enabled": True})
    assert toggle.mcp_enabled()


# ── terminal dialog ──────────────────────────────────────────────────────


def _switch_x(screen, row_name: str):
    """Screen-relative (x, y) of a server row's ON / OFF pill."""
    opts = screen.query_one("#mcp_list")
    for y in range(opts.size.height):
        x = 0
        for seg in opts.render_line(y):
            meta = seg.style.meta if seg.style else {}
            if meta.get("mcp_switch") == row_name and seg.text.strip():
                return x + 1, y
            x += len(seg.text)
    raise AssertionError(f"no switch for {row_name}")


def test_tui_space_switches_a_server_and_T_switches_mcp(env, monkeypatch):  # noqa: F811
    from jarvis.mcp import toggle
    from jarvis.tui.mcp_modal import MCPModalScreen

    env.write_servers(BROKEN)

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(170, 48)) as pilot:
            await pilot.pause(0.4)
            app.push_screen(MCPModalScreen())
            await pilot.pause(0.4)
            screen = app.screen
            screen.query_one("#mcp_list").focus()
            assert screen._selected_name() == "broken"
            assert "ON" in str(screen.query_one("#mcp_header").render())

            await pilot.press("space")
            await pilot.pause(0.5)
            assert not toggle.server_enabled("broken")
            assert str(screen.query_one("#mcp_primary").label) == "Turn on"
            assert "off" in str(screen.query_one("#mcp_health").render())

            await pilot.press("enter")  # the primary button turns it back on
            await pilot.pause(0.8)
            assert toggle.server_enabled("broken")

            await pilot.press("T")
            await pilot.pause(0.5)
            assert not toggle.mcp_enabled()
            assert "all servers off" in str(screen.query_one("#mcp_header").render())
            assert str(screen.query_one("#mcp_primary").label) == "Turn MCP on"
            await pilot.press("T")
            await pilot.pause(0.5)
            assert toggle.mcp_enabled()

    _run(run())


def test_tui_clicking_the_pill_switches_without_activating(env, monkeypatch):  # noqa: F811
    from jarvis.mcp import toggle
    from jarvis.tui.mcp_modal import MCPModalScreen

    env.write_servers(TWO)
    activated: list[str] = []
    monkeypatch.setattr(MCPModalScreen, "action_toggle", lambda self: activated.append(self._selected_name()))

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(170, 48)) as pilot:
            await pilot.pause(0.4)
            app.push_screen(MCPModalScreen())
            await pilot.pause(0.4)
            screen = app.screen
            x, y = _switch_x(screen, "other")
            await pilot.click("#mcp_list", offset=(x, y))
            await pilot.pause(0.5)
            assert not toggle.server_enabled("other") and toggle.server_enabled("broken")
            assert activated == []
            assert screen._selected_name() == "other"

            # the header pill is MCP as a whole
            header = screen.query_one("#mcp_header")
            await pilot.click("#mcp_header", offset=(5, 0))
            await pilot.pause(0.5)
            assert not toggle.mcp_enabled()
            assert header is screen.query_one("#mcp_header")

    _run(run())
