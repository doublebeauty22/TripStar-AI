import unittest
from unittest.mock import patch

from backend.app.models.schemas import (
    Attraction,
    Budget,
    DayPlan,
    Location,
    PreferenceConstraints,
    PreferenceProfile,
    TripPlan,
    TripRequest,
)
from backend.app.services.trip_validator_service import TripValidatorService


def _attraction(name: str, place_id: str = "", *, verified: bool = True, visit_duration: int = 120):
    return Attraction(
        name=name,
        address=f"{name}, Tokyo",
        location=Location(longitude=139.7, latitude=35.6),
        visit_duration=visit_duration,
        description="test",
        place_id=place_id,
        poi_id=place_id,
        poi_match_status="verified" if verified else "partial_match",
        map_data_source="google_places" if verified else "llm_unverified",
    )


def _request(*, earliest="10:00", budget=5000, mobility=False):
    return TripRequest(
        city="东京",
        start_date="2026-10-01",
        end_date="2026-10-01",
        travel_days=1,
        transportation="公共交通",
        accommodation="舒适型酒店",
        preference_profile=PreferenceProfile(
            party_type="family",
            party_size=2,
            budget_cny=budget,
            pace="balanced",
            constraints=PreferenceConstraints(
                avoid_early_start=bool(earliest),
                earliest_start_time=earliest,
                mobility_notes=["妈妈膝盖不好"] if mobility else [],
            ),
        ),
    )


def _plan(*, start_time="10:00", attractions=None, total=4800):
    return TripPlan(
        city="东京",
        start_date="2026-10-01",
        end_date="2026-10-01",
        days=[DayPlan(
            date="2026-10-01",
            day_index=0,
            start_time=start_time,
            city="东京",
            description="test",
            transportation="公共交通",
            accommodation="舒适型酒店",
            attractions=attractions or [],
            meals=[],
        )],
        weather_info=[],
        overall_suggestions="test",
        budget=Budget(
            total_attractions=500,
            total_hotels=3000,
            total_meals=800,
            total_transportation=500,
            total_inter_city_transport=0,
            total=total,
        ),
    )


class _FakeGoogleService:
    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def plan_route(self, origin, destination, origin_city, destination_city, route_type):
        self.calls.append((origin, destination, route_type))
        value = self.routes.pop(0)
        if value is None:
            return {}
        distance, duration = value
        return {
            "distance": distance,
            "duration": duration,
            "distance_text": f"{distance}m",
            "duration_text": f"{duration}s",
            "route_type": route_type,
            "data_source": "google_directions",
        }


class TripValidatorServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_earliest_start_violation_is_blocking(self):
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=None):
            result = await TripValidatorService().validate(
                _request(earliest="10:00"), _plan(start_time="08:30")
            )

        risk = next(risk for risk in result.risks if risk.type == "earliest_start")
        self.assertEqual(risk.severity, "blocking")
        self.assertEqual(risk.evidence["constraint"], "10:00")

    async def test_risk_day_index_uses_array_position_for_legacy_one_based_plan(self):
        plan = _plan(start_time="08:30")
        plan.days[0].day_index = 1
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=None):
            result = await TripValidatorService().validate(_request(earliest="10:00"), plan)

        risk = next(risk for risk in result.risks if risk.type == "earliest_start")
        self.assertEqual(risk.day_index, 0)
        self.assertIn("第 1 天", risk.message)

    async def test_budget_limit_is_blocking_and_copy_calls_it_an_estimate(self):
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=None):
            result = await TripValidatorService().validate(
                _request(budget=4000), _plan(total=4800)
            )

        risk = next(risk for risk in result.risks if risk.id == "budget:over_limit")
        self.assertEqual(risk.severity, "blocking")
        self.assertIn("计划估算", risk.message)
        self.assertEqual(risk.evidence["over_by_cny"], 800)

    async def test_route_and_mobility_are_warnings_never_blocking(self):
        attractions = [_attraction("浅草寺", "a"), _attraction("东京晴空塔", "b")]
        google = _FakeGoogleService([
            (12000, 160 * 60),  # transit route: high route warning
            (2200, 35 * 60),    # walking route: mobility warning
        ])
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=google):
            result = await TripValidatorService().validate(
                _request(mobility=True), _plan(attractions=attractions)
            )

        relevant = [risk for risk in result.risks if risk.type in {"route_feasibility", "mobility"}]
        self.assertTrue(relevant)
        self.assertTrue(all(risk.severity == "warning" for risk in relevant))
        self.assertEqual(result.route_api_calls, 2)
        self.assertEqual(len(google.calls), 2)

    async def test_unverified_poi_skips_directions_and_degrades(self):
        attractions = [_attraction("浅草寺", "a"), _attraction("候选景点", "", verified=False)]
        google = _FakeGoogleService([])
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=google):
            result = await TripValidatorService().validate(
                _request(), _plan(attractions=attractions)
            )

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.route_api_calls, 0)
        self.assertEqual(google.calls, [])
        self.assertTrue(any(risk.type == "validation_unavailable" for risk in result.risks))

    async def test_invalid_coordinate_verified_label_skips_directions_and_degrades(self):
        first = _attraction("Synthetic One", "a")
        second = _attraction("Synthetic Two", "b")
        second.location = Location(longitude=0, latitude=0)
        google = _FakeGoogleService([])
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=google):
            result = await TripValidatorService().validate(
                _request(), _plan(attractions=[first, second])
            )
        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.route_api_calls, 0)
        self.assertEqual(google.calls, [])
        self.assertTrue(any(risk.type == "validation_unavailable" for risk in result.risks))

    async def test_verified_route_below_threshold_passes(self):
        attractions = [_attraction("浅草寺", "a"), _attraction("东京晴空塔", "b")]
        google = _FakeGoogleService([(3000, 30 * 60)])
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=google):
            result = await TripValidatorService().validate(
                _request(earliest=None, budget=None), _plan(attractions=attractions)
            )

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.route_api_calls, 1)
        self.assertFalse(result.risks)

    async def test_missing_route_result_is_info_and_degraded(self):
        attractions = [_attraction("浅草寺", "a"), _attraction("东京晴空塔", "b")]
        google = _FakeGoogleService([None])
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=google):
            result = await TripValidatorService().validate(
                _request(), _plan(attractions=attractions)
            )

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.route_api_calls, 1)
        unavailable = [risk for risk in result.risks if risk.type == "validation_unavailable"]
        self.assertEqual(len(unavailable), 1)
        self.assertEqual(unavailable[0].severity, "info")


if __name__ == "__main__":
    unittest.main()
