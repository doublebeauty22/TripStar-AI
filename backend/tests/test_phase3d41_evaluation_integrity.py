import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner
from backend.app.evaluation.badcases import label_badcases
from backend.app.evaluation.capture import PlannerArtifactCapture, to_offline_evaluation_input
from backend.app.evaluation.capture_cli import (
    CaptureBootstrapError, _run as capture_cli_run, bootstrap_capture_configuration,
)
from backend.app.evaluation.capture_models import (
    CaptureBudget, CaptureFailureManifest, CapturedRouteCheck, PlannerCaptureArtifact,
    RevisionCapture,
)
from backend.app.evaluation.metrics import calculate_metrics, classify_plan_route_legs
from backend.app.evaluation.models import ArtifactEvaluationInput, EvalCase, RouteCheck
from backend.app.evaluation.production_capture import (
    ProductionExecutionError, build_revision_capture, execute_production_patch,
)
from backend.app.evaluation.snapshots import ProviderSnapshotStore
from backend.app.models.schemas import RiskItem, TripPatch, TripPlan, ValidationResult
from backend.app.services.planner_observation import capture_planner_observations
from backend.tests.test_phase3b_eval_runner import plan as eval_plan
from backend.tests.test_phase3d1_capture import CASES, known, plan_for, result_for


ROOT = Path(__file__).resolve().parents[2]
GOLDEN = [EvalCase.model_validate(x) for x in json.loads(
    (ROOT / "eval/cases/golden_cases_v1.json").read_text())]


def route(phase, *, checked=True):
    return CapturedRouteCheck(
        day_index=0, origin_stable_id="a", destination_stable_id="b",
        provider="google_directions", request_attempted=checked, data_available=checked,
        distance_m=known(100) if checked else __import__("backend.app.evaluation.capture_models", fromlist=["CapturedValue"]).CapturedValue(status="unknown", reason="unavailable"),
        duration_s=known(60) if checked else __import__("backend.app.evaluation.capture_models", fromlist=["CapturedValue"]).CapturedValue(status="unknown", reason="unavailable"),
        feasible=known(True) if checked else __import__("backend.app.evaluation.capture_models", fromlist=["CapturedValue"]).CapturedValue(status="unknown", reason="unavailable"),
        route_mode="transit", verification_status="verified" if checked else "unavailable",
        reason=None if checked else "unavailable", validation_pass_id=f"validation.{phase}",
        validation_phase=phase,
    )


