import unittest
from pathlib import Path

from app.evaluation.pacing_regression import assess_capture_artifact


ROOT = Path(__file__).resolve().parents[2]


class Phase4BKnownCaseRegressionTests(unittest.TestCase):
    def test_badcases_retain_directional_detection(self):
        artifacts = [
            "eval/pilots/phase3d4/gc_shenzhen_overbudget_revision/gc_shenzhen_overbudget_revision.json",
            "eval/pilots/phase3d42/gc_chengdu_budget/gc_chengdu_budget.json",
            "eval/pilots/phase3d42/gc_lijiang_places_unavailable/gc_lijiang_places_unavailable.json",
        ]
        for artifact in artifacts:
            rows = assess_capture_artifact(ROOT / artifact)
            self.assertTrue(any(row["overload_status"] == "revisable_overload" for row in rows))

    def test_kyoto_control_has_no_revisable_false_positive(self):
        rows = assess_capture_artifact(
            ROOT / "eval/pilots/phase3d3/gc_kyoto_no_early_start/gc_kyoto_no_early_start.json"
        )
        self.assertTrue(all(row["overload_status"] != "revisable_overload" for row in rows))
