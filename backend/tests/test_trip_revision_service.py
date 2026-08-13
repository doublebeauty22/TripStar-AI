import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner
from backend.app.models.schemas import (
    CriticResult, PreferenceConstraints, PreferenceProfile, RiskItem,
    TripPlan, TripRequest, ValidationResult,
)
from backend.app.services.llm_service import llm_execution
from backend.app.services.trip_revision_service import (
    TripRevisionService, filter_actionable_risks,
)


def plan(name="Museum", start="09:00"):
    return TripPlan.model_validate({
        "city": "Tokyo", "cities": ["Tokyo"],
        "start_date": "2026-09-01", "end_date": "2026-09-01",
        "days": [{
            "date": "2026-09-01", "day_index": 0, "start_time": start,
            "city": "Tokyo", "description": "day", "transportation": "transit",
            "accommodation": "hotel", "attractions": [{
                "name": name, "address": "", "location": {"longitude": 0, "latitude": 0},
                "visit_duration": 60, "description": "visit", "ticket_price": 10,
            }], "meals": [],
        }],
        "weather_info": [], "overall_suggestions": "keep",
        "budget": {"total_attractions": 10, "total_hotels": 0, "total_meals": 0,
                   "total_transportation": 0, "total_inter_city_transport": 0, "total": 10},
    })


def request():
    return TripRequest(
        city="Tokyo", start_date="2026-09-01", end_date="2026-09-01",
        travel_days=1, transportation="transit", accommodation="hotel",
        preferences=["food"], preference_profile=PreferenceProfile(
            party_type="couple", party_size=2, budget_cny=5000,
            interests=["food"], constraints=PreferenceConstraints(
                avoid_early_start=True, earliest_start_time="10:30"
            ),
        ),
    )


def risk(kind="earliest_start", severity="blocking", revisable=True, suffix="1"):
    return RiskItem(
        id=f"{kind}:{suffix}", type=kind, severity=severity, title="risk", message="fact",
        evidence={"planned": "09:00", "threshold": "10:30"}, revisable=revisable,
    )


class FakeCompletions:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=output))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class FakeLLM:
    model = "fake-model"
    temperature = 0
    max_tokens = 6000

    def __init__(self, outputs):
        self.completions = FakeCompletions(outputs)
        self._client = SimpleNamespace(chat=SimpleNamespace(completions=self.completions))


class TripRevisionUnitTests(unittest.IsolatedAsyncioTestCase):
    def test_a_b_d_e_f_deterministic_actionable_filter(self):
        self.assertEqual(filter_actionable_risks([]), [])
        for item in [risk("validation_unavailable", "info", False), risk("budget", "info"), risk("mobility", "warning", False)]:
            self.assertEqual(filter_actionable_risks([item]), [])
        for kind, severity in [("earliest_start", "blocking"), ("budget", "blocking"),
                               ("mobility", "warning"), ("route_feasibility", "warning")]:
            self.assertEqual(len(filter_actionable_risks([risk(kind, severity)])), 1)

    async def test_g_critic_can_decline_revision(self):
        llm = FakeLLM([json.dumps({"should_revise": False, "revision_instructions": [],
                                  "protected_elements": [], "summary": "tradeoff"})])
        result = await TripRevisionService(llm).run_critic(request(), plan(), [risk()])
        self.assertFalse(result.should_revise)
        self.assertEqual(len(llm.completions.requests), 1)

    async def test_h_critic_exception_is_available_to_fail_open_caller(self):
        llm = FakeLLM([TimeoutError("timeout"), TimeoutError("timeout")])
        with self.assertRaises(TimeoutError):
            await TripRevisionService(llm).run_critic(request(), plan(), [risk()])

    async def test_i_critic_invalid_json_is_rejected(self):
        with self.assertRaises(ValueError):
            await TripRevisionService(FakeLLM(["not json"])).run_critic(request(), plan(), [risk()])

    async def test_k_revision_invalid_json_is_rejected_without_increment(self):
        original = plan()
        with self.assertRaises(ValueError):
            await TripRevisionService(FakeLLM(["bad"])).run_revision(
                request(), original, [risk()],
                CriticResult(should_revise=True, revision_instructions=["fix"], protected_elements=[], summary="fix"), {}
            )
        self.assertEqual(original.revision_count, 0)

    async def test_l_revision_timeout_does_not_mutate_original(self):
        original = plan()
        with self.assertRaises(TimeoutError):
            await TripRevisionService(FakeLLM([TimeoutError("timeout"), TimeoutError("timeout")])).run_revision(
                request(), original, [risk()],
                CriticResult(should_revise=True, revision_instructions=["fix"], protected_elements=[], summary="fix"), {}
            )
        self.assertEqual(original.revision_count, 0)

    async def test_o_budget_blocks_before_network_request(self):
        llm = FakeLLM(["{}"])
        with llm_execution("budget-test", max_calls=0):
            with self.assertRaises(Exception):
                await TripRevisionService(llm).run_critic(request(), plan(), [risk()])
        self.assertEqual(llm.completions.requests, [])

    async def test_p_usage_attributes_critic_revision_tokens_and_calls(self):
        critic_json = json.dumps({"should_revise": True, "revision_instructions": ["start later"],
                                  "protected_elements": [], "summary": "start later"})
        revised_json = plan(start="10:30").model_dump_json(exclude={"risks", "validation_status"})
        service = TripRevisionService(FakeLLM([critic_json, revised_json]))
        with llm_execution("usage", max_calls=5) as usage:
            critic = await service.run_critic(request(), plan(), [risk()])
            revised = await service.run_revision(request(), plan(), [risk()], critic, {})
            snapshot = usage.snapshot()
        self.assertEqual(snapshot["stage_calls"], {"critic": 1, "revision": 1})
        self.assertEqual(snapshot["logical_llm_calls"], 2)
        self.assertEqual(snapshot["total_tokens"], 30)
        self.assertEqual(snapshot["retry_count"], 0)
        self.assertEqual(revised.revision_count, 1)

    def test_q_legacy_trip_plan_defaults_revision_count(self):
        payload = plan().model_dump(mode="json")
        payload.pop("revision_count")
        payload.pop("revision_summary")
        self.assertEqual(TripPlan.model_validate(payload).revision_count, 0)


