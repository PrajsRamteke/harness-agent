"""Terminal UI for adding skills / MCP servers and signing in to hosted ones.

Covers the sign-in bar above the composer, the clickable sidebar rows, the
add-by-link dialogs and the agent-tool path. Everything runs against a temp
HOME / project (no real config, no network, no MCP processes).
"""
import asyncio
import json
import threading

import pytest


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """Hermetic app class + isolated config dirs and a fresh registry."""
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    import jarvis.mcp.auth as mcp_auth
    import jarvis.mcp.config as mcp_config
    import jarvis.mcp.registry as registry_mod
    import jarvis.mcp.secrets as mcp_secrets
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings
    import jarvis.storage.skill_install as si
    import jarvis.storage.skills as sk
    import jarvis.tui.prompt_history as prompt_history
    import jarvis.updater as updater
    from jarvis import state
    from jarvis.mcp.registry import mcp_registry

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(registry_mod, "auto_connect_servers", lambda console_print=None, **kw: None)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(settings.Settings, "save", lambda self: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    monkeypatch.setattr(state, "save_mcp_config", lambda: None)
    monkeypatch.setattr(state, "save_skills_config", lambda: None)

    monkeypatch.setattr(mcp_config, "MCP_GLOBAL_CONFIG_FILE", tmp_path / "home" / "mcp.json")
    monkeypatch.setattr(mcp_secrets, "SECRETS_FILE", tmp_path / "home" / "mcp_secrets.json")
    monkeypatch.setattr(mcp_auth, "AUTH_DIR", tmp_path / "home" / "mcp-auth")
    gskills = tmp_path / "home" / ".harness" / "skills"
    monkeypatch.setattr(si, "HARNESS_SKILLS_DIR", gskills)
    monkeypatch.setattr(sk, "HARNESS_SKILLS_DIR", gskills)
    monkeypatch.setattr(sk, "CONFIG_DIR", tmp_path / "home" / "cfg")
    sk.invalidate_cache()
    monkeypatch.setattr(state, "global_mcp", False)
    monkeypatch.setattr(state, "global_skills", False)
    monkeypatch.setattr(state, "auto_approve", True)
    mcp_config._config = None

    for name in ("_servers", "_pending", "_health"):
        monkeypatch.setattr(mcp_registry, name, {})
    monkeypatch.setattr(mcp_registry, "_needs_auth", set())
    monkeypatch.setattr(mcp_registry, "_connecting", set())
    monkeypatch.setattr(mcp_auth.coordinator, "_requests", {})

    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)

    class Env:
        app_cls = JarvisTUI
        project = proj
        home = tmp_path / "home"
        global_skills = gskills
        tmp = tmp_path

        @staticmethod
        def write_servers(servers: dict) -> None:
            (proj / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
            mcp_config.reload_config()

    yield Env
    sk.invalidate_cache()
    mcp_config._config = None


DEMO = {"demo": {"type": "http", "url": "https://mcp.example.com/mcp"}}
AUTH_URL = "https://mcp.example.com/authorize?response_type=code&state=abc123"


def _run(coro):
    asyncio.run(coro)


def test_bar_hidden_by_default(env):
    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.pause(0.4)
            assert not app.query_one("#mcp_auth").shown

    _run(run())


def test_bar_shows_for_pending_sign_in_and_clears_on_connect(env, monkeypatch):
    from jarvis.mcp.auth import coordinator
    from jarvis.mcp.registry import MCPServerState, mcp_registry

    env.write_servers(DEMO)

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.pause(0.4)
            bar = app.query_one("#mcp_auth")
            assert not bar.shown

            coordinator.begin("demo", AUTH_URL)  # → registry event → relay → UI
            await pilot.pause(0.6)
            assert bar.shown
            assert bar.message_text().startswith("◈ demo needs sign-in")
            # a live request has a link, so copy / paste are offered
            assert not app.query_one("#mcp_auth_copy").has_class("-off")

            # the server connects (browser callback finished)
            mcp_registry._servers["demo"] = MCPServerState(config={"type": "http"}, connected=True, tools=[])
            coordinator.finish("demo", True, "Connected")
            mcp_registry._emit("connected", "demo")
            await pilot.pause(0.6)
            assert not bar.shown
            assert "connected" in app._transcript().plain_text()

    _run(run())


def test_authenticate_button_opens_browser_and_waits(env, monkeypatch):
    import webbrowser

    from jarvis.mcp.auth import coordinator

    env.write_servers(DEMO)
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.pause(0.4)
            coordinator.begin("demo", AUTH_URL)
            await pilot.pause(0.6)
            await pilot.click("#mcp_auth_go")
            await pilot.pause(0.5)
            assert opened == [AUTH_URL]
            bar = app.query_one("#mcp_auth")
            assert "waiting for the browser" in bar.message_text()
            assert not app.query_one("#mcp_auth_cancel").has_class("-off")

            # Cancel really stops the sign-in
            await pilot.click("#mcp_auth_cancel")
            await pilot.pause(0.5)
            assert not coordinator.is_pending("demo")
            assert not bar.shown

    _run(run())


def test_startup_needs_sign_in_starts_the_flow_on_click(env, monkeypatch):
    """A quiet startup connect leaves the server in 'auth' with no link yet."""
    import webbrowser

    from jarvis.mcp.auth import coordinator
    from jarvis.mcp.registry import mcp_registry

    env.write_servers(DEMO)
    mcp_registry._needs_auth.add("demo")
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)

    def fake_authenticate(name, cfg):
        coordinator.begin(name, AUTH_URL)
        return {"ok": True, "name": name, "url": AUTH_URL, "status": "pending"}

    monkeypatch.setattr(mcp_registry, "authenticate", fake_authenticate)

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.pause(0.4)
            app._mcp_auth_sync()
            bar = app.query_one("#mcp_auth")
            assert bar.shown and "demo needs sign-in" in bar.message_text()
            # no request yet → nothing to copy or paste
            assert app.query_one("#mcp_auth_copy").has_class("-off")
            await pilot.click("#mcp_auth_go")
            await pilot.pause(1.0)
            assert opened == [AUTH_URL]
            assert "waiting for the browser" in bar.message_text()

    _run(run())


