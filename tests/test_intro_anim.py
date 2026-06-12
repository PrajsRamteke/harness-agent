"""Welcome-art shine animation: frame fidelity and the TUI sweep→settle flow."""
import asyncio

import pytest

from jarvis.tui.intro_anim import shine_frame, sweep_centers, _blend


ART = (
    "\n"
    "  ██╗  ██╗ █████╗\n"
    "  ██║  ██║██╔══██╗\n"
    "  ╚═╝  ╚═╝╚═════╝\n"
)


def test_shine_frame_preserves_layout_exactly():
    """frame.plain must equal the art for every band position — no drift."""
    for center in sweep_centers(ART):
        assert shine_frame(ART, center, "#ffa198").plain == ART


def test_shine_frame_highlights_band():
    """Characters inside the band get a brighter style than the base."""
    frame = shine_frame(ART, 4.0, "#ffa198")
    styles = {str(span.style) for span in frame.spans}
    assert any("bold" in s for s in styles), "no glow styles in band"
    assert "#ffa198" in styles, "base color missing outside band"


def test_blend_falls_back_on_bad_color():
    assert _blend("not-a-color", "#ffffff", 0.5) == "not-a-color"
    assert _blend("#000000", "#ffffff", 0.5) == "#808080"


def test_sweep_centers_cover_full_width():
    centers = sweep_centers(ART)
    width = max(len(l) for l in ART.split("\n"))
    assert centers[0] < 0
    assert centers[-1] >= width


@pytest.fixture()
def hermetic_app(monkeypatch):
    """JarvisTUI with startup side effects stubbed out."""
    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")

    import jarvis.updater as updater
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(
        mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
        raising=False,
    )
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)

    from jarvis.tui.app import JarvisTUI

    return JarvisTUI


def test_intro_sweep_then_settles_into_banner(hermetic_app):
    """Art is visible while the sweep runs, and the welcome card appears
    exactly once after the animation settles."""
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            # Mid-sweep: art frames are on screen, card not yet printed.
            await pilot.pause(0.3)
            log = app.query_one("#transcript")
            rendered = "\n".join(strip.text for strip in log.lines)
            assert "██" in rendered, "art missing during sweep"

            # After the sweep settles: static banner + welcome card.
            await pilot.pause(1.5)
            rendered = "\n".join(strip.text for strip in log.lines)
            assert "██" in rendered, "art missing after settle"
            assert "JARVIS v" in rendered, "welcome card missing after settle"
            assert rendered.count("JARVIS v") == 1, "welcome card duplicated"

    asyncio.run(run())


def test_intro_abort_guard_keeps_foreign_writes(hermetic_app):
    """A transcript write mid-sweep stops the redraw loop without eating it."""
    async def run() -> None:
        app = hermetic_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.15)
            log = app.query_one("#transcript")
            log.write("user-was-here")
            await pilot.pause(1.5)
            rendered = "\n".join(strip.text for strip in log.lines)
            assert "user-was-here" in rendered, "foreign write was eaten by the sweep"
            assert "JARVIS v" in rendered, "intro never finished after abort"

    asyncio.run(run())
