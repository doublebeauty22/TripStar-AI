import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from backend.app.services.chat_service import chat_with_trip_context
from backend.app.services.llm_service import OpenAICompatibilityClient


class _RecordingCompletions:
    def __init__(self, *, error=None):
        self.error = error
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="safe reply"))],
            usage=None,
        )


def _fake_llm(model="gpt-5.6-luna", *, error=None):
    completions = _RecordingCompletions(error=error)
    raw_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    llm = SimpleNamespace(
        model=model,
        _client=OpenAICompatibilityClient(raw_client),
    )
    return llm, completions


class _ProviderStatusError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code


class ChatServiceTests(unittest.IsolatedAsyncioTestCase):
    def _configured_settings(self):
        return SimpleNamespace(openai_api_key="configured")

    async def test_chat_uses_compatibility_path_and_preserves_context(self):
        llm, completions = _fake_llm()
        trip_plan = {"city": "京都", "days": [{"description": "清水寺"}]}
        history = [
            {"role": "user", "content": "之前的问题"},
            {"role": "assistant", "content": "之前的回答"},
        ]

        with patch("backend.app.services.chat_service.get_settings", return_value=self._configured_settings()), patch(
            "backend.app.services.chat_service.get_llm", return_value=llm
        ):
            reply = await chat_with_trip_context("这个行程适合老人吗？", trip_plan, history)

        self.assertEqual(reply, "safe reply")
        self.assertEqual(len(completions.requests), 1)
        request = completions.requests[0]
        self.assertEqual(request["model"], "gpt-5.6-luna")
        self.assertEqual(request["max_completion_tokens"], 1024)
        self.assertNotIn("max_tokens", request)
        self.assertNotIn("temperature", request)
        self.assertEqual(
            [item["role"] for item in request["messages"]],
            ["system", "user", "user", "assistant", "user"],
        )
        self.assertIn('"city": "京都"', request["messages"][1]["content"])
        self.assertIn("清水寺", request["messages"][1]["content"])
        self.assertEqual(request["messages"][2:4], history)
        self.assertEqual(request["messages"][-1]["content"], "这个行程适合老人吗？")

    async def test_legacy_model_keeps_legacy_parameters(self):
        llm, completions = _fake_llm("gpt-4o")

        with patch("backend.app.services.chat_service.get_settings", return_value=self._configured_settings()), patch(
            "backend.app.services.chat_service.get_llm", return_value=llm
        ):
            await chat_with_trip_context("question", {"city": "Tokyo"})

        request = completions.requests[0]
        self.assertEqual(request["max_tokens"], 1024)
        self.assertEqual(request["temperature"], 0.7)
        self.assertNotIn("max_completion_tokens", request)

    async def test_provider_400_is_safe_and_does_not_leak_body(self):
        secret_body = "raw-provider-sensitive-detail-do-not-log"
        llm, _ = _fake_llm(error=_ProviderStatusError(400, secret_body))
        output = io.StringIO()

        with patch("backend.app.services.chat_service.get_settings", return_value=self._configured_settings()), patch(
            "backend.app.services.chat_service.get_llm", return_value=llm
        ), redirect_stdout(output):
            reply = await chat_with_trip_context("question", {"city": "Tokyo"})

        self.assertIn("HTTP 400", reply)
        self.assertNotIn(secret_body, reply)
        self.assertNotIn(secret_body, output.getvalue())
        self.assertIn("category=non_retryable_request", output.getvalue())
        self.assertIn("status=400", output.getvalue())

    async def test_provider_unavailable_returns_safe_fallback(self):
        raw_detail = "upstream unavailable with hidden provider detail"
        llm, completions = _fake_llm(error=_ProviderStatusError(503, raw_detail))
        output = io.StringIO()

        with patch("backend.app.services.chat_service.get_settings", return_value=self._configured_settings()), patch(
            "backend.app.services.chat_service.get_llm", return_value=llm
        ), redirect_stdout(output):
            reply = await chat_with_trip_context("question", {"city": "Tokyo"})

        self.assertIn("HTTP 503", reply)
        self.assertNotIn(raw_detail, reply)
        self.assertNotIn(raw_detail, output.getvalue())
        self.assertEqual(len(completions.requests), 2)
        self.assertIn("category=transient_http", output.getvalue())

    async def test_timeout_returns_safe_fallback(self):
        raw_detail = "timeout contacting provider with hidden detail"
        llm, completions = _fake_llm(error=httpx.TimeoutException(raw_detail))
        output = io.StringIO()

        with patch("backend.app.services.chat_service.get_settings", return_value=self._configured_settings()), patch(
            "backend.app.services.chat_service.get_llm", return_value=llm
        ), redirect_stdout(output):
            reply = await chat_with_trip_context("question", {"city": "Tokyo"})

        self.assertIn("超时", reply)
        self.assertNotIn(raw_detail, reply)
        self.assertNotIn(raw_detail, output.getvalue())
        self.assertEqual(len(completions.requests), 2)
        self.assertIn("category=transient_network", output.getvalue())

    async def test_missing_key_does_not_initialize_provider(self):
        with patch(
            "backend.app.services.chat_service.get_settings",
            return_value=SimpleNamespace(openai_api_key=""),
        ), patch("backend.app.services.chat_service.get_llm") as mocked_get_llm:
            reply = await chat_with_trip_context("question", {"city": "Tokyo"})

        self.assertIn("尚未配置 API Key", reply)
        mocked_get_llm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