class RouteScopeAndRevisionTests(unittest.TestCase):
    def test_no_revision_initial_pass_is_final(self):
        result = result_for(CASES[0]); result.route_checks = [route("initial")]
        with tempfile.TemporaryDirectory() as temp:
            service = PlannerArtifactCapture(ROOT, ProviderSnapshotStore(Path(temp) / "snapshots"))
            async def executor(case, context): return result
            artifact = asyncio.run(service.capture(
                CASES[0], run_id="capture_scope", mode="record", planner_version="p",
                prompt_version="q", output_path=Path(temp) / "a.json",
                budget=CaptureBudget(max_llm_calls=4, case_allowlist=[CASES[0].case_id]),
                allow_real_api=True, executor=executor))
        offline = to_offline_evaluation_input(artifact)
        self.assertEqual([x.validation_phase for x in offline.route_checks], ["initial"])

    def test_revision_uses_post_revision_only(self):
        before = plan_for(CASES[0]); after = before.model_copy(deep=True); after.revision_count = 1
        revision = RevisionCapture(status="known", before=before, after=after,
            target_risk_ids=["risk"], revalidation_result={"status": "passed"})
        result = result_for(CASES[0], revision=revision); result.final_trip_plan = after
        result.route_checks = [route("initial"), route("post_revision")]
        with tempfile.TemporaryDirectory() as temp:
            service = PlannerArtifactCapture(ROOT, ProviderSnapshotStore(Path(temp) / "snapshots"))
            async def executor(case, context): return result
            artifact = asyncio.run(service.capture(
                CASES[0], run_id="capture_revision_scope", mode="record", planner_version="p",
                prompt_version="q", output_path=Path(temp) / "a.json",
                budget=CaptureBudget(max_llm_calls=4, case_allowlist=[CASES[0].case_id]),
                allow_real_api=True, executor=executor))
        offline = to_offline_evaluation_input(artifact)
        self.assertEqual(len(offline.route_checks), 1)
        self.assertEqual(offline.route_checks[0].validation_phase, "post_revision")

    def test_metric_integrity_rejects_numerator_over_denominator(self):
        case = CASES[0]; value = eval_plan(case)
        value["days"][0]["attractions"].append({**value["days"][0]["attractions"][0], "name": "B", "place_id": "b"})
        checks = [RouteCheck(day_index=0, origin="a", destination="b", status="checked", feasible=True, data_source="google_directions") for _ in range(2)]
        metrics = {m.metric: m for m in calculate_metrics(case, ArtifactEvaluationInput(output=value, route_checks=checks))}
        self.assertEqual(metrics["route_check_coverage"].status, "failed")
        self.assertEqual(metrics["route_check_coverage"].reason, "metric_integrity_error")

    def test_revision_capture_and_resolved_unresolved_semantics(self):
        case = CASES[0]; before = plan_for(case); before.revision_count = 0
        risk = RiskItem(id="risk-1", type="budget", severity="blocking", title="x", message="x", revisable=True)
        before.risks = [risk]; after = before.model_copy(deep=True); after.revision_count = 1; after.risks = []
        events = [
            {"event":"initial_validation","plan":before,"validation_result":{"status":"issues_found"},"risks":[risk]},
            {"event":"critic","target_risk_ids":["risk-1"],"protected_elements":["dates"],"revision_instructions":["reduce"]},
            {"event":"post_revision_enrichment","state":"complete","plan":after},
            {"event":"post_revision_validation","plan":after,"validation_result":{"status":"passed"},"risks":[]},
        ]
        captured = build_revision_capture(after, events)
        self.assertEqual(captured.before.risks[0].id, "risk-1")
        self.assertEqual(captured.target_risk_ids, ["risk-1"])
        self.assertEqual(captured.after.revision_count, 1)
        artifact = ArtifactEvaluationInput(output=after.model_dump(mode="json"), revision_before=before,
            revision_after=after, revision_target_risk_ids=["risk-1"], revision_revalidation_result={"status":"passed"})
        metric = next(x for x in calculate_metrics(case, artifact) if x.metric == "revision_risk_resolution_rate")
        self.assertEqual((metric.status, metric.value), ("known", 1.0))
        after.risks = [risk]; artifact.output = after.model_dump(mode="json"); artifact.revision_after = after
        metric = next(x for x in calculate_metrics(case, artifact) if x.metric == "revision_risk_resolution_rate")
        self.assertEqual(metric.value, 0.0)
        artifact.revision_revalidation_result = None
        metric = next(x for x in calculate_metrics(case, artifact) if x.metric == "revision_risk_resolution_rate")
        self.assertEqual(metric.status, "unknown")


class PatchFailureAndProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_patch_production_seam_and_protected_day_metrics(self):
        case = next(x for x in GOLDEN if x.case_id == "gc_nanjing_local_patch")
        before = plan_for(case); before.plan_version = 1
        typed = TripPatch.model_validate({"intent":"later start","operations":[{
            "operation":"update_start_time","day_index":2,"old_value":"10:00","new_value":"11:00"}],
            "affected_day_indices":[2],"protected_day_indices":[0,1,3]})
        async def identity(plan, patch_value): return plan
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=None):
            result = await execute_production_patch(case, {"base_plan":before,"typed_patch":typed,"patch_enricher":identity})
        self.assertEqual(result.patch.affected_day_indices, [2])
        self.assertEqual(result.patch.plan_version_after, 2)
        artifact = ArtifactEvaluationInput(output=result.final_trip_plan.model_dump(mode="json"),
            patch_before=result.patch.before, patch_after=result.patch.after)
        metrics = calculate_metrics(case, artifact)
        preserved = next(x for x in metrics if x.metric == "unaffected_day_preservation_rate")
        self.assertEqual(preserved.value, 1.0)
        drift = result.patch.after.model_copy(deep=True); drift.days[0].start_time = "12:00"
        artifact.patch_after = drift
        metrics = calculate_metrics(case, artifact)
        findings = {x.label:x for x in label_badcases(case, artifact, metrics)}
        self.assertTrue(findings["patch_scope_drift"].detected)

    async def test_generic_parse_failure_manifest_and_evaluator_rejection(self):
        case = CASES[0]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "artifact.json"
            service = PlannerArtifactCapture(ROOT, ProviderSnapshotStore(Path(temp) / "snapshots"))
            async def executor(case, context):
                raise ProductionExecutionError("planner_output_parse_failure", "planner", {
                    "logical_llm_calls":1,"prompt_tokens":10,"completion_tokens":5,"total_tokens":15,
                    "retry_count":0,"model":"mock-model"}, 25)
            with self.assertRaises(ProductionExecutionError):
                await service.capture(case, run_id="capture_failure", mode="record", planner_version="p",
                    prompt_version="q", output_path=output,
                    budget=CaptureBudget(max_llm_calls=4, case_allowlist=[case.case_id]),
                    allow_real_api=True, executor=executor)
            failure = CaptureFailureManifest.model_validate_json(output.with_name("artifact.failure.json").read_bytes())
            self.assertEqual((failure.run_status, failure.failure_type, failure.failed_stage),
                             ("failed", "planner_output_parse_failure", "planner"))
            with self.assertRaises(ValidationError): ArtifactEvaluationInput.model_validate(failure.model_dump())

    async def test_failure_secret_rejection_and_hotel_observation(self):
        with self.assertRaises(ValidationError):
            CaptureFailureManifest(failure_type="planner_execution_error", case_id="gc_test",
                planner_version="p",prompt_version="q",code_revision="sk-1234567890abcdef",
                model={"status":"unknown","reason":"missing"},calls_completed=0,prompt_tokens=0,
                completion_tokens=0,total_tokens=0,retries=0,configured_max_llm_calls=4,
                execution_started_at="a",execution_completed_at="b",failure_reason="planner_execution_error")
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner); planner.map_provider="google"
        planner._google_service=type("Google",(),{"search_poi":lambda self,*args:[{"id":"h"}]})()
        with capture_planner_observations() as observations:
            await planner._retrieve_hotel_context("南京","舒适型")
        self.assertEqual(observations["hotels"][0]["candidate_count"],1)
        self.assertEqual(observations["hotels"][0]["provider"],"hotel_google_places")


class ControlledBaseCLITests(unittest.IsolatedAsyncioTestCase):
    def args(self, root, **overrides):
        values = dict(
            cases=ROOT/"eval/cases/golden_cases_v1.json", case=["gc_nanjing_local_patch"],
            mode="record", output_directory=root/"runs", snapshot_directory=root/"snapshots",
            repo_root=root, run_id="controlled_base_test", max_cases=1, max_llm_calls=4,
            max_total_tokens=60000, stop_on_error=True, allow_multiple_cases=False,
            allow_real_api=True, base_artifact=None, capture_target="controlled-base",
        ); values.update(overrides); return SimpleNamespace(**values)

    def test_bootstrap_explicit_backend_env_and_missing_config_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {}, clear=True):
            root=Path(temp); (root/"backend").mkdir(); (root/"backend/.env").write_text("OPENAI_API_KEY=synthetic-test-key\n")
            self.assertEqual(bootstrap_capture_configuration(root), (root/"backend/.env").resolve())
            self.assertTrue(__import__("os").environ.get("OPENAI_API_KEY"))
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(CaptureBootstrapError): bootstrap_capture_configuration(Path(temp))

    async def test_controlled_base_cli_success_atomic_and_failure_manifest(self):
        case=next(x for x in GOLDEN if x.case_id=="gc_nanjing_local_patch")
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"OPENAI_API_KEY":"synthetic-test-key"}, clear=False):
            root=Path(temp)
            async def success(selected, context): return result_for(selected)
            with patch("backend.app.evaluation.production_capture.execute_production_planner", success):
                self.assertEqual(await capture_cli_run(self.args(root)), 0)
            artifact=root/"runs"/f"{case.case_id}.json"
            self.assertTrue(artifact.is_file()); self.assertTrue(artifact.with_name(artifact.stem+".manifest.json").is_file())
            PlannerCaptureArtifact.model_validate_json(artifact.read_bytes())

        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"OPENAI_API_KEY":"synthetic-test-key"}, clear=False):
            root=Path(temp)
            async def failure(selected, context):
                raise ProductionExecutionError("planner_execution_error","planner",{
                    "logical_llm_calls":0,"prompt_tokens":0,"completion_tokens":0,"total_tokens":0,
                    "retry_count":0,"model":None},1)
            with patch("backend.app.evaluation.production_capture.execute_production_planner", failure):
                with self.assertRaises(ProductionExecutionError): await capture_cli_run(self.args(root))
            self.assertFalse((root/"runs"/f"{case.case_id}.json").exists())
            self.assertTrue((root/"runs"/f"{case.case_id}.failure.json").is_file())

    async def test_patch_target_requires_explicit_base_and_never_regenerates(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"OPENAI_API_KEY":"synthetic-test-key"}, clear=False):
            with self.assertRaises(SystemExit):
                await capture_cli_run(self.args(Path(temp),capture_target="patch",base_artifact=None))


