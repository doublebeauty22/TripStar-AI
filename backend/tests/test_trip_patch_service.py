import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from backend.app.api.routes import trip as trip_route
from backend.app.models.schemas import (
    AddPOIOperation, PatchMealInput, PatchPOIInput, RemovePOIOperation,
    ReplacePOIOperation, TripPatch, TripPatchRequest, TripPlan, TripPlanResponse,
    TripRequest, UpdateDayPaceOperation, UpdateMealOperation,
    UpdateStartTimeOperation, UpdateTransportOperation, ValidationResult,
)
from backend.app.services.llm_service import llm_execution
from backend.app.services.trip_patch_service import (
    TripPatchEngine, TripPatchInterpreter, compact_plan_for_patch,
)


def make_plan() -> TripPlan:
    days = []
    for index in range(3):
        days.append({
            "date": f"2026-09-0{index + 1}", "day_index": index, "start_time": "10:00",
            "city": "Tokyo", "description": f"day {index + 1}",
            "transportation": "walking", "accommodation": "hotel",
            "attractions": [
                {"name": f"POI {index}A", "address": "verified", "location": {"longitude": 1, "latitude": 1},
                 "visit_duration": 60, "description": "a", "ticket_price": 10,
                 "place_id": f"place-{index}-a", "poi_id": f"place-{index}-a",
                 "poi_match_status": "verified", "map_data_source": "google_places"},
                {"name": f"POI {index}B", "address": "verified", "location": {"longitude": 2, "latitude": 2},
                 "visit_duration": 60, "description": "b", "ticket_price": 20,
                 "place_id": f"place-{index}-b", "poi_id": f"place-{index}-b",
                 "poi_match_status": "verified", "map_data_source": "google_places"},
            ],
            "meals": [
                {"type": "dinner", "name": f"Dinner {index}", "estimated_cost": 100}
            ],
        })
    return TripPlan.model_validate({
        "city": "Tokyo", "cities": ["Tokyo"], "start_date": "2026-09-01",
        "end_date": "2026-09-03", "days": days, "weather_info": [],
        "overall_suggestions": "keep", "plan_version": 1,
        "budget": {"total_attractions": 90, "total_hotels": 300,
                   "total_meals": 300, "total_transportation": 60,
                   "total_inter_city_transport": 0, "total": 750},
    })


def make_request() -> TripRequest:
    return TripRequest(
        city="Tokyo", start_date="2026-09-01", end_date="2026-09-03",
        travel_days=3, transportation="walking", accommodation="hotel",
        preferences=["food"],
    )


def scope(operation, affected=1):
    return TripPatch(
        intent="local edit", operations=[operation], affected_day_indices=[affected],
        protected_day_indices=[index for index in range(3) if index != affected],
        summary="local edit",
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
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        )


class FakeLLM:
    model = "fake-model"
    temperature = 0
    max_tokens = 1000

    def __init__(self, outputs):
        self.completions = FakeCompletions(outputs)
        self._client = SimpleNamespace(chat=SimpleNamespace(completions=self.completions))


