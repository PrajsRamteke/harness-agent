"""Freebuff provider tests."""
import json
import os
import tempfile
import unittest
from unittest import mock

from jarvis.constants.providers import (
    FREEBUFF_DEFAULT_MODEL,
    FREEBUFF_MODEL_IDS,
    FREEBUFF_MODELS,
    FREEBUFF_WIRE,
    PROVIDER_FREEBUFF,
    PROVIDER_OPENCODE,
    connected_model_sources,
    freebuff_wire,
    is_freebuff_model,
    model_belongs_to_provider,
    models_for_source,
    normalize_model_for_provider,
)
from jarvis.auth.freebuff import has_freebuff_credentials, load_freebuff_token


class FreebuffProviderTests(unittest.TestCase):
    def test_freebuff_models_registered(self):
        ids = {m for m, _ in FREEBUFF_MODELS}
        self.assertEqual(ids, FREEBUFF_MODEL_IDS)
        self.assertIn("freebuff-deepseek-flash", ids)
        self.assertIn("freebuff-kimi", ids)

    def test_freebuff_wire_mapping(self):
        api_model, agent_id = freebuff_wire("freebuff-deepseek-flash")
        self.assertEqual(api_model, "deepseek/deepseek-v4-flash")
        self.assertEqual(agent_id, "base2-free-deepseek-flash")
        self.assertEqual(
            freebuff_wire("unknown-slug"),
            FREEBUFF_WIRE[FREEBUFF_DEFAULT_MODEL],
        )

    def test_is_freebuff_model(self):
        self.assertTrue(is_freebuff_model("freebuff-minimax"))
        self.assertFalse(is_freebuff_model("deepseek-v4-flash-free"))

    def test_model_belongs_to_provider(self):
        self.assertTrue(model_belongs_to_provider("freebuff-kimi", PROVIDER_FREEBUFF))
        self.assertFalse(model_belongs_to_provider("freebuff-kimi", PROVIDER_OPENCODE))

    def test_normalize_model_for_provider(self):
        self.assertEqual(
            normalize_model_for_provider("claude-sonnet-4-6", PROVIDER_FREEBUFF),
            FREEBUFF_DEFAULT_MODEL,
        )
        self.assertEqual(
            normalize_model_for_provider("freebuff-deepseek-pro", PROVIDER_FREEBUFF),
            "freebuff-deepseek-pro",
        )

    def test_models_for_source(self):
        models = models_for_source(PROVIDER_FREEBUFF)
        self.assertEqual(len(models), 4)
        self.assertEqual(models[0][0], FREEBUFF_DEFAULT_MODEL)

    def test_has_freebuff_credentials_env(self):
        with mock.patch.dict(os.environ, {"FREEBUFF_AUTH_TOKEN": "tok123"}, clear=False):
            self.assertTrue(has_freebuff_credentials())

    def test_has_freebuff_credentials_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            creds = os.path.join(tmp, "credentials.json")
            with open(creds, "w", encoding="utf-8") as f:
                json.dump({"default": {"authToken": "abc"}}, f)
            with mock.patch("jarvis.auth.freebuff.CREDENTIALS_FILE", creds):
                self.assertTrue(has_freebuff_credentials())
                self.assertEqual(load_freebuff_token(), "abc")

    def test_connected_model_sources_includes_freebuff_when_configured(self):
        with mock.patch.dict(os.environ, {"FREEBUFF_AUTH_TOKEN": "tok"}, clear=False):
            sources = connected_model_sources()
            self.assertIn(PROVIDER_FREEBUFF, sources)


class FreebuffClientTests(unittest.TestCase):
    def test_bootstrap_and_client_headers(self):
        from jarvis.auth.freebuff_client import FreebuffClient

        session_resp = mock.Mock()
        session_resp.json.return_value = {"instanceId": "inst-123"}
        session_resp.raise_for_status = mock.Mock()

        run_resp = mock.Mock()
        run_resp.json.return_value = {"runId": "run-456"}
        run_resp.raise_for_status = mock.Mock()

        http_client = mock.Mock()
        http_client.post.side_effect = [session_resp, run_resp]
        http_client.__enter__ = mock.Mock(return_value=http_client)
        http_client.__exit__ = mock.Mock(return_value=False)

        with mock.patch("jarvis.auth.freebuff_client.httpx.Client", return_value=http_client):
            with mock.patch("jarvis.auth.freebuff_client.OpenAI") as mock_oai:
                client = FreebuffClient("test-token", "freebuff-kimi")

        self.assertEqual(client.instance_id, "inst-123")
        self.assertEqual(client.run_id, "run-456")
        self.assertEqual(client.api_model, "moonshotai/kimi-k2.6")
        mock_oai.assert_called_once()
        kwargs = mock_oai.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "test-token")
        self.assertTrue(kwargs["base_url"].endswith("/api/v1/"))

        http_client.post.assert_any_call(
            "https://www.codebuff.com/api/v1/freebuff/session",
            headers={
                "Authorization": "Bearer test-token",
                "Content-Type": "application/json",
                "x-freebuff-model": "moonshotai/kimi-k2.6",
            },
        )
        http_client.post.assert_any_call(
            "https://www.codebuff.com/api/v1/agent-runs",
            headers={
                "Authorization": "Bearer test-token",
                "Content-Type": "application/json",
            },
            json={"action": "START", "agentId": "base2-free-kimi", "ancestorRunIds": []},
        )

    def test_create_completion_injects_metadata(self):
        from jarvis.auth.freebuff_client import FreebuffClient, _FreebuffMessages

        owner = mock.Mock()
        owner.api_model = "deepseek/deepseek-v4-flash"
        owner.run_id = "run-1"
        owner.instance_id = "inst-1"

        oai = mock.Mock()
        msgs = _FreebuffMessages(oai, owner=owner)
        msgs._create_completion(model="freebuff-deepseek-flash", messages=[])

        oai.chat.completions.create.assert_called_once()
        kwargs = oai.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "deepseek/deepseek-v4-flash")
        meta = kwargs["extra_body"]["codebuff_metadata"]
        self.assertEqual(meta["run_id"], "run-1")
        self.assertEqual(meta["freebuff_instance_id"], "inst-1")
        self.assertEqual(meta["cost_mode"], "free")
        self.assertIn("client_id", meta)

    def test_get_freebuff_client_caches_by_token_and_model(self):
        from jarvis.auth.freebuff_client import (
            FreebuffClient,
            clear_freebuff_client_cache,
            get_freebuff_client,
        )

        clear_freebuff_client_cache()
        with mock.patch.object(FreebuffClient, "__init__", return_value=None) as init:
            c1 = get_freebuff_client("tok-a", "freebuff-kimi")
            c2 = get_freebuff_client("tok-a", "freebuff-kimi")
            c3 = get_freebuff_client("tok-a", "freebuff-minimax")
        self.assertIs(c1, c2)
        self.assertIsNot(c1, c3)
        self.assertEqual(init.call_count, 2)
        clear_freebuff_client_cache()


if __name__ == "__main__":
    unittest.main()
