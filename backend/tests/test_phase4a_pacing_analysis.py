import json
import unittest
from pathlib import Path

from app.evaluation.pacing_analysis import DEFAULT_POLICY, simulate_artifact


ROOT = Path(__file__).resolve().parents[2]


class Phase4APacingAnalysisTests(unittest.TestCase):
    def test_policy_matches_production_enum_and_is_proposed(self):
        policy = json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))
        self.assertEqual(policy["status"], "PROPOSED")
        self.assertEqual(policy["production_enum"], ["intensive", "balanced", "relaxed"])

    def test_known_badcases_have_revisable_overload_days(self):
        paths = [
            "eval/pilots/phase3d4/gc_shenzhen_overbudget_revision/gc_shenzhen_overbudget_revision.json",
            "eval/pilots/phase3d42/gc_chengdu_budget/gc_chengdu_budget.json",
            "eval/pilots/phase3d42/gc_lijiang_places_unavailable/gc_lijiang_places_unavailable.json",
        ]
        for path in paths:
            rows = simulate_artifact(ROOT / path)
            self.assertTrue(any(row["overload_status"] == "revisable_overload" for row in rows))

    def test_relaxed_control_has_no_revisable_overload(self):
        rows = simulate_artifact(
            ROOT / "eval/pilots/phase3d3/gc_kyoto_no_early_start/gc_kyoto_no_early_start.json"
        )
        self.assertTrue(all(row["overload_status"] != "revisable_overload" for row in rows))

    def test_unknown_routes_are_not_zero_and_are_not_infeasible(self):
        rows = simulate_artifact(
            ROOT / "eval/pilots/phase3d4/gc_shenzhen_overbudget_revision/gc_shenzhen_overbudget_revision.json"
        )
        unknown = [row for row in rows if row["estimated_travel_minutes"]]
        self.assertTrue(unknown)
        self.assertTrue(all(row["uncertainty_buffer_minutes"] > 0 for row in unknown))
        self.assertTrue(all("infeasible" not in row["overload_reasons"] for row in unknown))