def test_dismiss_hides_bar_until_a_new_request(env):
    from jarvis.mcp.auth import coordinator

    env.write_servers(DEMO)

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.pause(0.4)
            coordinator.begin("demo", AUTH_URL)
            await pilot.pause(0.6)
            bar = app.query_one("#mcp_auth")
            await pilot.click("#mcp_auth_dismiss")
            await pilot.pause(0.3)
            assert not bar.shown
            app._mcp_auth_sync()  # the slow tick must not bring it back
            assert not bar.shown
            coordinator.begin("demo", AUTH_URL + "2")  # a new request does
            await pilot.pause(0.6)
            assert bar.shown

    _run(run())


def test_ctrl_o_starts_sign_in_only_while_the_bar_is_up(env, monkeypatch):
    import webbrowser

    from jarvis.mcp.auth import coordinator

    env.write_servers(DEMO)
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.pause(0.4)
            await pilot.press("ctrl+o")
            await pilot.pause(0.2)
            assert opened == []
            coordinator.begin("demo", AUTH_URL)
            await pilot.pause(0.6)
            await pilot.press("ctrl+o")
            await pilot.pause(0.4)
            assert opened == [AUTH_URL]

    _run(run())


def test_sidebar_rows_are_clickable(env, monkeypatch):
    from jarvis.mcp.auth import coordinator
    from jarvis.tui.extension_modals import McpAddScreen
    from jarvis.tui.sidebar import SidebarBody

    env.write_servers(DEMO)
    started: list[str] = []

    async def run():
        app = env.app_cls()
        monkeypatch.setattr(app, "mcp_authenticate", started.append)
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause(0.4)
            coordinator.begin("demo", AUTH_URL)
            await pilot.pause(0.6)
            body = app.query_one(SidebarBody)
            text = body.render().plain
            assert "demo" in text and "sign in" in text and "+ add MCP server" in text

            def row_y(kind: str) -> int:
                for y in range(60):
                    hit = body._hit_at(y)
                    if hit and hit[0] == kind:
                        return y
                raise AssertionError(f"no {kind} row")

            await pilot.click(SidebarBody, offset=(3, row_y("mcp")))
            await pilot.pause(0.2)
            assert started == ["demo"]

            await pilot.click(SidebarBody, offset=(3, row_y("mcp-add")))
            await pilot.pause(0.4)
            assert isinstance(app.screen, McpAddScreen)

    _run(run())


