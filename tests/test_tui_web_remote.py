"""TUI side of the web remote: /web dialog, corner QR hide/show, start + stop mid-session."""
from __future__ import annotations

import asyncio
import socket
import urllib.request

import pytest


@pytest.fixture()
def web_app(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")

    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings
    import jarvis.tui.prompt_history as prompt_history
    import jarvis.updater as updater
    from jarvis import state

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    # A throwaway settings file: web.qr writes must never reach the real one.
    fresh = settings.Settings(tmp_path / "settings.json")
    monkeypatch.setattr(settings, "_singleton", fresh)
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)

    # Jarvis started WITHOUT --web, on a free port.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(state, "web_enabled", False)
    monkeypatch.setattr(state, "web_port", port)

    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    monkeypatch.setattr(JarvisTUI, "_copy_to_system_clipboard", lambda self, text: True, raising=False)
    return JarvisTUI


def _get(url: str) -> int:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        return urllib.request.build_opener(NoRedirect).open(url, timeout=3).status
    except urllib.error.HTTPError as e:
        return e.code


def test_web_command_starts_remote_mid_session_and_shows_qr_dialog(web_app):
    from jarvis.tui.web_bar import WebRemoteBar, WebRemoteQR
    from jarvis.tui.web_modal import WebConnectScreen

    async def run() -> None:
        app = web_app()
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            assert app._web_bridge is None  # launched without --web

            app._handle_web_command("/web qr")
            await pilot.pause(0.4)
            assert app._web_bridge is not None and app._web_primary_url
            assert isinstance(app.screen, WebConnectScreen)
            qr = str(app.screen.query_one("#web_qr").render())
            assert "█" in qr or "▀" in qr or "▄" in qr  # a real QR, not the fallback text

            # The server is live and hands out the page.
            port = app._web_server.server_address[1]
            assert _get(f"http://127.0.0.1:{port}/") == 302

            # 'h' unpins the corner QR without closing the dialog.
            corner = app.query_one("#web_qr_overlay", WebRemoteQR)
            assert not corner.has_class("hidden")
            await pilot.press("h")
            await pilot.pause(0.2)
            assert isinstance(app.screen, WebConnectScreen)
            assert corner.has_class("hidden")
            assert app._web_qr_wanted() is False

            await pilot.press("escape")
            await pilot.pause(0.2)
            assert not isinstance(app.screen, WebConnectScreen)
            bar = app.query_one("#webar", WebRemoteBar)
            assert not bar.has_class("hidden") and bar._qr_shown is False

            app._stop_web_remote()

    asyncio.run(run())


def test_clicking_the_corner_qr_hides_it_and_remembers(web_app):
    from jarvis.tui.web_bar import WebRemoteQR

    async def run() -> None:
        app = web_app()
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            assert app._start_web_remote(app._tui_console)
            await pilot.pause(0.2)
            corner = app.query_one("#web_qr_overlay", WebRemoteQR)
            assert not corner.has_class("hidden")

            await pilot.click("#web_qr_overlay")
            await pilot.pause(0.2)
            assert corner.has_class("hidden")
            assert app._web_qr_wanted() is False

            app._handle_web_command("/web show")
            await pilot.pause(0.2)
            assert not corner.has_class("hidden")
            assert app._web_qr_wanted() is True
            app._stop_web_remote()

    asyncio.run(run())


def test_web_stop_frees_the_port_and_unwraps_the_console(web_app):
    from jarvis import console as console_mod
    from jarvis.tui.console_shim import TUIConsole

    async def run() -> None:
        app = web_app()
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            assert app._start_web_remote(app._tui_console)
            port = app._web_server.server_address[1]
            bridge = app._web_bridge
            sub = bridge.subscribe()  # a connected browser

            app._handle_web_command("/web stop")
            await pilot.pause(1.2)  # shutdown runs off the UI thread

            assert app._web_bridge is None and not app._web_primary_url
            assert isinstance(app._tui_console, TUIConsole)
            assert isinstance(console_mod.console, TUIConsole)
            assert app.query_one("#webar").has_class("hidden")
            # The browser's stream was told to end.
            from jarvis.web.bridge import CLOSE_SENTINEL
            assert sub.get_nowait() == CLOSE_SENTINEL
            # Port released: we can bind it again (and /web restarts cleanly).
            with socket.socket() as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
            assert app._start_web_remote(app._tui_console)
            app._stop_web_remote()
            await pilot.pause(0.8)

    asyncio.run(run())


def test_web_anywhere_opens_a_public_link_with_the_token(web_app, tmp_path, monkeypatch):
    import stat

    import jarvis.web.tunnel as tun
    from jarvis.tui.web_modal import WebConnectScreen

    script = tmp_path / "cloudflared"
    script.write_text(
        "#!/bin/sh\n"
        "sleep 0.3\n"
        "echo 'INF |  https://test-anywhere.trycloudflare.com  |'\n"
        "exec sleep 30\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(tun, "binary_for", lambda p: str(script) if p == "cloudflare" else None)
    monkeypatch.setattr(tun, "_doh_resolves", lambda host: True)

    async def run() -> None:
        app = web_app()
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            app._handle_web_command("/web anywhere")
            await pilot.pause(0.2)
            screen = app.screen
            assert isinstance(screen, WebConnectScreen) and screen._mode == "anywhere"
            assert screen._anywhere_state() == "starting"

            for _ in range(40):  # tunnel reports its URL → dialog repaints
                await pilot.pause(0.1)
                if screen._anywhere_state() == "live":
                    break
            assert screen._anywhere_state() == "live"
            token = app._web_bridge.token
            assert app._web_public_link == f"https://test-anywhere.trycloudflare.com/?token={token}"
            assert app._web_bridge.public_host == "test-anywhere.trycloudflare.com"
            await pilot.pause(0.1)
            qr = screen.query_one("#web_qr")
            assert not qr.has_class("-message")  # a QR, not the "starting…" note
            from jarvis.web.qr_ascii import qr_ascii
            assert str(qr.content) == qr_ascii(app._web_public_link)  # token is IN the QR

            # "turn off Anywhere" → back to the local network, process stopped.
            proc = app._web_tunnel._proc
            screen.action_mode("local_off")
            await pilot.pause(0.5)
            assert app._web_tunnel is None and not app._web_public_link
            assert app._web_bridge.public_host == ""
            assert screen._mode == "local"
            for _ in range(30):
                if proc.poll() is not None:
                    break
                await pilot.pause(0.1)
            assert proc.poll() is not None
            app._stop_web_remote()
            await pilot.pause(0.6)

    asyncio.run(run())


def test_anywhere_without_a_tunnel_app_explains_how_to_get_one(web_app, monkeypatch):
    import jarvis.web.tunnel as tun
    from jarvis.tui.web_modal import WebConnectScreen

    monkeypatch.setattr(tun, "binary_for", lambda p: None)

    async def run() -> None:
        app = web_app()
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause(0.3)
            app._handle_web_command("/web anywhere")
            await pilot.pause(0.3)
            screen = app.screen
            assert isinstance(screen, WebConnectScreen)
            assert screen._anywhere_state() == "missing"
            text = str(screen.query_one("#web_qr").render())
            assert "brew install cloudflared" in text
            app._stop_web_remote()
            await pilot.pause(0.6)

    asyncio.run(run())