class TripRevisionFlowTests(unittest.IsolatedAsyncioTestCase):
    async def _run_flow(self, first_risks, critic_should_revise=True):
        original = plan("Old POI")
        revised = plan("New POI", "10:30")
        revised.revision_count = 1
        revised.revision_summary = "targeted fix"
        revision = SimpleNamespace(
            run_critic=AsyncMock(return_value=CriticResult(
                should_revise=critic_should_revise, revision_instructions=["fix"],
                protected_elements=["cities"], summary="targeted fix"
            )),
            run_revision=AsyncMock(return_value=revised),
        )
        second_risk = risk("budget", "blocking", suffix="second")
        validator = SimpleNamespace(validate=AsyncMock(side_effect=[
            ValidationResult(status="issues_found", risks=first_risks),
            ValidationResult(status="issues_found", risks=[second_risk]),
        ]))
        enriched_names = []

        async def enrich(value):
            enriched_names.append(value.days[0].attractions[0].name)
            value.days[0].attractions[0].place_id = f"verified-{enriched_names[-1]}"
            value.days[0].attractions[0].poi_match_status = "verified"
            return value

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._search_attractions_with_xhs_fallback = AsyncMock(return_value="research")
        planner._retrieve_weather_context = AsyncMock(return_value="weather")
        planner._retrieve_hotel_context = AsyncMock(return_value="hotel")
        planner._run_planner_with_retry = AsyncMock(return_value="{}")
        planner._parse_response = lambda *_: original
        planner._enrich_trip_plan_pois = AsyncMock(side_effect=enrich)

        with patch("backend.app.services.trip_validator_service.get_trip_validator_service", return_value=validator), \
             patch("backend.app.services.trip_revision_service.get_trip_revision_service", return_value=revision):
            result = await planner.plan_trip(request())
        return result, revision, validator, planner, enriched_names

    async def test_a_no_actionable_risk_skips_critic_and_revision(self):
        result, revision, validator, planner, names = await self._run_flow([])
        revision.run_critic.assert_not_awaited()
        revision.run_revision.assert_not_awaited()
        self.assertEqual(result.revision_count, 0)
        self.assertEqual(validator.validate.await_count, 1)

    async def test_g_declined_critic_returns_original_and_original_risks(self):
        first = risk()
        result, revision, validator, planner, names = await self._run_flow([first], False)
        revision.run_revision.assert_not_awaited()
        self.assertEqual(result.days[0].attractions[0].name, "Old POI")
        self.assertEqual(result.risks[0].id, first.id)
        self.assertEqual(result.revision_count, 0)

    async def test_j_m_n_success_reenriches_revalidates_uses_final_risks_and_stops(self):
        result, revision, validator, planner, names = await self._run_flow([risk()])
        revision.run_critic.assert_awaited_once()
        revision.run_revision.assert_awaited_once()
        self.assertEqual(planner._enrich_trip_plan_pois.await_count, 2)
        self.assertEqual(validator.validate.await_count, 2)
        self.assertEqual(names, ["Old POI", "New POI"])
        self.assertEqual(result.days[0].attractions[0].place_id, "verified-New POI")
        self.assertEqual(result.risks[0].id, "budget:second")
        self.assertEqual(result.revision_count, 1)


if __name__ == "__main__":
    unittest.main()
