import asyncio
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, Mock, patch

from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner
from backend.app.models.schemas import Attraction, DayPlan, Location, POIInfo, TripPlan
from backend.app.services.google_map_service import (
    GoogleMapService,
    _GROUNDING_OBSERVABILITY_CONTEXT,
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

    def test_city_resolution_categories_are_mutually_exclusive_and_bounded(self):
        match = {"status": "unverified", "evidence": {"city_consistent": False}}
        cases = (
            ({}, "identity_not_attempted"),
            ({"city_identity_attempted": True}, "identity_unresolved"),
            ({"city_identity_attempted": True,
              "city_identity_resolution": "conflicting"}, "identity_conflicting"),
            ({"city_identity_attempted": True,
              "city_identity_resolution": "resolved",
              "city_containment_present": False},
             "trusted_name_absent_containment_empty"),
            ({"city_identity_attempted": True,
              "city_identity_resolution": "resolved",
              "city_containment_present": True},
             "trusted_name_absent_containment_nonmatching"),
        )
        for diagnostics, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    GoogleMapService.city_resolution_terminal_category(match, diagnostics),
                    expected,
                )

    def test_non_city_or_non_unverified_results_have_no_city_resolution_category(self):
        cases = (
            {"status": "verified", "evidence": {"city_consistent": True}},
            {"status": "partial_match", "evidence": {"city_consistent": True}},
            {"status": "unverified", "evidence": {"city_consistent": True}},
        )
        for match in cases:
            self.assertIsNone(
                GoogleMapService.city_resolution_terminal_category(match, {})
            )

    def test_identity_prerequisite_categories_follow_lookup_level_survivors(self):
        passing = {
            "name": True, "type": True, "scope": True, "place_id": True,
            "provider": True, "coordinates": True,
        }
        cases = (
            ([{**passing, "name": False}], "name_below_threshold"),
            ([{**passing, "type": False}], "type_incompatible"),
            ([{**passing, "scope": False}], "scope_conflict"),
            ([{**passing, "place_id": False}], "invalid_place_id"),
            ([{**passing, "provider": False}], "provider_untrusted"),
            ([{**passing, "coordinates": False}], "invalid_coordinates"),
            ([], "name_below_threshold"),
        )
        for gates, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    GoogleMapService.city_identity_prerequisite_category(gates),
                    expected,
                )

    def test_multi_candidate_category_uses_cumulative_lookup_level_gate(self):
        candidates = [
            {
                "name": False, "type": True, "scope": True, "place_id": True,
                "provider": True, "coordinates": True,
            },
            {
                "name": True, "type": False, "scope": True, "place_id": True,
                "provider": True, "coordinates": True,
            },
        ]
        self.assertEqual(
            GoogleMapService.city_identity_prerequisite_category(candidates),
            "type_incompatible",
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

    def test_city_observation_does_not_change_return_or_provider_call_count(self):
        candidate = _poi(name="Exact Place", address="Other Region")
        self.service.search_poi = Mock(return_value=[candidate])
        with patch.object(self.service, "_resolve_city_identity", return_value=None):
            baseline = self.service.match_poi("Exact Place", "Safe City")
            baseline_calls = self.service.search_poi.call_count
        self.service.search_poi.reset_mock()
        with patch.object(self.service, "_resolve_city_identity", return_value=None), \
                observe_generation_grounding() as diagnostics:
            observed = self.service.match_poi("Exact Place", "Safe City")
        self.assertEqual(observed, baseline)
        self.assertEqual(self.service.search_poi.call_count, baseline_calls)
        self.assertEqual(diagnostics["terminal_category"], "city_mismatch")
        self.assertEqual(diagnostics["city_resolution_category"], "identity_unresolved")

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

    async def test_city_resolution_metadata_propagates_to_thread(self):
        def worker():
            diagnostics = _GROUNDING_OBSERVABILITY_CONTEXT.get()
            diagnostics["city_resolution_category"] = "identity_unresolved"

        with observe_generation_grounding() as diagnostics:
            await asyncio.to_thread(worker)
        self.assertEqual(diagnostics["city_resolution_category"], "identity_unresolved")


class CityResolutionRealFlowTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _geocode_response(results):
        return type("Response", (), {
            "raise_for_status": lambda _self: None,
            "json": lambda _self: {"results": results},
        })()

    @staticmethod
    def _identity_result(place_id, name="Trusted City"):
        return {
            "place_id": place_id,
            "types": ["locality", "political"],
            "address_components": [{
                "long_name": name,
                "short_name": name,
                "types": ["locality", "political"],
            }],
        }

    def _service_with_identity_results(self, results):
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)
        response = self._geocode_response(results)
        service._client = type("Client", (), {
            "get": lambda *_args, **_kwargs: response,
            "close": lambda _self: None,
        })()
        return service

    @staticmethod
    def _search(candidate, containing=()):
        def search(*_args, _containing_places=None, **_kwargs):
            if _containing_places is not None:
                _containing_places[candidate.id] = set(containing)
            return [candidate]
        return search

    def test_real_matcher_identity_not_attempted(self):
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)
        service.search_poi = Mock(return_value=[
            _poi(name="Unrelated Object", address="Other Region")
        ])
        with patch.object(
            service, "_resolve_city_identity",
            side_effect=AssertionError("identity resolution must not run"),
        ), observe_generation_grounding() as diagnostics:
            result = service.match_poi("Exact Place", "Safe City")
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["evidence"]["city_consistent"])
        self.assertEqual(diagnostics["city_resolution_category"], "identity_not_attempted")
        self.assertEqual(
            diagnostics["city_identity_prerequisite_category"],
            "name_below_threshold",
        )

    def test_real_matcher_identity_not_attempted_by_type(self):
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)
        candidate = _poi(name="Exact Museum", address="Other Region")
        candidate.type = "tourist_attraction"
        service.search_poi = Mock(return_value=[candidate])
        with patch.object(
            service, "_resolve_city_identity",
            side_effect=AssertionError("identity resolution must not run"),
        ), observe_generation_grounding() as diagnostics:
            result = service.match_poi("Exact Museum", "Safe City")
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(diagnostics["city_resolution_category"], "identity_not_attempted")
        self.assertEqual(
            diagnostics["city_identity_prerequisite_category"], "type_incompatible"
        )

    def test_real_matcher_identity_not_attempted_by_scope(self):
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)
        service.search_poi = Mock(return_value=[
            _poi(name="Exact Place Mall", address="Other Region")
        ])
        with patch.object(
            service, "_resolve_city_identity",
            side_effect=AssertionError("identity resolution must not run"),
        ), observe_generation_grounding() as diagnostics:
            result = service.match_poi("Exact Place", "Safe City")
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(diagnostics["city_resolution_category"], "identity_not_attempted")
        self.assertEqual(
            diagnostics["city_identity_prerequisite_category"], "scope_conflict"
        )

    def test_real_multi_candidate_prerequisite_uses_lookup_level_survivors(self):
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)
        low_name = _poi(
            name="Unrelated Object", address="Other Region", poi_id="low-name"
        )
        wrong_type = _poi(
            name="Exact Museum", address="Other Region", poi_id="wrong-type"
        )
        wrong_type.type = "tourist_attraction"
        service.search_poi = Mock(return_value=[low_name, wrong_type])
        with patch.object(
            service, "_resolve_city_identity",
            side_effect=AssertionError("identity resolution must not run"),
        ), observe_generation_grounding() as diagnostics:
            result = service.match_poi("Exact Museum", "Safe City")
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(
            diagnostics["city_identity_prerequisite_category"], "type_incompatible"
        )

    def test_identity_prerequisite_observation_preserves_return_and_calls(self):
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)
        candidate = _poi(name="Unrelated Object", address="Other Region")
        service.search_poi = Mock(return_value=[candidate])
        baseline = service.match_poi("Exact Place", "Safe City")
        baseline_calls = service.search_poi.call_count
        service.search_poi.reset_mock()
        with observe_generation_grounding() as diagnostics:
            observed = service.match_poi("Exact Place", "Safe City")
        self.assertEqual(observed, baseline)
        self.assertEqual(service.search_poi.call_count, baseline_calls)
        self.assertEqual(
            diagnostics["city_identity_prerequisite_category"],
            "name_below_threshold",
        )

    async def test_concurrent_identity_prerequisite_categories_are_isolated(self):
        low_name_service = GoogleMapService("fake-key")
        wrong_type_service = GoogleMapService("fake-key")
        self.addCleanup(low_name_service.close)
        self.addCleanup(wrong_type_service.close)
        low_name_service.search_poi = Mock(return_value=[
            _poi(name="Unrelated Object", address="Other Region")
        ])
        wrong_type = _poi(name="Exact Museum", address="Other Region")
        wrong_type.type = "tourist_attraction"
        wrong_type_service.search_poi = Mock(return_value=[wrong_type])

        async def worker(service, requested_name):
            with observe_generation_grounding() as diagnostics:
                result = await asyncio.to_thread(
                    service.match_poi, requested_name, "Safe City"
                )
                return result, dict(diagnostics)

        low_name, wrong_type_result = await asyncio.gather(
            worker(low_name_service, "Exact Place"),
            worker(wrong_type_service, "Exact Museum"),
        )
        self.assertEqual(
            low_name[1]["city_identity_prerequisite_category"],
            "name_below_threshold",
        )
        self.assertEqual(
            wrong_type_result[1]["city_identity_prerequisite_category"],
            "type_incompatible",
        )
        self.assertNotIn("type_incompatible", low_name[1].values())
        self.assertNotIn("name_below_threshold", wrong_type_result[1].values())

    def test_real_matcher_identity_unresolved(self):
        service = self._service_with_identity_results([])
        service.search_poi = Mock(side_effect=self._search(
            _poi(name="Exact Place", address="Other Region")
        ))
        with observe_generation_grounding() as diagnostics:
            result = service.match_poi("Exact Place", "Safe City")
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["evidence"]["city_consistent"])
        self.assertEqual(diagnostics["city_resolution_category"], "identity_unresolved")

    def test_real_matcher_identity_conflicting(self):
        service = self._service_with_identity_results([
            self._identity_result("city-a", "Trusted City A"),
            self._identity_result("city-b", "Trusted City B"),
        ])
        service.search_poi = Mock(side_effect=self._search(
            _poi(name="Exact Place", address="Other Region")
        ))
        with observe_generation_grounding() as diagnostics:
            result = service.match_poi("Exact Place", "Safe City")
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["evidence"]["city_consistent"])
        self.assertEqual(diagnostics["city_resolution_category"], "identity_conflicting")

    def test_real_matcher_resolved_identity_with_empty_containment(self):
        service = self._service_with_identity_results([
            self._identity_result("trusted-city-id")
        ])
        service.search_poi = Mock(side_effect=self._search(
            _poi(name="Exact Place", address="Other Region")
        ))
        with observe_generation_grounding() as diagnostics:
            result = service.match_poi("Exact Place", "Safe City")
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["evidence"]["city_consistent"])
        self.assertEqual(
            diagnostics["city_resolution_category"],
            "trusted_name_absent_containment_empty",
        )

    def test_real_matcher_resolved_identity_with_nonmatching_containment(self):
        service = self._service_with_identity_results([
            self._identity_result("trusted-city-id")
        ])
        service.search_poi = Mock(side_effect=self._search(
            _poi(name="Exact Place", address="Other Region"), {"other-parent-id"},
        ))
        with observe_generation_grounding() as diagnostics:
            result = service.match_poi("Exact Place", "Safe City")
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["evidence"]["city_consistent"])
        self.assertEqual(
            diagnostics["city_resolution_category"],
            "trusted_name_absent_containment_nonmatching",
        )

    def test_multi_candidate_diagnostic_uses_final_selected_candidate(self):
        service = self._service_with_identity_results([
            self._identity_result("trusted-city-id")
        ])
        selected = _poi(name="Exact Place", address="Other Region", poi_id="selected")
        runner_up = _poi(
            name="Very Different Place", address="Other Region", poi_id="runner-up"
        )

        def search(*_args, _containing_places=None, **_kwargs):
            if _containing_places is not None:
                _containing_places["selected"] = set()
                _containing_places["runner-up"] = {"other-parent-id"}
            return [selected, runner_up]

        service.search_poi = Mock(side_effect=search)
        with observe_generation_grounding() as diagnostics:
            result = service.match_poi("Exact Place", "Safe City")
        self.assertEqual(result["poi"].id, "selected")
        self.assertEqual(
            diagnostics["city_resolution_category"],
            "trusted_name_absent_containment_empty",
        )

    def test_exception_resets_context_before_next_real_match(self):
        failing = GoogleMapService("fake-key")
        healthy = self._service_with_identity_results([])
        self.addCleanup(failing.close)
        candidate = _poi(name="Exact Place", address="Other Region")
        failing.search_poi = Mock(side_effect=self._search(candidate))
        healthy.search_poi = Mock(side_effect=self._search(candidate))

        def fail_after_touching_context(_city):
            diagnostics = _GROUNDING_OBSERVABILITY_CONTEXT.get()
            diagnostics["city_identity_resolution"] = "conflicting"
            raise RuntimeError("safe-test-failure")

        with self.assertRaises(RuntimeError):
            with observe_generation_grounding():
                with patch.object(failing, "_resolve_city_identity", side_effect=fail_after_touching_context):
                    failing.match_poi("Exact Place", "Safe City")
        self.assertIsNone(_GROUNDING_OBSERVABILITY_CONTEXT.get())

        with observe_generation_grounding() as diagnostics:
            result = healthy.match_poi("Exact Place", "Safe City")
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(diagnostics["city_resolution_category"], "identity_unresolved")
        self.assertNotIn("city_identity_prerequisite_category", diagnostics)
        self.assertIsNone(_GROUNDING_OBSERVABILITY_CONTEXT.get())

    async def test_concurrent_real_matchers_keep_categories_isolated(self):
        unresolved = self._service_with_identity_results([])
        resolved = self._service_with_identity_results([
            self._identity_result("trusted-city-id")
        ])
        candidate = _poi(name="Exact Place", address="Other Region")
        unresolved.search_poi = Mock(side_effect=self._search(candidate))
        resolved.search_poi = Mock(side_effect=self._search(candidate))

        async def worker(service):
            with observe_generation_grounding() as diagnostics:
                result = await asyncio.to_thread(
                    service.match_poi, "Exact Place", "Safe City"
                )
                return result, dict(diagnostics)

        first, second = await asyncio.gather(worker(unresolved), worker(resolved))
        self.assertEqual(first[1]["city_resolution_category"], "identity_unresolved")
        self.assertEqual(
            second[1]["city_resolution_category"],
            "trusted_name_absent_containment_empty",
        )
        self.assertNotIn("trusted_name_absent_containment_empty", first[1].values())
        self.assertNotIn("identity_unresolved", second[1].values())

    def test_photo_stage_real_match_has_no_generation_city_diagnostic(self):
        service = self._service_with_identity_results([])
        service.search_poi = Mock(side_effect=self._search(
            _poi(name="Exact Place", address="Other Region")
        ))
        with patch.object(
            service, "city_resolution_terminal_category",
            wraps=service.city_resolution_terminal_category,
        ) as classify, patch.object(
            service, "_city_identity_prerequisite_gates",
            wraps=service._city_identity_prerequisite_gates,
        ) as prerequisite_gates:
            result = service.match_poi("Exact Place", "Safe City")
        self.assertEqual(result["status"], "unverified")
        classify.assert_not_called()
        prerequisite_gates.assert_not_called()
        self.assertIsNone(_GROUNDING_OBSERVABILITY_CONTEXT.get())


class GroundingSummaryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _city_mismatch_side_effect(category):
        def match(*_args, **_kwargs):
            diagnostics = _GROUNDING_OBSERVABILITY_CONTEXT.get()
            diagnostics["terminal_category"] = "city_mismatch"
            diagnostics["city_resolution_category"] = category
            return {
                "status": "unverified",
                "poi": _poi(),
                "evidence": {"search_calls": 3, "city_consistent": False},
            }
        return match

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

    async def test_city_resolution_counters_sum_to_city_mismatch_without_cache_duplicates(self):
        service = Mock()
        service.match_poi = Mock(side_effect=self._city_mismatch_side_effect(
            "trusted_name_absent_containment_nonmatching"
        ))
        service.grounding_terminal_category = GoogleMapService.grounding_terminal_category
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        output = io.StringIO()
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), redirect_stdout(output):
            await planner._enrich_trip_plan_pois(_plan(["Private Name", "Private Name"]))
        summary = next(line for line in output.getvalue().splitlines()
                       if line.startswith("event=poi_grounding_summary"))
        values = {key: int(value) for key, value in
                  (field.split("=", 1) for field in summary.split()[1:])}
        city_fields = [key for key in values if key.startswith("city_") and key != "city_mismatch"]
        self.assertEqual(values["city_mismatch"], 1)
        self.assertEqual(sum(values[key] for key in city_fields), values["city_mismatch"])
        self.assertEqual(values["city_trusted_name_absent_containment_nonmatching"], 1)
        self.assertEqual(service.match_poi.call_count, 1)

    async def test_identity_not_attempted_prerequisite_counter_invariant(self):
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)

        def search(keywords, *_args, **_kwargs):
            if keywords == "Exact Museum":
                candidate = _poi(name=keywords, address="Other Region", poi_id="museum")
                candidate.type = "tourist_attraction"
                return [candidate]
            if keywords == "Exact Place":
                return [_poi(
                    name="Exact Place Mall", address="Other Region", poi_id="scope"
                )]
            return [_poi(
                name="Unrelated Object", address="Other Region", poi_id="name"
            )]

        service.search_poi = Mock(side_effect=search)
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        output = io.StringIO()
        names = ["Low Name", "Exact Museum", "Exact Place", "Low Name"]
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), redirect_stdout(output):
            await planner._enrich_trip_plan_pois(_plan(names))
        summary = next(line for line in output.getvalue().splitlines()
                       if line.startswith("event=poi_grounding_summary"))
        values = {key: int(value) for key, value in
                  (field.split("=", 1) for field in summary.split()[1:])}
        prerequisite_fields = [
            key for key in values if key.startswith("identity_not_attempted_")
        ]
        city_fields = [
            key for key in values
            if key.startswith("city_") and key != "city_mismatch"
        ]
        self.assertEqual(values["attractions"], 4)
        self.assertEqual(values["unique_lookups"], 3)
        self.assertEqual(values["city_mismatch"], 3)
        self.assertEqual(values["city_identity_not_attempted"], 3)
        self.assertEqual(
            sum(values[key] for key in prerequisite_fields),
            values["city_identity_not_attempted"],
        )
        self.assertEqual(sum(values[key] for key in city_fields), 3)
        self.assertEqual(values["identity_not_attempted_name_below_threshold"], 1)
        self.assertEqual(values["identity_not_attempted_type_incompatible"], 1)
        self.assertEqual(values["identity_not_attempted_scope_conflict"], 1)
        self.assertNotIn("Low Name", summary)
        self.assertNotIn("Exact Museum", summary)
        self.assertNotIn("Exact Place", summary)

    async def test_real_matcher_mixed_batch_preserves_city_counter_invariant(self):
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)
        empty_geocode = type("Response", (), {
            "raise_for_status": lambda _self: None,
            "json": lambda _self: {"results": []},
        })()
        service._client = type("Client", (), {
            "get": lambda *_args, **_kwargs: empty_geocode,
            "close": lambda _self: None,
        })()
        partial = POIInfo(
            id="partial-id", name="Exact Partial", address="Private City",
            type="tourist_attraction", location=Location(longitude=0, latitude=0),
            data_source="google_places", verification_status="verified",
        )

        def search(keywords, *_args, **_kwargs):
            if keywords == "Exact Verified":
                return [_poi(name=keywords, address="Private City", poi_id="verified-id")]
            if keywords == "Exact Partial":
                return [partial]
            if keywords == "Exact Scope":
                return [_poi(
                    name="Exact Scope Mall", address="Private City", poi_id="scope-id"
                )]
            return [_poi(name=keywords, address="Other Region", poi_id="city-failure-id")]

        service.search_poi = Mock(side_effect=search)
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        output = io.StringIO()
        names = [
            "Exact Verified", "Exact Partial", "Exact Scope",
            "City Failure", "City Failure",
        ]
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), redirect_stdout(output):
            await planner._enrich_trip_plan_pois(_plan(names))
        summary = next(line for line in output.getvalue().splitlines()
                       if line.startswith("event=poi_grounding_summary"))
        values = {key: int(value) for key, value in
                  (field.split("=", 1) for field in summary.split()[1:])}
        city_fields = [key for key in values if key.startswith("city_") and key != "city_mismatch"]
        self.assertEqual(values["attractions"], 5)
        self.assertEqual(values["unique_lookups"], 4)
        self.assertEqual(values["verified"], 1)
        self.assertEqual(values["partial"], 1)
        self.assertEqual(values["scope_conflict"], 1)
        self.assertEqual(values["city_mismatch"], 1)
        self.assertEqual(values["city_identity_unresolved"], 1)
        self.assertEqual(sum(values[key] for key in city_fields), values["city_mismatch"])

    async def test_verified_partial_and_non_city_failures_increment_no_city_counter(self):
        service = Mock()
        service.match_poi = Mock(side_effect=[
            _match("verified"),
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
            await planner._enrich_trip_plan_pois(_plan(["First", "Second", "Third"]))
        summary = next(line for line in output.getvalue().splitlines()
                       if line.startswith("event=poi_grounding_summary"))
        values = {key: int(value) for key, value in
                  (field.split("=", 1) for field in summary.split()[1:])}
        city_fields = [key for key in values if key.startswith("city_") and key != "city_mismatch"]
        prerequisite_fields = [
            key for key in values if key.startswith("identity_not_attempted_")
        ]
        self.assertEqual(sum(values[key] for key in city_fields), 0)
        self.assertEqual(sum(values[key] for key in prerequisite_fields), 0)

    async def test_city_summary_contains_no_sensitive_values(self):
        service = Mock()
        service.match_poi = Mock(side_effect=self._city_mismatch_side_effect(
            "identity_conflicting"
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
        self.assertIn("city_identity_conflicting=1", summary)
        for marker in ("Private Name", "Private City", "Private Address", "place-1", "fake-key"):
            self.assertNotIn(marker, summary)

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
