"""Opt-in live Google Maps smoke test for the Phase 1.5 Tokyo demo.

Run explicitly after configuring GOOGLE_MAPS_SERVER_API_KEY:
RUN_GOOGLE_MAPS_E2E=1 backend/.venv/bin/python -m unittest \
  backend.tests.test_tokyo_google_e2e -v
"""

import os
import unittest

from backend.app.config import get_google_maps_server_api_key, get_settings
from backend.app.services.google_map_service import GoogleMapService


@unittest.skipUnless(
    os.getenv("RUN_GOOGLE_MAPS_E2E") == "1" and bool(get_google_maps_server_api_key()),
    "需要显式启用 RUN_GOOGLE_MAPS_E2E=1 并配置 GOOGLE_MAPS_SERVER_API_KEY",
)
class TokyoGoogleE2ETests(unittest.TestCase):
    def setUp(self):
        settings = get_settings()
        self.service = GoogleMapService(
            get_google_maps_server_api_key(),
            settings.google_maps_proxy,
        )

    def tearDown(self):
        self.service.close()

    def test_tokyo_places_photo_and_route(self):
        names = ["浅草寺", "东京晴空塔", "涩谷十字路口"]
        matches = [self.service.match_poi(name, "东京") for name in names]
        verified = [match for match in matches if match["status"] == "verified"]

        self.assertGreaterEqual(len(verified), 2)
        for match in verified:
            poi = match["poi"]
            self.assertTrue(poi.id)
            self.assertNotEqual(poi.location.latitude, 0)
            self.assertNotEqual(poi.location.longitude, 0)

        first_poi = verified[0]["poi"]
        photo = self.service.get_place_photo(
            place_id=first_poi.id,
            name=first_poi.name,
            city="东京",
        )
        self.assertTrue(photo["photo_url"])

        route = self.service.plan_route("浅草寺", "东京晴空塔", "东京", "东京", "walking")
        self.assertGreater(route.get("distance", 0), 0)
        self.assertGreater(route.get("duration", 0), 0)
        self.assertEqual(route.get("data_source"), "google_directions")


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
