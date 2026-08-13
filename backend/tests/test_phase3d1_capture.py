import asyncio
import copy
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from backend.app.agents.trip_planner_agent import (
    BASELINE_PLANNER_PROMPT_VERSION, BASELINE_PLANNER_VERSION,
    PLANNER_PROMPT_VERSION, PLANNER_VERSION,
)
from backend.app.evaluation.capture import (
    CaptureGuardError, PlannerArtifactCapture, current_code_revision,
    to_offline_evaluation_input, validate_batch_selection,
)
from backend.app.evaluation.capture_models import (
    CaptureBudget, CaptureUsage, CapturedRouteCheck, CapturedValue,
    PatchCapture, PlannerCaptureArtifact, ProductionCaptureResult,
    ProviderStatusCapture, RevisionCapture,
)
from backend.app.evaluation.models import EvalCase
from backend.app.evaluation.snapshots import ProviderSnapshotStore
from backend.app.models.schemas import TripPlan


ROOT = Path(__file__).resolve().parents[2]
CASES = [EvalCase.model_validate(item) for item in json.loads(
    (ROOT / "eval/cases/golden_cases_v1.json").read_text())]


def known(value): return CapturedValue(status="known", value=value)
def missing(state, reason): return CapturedValue(status=state, reason=reason)


def plan_for(case, secret=False):
    cities = [stay.city for stay in case.trip_request.cities for _ in range(stay.days)]
    days = []
    for i, city in enumerate(cities):
        days.append({
            "date": str(date.fromisoformat(case.trip_request.start_date) + timedelta(days=i)),
            "day_index": i, "start_time": "10:00", "city": city,
            "description": "mock", "transportation": case.trip_request.transportation,
            "accommodation": case.trip_request.accommodation, "attractions": [], "meals": [],
        })
    return TripPlan.model_validate({
        "city": case.trip_request.city, "cities": [stay.city for stay in case.trip_request.cities],
        "start_date": case.trip_request.start_date, "end_date": case.trip_request.end_date,
        "days": days, "overall_suggestions": "sk-1234567890abcdef" if secret else "mock",
        "budget": {"total_attractions": 0, "total_hotels": 0, "total_meals": 0,
                   "total_transportation": 0, "total_inter_city_transport": 0, "total": 0},
        "validation_status": "passed", "risks": [],
    })


def usage(calls=2, tokens=150):
    return CaptureUsage(
        logical_llm_calls=known(calls), prompt_tokens=known(100),
        completion_tokens=known(tokens - 100), total_tokens=known(tokens),
        retries=known(0), stages=[],
    )


