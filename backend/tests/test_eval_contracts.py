import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from backend.app.evaluation.models import (
    EvalCase,
    HumanReviewRubric,
    MetricResult,
    PlannerVersionMetadata,
    SanitizedProviderFixture,
)


ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = ROOT / "eval"


class EvaluationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_cases = json.loads(
            (EVAL_ROOT / "cases" / "golden_cases_v1.json").read_text(encoding="utf-8")
        )

    def test_all_golden_cases_validate_and_ids_are_unique(self):
        cases = [EvalCase.model_validate(item) for item in self.raw_cases]
        self.assertGreaterEqual(len(cases), 12)
        self.assertLessEqual(len(cases), 20)
        self.assertEqual(len(cases), len({case.case_id for case in cases}))

    def test_required_scenario_tags_are_covered(self):
        tags = {tag for item in self.raw_cases for tag in item["scenario_tags"]}
        required = {
            "single_city", "multi_city", "relaxed", "intensive", "avoid_early_start",
            "budget_limit", "mobility", "food_constraint", "multi_interest",
            "xhs_unavailable", "google_places_partial", "google_places_unavailable",
            "route_unavailable", "weather_fallback", "zh_input", "en_input",
            "local_patch", "revision_trigger",
        }
        self.assertEqual(required - tags, set())

    def test_invalid_case_is_rejected(self):
        invalid = dict(self.raw_cases[0])
        invalid["trip_request"] = dict(invalid["trip_request"], travel_days=9)
        with self.assertRaises(ValidationError):
            EvalCase.model_validate(invalid)

    def test_local_patch_requires_instruction(self):
        patch_case = next(item for item in self.raw_cases if "local_patch" in item["scenario_tags"])
        invalid = dict(patch_case)
        invalid.pop("patch_instruction")
        with self.assertRaises(ValidationError):
            EvalCase.model_validate(invalid)

    def test_human_rubric_has_anchored_dimensions(self):
        raw = json.loads((EVAL_ROOT / "contracts" / "human_rubric_v1.json").read_text(encoding="utf-8"))
        rubric = HumanReviewRubric.model_validate(raw)
        self.assertEqual(len(rubric.dimensions), 5)
        self.assertTrue(rubric.rationale_required)

    def test_planner_version_metadata_is_explicit(self):
        metadata = PlannerVersionMetadata(
            planner_version="planner.v1",
            prompt_version="planner_prompt.v1",
            model="fixture-model",
            eval_run_id="eval_20260812_001",
            case_id="gc_beijing_baseline",
            fixture_set_version="fixtures.v1",
        )
        self.assertEqual(metadata.planner_version, "planner.v1")

    def test_metric_unknown_is_not_converted_to_failure(self):
        result = MetricResult(
            metric="route_feasibility_rate", status="unknown", value=None,
            reason="no checked route legs", policy_version="metrics.v1",
        )
        self.assertEqual(result.status, "unknown")
        with self.assertRaises(ValidationError):
            MetricResult(
                metric="route_feasibility_rate", status="unknown", value=0,
                reason="no checked route legs", policy_version="metrics.v1",
            )

    def test_fixture_model_rejects_secret_keys_and_values(self):
        with self.assertRaises(ValidationError):
            SanitizedProviderFixture(
                fixture_version="v1", provider="xhs", state="available",
                payload={"cookie": "private"},
            )
        with self.assertRaises(ValidationError):
            SanitizedProviderFixture(
                fixture_version="v1", provider="google_places", state="available",
                payload={"header": "Bearer abcdefghijklmnop"},
            )
        with self.assertRaises(ValidationError):
            SanitizedProviderFixture(
                fixture_version="v1", provider="google_places", state="available",
                payload={"apiKey": "private"},
            )

    def test_fixture_model_accepts_sanitized_payload(self):
        fixture = SanitizedProviderFixture(
            fixture_version="v1", provider="google_places", state="partial",
            payload={"results": [{"name": "Synthetic Museum", "status": "partial"}]},
        )
        self.assertTrue(fixture.sanitized)


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
