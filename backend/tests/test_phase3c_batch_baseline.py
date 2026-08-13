import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from backend.app.evaluation.baseline_reports import batch_markdown, write_batch_json
from backend.app.evaluation.batch import BaselineError, aggregate_metric, build_batch_report
from backend.app.evaluation.fixtures import FrozenFixtureResolver
from backend.app.evaluation.models import (
    ArtifactEvaluationInput, BatchEvaluationReport, EvalCase, HumanReviewRecord,
    MetricResult, PlannerVersionMetadata, UsageMetadata,
)
from backend.app.evaluation.runner import evaluate_artifact


ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = ROOT / "eval"


def synthetic_plan(case: EvalCase, grounded=True):
    days = []
    city_schedule = [stay.city for stay in case.trip_request.cities for _ in range(stay.days)]
    for index, city in enumerate(city_schedule):
        days.append({
            "date": str(date.fromisoformat(case.trip_request.start_date) + timedelta(days=index)),
            "day_index": index, "start_time": case.expected_constraints.earliest_start_time or "10:00",
            "city": city, "description": "Synthetic test artifact", "transportation": case.trip_request.transportation,
            "accommodation": case.trip_request.accommodation, "attractions": [{
                "name": f"Synthetic POI {index}", "address": "Synthetic address",
                "location": {"longitude": 100 + index, "latitude": 20 + index},
                "visit_duration": 60, "description": "Synthetic test POI",
                "place_id": f"place-{index}" if grounded else "",
                "poi_match_status": "verified" if grounded else "unverified",
                "map_data_source": "google_places" if grounded else "llm_unverified",
            }], "meals": []})
    cap = case.expected_constraints.budget_limit_cny
    total = min(1000, cap) if cap else 1000
    return {"city": case.trip_request.city, "cities": [x.city for x in case.trip_request.cities],
            "start_date": case.trip_request.start_date, "end_date": case.trip_request.end_date,
            "days": days, "overall_suggestions": "Synthetic test only",
            "budget": {"total_attractions": total, "total_hotels": 0, "total_meals": 0,
                       "total_transportation": 0, "total_inter_city_transport": 0, "total": total},
            "validation_status": "passed", "risks": []}


