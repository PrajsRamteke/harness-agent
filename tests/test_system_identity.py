import unittest
from unittest import mock

from jarvis import state
from jarvis.constants import AUTH_API_KEY, AUTH_OAUTH, OAUTH_IDENTITY
from jarvis.constants.system_prompt import build_base_system
from jarvis.repl import system


class SystemIdentityTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            "auth_mode": state.auth_mode,
            "MODEL": state.MODEL,
            "provider": state.provider,
            "harness_agent_free": state.harness_agent_free,
            "active_agent": state.active_agent,
            "active_agent_name": state.active_agent_name,
        }
        system.invalidate_system_cache()

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(state, name, value)
        system.invalidate_system_cache()

    def test_base_prompt_identifies_as_jarvis_not_claude_code(self):
        prompt = build_base_system()
        self.assertIn("Jarvis", prompt)
        self.assertIn("Harness Agent", prompt)
        self.assertIn("Never call yourself Claude Code", prompt)

    def test_selected_model_block_includes_id_and_name(self):
        state.MODEL = "claude-sonnet-4-6"
        state.provider = "anthropic"
        state.harness_agent_free = False
        state.auth_mode = AUTH_API_KEY
        with mock.patch.object(system, "_build_static_body", return_value="BASE"):
            prompt = system.build_system()
        self.assertTrue(prompt.startswith("SELECTED MODEL: claude-sonnet-4-6"))
        self.assertIn("MODEL NAME: Sonnet 4.6 — balanced", prompt)
        self.assertIn("PROVIDER: Anthropic", prompt)

    def test_oauth_prepends_wire_block_and_identity_override(self):
        state.auth_mode = AUTH_OAUTH
        state.MODEL = "claude-sonnet-4-6"
        with mock.patch.object(system, "_build_static_body", return_value="BASE"):
            blocks = system.build_system()
        self.assertIsInstance(blocks, list)
        self.assertEqual(blocks[0]["text"], OAUTH_IDENTITY)
        self.assertIn("OAUTH WIRE BLOCK", blocks[1]["text"])
        self.assertIn("Jarvis (Harness Agent)", blocks[1]["text"])
        self.assertIn("SELECTED MODEL: claude-sonnet-4-6", blocks[1]["text"])
        self.assertIn("MODEL NAME: Sonnet 4.6 — balanced", blocks[1]["text"])

    def test_harness_agent_free_provider_label(self):
        state.harness_agent_free = True
        state.provider = "opencode_zen"
        state.MODEL = "deepseek-v4-flash-free"
        state.auth_mode = AUTH_API_KEY
        with mock.patch.object(system, "_build_static_body", return_value="BASE"):
            prompt = system.build_system()
        self.assertIn("SELECTED MODEL: deepseek-v4-flash-free", prompt)
        self.assertIn("MODEL NAME: DeepSeek V4 Flash Free — default", prompt)
        self.assertIn("PROVIDER: Harness Agent (free)", prompt)
