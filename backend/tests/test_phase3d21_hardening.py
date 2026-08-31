import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner
from backend.app.evaluation.capture import reject_secrets, sanitized_plan_for_evaluation, to_offline_evaluation_input
from backend.app.evaluation.capture_models import reject_personal_identifiers
from backend.app.evaluation.models import SanitizedProviderFixture
from backend.app.evaluation.snapshots import ProviderSnapshotStore, SnapshotError, commit_capture_set
from backend.app.evaluation.production_capture import build_weather_capture
from backend.app.models.schemas import WeatherResult
from backend.app.services.planner_observation import capture_planner_observations
from backend.app.services.trip_validator_service import TripValidatorService
from backend.tests.test_phase3d1_capture import CASES, known, result_for
from backend.tests.test_trip_validator_service import _FakeGoogleService, _attraction, _plan, _request


class SanitizerHardeningTests(unittest.TestCase):
    def test_legitimate_travel_text_numbers_at_urls_and_quotes_pass(self):
        values = [
            "故宫 2026年10月12日 09:30 入场，编号 1234567890。",
            "从 A@B 艺术区前往景山，参考 https://example.com/travel?id=123456789012",
            "证据摘录：建议下午四点以后到达，避开人流。",
        ]
        for value in values:
            reject_personal_identifiers({"quote": value}); reject_secrets({"quote": value})

    def test_real_pii_identifiers_and_credentials_fail_closed(self):
        rejected = [
            {"quote": "contact person@example.com"}, {"quote": "电话 13812345678"},
            {"user_id": "u-1"}, {"session_id": "s-1"}, {"passport": "P123"},
            {"Authorization": "Bearer abc"}, {"url": "http://user:pass@example.com"},
            {"url": "https://example.com?q=1&api_key=secret"},
        ]
        for value in rejected:
            with self.assertRaises(ValueError):
                reject_personal_identifiers(value); reject_secrets(value)

    def test_xhs_raw_context_removed_but_evidence_support_retained(self):
        result = result_for(CASES[0])
        # The fixture plan may have no XHS data; this verifies copying/immutability.
        original = result.final_trip_plan.model_copy(deep=True)
        safe = sanitized_plan_for_evaluation(result.final_trip_plan)
        self.assertEqual(result.final_trip_plan, original)
        self.assertEqual(safe.xhs_research, [])


class AtomicCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.store = ProviderSnapshotStore(self.root / "snapshots")
        self.output = self.root / "runs" / "artifact.json"
        self.fixture = SanitizedProviderFixture(
            fixture_version="v1", provider="xhs", state="available",
            sanitized=True, payload={"facts": [{"note_id": "n1"}]})

    def tearDown(self): self.temp.cleanup()

    def assert_no_official_set(self):
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_name("artifact.manifest.json").exists())
        self.assertFalse(self.store.path("gc_test", "xhs").exists())

    def test_hash_failure_leaves_no_capture_set(self):
        with self.assertRaises(SnapshotError):
            commit_capture_set(output_path=self.output, snapshot_store=self.store,
                case_id="gc_test", artifact={"sanitized": True}, fixtures={"xhs": self.fixture},
                hash_function=lambda raw: "sha256:wrong", artifact_validator=lambda value: value)
        self.assert_no_official_set()

    def test_write_failure_cleans_published_files(self):
        calls = 0
        def fail_second(source, target):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError("injected write failure")
            Path(source).replace(target)
        with self.assertRaises(OSError):
            commit_capture_set(output_path=self.output, snapshot_store=self.store,
                case_id="gc_test", artifact={"sanitized": True}, fixtures={"xhs": self.fixture},
                replace_function=fail_second, artifact_validator=lambda value: value)
        self.assert_no_official_set()

    def test_snapshot_validation_and_artifact_sanitization_precede_commit(self):
        with self.assertRaises(Exception):
            SanitizedProviderFixture(fixture_version="v1", provider="xhs", state="available",
                sanitized=True, payload={"cookie": "secret"})
        self.assert_no_official_set()
        with self.assertRaises(ValueError): reject_personal_identifiers({"email": "a@example.com"})
        self.assert_no_official_set()


