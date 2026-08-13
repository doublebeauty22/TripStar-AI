import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.app.evaluation.capture_models import RevisionCapture
from backend.app.evaluation.production_capture import (
    ProductionExecutionError, build_revision_capture, build_revision_capture_safely,
)
from backend.app.models.schemas import Attraction, DayPlan, Location, TripPlan
from backend.app.services.pacing_revision_service import (
    DelayStartTime, PacingRevisionProposal, ReduceOptionalDuration,
    RemoveOptionalPOI, validate_affected_day_grounding,
)


def poi(name, place_id="", status=None):
    return Attraction(
        name=name, address="test", location=Location(longitude=114.05, latitude=22.55),
        visit_duration=120, description="test", category="sight",
        place_id=place_id, poi_id=place_id,
        poi_match_status=status or ("verified" if place_id else "unverified"),
        map_data_source="google_places" if place_id else "llm_unverified",
    )


def plan(items):
    return TripPlan(
        city="Shenzhen", start_date="2026-10-01", end_date="2026-10-01",
        days=[DayPlan(date="2026-10-01", day_index=0, city="Shenzhen",
                      description="test", transportation="transit",
                      accommodation="hotel", attractions=items)],
        overall_suggestions="test",
    )


def proposal(operation):
    return PacingRevisionProposal(
        target_risk_ids=["pacing_daily_load:day:0"], affected_day_indices=[0],
        protected_day_indices=[], operations=[operation],
    )


class TypedRevisionCaptureTests(unittest.TestCase):
    def capture(self, metadata):
        return RevisionCapture(status="not_applicable", reason="contract test",
                               revision_instructions_metadata=metadata)

    def test_remove_reduce_delay_and_round_trip(self):
        values = [
            {"operation": "remove_optional_poi", "day_index": 0, "target_id": "a",
             "target_name": "A", "reason": "optional"},
            {"operation": "reduce_optional_duration", "day_index": 0, "target_id": "b",
             "target_name": "B", "old_minutes": 180, "new_minutes": 90, "reason": "load"},
            {"operation": "delay_start_time", "day_index": 0, "old_value": "08:00",
             "new_value": "09:00", "reason": "preference"},
        ]
        captured = self.capture(values)
        dumped = captured.model_dump(mode="json")
        self.assertEqual(RevisionCapture.model_validate(dumped).model_dump(mode="json"), dumped)
        self.assertEqual([item["operation"] for item in dumped["revision_instructions_metadata"]],
                         [item["operation"] for item in values])

    def test_legacy_strings_remain_readable(self):
        value = self.capture(["Remove optional stop", "Keep hotel"])
        self.assertEqual(value.revision_instructions_metadata[0], "Remove optional stop")

    def test_invalid_typed_metadata_fails_closed(self):
        with self.assertRaises(ValidationError):
            self.capture([{"operation": "reduce_optional_duration", "day_index": 0,
                           "target_id": "b", "target_name": "B", "new_minutes": 90}])

    def test_capture_failure_wrap_retains_execution_telemetry(self):
        usage = {"logical_llm_calls": 3, "total_tokens": 34581, "model": "known-model"}
        with patch("backend.app.evaluation.production_capture.build_revision_capture",
                   side_effect=ValidationError.from_exception_data("capture", [])):
            with self.assertRaises(ProductionExecutionError) as raised:
                build_revision_capture_safely(None, [], usage, 123698)
        error = raised.exception
        self.assertEqual(error.failure_type, "capture_validation_failure")
        self.assertEqual(error.failed_stage, "capture_serialization")
        self.assertEqual(error.usage, usage)
        self.assertEqual(error.elapsed_ms, 123698)

    def test_safely_rejected_candidate_metadata_serializes(self):
        before = plan([poi("Keep", "v1"), poi("Optional")])
        operation = {"operation": "remove_optional_poi", "day_index": 0,
                     "target_id": "day:0:poi:1", "target_name": "Optional",
                     "reason": "reduce load"}
        events = [
            {"event": "pacing_revision_proposal", "revision_instructions": [operation]},
            {"event": "pacing_revision_result", "status": "rejected", "before": before,
             "candidate": before, "after": before, "target_risk_ids": ["risk-1"],
             "affected_day_indices": [0], "protected_day_indices": [],
             "protected_day_equality": {}, "post_pacing_risk_ids": ["risk-1"],
             "resolution_outcome": "rejected",
             "failure_reason": "retained_poi_grounding_regression",
             "grounding_details": {"day_index": 0, "poi_name": "Keep"},
             "pacing_policy_version": "pacing.daily_load.v0.proposed", "metrics": {}},
        ]
        captured = build_revision_capture(before, events)
        dumped = captured.model_dump(mode="json")
        self.assertEqual(dumped["revision_status"], "rejected")
        self.assertEqual(dumped["failure_reason"], "retained_poi_grounding_regression")
        self.assertEqual(dumped["revision_instructions_metadata"], [operation])


class OperationAwareGroundingTests(unittest.TestCase):
    def test_remove_unverified_improves_aggregate(self):
        before = plan([poi("Verified", "v1"), poi("Optional")])
        after = plan([poi("Verified", "v1")])
        op = RemoveOptionalPOI(operation="remove_optional_poi", day_index=0,
                               target_id="day:0:poi:1", target_name="Optional")
        result = validate_affected_day_grounding(before, after, [0], proposal(op))
        self.assertTrue(result.accepted)
        self.assertEqual(result.outcome, "grounding_improvement")

    def test_remove_verified_is_valid_when_retained_stays_verified(self):
        before = plan([poi("Keep", "v1"), poi("Remove", "v2")])
        after = plan([poi("Keep", "v1")])
        op = RemoveOptionalPOI(operation="remove_optional_poi", day_index=0,
                               target_id="v2", target_name="Remove")
        self.assertTrue(validate_affected_day_grounding(before, after, [0], proposal(op)).accepted)

    def test_retained_verified_downgrade_rejects(self):
        before = plan([poi("Keep", "v1")])
        after = plan([poi("Keep")])
        op = ReduceOptionalDuration(operation="reduce_optional_duration", day_index=0,
            target_id="v1", target_name="Keep", old_minutes=120, new_minutes=60)
        result = validate_affected_day_grounding(before, after, [0], proposal(op))
        self.assertEqual(result.failure_reason, "retained_poi_grounding_regression")

    def test_new_unverified_rejects_and_new_verified_accepts(self):
        before = plan([poi("Keep", "v1")])
        op = DelayStartTime(operation="delay_start_time", day_index=0,
                            old_value=None, new_value="09:00")
        rejected = validate_affected_day_grounding(
            before, plan([poi("Keep", "v1"), poi("New")]), [0], proposal(op))
        accepted = validate_affected_day_grounding(
            before, plan([poi("Keep", "v1"), poi("New", "v2")]), [0], proposal(op))
        self.assertEqual(rejected.failure_reason, "new_or_changed_poi_unverified")
        self.assertTrue(accepted.accepted)


if __name__ == "__main__":
    unittest.main()