def result_for(case, *, secret=False, revision=None, patch=None):
    route = CapturedRouteCheck(
        day_index=0, origin_stable_id="poi-a", destination_stable_id="poi-b",
        provider="google_directions", request_attempted=True, data_available=True,
        distance_m=known(1200), duration_s=known(900), feasible=known(True), route_mode="walking",
        verification_status="verified",
    )
    provider_names = list(dict.fromkeys(req.provider for req in case.provider_fixtures))
    providers = [ProviderStatusCapture(provider=name, status="success",
        data_available=True, evidence_count=1, summary={"facts": 1}) for name in provider_names]
    plan = plan_for(case, secret=secret)
    return ProductionCaptureResult(
        final_trip_plan=plan, model="mock-model", final_validation_result={"status": "passed"},
        risks=[], provider_statuses_complete=True, provider_statuses=providers,
        xhs_evidence_metadata={"evidence_count": 0}, route_checks=[route], route_checks_complete=True,
        usage=usage(), total_latency_ms=321, stage_latency_ms={"planning": 200},
        revision=revision or RevisionCapture(status="not_applicable", reason="not triggered"),
        patch=patch or PatchCapture(status="not_applicable", reason="not a patch case"),
        provider_snapshots={name: {"facts": [{"id": f"mock-{name}"}]} for name in provider_names},
        pacing_policy_version="pacing.daily_load.v0.proposed",
        daily_load_assessments=[{"day_index": 0, "confidence": "HIGH", "overload_status": "within_target"}],
        pacing_risk_ids=[], validation_pass_scope="validation.initial",
    )


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.case = CASES[0]
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = PlannerArtifactCapture(ROOT, ProviderSnapshotStore(root / "snapshots"))
        self.output = root / "artifact.json"
        self.budget = CaptureBudget(max_cases=1, max_llm_calls=3, max_total_tokens=200,
                                    case_allowlist=[self.case.case_id])

    def tearDown(self): self.temp.cleanup()

    def run_capture(self, **kwargs):
        defaults = dict(case=self.case, run_id="capture_test", planner_version=PLANNER_VERSION,
                        prompt_version=PLANNER_PROMPT_VERSION, output_path=self.output,
                        budget=self.budget)
        defaults.update(kwargs)
        return asyncio.run(self.service.capture(**defaults))

    def test_dry_run_contract_identity_revision_and_unknown_semantics(self):
        artifact = self.run_capture(mode="dry-run")
        self.assertEqual((artifact.identity.planner_version, artifact.identity.prompt_version),
                         ("planner_pacing_v1", "planner_prompt_pacing_v1"))
        self.assertEqual(
            (BASELINE_PLANNER_VERSION, BASELINE_PLANNER_PROMPT_VERSION),
            ("planner_baseline_v1", "planner_prompt_v1"),
        )
        self.assertIn("96b9c5e", artifact.identity.code_revision)
        self.assertEqual(artifact.identity.model.status, "unknown")
        self.assertEqual(artifact.final_trip_plan.status, "unknown")
        self.assertEqual(artifact.total_latency_ms.status, "not_applicable")
        self.assertEqual(artifact.human_review.status, "pending")
        PlannerCaptureArtifact.model_validate_json(self.output.read_bytes())

    def test_mock_capture_provider_route_usage_latency_and_no_mutation(self):
        source = result_for(self.case); original = source.model_copy(deep=True)
        async def executor(case, context): return source
        artifact = self.run_capture(mode="record", allow_real_api=True, executor=executor)
        self.assertEqual(artifact.identity.model.value, "mock-model")
        self.assertEqual(artifact.provider_statuses[0].status, "success")
        self.assertEqual(artifact.route_checks[0].distance_m.value, 1200)
        self.assertEqual(artifact.usage.total_tokens.value, 150)
        self.assertEqual(artifact.total_latency_ms.value, 321)
        self.assertEqual(artifact.execution_started_at.status, "known")
        self.assertEqual(artifact.execution_completed_at.status, "known")
        self.assertEqual(artifact.pacing_policy_version, "pacing.daily_load.v0.proposed")
        self.assertEqual(artifact.daily_load_assessments[0]["confidence"], "HIGH")
        self.assertEqual(artifact.validation_pass_scope, "validation.initial")
        offline = to_offline_evaluation_input(artifact)
        self.assertEqual(offline.route_checks[0].feasible, True)
        self.assertEqual(offline.usage.total_tokens, 150)
        self.assertEqual(source, original)

    def test_revision_and_patch_before_after_are_preserved(self):
        plan = plan_for(self.case)
        revision = RevisionCapture(status="known", before=plan, target_risk_ids=["risk-1"],
                                   after=plan, revalidation_result={"status": "passed"})
        patch = PatchCapture(status="known", before=plan, after=plan,
                             affected_day_indices=[0], protected_day_indices=[1])
        async def executor(case, context): return result_for(case, revision=revision, patch=patch)
        artifact = self.run_capture(mode="record", allow_real_api=True, executor=executor)
        self.assertEqual(artifact.revision.target_risk_ids, ["risk-1"])
        self.assertEqual(artifact.patch.affected_day_indices, [0])

    def test_targeted_pacing_revision_capture_is_backward_compatible(self):
        plan = plan_for(self.case)
        revision = RevisionCapture(
            status="known", revision_kind="targeted_pacing", revision_status="success",
            before=plan, candidate=plan, after=plan,
            target_risk_ids=["pacing_daily_load:day:0"], affected_day_indices=[0],
            protected_day_indices=[1], protected_day_equality={1: True},
            revalidation_result={"status": "issues_found"},
            post_pacing_risk_ids=[], resolution_outcome="resolved",
            pacing_policy_version="pacing.daily_load.v0.proposed",
            pacing_revision_metrics={"pacing_revision_resolution_rate": 1.0},
        )
        async def executor(case, context): return result_for(case, revision=revision)
        artifact = self.run_capture(mode="record", allow_real_api=True, executor=executor)
        self.assertEqual(artifact.revision.revision_kind, "targeted_pacing")
        self.assertTrue(artifact.revision.protected_day_equality[1])
        self.assertEqual(artifact.revision.pacing_revision_metrics[
            "pacing_revision_resolution_rate"], 1.0)
        PlannerCaptureArtifact.model_validate_json(self.output.read_bytes())

    def test_secret_sanitization_rejects_artifact_and_snapshot(self):
        async def artifact_secret(case, context): return result_for(case, secret=True)
        with self.assertRaises(ValueError):
            self.run_capture(mode="record", allow_real_api=True, executor=artifact_secret)
        self.assertFalse(self.output.exists())
        self.assertFalse(any(self.service.snapshot_store.root.rglob("*.json")))
        async def snapshot_secret(case, context):
            result = result_for(case); result.provider_snapshots["google_places"] = {"Authorization": "Bearer abc"}
            return result
        with self.assertRaises(ValueError):
            self.run_capture(mode="record", allow_real_api=True, executor=snapshot_secret)

    def test_record_replay_hash_stability_and_replay_context(self):
        async def record_executor(case, context): return result_for(case)
        recorded = self.run_capture(mode="record", allow_real_api=True, executor=record_executor)
        seen = {}
        async def replay_executor(case, context):
            seen.update(context["snapshots"]); return result_for(case)
        replayed = self.run_capture(mode="replay", executor=replay_executor)
        self.assertEqual(recorded.snapshot_hashes["google_places"], replayed.snapshot_hashes["google_places"])
        self.assertIn("google_places", seen)

    def test_real_api_guard_and_executor_requirement(self):
        async def executor(case, context): return result_for(case)
        with self.assertRaisesRegex(CaptureGuardError, "real_api_not_allowed"):
            self.run_capture(mode="record", executor=executor)

    def test_case_allowlist_and_max_cases(self):
        with self.assertRaisesRegex(CaptureGuardError, "case_not_in_allowlist"):
            self.run_capture(mode="dry-run", budget=CaptureBudget(case_allowlist=[]))
        budget = CaptureBudget(max_cases=1, case_allowlist=[CASES[0].case_id, CASES[1].case_id])
        with self.assertRaisesRegex(CaptureGuardError, "max_cases_exceeded"):
            validate_batch_selection(CASES, budget, allow_multiple=True)

    def test_max_llm_calls_and_token_guards(self):
        async def too_many_calls(case, context):
            result = result_for(case); result.usage = usage(calls=4); return result
        with self.assertRaisesRegex(CaptureGuardError, "max_llm_calls_exceeded"):
            self.run_capture(mode="record", allow_real_api=True, executor=too_many_calls)
        async def too_many_tokens(case, context):
            result = result_for(case); result.usage = usage(tokens=201); return result
        with self.assertRaisesRegex(CaptureGuardError, "max_total_tokens_exceeded"):
            self.run_capture(mode="record", allow_real_api=True, executor=too_many_tokens)

    def test_capture_states_reject_fake_defaults(self):
        with self.assertRaises(Exception): CapturedValue(status="unknown", value=0, reason="missing")
        with self.assertRaises(Exception): CapturedValue(status="not_applicable", value=False, reason="n/a")


if __name__ == "__main__": unittest.main()
