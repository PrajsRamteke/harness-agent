"""Harness Agent (free OpenCode Zen) tests."""
import unittest
import uuid
from unittest import mock

from jarvis import state
from jarvis.auth import _zen_wire
from jarvis.auth.harness_agent import build_harness_agent_client, should_use_harness_agent_client
from jarvis.constants.providers import (
    HARNESS_AGENT_DEFAULT_MODEL,
    HARNESS_AGENT_MODELS,
    OPENCODE_ZEN_MODELS,
    PROVIDER_HARNESS_AGENT,
    PROVIDER_OPENCODE_ZEN,
    connected_model_sources,
    is_harness_agent_model,
    models_for_source,
)


class HarnessAgentTests(unittest.TestCase):
    def test_harness_agent_models_listed(self):
        ids = {m for m, _ in HARNESS_AGENT_MODELS}
        self.assertEqual(
            ids,
            {
                "nemotron-3-ultra-free",
                "mimo-v2.5-free",
                "big-pickle",
                "nemotron-3.5-lightning-free",
                "muse-spark-1.2-contributor-free",
                "muse-spark-1.3-contributor-free",
                "ling-3.0-flash-fin-free",
            },
        )

    def test_harness_agent_always_in_model_sources(self):
        sources = connected_model_sources()
        self.assertIn(PROVIDER_HARNESS_AGENT, sources)
        self.assertEqual(sources[0], PROVIDER_HARNESS_AGENT)

    def test_models_for_harness_agent_source(self):
        models = models_for_source(PROVIDER_HARNESS_AGENT)
        self.assertEqual(len(models), 7)
        self.assertEqual(models[0][0], HARNESS_AGENT_DEFAULT_MODEL)

    def test_opencode_zen_models_include_exclusive_and_shared(self):
        ids = {m for m, _ in OPENCODE_ZEN_MODELS}
        self.assertIn("nemotron-3-ultra-free", ids)
        self.assertIn("mimo-v2.5-free", ids)
        self.assertIn("big-pickle", ids)
        self.assertNotIn("hy3-free", ids)
        self.assertNotIn("minimax-m2.5-free", ids)

    def test_is_harness_agent_model(self):
        self.assertTrue(is_harness_agent_model("nemotron-3-ultra-free"))
        self.assertTrue(is_harness_agent_model("mimo-v2.5-free"))
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
