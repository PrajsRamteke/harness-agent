"""Jarvis the pet: model maths, sprites, /pet command, and the TUI wiring."""
import asyncio
import json

import pytest
from rich.console import Console

from jarvis.pet import model as pm
from jarvis.pet import sprites as sp

T0 = 1_700_000_000.0
H = 3600.0


def _pet(**kw) -> pm.Pet:
    base = dict(born=T0, last_tick=T0)
    base.update(kw)
    return pm.Pet(**base)


# ── levels ───────────────────────────────────────────────────────────────


def test_level_curve_and_titles():
    assert [pm.xp_for_level(n) for n in (1, 2, 3, 4)] == [0, 50, 150, 300]
    assert pm.level_for_xp(0) == 1
    assert pm.level_for_xp(49) == 1 and pm.level_for_xp(50) == 2
    assert pm.level_for_xp(299) == 3
    assert pm.level_title(1) == "Kitten"
    assert pm.level_title(99) == pm.LEVEL_TITLES[-1]
    p = _pet(xp=180)
    assert p.level == 3 and p.level_progress() == (30, 150)


# ── time ─────────────────────────────────────────────────────────────────


def test_tick_decays_food_and_love_gently():
    p = _pet(fullness=70, happiness=75, energy=50)
    p.tick(T0 + 0.25 * H)  # 15 minutes with the app open
    assert p.fullness == pytest.approx(70 - pm.FULLNESS_DECAY_H * 0.25)
    assert p.energy == pytest.approx(50 + pm.ENERGY_RECOVER_H * 0.25)
    p = _pet(happiness=25, fullness=90)
    p.tick(T0 + 48 * H)  # two days away
    assert p.happiness == pm.HAPPINESS_FLOOR, "decay alone never goes below the floor"
    assert p.energy == 100, "Jarvis sleeps while the app is closed"
    assert p.fullness == 0


def test_busy_time_costs_energy():
    p = _pet(energy=50)
    p.tick(T0 + 0.25 * H, busy=True)
    assert p.energy == pytest.approx(50 - pm.ENERGY_WORK_H * 0.25)


# ── care ─────────────────────────────────────────────────────────────────


def test_pat_is_diminishing_and_xp_is_rate_limited():
    p = _pet(happiness=50)
    r = p.pat(T0)
    assert r.anim == "love" and p.happiness == 57 and p.xp == pm.XP_PAT
    p.pat(T0 + 2)  # spam
    assert p.happiness == 59 and p.xp == pm.XP_PAT
    assert p.count("pats") == 2


def test_pat_wakes_a_napping_pet():
    p = _pet()
    p.nap(minutes=5, now=T0)
    assert p.napping(T0 + 60) and p.mood(T0 + 60) == "napping"
    r = p.pat(T0 + 60)
    assert r.anim == "surprised" and not p.napping(T0 + 61)


def test_feed_cycles_snacks_caps_and_refuses_when_full():
    p = _pet(fullness=10, happiness=50)
    first = p.feed(now=T0)
    second = p.feed(now=T0)
    assert (first.snack, second.snack) == ("fish", "milk")
    assert p.fullness == 10 + pm.SNACKS["fish"][0] + pm.SNACKS["milk"][0]
    p.fullness = 97
    full = p.feed("cookie", now=T0)
    assert full.anim == "happy" and "full" in full.say and p.fullness == 97
    assert p.count("snacks") == 2


def test_play_needs_energy():
    tired = _pet(energy=10)
    assert tired.play(T0).anim == "sleep" and tired.count("plays") == 0
    p = _pet(energy=60, happiness=40)
    r = p.play(T0)
    assert r.anim == "play" and p.happiness == 54 and p.energy == 51


def test_rename_trims_and_defaults():
    p = _pet()
    assert p.rename("  Mochi   the   Cat  ") == "Mochi the Cat"
    assert p.rename("x" * 40) == "x" * pm.MAX_NAME
    assert p.rename("   ") == pm.DEFAULT_NAME


def test_mood_priorities():
    assert _pet(fullness=10).mood(T0) == "hungry"
    assert _pet(energy=10).mood(T0) == "sleepy"
    assert _pet(happiness=20).mood(T0) == "lonely"
    assert _pet(happiness=90).mood(T0) == "ecstatic"
    assert _pet(happiness=65).mood(T0) == "happy"
    assert _pet(happiness=50).mood(T0) == "content"


