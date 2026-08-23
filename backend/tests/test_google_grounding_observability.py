import asyncio
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, Mock, patch

from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner
from backend.app.models.schemas import Attraction, DayPlan, Location, POIInfo, TripPlan
from backend.app.services.google_map_service import (
    GoogleMapService,
    observe_generation_grounding,
)
from backend.app.services.timing import _ALLOWED_STAGES


def _poi(name="Exact Place", address="Safe City", poi_id="place-1"):
    return POIInfo(
        id=poi_id,
        name=name,
        address=address,
        type="tourist_attraction",
        location=Location(longitude=151.2, latitude=-33.8),
        data_source="google_places",
        verification_status="verified",
    )


def _match(status, *, reason=None, search_calls=1, poi=True):
    evidence = {"search_calls": search_calls}
    if reason:
        evidence["reason"] = reason
    return {
        "status": status,
        "poi": _poi() if poi else None,
        "evidence": evidence,
    }


def _plan(names):
    return TripPlan(
        city="Private City",
        start_date="2026-09-01",
        end_date="2026-09-01",
        days=[DayPlan(
            date="2026-09-01",
            day_index=0,
            city="Private City",
            description="",
            transportation="",
            accommodation="",
            attractions=[Attraction(
                name=name,
                address="Private Address",
                category="Private Category",
                location=Location(longitude=0, latitude=0),
                visit_duration=60,
                description="",
            ) for name in names],
            meals=[],
        )],
        weather_info=[],
        overall_suggestions="",
    )


class GroundingCategoryTests(unittest.TestCase):
    def test_all_bounded_terminal_categories_follow_existing_gate_order(self):
        cases = [
            ({"reason": "no_candidates"}, "no_candidates"),
            ({"reason": "provider_failure"}, "provider_failure"),
            ({"city_consistent": False}, "city_mismatch"),
            ({"city_consistent": True, "type_compatible": False}, "type_mismatch"),
            ({"city_consistent": True, "type_compatible": True,
              "scope_compatible": False}, "scope_conflict"),
            ({"city_consistent": True, "type_compatible": True,
              "scope_compatible": True, "place_id_valid": False}, "invalid_place_id"),
            ({"city_consistent": True, "type_compatible": True,
              "scope_compatible": True, "place_id_valid": True,
              "coordinate_valid": False}, "invalid_coordinates"),
            ({"city_consistent": True, "type_compatible": True,
              "scope_compatible": True, "place_id_valid": True,
              "coordinate_valid": True, "name_score": 0.59}, "name_mismatch"),
            ({"city_consistent": True, "type_compatible": True,
              "scope_compatible": True, "place_id_valid": True,
              "coordinate_valid": True, "name_score": 0.8,
              "runner_up_margin": 0.07}, "ambiguous"),
            ({"city_consistent": True, "type_compatible": True,
              "scope_compatible": True, "place_id_valid": True,
              "coordinate_valid": True, "name_score": 0.8,
              "runner_up_margin": 0.2}, "insufficient_evidence"),
        ]
        for evidence, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    GoogleMapService.grounding_terminal_category({"evidence": evidence}),
                    expected,
                )


class GroundingTimingTests(unittest.TestCase):
    def setUp(self):
        self.service = GoogleMapService("fake-key")

    def tearDown(self):
        self.service.close()

    def test_required_stages_are_bounded_and_zh_only_counts_one_search(self):
        self.assertEqual(
            _ALLOWED_STAGES["google_grounding_timing"],
            {"match_total", "initial_text_search", "multilingual_text_search",
             "address_geocode", "city_geocode", "local_scoring"},
        )
        self.service.search_poi = Mock(return_value=[_poi()])
        output = io.StringIO()
        with redirect_stdout(output), observe_generation_grounding():
            result = self.service.match_poi("Exact Place", "Safe City")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["evidence"]["search_calls"], 1)
        self.assertEqual(self.service.search_poi.call_count, 1)
        text = output.getvalue()
        self.assertIn("stage=initial_text_search", text)
        self.assertIn("stage=match_total", text)
        self.assertNotIn("stage=multilingual_text_search", text)

    def test_multilingual_path_counts_three_without_retry_or_extra_call(self):
        self.service.search_poi = Mock(return_value=[_poi(name="Different")])
        output = io.StringIO()
        with redirect_stdout(output), observe_generation_grounding():
            result = self.service.match_poi("Requested", "Safe City")
        self.assertEqual(result["evidence"]["search_calls"], 3)
        self.assertEqual(self.service.search_poi.call_count, 3)
        self.assertIn("stage=multilingual_text_search", output.getvalue())

    def test_observability_is_opt_in_for_generation_path(self):
        self.service.search_poi = Mock(return_value=[_poi()])
        output = io.StringIO()
        with redirect_stdout(output):
            self.service.match_poi("Exact Place", "Safe City")
        self.assertNotIn("google_grounding_timing", output.getvalue())

    def test_observation_does_not_change_match_return_and_resets_context(self):
        self.service.search_poi = Mock(return_value=[])
        baseline = self.service.match_poi("Private Name", "Private City")
        observed_output = io.StringIO()
        with redirect_stdout(observed_output), observe_generation_grounding() as diagnostics:
            observed = self.service.match_poi("Private Name", "Private City")
        self.assertEqual(observed, baseline)
        self.assertEqual(diagnostics["terminal_category"], "no_candidates")
        reset_output = io.StringIO()
        with redirect_stdout(reset_output):
            self.service.match_poi("Private Name", "Private City")
        self.assertNotIn("google_grounding_timing", reset_output.getvalue())

    def test_invalid_parsed_candidate_uses_side_channel_without_return_mutation(self):
        baseline = {"status": "unverified", "score": 0.0, "poi": None,
                    "evidence": {"search_calls": 1, "reason": "no_candidates"}}
        diagnostics = {"raw_candidates_found": True, "invalid_place_id": True}
        self.assertEqual(
            self.service.grounding_terminal_category(baseline, diagnostics),
            "invalid_place_id",
        )
        self.assertEqual(baseline["evidence"]["reason"], "no_candidates")


class GroundingContextPropagationTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_propagates_to_thread_without_leaking_after_exit(self):
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)
        service.search_poi = Mock(return_value=[_poi()])
        observed_output = io.StringIO()
        with redirect_stdout(observed_output), observe_generation_grounding():
            await asyncio.to_thread(service.match_poi, "Exact Place", "Safe City")
        self.assertIn("google_grounding_timing", observed_output.getvalue())
        plain_output = io.StringIO()
        with redirect_stdout(plain_output):
            await asyncio.to_thread(service.match_poi, "Exact Place", "Safe City")
        self.assertNotIn("google_grounding_timing", plain_output.getvalue())

    async def test_concurrent_observation_contexts_are_isolated(self):
        async def worker(marker):
            with observe_generation_grounding() as diagnostics:
                diagnostics["test_marker"] = marker
                await asyncio.sleep(0)
                return diagnostics["test_marker"], id(diagnostics)

        first, second = await asyncio.gather(worker("first"), worker("second"))
        self.assertEqual((first[0], second[0]), ("first", "second"))
        self.assertNotEqual(first[1], second[1])


class GroundingSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_cache_and_terminal_status_counts_emit_once(self):
        service = Mock()
        service.match_poi = Mock(return_value=_match("verified", search_calls=1))
        service.grounding_terminal_category = GoogleMapService.grounding_terminal_category
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        output = io.StringIO()
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), redirect_stdout(output):
            result = await planner._enrich_trip_plan_pois(
                _plan(["Private Name", "Private Name"])
            )
        text = output.getvalue()
        summaries = [line for line in text.splitlines()
                     if line.startswith("event=poi_grounding_summary")]
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertIn("attractions=2", summary)
        self.assertIn("unique_lookups=1", summary)
        self.assertIn("text_search_calls=1", summary)
        self.assertIn("candidate_found=1", summary)
        self.assertIn("verified=1", summary)
        values = {
            key: int(value) for key, value in
            (field.split("=", 1) for field in summary.split()[1:])
        }
        self.assertGreaterEqual(values["attractions"], values["unique_lookups"])
        self.assertEqual(
            values["verified"] + values["partial"] + values["unverified"],
            values["unique_lookups"],
        )
        self.assertLessEqual(values["candidate_found"], values["unique_lookups"])
        self.assertEqual(service.match_poi.call_count, 1)
        self.assertTrue(all(item.poi_match_status == "verified"
                            for item in result.days[0].attractions))

    async def test_partial_and_unverified_are_one_terminal_decision_each(self):
        service = Mock()
        service.match_poi = Mock(side_effect=[
            _match("partial_match", search_calls=3),
            _match("unverified", reason="no_candidates", search_calls=1, poi=False),
        ])
        service.grounding_terminal_category = GoogleMapService.grounding_terminal_category
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        output = io.StringIO()
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), redirect_stdout(output):
            await planner._enrich_trip_plan_pois(_plan(["First", "Second"]))
        summary = next(line for line in output.getvalue().splitlines()
                       if line.startswith("event=poi_grounding_summary"))
        self.assertIn("unique_lookups=2", summary)
        self.assertIn("text_search_calls=4", summary)
        self.assertIn("partial=1", summary)
        self.assertIn("unverified=1", summary)
        self.assertIn("no_candidates=1", summary)

    async def test_summary_logs_no_user_or_provider_sensitive_values(self):
        markers = ["Private Name", "Private City", "Private Address",
                   "place-1", "fake-key"]
        service = Mock()
        service.match_poi = Mock(return_value=_match(
            "unverified", reason="provider_failure", poi=False,
        ))
        service.grounding_terminal_category = GoogleMapService.grounding_terminal_category
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        output = io.StringIO()
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), redirect_stdout(output):
            await planner._enrich_trip_plan_pois(_plan(["Private Name"]))
        summary = next(line for line in output.getvalue().splitlines()
                       if line.startswith("event=poi_grounding_summary"))
        self.assertIn("provider_failure=1", summary)
        for marker in markers:
            self.assertNotIn(marker, summary)

    async def test_fail_open_does_not_emit_incomplete_summary(self):
        service = Mock()
        service.match_poi = Mock(side_effect=RuntimeError("private failure"))
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        output = io.StringIO()
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), redirect_stdout(output):
            result = await planner._enrich_trip_plan_pois(_plan(["Private Name"]))
        self.assertNotIn("event=poi_grounding_summary", output.getvalue())
        self.assertEqual(result.days[0].attractions[0].poi_match_status, "unverified")


if __name__ == "__main__":
    unittest.main()
