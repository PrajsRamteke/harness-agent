"""Dev-only smoke check: headlessly mount the TUI and probe key wiring.

Run: .venv/bin/python scripts/_verify_tui.py
Catches compose/mount/binding errors that a plain import cannot.
"""
import asyncio
import os
import sys

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-dummy")

PROBE_METHODS = [
    "action_cancel_or_quit", "action_escape_action", "action_cycle_agent",
    "action_toggle_internal", "action_show_shortcuts", "action_open_tools_inspector",
    "_open_palette", "_run_turn", "_begin_turn", "_turn_done",
    "_start_web_remote", "_render_web_bar", "_copy_web_url", "_handle_web_submit",
    "_refresh_tool_dock", "_build_tool_activity_panel", "reset_tool_activity_panel",
    "_build_status_segments", "_write_status_line", "_set_status",
    "_tick_activity_spinner", "_start_activity_pulse", "_stop_activity_pulse",
    "handle_prompt_key_for_file_ref", "close_file_ref_picker", "try_accept_file_ref",
    "_refresh_git_branch", "_refresh_queue_bar", "on_prompt_area_submitted",
]


async def main() -> int:
    from jarvis.tui.app import JarvisTUI, PromptArea  # noqa: F401
    from jarvis.tui import app as app_mod

    # Module-level names that tests / callers import from jarvis.tui.app
    for name in ("PromptArea", "JarvisTUI", "run", "_is_think_picker_command"):
        assert hasattr(app_mod, name), f"jarvis.tui.app.{name} missing after refactor"

    app = JarvisTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        missing = [m for m in PROBE_METHODS if not callable(getattr(app, m, None))]
        if missing:
            print("MISSING METHODS:", missing)
            return 1
    print("HEADLESS MOUNT OK —", len(PROBE_METHODS), "methods present")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