class MultiCityAndCompatibilityTests(unittest.TestCase):
    def test_multi_city_transfer_classification_excluded(self):
        case = next(x for x in GOLDEN if x.case_id == "gc_beijing_xian_multi_city")
        plan = TripPlan.model_validate(eval_plan(case))
        legs = classify_plan_route_legs(plan)
        self.assertEqual(sum(x["leg_type"] == "inter_city_transfer" for x in legs), 1)
        self.assertFalse(any(x["leg_type"] == "intra_city_poi_leg" for x in legs))

    def test_old_revision_artifacts_do_not_reuse_unscoped_routes(self):
        path = ROOT / "eval/pilots/phase3d4/gc_chengdu_budget/gc_chengdu_budget.json"
        artifact = PlannerCaptureArtifact.model_validate_json(path.read_bytes())
        self.assertEqual(artifact.identity.capture_version, "capture.v1")
        offline = to_offline_evaluation_input(artifact)
        self.assertIsNone(offline.route_checks)
        case = next(x for x in GOLDEN if x.case_id == artifact.identity.case_id)
        metric = next(x for x in calculate_metrics(case, offline) if x.metric == "route_check_coverage")
        self.assertEqual(metric.status, "unknown")

    def test_existing_controlled_artifact_structural_regression(self):
        paths = {
            "gc_beijing_baseline": ROOT / "eval/pilots/phase3d2_pilot3/gc_beijing_baseline/gc_beijing_baseline.json",
            "gc_kyoto_no_early_start": ROOT / "eval/pilots/phase3d3/gc_kyoto_no_early_start/gc_kyoto_no_early_start.json",
            "gc_osaka_places_partial": ROOT / "eval/pilots/phase3d3/gc_osaka_places_partial/gc_osaka_places_partial.json",
            "gc_shenzhen_overbudget_revision": ROOT / "eval/pilots/phase3d4/gc_shenzhen_overbudget_revision/gc_shenzhen_overbudget_revision.json",
        }
        for case_id, path in paths.items():
            artifact = PlannerCaptureArtifact.model_validate_json(path.read_bytes())
            offline = to_offline_evaluation_input(artifact)
            case = next(x for x in GOLDEN if x.case_id == case_id)
            metrics = {x.metric:x for x in calculate_metrics(case, offline)}
            self.assertEqual(metrics["schema_valid"].value, 1.0)
            self.assertEqual(metrics["budget_arithmetic_consistency"].value, 1.0)
            self.assertNotEqual(metrics["route_check_coverage"].status, "failed")
        self.assertEqual(
            {x.metric:x for x in calculate_metrics(
                next(x for x in GOLDEN if x.case_id == "gc_shenzhen_overbudget_revision"),
                to_offline_evaluation_input(PlannerCaptureArtifact.model_validate_json(
                    paths["gc_shenzhen_overbudget_revision"].read_bytes()))
            )}["date_day_consistency"].value, 0.0)


if __name__ == "__main__":
    unittest.main()
