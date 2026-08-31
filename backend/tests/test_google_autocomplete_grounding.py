import asyncio
import io
import os
import socket
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner
from backend.app.models.schemas import Attraction, DayPlan, Location, POIInfo, TripPlan
from backend.app.services.google_map_service import (
    GoogleMapService,
    _GROUNDING_OBSERVABILITY_CONTEXT,
    observe_generation_grounding,
)
from backend.tests import ExternalNetworkBlocked, assert_network_guard_active


def _poi(name="Unrelated English Label", poi_id="google-place-1", address="Other Region"):
    return POIInfo(
        id=poi_id,
        name=name,
        address=address,
        type="tourist_attraction",
        location=Location(longitude=151.2, latitude=-33.8),
        data_source="google_places",
        verification_status="verified",
    )


def _prediction(place_id="google-place-1", main_text="Shibuya Crossing", types=None):
    return {
        "state": "prediction",
        "place_id": place_id,
        "main_text": main_text,
        "types": types or ["tourist_attraction"],
    }


class AutocompleteShadowMatchingTests(unittest.TestCase):
    def setUp(self):
        self.service = GoogleMapService("fake-key")
        self.addCleanup(self.service.close)

    def _run(self, requested, prediction=None, candidate=None):
        candidate = candidate or _poi()
        self.service.search_poi = Mock(return_value=[candidate])
        self.service._autocomplete_shadow_prediction = Mock(
            return_value=prediction or _prediction()
        )
        with observe_generation_grounding() as observation:
            result = self.service.match_poi(requested, "Safe City")
        return result, observation

    def test_chinese_to_english_strong_alias_is_shadow_eligible_only(self):
        result, observation = self._run("涩谷十字路口")
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(observation["autocomplete_shadow"]["outcome"], "eligible")
        self.assertEqual(self.service.search_poi.call_count, 3)
        self.service._autocomplete_shadow_prediction.assert_called_once()

    def test_japanese_to_english_strong_alias_is_shadow_eligible_only(self):
        # The existing script classifier treats this mixed kanji/kana spelling as
        # mixed-script. Pin the production-proven cross-script eligibility gate so
        # this fixture isolates the Japanese-to-English alias corroboration rule.
        with patch.object(
            self.service, "_script_relationship", return_value="cross_script"
        ):
            result, observation = self._run(
                "東京スカイツリー",
                _prediction(main_text="Tokyo Skytree"),
            )
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(observation["autocomplete_shadow"]["outcome"], "eligible")

    def test_weak_main_text_is_not_eligible(self):
        result, observation = self._run(
            "涩谷十字路口", _prediction(main_text="Different Facility")
        )
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(observation["autocomplete_shadow"]["outcome"], "name_weak")

    def test_missing_main_text_is_a_bounded_terminal_outcome(self):
        result, observation = self._run(
            "涩谷十字路口", _prediction(main_text="")
        )
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(
            observation["autocomplete_shadow"]["outcome"], "main_text_missing"
        )

    def test_place_id_mismatch_is_not_eligible(self):
        _, observation = self._run(
            "涩谷十字路口", _prediction(place_id="different-google-place")
        )
        self.assertEqual(
            observation["autocomplete_shadow"]["outcome"], "place_id_mismatch"
        )

    def test_no_prediction_query_only_malformed_and_provider_failure(self):
        for state, expected in (
            ("no_prediction", "no_prediction"),
            ("malformed", "malformed"),
            ("provider_failure", "provider_failure"),
        ):
            with self.subTest(state=state):
                _, observation = self._run("涩谷十字路口", {"state": state})
                self.assertEqual(
                    observation["autocomplete_shadow"]["outcome"], expected
                )

    def test_type_incompatible_is_not_eligible(self):
        # Exercise the helper directly so the Text candidate passes the current
        # museum gate while the corroborating prediction does not.
        candidate = _poi(name="Unrelated English Label")
        candidate.type = "museum"
        entry = {
            "poi": candidate,
            "names": ["Unrelated English Label"],
        }
        direct_observation = {}
        self.service._autocomplete_shadow_prediction = Mock(return_value=_prediction(
            main_text="合成博物馆", types=["train_station"]
        ))
        self.service._observe_autocomplete_shadow(
            requested_name="合成博物馆",
            city="Safe City",
            status="unverified",
            best_evidence={
                "name_score": 0.1, "city_consistent": False,
                "type_compatible": True, "scope_compatible": True,
            },
            best_entry=entry,
            aggregated={"google-place-1": entry},
            city_identity_attempted=False,
            observation=direct_observation,
        )
        self.assertEqual(
            direct_observation["autocomplete_shadow"]["outcome"],
            "type_incompatible",
        )

    def test_verified_partial_and_containment_empty_never_call_autocomplete(self):
        autocomplete = Mock(return_value=_prediction())
        self.service._autocomplete_shadow_prediction = autocomplete

        self.service.search_poi = Mock(return_value=[_poi(
            name="Exact Place", address="Safe City"
        )])
        with observe_generation_grounding():
            self.assertEqual(
                self.service.match_poi("Exact Place", "Safe City")["status"], "verified"
            )

        entry = {"poi": _poi(), "names": ["Unrelated English Label"]}
        self.service._observe_autocomplete_shadow(
            requested_name="涩谷十字路口", city="Safe City",
            status="partial_match",
            best_evidence={
                "name_score": 0.1, "city_consistent": True,
                "type_compatible": True, "scope_compatible": True,
            },
            best_entry=entry, aggregated={"google-place-1": entry},
            city_identity_attempted=False, observation={},
        )

        self.service.search_poi = Mock(return_value=[_poi(
            name="Exact Place", address="Other Region"
        )])
        with patch.object(
            self.service,
            "_resolve_city_identity",
            return_value=SimpleNamespace(
                place_id="city-place", names=frozenset({"safecity"}), result_type="locality"
            ),
        ), observe_generation_grounding():
            self.assertEqual(
                self.service.match_poi("Exact Place", "Safe City")["status"],
                "unverified",
            )
        autocomplete.assert_not_called()

    def test_all_remaining_noneligible_paths_make_zero_autocomplete_calls(self):
        identity = SimpleNamespace(
            place_id="city-place", names=frozenset({"safecity"}), result_type="locality"
        )

        def conflicting(_city):
            observation = _GROUNDING_OBSERVABILITY_CONTEXT.get()
            if observation is not None:
                observation["city_identity_resolution"] = "conflicting"
            return None

        def run_case(
            candidate,
            requested="Exact Place",
            city="Safe City",
            resolver=None,
            containing=(),
            observed=True,
        ):
            service = GoogleMapService("fake-key")
            self.addCleanup(service.close)

            def search(*_args, _containing_places=None, **_kwargs):
                if _containing_places is not None and candidate.id:
                    _containing_places[candidate.id] = set(containing)
                return [candidate]

            service.search_poi = Mock(side_effect=search)
            service._autocomplete_shadow_prediction = Mock(return_value=_prediction())
            resolver_patch = patch.object(
                service, "_resolve_city_identity", side_effect=resolver
            ) if resolver is not None else patch.object(
                service, "_resolve_city_identity", return_value=None
            )
            with resolver_patch:
                if observed:
                    with observe_generation_grounding():
                        result = service.match_poi(requested, city)
                else:
                    result = service.match_poi(requested, city)
            service._autocomplete_shadow_prediction.assert_not_called()
            return result

        cases = (
            ("identity_unresolved", _poi(name="Exact Place"), None, (), True),
            ("identity_conflicting", _poi(name="Exact Place"), conflicting, (), True),
            ("containment_empty", _poi(name="Exact Place"), lambda _city: identity, (), True),
            ("containment_nonmatching", _poi(name="Exact Place"), lambda _city: identity,
             ("other-container",), True),
        )
        for label, candidate, resolver, containing, observed in cases:
            with self.subTest(label=label):
                result = run_case(candidate, resolver=resolver, containing=containing,
                                  observed=observed)
                self.assertEqual(result["status"], "unverified")

        with self.subTest(label="non_city_unverified"):
            result = run_case(_poi(address="Safe City"), requested="完全不同")
            self.assertEqual(result["status"], "unverified")

        invalid_id = _poi()
        invalid_id.id = ""
        invalid_coordinates = _poi()
        invalid_coordinates.location = Location(longitude=0, latitude=0)
        untrusted = _poi()
        untrusted.data_source = "amap"
        for label, candidate in (
            ("invalid_place_id", invalid_id),
            ("invalid_coordinates", invalid_coordinates),
            ("provider_untrusted", untrusted),
        ):
            with self.subTest(label=label):
                result = run_case(candidate, requested="涩谷十字路口")
                self.assertEqual(result["status"], "unverified")

        with self.subTest(label="photo_stage"):
            result = run_case(
                _poi(), requested="涩谷十字路口", observed=False,
            )
            self.assertEqual(result["status"], "unverified")

    def test_scope_blockers_never_enter_shadow(self):
        for requested, candidate_name in (
            ("Synthetic Tower", "Synthetic Tower Observation Deck"),
            ("Synthetic Station", "Synthetic Station Mall"),
            ("Synthetic Museum", "Synthetic Museum Entrance"),
        ):
            with self.subTest(candidate=candidate_name):
                self.service.search_poi = Mock(return_value=[_poi(name=candidate_name)])
                autocomplete = Mock(return_value=_prediction())
                self.service._autocomplete_shadow_prediction = autocomplete
                with observe_generation_grounding():
                    result = self.service.match_poi(requested, "Safe City")
                self.assertNotEqual(result["status"], "verified")
                autocomplete.assert_not_called()

    def test_observed_and_unobserved_match_results_are_identical(self):
        self.service.search_poi = Mock(return_value=[_poi()])
        baseline = self.service.match_poi("涩谷十字路口", "Safe City")
        baseline_calls = self.service.search_poi.call_count
        self.service.search_poi.reset_mock()
        self.service._autocomplete_shadow_prediction = Mock(return_value=_prediction())
        with observe_generation_grounding():
            observed = self.service.match_poi("涩谷十字路口", "Safe City")
        self.assertEqual(observed, baseline)
        self.assertEqual(self.service.search_poi.call_count, baseline_calls)
        self.service._autocomplete_shadow_prediction.assert_called_once()


