import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.tests import assert_network_guard_active
from backend.app.api.routes import trip as trip_route
from backend.app.models.schemas import (
    TripPatch, TripPatchRequest, TripPlan, TripRequest, ValidationResult,
)


def make_profile(budget=5000):
    return {
        "party_type": "couple", "party_size": 2, "budget_cny": budget,
        "pace": "balanced", "interests": [],
        "special_requirements": "private preference text",
    }


def make_request_payload(profile):
    return {
        "city": "Sydney", "start_date": "2026-10-20", "end_date": "2026-10-20",
        "travel_days": 1, "transportation": "walking", "accommodation": "hotel",
        "free_text_input": "private request text", "preference_profile": profile,
    }


def make_plan_payload():
    return {
        "city": "Sydney", "start_date": "2026-10-20", "end_date": "2026-10-20",
        "days": [{
            "date": "2026-10-20", "day_index": 0, "start_time": "10:00",
            "city": "Sydney", "description": "Harbour", "transportation": "walking",
            "accommodation": "hotel", "attractions": [], "meals": [],
        }],
        "overall_suggestions": "keep", "budget": {"total_hotels": 1900, "total": 1900},
    }


class TripCostDisplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        assert_network_guard_active()
        self.saved_tasks = trip_route._tasks.copy()
        self.saved_fingerprints = trip_route._active_trip_fingerprints.copy()
        trip_route._tasks.clear()
        trip_route._active_trip_fingerprints.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = patch.object(trip_route, "_TASKS_DATA_DIR", Path(self.temp_dir.name))
        self.data_dir.start()

    def tearDown(self):
        self.data_dir.stop()
        self.temp_dir.cleanup()
        trip_route._tasks.clear()
        trip_route._tasks.update(self.saved_tasks)
        trip_route._active_trip_fingerprints.clear()
        trip_route._active_trip_fingerprints.update(self.saved_fingerprints)
        for task_id in ("task-a", "task-b", "old-task", "new-task", "patch-task"):
            trip_route._trip_patch_locks.pop(task_id, None)

    def add_task(self, task_id, profile, result=None):
        task = {
            **trip_route._create_task_state(task_id),
            "status": "completed", "stage": "completed",
            "request_payload": make_request_payload(profile),
            "result": result or {"success": True, "data": make_plan_payload()},
        }
        trip_route._tasks[task_id] = task
        return task

    async def test_status_uses_each_tasks_original_cap_instead_of_stored_or_model_metadata(self):
        forged_plan = make_plan_payload()
        forged_plan["cost_display"] = {"budget_cny": 88888}
        forged_result = {
            "success": True, "plan_id": "different-task", "data": forged_plan,
            "cost_display": {"budget_cny": 99999},
        }
        for task_id, cap in (("task-a", 5000), ("task-b", 8000)):
            self.add_task(task_id, make_profile(cap), copy.deepcopy(forged_result))
            response = await trip_route.get_task_status(task_id)
            self.assertEqual(response["result"]["plan_id"], task_id)
            self.assertEqual(response["result"].get("cost_display"), {"budget_cny": cap})
            self.assertEqual(response["result"]["data"]["budget"]["total"], 1900)
            self.assertNotIn("request_payload", response)
            self.assertNotIn("private", json.dumps(response))

    async def test_missing_legacy_budget_is_unknown_but_explicit_null_is_unset(self):
        profile = make_profile()
        profile.pop("budget_cny")
        self.add_task("old-task", profile)
        self.add_task("task-a", make_profile(None))
        old = await trip_route.get_task_status("old-task")
        unset = await trip_route.get_task_status("task-a")
        self.assertNotIn("cost_display", old["result"])
        self.assertEqual(unset["result"].get("cost_display"), {"budget_cny": None})

    async def test_invalid_original_profiles_or_budgets_cannot_supply_cost_metadata(self):
        invalid_profiles = [None, [], "invalid", {"budget_cny": 5000}]
        invalid_profiles += [make_profile(value) for value in (True, 0, -1, "5000", float("nan"), float("inf"))]
        for profile in invalid_profiles:
            with self.subTest(profile=profile):
                self.add_task("old-task", profile, {
                    "success": True, "data": make_plan_payload(),
                    "cost_display": {"budget_cny": 99999},
                })
                response = await trip_route.get_task_status("old-task")
                self.assertNotIn("cost_display", response["result"])

    async def test_absent_original_request_cannot_reuse_stored_metadata(self):
        task = self.add_task("old-task", make_profile())
        task["request_payload"] = None
        task["result"]["cost_display"] = {"budget_cny": 99999}
        response = await trip_route.get_task_status("old-task")
        self.assertNotIn("cost_display", response["result"])

    def test_completed_websocket_snapshot_backfills_safe_budget_metadata(self):
        task = self.add_task("old-task", make_profile(5000))
        event = trip_route._build_task_event("old-task", task)
        self.assertEqual(event["result"].get("cost_display"), {"budget_cny": 5000})
        self.assertEqual(event["result"]["plan_id"], "old-task")
        self.assertNotIn("request_payload", event)
        self.assertNotIn("private", json.dumps(event))

    async def test_persist_reload_preserves_cap_and_sanitizes_only_original_private_text(self):
        task = self.add_task("old-task", make_profile(5000))
        self.assertTrue(trip_route._persist_task_state("old-task", task))
        payload = json.loads((Path(self.temp_dir.name) / "old-task.json").read_text())
        self.assertEqual(payload["result"].get("cost_display"), {"budget_cny": 5000})
        self.assertEqual(payload["request_payload"]["preference_profile"]["budget_cny"], 5000)
        self.assertEqual(payload["request_payload"]["free_text_input"], "")
        self.assertEqual(payload["request_payload"]["preference_profile"]["special_requirements"], "")
        trip_route._tasks["old-task"] = trip_route._normalize_loaded_task("old-task", payload)
        response = await trip_route.get_task_status("old-task")
        self.assertEqual(response["result"].get("cost_display"), {"budget_cny": 5000})
        self.assertNotIn("request_payload", response)

    async def test_new_submission_preserves_absent_budget_as_unknown(self):
        profile = make_profile()
        profile.pop("budget_cny")
        request = TripRequest.model_validate(make_request_payload(profile))

        def do_not_start_provider_task(coro):
            coro.close()
            return object()

        with patch.object(trip_route.asyncio, "create_task", side_effect=do_not_start_provider_task):
            response = await trip_route.plan_trip(request)
        saved_profile = trip_route._tasks[response["task_id"]]["request_payload"]["preference_profile"]
        self.assertNotIn("budget_cny", saved_profile)

    async def test_new_generation_result_includes_request_budget_after_fake_planner(self):
        request = TripRequest.model_validate(make_request_payload(make_profile(5000)))
        trip_route._tasks["new-task"] = {
            **trip_route._create_task_state("new-task"),
            "request_payload": request.model_dump(mode="json"),
        }
        agent = SimpleNamespace(plan_trip=AsyncMock(return_value=TripPlan.model_validate(make_plan_payload())))
        with (
            patch.object(trip_route, "get_trip_planner_agent", return_value=agent),
            patch.object(trip_route, "build_knowledge_graph", return_value=None),
        ):
            await trip_route._run_trip_planning_impl("new-task", request)
        response = await trip_route.get_task_status("new-task")
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["result"].get("cost_display"), {"budget_cny": 5000})

    async def test_patch_status_and_persisted_result_keep_original_budget(self):
        self.add_task("patch-task", make_profile(5000))
        local_patch = TripPatch.model_validate({
            "intent": "start later", "affected_day_indices": [0], "protected_day_indices": [],
            "operations": [{
                "operation": "update_start_time", "day_index": 0,
                "old_value": "10:00", "new_value": "11:00", "user_instruction": "later",
            }],
        })
        interpreter = SimpleNamespace(interpret=AsyncMock(return_value=local_patch))
        validator = SimpleNamespace(validate=AsyncMock(return_value=ValidationResult(status="passed")))
        with (
            patch("backend.app.services.trip_patch_service.get_trip_patch_interpreter", return_value=interpreter),
            patch("backend.app.services.trip_validator_service.get_trip_validator_service", return_value=validator),
            patch.object(trip_route, "build_knowledge_graph", return_value=None),
        ):
            result = await trip_route.patch_trip("patch-task", TripPatchRequest(
                instruction="later", current_plan_version=1, patch_request_id="budget-patch-1",
            ))
        self.assertTrue(result.success)
        response = await trip_route.get_task_status("patch-task")
        self.assertEqual(response["result"].get("cost_display"), {"budget_cny": 5000})
        self.assertEqual(response["result"]["data"]["plan_version"], 2)
        self.assertNotIn("private", json.dumps(response))
        payload = json.loads((Path(self.temp_dir.name) / "patch-task.json").read_text())
        self.assertEqual(payload["result"].get("cost_display"), {"budget_cny": 5000})


if __name__ == "__main__":
    from backend.tests.network_guard import guarded_unittest_main

    guarded_unittest_main()
