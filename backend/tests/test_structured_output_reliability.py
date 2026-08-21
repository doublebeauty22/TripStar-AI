import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner
from backend.app.models.schemas import TripRequest
from backend.app.services.llm_service import StructuredOutputLimitReached


def _request():
    return TripRequest(
        city="Safe City", start_date="2026-09-01", end_date="2026-09-01",
        travel_days=1,
        preferences=[], transportation="public_transport", accommodation="hotel",
    )


def _plan(suggestion="safe"):
    return {
        "city": "Safe City", "cities": ["Safe City"],
        "start_date": "2026-09-01", "end_date": "2026-09-01",
        "days": [], "overall_suggestions": suggestion,
    }


def _metadata(reason="stop", limit=6000, tokens=100):
    return {
        "finish_reason": reason, "configured_output_limit": limit,
        "completion_tokens": tokens, "limit_observed": tokens == limit,
    }


class PlannerStructuredOutputTests(unittest.TestCase):
    def setUp(self):
        self.planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)

    def test_valid_json_parses_without_repair(self):
        with patch(
            "backend.app.agents.trip_planner_agent.get_last_structured_output",
            return_value=_metadata(),
        ), patch.object(self.planner, "_llm_repair_json") as repair:
            result = self.planner._parse_response(json.dumps(_plan()), _request())
        self.assertEqual(result.city, "Safe City")
        repair.assert_not_called()

    def test_deterministic_local_repair_still_handles_malformed_json(self):
        broken = json.dumps(_plan())[:-1]
        with patch(
            "backend.app.agents.trip_planner_agent.get_last_structured_output",
            return_value=_metadata(),
        ), patch.object(self.planner, "_llm_repair_json") as repair:
            result = self.planner._parse_response(broken, _request())
        self.assertEqual(result.city, "Safe City")
        repair.assert_not_called()

    def test_valid_json_schema_failure_does_not_invoke_syntax_repair(self):
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.agents.trip_planner_agent.get_last_structured_output",
            return_value=_metadata(),
        ), patch.object(self.planner, "_llm_repair_json") as repair:
            with self.assertRaisesRegex(ValueError, "schema validation failed"):
                self.planner._parse_response('{"city":"Safe City"}', _request())
        repair.assert_not_called()
        self.assertIn("category=schema_validation_failed", output.getvalue())
        self.assertNotIn("Safe City", output.getvalue())

    def test_json_decode_failure_is_safe_and_repair_runs_once(self):
        output = io.StringIO()
        repaired = json.dumps(_plan())
        with redirect_stdout(output), patch(
            "backend.app.agents.trip_planner_agent.get_last_structured_output",
            side_effect=[_metadata(), _metadata()],
        ), patch.object(self.planner, "_llm_repair_json", return_value=repaired) as repair:
            result = self.planner._parse_response('{broken', _request())
        self.assertEqual(result.city, "Safe City")
        repair.assert_called_once()
        self.assertIn("category=json_decode_failed", output.getvalue())
        self.assertNotIn("{broken", output.getvalue())

    def test_planner_length_is_rejected_before_parsing(self):
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.agents.trip_planner_agent.get_last_structured_output",
            return_value=_metadata("length", 6000, 6000),
        ), patch.object(self.planner, "_llm_repair_json") as repair:
            with self.assertRaises(StructuredOutputLimitReached):
                self.planner._parse_response(json.dumps(_plan()), _request())
        repair.assert_not_called()
        self.assertIn("category=output_limit_reached", output.getvalue())
        self.assertNotIn("Safe City", output.getvalue())

    def test_repair_uses_complete_input_and_6000_bound(self):
        middle = "MIDDLE_PRIVATE_MARKER"
        broken = "prefix" + ("x" * 2200) + middle + ("y" * 2200) + "suffix"
        captured = {}
        response = SimpleNamespace(
            choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content=json.dumps(_plan("z" * 1800))),
            )],
            usage=SimpleNamespace(completion_tokens=2200),
        )

        def complete(**kwargs):
            captured.update(kwargs)
            return response

        with patch(
            "backend.app.agents.trip_planner_agent.get_llm",
            return_value=SimpleNamespace(model="fake"),
        ), patch(
            "backend.app.agents.trip_planner_agent.create_chat_completion",
            side_effect=complete,
        ):
            repaired = self.planner._llm_repair_json(broken)
        self.assertIn(middle, captured["messages"][0]["content"])
        self.assertEqual(captured["max_tokens"], 6000)
        self.assertEqual(captured["stage_max_token_exposure"], 6000)
        self.assertGreater(len(json.loads(repaired)["overall_suggestions"]), 1500)

    def test_repair_length_is_rejected_without_content_logging(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(
                finish_reason="length", message=SimpleNamespace(content="PRIVATE_GENERATED_JSON"),
            )],
            usage=SimpleNamespace(completion_tokens=6000),
        )
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.agents.trip_planner_agent.get_llm",
            return_value=SimpleNamespace(model="fake"),
        ), patch(
            "backend.app.agents.trip_planner_agent.create_chat_completion",
            return_value=response,
        ):
            with self.assertRaises(StructuredOutputLimitReached):
                self.planner._llm_repair_json("PRIVATE_BROKEN_JSON")
        self.assertIn("category=output_limit_reached", output.getvalue())
        self.assertNotIn("PRIVATE", output.getvalue())

    def test_repaired_decode_and_schema_failures_are_distinct(self):
        for repaired, category in (
            ("{broken", "json_decode_failed"),
            ('{"city":"Safe City"}', "schema_validation_failed"),
        ):
            with self.subTest(category=category):
                output = io.StringIO()
                with redirect_stdout(output), patch(
                    "backend.app.agents.trip_planner_agent.get_last_structured_output",
                    side_effect=[_metadata(), _metadata()],
                ), patch.object(self.planner, "_llm_repair_json", return_value=repaired):
                    with self.assertRaises(ValueError):
                        self.planner._parse_response("{broken", _request())
                self.assertIn(f"category={category}", output.getvalue())


if __name__ == "__main__":
    unittest.main()
