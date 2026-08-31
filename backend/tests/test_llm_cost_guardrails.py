import unittest
from types import SimpleNamespace

from backend.app.services.llm_service import (
    LLMCallBudgetExceeded,
    create_chat_completion,
    llm_execution,
)


class _Completions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))],
            usage=None,
        )


class _Llm:
    model = "mock-model"
    temperature = 0.7
    max_tokens = None

    def __init__(self, outcomes):
        self.completions = _Completions(outcomes)
        self._client = SimpleNamespace(
            chat=SimpleNamespace(completions=self.completions)
        )


class _HttpError(RuntimeError):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class LlmCostGuardrailTests(unittest.TestCase):
    def _call(self, llm):
        return create_chat_completion(
            stage="planner",
            model=llm.model,
            messages=[{"role": "user", "content": "safe-test"}],
            llm_instance=llm,
        )

    def test_quota_error_is_not_retried(self):
        llm = _Llm([_HttpError(429, "insufficient_quota credit_balance_exhausted")])
        with self.assertRaises(_HttpError):
            self._call(llm)
        self.assertEqual(llm.completions.calls, 1)

    def test_auth_error_is_not_retried(self):
        llm = _Llm([_HttpError(401, "authentication invalid_api_key")])
        with self.assertRaises(_HttpError):
            self._call(llm)
        self.assertEqual(llm.completions.calls, 1)

    def test_transient_server_error_retries_once(self):
        llm = _Llm([_HttpError(500, "temporary server error"), "ok"])
        with llm_execution("retry-test") as usage:
            response = self._call(llm)
        self.assertEqual(response.choices[0].message.content, "ok")
        self.assertEqual(llm.completions.calls, 2)
        self.assertEqual(usage.logical_llm_calls, 1)
        self.assertEqual(usage.retry_count, 1)

    def test_per_trip_logical_call_limit_blocks_before_network(self):
        llm = _Llm(["first", "must-not-run"])
        with llm_execution("budget-test", max_calls=1) as usage:
            self._call(llm)
            with self.assertRaises(LLMCallBudgetExceeded):
                self._call(llm)
        self.assertEqual(llm.completions.calls, 1)
        self.assertEqual(usage.logical_llm_calls, 1)


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
