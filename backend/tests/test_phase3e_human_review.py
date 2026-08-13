import unittest

from app.evaluation.models import HumanReviewRecord, ProductFailureReviewRecord


class Phase3EHumanReviewContractTests(unittest.TestCase):
    def test_pending_record_can_bind_immutable_artifact_without_scores(self):
        record = HumanReviewRecord(
            review_id="review_gc_beijing_baseline_v1",
            case_id="gc_beijing_baseline",
            planner_version="planner_baseline_v1",
            artifact_reference="eval/pilots/example.json",
            artifact_sha256="sha256:" + "a" * 64,
        )
        self.assertEqual(record.status, "pending")
        self.assertEqual(record.unsupported_fact, "pending")
        self.assertEqual(record.scores, {})

    def test_pending_record_rejects_simulated_human_evidence(self):
        with self.assertRaises(ValueError):
            HumanReviewRecord(
                case_id="gc_beijing_baseline",
                planner_version="planner_baseline_v1",
                scores={"usefulness": 5},
            )

    def test_complete_record_requires_per_dimension_rationale_and_fact_review(self):
        scores = {
            "preference_satisfaction": 4,
            "itinerary_coherence": 4,
            "pacing_quality": 3,
            "usefulness": 4,
            "explanation_quality": 3,
        }
        rationale = {key: f"Evidence for {key}" for key in scores}
        record = HumanReviewRecord(
            review_id="review_gc_beijing_baseline_v1",
            case_id="gc_beijing_baseline",
            planner_version="planner_baseline_v1",
            status="complete",
            artifact_reference="eval/pilots/example.json",
            artifact_sha256="sha256:" + "b" * 64,
            reviewer="reviewer-1",
            timestamp="2026-08-13T00:00:00Z",
            scores=scores,
            rationale_by_dimension=rationale,
            unsupported_fact="uncertain",
            unsupported_fact_rationale="The booking claim needs source verification.",
        )
        self.assertEqual(record.status, "complete")

    def test_existing_initial_and_patch_human_reviews_validate(self):
        from pathlib import Path

        root = Path("eval/phase3e/human_review_records")
        for name in (
            "gc_beijing_baseline.json",
            "gc_nanjing_local_patch.json",
        ):
            record = HumanReviewRecord.model_validate_json((root / name).read_text())
            self.assertEqual(record.status, "complete")


class ProductFailureReviewContractTests(unittest.TestCase):
    def base(self):
        return {
            "case_id": "gc_beijing_xian_multi_city",
            "artifact_reference": "eval/pilots/failure.json",
            "artifact_sha256": "sha256:" + "c" * 64,
            "failure_type": "planner_output_parse_failure",
            "failed_stage": "planner",
        }

    def test_valid_pending_failure_review_has_no_itinerary_scores(self):
        record = ProductFailureReviewRecord(**self.base())
        self.assertEqual(record.status, "pending")
        self.assertFalse(hasattr(record, "scores"))

    def test_valid_completed_failure_review(self):
        record = ProductFailureReviewRecord(
            **self.base(), status="complete", reviewer="reviewer-1",
            reviewed_at="2026-08-13T00:00:00Z", severity="high",
            severity_rationale="Complete task failure after a long wait.",
            recoverability="difficult",
            recoverability_rationale="No checkpoint or partial result exists.",
            retry_guidance_present="unknown",
            retry_guidance_rationale="The user-visible UI was not captured.",
            user_impact="high",
            user_impact_rationale="The user received no usable deliverable.",
        )
        self.assertEqual(record.status, "complete")

    def test_invalid_enum_is_rejected(self):
        with self.assertRaises(ValueError):
            ProductFailureReviewRecord(**self.base(), severity="severe")

    def test_complete_requires_reviewer_and_reviewed_at(self):
        with self.assertRaises(ValueError):
            ProductFailureReviewRecord(
                **self.base(), status="complete", severity="high",
                recoverability="difficult", retry_guidance_present="unknown",
                user_impact="high", severity_rationale="x",
                recoverability_rationale="x", retry_guidance_rationale="x",
                user_impact_rationale="x",
            )

    def test_complete_requires_every_rationale(self):
        with self.assertRaises(ValueError):
            ProductFailureReviewRecord(
                **self.base(), status="complete", reviewer="reviewer-1",
                reviewed_at="2026-08-13T00:00:00Z", severity="high",
                recoverability="difficult", retry_guidance_present="unknown",
                user_impact="high", severity_rationale="x",
                recoverability_rationale="x", retry_guidance_rationale="x",
            )


if __name__ == "__main__":
    unittest.main()
