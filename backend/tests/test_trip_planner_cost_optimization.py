import unittest
from types import SimpleNamespace

from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner
from backend.app.models.schemas import TripRequest


class _RecordingCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
            usage=None,
        )


class _FakeLlm:
    model = "mock-model"
    temperature = 0.7
    max_tokens = None

    def __init__(self):
        self.completions = _RecordingCompletions()
        self._client = SimpleNamespace(
            chat=SimpleNamespace(completions=self.completions)
        )


class _FakeGoogleService:
    def __init__(self):
        self.weather_cities = []
        self.hotel_cities = []

    def get_weather(self, city):
        self.weather_cities.append(city)
        return [{"city": city, "day_weather": "sunny"}]

    def search_poi(self, keywords, city):
        self.hotel_cities.append(city)
        return [{"name": f"{city} Hotel", "keywords": keywords}]


class TripPlannerCostOptimizationTests(unittest.IsolatedAsyncioTestCase):
    def _planner(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner.llm = _FakeLlm()
        planner.planner_agent_name = "planner"
        planner.map_provider = "google"
        planner._google_service = _FakeGoogleService()
        return planner

    def test_planner_agents_are_history_isolated_between_trips(self):
        planner = self._planner()
        trip_a = planner._new_planner_agent()
        trip_a.run("TRIP_A_PRIVATE_RESEARCH", max_tokens=6000)
        trip_b = planner._new_planner_agent()
        trip_b.run("TRIP_B_RESEARCH", max_tokens=6000)

        second_messages = planner.llm.completions.requests[1]["messages"]
        serialized = str(second_messages)
        self.assertIn("TRIP_B_RESEARCH", serialized)
        self.assertNotIn("TRIP_A_PRIVATE_RESEARCH", serialized)
        self.assertEqual(trip_a._history[-2].content, "TRIP_A_PRIVATE_RESEARCH")
        self.assertEqual(len(trip_b._history), 2)

    async def test_weather_and_hotel_are_deterministic_and_history_free(self):
        planner = self._planner()
        tokyo_weather = await planner._retrieve_weather_context("东京")
        bali_weather = await planner._retrieve_weather_context("巴厘岛")
        tokyo_hotels = await planner._retrieve_hotel_context("东京", "villa")
        bali_hotels = await planner._retrieve_hotel_context("巴厘岛", "villa")

        self.assertIn("东京", tokyo_weather)
        self.assertNotIn("东京", bali_weather)
        self.assertIn("东京", tokyo_hotels)
        self.assertNotIn("东京", bali_hotels)
        self.assertEqual(planner.llm.completions.requests, [])
        self.assertEqual(planner._google_service.weather_cities, ["东京", "巴厘岛"])
        self.assertEqual(planner._google_service.hotel_cities, ["东京", "巴厘岛"])

    async def test_planner_has_bounded_output_tokens(self):
        planner = self._planner()
        captured = {}

        class _Agent:
            def run(self, prompt, **kwargs):
                captured["prompt"] = prompt
                captured.update(kwargs)
                return "{}"

        planner._new_planner_agent = lambda: _Agent()
        request = TripRequest(
            city="东京",
            start_date="2026-09-01",
            end_date="2026-09-03",
            travel_days=3,
            transportation="public_transport",
            accommodation="hotel",
            preferences=["美食"],
        )
        await planner._run_planner_with_retry(
            request,
            {"东京": "research"},
            {"东京": "weather"},
            {"东京": "hotels"},
        )
        self.assertEqual(captured["max_tokens"], 6000)


if __name__ == "__main__":
    unittest.main()
