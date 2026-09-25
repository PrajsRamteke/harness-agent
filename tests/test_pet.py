"""Jarvis the pet: model maths, sprites, /pet command, and the TUI wiring."""
import asyncio
import json

import pytest
from rich.console import Console

from jarvis.pet import model as pm
from jarvis.pet import sprites as sp

T0 = 1_700_000_000.0
H = 3600.0


def _saved() -> dict:
    """The active pet as written to the (isolated) pet.json."""
    data = json.loads(pm.PET_FILE.read_text())
    return data["pets"][data["active"]]


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
    assert _saved()["xp"] == 5
    assert "pytest" in str(pm.PET_FILE) or "tmp" in str(pm.PET_FILE)


# ── sprites ──────────────────────────────────────────────────────────────

ANIMS = ("idle", "work", "love", "eat", "play", "sleep", "proud", "ouch", "surprised",
         "trick", "wave", "happy", "cheer", "party", "hide", "worried", "hatch", "wobble")


@pytest.mark.parametrize("species", pm.SPECIES + ("egg",))
def test_every_frame_keeps_a_fixed_size(species):
    furs = sp.SPECIES_FURS.get(species, sp.SPECIES_FURS["dragon"])
    for fur in furs:
        for anim in ANIMS:
            for i in range(0, 24, 5):
                t = i * 0.17
                small = sp.buddy_lines(anim, t, fur, look=(-1, 0, 1)[i % 3], species=species)
                assert len(small) == 3
                assert all(line.cell_len == sp.BUDDY_W for line in small), (species, anim, t)
                for stage in (0, 1, 2):
                    rows = sp.pet_pixels(species, anim, t, stage=stage, walking=bool(i % 2),
                                         facing=(-1, 1)[i % 2],
                                         outfit={"glasses", "party", "scarf", "crown"})
                    grow = 0 if species == "egg" else stage
                    assert len(rows) == sp.SPRITE_H + grow
                    assert {len(r) for r in rows} == {sp.SPRITE_W + grow}, (species, anim, stage)
        stage = sp.pen_stage(34, 5, "love", 0.3, fur, species=species, stage=2, rows=10,
                             props=[("box", 2, -1), ("cloud", -3, 1)], front=[("fish", 30, 4)],
                             pixels=[(33, 19, sp.LASER)], deco=[(0, 0, "✦", sp.GOLD)], confetti=1)
        assert len(stage) == 10 and all(line.cell_len == 34 for line in stage)


def test_pixel_grid_mirrors_symmetric_eyes():
    rows = sp.pet_pixels("cat", "ouch", 0.1)
    left, right = [r[1:4] for r in rows[8:10]], [r[9:12] for r in rows[8:10]]
    assert left == [r[::-1] for r in right], "> < eyes mirror"


def test_accessories_and_growth_change_the_sprite():
    plain = sp.pet_pixels("cat", "idle", 1.0)
    crowned = sp.pet_pixels("cat", "idle", 1.0, outfit={"crown", "scarf"})
    assert plain != crowned and any("G" in r for r in crowned) and any("C" in r for r in crowned)
    assert len(sp.pet_pixels("cat", "idle", 1.0, stage=2)[0]) == sp.SPRITE_W + 2
    assert sp.pet_pixels("cat", "idle", 1.0, facing=1)[8] == plain[8][::-1]


def test_fur_cycle_visits_every_palette_per_species():
    for species, order in sp.SPECIES_FURS.items():
        seen, fur = [], order[0]
        for _ in order:
            seen.append(fur)
            fur = sp.next_fur(fur, species)
        assert sorted(seen) == sorted(order) and fur == order[0]
        assert sp.default_fur(species) == order[0]


def test_sky_follows_the_clock_and_seasons():
    from datetime import datetime

    day, _ = sp.sky(datetime(2026, 5, 3, 12), 34, 1.0)
    night, stars = sp.sky(datetime(2026, 5, 3, 23), 34, 1.0)
    assert [p[0] for p in day] == ["cloud"] and "moon" in [p[0] for p in night] and stars
    assert "pumpkin" in [p[0] for p in sp.sky(datetime(2026, 10, 31, 12), 34, 1.0)[0]]
    _, snow = sp.sky(datetime(2026, 12, 24, 12), 34, 1.0)
    assert any(d[2] in "*·" for d in snow)


# ── work events, badges, roster ──────────────────────────────────────────