def test_smart_add_previews_and_adds_a_stdio_server_to_the_project(env, monkeypatch):
    import jarvis.mcp.install as install
    from jarvis.tui.extension_modals import McpAddScreen

    monkeypatch.setattr(install.mcp_registry, "connect", lambda name, cfg, **kw: None)
    monkeypatch.setattr(install.mcp_registry, "get_server_tools", lambda name: [])
    results: list = []

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause(0.4)
            app.push_screen(McpAddScreen(), results.append)
            await pilot.pause(0.4)
            screen = app.screen
            screen.query_one("#add_source").value = "npx -y @modelcontextprotocol/server-memory"
            await pilot.pause(0.9)
            assert screen._preview and screen._preview["ok"]
            assert screen._preview["servers"][0]["name"] == "memory"
            assert screen._preview["servers"][0]["transport"] == "stdio"

            screen.query_one("#add_scope").set_value("project")
            await pilot.pause(0.2)
            assert "mcp.json" in str(screen.query_one("#add_path").render())
            await pilot.press("enter")
            await pilot.pause(2.6)  # add → result → auto-close

    _run(run())
    data = json.loads((env.project / ".mcp.json").read_text())
    assert data["mcpServers"]["memory"]["command"] == "npx"
    assert results and results[0]["servers"][0]["status"] == "connected"
    assert results[0]["servers"][0]["scope"] == "project"


def test_smart_add_offers_authenticate_for_a_hosted_server(env, monkeypatch):
    import jarvis.mcp.install as install
    from jarvis.mcp.auth import coordinator
    from jarvis.mcp.registry import AUTH_REQUIRED_MSG
    from jarvis.tui.extension_modals import McpAddScreen

    def fake_connect(name, cfg, **kw):
        coordinator.begin(name, AUTH_URL)
        return AUTH_REQUIRED_MSG

    monkeypatch.setattr(install.mcp_registry, "connect", fake_connect)

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause(0.4)
            app.push_screen(McpAddScreen())
            await pilot.pause(0.4)
            screen = app.screen
            screen.query_one("#add_source").value = "https://mcp.example.com/mcp"
            await pilot.pause(0.5)
            await pilot.press("enter")
            await pilot.pause(1.5)
            assert screen.query_one("#add_sign").display
            assert not screen.query_one("#add_keys").display
            # …and the bar above the composer shows it too (registry event → UI)
            assert app.query_one("#mcp_auth").shown

    _run(run())


def test_smart_add_asks_for_a_missing_key(env, monkeypatch):
    import jarvis.mcp.install as install
    from jarvis.mcp.secrets import get_secret
    from jarvis.tui.extension_modals import KeysScreen, McpAddScreen

    monkeypatch.setattr(install.mcp_registry, "connect", lambda name, cfg, **kw: None)
    monkeypatch.setattr(install.mcp_registry, "get_server_tools", lambda name: [])

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause(0.4)
            app.push_screen(McpAddScreen())
            await pilot.pause(0.4)
            add = app.screen
            add.query_one("#add_source").value = "github"
            await pilot.pause(0.5)
            await pilot.press("enter")
            await pilot.pause(1.2)
            assert add.query_one("#add_keys").display
            await pilot.click("#add_keys")
            await pilot.pause(0.5)
            assert isinstance(app.screen, KeysScreen)
            assert app.screen.query_one("#key_0").password is True
            for ch in "ghp_test":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause(1.5)

    _run(run())
    assert get_secret("GITHUB_PERSONAL_ACCESS_TOKEN") == "ghp_test"


def test_skill_install_from_a_local_folder_via_the_modal(env):
    from jarvis.storage import skill_install as si
    from jarvis.tui.skill_modal import SkillBrowserScreen

    src = env.tmp / "src" / "alpha-notes"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: alpha-notes\ndescription: Turn meeting notes into action items.\n---\n# Alpha\n"
    )

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause(0.4)
            app.push_screen(SkillBrowserScreen())
            await pilot.pause(0.4)
            await pilot.press("a")
            await pilot.pause(0.4)
            screen = app.screen
            screen.query_one("#sk_source").value = str(src)
            await pilot.pause(2.0)
            assert screen._info and screen._info["ok"]
            assert screen._selected == {"alpha-notes"}  # one skill → ready to go
            await pilot.press("enter")
            await pilot.pause(2.8)  # install → auto-close → browser repopulates
            assert isinstance(app.screen, SkillBrowserScreen)
            names = [r["name"] for r in si.describe_installed()["skills"]]
            assert "alpha-notes" in names

    _run(run())
    assert (env.global_skills / "alpha-notes" / "SKILL.md").is_file()


