import unittest
from unittest.mock import patch

from backend.app.models.schemas import (
    Attraction, Budget, DayPlan, Location, Meal, PreferenceConstraints,
    PreferenceProfile, TripPlan, TripRequest,
)
from backend.app.services.pacing_revision_service import (
    DelayStartTime, PacingRevisionProposal, PacingRevisionService,
    ReduceOptionalDuration, RemoveOptionalPOI, select_pacing_revision_risks,
)
from backend.app.services.trip_validator_service import TripValidatorService


def poi(name, minutes=240, price=0, place_id=""):
    return Attraction(
        name=name, address="test", location=Location(longitude=139.7, latitude=35.6),
        visit_duration=minutes, description="optional test", category="museum",
        ticket_price=price, place_id=place_id, poi_id=place_id,
        poi_match_status="verified" if place_id else "unverified",
        map_data_source="google_places" if place_id else "llm_unverified",
    )


def request(pace="balanced", earliest=None, budget=5000):
    return TripRequest(
        city="Tokyo", start_date="2026-10-01", end_date="2026-10-02", travel_days=2,
        transportation="transit", accommodation="hotel",
        preference_profile=PreferenceProfile(
            party_type="couple", party_size=2, pace=pace, budget_cny=budget,
            constraints=PreferenceConstraints(
                avoid_early_start=bool(earliest), earliest_start_time=earliest,
            ),
        ),
    )


def plan(start="09:00", minutes=(270, 270)):
    days = [DayPlan(
        date="2026-10-01", day_index=0, start_time=start, city="Tokyo",
        description="urban", transportation="transit", accommodation="hotel",
        attractions=[poi("Optional A", minutes[0]), poi("Optional B", minutes[1])],
        meals=[Meal(type="lunch", name="lunch"), Meal(type="dinner", name="dinner")],
    ), DayPlan(
        date="2026-10-02", day_index=1, start_time="10:00", city="Tokyo",
        description="protected", transportation="transit", accommodation="hotel",
        attractions=[poi("Protected", 90)], meals=[],
    )]
    return TripPlan(
        city="Tokyo", start_date="2026-10-01", end_date="2026-10-02", days=days,
        overall_suggestions="test", budget=Budget(
            total_attractions=0, total_hotels=1000, total_meals=200,
            total_transportation=100, total_inter_city_transport=0, total=1300,
        ),
    )


async def identity_enricher(candidate, affected):
    return candidate.model_copy(deep=True)


