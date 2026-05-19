import unittest
from unittest import mock

from jarvis import state
from jarvis.mcp import scope as mcp_scope
from jarvis.mcp.registry import as_prompt_block
from jarvis.repl import system


class MCPScopeTests(unittest.TestCase):
    def setUp(self):
        self._global_mcp = state.global_mcp

    def tearDown(self):
        state.global_mcp = self._global_mcp
        system.invalidate_system_cache()

    def test_prompt_block_lists_project_servers_when_global_off(self):
        state.global_mcp = False
        fake_config = mock.Mock()
        fake_config.list_servers.return_value = {"clickup": {}}
        with mock.patch("jarvis.mcp.config.get_config", return_value=fake_config), \
                mock.patch("jarvis.mcp.registry.mcp_registry.list_connected", return_value=[]):
            block = as_prompt_block()
        self.assertIn("MCP (project-only)", block)
        self.assertIn("clickup", block)
        self.assertIn("offline", block)
        self.assertIn("global OFF", block)

    def test_prompt_block_warns_when_global_off_and_no_servers(self):
        state.global_mcp = False
        fake_config = mock.Mock()
        fake_config.list_servers.return_value = {}
        with mock.patch("jarvis.mcp.config.get_config", return_value=fake_config), \
                mock.patch("jarvis.mcp.registry.mcp_registry.list_connected", return_value=[]):
            block = as_prompt_block()
        self.assertIn("global OFF", block)
        self.assertIn("Do NOT read", block)

    def test_apply_mcp_scope_change_invalidates_system_cache(self):
        system._cached_body = "stale"
        with mock.patch("jarvis.mcp.scope.reload_config") as reload, \
                mock.patch("jarvis.mcp.scope.mcp_registry.list_connected", return_value=[]), \
                mock.patch("jarvis.mcp.scope.mcp_registry.is_connected", return_value=False), \
                mock.patch("jarvis.mcp.scope.mcp_registry.connect", return_value=None):
            cfg = mock.Mock()
            cfg.list_servers.return_value = {"clickup": {}}
            cfg.get_auto_connect.return_value = ["clickup"]
            cfg.get_server.return_value = {"type": "stdio", "command": "echo"}
            reload.return_value = cfg
            mcp_scope.apply_mcp_scope_change()
        self.assertEqual(system._cached_body, "")

    def test_get_config_reloads_when_global_scope_changes(self):
        from jarvis.mcp import config as mcp_config

        mcp_config._config = None
        state.global_mcp = False
        first = mcp_config.get_config()
        self.assertFalse(first._include_global)

        state.global_mcp = True
        second = mcp_config.get_config()
        self.assertTrue(second._include_global)
        self.assertIsNot(first, second)


if __name__ == "__main__":
    unittest.main()
