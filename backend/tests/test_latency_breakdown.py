import io
import re
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner
from backend.app.services import xhs_service
from backend.app.services.timing import timed_stage


def _lines(output: str, event: str, stage: str) -> list[str]:
    return [
        line for line in output.splitlines()
        if line.startswith(f"event={event} ") and f"stage={stage} " in line
    ]


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class XHSBreakdownTimingTests(unittest.TestCase):
    def test_native_search_and_detail_emit_bounded_component_timers(self):
        search_response = _Response({"success": True, "data": {"items": []}})
        detail_response = _Response({"success": True, "data": {"items": []}})
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            xhs_service, "_sign_request", return_value=({}, {}, "{}")
        ), patch.object(
            xhs_service.requests, "post", side_effect=[search_response, detail_response]
        ) as post:
            client = xhs_service.XhsNativeClient("private-cookie")
            self.assertEqual(client.search_notes("private-query"), search_response.payload)
            self.assertEqual(client.get_note_detail("private-note"), detail_response.payload)

        for stage in (
            "research_search_sign_wait", "research_search_sign",
            "research_search_http", "research_search_parse",
            "research_detail_sign_wait", "research_detail_sign",
            "research_detail_http", "research_detail_parse",
        ):
            line = _lines(output.getvalue(), "xhs_stage_timing", stage)
            self.assertEqual(len(line), 1)
            self.assertRegex(line[0], r"duration_ms=\d+ success=true$")
        self.assertEqual(post.call_count, 2)
        timing = "\n".join(
            line for line in output.getvalue().splitlines()
            if line.startswith("event=xhs_stage_timing ")
        )
        for private in ("private-cookie", "private-query", "private-note"):
            self.assertNotIn(private, timing)

    def test_http_failure_marks_only_http_timer_failed_and_reraises(self):
        marker = RuntimeError("private-provider-body")
        output = io.StringIO()
        with self.assertRaises(RuntimeError) as raised:
            with redirect_stdout(output), patch.object(
                xhs_service, "_sign_request", return_value=({}, {}, "{}")
            ), patch.object(xhs_service.requests, "post", side_effect=marker):
                xhs_service.XhsNativeClient("private-cookie").search_notes("private-query")

        self.assertIs(raised.exception, marker)
        self.assertIn(
            "success=false",
            _lines(output.getvalue(), "xhs_stage_timing", "research_search_http")[0],
        )
        self.assertFalse(_lines(output.getvalue(), "xhs_stage_timing", "research_search_parse"))
        self.assertNotIn("private-provider-body", "\n".join(
            line for line in output.getvalue().splitlines()
            if line.startswith("event=xhs_stage_timing ")
        ))

    def test_detail_batch_timer_measures_concurrent_wall_clock(self):
        notes = [
            (index, {"model_type": "note", "id": str(index), "note_card": {}})
            for index in range(2)
        ]

        def delayed(_client, indexed_note):
            time.sleep(0.04)
            return None, "", ""

        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            xhs_service, "get_xhs_client",
            return_value=SimpleNamespace(search_notes=lambda **_kwargs: {
                "data": {"items": [note for _, note in notes]}
            }),
        ), patch.object(xhs_service, "_research_note", side_effect=delayed):
            result = xhs_service.search_xhs_attractions("private-city", "private-query")

        self.assertEqual(result.reason, "empty_search")
        line = _lines(output.getvalue(), "xhs_stage_timing", "research_detail_batch")[0]
        duration = int(re.search(r"duration_ms=(\d+)", line).group(1))
        self.assertGreaterEqual(duration, 30)
        self.assertLess(duration, 75)


class PlannerBreakdownTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_input_wraps_only_construction_and_does_not_add_calls(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        agent = SimpleNamespace(run=lambda *_args, **_kwargs: "planner-result")
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            planner, "_build_planner_query", return_value="private-prompt"
        ) as build, patch.object(planner, "_new_planner_agent", return_value=agent):
            result = await planner._run_planner_with_retry(
                SimpleNamespace(), {}, {}, {},
            )

        self.assertEqual(result, "planner-result")
        build.assert_called_once()
        self.assertEqual(
            len(_lines(output.getvalue(), "planner_stage_timing", "planner_input")), 1
        )
        self.assertNotIn("private-prompt", "\n".join(
            line for line in output.getvalue().splitlines()
            if line.startswith("event=planner_stage_timing ")
        ))

    def test_parse_wrapper_preserves_result_and_emits_timer(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        sentinel = object()
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            planner, "_parse_response", return_value=sentinel
        ) as parse:
            result = planner._parse_response_with_timing("private-json", SimpleNamespace())

        self.assertIs(result, sentinel)
        parse.assert_called_once()
        line = _lines(
            output.getvalue(), "planner_stage_timing", "planner_parse_validate"
        )[0]
        self.assertIn("success=true", line)
        self.assertNotIn("private-json", line)

    def test_repair_timers_are_allow_listed_and_invalid_stage_is_rejected(self):
        output = io.StringIO()
        with redirect_stdout(output):
            for stage in ("planner_json_repair", "planner_schema_repair"):
                with timed_stage("planner_stage_timing", stage):
                    pass
        self.assertEqual(
            len([line for line in output.getvalue().splitlines()
                 if line.startswith("event=planner_stage_timing ")]), 2,
        )
        with self.assertRaises(ValueError):
            with timed_stage("planner_stage_timing", "private-user-stage"):
                pass


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