class Phase4CPacingRevisionTests(unittest.IsolatedAsyncioTestCase):
    async def validate(self, req, value):
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=None):
            return await TripValidatorService().validate(req, value)

    async def targets(self, req, value):
        validation = await self.validate(req, value)
        return validation, select_pacing_revision_risks(validation.risks)

    async def test_a_removal_resolves_and_preserves_protected_day(self):
        req, before = request(), plan()
        validation, targets = await self.targets(req, before)
        self.assertEqual(len(targets), 1)
        proposal = PacingRevisionProposal(
            target_risk_ids=[targets[0].id], affected_day_indices=[0], protected_day_indices=[1],
            operations=[RemoveOptionalPOI(
                operation="remove_optional_poi", day_index=0,
                target_id="day:0:poi:1", target_name="Optional B",
            )],
        )
        outcome = await PacingRevisionService().execute(
            req, before, validation.risks, proposal,
            enricher=identity_enricher, validator=self.validate,
        )
        self.assertEqual(outcome.status, "success")
        self.assertEqual(outcome.committed_plan.days[1], before.days[1])
        self.assertEqual(outcome.metrics["pacing_revision_resolution_rate"], 1.0)
        self.assertLess(outcome.metrics["affected_day_load_ratio_after"][0],
                        outcome.metrics["affected_day_load_ratio_before"][0])

    async def test_b_still_overload_is_unresolved_and_original_retained(self):
        req, before = request("relaxed"), plan(minutes=(360, 360))
        validation, targets = await self.targets(req, before)
        proposal = PacingRevisionProposal(
            target_risk_ids=[targets[0].id], affected_day_indices=[0], protected_day_indices=[1],
            operations=[ReduceOptionalDuration(
                operation="reduce_optional_duration", day_index=0,
                target_id="day:0:poi:1", target_name="Optional B", old_minutes=360, new_minutes=330,
            )],
        )
        outcome = await PacingRevisionService().execute(
            req, before, validation.risks, proposal,
            enricher=identity_enricher, validator=self.validate,
        )
        self.assertEqual((outcome.status, outcome.failure_reason),
                         ("unresolved", "target_risk_unresolved"))
        self.assertEqual(outcome.committed_plan, before)

    async def test_c_protected_day_operation_hard_rejects(self):
        req, before = request(), plan()
        validation, targets = await self.targets(req, before)
        proposal = PacingRevisionProposal(
            target_risk_ids=[targets[0].id], affected_day_indices=[0], protected_day_indices=[1],
            operations=[RemoveOptionalPOI(
                operation="remove_optional_poi", day_index=1,
                target_id="day:1:poi:0", target_name="Protected",
            )],
        )
        outcome = await PacingRevisionService().execute(
            req, before, validation.risks, proposal,
            enricher=identity_enricher, validator=self.validate,
        )
        self.assertEqual(outcome.failure_reason, "protected_day_drift")
        self.assertEqual(outcome.committed_plan, before)

    async def test_d_earlier_start_rejects_explicit_no_early_start(self):
        req, before = request(earliest="10:00"), plan(start="10:00")
        validation, targets = await self.targets(req, before)
        proposal = PacingRevisionProposal(
            target_risk_ids=[targets[0].id], affected_day_indices=[0], protected_day_indices=[1],
            operations=[DelayStartTime(
                operation="delay_start_time", day_index=0, old_value="10:00", new_value="08:00",
            )],
        )
        outcome = await PacingRevisionService().execute(
            req, before, validation.risks, proposal,
            enricher=identity_enricher, validator=self.validate,
        )
        self.assertEqual(outcome.failure_reason, "constraint_regression")

    async def test_e_budget_regression_after_enrichment_rejects(self):
        req, before = request(), plan()
        validation, targets = await self.targets(req, before)
        proposal = PacingRevisionProposal(
            target_risk_ids=[targets[0].id], affected_day_indices=[0], protected_day_indices=[1],
            operations=[ReduceOptionalDuration(
                operation="reduce_optional_duration", day_index=0,
                target_id="day:0:poi:1", target_name="Optional B",
                old_minutes=270, new_minutes=60,
            )],
        )
        async def corrupt_budget(candidate, affected):
            candidate = candidate.model_copy(deep=True); candidate.budget.total += 1; return candidate
        outcome = await PacingRevisionService().execute(
            req, before, validation.risks, proposal,
            enricher=corrupt_budget, validator=self.validate,
        )
        self.assertEqual(outcome.failure_reason, "budget_regression")
        self.assertEqual(outcome.committed_plan, before)

    async def test_f_route_unavailable_semantics_remain_after_resolution(self):
        req, before = request(), plan()
        validation, targets = await self.targets(req, before)
        self.assertTrue(any(risk.type == "validation_unavailable" for risk in validation.risks))
        proposal = PacingRevisionProposal(
            target_risk_ids=[targets[0].id], affected_day_indices=[0], protected_day_indices=[1],
            operations=[ReduceOptionalDuration(
                operation="reduce_optional_duration", day_index=0,
                target_id="day:0:poi:1", target_name="Optional B",
                old_minutes=270, new_minutes=60,
            )],
        )
        outcome = await PacingRevisionService().execute(
            req, before, validation.risks, proposal,
            enricher=identity_enricher, validator=self.validate,
        )
        self.assertEqual(outcome.status, "success")
        self.assertTrue(any(risk["type"] == "validation_unavailable"
                            for risk in outcome.post_validation["risks"]))
        self.assertFalse(any(risk.get("evidence", {}).get("data_source") == "google_directions"
                             for risk in outcome.post_validation["risks"] if risk["type"] == "pacing"))

    async def test_g_warning_only_does_not_trigger(self):
        validation = await self.validate(request(), plan(minutes=(210, 210)))
        self.assertTrue(any(risk.type == "pacing" for risk in validation.risks))
        self.assertEqual(select_pacing_revision_risks(validation.risks), [])

    async def test_h_low_confidence_does_not_trigger(self):
        req, before = request(), plan()
        before.days[0].is_transfer_day = True
        validation = await self.validate(req, before)
        pacing = next(risk for risk in validation.risks if risk.type == "pacing")
        self.assertEqual(pacing.evidence["confidence"], "LOW")
        self.assertEqual(select_pacing_revision_risks(validation.risks), [])

    async def test_failure_taxonomy_invalid_enrichment_grounding_and_unsupported(self):
        req, before = request(), plan()
        validation, targets = await self.targets(req, before)
        invalid = PacingRevisionProposal(
            target_risk_ids=[targets[0].id], affected_day_indices=[0], protected_day_indices=[1],
            operations=[ReduceOptionalDuration(
                operation="reduce_optional_duration", day_index=0,
                target_id="day:0:poi:1", target_name="Optional B", old_minutes=240, new_minutes=60,
            )],
        )
        service = PacingRevisionService()
        outcome = await service.execute(req, before, validation.risks, invalid,
                                        enricher=identity_enricher, validator=self.validate)
        self.assertEqual(outcome.failure_reason, "invalid_revision_output")

        valid = PacingRevisionProposal(
            target_risk_ids=[targets[0].id], affected_day_indices=[0], protected_day_indices=[1],
            operations=[ReduceOptionalDuration(
                operation="reduce_optional_duration", day_index=0,
                target_id="day:0:poi:1", target_name="Optional B", old_minutes=270, new_minutes=60,
            )],
        )
        async def fail_enrichment(candidate, affected): raise RuntimeError("offline")
        outcome = await service.execute(req, before, validation.risks, valid,
                                        enricher=fail_enrichment, validator=self.validate)
        self.assertEqual(outcome.failure_reason, "enrichment_failure")

        async def invent_grounding(candidate, affected):
            candidate = candidate.model_copy(deep=True)
            candidate.days[0].attractions[0].place_id = "invented"
            candidate.days[0].attractions[0].poi_id = "invented"
            candidate.days[0].attractions[0].poi_match_status = "verified"
            candidate.days[0].attractions[0].map_data_source = "google_places"
            return candidate
        outcome = await service.execute(req, before, validation.risks, valid,
                                        enricher=invent_grounding, validator=self.validate)
        self.assertEqual(outcome.status, "success")
        self.assertEqual(outcome.grounding_outcome, "grounding_improvement")

        mismatch = valid.model_copy(deep=True)
        mismatch.target_risk_ids = ["pacing_daily_load:day:99"]
        outcome = await service.execute(req, before, validation.risks, mismatch,
                                        enricher=identity_enricher, validator=self.validate)
        self.assertEqual(outcome.failure_reason, "pacing_revision_unsupported")