# ── the agent's work ─────────────────────────────────────────────────────


def test_turns_and_tools_grant_xp_and_level_up():
    p = _pet(xp=45)
    r = p.on_turn_done(12.0)
    assert r.anim == "proud" and r.level_up == 2 and p.xp == 55 and p.count("turns") == 1
    assert p.on_turn_done(5.0, interrupted=True).level_up == 0 and p.xp == 55
    assert p.on_tool_done("read_file", error=False) is None and p.xp == 56
    p.on_tool_done("edit_file", error=False)
    assert p.xp == 56 + pm.XP_TOOL + pm.XP_EDIT and p.count("edits") == 1
    assert p.on_tool_done("run_bash", error=True).anim == "ouch"
    assert p.count("oops") == 1


# ── persistence ──────────────────────────────────────────────────────────


def test_save_load_round_trip_and_bad_files(tmp_path):
    path = tmp_path / "pet.json"
    p = _pet(name="Mochi", fur="snow", xp=77, happiness=12.5)
    p.counters["turns"] = 3
    pm.save_pet(p, path)
    back = pm.load_pet(path)
    assert (back.name, back.fur, back.xp, back.happiness, back.count("turns")) == \
        ("Mochi", "snow", 77, 12.5, 3)

    path.write_text("{not json")
    assert pm.load_pet(path).name == pm.DEFAULT_NAME
    path.write_text(json.dumps({"name": 5, "xp": "lots", "happiness": 900, "energy": 3,
                                "counters": {"pats": 2.0, "bad": "x"}}))
    odd = pm.load_pet(path)
    assert odd.name == pm.DEFAULT_NAME and odd.xp == 0
    assert odd.happiness == 100 and odd.energy == 3.0 and odd.counters == {"pats": 2}


def test_get_pet_uses_the_isolated_file():
    pet = pm.get_pet()
    pet.xp = 5
    pm.save_pet()
    assert json.loads(pm.PET_FILE.read_text())["xp"] == 5
    assert "pytest" in str(pm.PET_FILE) or "tmp" in str(pm.PET_FILE)


# ── sprites ──────────────────────────────────────────────────────────────

ANIMS = ("idle", "work", "love", "eat", "play", "sleep", "proud", "ouch",
         "surprised", "trick", "wave", "happy")


@pytest.mark.parametrize("fur", sp.FUR_ORDER)
def test_every_frame_keeps_a_fixed_size(fur):
    for anim in ANIMS:
        for i in range(24):
            t = i * 0.17
            for look in (-1, 0, 1):
                small = sp.buddy_lines(anim, t, fur, look=look)
                assert len(small) == 3
                assert all(line.cell_len == sp.BUDDY_W for line in small), (anim, t, look)
            big = sp.big_cat(anim, t, fur, snack="milk")
            assert len(big) == sp.BIG_ROWS
            assert all(line.cell_len == sp.BIG_W + sp.DECO_W for line in big), (anim, t)


def test_pixel_grid_is_rectangular_and_mirrors_eyes():
    rows = sp.cat_pixels("ouch", 0.1)
    assert len(rows) == 2 * sp.BIG_ROWS and {len(r) for r in rows} == {sp.BIG_W}
    head = rows[2:]  # no lift at t=0.1 → 2 px headroom
    left, right = [r[2:5] for r in head[6:9]], [r[14:17] for r in head[6:9]]
    assert left == [r[::-1] for r in right], "> < eyes mirror"


def test_fur_cycle_visits_every_palette():
    seen, fur = [], "ginger"
    for _ in sp.FUR_ORDER:
        seen.append(fur)
        fur = sp.next_fur(fur)
    assert sorted(seen) == sorted(sp.FUR_ORDER) and fur == "ginger"


# ── /pet command ─────────────────────────────────────────────────────────


@pytest.fixture()
def pet_cmd(monkeypatch, tmp_path):
    import jarvis.commands.pet as cmd
    import jarvis.storage.settings as settings_mod

    rec = Console(record=True, width=120, file=open("/dev/null", "w"))
    monkeypatch.setattr(cmd, "console", rec)
    fresh = settings_mod.Settings(path=tmp_path / "settings.json")
    monkeypatch.setattr(settings_mod, "get_settings", lambda: fresh)
    return cmd, rec, fresh