def test_classify_tests_and_git():
    from jarvis.pet import events as ev

    run = lambda cmd, code, out="": ev.classify("run_bash", {"cmd": cmd}, f"$ {cmd}\nexit={code}\n{out}")
    assert run("python -m pytest tests/ -q", 0) == ["tests_pass"]
    assert run("npm test", 1) == ["tests_fail"]
    assert run("pytest -k nothing", 5) == []
    assert run("git commit -m 'x'", 0, "[main abc] x") == ["commit"]
    assert run("git commit -m 'x'", 1, "nothing to commit") == []
    assert run("git push", 0, "Everything up-to-date") == []
    assert run("cargo test && git push origin main", 0) == ["tests_pass", "push"]
    assert run("git merge dev", 1, "CONFLICT (content): Merge conflict in a.py") == ["conflict"]
    assert ev.classify("read_file", {"path": "pytest.ini"}, "pytest") == []
    assert ev.classify("run_bash", {"cmd": "pytest"}, "USER DENIED") == []


def test_red_then_green_counts_as_a_fix_and_earns_badges():
    p = _pet()
    assert p.on_work_event("tests_fail").anim == "hide" and p.tests == "fail"
    r = p.on_work_event("tests_pass")
    assert r.anim == "cheer" and p.count("tests_fixed") == 1 and p.xp == pm.XP_FIXED
    for _ in range(4):
        p.on_work_event("tests_fail")
        last = p.on_work_event("tests_pass")
    assert "bug_squasher" in last.badges and "bug_squasher" in p.badges
    assert p.on_work_event("commit").anim == "party" and p.count("commits") == 1
    assert p.on_work_event("conflict").anim == "worried"


def test_big_diffs_and_lines_shipped_today():
    p = _pet()
    assert p.on_diff(3, 1, now=T0) is None
    r = p.on_diff(90, 30, now=T0)
    assert r.anim == "surprised" and "whoa" in r.say and "big_diff" in r.badges
    assert p.today_stat("lines", T0) == 124 and p.count("lines") == 124
    assert p.today_stat("lines", T0 + 86400) == 0, "a new day starts from zero"


def test_streak_counts_consecutive_days():
    p = _pet()
    day = 24 * H
    p.on_turn_done(5, now=T0)
    p.on_turn_done(5, now=T0 + 60)
    assert p.streak == 1
    p.on_turn_done(5, now=T0 + day)
    p.on_turn_done(5, now=T0 + 2 * day)
    assert p.streak == 3 and p.best_streak == 3
    p.on_turn_done(5, now=T0 + 5 * day)
    assert p.streak == 1 and p.best_streak == 3


def test_accessories_unlock_by_level_and_can_be_taken_off():
    p = _pet()
    assert p.outfit(working=True) == set()
    p.xp = pm.xp_for_level(5)
    assert p.outfit() == {"party"} and p.outfit(working=True) == {"party", "glasses"}
    p.xp = pm.xp_for_level(10)
    assert p.outfit() == {"crown", "scarf"}, "the crown replaces the party hat"
    assert p.stage == 2
    assert p.toggle_accessory("crown") is False and p.outfit() == {"party", "scarf"}
    assert p.toggle_accessory("crown") is True


def test_dragon_egg_hatches_after_ten_turns():
    egg = pm.new_pet("dragon", "Ember")
    assert egg.is_egg and egg.title == "Egg" and egg.hatch_left == pm.HATCH_TURNS
    assert egg.feed().anim == "wobble" and egg.fullness == 70
    for _ in range(pm.HATCH_TURNS - 1):
        assert egg.on_turn_done(5).anim == "wobble"
    r = egg.on_turn_done(5)
    assert r.anim == "hatch" and not egg.is_egg and "hatched" in r.badges
    assert egg.title == pm.level_title(egg.level, "dragon")


def test_roster_adopts_switches_and_loads_old_single_pet_files(tmp_path):
    path = tmp_path / "pet.json"
    path.write_text(json.dumps({"name": "kuku", "xp": 23, "counters": {"pats": 3}}))
    roster = pm.load_roster(path)
    assert len(roster.pets) == 1 and roster.pet.name == "kuku" and roster.pet.count("pats") == 3
    dog = roster.adopt("dog", "Biscuit")
    assert roster.pet is dog and dog.fur == sp.default_fur("dog")
    roster.adopt("bunny")
    assert roster.pet.name == "Mochi"
    assert roster.switch().name == "kuku"
    pm.save_roster(roster, path)
    back = pm.load_roster(path)
    assert [p.name for p in back.pets] == ["kuku", "Biscuit", "Mochi"] and back.active == 0
    for _ in range(pm.MAX_PETS - 3):
        back.adopt("cat")
    with pytest.raises(ValueError):
        back.adopt("cat")


def test_session_recap():
    from jarvis.pet.session import SessionStats

    s = SessionStats(t0=T0)
    assert s.empty
    s.note_diff("a.py", 10, 2)
    s.note_diff("b.py", 5, 0)
    s.note_event("tests_pass", fixed=True)
    s.note_event("commit")
    s.turns = 4
    line = s.recap("kuku", now=T0 + 38 * 60)
    assert line == ("✦ kuku's recap — 2 files edited · +15 −2 lines · 1 test fix · "
                    "1 commit · 4 turns · 38 min")


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
    assert _saved()["name"] == "Mochi"


