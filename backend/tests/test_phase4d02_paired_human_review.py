import json
import hashlib
import unittest
from pathlib import Path

from pydantic import ValidationError

from backend.app.evaluation.models import (
    HumanReviewRecord, PairedHumanReviewRecord, PairedPlanHumanReview,
    PairedReviewReveal, ProductFailureReviewRecord, RevealedPlanIdentity,
)


SCORES = {
    "preference_satisfaction": 4, "itinerary_coherence": 4,
    "pacing_quality": 4, "usefulness": 4, "explanation_quality": 4,
}


def plan_review(**changes):
    value = dict(
        scores=SCORES,
        rationale_by_dimension={key: f"Evidence for {key}." for key in SCORES},
        unsupported_fact="uncertain",
        unsupported_fact_rationale="Some external facts require confirmation.",
    )
    value.update(changes)
    return PairedPlanHumanReview(**value)


def complete(**changes):
    value = dict(
        review_id="paired_review_shenzhen_v1", case_id="gc_shenzhen_overbudget_revision",
        status="blind_complete", reviewer="Yi Huang",
        reviewed_at="2026-08-13T10:00:00Z",
        blind_review_completed_at="2026-08-13T10:00:00Z",
        blind_material_reference="eval/phase4d/blind_review/review.md",
        blind_material_sha256="sha256:" + "a" * 64,
        blind_order_integrity="limitation", plan_a_review=plan_review(),
        plan_b_review=plan_review(), paired_verdict="plan_b_better",
        paired_rationale="Plan B is more balanced and executable.",
    )
    value.update(changes)
    return value


def identity(role, suffix):
    return RevealedPlanIdentity(
        role=role, artifact_reference=f"eval/{suffix}.json",
        artifact_sha256="sha256:" + suffix * 64,
        planner_version=f"planner_{role}_v1", prompt_version=f"prompt_{role}_v1",
    )


