import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from backend.app.evaluation.badcases import label_badcases
from backend.app.evaluation.comparison import ComparisonError, compare_runs
from backend.app.evaluation.fixtures import FixtureError, FrozenFixtureResolver, content_hash
from backend.app.evaluation.metrics import calculate_metrics
from backend.app.evaluation.models import (
    ArtifactEvaluationInput, EvalCase, EvalRunArtifact, PlannerVersionMetadata,
    SanitizedProviderFixture, UsageMetadata,
)
from backend.app.evaluation.network import NetworkAccessBlocked, deny_network
from backend.app.evaluation.reports import comparison_markdown, write_json_report
from backend.app.evaluation.runner import evaluate_artifact


ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = ROOT / "eval"


def plan(case, *, start="09:30", grounded=True, total=1000, component_total=1000, validation="passed"):
    days = []
    for index in range(case.trip_request.travel_days):
        city = next(stay.city for stay in case.trip_request.cities if index < sum(x.days for x in case.trip_request.cities[:case.trip_request.cities.index(stay)+1]))
        poi = {
            "name": f"Synthetic POI {index}", "address": f"Synthetic address {index}",
            "location": {"longitude": 116.0 + index, "latitude": 39.0 + index},
            "visit_duration": 60, "description": "Synthetic fixture-backed place",
            "place_id": f"synthetic-{index}" if grounded else "",
            "poi_match_status": "verified" if grounded else "unverified",
            "map_data_source": "google_places" if grounded else "llm_unverified",
        }
        days.append({"date": str(__import__("datetime").date.fromisoformat(case.trip_request.start_date) + __import__("datetime").timedelta(days=index)),
                     "day_index": index, "start_time": start, "city": city, "description": "Synthetic day",
                     "transportation": case.trip_request.transportation, "accommodation": case.trip_request.accommodation,
                     "attractions": [poi], "meals": []})
    return {"city": case.trip_request.city, "cities": [x.city for x in case.trip_request.cities],
            "start_date": case.trip_request.start_date, "end_date": case.trip_request.end_date,
            "days": days, "overall_suggestions": "Synthetic offline artifact",
            "budget": {"total_attractions": component_total, "total_hotels": 0, "total_meals": 0,
                       "total_transportation": 0, "total_inter_city_transport": 0, "total": total},
            "validation_status": validation, "risks": []}


