import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from backend.app.evaluation.capture import PlannerArtifactCapture
from backend.app.evaluation.capture_models import CaptureBudget, CaptureFailureManifest
from backend.app.evaluation.models import EvalRunArtifact
from backend.app.evaluation.snapshots import ProviderSnapshotStore
from backend.app.services.llm_service import (
    LLMCallBudgetExceeded, create_chat_completion, llm_execution,
)
from backend.tests.test_phase3d1_capture import CASES, result_for, usage


class _Usage:
    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt; self.completion_tokens = completion
        self.total_tokens = prompt + completion


class _Response:
    def __init__(self, prompt=10, completion=5):
        self.usage = _Usage(prompt, completion)
        self.choices = [type("Choice", (), {"message": type("Message", (), {"content": "{}"})()})()]


class _Completions:
    def __init__(self, responses): self.responses = list(responses); self.calls = 0
    def create(self, **kwargs): self.calls += 1; return self.responses.pop(0)


class _LLM:
    def __init__(self, responses):
        self._client = type("Client", (), {})()
        self._client.chat = type("Chat", (), {})()
        self._client.chat.completions = _Completions(responses)


def invoke(llm, stage="planner", **kwargs):
    return create_chat_completion(stage=stage, model="mock-model", messages=[],
                                  llm_instance=llm, **kwargs)


class StageBudgetTests(unittest.TestCase):
    def test_call_limit_blocks_before_next_request(self):
        llm = _LLM([_Response(), _Response()])
        with llm_execution("eval", max_calls=1):
            invoke(llm, stage="xhs_research")
            with self.assertRaises(LLMCallBudgetExceeded) as raised:
                invoke(llm, stage="planner")
        self.assertEqual(llm._client.chat.completions.calls, 1)
        self.assertEqual(raised.exception.failed_before_stage, "planner")

    def test_known_stage_exposure_preblocks_unknown_does_not_predict(self):
        llm = _LLM([_Response()])
        with llm_execution("eval", max_calls=2, max_total_tokens=100):
            with self.assertRaises(LLMCallBudgetExceeded) as raised:
                invoke(llm, stage="revision", stage_max_token_exposure=101)
        self.assertEqual(llm._client.chat.completions.calls, 0)
        event = raised.exception.snapshot["admission_events"][-1]
        self.assertEqual(event["admission_certainty"], "known")

        llm = _LLM([_Response(20, 10)])
        with llm_execution("eval", max_calls=1, max_total_tokens=100) as observed:
            invoke(llm, stage="planner")
            snapshot = observed.snapshot()
        self.assertEqual(snapshot["admission_events"][0]["admission_certainty"], "unknown")
        self.assertEqual(llm._client.chat.completions.calls, 1)

    def test_post_call_ceiling_is_immediate_and_later_stages_blocked(self):
        llm = _LLM([_Response(80, 30), _Response()])
        with llm_execution("eval", max_calls=4, max_total_tokens=100) as observed:
            with self.assertRaises(LLMCallBudgetExceeded) as raised:
                invoke(llm, stage="critic")
            self.assertEqual(raised.exception.failed_after_stage, "critic")
            self.assertTrue(observed.snapshot()["budget_exceeded"])
            with self.assertRaises(LLMCallBudgetExceeded):
                invoke(llm, stage="revision")
        self.assertEqual(llm._client.chat.completions.calls, 1)

    def test_normal_production_without_token_ceiling_is_unaffected(self):
        llm = _LLM([_Response(1000, 1000)])
        with llm_execution("production", max_calls=1) as observed:
            invoke(llm)
        self.assertFalse(observed.snapshot()["budget_exceeded"])


class FailureManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.output = root / "runs" / "artifact.json"
        self.service = PlannerArtifactCapture(Path("."), ProviderSnapshotStore(root / "snapshots"))
        self.case = CASES[0]
        self.budget = CaptureBudget(max_cases=1, max_llm_calls=4, max_total_tokens=100,
                                    case_allowlist=[self.case.case_id])

    def tearDown(self): self.temp.cleanup()

    def test_budget_exception_writes_only_safe_failure_manifest(self):
        async def executor(case, context):
            llm = _LLM([_Response(80, 30)])
            with llm_execution("eval", max_calls=4, max_total_tokens=100):
                invoke(llm, stage="planner")
        with self.assertRaises(LLMCallBudgetExceeded):
            asyncio.run(self.service.capture(
                self.case, run_id="capture_budget", mode="record", planner_version="v1",
                prompt_version="p1", output_path=self.output, budget=self.budget,
                allow_real_api=True, executor=executor))
        failure_path = self.output.with_name("artifact.failure.json")
        failure = CaptureFailureManifest.model_validate_json(failure_path.read_bytes())
        self.assertEqual(failure.capture_status, "failed")
        self.assertEqual(failure.run_status, "budget_exceeded")
        self.assertEqual(failure.total_tokens, 110)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_name("artifact.manifest.json").exists())
        with self.assertRaises(Exception): EvalRunArtifact.model_validate_json(failure_path.read_bytes())

    def test_failure_manifest_rejects_secret(self):
        value = {
            "capture_status": "failed", "failure_type": "budget_exceeded",
            "case_id": self.case.case_id, "planner_version": "v1", "prompt_version": "p1",
            "code_revision": "dirty", "model": {"status": "known", "value": "sk-1234567890abcdef"},
            "calls_completed": 1, "prompt_tokens": 1, "completion_tokens": 1,
            "total_tokens": 2, "retries": 0, "failed_after_stage": "planner",
            "configured_max_llm_calls": 4, "configured_max_total_tokens": 1,
            "execution_started_at": "now", "execution_completed_at": "later",
            "failure_reason": "post_call_token_ceiling_exceeded", "sanitized": True,
        }
        with self.assertRaises(Exception): CaptureFailureManifest.model_validate(value)


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
