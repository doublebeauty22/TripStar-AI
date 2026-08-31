import io
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

from backend.app.api.routes.poi import get_attraction_photo
from backend.app.api.routes import trip as trip_route
from backend.app.models.schemas import Location
from backend.app.services.timing import timed_stage


def _timing_lines(output: str, event: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(f"event={event} ")]


class TimingHelperTests(unittest.TestCase):
    def test_success_event_has_bounded_stage_and_non_negative_numeric_duration(self):
        output = io.StringIO()
        with redirect_stdout(output), timed_stage("trip_stage_timing", "planner"):
            pass

        lines = _timing_lines(output.getvalue(), "trip_stage_timing")
        self.assertEqual(len(lines), 1)
        self.assertIn("stage=planner", lines[0])
        self.assertIn("success=true", lines[0])
        match = re.search(r"duration_ms=(\d+)", lines[0])
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), 0)

    def test_failure_event_reraises_the_same_exception_without_logging_it(self):
        marker = RuntimeError("sensitive-provider-payload")
        output = io.StringIO()
        with self.assertRaises(RuntimeError) as raised:
            with redirect_stdout(output), timed_stage("trip_stage_timing", "weather"):
                raise marker

        self.assertIs(raised.exception, marker)
        line = _timing_lines(output.getvalue(), "trip_stage_timing")[0]
        self.assertIn("stage=weather", line)
        self.assertIn("success=false", line)
        self.assertNotIn("sensitive-provider-payload", line)

    def test_handled_failure_can_be_marked_without_changing_control_flow(self):
        output = io.StringIO()
        with redirect_stdout(output), timed_stage(
            "trip_stage_timing", "total_trip"
        ) as timing:
            timing.mark_failed()

        line = _timing_lines(output.getvalue(), "trip_stage_timing")[0]
        self.assertIn("stage=total_trip", line)
        self.assertIn("success=false", line)

    def test_unbounded_or_user_controlled_stage_is_rejected(self):
        with self.assertRaises(ValueError):
            with timed_stage("trip_stage_timing", "user-supplied-stage"):
                pass

    def test_all_required_main_stage_names_are_allow_listed(self):
        required = {
            "total_trip", "xhs_research", "weather", "hotel_search", "planner",
            "poi_enrichment", "validator", "revision", "knowledge_graph",
            "persistence",
        }
        output = io.StringIO()
        with redirect_stdout(output):
            for stage in required:
                with timed_stage("trip_stage_timing", stage):
                    pass
        emitted = {
            re.search(r"stage=([^ ]+)", line).group(1)
            for line in _timing_lines(output.getvalue(), "trip_stage_timing")
        }
        self.assertEqual(emitted, required)


class TotalTripTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_total_trip_reflects_completed_task_without_changing_result(self):
        task_id = "timing-success"
        trip_route._tasks[task_id] = {"status": "processing"}

        async def complete(_task_id, _request):
            trip_route._tasks[task_id]["status"] = "completed"

        output = io.StringIO()
        try:
            with redirect_stdout(output), patch.object(
                trip_route, "_run_trip_planning_impl", side_effect=complete
            ):
                result = await trip_route._run_trip_planning(task_id, object())
        finally:
            trip_route._tasks.pop(task_id, None)

        self.assertIsNone(result)
        line = _timing_lines(output.getvalue(), "trip_stage_timing")[0]
        self.assertIn("stage=total_trip", line)
        self.assertIn("success=true", line)

    async def test_total_trip_marks_handled_failed_task(self):
        task_id = "timing-failure"
        trip_route._tasks[task_id] = {"status": "processing"}

        async def fail(_task_id, _request):
            trip_route._tasks[task_id]["status"] = "failed"

        output = io.StringIO()
        try:
            with redirect_stdout(output), patch.object(
                trip_route, "_run_trip_planning_impl", side_effect=fail
            ):
                await trip_route._run_trip_planning(task_id, object())
        finally:
            trip_route._tasks.pop(task_id, None)

        line = _timing_lines(output.getvalue(), "trip_stage_timing")[0]
        self.assertIn("stage=total_trip", line)
        self.assertIn("success=false", line)


class _ImageService:
    def __init__(self, photo_url: str = ""):
        self.photo_url = photo_url

    def match_poi(self, *_args):
        poi = type("POI", (), {
            "id": "safe-place-id",
            "location": Location(longitude=12.5, latitude=45.5),
        })()
        return {"status": "verified", "poi": poi}

    def get_place_photo(self, **_kwargs):
        return {
            "photo_url": self.photo_url,
            "place_id": "safe-place-id",
            "attributions": [],
            "match_status": "verified",
            "reason": None if self.photo_url else "google_no_photo",
        }


class ImageTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_success_logs_only_executed_image_stages(self):
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=_ImageService("https://safe.invalid/photo"),
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(side_effect=AssertionError("XHS must not execute")),
        ):
            result = await get_attraction_photo("Private Attraction", "Private City")

        self.assertEqual(result["data"]["source"], "google_places")
        rendered = output.getvalue()
        timing = _timing_lines(rendered, "image_stage_timing")
        self.assertEqual(
            {re.search(r"stage=([^ ]+)", line).group(1) for line in timing},
            {"image_total", "google_grounding", "google_photo"},
        )
        self.assertNotIn("stage=xhs_image", rendered)
        self.assertNotIn("Private Attraction", "\n".join(timing))
        self.assertNotIn("Private City", "\n".join(timing))
        self.assertNotIn("https://safe.invalid/photo", "\n".join(timing))

    async def test_xhs_fallback_preserves_result_and_logs_xhs_timing(self):
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=_ImageService(),
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(return_value="https://safe.invalid/xhs-photo"),
        ):
            result = await get_attraction_photo("Private Attraction", "Private City")

        self.assertEqual(result["data"]["source"], "xhs")
        timing = _timing_lines(output.getvalue(), "image_stage_timing")
        stages = {re.search(r"stage=([^ ]+)", line).group(1) for line in timing}
        self.assertEqual(
            stages,
            {"image_total", "google_grounding", "google_photo", "xhs_image"},
        )
        self.assertTrue(all("success=true" in line for line in timing))


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
