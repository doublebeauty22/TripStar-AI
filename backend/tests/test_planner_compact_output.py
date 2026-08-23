import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.app.agents.trip_planner_agent import (
    MultiAgentTripPlanner,
    PLANNER_AGENT_PROMPT,
)
from backend.app.models.schemas import (
    Attraction,
    Budget,
    DayPlan,
    Hotel,
    Location,
    Meal,
    TripPlan,
    TripRequest,
    ValidationResult,
    WeatherInfo,
    WeatherResult,
)


def _request() -> TripRequest:
    return TripRequest(
        city="测试城", start_date="2026-09-01", end_date="2026-09-01",
        travel_days=1, transportation="公共交通", accommodation="经济型酒店",
        preferences=["文化"],
    )


def _planner_plan() -> TripPlan:
    return TripPlan(
        city="测试城", cities=["测试城"],
        start_date="2026-09-01", end_date="2026-09-01",
        days=[DayPlan(
            date="2026-09-01", day_index=0, start_time="09:00", city="测试城",
            is_transfer_day=False, transfer_info="", description="文化主题一日游。",
            transportation="地铁+步行", accommodation="经济型酒店",
            hotel=Hotel(name="测试酒店", address="酒店地址", estimated_cost=300),
            attractions=[Attraction(
                name="测试景点", address="景点地址", category="博物馆",
                location=Location(longitude=0, latitude=0), visit_duration=120,
                description="了解当地历史与代表性文化。", ticket_price=50,
                reservation_required=True, reservation_tips="提前预约",
            )],
            meals=[
                Meal(type="breakfast", name="早餐", estimated_cost=20),
                Meal(type="lunch", name="午餐", estimated_cost=50),
                Meal(type="dinner", name="晚餐", estimated_cost=80),
            ],
        )],
        weather_info=[WeatherInfo(
            date="2026-09-01", city="测试城", day_weather="模型天气",
        )],
        overall_suggestions="提前预约并穿舒适鞋。",
        budget=Budget(
            total_attractions=50, total_hotels=300, total_meals=150,
            total_transportation=20, total_inter_city_transport=0, total=520,
        ),
    )


class PlannerCompactPromptTests(unittest.TestCase):
    def test_structural_contract_and_compact_prose_are_both_explicit(self):
        prompt = PLANNER_AGENT_PROMPT
        for field in (
            '"city"', '"cities"', '"start_date"', '"end_date"', '"days"',
            '"date"', '"day_index"', '"start_time"', '"is_transfer_day"',
            '"transfer_info"', '"transportation"', '"accommodation"',
            '"hotel"', '"attractions"', '"meals"', '"budget"',
            '"name"', '"address"', '"visit_duration"', '"category"',
            '"ticket_price"', '"reservation_required"', '"reservation_tips"',
            '"type"', '"estimated_cost"',
        ):
            self.assertIn(field, prompt)

        for compact_rule in (
            "约20-45个中文字符", "day.description", "meal.description",
            "transportation 只写简短方式", "accommodation 只写简短住宿类型",
            "非换乘日 transfer_info 输出空字符串", "最多三条简明建议",
        ):
            self.assertIn(compact_rule, prompt)

    def test_weather_and_provider_owned_fields_use_safe_contract(self):
        prompt = PLANNER_AGENT_PROMPT
        self.assertIn('"weather_info": []', prompt)
        self.assertIn('location 固定使用 {"longitude":0,"latitude":0}', prompt)
        for provider_owned in (
            "rating", "photos", "image_url", "poi_id", "place_id",
            "poi_match_status", "map_data_source",
        ):
            self.assertIn(provider_owned, prompt)

    def test_dynamic_query_preserves_grounding_budget_and_complete_json_inputs(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        query = planner._build_planner_query(
            _request(), {"测试城": "景点证据"}, {"测试城": "天气证据"},
            {"测试城": "酒店证据"},
        )
        for required in (
            "name、address、category、visit_duration、ticket_price",
            "预约字段必须保留", "estimated_cost", "实际游览顺序",
            "weather_info 固定输出 []", "完整 JSON 结构",
        ):
            self.assertIn(required, query)


class PlannerCompactDownstreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_weather_overwrites_planner_weather_and_poi_inputs_survive(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._search_attractions_with_xhs_fallback = AsyncMock(return_value="景点证据")
        planner._retrieve_hotel_context = AsyncMock(return_value="酒店证据")
        planner._run_planner_with_retry = AsyncMock(return_value="{}")
        planner._parse_response = lambda *_args: _planner_plan()

        provider_day = WeatherInfo(
            date="2026-09-01", city="测试城", day_weather="权威天气",
            data_source="google_weather", verification_status="verified",
        )

        async def weather(city):
            planner._weather_results[city] = WeatherResult(
                provider="google_weather", city=city, request_success=True,
                data_available=True, degraded=False, days=[provider_day],
            )
            return "天气证据"

        planner._retrieve_weather_context = AsyncMock(side_effect=weather)
        grounding_inputs = []

        async def enrich(plan):
            poi = plan.days[0].attractions[0]
            grounding_inputs.append((poi.name, poi.address, poi.category))
            return plan

        planner._enrich_trip_plan_pois = AsyncMock(side_effect=enrich)
        validator = SimpleNamespace(validate=AsyncMock(return_value=ValidationResult(
            status="passed", risks=[], checked_rules=[], unavailable_checks=[],
        )))

        with patch(
            "backend.app.services.trip_validator_service.get_trip_validator_service",
            return_value=validator,
        ):
            result = await planner.plan_trip(_request())

        self.assertEqual([item.day_weather for item in result.weather_info], ["权威天气"])
        self.assertEqual(result.weather_results[0].provider, "google_weather")
        self.assertEqual(grounding_inputs, [("测试景点", "景点地址", "博物馆")])
        self.assertEqual([meal.type for meal in result.days[0].meals], [
            "breakfast", "lunch", "dinner",
        ])
        self.assertEqual(result.budget.total, 520)

    async def test_unavailable_provider_keeps_final_weather_empty(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._search_attractions_with_xhs_fallback = AsyncMock(return_value="景点证据")
        planner._retrieve_hotel_context = AsyncMock(return_value="酒店证据")
        planner._run_planner_with_retry = AsyncMock(return_value="{}")
        planner._parse_response = lambda *_args: _planner_plan()
        planner._enrich_trip_plan_pois = AsyncMock(side_effect=lambda plan: plan)

        async def unavailable(city):
            planner._weather_results[city] = WeatherResult(
                provider="unavailable", city=city, request_success=False,
                data_available=False, degraded=True, reason="empty_forecast", days=[],
            )
            return "天气不可用"

        planner._retrieve_weather_context = AsyncMock(side_effect=unavailable)
        validator = SimpleNamespace(validate=AsyncMock(return_value=ValidationResult(
            status="passed", risks=[], checked_rules=[], unavailable_checks=["weather"],
        )))
        with patch(
            "backend.app.services.trip_validator_service.get_trip_validator_service",
            return_value=validator,
        ):
            result = await planner.plan_trip(_request())

        self.assertEqual(result.weather_info, [])
        self.assertFalse(result.weather_results[0].data_available)


if __name__ == "__main__":
    unittest.main()
