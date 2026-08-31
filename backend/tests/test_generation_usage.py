import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.api.routes.preferences import parse_preferences
from backend.app.models.schemas import PreferenceParseRequest, TripRequest
from backend.app.services.llm_service import (
    LLMCallBudgetExceeded,
    create_chat_completion,
    generation_llm_execution,
    get_generation_usage,
    get_or_create_generation_usage,
    llm_execution,
    release_generation_usage,
)


class _FakeCompletions:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        content, prompt, completion = self.contents.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
            ),
        )


class _FakeLlm:
    model = "mock-model"
    temperature = 0.7
    max_tokens = None

    def __init__(self, contents):
        self.completions = _FakeCompletions(contents)
        self._client = SimpleNamespace(
            chat=SimpleNamespace(completions=self.completions)
        )


PREFERENCE_JSON = (
    '{"avoid_early_start":true,"earliest_start_time":null,'
    '"mobility_notes":["reduce walking"],"food_notes":[],'
    '"other_notes":[],"inferred_interests":[],"parsing_notes":[]}'
)


class FullGenerationUsageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.generation_ids = []

    def tearDown(self):
        for generation_id in self.generation_ids:
            release_generation_usage(generation_id)

    def _generation_id(self, suffix):
        generation_id = f"test-generation-{suffix}"
        self.generation_ids.append(generation_id)
        return generation_id

    def _preference_request(self, generation_id, special="mom needs less walking"):
        return PreferenceParseRequest(
            party_type="with_parents",
            party_size=2,
            budget_cny=5000,
            pace="balanced",
            interests=["美食"],
            special_requirements=special,
            generation_id=generation_id,
        )

    def _planner_call(self, llm):
        return create_chat_completion(
            stage="planner",
            model=llm.model,
            messages=[{"role": "user", "content": "mock planner"}],
            llm_instance=llm,
        )

    async def test_preference_and_planner_share_generation_total_two(self):
        generation_id = self._generation_id("a")
        preference_llm = _FakeLlm([(PREFERENCE_JSON, 100, 20)])
        with patch(
            "backend.app.services.preference_service._get_llm",
            return_value=preference_llm,
        ):
            response = await parse_preferences(self._preference_request(generation_id))

        planner_llm = _FakeLlm([("{}", 200, 30)])
        with generation_llm_execution(generation_id, task_id="task-a"):
            self._planner_call(planner_llm)

        usage = get_generation_usage(generation_id)
        self.assertTrue(response.used_llm)
        self.assertEqual(response.generation_id, generation_id)
        self.assertEqual(usage.logical_llm_calls, 2)
        self.assertEqual(usage.stage_calls, {"preference": 1, "planner": 1})
        self.assertEqual(usage.prompt_tokens, 300)
        self.assertEqual(usage.completion_tokens, 50)
        self.assertEqual(usage.total_tokens, 350)

    async def test_empty_special_requirements_plus_planner_total_one(self):
        generation_id = self._generation_id("b")
        response = await parse_preferences(
            self._preference_request(generation_id, special="")
        )
        planner_llm = _FakeLlm([("{}", 10, 5)])
        with generation_llm_execution(generation_id, task_id="task-b"):
            self._planner_call(planner_llm)

        usage = get_generation_usage(generation_id)
        self.assertFalse(response.used_llm)
        self.assertEqual(usage.logical_llm_calls, 1)
        self.assertEqual(usage.stage_calls, {"planner": 1})

    async def test_preference_consumes_same_budget_used_by_planner(self):
        generation_id = self._generation_id("c")
        get_or_create_generation_usage(generation_id, max_calls=2)
        preference_llm = _FakeLlm([(PREFERENCE_JSON, 10, 5)])
        with patch(
            "backend.app.services.preference_service._get_llm",
            return_value=preference_llm,
        ):
            await parse_preferences(self._preference_request(generation_id))
        planner_llm = _FakeLlm([("{}", 10, 5)])
        with generation_llm_execution(generation_id, task_id="task-c", max_calls=2):
            self._planner_call(planner_llm)
        self.assertEqual(get_generation_usage(generation_id).logical_llm_calls, 2)

    async def test_planner_is_blocked_before_network_after_preference_uses_limit(self):
        generation_id = self._generation_id("d")
        get_or_create_generation_usage(generation_id, max_calls=1)
        preference_llm = _FakeLlm([(PREFERENCE_JSON, 10, 5)])
        with patch(
            "backend.app.services.preference_service._get_llm",
            return_value=preference_llm,
        ):
            await parse_preferences(self._preference_request(generation_id))

        planner_llm = _FakeLlm([("must-not-run", 10, 5)])
        with generation_llm_execution(generation_id, task_id="task-d", max_calls=1):
            with self.assertRaises(LLMCallBudgetExceeded):
                self._planner_call(planner_llm)
        self.assertEqual(planner_llm.completions.calls, 0)

    async def test_two_concurrent_generations_are_isolated(self):
        first = self._generation_id("e1")
        second = self._generation_id("e2")

        def run(generation_id, prompt_tokens):
            llm = _FakeLlm([("ok", prompt_tokens, 1)])
            with generation_llm_execution(generation_id, task_id=f"task-{generation_id}"):
                self._planner_call(llm)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(run, first, 11),
                executor.submit(run, second, 22),
            ]
            for future in futures:
                future.result()

        usage_first = get_generation_usage(first)
        usage_second = get_generation_usage(second)
        self.assertEqual(usage_first.logical_llm_calls, 1)
        self.assertEqual(usage_second.logical_llm_calls, 1)
        self.assertEqual(usage_first.prompt_tokens, 11)
        self.assertEqual(usage_second.prompt_tokens, 22)

    def test_legacy_requests_without_generation_id_remain_valid(self):
        preference = self._preference_request("legacy", special="").model_copy(
            update={"generation_id": None}
        )
        trip = TripRequest(
            city="东京",
            start_date="2026-09-01",
            end_date="2026-09-03",
            travel_days=3,
            transportation="公共交通",
            accommodation="酒店",
            preferences=["美食"],
        )
        self.assertIsNone(preference.generation_id)
        self.assertIsNone(trip.generation_id)

        llm = _FakeLlm([("{}", 7, 3)])
        with llm_execution("legacy-task") as usage:
            self._planner_call(llm)
        self.assertEqual(usage.logical_llm_calls, 1)


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