def test_pet_command_actions(pet_cmd):
    from jarvis.commands.dispatch import handle_slash

    cmd, rec, settings = pet_cmd
    pet = pm.get_pet()
    pet.fullness = 20
    assert handle_slash("/pet feed fish")[:2] == ("ok", False)
    assert pet.fullness == pytest.approx(20 + pm.SNACKS["fish"][0], abs=0.01)
    handle_slash("/pet feed pizza")
    handle_slash("/pet name Mochi")
    handle_slash("/pet fur midnight")
    handle_slash("/pet off")
    handle_slash("/pet nudges off")
    handle_slash("/pet")
    out = rec.export_text()
    assert "unknown snack" in out and "Mochi" in out and "Lv 1" in out
    assert pet.name == "Mochi" and pet.fur == "midnight"
    assert settings.get("pet.enabled") is False and settings.get("pet.nudges") is False
    assert json.loads(pm.PET_FILE.read_text())["name"] == "Mochi"


def test_pet_card_command_routes_to_the_dialog():
    from jarvis.tui.app_commands import _is_pet_card_command

    assert _is_pet_card_command("/pet") and _is_pet_card_command(" /PET ")
    assert not _is_pet_card_command("/pet feed")


def test_pet_settings_are_booleans():
    from jarvis.storage.settings import DEFAULTS, _coerce

    assert DEFAULTS["pet"] == {"enabled": True, "nudges": True}
    assert _coerce("pet.enabled", "off") is False
    with pytest.raises(ValueError):
        _coerce("pet.nudges", "maybe")


# ── TUI ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def hermetic_app(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")

    import jarvis.updater as updater
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.storage.settings as settings_mod
    import jarvis.tui.prompt_history as prompt_history
    from jarvis import state

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    monkeypatch.setattr(state, "save_trace_config", lambda: None)
    fresh = settings_mod.Settings(path=tmp_path / "settings.json")
    monkeypatch.setattr(settings_mod, "get_settings", lambda: fresh)

    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)
    return JarvisTUI


def test_kitty_lives_in_the_composer_and_speaks_above_it(hermetic_app):
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.3)
            buddy, composer = app.query_one("#pet"), app.query_one("#composer")
            assert buddy.display and buddy.region.height == 3
            assert buddy.region.y == composer.region.y and buddy.region.right <= composer.region.right
            prompt = app.query_one("#prompt")
            assert prompt.region.y == composer.region.y + 1, "prompt keeps its padding row"

            app._pet_say("hello!", 3)
            await pilot.pause()
            bubble = app.query_one("#pet_bubble")
            assert bubble.display and "hello!" in bubble.render().plain
            assert bubble.region.y == composer.region.y - 1
            assert bubble.region.right == buddy.region.right

            await pilot.click("#pet")
            await pilot.pause()
            assert pm.get_pet().count("pats") == 1
            assert buddy.current()[0] == "love"

    asyncio.run(run())


def test_turns_and_tools_feed_xp_and_animate(hermetic_app):
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.3)
            pet = pm.get_pet()
            app._pet_turn_finished(20.0, interrupted=False, llm=True)
            await pilot.pause()
            assert pet.xp == pm.XP_TURN and app.query_one("#pet").current()[0] == "proud"
            app._pet_turn_finished(2.0, interrupted=False, llm=False)  # a slash command
            assert pet.xp == pm.XP_TURN

            con = app._tui_console
            con.emit_tool_event("tool_start", {"id": "t1", "name": "edit_file", "input": {"path": "a.py"}})
            con.emit_tool_event("tool_done", {"id": "t1", "output": "EDITED a.py (1 replacements)"})
            await pilot.pause()
            assert pet.count("edits") == 1
            assert pet.xp == pm.XP_TURN + pm.XP_TOOL + pm.XP_EDIT
            assert pm.PET_FILE.exists()

    asyncio.run(run())


def test_pet_card_actions_and_hide_toggle(hermetic_app):
    from jarvis.tui.pet_modal import PetCardScreen

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(110, 36)) as pilot:
            await pilot.pause(0.3)
            pet = pm.get_pet()
            pet.fullness = 30
            app._route_command("/pet")
            await pilot.pause(0.3)
            assert isinstance(app.screen, PetCardScreen)
            await pilot.press("f")
            await pilot.pause()
            assert pet.fullness > 30 and pet.count("snacks") == 1
            await pilot.press("enter")
            await pilot.pause()
            assert pet.count("pats") == 1
            await pilot.press("c")
            assert pet.fur == "midnight"
            await pilot.press("h")
            await pilot.pause()
            assert app._pet_enabled() is False
            buddy = app.screen_stack[0].query_one("#pet")
            assert buddy.has_class("hidden")
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, PetCardScreen)
            assert json.loads(pm.PET_FILE.read_text())["fur"] == "midnight"

    asyncio.run(run())


