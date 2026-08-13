import unittest
from unittest.mock import AsyncMock, patch

from backend.app.api.routes import trip as trip_route
from backend.app.models.schemas import TripRequest


class TripSubmissionDedupeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved_tasks = trip_route._tasks.copy()
        self.saved_fingerprints = trip_route._active_trip_fingerprints.copy()
        trip_route._tasks.clear()
        trip_route._active_trip_fingerprints.clear()
        self.request = TripRequest(
            city="东京",
            start_date="2026-09-01",
            end_date="2026-09-03",
            travel_days=3,
            transportation="public_transport",
            accommodation="hotel",
            preferences=["美食"],
        )

    def tearDown(self):
        trip_route._tasks.clear()
        trip_route._tasks.update(self.saved_tasks)
        trip_route._active_trip_fingerprints.clear()
        trip_route._active_trip_fingerprints.update(self.saved_fingerprints)

    async def test_identical_active_requests_return_same_task_and_start_once(self):
        scheduled = []

        def fake_create_task(coro):
            scheduled.append(coro)
            coro.close()
            return object()

        with (
            patch.object(trip_route, "_persist_task_state"),
            patch.object(trip_route, "_update_task_state", new=AsyncMock()),
            patch.object(trip_route.asyncio, "create_task", side_effect=fake_create_task),
        ):
            first = await trip_route.plan_trip(self.request)
            second = await trip_route.plan_trip(self.request)

        self.assertEqual(first["task_id"], second["task_id"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(len(scheduled), 1)

    def test_task_persistence_payload_removes_free_form_preference_text(self):
        payload = {
            "city": "东京",
            "free_text_input": "private free-form note",
            "preference_profile": {
                "special_requirements": "private special requirement",
                "interests": ["美食"],
            },
        }
        sanitized = trip_route._sanitize_request_payload_for_persistence(payload)

        self.assertEqual(sanitized["free_text_input"], "")
        self.assertEqual(sanitized["preference_profile"]["special_requirements"], "")
        self.assertEqual(sanitized["preference_profile"]["interests"], ["美食"])
        self.assertEqual(payload["free_text_input"], "private free-form note")

    async def test_failed_task_allows_new_submission(self):
        def fake_create_task(coro):
            coro.close()
            return object()

        with (
            patch.object(trip_route, "_persist_task_state"),
            patch.object(trip_route, "_update_task_state", new=AsyncMock()),
            patch.object(trip_route.asyncio, "create_task", side_effect=fake_create_task),
        ):
            first = await trip_route.plan_trip(self.request)
            trip_route._tasks[first["task_id"]]["status"] = "failed"
            second = await trip_route.plan_trip(self.request)

        self.assertNotEqual(first["task_id"], second["task_id"])

    async def test_different_generation_ids_dedupe_without_orphaning_attribution(self):
        scheduled = []

        def fake_create_task(coro):
            scheduled.append(coro)
            coro.close()
            return object()

        first_request = self.request.model_copy(update={"generation_id": "generation-a"})
        second_request = self.request.model_copy(update={"generation_id": "generation-b"})
        with (
            patch.object(trip_route, "_persist_task_state"),
            patch.object(trip_route, "_update_task_state", new=AsyncMock()),
            patch.object(trip_route, "get_or_create_generation_usage"),
            patch.object(trip_route.asyncio, "create_task", side_effect=fake_create_task),
        ):
            first = await trip_route.plan_trip(first_request)
            second = await trip_route.plan_trip(second_request)

        task = trip_route._tasks[first["task_id"]]
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(task["generation_id"], "generation-a")
        self.assertEqual(task["deduplicated_generation_ids"], ["generation-b"])
        self.assertEqual(len(scheduled), 1)

    def test_legacy_completed_task_safely_normalizes_plan_version_one(self):
        legacy_plan = {
            "city": "东京", "cities": ["东京"], "start_date": "2026-08-20",
            "end_date": "2026-08-22", "days": [], "weather_info": [],
            "overall_suggestions": "keep", "revision_count": 1,
        }
        payload = {
            "task_id": "legacy", "status": "completed", "stage": "completed",
            "result": {"success": True, "data": dict(legacy_plan)},
            "patch_history": [], "patch_requests": {},
        }
        task = trip_route._normalize_loaded_task("legacy", payload)
        self.assertEqual(task["plan_version"], 1)
        self.assertEqual(task["result"]["data"]["plan_version"], 1)
        normalized_plan = dict(task["result"]["data"])
        normalized_plan.pop("plan_version")
        self.assertEqual(normalized_plan, legacy_plan)

    def test_existing_plan_version_three_is_never_reset(self):
        payload = {
            "task_id": "new", "status": "completed", "stage": "completed",
            "plan_version": 3,
            "result": {"success": True, "data": {"city": "东京", "plan_version": 3}},
            "patch_history": [{"plan_version": 2}, {"plan_version": 3}],
            "patch_requests": {},
        }
        task = trip_route._normalize_loaded_task("new", payload)
        self.assertEqual(task["plan_version"], 3)
        self.assertEqual(task["result"]["data"]["plan_version"], 3)

    def test_ambiguous_existing_patch_metadata_is_not_reset_to_one(self):
        payload = {
            "task_id": "ambiguous", "status": "completed", "stage": "completed",
            "result": {"success": True, "data": {"city": "东京"}},
            "patch_history": [{"operation_types": ["update_start_time"]}],
            "patch_requests": {},
        }
        task = trip_route._normalize_loaded_task("ambiguous", payload)
        self.assertIsNone(task["plan_version"])
        self.assertNotIn("plan_version", task["result"]["data"])

    async def test_status_persists_and_returns_canonical_legacy_version(self):
        payload = {
            "task_id": "legacy-status", "status": "completed", "stage": "completed",
            "result": {"success": True, "data": {"city": "东京", "days": []}},
            "patch_history": [], "patch_requests": {},
        }
        trip_route._tasks["legacy-status"] = trip_route._normalize_loaded_task(
            "legacy-status", payload
        )

        with patch.object(trip_route, "_persist_task_state", return_value=True) as persist:
            response = await trip_route.get_task_status("legacy-status")

        self.assertEqual(response["result"]["data"]["plan_version"], 1)
        persist.assert_called_once()


if __name__ == "__main__":
    unittest.main()