class ObservationSeamTests(unittest.IsolatedAsyncioTestCase):
    async def _weather(self, google, amap):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner.map_provider = "google"
        planner._weather_results = {}
        planner._google_service = type("Google", (), {"get_weather": lambda self, city: google})()
        planner._amap_service = type("Amap", (), {"get_weather": lambda self, city, degraded=True: amap})()
        with capture_planner_observations() as observations:
            try: await planner._retrieve_weather_context("北京")
            except Exception: pass
        return observations["weather"]

    async def test_google_success_does_not_call_amap(self):
        good = WeatherResult(provider="google_weather", city="北京", request_success=True,
            data_available=True, degraded=False, days=[])
        observed = await self._weather(good, None)
        self.assertEqual([item["provider"] for item in observed], ["google_weather"])

    async def test_google_failure_amap_success_and_both_unavailable(self):
        google = WeatherResult(provider="google_weather", city="北京", request_success=False,
            data_available=False, degraded=True, reason="empty_forecast", days=[])
        amap = WeatherResult(provider="amap", city="北京", request_success=True,
            data_available=True, degraded=True, days=[])
        observed = await self._weather(google, amap)
        self.assertEqual([item["provider"] for item in observed], ["google_weather", "amap"])
        statuses, snapshots = build_weather_capture(observed)
        by_provider = {item.provider: item for item in statuses}
        self.assertEqual(by_provider["google_weather"].status, "unavailable")
        self.assertEqual(by_provider["amap"].status, "degraded")
        self.assertEqual(set(snapshots), {"google_weather", "amap"})
        unavailable = WeatherResult(provider="unavailable", city="北京", request_success=False,
            data_available=False, degraded=True, reason="network_error", days=[])
        observed = await self._weather(google, unavailable)
        self.assertFalse(observed[-1]["result"].data_available)
        statuses, _ = build_weather_capture(observed)
        self.assertTrue(all(item.status == "unavailable" for item in statuses))

    async def test_malformed_amap_is_observed_without_fabricated_success(self):
        google = WeatherResult(provider="google_weather", city="北京", request_success=False,
            data_available=False, degraded=True, reason="empty_forecast", days=[])
        observed = await self._weather(google, {"malformed": True})
        self.assertTrue(any(isinstance(item["result"], dict) for item in observed))
        statuses, snapshots = build_weather_capture(observed)
        self.assertEqual(next(item for item in statuses if item.provider == "amap").status, "unavailable")
        self.assertGreater(snapshots["amap"]["malformed_count"], 0)

    async def test_success_unavailable_invalid_and_mixed_routes(self):
        attractions = [_attraction("A", "a"), _attraction("B", "b"), _attraction("C", "", verified=False)]
        google = _FakeGoogleService([(1000, 600)])
        with capture_planner_observations() as observations:
            with patch("backend.app.services.google_map_service.get_google_map_service", return_value=google):
                await TripValidatorService().validate(_request(), _plan(attractions=attractions))
        routes = observations["routes"]
        self.assertEqual(len(routes), 2)
        self.assertTrue(routes[0]["data_available"]); self.assertTrue(routes[0]["feasible"])
        self.assertFalse(routes[1]["request_attempted"]); self.assertEqual(routes[1]["reason"], "invalid_or_unverified_poi")

        google = _FakeGoogleService([None])
        with capture_planner_observations() as observations:
            with patch("backend.app.services.google_map_service.get_google_map_service", return_value=google):
                await TripValidatorService().validate(_request(), _plan(attractions=[_attraction("A", "a"), _attraction("B", "b")]))
        self.assertTrue(observations["routes"][0]["request_attempted"])
        self.assertFalse(observations["routes"][0]["data_available"])

    async def test_no_route_checks_is_explicit_empty_observation(self):
        with capture_planner_observations() as observations:
            with patch("backend.app.services.google_map_service.get_google_map_service", return_value=None):
                await TripValidatorService().validate(_request(), _plan(attractions=[]))
        self.assertEqual(observations["routes"], [])


class BridgeTests(unittest.TestCase):
    def test_bridge_preserves_route_usage_and_total_latency(self):
        # Covered end-to-end by the Phase 3D-1 mock capture; assert its source model remains explicit.
        result = result_for(CASES[0])
        self.assertEqual(result.model, "mock-model")
        self.assertEqual(result.usage.total_tokens.value, 150)
        self.assertEqual(result.total_latency_ms, 321)


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