def test_skill_browser_remove_needs_a_second_press(env):
    from jarvis.storage import skill_install as si
    from jarvis.tui.skill_modal import SkillBrowserScreen

    d = env.project / ".harness" / "skills" / "beta-review"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: beta-review\ndescription: Review a pull request.\n---\nbody\n")

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause(0.4)
            app.push_screen(SkillBrowserScreen())
            await pilot.pause(0.5)
            await pilot.press("d")
            await pilot.pause(0.2)
            assert d.exists(), "first d only arms"
            await pilot.press("d")
            await pilot.pause(0.3)
            assert not d.exists()
            assert si.find_installed("beta-review") is None

    _run(run())


def test_agent_mcp_add_shows_the_sign_in_bar(env, monkeypatch):
    """The agent calls mcp_add; a hosted server needing sign-in surfaces the button."""
    import jarvis.mcp.install as install
    from jarvis.mcp.auth import coordinator
    from jarvis.mcp.registry import AUTH_REQUIRED_MSG
    from jarvis.tools import extensions

    def fake_connect(name, cfg, **kw):
        coordinator.begin(name, AUTH_URL)
        return AUTH_REQUIRED_MSG

    monkeypatch.setattr(install.mcp_registry, "connect", fake_connect)
    out: list[str] = []

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause(0.4)
            out.append(await asyncio.to_thread(extensions.mcp_add, "https://mcp.example.com/mcp", scope="project"))
            await pilot.pause(0.7)
            bar = app.query_one("#mcp_auth")
            assert bar.shown and "needs sign-in" in bar.message_text()

    _run(run())
    assert "auth_required" in out[0] or "Authenticate" in out[0]
    data = json.loads((env.project / ".mcp.json").read_text())
    assert data["mcpServers"]["example"]["url"] == "https://mcp.example.com/mcp"


def test_hidden_password_prompt_for_keys(env):
    """tools/extensions._ask_credentials → console.input(password=True) → masked TUI input."""
    from jarvis.tui.text_input_modal import TextInputScreen

    got: list[str] = []

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause(0.4)
            t = threading.Thread(
                target=lambda: got.append(app._tui_console.input("API key: ", password=True)), daemon=True
            )
            t.start()
            await pilot.pause(0.6)
            assert isinstance(app.screen, TextInputScreen)
            assert app.screen.query_one("#text_input").password is True
            for ch in "s3cret":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause(0.6)
            t.join(timeout=3)

    _run(run())
    assert got == ["s3cret"]


def test_paste_address_finishes_a_sign_in(env, monkeypatch):
    from jarvis.mcp.auth import coordinator

    env.write_servers(DEMO)

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 46)) as pilot:
            await pilot.pause(0.4)
            req = coordinator.begin("demo", AUTH_URL)
            await pilot.pause(0.6)
            await pilot.click("#mcp_auth_paste")
            await pilot.pause(0.4)
            from jarvis.tui.text_input_modal import TextInputScreen

            assert isinstance(app.screen, TextInputScreen)
            app.screen.query_one("#text_input").value = (
                "http://localhost:33418/callback?code=thecode&state=" + req.state
            )
            await pilot.press("enter")
            await pilot.pause(0.5)
            assert req.code == "thecode"

    _run(run())


def test_relay_thread_never_blocks_emitters(env):
    """Registry events are queued for the UI, not waited on."""
    import time

    from jarvis.mcp.registry import mcp_registry

    async def run():
        app = env.app_cls()
        async with app.run_test(size=(160, 44)) as pilot:
            await pilot.pause(0.4)
            t0 = time.monotonic()
            for i in range(50):
                mcp_registry._emit("connected", f"s{i}")
            assert time.monotonic() - t0 < 0.5

    _run(run())
