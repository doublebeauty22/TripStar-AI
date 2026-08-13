import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.models.schemas import (
    Attraction, DayPlan, Location, POIInfo, TripPlan,
    has_valid_verified_coordinates,
)
from backend.app.services.google_map_service import GoogleMapService


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FakeClient:
    def __init__(self, *, post_payload=None, get_payloads=None):
        self.post_payload = post_payload or {}
        self.get_payloads = list(get_payloads or [])
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _FakeResponse(self.post_payload)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return _FakeResponse(self.get_payloads.pop(0))

    def close(self):
        return None


def _poi(name="浅草寺"):
    return POIInfo(
        id="place-asakusa",
        name=name,
        type="tourist_attraction",
        address="2 Chome-3-1 Asakusa, Taito City, Tokyo",
        location=Location(longitude=139.7967, latitude=35.7148),
        rating=4.5,
        user_rating_count=80000,
        photo_name="places/place-asakusa/photos/photo-1",
        photo_attributions=[{"displayName": "Test Photographer", "uri": "https://example.com"}],
    )


def _candidate(place_id, name, address, types, longitude, latitude):
    return POIInfo(
        id=place_id,
        name=name,
        type=",".join(types),
        address=address,
        location=Location(longitude=longitude, latitude=latitude),
        photo_name=f"places/{place_id}/photos/photo-1",
    )


class GoogleMapServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = GoogleMapService("test-key")

    def tearDown(self):
        self.service.close()

    def test_text_search_maps_place_facts_and_photo_metadata(self):
        self.service._client = _FakeClient(post_payload={"places": [{
            "id": "place-asakusa",
            "displayName": {"text": "浅草寺"},
            "formattedAddress": "Tokyo address",
            "location": {"longitude": 139.7967, "latitude": 35.7148},
            "types": ["tourist_attraction"],
            "rating": 4.5,
            "userRatingCount": 80000,
            "photos": [{
                "name": "places/place-asakusa/photos/photo-1",
                "authorAttributions": [{"displayName": "Photographer"}],
            }],
        }]})

        results = self.service.search_poi("浅草寺", "东京")

        self.assertEqual(results[0].id, "place-asakusa")
        self.assertEqual(results[0].location.latitude, 35.7148)
        self.assertEqual(results[0].photo_name, "places/place-asakusa/photos/photo-1")
        field_mask = self.service._client.calls[0][2]["headers"]["X-Goog-FieldMask"]
        self.assertIn("places.photos", field_mask)

    def test_canonical_coordinate_validity(self):
        valid = [
            {"longitude": 12.5, "latitude": 45.5},
            {"longitude": 0, "latitude": 45.5},
            {"longitude": 12.5, "latitude": 0},
            {"longitude": "12.5", "latitude": "45.5"},
            {"longitude": -180, "latitude": -90},
            {"longitude": 180, "latitude": 90},
        ]
        invalid = [
            None, {}, {"longitude": 12.5}, {"latitude": 45.5},
            {"longitude": "bad", "latitude": 45.5},
            {"longitude": 12.5, "latitude": "bad"},
            {"longitude": float("nan"), "latitude": 45.5},
            {"longitude": 12.5, "latitude": float("inf")},
            {"longitude": 181, "latitude": 45.5},
            {"longitude": 12.5, "latitude": 91},
            {"longitude": 0, "latitude": 0},
            {"longitude": True, "latitude": 45.5},
        ]
        for location in valid:
            with self.subTest(valid=location):
                self.assertTrue(has_valid_verified_coordinates(location))
        for location in invalid:
            with self.subTest(invalid=location):
                self.assertFalse(has_valid_verified_coordinates(location))

    def test_text_search_skips_missing_or_malformed_locations(self):
        locations = [
            None, "wrong-type", {}, {"longitude": 12.5}, {"latitude": 45.5},
            {"longitude": "not-number", "latitude": 45.5},
            {"longitude": 12.5, "latitude": "not-number"},
            {"longitude": float("nan"), "latitude": 45.5},
            {"longitude": 12.5, "latitude": float("inf")},
            {"longitude": 181, "latitude": 45.5},
            {"longitude": 12.5, "latitude": 91},
            {"longitude": 0, "latitude": 0},
        ]
        for location in locations:
            with self.subTest(location=location):
                self.service._client = _FakeClient(post_payload={"places": [{
                    "id": "synthetic-id",
                    "displayName": {"text": "Synthetic Landmark"},
                    "formattedAddress": "Synthetic City",
                    "location": location,
                    "types": ["tourist_attraction"],
                }]})
                self.assertEqual(
                    self.service.search_poi("Synthetic Landmark", "Synthetic City"), []
                )

    def test_strong_name_match_with_invalid_coordinates_is_not_verified(self):
        candidate = _candidate(
            "synthetic-id", "Synthetic Landmark", "Synthetic City",
            ["tourist_attraction"], 0, 0,
        )
        with patch.object(self.service, "search_poi", return_value=[candidate]):
            match = self.service.match_poi("Synthetic Landmark", "Synthetic City")
        self.assertNotEqual(match["status"], "verified")
        self.assertFalse(match["evidence"]["coordinate_valid"])

    def test_match_status_thresholds_are_deterministic(self):
        with patch.object(self.service, "search_poi", return_value=[_poi("浅草寺")]):
            self.assertEqual(self.service.match_poi("浅草寺", "东京")["status"], "verified")
        with patch.object(self.service, "search_poi", return_value=[_poi("浅草文化观光中心")]):
            self.assertEqual(self.service.match_poi("浅草文化中心", "东京")["status"], "partial_match")
        with patch.object(self.service, "search_poi", return_value=[_poi("东京迪士尼乐园")]):
            self.assertEqual(self.service.match_poi("浅草寺", "东京")["status"], "unverified")

    def test_tokyo_skytree_alias_matches_main_entity(self):
        with patch.object(self.service, "search_poi", return_value=[_poi("东京天空树")]):
            match = self.service.match_poi("东京晴空塔", "东京")
        self.assertEqual(match["status"], "verified")
        self.assertEqual(match["score"], 1.0)

    def test_tokyo_skytree_related_facility_is_not_verified(self):
        with patch.object(self.service, "search_poi", return_value=[_poi("东京晴空塔东塔")]):
            match = self.service.match_poi("东京晴空塔", "东京")
        self.assertNotEqual(match["status"], "verified")
        self.assertLess(match["score"], 0.6)

    def test_shibuya_crossing_cross_language_alias_is_verified(self):
        with patch.object(self.service, "search_poi", return_value=[_poi("Shibuya Crossing")]):
            match = self.service.match_poi("涩谷十字路口", "东京")
        self.assertEqual(match["status"], "verified")
        self.assertEqual(match["score"], 1.0)

    def test_ameyoko_shopping_street_is_not_treated_as_store_facility(self):
        localized_names = {
            "zh-CN": "阿美横商店街",
            "ja": "アメ横商店街",
            "en": "Ameyoko market",
        }

        def localized(*_args, language_code="zh-CN", **_kwargs):
            return [_candidate(
                "ameyoko", localized_names[language_code],
                "6-chome-10-7 Ueno, Taito City, Tokyo",
                ["tourist_attraction", "market", "point_of_interest"],
                139.7745, 35.7101,
            )]

        with patch.object(self.service, "search_poi", side_effect=localized):
            match = self.service.match_poi("阿美横丁", "东京")

        self.assertEqual(match["status"], "verified")
        self.assertEqual(match["poi"].id, "ameyoko")
        self.assertTrue(match["evidence"]["scope_compatible"])

    def test_ameyoko_english_market_alias_is_verified(self):
        market = _candidate(
            "ameyoko", "Ameyoko market", "Ueno, Taito City, Tokyo",
            ["tourist_attraction", "market"], 139.7745, 35.7101,
        )
        with patch.object(self.service, "search_poi", return_value=[market]):
            match = self.service.match_poi("阿美横丁", "东京")
        self.assertEqual(match["status"], "verified")

    def test_store_and_other_facilities_remain_scope_conflicts(self):
        facility_names = (
            "Ameyoko Store",
            "Ameyoko Mall",
            "Ameyoko East Tower",
            "Ameyoko West Tower",
            "Ameyoko Observation Deck",
            "Ameyoko Station",
            "Ameyoko Plaza",
        )
        for facility_name in facility_names:
            candidate = _candidate(
                facility_name, facility_name, "Ueno, Taito City, Tokyo",
                ["point_of_interest", "establishment"], 139.7745, 35.7101,
            )
            with self.subTest(candidate=facility_name), patch.object(
                self.service, "search_poi", return_value=[candidate]
            ):
                match = self.service.match_poi("阿美横丁", "东京")
                self.assertNotEqual(match["status"], "verified")

    def test_skytree_town_composes_reviewed_main_alias_with_generic_town_scope(self):
        localized_names = {
            "zh-CN": "Tokyo Skytree Town",
            "ja": "東京スカイツリータウン",
            "en": "Tokyo Skytree Town",
        }

        def localized(*_args, language_code="zh-CN", **_kwargs):
            return [_candidate(
                "skytree-town", localized_names[language_code],
                "1-chome-1-1 Oshiage, Sumida City, Tokyo",
                ["tourist_attraction", "point_of_interest"], 139.8106, 35.7100,
            )]

        with patch.object(self.service, "search_poi", side_effect=localized):
            match = self.service.match_poi("东京晴空塔城", "东京")

        self.assertEqual(match["status"], "verified")
        self.assertEqual(match["poi"].id, "skytree-town")
        self.assertTrue(match["evidence"]["scope_compatible"])

    def test_water_transport_pier_terms_normalize_to_one_entity_category(self):
        for requested_name in (
            "Asakusa码头", "Asakusa船着场", "Asakusa桟橋", "Asakusa water bus pier"
        ):
            candidate = _candidate(
                "asakusa-pier", "Asakusa Pier", "Hanakawado, Taito City, Tokyo",
                ["ferry_terminal", "transportation_service"], 139.7987, 35.7107,
            )
            with self.subTest(requested=requested_name), patch.object(
                self.service, "search_poi", return_value=[candidate]
            ):
                match = self.service.match_poi(requested_name, "东京")
                self.assertEqual(match["status"], "verified")
                self.assertEqual(match["poi"].id, "asakusa-pier")

    def test_water_bus_pier_uses_multilingual_and_address_evidence(self):
        names = {
            "zh-CN": "Tokyo Cruise Asakusa Pier",
            "ja": "東京クルーズ 浅草乗り場（TOKYO CRUISE）",
            "en": "Tokyo Cruise Asakusa Pier",
        }
        pier = _candidate(
            "asakusa-pier", names["zh-CN"], "1-1-1 Hanakawado, Taito City, Tokyo",
            ["point_of_interest", "establishment"], 139.7987, 35.7107,
        )
        runner = _candidate(
            "asakusa-terminal", "Asakusa", "1-2-7 Hanakawado, Taito City, Tokyo",
            ["ferry_terminal", "transit_station"], 139.7995, 35.7115,
        )

        def localized(*_args, language_code="zh-CN", **_kwargs):
            return [pier.model_copy(update={"name": names[language_code]}), runner]

        with patch.object(self.service, "search_poi", side_effect=localized), patch.object(
            self.service, "geocode", return_value=Location(longitude=139.7987, latitude=35.7107)
        ):
            match = self.service.match_poi(
                "隅田川水上巴士浅草码头", "东京", "台东区花川户1-1-1"
            )

        self.assertEqual(match["status"], "verified")
        self.assertEqual(match["poi"].id, "asakusa-pier")
        self.assertEqual(match["evidence"]["top_language_count"], 3)

    def test_pier_does_not_match_unrelated_terminal_station_parking_or_office(self):
        incompatible = (
            ("airport", "Haneda Airport Terminal", ["airport", "airport_terminal"]),
            ("station", "Asakusa Train Station", ["train_station", "transit_station"]),
            ("parking", "Asakusa Pier Parking", ["parking_lot"]),
            ("office", "Tokyo Cruise Asakusa Office", ["corporate_office"]),
        )
        for place_id, candidate_name, types in incompatible:
            candidate = _candidate(
                place_id, candidate_name, "Taito City, Tokyo", types, 139.7987, 35.7107,
            )
            with self.subTest(candidate=candidate_name), patch.object(
                self.service, "search_poi", return_value=[candidate]
            ):
                match = self.service.match_poi("浅草水上巴士码头", "东京")
                self.assertNotEqual(match["status"], "verified")

    def test_sumida_park_uses_multilingual_name_and_address_to_choose_taito_side(self):
        taito = _candidate(
            "sumida-taito", "Sumida Park", "Hanakawado, Taito City, Tokyo",
            ["park", "point_of_interest"], 139.8010, 35.7140,
        )
        sumida = _candidate(
            "sumida-sumida", "Sumida Park", "Mukojima, Sumida City, Tokyo",
            ["park", "point_of_interest"], 139.8070, 35.7140,
        )

        def localized(*_args, language_code="zh-CN", **_kwargs):
            if language_code == "ja":
                return [taito.model_copy(update={"name": "隅田公園"}), sumida.model_copy(update={"name": "隅田公園"})]
            return [taito, sumida]

        with patch.object(self.service, "search_poi", side_effect=localized), patch.object(
            self.service, "geocode", return_value=Location(longitude=139.8011, latitude=35.7141)
        ):
            match = self.service.match_poi("隅田公园", "东京", "东京都台东区花川户1-1")

        self.assertEqual(match["status"], "verified")
        self.assertEqual(match["poi"].id, "sumida-taito")
        self.assertGreaterEqual(match["evidence"]["address_score"], 0.55)

    def test_shibuya_sky_uses_multilingual_consensus_without_poi_alias(self):
        sky = _candidate(
            "shibuya-sky", "Shibuya Sky", "2-24-12 Shibuya, Shibuya City, Tokyo",
            ["observation_deck", "tourist_attraction"], 139.7038, 35.6585,
        )
        with patch.object(self.service, "search_poi", return_value=[sky]), patch.object(
            self.service, "geocode", return_value=Location(longitude=139.7039, latitude=35.6586)
        ):
            match = self.service.match_poi("涩谷天空", "东京", "涩谷区涩谷2-24-12")

        self.assertEqual(match["status"], "verified")
        self.assertEqual(match["poi"].id, "shibuya-sky")
        self.assertEqual(match["evidence"]["path"], "multilingual_consensus")

    def test_ueno_common_name_matches_official_park_name(self):
        park = _candidate(
            "ueno-park", "上野恩赐公园", "4 Uenokoen, Taito City, Tokyo",
            ["park", "state_park"], 139.7730, 35.7148,
        )
        with patch.object(self.service, "search_poi", return_value=[park]), patch.object(
            self.service, "geocode", return_value=Location(longitude=139.7731, latitude=35.7149)
        ):
            match = self.service.match_poi("上野公园", "东京", "东京都台东区上野公园5-20")

        self.assertEqual(match["status"], "verified")
        self.assertEqual(match["poi"].id, "ueno-park")

    def test_tokyo_station_building_rejects_station_and_plaza(self):
        building = _candidate(
            "marunouchi-building", "Tokyo Station Marunouchi Building",
            "1-9-1 Marunouchi, Chiyoda City, Tokyo", ["tourist_attraction"], 139.7650, 35.6810,
        )
        station = _candidate(
            "tokyo-station", "东京站", "1-9 Marunouchi, Chiyoda City, Tokyo",
            ["transit_station", "train_station"], 139.7670, 35.6812,
        )
        plaza = _candidate(
            "station-plaza", "东京站丸之内站前广场", "1-9 Marunouchi, Chiyoda City, Tokyo",
            ["tourist_attraction", "park"], 139.7645, 35.6812,
        )

        def localized(*_args, language_code="zh-CN", **_kwargs):
            if language_code == "ja":
                return [building.model_copy(update={"name": "東京駅丸の内駅舎"}), plaza, station]
            return [building, plaza, station]

        with patch.object(self.service, "search_poi", side_effect=localized), patch.object(
            self.service, "geocode", return_value=Location(longitude=139.7650, latitude=35.6810)
        ):
            match = self.service.match_poi("东京站丸之内站舍", "东京", "千代田区丸之内1-9-1")

        self.assertEqual(match["status"], "verified")
        self.assertEqual(match["poi"].id, "marunouchi-building")
        self.assertNotEqual(match["poi"].id, "tokyo-station")
        self.assertNotEqual(match["poi"].id, "station-plaza")

    def test_same_name_in_another_city_cannot_be_verified(self):
        wrong_city = _candidate(
            "wrong-city", "浅草寺", "Osaka, Japan", ["tourist_attraction"], 135.5, 34.7,
        )
        with patch.object(self.service, "search_poi", return_value=[wrong_city]):
            match = self.service.match_poi("浅草寺", "东京")
        self.assertNotEqual(match["status"], "verified")

    def test_type_conflict_cannot_be_verified(self):
        station = _candidate(
            "park-station", "上野公园", "Tokyo", ["transit_station", "train_station"], 139.7, 35.7,
        )
        with patch.object(self.service, "search_poi", return_value=[station]):
            match = self.service.match_poi("上野公园", "东京")
        self.assertNotEqual(match["status"], "verified")

    def test_skytree_town_and_observation_deck_are_not_main_tower(self):
        for candidate_name in ("Tokyo Skytree Town", "Tokyo Skytree Observation Deck"):
            candidate = _candidate(
                candidate_name, candidate_name, "Sumida City, Tokyo",
                ["tourist_attraction", "observation_deck"], 139.81, 35.71,
            )
            with self.subTest(candidate=candidate_name), patch.object(
                self.service, "search_poi", return_value=[candidate]
            ):
                match = self.service.match_poi("东京晴空塔", "东京")
                self.assertNotEqual(match["status"], "verified")

    def test_place_photo_uses_details_then_media(self):
        self.service._client = _FakeClient(get_payloads=[
            {"photos": [{
                "name": "places/place-asakusa/photos/photo-1",
                "authorAttributions": [{"displayName": "Photographer"}],
            }]},
            {"photoUri": "https://lh3.googleusercontent.com/photo"},
        ])

        result = self.service.get_place_photo(place_id="place-asakusa", name="浅草寺")

        self.assertEqual(result["photo_url"], "https://lh3.googleusercontent.com/photo")
        self.assertEqual(len(self.service._client.calls), 2)
        self.assertEqual(self.service._client.calls[1][2]["params"]["skipHttpRedirect"], "true")

    def test_partial_candidate_is_used_for_photo_only(self):
        partial = _poi("浅草文化观光中心")
        self.service._client = _FakeClient(get_payloads=[
            {"photoUri": "https://lh3.googleusercontent.com/partial-photo"},
        ])
        with patch.object(self.service, "match_poi", return_value={
            "status": "partial_match", "score": 0.7, "poi": partial,
        }):
            result = self.service.get_place_photo(name="浅草文化中心", city="东京")

        self.assertEqual(result["photo_url"], "https://lh3.googleusercontent.com/partial-photo")
        self.assertEqual(result["match_status"], "partial_match")
        self.assertEqual(result["place_id"], "place-asakusa")
        self.assertEqual(len(self.service._client.calls), 1)

    def test_verified_place_id_photo_path_does_not_text_search(self):
        self.service._client = _FakeClient(get_payloads=[
            {"photos": [{"name": "places/place-asakusa/photos/photo-1"}]},
            {"photoUri": "https://lh3.googleusercontent.com/photo"},
        ])
        with patch.object(self.service, "search_poi", side_effect=AssertionError("must not search")):
            result = self.service.get_place_photo(place_id="place-asakusa", name="浅草寺")

        self.assertTrue(result["photo_url"])
        self.assertEqual(result["match_status"], "verified")

    def test_route_distance_and_duration_are_map_api_facts(self):
        self.service._client = _FakeClient(get_payloads=[{
            "routes": [{"legs": [{
                "distance": {"value": 1800, "text": "1.8 km"},
                "duration": {"value": 1500, "text": "25 mins"},
                "steps": [],
            }]}],
        }])

        route = self.service.plan_route("浅草寺", "东京晴空塔", "东京", "东京", "walking")

        self.assertEqual(route["distance"], 1800)
        self.assertEqual(route["duration"], 1500)
        self.assertEqual(route["data_source"], "google_directions")


class PoiEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_match_overwrites_only_map_facts(self):
        from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        plan = TripPlan(
            city="东京",
            start_date="2026-10-01",
            end_date="2026-10-01",
            days=[DayPlan(
                date="2026-10-01",
                day_index=0,
                city="东京",
                description="test",
                transportation="步行",
                accommodation="酒店",
                attractions=[Attraction(
                    name="浅草寺",
                    address="LLM address",
                    location=Location(longitude=1, latitude=1),
                    visit_duration=120,
                    description="test",
                )],
                meals=[],
            )],
            weather_info=[],
            overall_suggestions="test",
        )
        service = SimpleNamespace(match_poi=lambda *_args: {
            "status": "verified", "score": 1.0, "poi": _poi(),
        })

        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ):
            result = await planner._enrich_trip_plan_pois(plan)

        attraction = result.days[0].attractions[0]
        self.assertEqual(attraction.name, "浅草寺")
        self.assertEqual(attraction.place_id, "place-asakusa")
        self.assertEqual(attraction.address, _poi().address)
        self.assertEqual(attraction.map_data_source, "google_places")

    async def test_partial_match_keeps_original_address_and_coordinates(self):
        from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        attraction = Attraction(
            name="浅草文化中心",
            address="original address",
            location=Location(longitude=10, latitude=20),
            visit_duration=60,
            description="test",
        )
        plan = TripPlan(
            city="东京", start_date="2026-10-01", end_date="2026-10-01",
            days=[DayPlan(
                date="2026-10-01", day_index=0, city="东京", description="test",
                transportation="步行", accommodation="酒店", attractions=[attraction], meals=[],
            )],
            weather_info=[], overall_suggestions="test",
        )
        service = SimpleNamespace(match_poi=lambda *_args: {
            "status": "partial_match", "score": 0.7, "poi": _poi("浅草文化观光中心"),
        })

        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ):
            result = await planner._enrich_trip_plan_pois(plan)

        enriched = result.days[0].attractions[0]
        self.assertEqual(enriched.address, "original address")
        self.assertEqual(enriched.location.longitude, 10)
        self.assertFalse(enriched.place_id)
        self.assertEqual(enriched.poi_match_status, "partial_match")
        self.assertEqual(enriched.map_data_source, "llm_unverified")

    async def test_invalid_coordinate_verified_match_cannot_enrich_map_facts(self):
        from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        attraction = Attraction(
            name="Synthetic Landmark", address="planner address",
            location=Location(longitude=0, latitude=0), visit_duration=60,
            description="test",
        )
        plan = TripPlan(
            city="Synthetic City", start_date="2026-10-01", end_date="2026-10-01",
            days=[DayPlan(
                date="2026-10-01", day_index=0, city="Synthetic City",
                description="test", transportation="walking", accommodation="hotel",
                attractions=[attraction], meals=[],
            )], weather_info=[], overall_suggestions="test",
        )
        invalid = _candidate(
            "synthetic-id", "Synthetic Landmark", "Synthetic City",
            ["tourist_attraction"], 0, 0,
        )
        service = SimpleNamespace(match_poi=lambda *_args: {
            "status": "verified", "score": 1.0, "poi": invalid,
        })
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ):
            result = await planner._enrich_trip_plan_pois(plan)
        enriched = result.days[0].attractions[0]
        self.assertEqual(enriched.poi_match_status, "unverified")
        self.assertEqual(enriched.map_data_source, "llm_unverified")
        self.assertFalse(enriched.place_id)
        self.assertFalse(enriched.poi_id)
        self.assertEqual((enriched.location.longitude, enriched.location.latitude), (0, 0))


if __name__ == "__main__":
    unittest.main()
