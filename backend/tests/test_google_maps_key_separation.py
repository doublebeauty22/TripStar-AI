"""Regression tests for backend-only Google Maps credentials."""

import asyncio
import unittest
from unittest.mock import patch
from pydantic import ValidationError

from backend.app import config
from backend.app.api.routes.settings import RuntimeSettingsPayload, get_settings as get_settings_route
from backend.app.services import google_map_service


class GoogleMapsKeySeparationTests(unittest.TestCase):
    def setUp(self):
        self.original_server_key = config.settings.google_maps_server_api_key

    def tearDown(self):
        config.settings.google_maps_server_api_key = self.original_server_key
        google_map_service.reset_google_map_service()

    def test_server_key_has_no_legacy_runtime_fallback(self):
        config.settings.google_maps_server_api_key = "server-key"
        self.assertEqual(config.get_google_maps_server_api_key(), "server-key")

    def test_google_map_service_uses_resolved_backend_key(self):
        google_map_service.reset_google_map_service()
        with patch.object(
            google_map_service, "get_google_maps_server_api_key", return_value="server-key"
        ):
            service = google_map_service.get_google_map_service()

        self.assertIsNotNone(service)
        self.assertEqual(service.api_key, "server-key")

    def test_settings_api_never_returns_google_credentials(self):
        config.settings.google_maps_server_api_key = "server-secret"

        response = asyncio.run(get_settings_route())
        self.assertNotIn("google_maps_server_api_key", response["data"])
        self.assertNotIn("google_maps_api_key", response["data"])
        self.assertNotIn("server-secret", str(response))

    def test_settings_payload_has_no_google_key_edit_field(self):
        for field in ("google_maps_api_key", "google_maps_server_api_key"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                RuntimeSettingsPayload.model_validate({field: "fake-secret"})


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
