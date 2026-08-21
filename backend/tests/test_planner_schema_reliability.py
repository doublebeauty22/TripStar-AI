import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner
from backend.app.models.schemas import TripPlan, TripRequest
from backend.app.services.llm_service import StructuredOutputLimitReached, llm_execution


def _request():
    return TripRequest(
        city="Private City", start_date="2026-09-01", end_date="2026-09-01",
        travel_days=1, transportation="public", accommodation="hotel",
    )


def _plan():
    return {
        "city": "Private City", "cities": ["Private City"],
        "start_date": "2026-09-01", "end_date": "2026-09-01",
        "days": [], "overall_suggestions": "PRIVATE_SUGGESTION",
    }


def _response(content, finish_reason="stop", tokens=100):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content),
        )],
        usage=SimpleNamespace(completion_tokens=tokens),
    )


class PlannerSchemaReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        self.metadata = {
            "finish_reason": "stop", "configured_output_limit": 6000,
            "completion_tokens": 100, "limit_observed": False,
        }

    def _parse(self, payload, repair=None):
        patches = [patch(
            "backend.app.agents.trip_planner_agent.get_last_structured_output",
            return_value=self.metadata,
        )]
        if repair is not None:
            patches.append(patch.object(self.planner, "_llm_repair_schema", side_effect=repair))
        with patches[0]:
            if len(patches) == 2:
                with patches[1]:
                    return self.planner._parse_response(payload, _request())
            return self.planner._parse_response(payload, _request())

    def test_valid_schema_needs_no_repair(self):
        with patch.object(self.planner, "_llm_repair_schema") as repair:
            result = self._parse(json.dumps(_plan()))
        self.assertEqual(result.city, "Private City")
        repair.assert_not_called()

    def test_valid_json_schema_failure_uses_schema_repair_once_not_syntax_repair(self):
        repaired = _plan()
        with patch.object(self.planner, "_llm_repair_json") as syntax, patch.object(
            self.planner, "_llm_repair_schema", return_value=repaired,
        ) as schema, patch(
            "backend.app.agents.trip_planner_agent.get_last_structured_output",
            return_value=self.metadata,
        ):
            result = self.planner._parse_response('{"city":"Private City"}', _request())
        self.assertEqual(result.city, "Private City")
        syntax.assert_not_called()
        schema.assert_called_once()

    def test_schema_repair_invalid_schema_fails_without_second_call(self):
        repair = unittest.mock.Mock(return_value={"city": "Private City"})
        with self.assertRaisesRegex(ValueError, "schema repair validation failed"):
            self._parse('{"city":"Private City"}', repair)
        repair.assert_called_once()

    def test_empty_optional_start_time_is_safely_normalized(self):
        plan = _plan()
        plan["days"] = [{
            "date": "2026-09-01", "day_index": 0, "start_time": "",
            "description": "safe", "transportation": "walk",
            "accommodation": "hotel", "attractions": [], "meals": [],
        }]
        with patch.object(self.planner, "_llm_repair_schema") as repair:
            result = self._parse(json.dumps(plan))
        self.assertIsNone(result.days[0].start_time)
        repair.assert_not_called()

    def test_ambiguous_values_and_missing_facts_are_not_normalized(self):
        for payload in (
            {**_plan(), "days": "not-a-list"},
            {"city": "Private City"},
        ):
            normalized = self.planner._normalize_planner_schema_data(payload)
            self.assertEqual(normalized, payload)
            with self.assertRaises(ValidationError):
                TripPlan.model_validate(normalized)

    def test_schema_telemetry_is_bounded_normalized_and_private(self):
        payload = _plan()
        payload["days"] = [{
            "date": "PRIVATE_DATE", "day_index": "bad", "start_time": "bad",
            "description": "PRIVATE_DESCRIPTION", "transportation": "walk",
            "accommodation": "hotel", "attractions": [{
                "name": "PRIVATE_POI", "address": "PRIVATE_ADDRESS",
                "location": {}, "visit_duration": "bad", "description": "safe",
            }], "meals": [],
        }]
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(ValueError):
            self._parse(json.dumps(payload), lambda *_args: payload)
        lines = [line for line in output.getvalue().splitlines()
                 if line.startswith("event=planner_schema_event")]
        self.assertGreaterEqual(len(lines), 1)
        self.assertLessEqual(len(lines), 10)
        self.assertTrue(any("days.*.attractions.*.location.longitude" in line for line in lines))
        for private in ("PRIVATE", "bad", "{}", "input_value"):
            self.assertNotIn(private, output.getvalue())

    def test_schema_repair_prompt_is_bounded_safe_and_uses_compatibility_path(self):
        captured = {}
        repaired = json.dumps(_plan())

        def complete(**kwargs):
            captured.update(kwargs)
            return _response(repaired)

        with patch(
            "backend.app.agents.trip_planner_agent.get_llm",
            return_value=SimpleNamespace(model="fake-model"),
        ), patch(
            "backend.app.agents.trip_planner_agent.create_chat_completion",
            side_effect=complete,
        ):
            result = self.planner._llm_repair_schema(
                _plan(), [{"field": "days.*.day_index", "error_type": "int_parsing"}],
            )
        self.assertEqual(result["city"], "Private City")
        self.assertEqual(captured["stage"], "schema_repair")
        self.assertEqual(captured["max_tokens"], 6000)
        self.assertEqual(captured["stage_max_token_exposure"], 6000)
        prompt = captured["messages"][0]["content"]
        self.assertIn("preserve", prompt.lower())
        self.assertNotIn("pydantic", prompt.lower())

    def test_schema_repair_length_and_invalid_json_fail_without_recursion(self):
        cases = (
            (_response("PRIVATE_JSON", "length", 6000), StructuredOutputLimitReached),
            (_response("not-json"), ValueError),
        )
        for response, expected in cases:
            with self.subTest(expected=expected.__name__), patch(
                "backend.app.agents.trip_planner_agent.get_llm",
                return_value=SimpleNamespace(model="fake-model"),
            ), patch(
                "backend.app.agents.trip_planner_agent.create_chat_completion",
                return_value=response,
            ) as complete, self.assertRaises(expected):
                self.planner._llm_repair_schema(
                    _plan(), [{"field": "days", "error_type": "list_type"}],
                )
            complete.assert_called_once()

    def test_schema_repair_uses_existing_logical_call_budget(self):
        fake_llm = SimpleNamespace(
            model="fake-model",
            _client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                create=lambda **_kwargs: _response(json.dumps(_plan())),
            ))),
        )
        with llm_execution("schema-budget", max_calls=1) as usage:
            with patch(
                "backend.app.agents.trip_planner_agent.get_llm", return_value=fake_llm,
            ):
                self.planner._llm_repair_schema(
                    _plan(), [{"field": "days", "error_type": "list_type"}],
                )
        self.assertEqual(usage.logical_llm_calls, 1)
        self.assertEqual(usage.stage_calls, {"schema_repair": 1})


if __name__ == "__main__":
    unittest.main()