class TripPatchEngineTests(unittest.TestCase):
    def setUp(self):
        self.plan = make_plan()
        self.engine = TripPatchEngine()

    def test_update_start_time_changes_only_target_day(self):
        before = self.plan.model_copy(deep=True)
        patch_value = scope(UpdateStartTimeOperation(
            operation="update_start_time", day_index=1, old_value="10:00",
            new_value="11:00", user_instruction="later",
        ))
        updated, affected = self.engine.apply_patch(self.plan, patch_value)
        self.assertEqual(affected, [1])
        self.assertEqual(updated.days[1].start_time, "11:00")
        self.assertEqual(updated.days[0], before.days[0])
        self.assertEqual(updated.days[2], before.days[2])
        self.assertEqual(updated.days[1].attractions[0].place_id, "place-1-a")

    def test_remove_poi_and_deterministic_diff(self):
        patch_value = scope(RemovePOIOperation(
            operation="remove_poi", day_index=1, target_id="place-1-a",
            target_name="POI 1A", user_instruction="remove",
        ))
        updated, _ = self.engine.apply_patch(self.plan, patch_value)
        diff = self.engine.compare_before_after(self.plan, updated)
        self.assertEqual([poi.name for poi in updated.days[1].attractions], ["POI 1B"])
        self.assertEqual(diff.changed_day_indices, [1])
        self.assertIn("POI 1A", diff.removed_pois)
        self.assertEqual(diff.unchanged_day_indices, [0, 2])

    def test_replace_poi_has_no_fabricated_map_facts(self):
        patch_value = scope(ReplacePOIOperation(
            operation="replace_poi", day_index=1, target_id="place-1-a", target_name="POI 1A",
            new_poi=PatchPOIInput(name="New Park", visit_duration=45), user_instruction="replace",
        ))
        updated, _ = self.engine.apply_patch(self.plan, patch_value)
        replacement = updated.days[1].attractions[0]
        self.assertEqual(replacement.name, "New Park")
        self.assertEqual(replacement.place_id, "")
        self.assertEqual(replacement.poi_match_status, "unverified")
        self.assertEqual(updated.days[1].attractions[1].place_id, "place-1-b")

    def test_update_transport_meal_and_day_pace_are_local(self):
        operations = [
            UpdateTransportOperation(operation="update_transport", day_index=1,
                                     old_value="walking", new_value="taxi", user_instruction="taxi"),
            UpdateMealOperation(operation="update_meal", day_index=1, meal_type="dinner",
                                target_name="Dinner 1", new_meal=PatchMealInput(
                                    type="dinner", name="Noodles", estimated_cost=80),
                                user_instruction="no sushi"),
            UpdateDayPaceOperation(operation="update_day_pace", day_index=1,
                                   new_value="lighter", user_instruction="lighter"),
        ]
        patch_value = TripPatch(intent="local", operations=operations, affected_day_indices=[1],
                                protected_day_indices=[0, 2], summary="local")
        updated, _ = self.engine.apply_patch(self.plan, patch_value)
        self.assertEqual(updated.days[1].transportation, "taxi")
        self.assertEqual(updated.days[1].meals[0].name, "Noodles")
        self.assertEqual(len(updated.days[1].attractions), 1)
        self.assertEqual(updated.days[0], self.plan.days[0])

    def test_invalid_scope_or_stale_target_keeps_original(self):
        original_json = self.plan.model_dump_json()
        bad = TripPatch(
            intent="bad", operations=[RemovePOIOperation(
                operation="remove_poi", day_index=1, target_id="wrong",
                target_name="POI 1A", user_instruction="remove")],
            affected_day_indices=[1], protected_day_indices=[0, 2], summary="bad",
        )
        with self.assertRaises(ValueError):
            self.engine.apply_patch(self.plan, bad)
        self.assertEqual(self.plan.model_dump_json(), original_json)

    def test_strict_schema_rejects_protected_or_map_fields(self):
        raw = {
            "intent": "fake", "affected_day_indices": [1], "protected_day_indices": [0, 2],
            "summary": "fake", "operations": [{
                "operation": "add_poi", "day_index": 1, "user_instruction": "add",
                "new_poi": {"name": "Fake", "place_id": "fabricated"},
            }],
        }
        with self.assertRaises(ValidationError):
            TripPatch.model_validate(raw)
        raw["operations"][0]["new_poi"].pop("place_id")
        raw["start_date"] = "2030-01-01"
        with self.assertRaises(ValidationError):
            TripPatch.model_validate(raw)

    def test_hotel_and_broad_budget_operations_are_outside_patch_union(self):
        for operation in ("update_hotel", "update_budget"):
            with self.assertRaises(ValidationError):
                TripPatch.model_validate({
                    "intent": "broad change", "affected_day_indices": [1],
                    "protected_day_indices": [0, 2], "summary": "unsupported",
                    "operations": [{"operation": operation, "day_index": 1,
                                    "new_value": "replacement"}],
                })
        regeneration = TripPatch(
            intent="lower whole budget", operations=[], affected_day_indices=[],
            protected_day_indices=[], summary="regenerate", requires_regeneration=True,
            regeneration_reason="Broad budget replanning is not a safe local patch",
        )
        self.assertTrue(regeneration.requires_regeneration)

    def test_legacy_plan_defaults_version(self):
        raw = self.plan.model_dump(mode="json")
        raw.pop("plan_version")
        self.assertEqual(TripPlan.model_validate(raw).plan_version, 1)


class TripPatchInterpreterTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_request_is_one_bounded_llm_call(self):
        output = scope(UpdateStartTimeOperation(
            operation="update_start_time", day_index=1, old_value="10:00",
            new_value="11:00", user_instruction="later",
        )).model_dump_json()
        llm = FakeLLM([output])
        with llm_execution("patch-one", max_calls=1) as usage:
            result = await TripPatchInterpreter(llm).interpret("day two later", make_plan(), make_request())
            snapshot = usage.snapshot()
        self.assertEqual(result.operations[0].operation, "update_start_time")
        self.assertEqual(snapshot["logical_llm_calls"], 1)
        self.assertEqual(snapshot["stage_calls"], {"trip_patch": 1})
        self.assertEqual(len(llm.completions.requests), 1)
        prompt = str(llm.completions.requests[0]["messages"])
        self.assertNotIn("place-0-a", prompt.split('current_plan')[0])


class TripPatchRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.task_id = "patchtest"
        plan = make_plan()
        trip_route._tasks[self.task_id] = {
            **trip_route._create_task_state(self.task_id),
            "status": "completed", "stage": "completed", "plan_version": 1,
            "request_payload": make_request().model_dump(mode="json"),
            "result": TripPlanResponse(success=True, plan_id=self.task_id, data=plan),
        }
        trip_route._trip_patch_locks.pop(self.task_id, None)

    def tearDown(self):
        trip_route._tasks.pop(self.task_id, None)
        trip_route._trip_patch_locks.pop(self.task_id, None)

    async def _call(self, patch_value, request_id="patch-req-0001", enrich=None):
        interpreter = SimpleNamespace(interpret=AsyncMock(return_value=patch_value))
        validator = SimpleNamespace(validate=AsyncMock(return_value=ValidationResult(status="passed")))
        patches = [
            patch("backend.app.api.routes.trip.get_trip_patch_interpreter", create=True),
        ]
        with patch("backend.app.services.trip_patch_service.get_trip_patch_interpreter", return_value=interpreter), \
             patch("backend.app.services.trip_validator_service.get_trip_validator_service", return_value=validator), \
             patch("backend.app.api.routes.trip._persist_task_state"), \
             patch("backend.app.api.routes.trip.build_knowledge_graph", return_value=None):
            if enrich is None:
                result = await trip_route.patch_trip(self.task_id, TripPatchRequest(
                    instruction="edit", current_plan_version=1, patch_request_id=request_id))
            else:
                with patch("backend.app.api.routes.trip.get_trip_planner_agent", return_value=enrich):
                    result = await trip_route.patch_trip(self.task_id, TripPatchRequest(
                        instruction="edit", current_plan_version=1, patch_request_id=request_id))
        return result, interpreter, validator

    async def test_patch_runs_validator_and_never_phase2b(self):
        patch_value = scope(UpdateStartTimeOperation(
            operation="update_start_time", day_index=1, old_value="10:00",
            new_value="11:00", user_instruction="later"))
        result, interpreter, validator = await self._call(patch_value)
        self.assertTrue(result.success)
        self.assertEqual(result.updated_plan.plan_version, 2)
        validator.validate.assert_awaited_once()
        self.assertEqual(result.updated_plan.revision_count, 0)
        interpreter.interpret.assert_awaited_once()

    async def test_replace_reenriches_only_new_poi_and_preserves_other_identity(self):
        patch_value = scope(ReplacePOIOperation(
            operation="replace_poi", day_index=1, target_id="place-1-a", target_name="POI 1A",
            new_poi=PatchPOIInput(name="New Park"), user_instruction="replace"))

        class Enricher:
            def __init__(self): self.seen = []
            async def _enrich_trip_plan_pois(self, value):
                self.seen = [poi.name for day in value.days for poi in day.attractions]
                poi = value.days[0].attractions[0]
                poi.place_id = "grounded-new"
                poi.poi_id = "grounded-new"
                poi.poi_match_status = "verified"
                poi.map_data_source = "google_places"
                poi.location.longitude = 9
                poi.location.latitude = 9
                return value

        enricher = Enricher()
        result, _, _ = await self._call(patch_value, enrich=enricher)
        self.assertTrue(result.success)
        self.assertEqual(enricher.seen, ["New Park"])
        self.assertEqual(result.updated_plan.days[1].attractions[0].place_id, "grounded-new")
        self.assertEqual(result.updated_plan.days[1].attractions[1].place_id, "place-1-b")

    async def test_grounding_failure_rolls_back(self):
        patch_value = scope(AddPOIOperation(
            operation="add_poi", day_index=1, new_poi=PatchPOIInput(name="Unknown"),
            user_instruction="add"))

        class Enricher:
            async def _enrich_trip_plan_pois(self, value): return value

        original = trip_route._task_trip_plan(trip_route._tasks[self.task_id]).model_dump_json()
        result, _, validator = await self._call(patch_value, enrich=Enricher())
        self.assertFalse(result.success)
        validator.validate.assert_not_awaited()
        self.assertEqual(trip_route._task_trip_plan(trip_route._tasks[self.task_id]).model_dump_json(), original)

    async def test_requires_regeneration_does_not_modify_or_validate(self):
        patch_value = TripPatch(
            intent="change city", operations=[], affected_day_indices=[], protected_day_indices=[],
            summary="large", requires_regeneration=True,
            regeneration_reason="This affects the whole trip structure",
        )
        original = trip_route._task_trip_plan(trip_route._tasks[self.task_id]).model_dump_json()
        result, _, validator = await self._call(patch_value)
        self.assertTrue(result.requires_regeneration)
        self.assertFalse(result.success)
        validator.validate.assert_not_awaited()
        self.assertEqual(trip_route._task_trip_plan(trip_route._tasks[self.task_id]).model_dump_json(), original)

    async def test_version_conflict_blocks_before_llm(self):
        interpreter = SimpleNamespace(interpret=AsyncMock())
        with patch("backend.app.services.trip_patch_service.get_trip_patch_interpreter", return_value=interpreter):
            with self.assertRaises(HTTPException) as caught:
                await trip_route.patch_trip(self.task_id, TripPatchRequest(
                    instruction="edit", current_plan_version=9, patch_request_id="patch-conflict"))
        self.assertEqual(caught.exception.status_code, 409)
        interpreter.interpret.assert_not_awaited()

    async def test_duplicate_request_is_idempotent_without_second_llm_call(self):
        patch_value = scope(UpdateStartTimeOperation(
            operation="update_start_time", day_index=1, old_value="10:00",
            new_value="11:00", user_instruction="later"))
        interpreter = SimpleNamespace(interpret=AsyncMock(return_value=patch_value))
        validator = SimpleNamespace(validate=AsyncMock(return_value=ValidationResult(status="passed")))
        req = TripPatchRequest(instruction="edit", current_plan_version=1,
                               patch_request_id="patch-dedupe-1")
        with patch("backend.app.services.trip_patch_service.get_trip_patch_interpreter", return_value=interpreter), \
             patch("backend.app.services.trip_validator_service.get_trip_validator_service", return_value=validator), \
             patch("backend.app.api.routes.trip._persist_task_state"), \
             patch("backend.app.api.routes.trip.build_knowledge_graph", return_value=None):
            first = await trip_route.patch_trip(self.task_id, req)
            second = await trip_route.patch_trip(self.task_id, req)
        self.assertEqual(first.model_dump(), second.model_dump())
        interpreter.interpret.assert_awaited_once()
        validator.validate.assert_awaited_once()

    async def test_timeout_keeps_original(self):
        original = trip_route._task_trip_plan(trip_route._tasks[self.task_id]).model_dump_json()
        interpreter = SimpleNamespace(interpret=AsyncMock(side_effect=TimeoutError("timeout")))
        with patch("backend.app.services.trip_patch_service.get_trip_patch_interpreter", return_value=interpreter), \
             patch("backend.app.api.routes.trip._persist_task_state"):
            result = await trip_route.patch_trip(self.task_id, TripPatchRequest(
                instruction="edit", current_plan_version=1, patch_request_id="patch-timeout"))
        self.assertFalse(result.success)
        self.assertEqual(trip_route._task_trip_plan(trip_route._tasks[self.task_id]).model_dump_json(), original)

    async def test_validator_exception_keeps_original(self):
        original = trip_route._task_trip_plan(trip_route._tasks[self.task_id]).model_dump_json()
        patch_value = scope(UpdateStartTimeOperation(
            operation="update_start_time", day_index=1, old_value="10:00",
            new_value="11:00", user_instruction="later"))
        interpreter = SimpleNamespace(interpret=AsyncMock(return_value=patch_value))
        validator = SimpleNamespace(validate=AsyncMock(side_effect=RuntimeError("validator down")))
        with patch("backend.app.services.trip_patch_service.get_trip_patch_interpreter", return_value=interpreter), \
             patch("backend.app.services.trip_validator_service.get_trip_validator_service", return_value=validator), \
             patch("backend.app.api.routes.trip._persist_task_state", return_value=True):
            result = await trip_route.patch_trip(self.task_id, TripPatchRequest(
                instruction="edit", current_plan_version=1, patch_request_id="patch-validator"))
        self.assertFalse(result.success)
        self.assertEqual(trip_route._task_trip_plan(trip_route._tasks[self.task_id]).model_dump_json(), original)

    async def test_persistence_failure_rolls_back_memory_commit(self):
        original = trip_route._task_trip_plan(trip_route._tasks[self.task_id]).model_dump_json()
        patch_value = scope(UpdateStartTimeOperation(
            operation="update_start_time", day_index=1, old_value="10:00",
            new_value="11:00", user_instruction="later"))
        interpreter = SimpleNamespace(interpret=AsyncMock(return_value=patch_value))
        validator = SimpleNamespace(validate=AsyncMock(return_value=ValidationResult(status="passed")))
        with patch("backend.app.services.trip_patch_service.get_trip_patch_interpreter", return_value=interpreter), \
             patch("backend.app.services.trip_validator_service.get_trip_validator_service", return_value=validator), \
             patch("backend.app.api.routes.trip._persist_task_state", return_value=False), \
             patch("backend.app.api.routes.trip.build_knowledge_graph", return_value=None):
            result = await trip_route.patch_trip(self.task_id, TripPatchRequest(
                instruction="edit", current_plan_version=1, patch_request_id="patch-persist"))
        self.assertFalse(result.success)
        self.assertEqual(trip_route._tasks[self.task_id]["plan_version"], 1)
        self.assertEqual(trip_route._task_trip_plan(trip_route._tasks[self.task_id]).model_dump_json(), original)

    async def test_concurrent_requests_cannot_lost_update(self):
        patch_value = scope(UpdateStartTimeOperation(
            operation="update_start_time", day_index=1, old_value="10:00",
            new_value="11:00", user_instruction="later"))
        interpreter = SimpleNamespace(interpret=AsyncMock(return_value=patch_value))
        validator = SimpleNamespace(validate=AsyncMock(return_value=ValidationResult(status="passed")))
        req1 = TripPatchRequest(instruction="one", current_plan_version=1, patch_request_id="patch-concurrent-1")
        req2 = TripPatchRequest(instruction="two", current_plan_version=1, patch_request_id="patch-concurrent-2")
        with patch("backend.app.services.trip_patch_service.get_trip_patch_interpreter", return_value=interpreter), \
             patch("backend.app.services.trip_validator_service.get_trip_validator_service", return_value=validator), \
             patch("backend.app.api.routes.trip._persist_task_state"), \
             patch("backend.app.api.routes.trip.build_knowledge_graph", return_value=None):
            results = await asyncio.gather(
                trip_route.patch_trip(self.task_id, req1),
                trip_route.patch_trip(self.task_id, req2),
                return_exceptions=True,
            )
        self.assertTrue(any(getattr(item, "success", False) for item in results))
        conflicts = [item for item in results if isinstance(item, HTTPException)]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].status_code, 409)
        interpreter.interpret.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
