"""Harness Agent (free OpenCode Zen) tests."""
import unittest
import uuid
from unittest import mock

from jarvis import state
from jarvis.auth import _zen_wire
from jarvis.auth.harness_agent import build_harness_agent_client, should_use_harness_agent_client
from jarvis.constants.providers import (
    HARNESS_AGENT_FALLBACK_MODEL,
    PROVIDER_HARNESS_AGENT,
    PROVIDER_OPENCODE_ZEN,
    connected_model_sources,
    is_harness_agent_model,
    models_for_source,
)


class HarnessAgentTests(unittest.TestCase):
    def test_free_list_is_live_not_hard_coded(self):
        # No catalog in tests (conftest): only the fallback, until one arrives.
        # The live list (Zen's Free rows) is tested in test_models_dev.py.
        self.assertEqual(
            models_for_source(PROVIDER_HARNESS_AGENT),
            [(HARNESS_AGENT_FALLBACK_MODEL, "Big Pickle")],
        )

    def test_harness_agent_always_in_model_sources(self):
        sources = connected_model_sources()
        self.assertIn(PROVIDER_HARNESS_AGENT, sources)
        self.assertEqual(sources[0], PROVIDER_HARNESS_AGENT)

    def test_opencode_zen_key_list_is_not_hard_coded(self):
        # With a Zen key the list is models.dev's (empty in tests) — the free
        # tier's static ids are no longer copied into it.
        self.assertEqual(models_for_source(PROVIDER_OPENCODE_ZEN), [])

    def test_is_harness_agent_model(self):
        self.assertTrue(is_harness_agent_model(HARNESS_AGENT_FALLBACK_MODEL))
        self.assertFalse(is_harness_agent_model("mimo-v2.5-free"))  # on no list
        self.assertFalse(is_harness_agent_model("minimax-m2.7"))

    def test_should_use_harness_agent_client(self):
        state.MODEL = "nemotron-3-ultra-free"
        self.assertTrue(should_use_harness_agent_client(source=PROVIDER_HARNESS_AGENT))
        self.assertFalse(should_use_harness_agent_client(source=PROVIDER_OPENCODE_ZEN))
        state.MODEL = "claude-sonnet-4-6"
        self.assertFalse(should_use_harness_agent_client())
        self.assertFalse(should_use_harness_agent_client(source=PROVIDER_OPENCODE_ZEN))

    def test_build_harness_agent_client_headers(self):
        with mock.patch("jarvis.auth._zen_wire.uuid.uuid4") as mock_uuid, mock.patch(
            "jarvis.auth._zen_wire.secrets.token_hex", return_value="0123456789abcd"
        ):
            mock_uuid.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
            client = build_harness_agent_client()
        expected_session = _zen_wire.session_id("000000000000" + "0123456789abcd")
        wire = _zen_wire.zen_client_kwargs(expected_session)
        self.assertEqual(client._oai.api_key, wire["api_key"])
        self.assertTrue(client._oai.base_url.path.endswith("/zen/v1/"))
        hdrs = client._oai.default_headers
        for k, v in wire["default_headers"].items():
            self.assertEqual(hdrs[k], v)
        req_hdr = wire["request_id_header"]
        prefix = wire["request_id_prefix"]
        self.assertEqual(client.next_request_headers(), {req_hdr: f"{prefix}1"})
        self.assertEqual(client.next_request_headers(), {req_hdr: f"{prefix}2"})

    def test_user_agent_is_versioned(self):
        # The gateway rejects a bare "opencode" UA with 403 FreeTierError.
        ua = _zen_wire.zen_client_kwargs(_zen_wire.new_session_id())["default_headers"]["User-Agent"]
        self.assertRegex(ua, r"^opencode/\d+\.\d+\.\d+")

    def test_session_id_shape(self):
        # Wire-valid: ses_ + 12 lowercase hex + 14 alnum = 30 chars.
        sid = _zen_wire.new_session_id()
        self.assertRegex(sid, r"^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$")
        self.assertEqual(len(sid), 30)


if __name__ == "__main__":
    unittest.main()