class AutocompleteContractTests(unittest.TestCase):
    class Response:
        def __init__(self, payload, status=200):
            self.payload = payload
            self.status_code = status
            self.request = httpx.Request("POST", "https://example.invalid")

        def raise_for_status(self):
            if self.status_code >= 400:
                response = httpx.Response(self.status_code, request=self.request)
                raise httpx.HTTPStatusError("private", request=self.request, response=response)

        def json(self):
            if isinstance(self.payload, Exception):
                raise self.payload
            return self.payload

    def setUp(self):
        self.service = GoogleMapService("fake-key")
        self.addCleanup(self.service.close)

    def test_request_is_one_bounded_unbiased_no_retry_post(self):
        self.assertEqual(self.service._client.timeout.connect, 15.0)
        client = Mock()
        client.post.return_value = self.Response({"suggestions": []})
        self.service._client = client
        result = self.service._autocomplete_shadow_prediction("Synthetic Name", "Synthetic City")
        self.assertEqual(result, {"state": "no_prediction"})
        client.post.assert_called_once()
        url = client.post.call_args.args[0]
        kwargs = client.post.call_args.kwargs
        self.assertEqual(url, "https://places.googleapis.com/v1/places:autocomplete")
        self.assertEqual(kwargs["json"]["input"], "Synthetic Name Synthetic City")
        self.assertNotIn("locationBias", kwargs["json"])
        self.assertNotIn("locationRestriction", kwargs["json"])
        self.assertFalse(kwargs["json"]["includeQueryPredictions"])
        self.assertNotIn("sessionToken", kwargs["json"])
        self.assertEqual(kwargs["timeout"], 2.0)

    def test_query_prediction_is_ignored_and_first_place_prediction_is_parsed(self):
        self.service._client = Mock(post=Mock(return_value=self.Response({
            "suggestions": [
                {"queryPrediction": {"text": {"text": "private"}}},
                {"placePrediction": {
                    "placeId": "google-place-1",
                    "structuredFormat": {"mainText": {"text": "Safe Name"}},
                    "types": ["tourist_attraction"],
                }},
            ]
        })))
        result = self.service._autocomplete_shadow_prediction("Synthetic", "City")
        self.assertEqual(result["state"], "prediction")
        self.assertEqual(result["place_id"], "google-place-1")

    def test_malformed_and_timeout_are_bounded_and_secret_safe(self):
        for response, expected in (
            (self.Response(ValueError("private-body")), "malformed"),
            (httpx.TimeoutException("private-query"), "provider_failure"),
        ):
            with self.subTest(expected=expected):
                client = Mock()
                if isinstance(response, Exception):
                    client.post.side_effect = response
                else:
                    client.post.return_value = response
                self.service._client = client
                output = io.StringIO()
                with redirect_stdout(output):
                    result = self.service._autocomplete_shadow_prediction(
                        "Private Planner Name", "Private City"
                    )
                self.assertEqual(result["state"], expected)
                rendered = output.getvalue()
                for private in (
                    "Private Planner Name", "Private City", "private-body",
                    "private-query", "fake-key",
                ):
                    self.assertNotIn(private, rendered)

    def test_missing_and_malformed_prediction_fields_fail_closed(self):
        cases = (
            ({"placeId": "google-place-1", "structuredFormat": {}, "types": []},
             "prediction"),
            ({"placeId": "google-place-1", "structuredFormat": {"mainText": []},
              "types": []}, "malformed"),
            ({"placeId": 123, "structuredFormat": {"mainText": {"text": "Safe"}},
              "types": []}, "malformed"),
        )
        for prediction, expected in cases:
            with self.subTest(expected=expected):
                self.service._client = Mock(post=Mock(return_value=self.Response({
                    "suggestions": [{"placePrediction": prediction}]
                })))
                result = self.service._autocomplete_shadow_prediction("Synthetic", "City")
                self.assertEqual(result["state"], expected)
                if expected == "prediction":
                    self.assertEqual(result["main_text"], "")

    def test_malformed_first_place_prediction_is_not_rescued_by_later_valid_one(self):
        self.service._client = Mock(post=Mock(return_value=self.Response({
            "suggestions": [
                {"placePrediction": {"placeId": 123}},
                {"placePrediction": {
                    "placeId": "google-place-1",
                    "structuredFormat": {"mainText": {"text": "Safe"}},
                    "types": ["tourist_attraction"],
                }},
            ]
        })))
        result = self.service._autocomplete_shadow_prediction("Synthetic", "City")
        self.assertEqual(result, {"state": "malformed"})


