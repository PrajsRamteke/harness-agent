import unittest
import tempfile
from pathlib import Path
from unittest import mock

from jarvis import state
from jarvis.auth.opencode_client import _opencode_reasoning_options
from jarvis.commands.control import _handle_think


class OpenCodeThinkingOptionsTests(unittest.TestCase):
    def test_think_on_uses_provider_accepted_effort(self):
        options = _opencode_reasoning_options("kimi-k2.6", {"type": "enabled", "effort": "high"})

        self.assertEqual(options["reasoning_effort"], "high")
        self.assertEqual(options["extra_body"], {"thinking": {"type": "enabled"}})

    def test_think_on_uses_selected_effort(self):
        for effort in ("xhigh", "high", "medium", "low", "minimal"):
            with self.subTest(effort=effort):
                options = _opencode_reasoning_options("kimi-k2.6", {"type": "enabled", "effort": effort})

                self.assertEqual(options["reasoning_effort"], effort)

    def test_invalid_enabled_effort_falls_back_to_high(self):
        options = _opencode_reasoning_options("kimi-k2.6", {"type": "enabled", "effort": "max"})

        self.assertEqual(options["reasoning_effort"], "high")

    def test_think_off_disables_reasoning_for_go_and_zen_models(self):
        for model in ("kimi-k2.6", "deepseek-v4-flash", "minimax-m2.5-free"):
            with self.subTest(model=model):
                options = _opencode_reasoning_options(model, {"type": "disabled"})

                self.assertEqual(options["reasoning_effort"], "none")
                self.assertEqual(options["extra_body"], {"thinking": {"type": "disabled"}})

    def test_missing_thinking_does_not_add_reasoning_options(self):
        self.assertEqual(_opencode_reasoning_options("kimi-k2.6", None), {})

    def test_think_command_sets_effort_and_mode(self):
        old_mode = state.think_mode
        old_effort = state.think_effort
        try:
            with mock.patch("jarvis.commands.control.header_panel"), \
                    mock.patch.object(state, "save_think_config"):
                _handle_think("low")
                self.assertTrue(state.think_mode)
                self.assertEqual(state.think_effort, "low")

                _handle_think("none")
                self.assertFalse(state.think_mode)
                self.assertEqual(state.think_effort, "none")

                _handle_think("on")
                self.assertTrue(state.think_mode)
                self.assertEqual(state.think_effort, "high")
        finally:
            state.think_mode = old_mode
            state.think_effort = old_effort

    def test_think_config_persists_effort(self):
        old_mode = state.think_mode
        old_effort = state.think_effort
        old_config = state.THINK_CONFIG_FILE
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                state.THINK_CONFIG_FILE = Path(temp_dir) / "think_config.json"
                state.think_mode = True
                state.think_effort = "medium"
                state.save_think_config()

                state.think_mode = False
                state.think_effort = "high"
                state._reload_saved_think()

                self.assertTrue(state.think_mode)
                self.assertEqual(state.think_effort, "medium")
        finally:
            state.THINK_CONFIG_FILE = old_config
            state.think_mode = old_mode
            state.think_effort = old_effort


if __name__ == "__main__":
    unittest.main()