class Phase3BEvalRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = [EvalCase.model_validate(item) for item in json.loads((EVAL_ROOT / "cases/golden_cases_v1.json").read_text())]
        cls.case = next(item for item in cls.cases if item.case_id == "gc_beijing_baseline")
        cls.resolver = FrozenFixtureResolver(EVAL_ROOT)
        _, cls.hashes = cls.resolver.resolve(cls.case)

    def artifact(self, case=None, **kwargs):
        case = case or self.case
        return ArtifactEvaluationInput(output=plan(case), usage=UsageMetadata(logical_llm_calls=2, prompt_tokens=100, completion_tokens=50, total_tokens=150), latency_ms=500, **kwargs)

    def make_run(self, artifact=None, run_id="eval_test_base", case=None, hashes=None):
        case = case or self.case; artifact = artifact or self.artifact(case)
        return evaluate_artifact(case, artifact, PlannerVersionMetadata(planner_version="planner.v1", prompt_version="prompt.v1", model="offline-artifact", eval_run_id=run_id, case_id=case.case_id, fixture_set_version="fixtures.v1"), hashes or self.hashes, "synthetic.json", "2026-08-12T00:00:00+00:00")

    def test_all_frozen_fixtures_resolve_and_hash_deterministically(self):
        for case in self.cases:
            _, first = self.resolver.resolve(case); _, second = self.resolver.resolve(case)
            self.assertEqual(first, second)
        self.assertEqual(content_hash(b"same"), content_hash(b"same"))

    def test_invalid_and_secret_fixture_rejected(self):
        with self.assertRaises(ValidationError):
            SanitizedProviderFixture(fixture_version="v1", provider="xhs", state="available", payload={"apiKey": "bad"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); path = root / "fixtures/xhs/beijing_culture_v1.json"; path.parent.mkdir(parents=True); path.write_text("{}")
            with self.assertRaises(FixtureError): FrozenFixtureResolver(root).resolve(self.case)

    def test_network_access_is_blocked(self):
        with self.assertRaisesRegex(NetworkAccessBlocked, "network_access_blocked"):
            with deny_network(): socket.create_connection(("example.com", 80))

    def test_accidental_network_attempt_marks_run_blocked(self):
        def accidental_network(*args, **kwargs):
            socket.create_connection(("example.com", 80))
        with patch("backend.app.evaluation.runner.calculate_metrics", side_effect=accidental_network):
            run = self.make_run(run_id="eval_network_attempt")
        self.assertEqual(run.run_status, "network_access_blocked")
        self.assertEqual(run.error, "network_access_blocked")
        self.assertEqual(run.metrics, [])

    def test_same_artifact_is_deterministic_and_not_mutated(self):
        artifact = self.artifact(); before = artifact.model_dump(mode="json")
        self.assertEqual(calculate_metrics(self.case, artifact), calculate_metrics(self.case, artifact))
        self.assertEqual(before, artifact.model_dump(mode="json"))

    def test_unknown_and_not_applicable_semantics(self):
        metrics = {x.metric: x for x in calculate_metrics(self.case, ArtifactEvaluationInput(output=plan(self.case)))}
        self.assertEqual(metrics["route_feasibility_rate"].status, "unknown")
        self.assertIsNone(metrics["route_feasibility_rate"].value)
        self.assertEqual(metrics["budget_limit_satisfaction"].status, "not_applicable")
        self.assertIsNone(metrics["budget_limit_satisfaction"].value)
        self.assertEqual(metrics["revision_risk_resolution_rate"].status, "not_applicable")

    def test_route_unavailable_is_not_infeasible(self):
        artifact = self.artifact(route_checks=[{"day_index": 0, "origin": "A", "destination": "B", "status": "unavailable"}])
        metrics = calculate_metrics(self.case, artifact); findings = label_badcases(self.case, artifact, metrics)
        labels = {x.label: x for x in findings}
        self.assertTrue(labels["route_unavailable"].detected)
        self.assertFalse(labels["route_infeasible"].detected)
        self.assertEqual(next(x for x in metrics if x.metric == "route_feasibility_rate").status, "unknown")

    def test_badcase_labels_budget_grounding_and_human_boundary(self):
        artifact = ArtifactEvaluationInput(output=plan(self.case, grounded=False, total=900, component_total=1000))
        findings = {x.label: x for x in label_badcases(self.case, artifact, calculate_metrics(self.case, artifact))}
        self.assertTrue(findings["ungrounded_poi"].detected)
        self.assertTrue(findings["budget_inconsistent"].detected)
        self.assertEqual(findings["preference_miss"].evidence_type, "human_required")
        self.assertEqual(findings["excessive_cost"].evidence_type, "not_evaluated")

    def test_fixture_mismatch_rejects_pair(self):
        base = self.make_run(); candidate = self.make_run(run_id="eval_test_candidate", hashes={**self.hashes, "different": "sha256:x"})
        with self.assertRaises(ComparisonError): compare_runs(base, candidate, "cmp")

    def test_known_to_unknown_is_investigate(self):
        base = self.make_run(); candidate = self.make_run(ArtifactEvaluationInput(output=plan(self.case), latency_ms=500), "eval_test_candidate")
        comparison = compare_runs(base, candidate, "cmp")
        self.assertEqual(comparison.release_decision, "INVESTIGATE")
        self.assertIn("known_to_unknown", {x.classification for x in comparison.metric_deltas})

    def test_hard_gate_regression_blocks(self):
        base = self.make_run(); invalid = self.artifact(); invalid.output = {"bad": "output"}
        candidate = self.make_run(invalid, "eval_test_candidate")
        self.assertEqual(compare_runs(base, candidate, "cmp").release_decision, "BLOCK")

    def test_soft_regression_investigates_and_clean_candidate_passes(self):
        base = self.make_run(); soft = self.artifact(); soft.usage.total_tokens = 151
        self.assertEqual(compare_runs(base, self.make_run(soft, "eval_test_soft"), "cmp-soft").release_decision, "INVESTIGATE")
        self.assertEqual(compare_runs(base, self.make_run(run_id="eval_test_clean"), "cmp-clean").release_decision, "PASS")

    def test_json_schema_and_markdown_report(self):
        base = self.make_run(); candidate = self.make_run(run_id="eval_test_clean"); comparison = compare_runs(base, candidate, "cmp")
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.json"; write_json_report(target, comparison)
            from backend.app.evaluation.models import PairedComparison
            PairedComparison.model_validate_json(target.read_bytes())
        markdown = comparison_markdown(comparison, base, candidate)
        self.assertIn("Deterministic metric deltas", markdown)
        self.assertIn("Human review", markdown)
        self.assertIn("Release decision", markdown)


if __name__ == "__main__": unittest.main()