class NetworkEgressGuardTests(unittest.TestCase):
    def test_external_ipv4_ipv6_dns_and_create_connection_are_blocked(self):
        assert_network_guard_active()

        def direct_connect(family, address, *, use_connect_ex=False):
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                method = sock.connect_ex if use_connect_ex else sock.connect
                return method(address)
            finally:
                sock.close()

        attempts = (
            lambda: direct_connect(socket.AF_INET, ("203.0.113.1", 443)),
            lambda: direct_connect(socket.AF_INET6, ("2001:db8::1", 443)),
            lambda: direct_connect(
                socket.AF_INET, ("203.0.113.1", 443), use_connect_ex=True
            ),
            lambda: socket.create_connection(("example.invalid", 443)),
            lambda: socket.getaddrinfo("example.invalid", 443),
        )
        for attempt in attempts:
            with self.subTest(attempt=attempt):
                with self.assertRaisesRegex(
                    ExternalNetworkBlocked, "real socket egress is blocked"
                ):
                    attempt()

    def test_supported_unittest_entrypoints_activate_guard(self):
        repository_root = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            (str(repository_root), str(repository_root / "backend"))
        )
        commands = (
            (
                sys.executable, "-m", "unittest",
                "backend.tests.test_network_guard_direct",
            ),
            (
                sys.executable, "-m", "unittest", "discover",
                "-s", "backend/tests", "-t", ".",
                "-p", "test_network_guard_direct.py",
            ),
            (sys.executable, "backend/tests/test_network_guard_direct.py"),
        )
        for command in commands:
            with self.subTest(command=command):
                completed = subprocess.run(
                    command,
                    cwd=repository_root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    msg="supported unittest entrypoint did not activate network guard",
                )


class AutocompleteSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_attractions_emit_one_attempt_and_one_terminal_outcome(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)
        service.search_poi = Mock(return_value=[_poi()])
        service._autocomplete_shadow_prediction = Mock(return_value=_prediction())
        plan = TripPlan(
            city="Safe City", start_date="2026-09-01", end_date="2026-09-01",
            days=[DayPlan(
                date="2026-09-01", day_index=0, city="Safe City",
                description="", transportation="", accommodation="",
                attractions=[Attraction(
                    name="涩谷十字路口", address="", category="",
                    location=Location(longitude=0, latitude=0), visit_duration=60,
                    description="",
                ) for _ in range(2)], meals=[],
            )], weather_info=[], overall_suggestions="",
        )
        output = io.StringIO()
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), redirect_stdout(output):
            result = await planner._enrich_trip_plan_pois(plan)
        summary = next(
            line for line in output.getvalue().splitlines()
            if line.startswith("event=poi_grounding_summary")
        )
        self.assertIn("autocomplete_shadow_attempted=1", summary)
        self.assertIn("autocomplete_shadow_eligible=1", summary)
        self.assertIn("autocomplete_shadow_place_id_match=1", summary)
        self.assertIn("autocomplete_shadow_name_strong=1", summary)
        self.assertIn("autocomplete_shadow_type_compatible=1", summary)
        self.assertIn("autocomplete_shadow_score_band_088plus=1", summary)
        values = {
            field: int(value)
            for field, value in (
                token.split("=", 1) for token in summary.split()[1:]
            )
        }
        terminal_fields = (
            "autocomplete_shadow_provider_failure",
            "autocomplete_shadow_malformed",
            "autocomplete_shadow_no_prediction",
            "autocomplete_shadow_place_id_mismatch",
            "autocomplete_shadow_main_text_missing",
            "autocomplete_shadow_name_weak",
            "autocomplete_shadow_type_incompatible",
            "autocomplete_shadow_eligible",
        )
        self.assertEqual(
            values["autocomplete_shadow_attempted"],
            sum(values[field] for field in terminal_fields),
        )
        self.assertEqual(values["autocomplete_shadow_place_id_mismatch"], 0)
        self.assertEqual(values["autocomplete_shadow_name_weak"], 0)
        self.assertEqual(values["autocomplete_shadow_type_incompatible"], 0)
        self.assertEqual(service._autocomplete_shadow_prediction.call_count, 1)
        self.assertTrue(all(item.poi_match_status == "unverified"
                            for item in result.days[0].attractions))
        for private in ("涩谷十字路口", "google-place-1", "Shibuya Crossing"):
            self.assertNotIn(private, summary)

    async def test_timeout_is_one_fail_open_terminal_outcome_without_retry(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)
        service.search_poi = Mock(return_value=[_poi()])
        client = Mock()
        client.post.side_effect = httpx.TimeoutException("private-timeout")
        service._client = client
        plan = TripPlan(
            city="Safe City", start_date="2026-09-01", end_date="2026-09-01",
            days=[DayPlan(
                date="2026-09-01", day_index=0, city="Safe City",
                description="", transportation="", accommodation="",
                attractions=[Attraction(
                    name="涩谷十字路口", address="", category="",
                    location=Location(longitude=0, latitude=0), visit_duration=60,
                    description="",
                )], meals=[],
            )], weather_info=[], overall_suggestions="",
        )
        output = io.StringIO()
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), redirect_stdout(output):
            result = await planner._enrich_trip_plan_pois(plan)
        summary = next(
            line for line in output.getvalue().splitlines()
            if line.startswith("event=poi_grounding_summary")
        )
        self.assertIn("autocomplete_shadow_attempted=1", summary)
        self.assertIn("autocomplete_shadow_provider_failure=1", summary)
        self.assertIn("autocomplete_shadow_place_id_match=0", summary)
        self.assertIn("autocomplete_shadow_name_strong=0", summary)
        self.assertIn("autocomplete_shadow_type_compatible=0", summary)
        client.post.assert_called_once()
        self.assertEqual(client.post.call_args.kwargs["timeout"], 2.0)
        self.assertEqual(result.days[0].attractions[0].poi_match_status, "unverified")

    async def test_nonattempted_summary_has_only_zero_shadow_counters(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)
        service.search_poi = Mock(return_value=[_poi(
            name="Exact Place", address="Safe City"
        )])
        service._autocomplete_shadow_prediction = Mock(return_value=_prediction())
        plan = TripPlan(
            city="Safe City", start_date="2026-09-01", end_date="2026-09-01",
            days=[DayPlan(
                date="2026-09-01", day_index=0, city="Safe City",
                description="", transportation="", accommodation="",
                attractions=[Attraction(
                    name="Exact Place", address="", category="",
                    location=Location(longitude=0, latitude=0), visit_duration=60,
                    description="",
                )], meals=[],
            )], weather_info=[], overall_suggestions="",
        )
        output = io.StringIO()
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), redirect_stdout(output):
            await planner._enrich_trip_plan_pois(plan)
        summary = next(
            line for line in output.getvalue().splitlines()
            if line.startswith("event=poi_grounding_summary")
        )
        shadow_values = [
            int(token.split("=", 1)[1])
            for token in summary.split()
            if token.startswith("autocomplete_shadow_")
        ]
        self.assertTrue(shadow_values)
        self.assertTrue(all(value == 0 for value in shadow_values))
        service._autocomplete_shadow_prediction.assert_not_called()

    async def test_contexts_are_isolated_and_reset(self):
        service = GoogleMapService("fake-key")
        self.addCleanup(service.close)
        service.search_poi = Mock(return_value=[_poi()])
        service._autocomplete_shadow_prediction = Mock(return_value=_prediction())

        async def observed():
            with observe_generation_grounding() as diagnostics:
                await asyncio.to_thread(service.match_poi, "涩谷十字路口", "Safe City")
                return dict(diagnostics)

        async def plain():
            await asyncio.to_thread(service.match_poi, "涩谷十字路口", "Safe City")

        observed_result, _ = await asyncio.gather(observed(), plain())
        self.assertIn("autocomplete_shadow", observed_result)
        self.assertEqual(service._autocomplete_shadow_prediction.call_count, 1)