class PairedHumanReviewSchemaTests(unittest.TestCase):
    def test_pending_valid(self):
        self.assertEqual(PairedHumanReviewRecord(
            review_id="paired_review_pending", case_id="gc_shenzhen_overbudget_revision",
        ).status, "pending")

    def test_blind_complete_unrevealed_valid(self):
        record = PairedHumanReviewRecord(**complete())
        self.assertFalse(record.identity_revealed)
        self.assertIsNone(record.reveal)

    def test_revealed_complete_valid(self):
        reveal = PairedReviewReveal(
            plan_a_identity=identity("baseline", "a"),
            plan_b_identity=identity("candidate", "b"),
            baseline_label="Plan A", candidate_label="Plan B",
        )
        record = PairedHumanReviewRecord(**complete(
            status="revealed_complete", identity_revealed=True,
            identity_revealed_at="2026-08-13T10:01:00Z", reveal=reveal,
        ))
        self.assertTrue(record.identity_revealed)

    def test_incomplete_five_scores_rejected(self):
        scores = dict(SCORES); scores.pop("usefulness")
        with self.assertRaises(ValidationError): plan_review(scores=scores)

    def test_missing_rationale_rejected(self):
        rationale = {key: "evidence" for key in SCORES}; rationale.pop("pacing_quality")
        with self.assertRaises(ValidationError): plan_review(rationale_by_dimension=rationale)

    def test_reveal_before_blind_completion_rejected(self):
        reveal = PairedReviewReveal(plan_a_identity=identity("baseline", "a"),
            plan_b_identity=identity("candidate", "b"), baseline_label="Plan A", candidate_label="Plan B")
        with self.assertRaises(ValidationError): PairedHumanReviewRecord(**complete(
            status="revealed_complete", identity_revealed=True,
            identity_revealed_at="2026-08-13T09:59:00Z", reveal=reveal))

    def test_identity_while_unrevealed_rejected(self):
        reveal = PairedReviewReveal(plan_a_identity=identity("baseline", "a"),
            plan_b_identity=identity("candidate", "b"), baseline_label="Plan A", candidate_label="Plan B")
        with self.assertRaises(ValidationError): PairedHumanReviewRecord(**complete(reveal=reveal))

    def test_invalid_paired_verdict_rejected(self):
        with self.assertRaises(ValidationError): PairedHumanReviewRecord(**complete(paired_verdict="better"))

    def test_json_round_trip_stable(self):
        record = PairedHumanReviewRecord(**complete())
        dumped = record.model_dump(mode="json")
        self.assertEqual(PairedHumanReviewRecord.model_validate_json(
            json.dumps(dumped)).model_dump(mode="json"), dumped)

    def test_old_human_review_records_all_readable(self):
        for path in Path("eval/phase3e/human_review_records").glob("gc_*.json"):
            if not path.name.endswith("failure_review.json"):
                self.assertEqual(HumanReviewRecord.model_validate_json(path.read_text()).status, "complete")

    def test_product_failure_review_regression(self):
        path = Path("eval/phase3e/human_review_records/gc_beijing_xian_multi_city.failure_review.json")
        self.assertEqual(ProductFailureReviewRecord.model_validate_json(path.read_text()).status, "complete")

    def test_shenzhen_reveal_hashes_scores_ordering_and_result(self):
        root = Path("eval/phase4d")
        record_path = root / "human_reviews/gc_shenzhen_overbudget_revision.json"
        record = PairedHumanReviewRecord.model_validate_json(record_path.read_text())
        self.assertEqual(record.status, "revealed_complete")
        self.assertGreaterEqual(record.identity_revealed_at, record.blind_review_completed_at)
        self.assertEqual(record.blind_order_integrity, "limitation")
        self.assertEqual(record.plan_a_review.scores, {
            "preference_satisfaction": 4, "itinerary_coherence": 3,
            "pacing_quality": 2, "usefulness": 3, "explanation_quality": 4,
        })
        self.assertEqual(record.plan_b_review.scores, SCORES)
        for plan_identity in (record.reveal.plan_a_identity, record.reveal.plan_b_identity):
            actual = "sha256:" + hashlib.sha256(Path(plan_identity.artifact_reference).read_bytes()).hexdigest()
            self.assertEqual(actual, plan_identity.artifact_sha256)
        material = "sha256:" + hashlib.sha256(Path(record.blind_material_reference).read_bytes()).hexdigest()
        self.assertEqual(material, record.blind_material_sha256)
        result_path = root / "results/gc_shenzhen_overbudget_revision.paired_result.json"
        result = json.loads(result_path.read_text())
        self.assertEqual(result["candidate_minus_baseline"], {
            "preference_satisfaction": 0, "itinerary_coherence": 1,
            "pacing_quality": 2, "usefulness": 1, "explanation_quality": 0,
        })
        self.assertEqual(json.loads(json.dumps(result, sort_keys=True)), result)

    def test_chengdu_reveal_hashes_scores_ordering_and_result(self):
        root = Path("eval/phase4d")
        record = PairedHumanReviewRecord.model_validate_json(
            (root / "human_reviews/gc_chengdu_budget.json").read_text())
        self.assertEqual(record.status, "revealed_complete")
        self.assertGreaterEqual(record.identity_revealed_at, record.blind_review_completed_at)
        self.assertEqual(record.blind_order_integrity, "limitation")
        self.assertEqual(record.plan_a_review.scores, {
            "preference_satisfaction": 4, "itinerary_coherence": 4,
            "pacing_quality": 3, "usefulness": 3, "explanation_quality": 4,
        })
        self.assertEqual(record.plan_b_review.scores, SCORES)
        for identity_value in (record.reveal.plan_a_identity, record.reveal.plan_b_identity):
            actual = "sha256:" + hashlib.sha256(Path(identity_value.artifact_reference).read_bytes()).hexdigest()
            self.assertEqual(actual, identity_value.artifact_sha256)
        self.assertEqual("sha256:" + hashlib.sha256(
            Path(record.blind_material_reference).read_bytes()).hexdigest(), record.blind_material_sha256)
        result = json.loads((root / "results/gc_chengdu_budget.paired_result.json").read_text())
        self.assertEqual(result["candidate_minus_baseline"], {
            "preference_satisfaction": 0, "itinerary_coherence": 0,
            "pacing_quality": 1, "usefulness": 1, "explanation_quality": 0,
        })
        self.assertTrue(result["evaluation_semantics_limitation"]["present"])
        self.assertEqual(result["grounding_outcome_capture"], "unavailable")

    def test_lijiang_reveal_hashes_scores_ordering_and_result(self):
        root = Path("eval/phase4d")
        record = PairedHumanReviewRecord.model_validate_json(
            (root / "human_reviews/gc_lijiang_places_unavailable.json").read_text())
        self.assertEqual(record.status, "revealed_complete")
        self.assertGreaterEqual(record.identity_revealed_at, record.blind_review_completed_at)
        self.assertEqual(record.blind_order_integrity, "limitation")
        self.assertEqual(record.plan_a_review.scores, {
            "preference_satisfaction": 4, "itinerary_coherence": 4,
            "pacing_quality": 3, "usefulness": 4, "explanation_quality": 4,
        })
        self.assertEqual(record.plan_b_review.scores, {
            "preference_satisfaction": 5, "itinerary_coherence": 5,
            "pacing_quality": 4, "usefulness": 4, "explanation_quality": 4,
        })
        for identity_value in (record.reveal.plan_a_identity, record.reveal.plan_b_identity):
            actual = "sha256:" + hashlib.sha256(Path(identity_value.artifact_reference).read_bytes()).hexdigest()
            self.assertEqual(actual, identity_value.artifact_sha256)
        self.assertEqual("sha256:" + hashlib.sha256(
            Path(record.blind_material_reference).read_bytes()).hexdigest(), record.blind_material_sha256)
        result = json.loads((root / "results/gc_lijiang_places_unavailable.paired_result.json").read_text())
        self.assertEqual(result["candidate_minus_baseline"], {
            "preference_satisfaction": 1, "itinerary_coherence": 1,
            "pacing_quality": 1, "usefulness": 0, "explanation_quality": 0,
        })
        self.assertEqual(result["revision"]["failure_reason"], "invalid_revision_output")
        self.assertFalse(result["revision"]["committed"])

    def test_kyoto_reveal_and_four_case_aggregate(self):
        root = Path("eval/phase4d")
        record = PairedHumanReviewRecord.model_validate_json(
            (root / "human_reviews/gc_kyoto_no_early_start.json").read_text())
        self.assertEqual(record.status, "revealed_complete")
        self.assertGreaterEqual(record.identity_revealed_at, record.blind_review_completed_at)
        self.assertEqual(record.paired_verdict, "mixed")
        self.assertTrue(record.metric_improvement_but_ux_regression)
        for identity_value in (record.reveal.plan_a_identity, record.reveal.plan_b_identity):
            actual = "sha256:" + hashlib.sha256(Path(identity_value.artifact_reference).read_bytes()).hexdigest()
            self.assertEqual(actual, identity_value.artifact_sha256)
        results = [json.loads(path.read_text()) for path in sorted((root / "results").glob("gc_*.paired_result.json"))]
        self.assertEqual(len(results), 4)
        dimensions = list(SCORES)
        means = {dimension: sum(item["candidate_minus_baseline"][dimension]
                                for item in results) / len(results) for dimension in dimensions}
        aggregate = json.loads((root / "phase4d_aggregate.json").read_text())
        self.assertEqual(means, aggregate["mean_candidate_minus_baseline"])
        self.assertEqual(aggregate["outcome_counts"], {
            "BETTER": 3, "MIXED": 1, "WORSE": 0, "EQUIVALENT": 0,
        })
        self.assertEqual(aggregate["positive_pacing_delta_cases"], 3)
        self.assertEqual(aggregate["negative_usefulness_delta_cases"], 1)
        self.assertEqual(aggregate["revision_summary"]["committed"], 2)
        self.assertEqual(aggregate["revision_summary"]["rejected"], 2)


if __name__ == "__main__":
    unittest.main()