def test_pet_card_command_routes_to_the_dialog():
    from jarvis.tui.app_commands import _is_pet_card_command

    assert _is_pet_card_command("/pet") and _is_pet_card_command(" /PET ")
    assert not _is_pet_card_command("/pet feed")


def test_pet_badges_command_routes_to_the_dialog():
    from jarvis.tui.app_commands import _is_pet_badges_command, _is_pet_card_command

    assert _is_pet_badges_command("/pet badges") and _is_pet_badges_command(" /PET BADGE ")
    assert _is_pet_badges_command("/badges")
    assert not _is_pet_badges_command("/pet")
    assert not _is_pet_card_command("/pet badges")


def test_badge_progress_counts_toward_locked_badges():
    from jarvis.pet import BADGE_INFO, badge_progress

    pet = _pet(counters={"pats": 42, "fish": 3, "hatched": 0})
    assert badge_progress(pet, BADGE_INFO["best_friends"]) == (42, 100)
    assert badge_progress(pet, BADGE_INFO["fisher"]) == (3, 100)
    assert badge_progress(pet, BADGE_INFO["hatched"]) == (0, 1)
    pet.counters["pats"] = 250  # never report more than the target
    assert badge_progress(pet, BADGE_INFO["best_friends"]) == (100, 100)


def test_pet_settings_are_booleans():
    from jarvis.storage.settings import DEFAULTS, _coerce

    assert DEFAULTS["pet"] == {"enabled": True, "nudges": True, "notify": True}
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
            assert pet.xp == pm.XP_TURN and app.query_one("#pet").current()[0] in ("proud", "cheer")
            assert "first_turn" in pet.badges
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
            assert _saved()["fur"] == "midnight"

    asyncio.run(run())


def test_badges_table_shows_meaning_and_progress():
    from jarvis.tui.pet_modal import badges_table

    pet = _pet(counters={"pats": 7}, badges={"first_turn": 0.0})
    rec = Console(record=True, width=100, file=open("/dev/null", "w"))
    rec.print(badges_table(pet))
    out = rec.export_text()
    assert "Best Friends" in out and "100 pats" in out
    assert "7/100" in out, "locked badges show progress toward the target"
    assert "✓ earned" in out, "earned badges are marked"


def test_pet_badges_screen_opens_from_command_and_card(hermetic_app):
    from jarvis.tui.pet_modal import PetBadgesScreen, PetCardScreen

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause(0.3)
            app._route_command("/pet badges")
            await pilot.pause(0.3)
            assert isinstance(app.screen, PetBadgesScreen)
            assert app.screen.query_one("#badge_scroll")
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, PetBadgesScreen)

            app._route_command("/pet")
            await pilot.pause(0.3)
            assert isinstance(app.screen, PetCardScreen)
            await pilot.press("b")
            await pilot.pause(0.3)
            assert isinstance(app.screen, PetBadgesScreen)

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
            start, _end, label = next(b for b in pen._buttons[0] if b[2] == "feed")
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

            start, _end, _ = next(b for b in pen._buttons[0] if b[2] == "play")
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


# ── new pen features ─────────────────────────────────────────────────────


def _run_bash(app, cmd: str, code: int, out: str = "") -> None:
    app._pet_tool_finished("run_bash", error=False, tool_input={"cmd": cmd},
                           output=f"$ {cmd}\nexit={code}\n{out}")


def test_tests_and_git_reactions_through_the_tool_hook(hermetic_app):
    from jarvis.pet import session as pet_session

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause(0.3)
            pen, pet = app.query_one("#pet_pen"), pm.get_pet()
            con = app._tui_console
            con.emit_tool_event("tool_start", {"id": "b1", "name": "run_bash", "input": {"cmd": "pytest -q"}})
            con.emit_tool_event("tool_done", {"id": "b1", "output": "$ pytest -q\nexit=1\n1 failed"})
            await pilot.pause()
            assert pen.anim[0] == "hide" and pet.tests == "fail"
            _run_bash(app, "pytest -q", 0, "3 passed")
            await pilot.pause()
            assert pen.anim[0] in ("cheer", "party") and pet.count("tests_fixed") == 1
            assert pet_session.current().tests_fixed == 1
            _run_bash(app, "git commit -m wip", 0, "[main 1a] wip")
            await pilot.pause()
            assert pet.count("commits") == 1 and pen.anim[0] == "party"
            app._pet_diff("big.py", 120, 5)
            await pilot.pause()
            assert pet.today_stat("lines") == 125 and "big_diff" in pet.badges
            assert "+125" in pen._status(pet, None, 40).plain

    asyncio.run(run())


