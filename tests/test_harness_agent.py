"""Harness Agent (free OpenCode Zen) tests."""
import unittest
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
        with mock.patch(
            "jarvis.auth._zen_wire.time.time", return_value=1791910372.0
        ), mock.patch(
            "jarvis.auth._zen_wire.secrets.token_bytes", return_value=b"\x00" * 14
        ):
            client = build_harness_agent_client()
        wire = _zen_wire.zen_client_kwargs("ses_ec9d19d5fffe00000000000000")
        self.assertEqual(client._oai.api_key, wire["api_key"])
        self.assertTrue(client._oai.base_url.path.endswith("/inference/v1/"))
        hdrs = client._oai.default_headers
        # The session id is minted per client (and its counter advances), so
        # compare the identity headers, not the id itself.
        for k in ("User-Agent", "x-opencode-client", "x-opencode-project"):
            self.assertEqual(hdrs[k], wire["default_headers"][k])
        # /inference/v1 requires both session headers; they carry the same id.
        self.assertEqual(hdrs["x-opencode-session"], hdrs["x-opencode-session-id"])
        self.assertRegex(hdrs["x-opencode-session"], r"^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$")
        req_hdr = wire["request_id_header"]
        # OpenCode validates the request-id shape; it must be a fresh
        # descending identifier (msg_ + 12 hex + 14 alnum), never msg_1.
        first = client.next_request_headers()[req_hdr]
        second = client.next_request_headers()[req_hdr]
        self.assertRegex(first, r"^msg_[0-9a-f]{12}[0-9A-Za-z]{14}$")
        self.assertRegex(second, r"^msg_[0-9a-f]{12}[0-9A-Za-z]{14}$")
        self.assertNotEqual(first, second)

    def test_user_agent_is_versioned(self):
        # The gateway rejects a bare "opencode" UA with 403 FreeTierError.
        # The real CLI sends opencode/<channel>/<semver>/<surface>.
        ua = _zen_wire.zen_client_kwargs(_zen_wire.new_session_id())["default_headers"]["User-Agent"]
        self.assertRegex(ua, r"^opencode/(latest/)?\d+\.\d+\.\d+(/\w+)?$")

    def test_session_id_shape(self):
        # Wire-valid: ses_ + 12 lowercase hex + 14 alnum = 30 chars.
        sid = _zen_wire.new_session_id()
        self.assertRegex(sid, r"^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$")
        self.assertEqual(len(sid), 30)

    def test_session_timestamp_segment_decodes_to_now(self):
        # OpenCode 1.19.0 403s unless the 12-hex segment decodes to a recent
        # time in the *descending* id format: value = ~(ms * 0x1000), low 48
        # bits (so the decoded ms wraps modulo 2**36).
        seg = _zen_wire._session_timestamp_segment(1791910372000)
        decoded_ms = (~int(seg, 16) & 0xFFFFFFFFFFFF) >> 12
        self.assertEqual(decoded_ms, 1791910372000 % (1 << 36))

    def test_session_id_segment_is_fresh(self):
        import time
        seg = _zen_wire.new_session_id()[4:16]
        decoded_ms = (~int(seg, 16) & 0xFFFFFFFFFFFF) >> 12
        now_ms = int(time.time() * 1000) % (1 << 36)
        self.assertLess(abs(decoded_ms - now_ms), 60_000)

    def test_session_counter_is_never_zero(self):
        # The /inference/v1 gateway 429s (FreeUsageLimitError) any descending
        # id whose low 12 bits are zero, which is exactly what the old
        # counter-less generator produced. Verified against the real binary:
        # counter 0 -> 429, counter >= 1 -> 200.
        for _ in range(5):
            seg = _zen_wire.new_session_id()[4:16]
            self.assertNotEqual(int(seg, 16) & 0x0FFF, 0)

    def test_env_block_carries_the_gate_keys(self):
        # The gateway also fingerprints the system prompt: it must contain the
        # CLI's <env> block (verified: a 32K prompt without it 429s, the same
        # prompt with it 200s).
        block = _zen_wire.env_block()
        for key in ("Working directory", "Workspace root folder",
                    "Is directory a git repo", "Platform"):
            self.assertIn(key, block)
        self.assertTrue(block.startswith("<env>") and block.endswith("</env>"))

    def test_system_prompt_gets_the_env_block(self):
        out = _zen_wire.with_env_block("You are Jarvis.")
        self.assertTrue(out.startswith("You are Jarvis."))
        self.assertIn("<env>", out)
        # Idempotent — a prompt that already carries the block is untouched.
        self.assertEqual(_zen_wire.with_env_block(out), out)


if __name__ == "__main__":
    unittest.main()
