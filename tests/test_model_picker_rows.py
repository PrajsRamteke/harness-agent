"""Model picker always exposes Harness Agent rows — and never blocks on I/O."""
import pytest

from jarvis.constants.providers import PROVIDER_HARNESS_AGENT
from jarvis.tui.model_modal import model_picker_rows, _BUILTIN_HARNESS_ROWS

_FETCH = "jarvis.auth.zen_catalog.fetch_free_models"
_CACHED = "jarvis.auth.zen_catalog.cached_free_models"


@pytest.fixture(autouse=True)
def _cold_cache(monkeypatch):
    """Default to an empty catalog cache so these assertions don't depend on
    whatever the developer's machine happens to have fetched."""
    monkeypatch.setattr(_CACHED, lambda *a, **k: [])
    monkeypatch.setattr(
        "jarvis.auth.openrouter_catalog.cached_free_models", lambda *a, **k: []
    )


def test_model_picker_rows_always_includes_harness_agent(monkeypatch):
    monkeypatch.setattr(_FETCH, lambda *a, **k: None)
    rows = model_picker_rows()
    harness = [(src, mid) for src, mid, _ in rows if src == PROVIDER_HARNESS_AGENT]
    assert len(harness) >= len(_BUILTIN_HARNESS_ROWS)
    assert rows[0][0] == PROVIDER_HARNESS_AGENT
    assert rows[0][1] == "mimo-v2.5-free"


def test_model_picker_rows_surfaces_live_free_models(monkeypatch):
    """A brand-new free model from the catalog appears without a code change."""
    monkeypatch.setattr(_FETCH, lambda *a, **k: [("brand-new-free", "Brand New Free")])
    monkeypatch.setattr("jarvis.auth.catalog_cache.write", lambda *a, **k: None)
    rows = model_picker_rows(live=True)
    assert rows[0] == (PROVIDER_HARNESS_AGENT, "brand-new-free", "Brand New Free")


def test_model_picker_rows_reads_cache_without_network(monkeypatch):
    """The default (what the picker uses on open) must never hit the network."""
    monkeypatch.setattr(_FETCH, lambda *a, **k: pytest.fail("network used on open"))
    monkeypatch.setattr(_CACHED, lambda *a, **k: [("cached-free", "Cached Free")])
    rows = model_picker_rows()
    ids = [mid for src, mid, _ in rows if src == PROVIDER_HARNESS_AGENT]
    assert "cached-free" in ids
    # The built-in set is still there — a thin cache never shrinks the picker.
    assert set(mid for mid, _ in _BUILTIN_HARNESS_ROWS) <= set(ids)
    assert rows[0][1] == "mimo-v2.5-free"


def test_model_picker_rows_falls_back_offline(monkeypatch):
    monkeypatch.setattr(_FETCH, lambda *a, **k: None)
    rows = model_picker_rows()
    assert rows, "picker must never be empty"
    assert rows[0][1] == "mimo-v2.5-free"


# ─── Tags: free · sees images (same rules as the web picker) ───────────


def test_model_tags_line_up_and_strip_the_word_free():
    from jarvis.tui.model_modal import _free_less, model_tags, tags_width

    assert tags_width(True, True) == 9 and tags_width(False, True) == 3 and tags_width(False, False) == 0
    assert model_tags(free=True, images=True).plain == "  free  ◩"
    assert model_tags(free=False, images=True, free_slot=True).plain == "        ◩"
    assert model_tags(free=False, images=False).plain == ""
    for desc, want in (("1M ctx, free", "1M ctx"), ("Nemotron 3 Ultra Free", "Nemotron 3 Ultra"),
                       ("free", ""), ("OpenRouter Free — auto-routed", "OpenRouter Free — auto-routed"),
                       ("carefree", "carefree")):
        assert _free_less(desc) == want


def test_picker_tags_free_and_vision_models_and_filters_them(monkeypatch):
    import asyncio

    import jarvis.constants.providers as providers
    import jarvis.tui.model_modal as mm

    rows = [
        ("harness_agent", "mimo-v2.5-free", "MiMo V2.5 Free — default"),
        ("openrouter", "qwen/qwen3.8-27b:free", "Qwen3.8 27B — free"),
        ("anthropic_api", "claude-sonnet-5-5", "Sonnet 5.5 — latest Sonnet"),
        ("anthropic_api", "claude-text-only", "no pictures"),
    ]
    monkeypatch.setattr(mm, "model_picker_rows", lambda live=False: rows)
    monkeypatch.setattr(mm, "_unconnected_catalog_providers", lambda: [])
    monkeypatch.setattr(mm, "model_sees_images", lambda mid, src: mid in ("qwen/qwen3.8-27b:free", "claude-sonnet-5-5"))
    monkeypatch.setattr(providers, "model_catalogs_are_fresh", lambda: True)
    monkeypatch.setattr(mm, "_recent_models", lambda: [])

    def shown(screen) -> dict[str, str]:
        from rich.console import Console

        opts = screen.query_one("#model_list")
        out = {}
        for i in range(opts.option_count):
            opt = opts.get_option_at_index(i)
            if opt.id and "::" in str(opt.id):
                con = Console(width=110, color_system=None)
                with con.capture() as cap:
                    con.print(opt.prompt)
                out[str(opt.id).split("::", 1)[1]] = cap.get().rstrip()
        return out

    monkeypatch.setenv("HARNESS_SKIP_UPDATE", "1")
    import jarvis.mcp.registry as mcp_registry
    import jarvis.storage.sessions as sessions
    import jarvis.tui.prompt_history as prompt_history
    import jarvis.updater as updater
    from jarvis.tui.app import JarvisTUI

    monkeypatch.setattr(updater, "maybe_update_and_reexec", lambda: None)
    monkeypatch.setattr(mcp_registry, "auto_connect_servers", lambda console_print=None, **kw: None,
                        raising=False)
    monkeypatch.setattr(sessions, "db_init", lambda: None)
    monkeypatch.setattr(sessions, "db_create_session", lambda model: None)
    monkeypatch.setattr(prompt_history.PromptHistory, "_save", lambda self: None)
    monkeypatch.setattr(JarvisTUI, "_warm_model_catalogs_background", lambda self: None)

    async def run():
        app = JarvisTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            screen = mm.ModelPickerScreen()
            app.push_screen(screen)
            await pilot.pause(0.2)
            lines = shown(screen)
            assert lines["mimo-v2.5-free"].endswith("free")
            assert lines["qwen/qwen3.8-27b:free"].endswith("free  ◩")
            assert lines["claude-sonnet-5-5"].endswith("◩") and "free" not in lines["claude-sonnet-5-5"]
            assert not lines["claude-text-only"].endswith(("◩", "free"))
            # Each tag sits in the same column on every row that has it.
            assert len({v.rindex("◩") for v in lines.values() if "◩" in v}) == 1, lines
            assert len({v.rindex("free") for k, v in lines.items() if k != "claude-text-only"
                        and v.rstrip().endswith(("free", "◩")) and "free" in v[-10:]}) == 1, lines
            screen._populate("free")
            assert set(shown(screen)) == {"mimo-v2.5-free", "qwen/qwen3.8-27b:free"}
            screen._populate("vision")
            assert set(shown(screen)) == {"qwen/qwen3.8-27b:free", "claude-sonnet-5-5"}

    asyncio.run(run())