def test_nudges_are_useful_and_rate_limited(hermetic_app):
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.3)
            now = 10_000_000.0
            app._pet_streak_t0 = now - 95 * 60
            app._pet_last_work = now - 30
            line = app._pet_nudge_line(now)
            assert "stretch" in line and "1h" in line
            assert app._pet_nudge_line(now + 60) == "", "one nudge per 10 minutes"
            pm.get_pet().fullness = 10
            app._pet_last_work = now - 10_000  # not working: no break nudge
            later = now + 11 * 60
            assert "hungry" in app._pet_nudge_line(later)

    asyncio.run(run())


# ── the sidebar pen ──────────────────────────────────────────────────────


def test_pen_lives_in_the_sidebar_and_everything_is_one_click(hermetic_app):
    from jarvis.tui import pet_pen as pp

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause(0.4)
            pen, kitty = app.query_one("#pet_pen"), app.query_one("#pet")
            sidebar = app.query_one("#sidebar")
            assert not sidebar.has_class("hidden") and pen.display
            assert pen.region.height == pp.ROWS and pen.region.bottom <= sidebar.region.bottom
            assert kitty.has_class("hidden"), "one Jarvis at a time: pen wins over the input box"

            pet = pm.get_pet()
            pet.fullness = 30
            start, _end, label = next(b for b in pen._buttons if b[2] == "feed")
            await pilot.click("#pet_pen", offset=(start + 1, pp.BUTTONS))
            await pilot.pause()
            assert pet.count("snacks") == 1 and pen.anim[0] == "eat"
            assert pen.speaking, "speech shows in the pen, not a bubble"
            assert not app.query_one("#pet_bubble").display

            a, _b = pen.cat_span()
            await pilot.click("#pet_pen", offset=(a + 6, pp.STAGE0 + 5))
            await pilot.pause()
            assert pet.count("pats") == 1

            pen.anim = None
            pen.x = 0.0
            far = pen.stage_w - 2
            await pilot.click("#pet_pen", offset=(far, pp.STAGE0 + 1))
            await pilot.pause(0.5)
            assert pen.x > 1.0 and pen.facing == 1, "the cat runs toward your click"

            start, _end, _ = next(b for b in pen._buttons if b[2] == "play")
            await pilot.click("#pet_pen", offset=(start, pp.BUTTONS))
            await pilot.pause(0.3)
            assert pen.ball is not None and pet.count("plays") == 1

            await pilot.click("#pet_pen", offset=(2, pp.HEADER))
            await pilot.pause(0.3)
            from jarvis.tui.pet_modal import PetCardScreen

            assert isinstance(app.screen, PetCardScreen)
            await pilot.press("escape")
            await pilot.pause()

            app.action_toggle_sidebar()
            await pilot.pause()
            assert sidebar.has_class("hidden") and not kitty.has_class("hidden"), \
                "sidebar hidden → Jarvis moves into the input box"

    asyncio.run(run())


def test_pen_wanders_and_paces_while_busy(hermetic_app):
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause(0.3)
            pen = app.query_one("#pet_pen")
            pen.anim = None
            pen.sit_until = 0.0
            import random

            random.seed(3)
            xs = set()
            for _ in range(40):
                await pilot.pause(0.1)
                xs.add(round(pen.x))
            assert len(xs) > 3, "the cat roams on its own"
            app._busy = True
            await pilot.pause(0.2)
            assert pen._frame[0] == "work"
            app._busy = False

    asyncio.run(run())


def test_speech_wraps_to_two_rows():
    from jarvis.tui.pet_pen import _wrap2

    rows = _wrap2("we've been at it 1h32m — stretch break? ♥ yes really", 20, "")
    assert len(rows) == 2 and all(r.cell_len <= 20 for r in rows)
    assert rows[0].plain.startswith("we've been at it")
    assert [r.plain for r in _wrap2("hi", 20, "")] == ["hi", ""]
