import inspect
import json
import unittest
from unittest.mock import patch

from backend.app.agents.trip_planner_agent import (
    BASELINE_PLANNER_PROMPT_VERSION, BASELINE_PLANNER_VERSION,
    MultiAgentTripPlanner, PLANNER_PROMPT_VERSION, PLANNER_VERSION,
)
from backend.app.models.schemas import (
    Attraction, DayPlan, Location, Meal, PreferenceConstraints, PreferenceProfile,
    TripRequest,
)
from backend.app.services.pacing_policy import (
    PACING_POLICY_VERSION, RouteLegEvidence, calculate_daily_load, normalize_attractions,
)
from backend.app.services.trip_validator_service import TripValidatorService


def attraction(name, minutes, *, category="museum", place_id=""):
    return Attraction(
        name=name, address="test", location=Location(longitude=139.7, latitude=35.6),
        visit_duration=minutes, description="test", category=category,
        place_id=place_id, poi_id=place_id,
        poi_match_status="verified" if place_id else "unverified",
        map_data_source="google_places" if place_id else "llm_unverified",
    )


def day(attractions, *, start="10:00", transfer=False):
    return DayPlan(
        date="2026-10-01", day_index=0, start_time=start, city="Tokyo",
        is_transfer_day=transfer, description="urban day", transportation="transit",
        accommodation="hotel", attractions=attractions,
        meals=[Meal(type="lunch", name="lunch"), Meal(type="dinner", name="dinner")],
    )


def request(pace="balanced", *, earliest=None):
    return TripRequest(
        city="Tokyo", start_date="2026-10-01", end_date="2026-10-01", travel_days=1,
        transportation="transit", accommodation="hotel",
        preference_profile=PreferenceProfile(
            party_type="couple", party_size=2, pace=pace,
            constraints=PreferenceConstraints(
                avoid_early_start=bool(earliest), earliest_start_time=earliest,
            ),
        ),
    )


class Phase4BPacingPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_relaxed_normal_day_is_within_target(self):
        assessment = calculate_daily_load(
            day([attraction("A", 90), attraction("B", 90)]), "relaxed",
            [RouteLegEvidence(state="verified", duration_minutes=20, source="fixture")],
        )
        self.assertEqual(assessment.overload_status, "within_target")
        self.assertEqual(assessment.breakdown.verified_travel_minutes, 20)

    def test_same_load_balanced_overload_but_intensive_not(self):
        value = day([attraction("A", 260), attraction("B", 260)], start="09:00")
        route = [RouteLegEvidence(state="verified", duration_minutes=30, source="fixture")]
        balanced = calculate_daily_load(value, "balanced", route)
        intensive = calculate_daily_load(value, "intensive", route)
        self.assertEqual(balanced.overload_status, "revisable_overload")
        self.assertNotEqual(intensive.overload_status, "revisable_overload")

    def test_unknown_route_is_estimated_and_not_infeasible(self):
        assessment = calculate_daily_load(
            day([attraction("A", 90), attraction("B", 90)]), "balanced",
            [RouteLegEvidence(state="unavailable", source="provider", reason="route_unavailable")],
        )
        self.assertEqual(assessment.breakdown.estimated_travel_minutes, 30)
        self.assertGreater(assessment.breakdown.uncertainty_buffer_minutes, 0)
        self.assertNotIn("infeasible", assessment.reasons)
        fallback = next(item for item in assessment.policy_assumptions if item["name"] == "route_fallback")
        self.assertEqual(fallback["source"], PACING_POLICY_VERSION)

    def test_inter_city_unknown_is_low_confidence_without_precise_total(self):
        assessment = calculate_daily_load(
            day([attraction("A", 90)], transfer=True), "balanced",
            [RouteLegEvidence(state="unknown", route_class="inter_city", reason="missing")],
        )
        self.assertEqual(assessment.confidence, "LOW")
        self.assertIsNone(assessment.breakdown.estimated_travel_minutes)
        self.assertIsNone(assessment.breakdown.effective_load_minutes)

    def test_nested_full_day_and_verified_same_complex_are_not_double_counted(self):
        full_day = normalize_attractions(day([
            attraction("Park", 480, category="theme park"),
            attraction("Ride", 90, category="ride"),
        ]))
        self.assertEqual((full_day.raw_minutes, full_day.effective_minutes), (570, 480))
        self.assertEqual(full_day.classification, "full_day_attraction")
        duplicate = normalize_attractions(day([
            attraction("Complex", 180, place_id="same"),
            attraction("Complex wing", 120, place_id="same"),
        ]))
        self.assertEqual(duplicate.effective_minutes, 180)

    def test_warning_and_revisable_are_distinct(self):
        warning = calculate_daily_load(
            day([attraction("A", 210), attraction("B", 210)]), "balanced", [],
        )
        overload = calculate_daily_load(
            day([attraction("A", 270), attraction("B", 270)]), "balanced", [],
        )
        self.assertEqual(warning.overload_status, "warning")
        self.assertEqual(overload.overload_status, "revisable_overload")

    async def test_validator_keeps_route_unavailable_and_emits_explainable_pacing(self):
        attractions = [attraction("A", 270, place_id="a"), attraction("B", 270, place_id="b")]
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=None):
            result = await TripValidatorService().validate(request(), _plan(day(attractions, start="09:00")))
        unavailable = next(risk for risk in result.risks if risk.type == "validation_unavailable")
        pacing = next(risk for risk in result.risks if risk.type == "pacing")
        self.assertIn("route", unavailable.id)
        self.assertEqual(pacing.evidence["overload_status"], "revisable_overload")
        self.assertEqual(pacing.evidence["breakdown"]["estimated_travel_minutes"], 30)
        self.assertTrue(pacing.evidence["revision_execution_supported"])
        self.assertIn("分钟景点活动", pacing.message)

    async def test_explicit_no_early_start_cannot_be_bypassed(self):
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=None):
            result = await TripValidatorService().validate(
                request(earliest="10:00"), _plan(day([attraction("A", 90)], start="08:00"))
            )
        risk = next(risk for risk in result.risks if risk.type == "earliest_start")
        self.assertEqual(risk.severity, "blocking")

    def test_planner_receives_compact_contract_and_versions_are_isolated(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        query = planner._build_planner_query(
            request("relaxed"), {"Tokyo": "places"}, {"Tokyo": "weather"}, {"Tokyo": "hotel"}
        )
        self.assertIn(PACING_POLICY_VERSION, query)
        self.assertIn("unknown_route_rule", query)
        self.assertEqual((PLANNER_VERSION, PLANNER_PROMPT_VERSION),
                         ("planner_pacing_v1", "planner_prompt_pacing_v1"))
        self.assertEqual((BASELINE_PLANNER_VERSION, BASELINE_PLANNER_PROMPT_VERSION),
                         ("planner_baseline_v1", "planner_prompt_v1"))

    def test_policy_source_has_no_known_case_city_or_poi_hardcode(self):
        from backend.app.services import pacing_policy
        source = inspect.getsource(pacing_policy)
        for token in ("深圳", "成都", "丽江", "Shenzhen", "Chengdu", "Lijiang"):
            self.assertNotIn(token, source)


def _plan(value):
    from backend.app.models.schemas import Budget, TripPlan
    return TripPlan(
        city="Tokyo", start_date="2026-10-01", end_date="2026-10-01", days=[value],
        overall_suggestions="test", budget=Budget(total=0),
    )