def test_laser_fish_game_and_toys(hermetic_app):
    from jarvis.tui import pet_pen as pp

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause(0.3)
            pen, pet = app.query_one("#pet_pen"), pm.get_pet()
            pen.anim = None
            pen.x = 0.0
            await pilot.hover("#pet_pen", offset=(pen.stage_w - 1, pp.STAGE0 + 2))
            await pilot.pause(0.6)
            assert pen.laser is not None and pen.x > 1.0, "the pet chases the laser dot"
            pen.laser = None

            start, _e, _ = next(b for b in pen._buttons[1] if b[2] == "fish")
            await pilot.click("#pet_pen", offset=(start, pp.BUTTONS2))
            await pilot.pause()
            assert pen.game is not None
            pen.game["fish"].append({"x": 2, "y": 8.0, "vy": 0.0, "kind": "fish"})
            assert pen._catch_at(3, pp.STAGE0 + 4) and pen.game["you"] == 1
            pen.game["until"] = 0.0
            await pilot.pause(0.3)
            assert pen.game is None and pet.count("fish") >= 1

            for kind in ("box", "butterfly", "cup"):
                pen.anim, pen.target = None, None
                pen.start_toy(kind)
                await pilot.pause(0.3)
                assert pen.toy is not None and pen.toy["kind"] == kind
                assert len(pen.render().plain.split("\n")) == pp.ROWS
            pen.toy = None

    asyncio.run(run())


def test_focus_timer_and_notifications(hermetic_app, monkeypatch):
    import jarvis.utils.notify as notify

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "desktop_notify", lambda title, msg: sent.append((title, msg)) or True)

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause(0.3)
            pen, pet = app.query_one("#pet_pen"), pm.get_pet()
            app._pet_action("focus")
            state = app._pet_focus_state()
            assert state[0] == "focus" and state[1] > 24 * 60
            assert "focus" in pen._status(pet, state, 40).plain
            app._pet_focus_until = 1.0  # time's up
            app._pet_focus_tick()
            assert app._pet_focus_state()[0] == "break" and pet.count("focus") == 1
            assert sent and "focus session done" in sent[-1][1]

            app._app_focused = False
            app._pet_turn_finished(95.0, interrupted=False, llm=True)
            assert "took 1m 35s" in sent[-1][1]
            app._app_focused = True
            n = len(sent)
            app._pet_turn_finished(95.0, interrupted=False, llm=True)
            assert len(sent) == n, "no notification while you're looking"

    asyncio.run(run())


def test_pets_button_adopts_then_switches(hermetic_app):
    from jarvis.tui.pet_modal import PetAdoptScreen

    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(170, 46)) as pilot:
            await pilot.pause(0.3)
            app._pet_action("pets")
            await pilot.pause(0.3)
            assert isinstance(app.screen, PetAdoptScreen)
            await pilot.press("down", "enter")  # puppy
            await pilot.pause(0.3)
            await pilot.press("enter")  # default name
            await pilot.pause(0.3)
            roster = pm.get_roster()
            assert [p.species for p in roster.pets] == ["cat", "dog"] and roster.pet.name == "Biscuit"
            app._pet_action("pets")
            await pilot.pause()
            assert roster.pet.species == "cat"
            assert "1/2" in app.query_one("#pet_pen").render().plain.split("\n")[0]

    asyncio.run(run())


def test_new_prints_a_session_recap(pet_cmd, monkeypatch):
    import jarvis.commands.history as history
    from jarvis.commands.dispatch import handle_slash
    from jarvis.pet import session as pet_session

    _cmd, rec, _settings = pet_cmd
    monkeypatch.setattr(history, "console", rec)
    monkeypatch.setattr(history, "db_create_session", lambda model: 7)  # no real sessions.db row
    monkeypatch.setattr(history, "welcome_banner", lambda: None)
    monkeypatch.setattr(history, "header_panel", lambda: None)
    stats = pet_session.current()
    stats.note_diff("a.py", 7, 1)
    stats.turns = 2
    handle_slash("/new")
    out = rec.export_text()
    assert "recap" in out and "1 file edited" in out and "2 turns" in out
    assert pet_session.current().empty


def test_palette_lists_only_the_card_and_a_live_on_off_toggle(monkeypatch, tmp_path):
    import jarvis.storage.settings as settings_mod
    from jarvis.tui.commands_catalog import filter_commands

    fresh = settings_mod.Settings(path=tmp_path / "settings.json")
    monkeypatch.setattr(settings_mod, "get_settings", lambda: fresh)
    pets = [c for c, _d in filter_commands("/pet")]
    assert pets == ["/pet", "/pet off"]
    fresh.set("pet.enabled", False)
    assert [c for c, _d in filter_commands("/pet")] == ["/pet", "/pet on"]
