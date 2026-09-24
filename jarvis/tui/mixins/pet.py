"""Jarvis the pet, wired into the TUI (mixed into ``JarvisTUI``).

Jarvis lives in a pen at the bottom of the sidebar (``pet_pen.PetPen``);
when the sidebar is hidden it moves into the input box instead
(``pet_widget.PetBuddy`` + its speech bubble).

The pet reacts to what the agent does — it works while a turn runs, winces
at failed tools, cheers when tests go green (and hides behind its paws when
they fail), throws a party for commits and pushes, says "whoa!" at big diffs,
levels up and earns badges as you ship. It keeps a focus timer, runs the fish
mini-game, sends a desktop notification when a long turn finishes while
you're away (``pet.notify``), and occasionally nudges you about something
useful (a stretch break, a nearly-full context window, a very late night, a
hungry tummy). Nudges are rate-limited and can be muted (``pet.nudges``).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from ... import pet as pet_pkg
from ...pet import BADGE_INFO, Reaction, get_pet, get_roster, greeting, save_pet
from ...pet import events as pet_events
from ...pet import session as pet_session

BREAK_AFTER = 90 * 60        # continuous work before a stretch nudge
STREAK_GAP = 15 * 60         # a pause this long resets the work streak
NUDGE_SPACING = 10 * 60      # at most one nudge per 10 minutes
MIN_WIDTH = 64               # hide the kitty on very narrow terminals
FOCUS_SECS = 25 * 60         # one focus session (pomodoro)
BREAK_SECS = 5 * 60
NOTIFY_AFTER = 30.0          # desktop-notify turns at least this long (when away)


class PetMixin:
    # ── setup ────────────────────────────────────────────────────────
    def _pet_init(self) -> None:
        now = time.time()
        self._pet_streak_t0 = now
        self._pet_last_work = now
        self._pet_last_nudge = 0.0
        self._pet_nudged: dict[str, float] = {}
        self._pet_last_save = 0.0
        self._pet_focus_until = 0.0
        self._pet_break_until = 0.0
        pet_pkg.set_reaction_hook(self._pet_react_any_thread)
        pet_pkg.set_action_hook(self._pet_action_any_thread)

    def _pet_mount(self) -> None:
        self._pet_apply_visibility()
        if self._pet_enabled():
            self.set_timer(1.4, self._pet_greet)

    def _pet_greet(self) -> None:
        pet = get_pet()
        pen, kitty = self._pet_pen(), self._pet_widget()
        if not any(w is not None and w.anim_active for w in (pen, kitty)):
            self._pet_play(Reaction("wave", 2.2))  # don't cut off something you started
        mood = pet.mood()
        if mood == "hungry":
            line = f"hi! {pet.name} is a bit hungry… (/pet)"
        elif mood == "lonely":
            line = "you're back! ♥ pat me?"
        else:
            line = greeting(pet)
        self._pet_say(line, 4.5)

    # ── settings ─────────────────────────────────────────────────────
    def _pet_setting(self, key: str, default: bool = True) -> bool:
        try:
            from ...storage.settings import get_settings

            val = get_settings().get(f"pet.{key}")
            return default if val is None else bool(val)
        except Exception:
            return default

    def _pet_enabled(self) -> bool:
        return self._pet_setting("enabled")

    def _pet_set_enabled(self, on: bool) -> None:
        try:
            from ...storage.settings import get_settings

            get_settings().set("pet.enabled", bool(on))
        except Exception:
            pass
        self._pet_apply_visibility()

    def _pet_apply_visibility(self) -> None:
        enabled = self._pet_enabled()
        sidebar = self._pet_base("#sidebar")
        sidebar_on = sidebar is not None and not sidebar.has_class("hidden")
        pen = self._pet_pen()
        if pen is not None:
            pen.set_class(not enabled, "hidden")
        widget = self._pet_widget()
        if widget is not None:
            show = enabled and not sidebar_on and (self.size.width or 100) >= MIN_WIDTH
            widget.set_class(not show, "hidden")
            if not show:
                self._pet_hide_bubble()

    # ── widgets (always on the base screen, even while a dialog is open) ──
    def _pet_base(self, selector: str):
        try:
            stack = self.screen_stack
            return (stack[0] if stack else self.screen).query_one(selector)
        except Exception:
            return None

    def _pet_widget(self):
        return self._pet_base("#pet")

    def _pet_pen(self):
        return self._pet_base("#pet_pen")

    def _pet_pen_visible(self) -> bool:
        pen = self._pet_pen()
        sidebar = self._pet_base("#sidebar")
        return (pen is not None and not pen.has_class("hidden")
                and sidebar is not None and not sidebar.has_class("hidden"))

    def _pet_bubble(self):
        return self._pet_base("#pet_bubble")

    def _pet_kitty_visible(self) -> bool:
        w = self._pet_widget()
        return w is not None and not w.has_class("hidden")

    def _pet_visible(self) -> bool:
        return self._pet_pen_visible() or self._pet_kitty_visible()

    def _pet_play(self, r: Reaction) -> None:
        pen = self._pet_pen()
        if pen is not None and self._pet_pen_visible():
            pen.play(r.anim, r.secs, r.snack)
        w = self._pet_widget()
        if w is not None and self._pet_kitty_visible():
            w.play(r.anim, r.secs, r.snack)

    def _pet_say(self, text: str, secs: float = 3.5) -> None:
        if not text:
            return
        if self._pet_pen_visible():
            self._pet_pen().say(text, secs + 1.0)
            return
        if not self._pet_kitty_visible():
            return
        # The row above the composer hosts the slash / @file popup when open.
        popup = self._pet_base("#popup")
        if popup is not None and popup.display and not popup.has_class("hidden"):
            return
        bubble, anchor, composer = self._pet_bubble(), self._pet_widget(), self._pet_base("#composer")
        if bubble is not None and anchor is not None and composer is not None:
            bubble.say(text, secs, anchor, composer)

    def _pet_hide_bubble(self) -> None:
        bubble = self._pet_bubble()
        if bubble is not None:
            bubble.hide()

    # ── reactions ────────────────────────────────────────────────────
    def _pet_react(self, r: Reaction | None, *, bubble: bool = True) -> None:
        """Animate a reaction; speak it; celebrate level-ups; persist."""
        if r is None:
            return
        self._pet_apply_visibility()  # /pet on|off from a worker lands here
        pet = get_pet()
        self._pet_play(r)
        for bid in r.badges:
            _id, icon, name, how, _c, _n = BADGE_INFO[bid]
            try:
                self.notify(f"{icon} {pet.name} earned a badge — {name} ({how})", timeout=5)
            except Exception:
                pass
        if r.level_up:
            self._pet_play(Reaction("party" if r.level_up in (5, 10) else "proud", 3.0))
            line = f"level up! ★ Lv {r.level_up} · {pet.title}"
            unlocked = [a for a, lvl in pet_pkg.ACCESSORIES if lvl == r.level_up]
            if unlocked:
                line += f" — new: {pet_pkg.ACCESSORY_LABELS[unlocked[0]]}!"
            if pet.stage and r.level_up in (5, 10):
                line += " (look how big I got!)"
            if bubble:
                self._pet_say(line, 6.0)
            try:
                self.notify(f"{pet.name} reached level {r.level_up} — {pet.title}", timeout=4)
            except Exception:
                pass
        elif r.badges and bubble:
            self._pet_play(Reaction("cheer", 2.6))
            self._pet_say(f"new badge! {BADGE_INFO[r.badges[0]][1]} {BADGE_INFO[r.badges[0]][2]}", 5.0)
        elif bubble and r.say:
            self._pet_say(r.say, 3.2)
        self._pet_save()

    def _pet_react_any_thread(self, r: Reaction) -> None:
        if threading.current_thread() is threading.main_thread():
            self._pet_react(r)
        else:
            try:
                self.call_from_thread(self._pet_react, r)
            except Exception:
                pass

    def _pet_action_any_thread(self, action: str) -> None:
        if threading.current_thread() is threading.main_thread():
            self._pet_action(action)
        else:
            try:
                self.call_from_thread(self._pet_action, action)
            except Exception:
                pass

    def _pet_save(self, *, force: bool = True) -> None:
        now = time.monotonic()
        if not force and now - self._pet_last_save < 60:
            return
        self._pet_last_save = now
        save_pet()

    def _pet_pat(self) -> None:
        pet = get_pet()
        r = pet.pat()
        if pet.count("pats") == 1:
            r.say = ("purr~ ♥ (click my name for my card)" if self._pet_pen_visible()
                     else "purr~ ♥ (double-click me for my card)")
        self._pet_react(r)

    def _pet_action(self, action: str) -> None:
        """A pen button: pat · feed · play · nap · trick."""
        pet = get_pet()
        if action == "pat":
            self._pet_pat()
            return
        if action == "feed":
            r = pet.feed()
        elif action == "play":
            r = pet.play()
        elif action == "nap":
            if pet.napping():
                pet.wake()
                r = Reaction("wave", 1.6, "*stretches* I'm up!")
            else:
                r = pet.nap()
        elif action == "trick":
            r = pet.trick()
        elif action == "fish":
            self._pet_fish_toggle()
            return
        elif action == "focus":
            self._pet_focus_toggle()
            return
        elif action == "pets":
            if len(get_roster().pets) > 1:
                self._pet_switch()
            else:
                self._pet_adopt_dialog()
            return
        elif action == "card":
            self._open_pet_card()
            return
        else:
            return
        self._pet_react(r)

    # ── roster ───────────────────────────────────────────────────────
    def _pet_switch(self, step: int = 1) -> None:
        pet = get_roster().switch(step)
        pen = self._pet_pen()
        if pen is not None:
            pen.x = max(0.0, min(pen.max_x, pen.x))
        save_pet()
        self._pet_react(Reaction("hatch" if pet.is_egg else "wave", 1.8,
                                 f"hi, it's {pet.name}! ♥" if not pet.is_egg else greeting(pet)))

    def _pet_adopt_dialog(self) -> None:
        from ..pet_modal import PetAdoptScreen
        from ..text_input_modal import TextInputScreen
        from ...pet.model import MAX_PETS

        if len(get_roster().pets) >= MAX_PETS:
            self._pet_say(f"the pen is full ({MAX_PETS} pets) ♥", 4.0)
            return

        def named(species: str, name: str | None) -> None:
            if name is None:
                return
            try:
                pet = get_roster().adopt(species, name.strip())
            except ValueError as e:
                self._pet_say(str(e), 4.0)
                return
            save_pet()
            if pet.is_egg:
                line = f"an egg! it hatches after {pet.hatch_left} turns together ✦"
            else:
                line = f"welcome home, {pet.name}! ♥"
            self._pet_react(Reaction("hatch" if not pet.is_egg else "wobble", 2.6, line))

        def picked(species: str | None) -> None:
            if not species:
                return
            from ...pet.model import new_pet

            default = new_pet(species).name
            self.push_screen(
                TextInputScreen(f"Name your new {pet_pkg.SPECIES_LABELS[species]}",
                                body=f"Leave empty for {default}.", placeholder=default),
                lambda name: named(species, name if name is not None else None),
            )

        self.push_screen(PetAdoptScreen(), picked)

    # ── fish game & focus timer ──────────────────────────────────────
    def _pet_fish_toggle(self) -> None:
        pen = self._pet_pen()
        if pen is None or not self._pet_pen_visible():
            self._pet_say("the fish game lives in the sidebar pen (⌃B) ✦", 4.0)
            return
        if get_pet().is_egg:
            self._pet_say("*wobble* …eggs can't fish yet!", 3.0)
            return
        if pen.game is not None:
            pen.stop_game()
        else:
            pen.start_game()

    def _pet_fish_done(self, caught: int) -> None:
        self._pet_react(get_pet().on_fish_round(caught))

    def _pet_focus_state(self) -> tuple[str, float] | None:
        now = time.time()
        if now < self._pet_focus_until:
            return ("focus", self._pet_focus_until - now)
        if now < self._pet_break_until:
            return ("break", self._pet_break_until - now)
        return None

    def _pet_focus_toggle(self) -> None:
        state = self._pet_focus_state()
        if state and state[0] == "focus":
            self._pet_focus_until = 0.0
            self._pet_react(Reaction("wave", 1.6, "focus stopped — no worries ♥"))
            return
        self._pet_focus_until = time.time() + FOCUS_SECS
        self._pet_break_until = 0.0
        self._pet_react(Reaction("happy", 1.6, "focus mode ✦ 25 min — I'll keep your keys warm"))

    def _pet_focus_tick(self) -> None:
        now = time.time()
        if self._pet_focus_until and now >= self._pet_focus_until:
            self._pet_focus_until = 0.0
            self._pet_break_until = now + BREAK_SECS
            self._pet_react(get_pet().on_focus_done())
            self._pet_notify("focus session done ✦ time for a 5-minute break")
            try:
                self.bell()
            except Exception:
                pass
        elif self._pet_break_until and now >= self._pet_break_until:
            self._pet_break_until = 0.0
            self._pet_react(Reaction("wave", 2.0, "break's over — ready when you are ♥"))
            self._pet_notify("break's over — ready when you are ♥")

    def _pet_notify(self, message: str) -> None:
        if not self._pet_setting("notify"):
            return
        try:
            from ...utils.notify import desktop_notify

            desktop_notify(f"{get_pet().name} ♥", message)
        except Exception:
            pass

    def _open_pet_card(self) -> None:
        from textual.screen import ModalScreen

        if isinstance(self.screen, ModalScreen):
            return
        from ..pet_modal import PetCardScreen

        self._pet_hide_bubble()
        self.push_screen(PetCardScreen())

    # ── events from the app ──────────────────────────────────────────
    def _pet_typing(self) -> None:
        pen = self._pet_pen()
        if pen is not None:
            pen.poke()
        w = self._pet_widget()
        if w is not None:
            woke = w.dozing
            w.typed()
            if woke and self._pet_visible():
                w.play("surprised", 0.8)
        # Your text has the floor (and the / @ popup uses the bubble's row).
        self._pet_hide_bubble()
        self._pet_mark_work()

    def _pet_turn_started(self) -> None:
        for w in (self._pet_widget(), self._pet_pen()):
            if w is not None:
                w.poke()
        get_pet().wake()
        self._pet_hide_bubble()
        self._pet_mark_work()

    def _pet_turn_finished(self, seconds: float, *, interrupted: bool, llm: bool) -> None:
        for w in (self._pet_widget(), self._pet_pen()):
            if w is not None:
                w.poke()
        self._pet_mark_work()
        if not llm:
            return
        pet = get_pet()
        pet.tick(busy=True)
        r = pet.on_turn_done(seconds, interrupted=interrupted)
        if interrupted:
            self._pet_play(r)
            return
        pet_session.current().turns += 1
        away = not getattr(self, "_app_focused", True)
        if seconds >= 45 and away:
            r.say = r.say or "all done ✦ come see!"
        if seconds >= NOTIFY_AFTER and away:
            mins, secs = divmod(int(seconds), 60)
            took = f"{mins}m {secs:02d}s" if mins else f"{secs}s"
            self._pet_notify(f"all done ✦ (took {took})")
        self._pet_react(r)

    def _pet_tool_finished(self, name: str, *, error: bool, tool_input=None, output: str = "") -> None:
        pet = get_pet()
        r = pet.on_tool_done(name, error=error)
        if r is not None:
            self._pet_react(r, bubble=bool(r.level_up or r.badges))
        for event in pet_events.classify(name, tool_input, output, error=error):
            fixed = event == "tests_pass" and pet.tests == "fail"
            pet_session.current().note_event(event, fixed=fixed)
            self._pet_react(pet.on_work_event(event))

    def _pet_diff(self, path: str, added: int, removed: int) -> None:
        pet_session.current().note_diff(path, added, removed)
        r = get_pet().on_diff(added, removed)
        if r is not None:
            self._pet_react(r)

    def _pet_mark_work(self) -> None:
        now = time.time()
        if now - getattr(self, "_pet_last_work", now) > STREAK_GAP:
            self._pet_streak_t0 = now
        self._pet_last_work = now

    # ── periodic (every ~2s from _slow_refresh) ──────────────────────
    def _pet_slow_tick(self) -> None:
        pet = get_pet()
        pet.tick(busy=bool(getattr(self, "_busy", False)))
        self._pet_save(force=False)
        self._pet_focus_tick()
        if not self._pet_visible() or getattr(self, "_busy", False) or self._pet_focus_state():
            return
        if not self._pet_setting("nudges"):
            return
        line = self._pet_nudge_line(time.time())
        if line:
            self._pet_play(Reaction("wave", 2.0))
            self._pet_say(line, 9.0)

    def _pet_nudge_line(self, now: float) -> str:
        """The most useful nudge right now, or '' (rate-limited)."""
        if now - self._pet_last_nudge < NUDGE_SPACING:
            return ""
        pet = get_pet()
        recent_work = now - self._pet_last_work < 120

        def fire(kind: str, every: float, text: str) -> str:
            if now - self._pet_nudged.get(kind, 0.0) < every:
                return ""
            self._pet_nudged[kind] = now
            self._pet_last_nudge = now
            return text

        streak = self._pet_last_work - self._pet_streak_t0
        if recent_work and streak >= BREAK_AFTER:
            mins = int(streak // 60)
            return fire("break", BREAK_AFTER,
                        f"we've been at it {mins // 60}h{mins % 60:02d}m — stretch break? ♥")
        try:
            from .. import sidebar
            from ... import state

            window = sidebar.context_window(state.MODEL)
            used = int(state.total_in or 0) + int(state.total_out or 0)
            if window and used / window >= 0.85:
                return fire("context", 10**9,
                            f"context is {used / window:.0%} full — /new starts fresh")
        except Exception:
            pass
        hour = datetime.fromtimestamp(now).hour
        if recent_work and 0 <= hour < 5:
            return fire("late", 10**9,
                        f"it's {datetime.fromtimestamp(now):%-I:%M}am… commit & sleep soon? ♥")
        if pet.fullness < 25:
            return fire("hungry", 45 * 60, "psst… I'm hungry (/pet feed) ♥")
        if pet.happiness < 30:
            return fire("lonely", 60 * 60, "pat me? (click me) ♥")
        if pet.energy < 15:
            return fire("sleepy", 60 * 60, "*yawn* …a little nap? (/pet nap)")
        return ""

