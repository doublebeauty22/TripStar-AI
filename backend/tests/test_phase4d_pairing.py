import json
import tempfile
import unittest
from pathlib import Path

from backend.app.evaluation.capture_models import PlannerCaptureArtifact
from backend.app.evaluation.phase4d_pairing import compare_pair, generate_blind_review


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "eval/pilots/phase3d3/gc_kyoto_no_early_start/gc_kyoto_no_early_start.json"


class Phase4DPairingTests(unittest.TestCase):
    def pair(self):
        baseline = PlannerCaptureArtifact.model_validate_json(BASE.read_bytes())
        candidate = baseline.model_copy(deep=True)
        candidate.identity.planner_version = "planner_pacing_v1"
        candidate.identity.prompt_version = "planner_prompt_pacing_v1"
        return baseline, candidate

    def test_pairing_detects_exact_and_provider_drift(self):
        baseline, candidate = self.pair()
        exact = compare_pair(baseline, candidate)
        self.assertTrue(exact["provider_snapshot_exact"])
        candidate.snapshot_hashes["google_places"] = "sha256:" + "0" * 64
        drift = compare_pair(baseline, candidate)
        self.assertEqual(drift["comparability"], "limited_provider_drift")
        self.assertFalse(drift["causal_claim_allowed"])

    def test_blind_material_hides_identity_scores_and_validator(self):
        baseline, candidate = self.pair()
        text, mapping = generate_blind_review(baseline, candidate)
        self.assertIn("Plan A", text); self.assertIn("Plan B", text)
        self.assertNotIn("planner_baseline_v1", text)
        self.assertNotIn("planner_pacing_v1", text)
        self.assertNotIn("validation_status", text)
        self.assertNotIn("Pacing = 4", text)
        self.assertEqual(set(mapping.values()), {"baseline", "candidate"})