class Phase3CBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = [EvalCase.model_validate(item) for item in json.loads((EVAL_ROOT / "cases/golden_cases_v1.json").read_text())]
        resolver = FrozenFixtureResolver(EVAL_ROOT)
        cls.runs = []
        for index, case in enumerate(cls.cases):
            _, hashes = resolver.resolve(case)
            artifact = ArtifactEvaluationInput(output=synthetic_plan(case), usage=UsageMetadata(
                logical_llm_calls=2, prompt_tokens=100 + index, completion_tokens=50,
                total_tokens=150 + index), latency_ms=500 + index)
            run = evaluate_artifact(case, artifact, PlannerVersionMetadata(
                planner_version="planner.baseline.v1", prompt_version="prompt.baseline.v1",
                model="saved-real-model", eval_run_id=f"eval_batch_{index}", case_id=case.case_id,
                fixture_set_version="fixtures.v1"), hashes, f"artifact-{index}.json")
            run.artifact_origin = "real_planner"
            cls.runs.append(run)

    def test_denominator_correctness_and_unknown_na_coverage(self):
        metrics = [
            MetricResult(metric="grounded_poi_rate", status="known", value=.5, numerator=1, denominator=2, policy_version="metrics.v1"),
            MetricResult(metric="grounded_poi_rate", status="known", value=1, numerator=3, denominator=3, policy_version="metrics.v1"),
            MetricResult(metric="grounded_poi_rate", status="unknown", reason="missing", policy_version="metrics.v1"),
            MetricResult(metric="grounded_poi_rate", status="not_applicable", reason="none", policy_version="metrics.v1"),
        ]
        result = aggregate_metric("grounded_poi_rate", metrics)
        self.assertEqual((result.numerator, result.denominator, result.aggregate_value), (4, 5, .8))
        self.assertEqual((result.known_cases, result.unknown_cases, result.not_applicable_cases), (2, 1, 1))

    def test_batch_aggregation_manifest_and_breakdowns(self):
        report = build_batch_report(self.cases, self.runs, baseline_id="baseline.v1", code_revision="worktree:phase3c")
        self.assertEqual(report.manifest.baseline_status, "established")
        self.assertEqual((report.cases_total, report.cases_evaluated, report.cases_failed), (16, 16, 0))
        tags = {item.group for item in report.scenario_breakdown}
        self.assertIn("relaxed", tags); self.assertIn("revision_trigger", tags)
        self.assertIn("provider_normal", tags); self.assertIn("provider_degraded", tags)
        self.assertIn("budget_unconstrained", tags); self.assertIn("no_revision_trigger", tags)
        self.assertEqual({item.group for item in report.language_breakdown}, {"en", "zh"})
        self.assertEqual({item.group for item in report.city_scope_breakdown}, {"single_city", "multi_city"})

    def test_human_review_is_pending_without_simulated_scores(self):
        report = build_batch_report(self.cases, self.runs, baseline_id="baseline.v1", code_revision="worktree:phase3c")
        self.assertTrue(all(item.status == "pending" and not item.scores for item in report.human_reviews))
        with self.assertRaises(Exception): HumanReviewRecord(case_id=self.cases[0].case_id, planner_version="v1", status="pending", reviewer="AI")

    def test_non_real_or_incomplete_batch_does_not_establish_baseline(self):
        runs = [item.model_copy(deep=True) for item in self.runs[:-1]]
        runs[0].artifact_origin = "synthetic"
        report = build_batch_report(self.cases, runs, baseline_id="baseline.invalid", code_revision="worktree")
        self.assertEqual(report.manifest.baseline_status, "not_established")

    def test_empty_baseline_preserves_declared_identity_and_pending_reviews(self):
        report = build_batch_report(
            self.cases, [], baseline_id="baseline.empty", code_revision="worktree",
            planner_version="planner_baseline_v1", prompt_version="unversioned",
            model="runtime-not-captured",
        )
        self.assertEqual(report.manifest.planner_version, "planner_baseline_v1")
        self.assertEqual(len(report.human_reviews), 16)
        self.assertTrue(all(review.status == "pending" for review in report.human_reviews))

    def test_candidate_identity_mismatch_is_rejected(self):
        runs = [item.model_copy(deep=True) for item in self.runs]
        runs[-1].metadata.prompt_version = "prompt.candidate.v2"
        with self.assertRaises(BaselineError):
            build_batch_report(self.cases, runs, baseline_id="baseline.mixed", code_revision="worktree")

    def test_badcase_frequency_and_deterministic_rerun(self):
        first = build_batch_report(self.cases, self.runs, baseline_id="baseline.v1", code_revision="worktree:phase3c")
        second = build_batch_report(self.cases, self.runs, baseline_id="baseline.v1", code_revision="worktree:phase3c")
        left = first.model_dump(mode="json"); right = second.model_dump(mode="json")
        left["manifest"]["generated_at"] = right["manifest"]["generated_at"]
        self.assertEqual(left, right)
        self.assertIsInstance(first.automatic_badcase_frequency, dict)

    def test_batch_json_and_markdown_report(self):
        report = build_batch_report(self.cases, self.runs, baseline_id="baseline.v1", code_revision="worktree:phase3c")
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "baseline.json"; write_batch_json(target, report)
            BatchEvaluationReport.model_validate_json(target.read_bytes())
        markdown = batch_markdown(report)
        self.assertIn("Numerator/denominator", markdown)
        self.assertIn("Human review status: **PENDING**", markdown)
        self.assertIn("baseline measurement, not an optimization result", markdown)


if __name__ == "__main__": unittest.main()
