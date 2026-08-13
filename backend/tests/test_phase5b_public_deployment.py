import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend.app import config
from backend.app.api.routes import demo, settings as settings_route, trip
from backend.app.services.public_demo_guard import public_demo_guard


class Phase5BPublicDeploymentTests(unittest.TestCase):
    def setUp(self):
        public_demo_guard.reset_for_tests()

    def test_example_is_valid_sanitized_and_eval_independent(self):
        demo.load_example_trip.cache_clear()
        payload = demo.load_example_trip()
        self.assertTrue(payload["example"])
        self.assertEqual(payload["schema_version"], "portfolio.example_trip.v1")
        rendered = json.dumps(payload).lower()
        for forbidden in ("golden_case", "reviewer", "baseline", "candidate", "raw_xhs"):
            self.assertNotIn(forbidden, rendered)
        self.assertNotIn("/eval/", str(demo._EXAMPLE_PATH))

    def test_production_settings_write_is_blocked(self):
        payload = settings_route.RuntimeSettingsPayload(openai_model="changed")
        with patch.object(config.settings, "app_env", "production"):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(settings_route.save_settings(payload))
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(
            raised.exception.detail["error"]["code"], "runtime_settings_read_only"
        )

    def test_public_history_is_disabled(self):
        with (
            patch.object(config.settings, "app_env", "production"),
            patch.object(config.settings, "public_history_enabled", False),
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(trip.get_trip_history())
        self.assertEqual(raised.exception.detail["error"]["code"], "feature_disabled")

    def test_generation_guard_has_capacity_and_cooldown(self):
        request_a = SimpleNamespace(client=SimpleNamespace(host="client-a"))
        request_b = SimpleNamespace(client=SimpleNamespace(host="client-b"))
        with (
            patch.object(config.settings, "app_env", "production"),
            patch.object(config.settings, "openai_api_key", "configured-for-test"),
            patch.object(config.settings, "public_max_concurrent_generations", 1),
            patch.object(config.settings, "public_generation_cooldown_seconds", 60),
        ):
            asyncio.run(public_demo_guard.reserve_generation(request_a, "task-a"))
            with self.assertRaises(HTTPException) as capacity:
                asyncio.run(public_demo_guard.reserve_generation(request_b, "task-b"))
            self.assertEqual(capacity.exception.status_code, 429)
            asyncio.run(public_demo_guard.release_generation("task-a"))
            with self.assertRaises(HTTPException) as cooldown:
                asyncio.run(public_demo_guard.reserve_generation(request_a, "task-c"))
            self.assertEqual(cooldown.exception.detail["error"]["code"], "rate_limited")

    def test_example_file_contains_no_secret_field_names(self):
        payload = Path(demo._EXAMPLE_PATH).read_text(encoding="utf-8").lower()
        for name in (
            "openai_api_key", "llm_api_key", "xhs_cookie",
            "google_maps_server_api_key", "amap_web_service_key",
        ):
            self.assertNotIn(name, payload)


if __name__ == "__main__":
    unittest.main()
