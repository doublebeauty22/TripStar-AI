import unittest
from types import SimpleNamespace

from hello_agents import HelloAgentsLLM

from backend.app.services.llm_service import (
    OpenAICompatibilityClient,
    _sanitize_chat_completion_kwargs,
    normalize_finish_reason,
    structured_output_metadata,
)


class _RecordingCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )


def _recording_client():
    completions = _RecordingCompletions()
    raw_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    return OpenAICompatibilityClient(raw_client), completions


class LlmCompatibilityTests(unittest.TestCase):
    def test_finish_reason_normalization_and_limit_evidence(self):
        for raw, expected in (("stop", "stop"), ("length", "length"), ("tool_calls", "other"), (None, "missing")):
            with self.subTest(raw=raw):
                response = SimpleNamespace(
                    choices=[SimpleNamespace(finish_reason=raw)],
                    usage=SimpleNamespace(completion_tokens=4000),
                )
                metadata = structured_output_metadata(response, 4000)
                self.assertEqual(normalize_finish_reason(response), expected)
                self.assertTrue(metadata["limit_observed"])
        self.assertEqual(normalize_finish_reason(SimpleNamespace(choices=[])), "missing")

    def test_unset_max_tokens_is_omitted(self):
        request = _sanitize_chat_completion_kwargs(
            {"model": "gpt-4.1", "messages": [], "max_tokens": None}
        )

        self.assertNotIn("max_tokens", request)
        self.assertNotIn("max_completion_tokens", request)

    def test_explicit_legacy_token_limit_is_preserved(self):
        request = _sanitize_chat_completion_kwargs(
            {"model": "gpt-4o", "messages": [], "max_tokens": 512}
        )

        self.assertEqual(request["max_tokens"], 512)
        self.assertNotIn("max_completion_tokens", request)

    def test_gpt_5_token_limit_uses_max_completion_tokens_without_conflict(self):
        request = _sanitize_chat_completion_kwargs(
            {
                "model": "gpt-5.6-luna",
                "messages": [],
                "max_tokens": 768,
                "max_completion_tokens": 1024,
            }
        )

        self.assertEqual(request["max_completion_tokens"], 1024)
        self.assertNotIn("max_tokens", request)

    def test_gpt_5_unsupported_temperature_is_omitted(self):
        request = _sanitize_chat_completion_kwargs(
            {"model": "gpt-5.6-luna", "messages": [], "temperature": 0.7}
        )

        self.assertNotIn("temperature", request)

    def test_gpt_5_6_luna_helloagents_invoke_omits_unset_limit(self):
        llm = HelloAgentsLLM(
            model="gpt-5.6-luna",
            api_key="test-key",
            base_url="https://example.invalid/v1",
        )
        client, completions = _recording_client()
        llm._client = client

        result = llm.invoke([{"role": "user", "content": "ping"}])

        self.assertEqual(result, "ok")
        self.assertEqual(len(completions.requests), 1)
        self.assertNotIn("max_tokens", completions.requests[0])
        self.assertNotIn("max_completion_tokens", completions.requests[0])

    def test_gpt_5_6_luna_explicit_limit_is_mapped_during_invoke(self):
        llm = HelloAgentsLLM(
            model="gpt-5.6-luna",
            api_key="test-key",
            base_url="https://example.invalid/v1",
        )
        client, completions = _recording_client()
        llm._client = client

        result = llm.invoke(
            [{"role": "user", "content": "ping"}],
            max_tokens=640,
        )

        self.assertEqual(result, "ok")
        self.assertEqual(completions.requests[0]["max_completion_tokens"], 640)
        self.assertNotIn("max_tokens", completions.requests[0])


if __name__ == "__main__":
    unittest.main()
