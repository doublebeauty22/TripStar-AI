import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.models.schemas import Location, POIInfo
from backend.app.services.google_map_service import GoogleMapService


def _poi(address, *, name="Civic Museum", place_id="candidate-id"):
    return POIInfo(
        id=place_id,
        name=name,
        address=address,
        type="museum,tourist_attraction",
        location=Location(longitude=151.2, latitude=-33.8),
        data_source="google_places",
        verification_status="verified",
    )


def _identity(*names, place_id="trusted-city-id"):
    return SimpleNamespace(place_id=place_id, names=frozenset(names))


class GoogleCityIdentityPhase1Tests(unittest.TestCase):
    def setUp(self):
        self.service = GoogleMapService("fake-key")

    def tearDown(self):
        self.service.close()

    def test_chinese_requested_city_matches_trusted_english_identity(self):
        self.service.search_poi = Mock(return_value=[_poi("Sample City Region")])
        with patch.object(
            self.service, "_resolve_city_identity",
            return_value=_identity("samplecity"),
        ):
            result = self.service.match_poi("Civic Museum", "示例城")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["evidence"]["search_calls"], 1)
        self.assertEqual(result["evidence"]["city_match_path"], "trusted_city_name")

    def test_chinese_requested_city_matches_trusted_japanese_identity(self):
        self.service.search_poi = Mock(return_value=[_poi("サンプル市中央区")])
        with patch.object(
            self.service, "_resolve_city_identity",
            return_value=_identity("サンプル"),
        ):
            result = self.service.match_poi("Civic Museum", "示例城")
        self.assertEqual(result["status"], "verified")

    def test_later_language_address_recovers_initial_mismatch_and_order_is_fixed(self):
        addresses = {
            "zh-CN": "Unrelated Local Address",
            "ja": "別地域",
            "en": "Sample City Region",
        }
        languages = []

        def search(*_args, language_code="", **_kwargs):
            languages.append(language_code)
            return [_poi(addresses[language_code])]

        self.service.search_poi = Mock(side_effect=search)
        with patch.object(
            self.service, "_resolve_city_identity",
            return_value=_identity("samplecity"),
        ):
            result = self.service.match_poi("Civic Museum", "示例城")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(languages, ["zh-CN", "ja", "en"])
        self.assertEqual(result["evidence"]["search_calls"], 3)

    def test_all_distinct_address_variants_are_used_and_duplicates_deduplicate(self):
        addresses = {
            "zh-CN": "First Region",
            "ja": "第一地域",
            "en": "Sample City Region",
        }

        def search(*_args, language_code="", **_kwargs):
            return [_poi(addresses[language_code])]

        self.service.search_poi = Mock(side_effect=search)
        original = self.service._city_consistent
        observed_addresses = []

        def observe(city, address, **kwargs):
            observed_addresses.append(address)
            return original(city, address, **kwargs)

        with patch.object(self.service, "_city_consistent", side_effect=observe), patch.object(
            self.service, "_resolve_city_identity", return_value=_identity("samplecity"),
        ):
            self.service.match_poi("Civic Museum", "示例城")
        self.assertTrue(set(addresses.values()).issubset(set(observed_addresses)))

        self.service.search_poi = Mock(return_value=[_poi("Same Address")])
        observed_addresses.clear()
        with patch.object(self.service, "_city_consistent", side_effect=observe), patch.object(
            self.service, "_resolve_city_identity", return_value=_identity("missingcity"),
        ):
            self.service.match_poi("Civic Museum", "示例城")
        self.assertEqual(set(observed_addresses), {"Same Address"})

    def test_bounded_administrative_name_normalization(self):
        normalize = self.service._normalize_city_name
        self.assertEqual(normalize("示例市"), normalize("示例"))
        self.assertEqual(normalize("東示例都"), normalize("東示例"))
        self.assertEqual(normalize("西示例府"), normalize("西示例"))
        self.assertEqual(normalize("Sample City"), "sample")
        self.assertEqual(normalize("City of Sample"), "sample")
        self.assertEqual(normalize("Sample Metropolis"), "sample")
        self.assertEqual(normalize("京都"), "京都")

    def test_short_administrative_names_do_not_collapse_to_one_character(self):
        normalize = self.service._normalize_city_name
        for value in ("城市", "都市", "首府"):
            with self.subTest(value=value):
                self.assertEqual(normalize(value), value)
                self.assertGreaterEqual(len(normalize(value)), 2)

    def test_suffix_normalization_does_not_make_distinct_cities_equivalent(self):
        normalize = self.service._normalize_city_name
        self.assertNotEqual(normalize("城市"), normalize("城都"))
        self.assertNotEqual(normalize("首府"), normalize("首市"))

    def test_existing_tokyo_aliases_still_match(self):
        for requested, address in (
            ("东京", "東京都墨田区"),
            ("東京", "Tokyo, Japan"),
            ("tokyo", "東京都新宿区"),
        ):
            with self.subTest(requested=requested, address=address):
                self.assertTrue(self.service._city_consistent(requested, address))

    def test_wrong_city_shared_prefecture_and_ward_fail_closed(self):
        for address, containing in (
            ("Other City Shared Prefecture", {"shared-prefecture-id"}),
            ("Example Ward Shared Prefecture", {"ward-id", "shared-prefecture-id"}),
        ):
            with self.subTest(address=address):
                self.assertFalse(self.service._city_consistent(
                    "Requested City",
                    address,
                    trusted_city_names={"requested"},
                    requested_city_place_id="requested-city-id",
                    containing_place_ids=containing,
                ))

    def test_exact_direct_containing_place_id_is_unchanged(self):
        self.assertTrue(self.service._city_consistent(
            "Requested City",
            "Suburb Shared Prefecture",
            requested_city_place_id="requested-city-id",
            containing_place_ids={"requested-city-id", "ward-id"},
        ))


class TrustedCityIdentityResolutionTests(unittest.TestCase):
    def setUp(self):
        self.service = GoogleMapService("fake-key")

    def tearDown(self):
        self.service.close()

    @staticmethod
    def _response(payload):
        return type("Response", (), {
            "raise_for_status": lambda _self: None,
            "json": lambda _self: payload,
        })()

    def _set_payload(self, payload):
        self.service._client = type("Client", (), {
            "get": lambda *_args, **_kwargs: self._response(payload),
            "close": lambda _self: None,
        })()

    @staticmethod
    def _city_result(place_id="trusted-city-id", result_type="locality", name="Sample City"):
        return {
            "place_id": place_id,
            "types": [result_type, "political"],
            "address_components": [{
                "long_name": name,
                "short_name": name,
                "types": [result_type, "political"],
            }],
        }

    def test_valid_identity_uses_only_city_components(self):
        self._set_payload({"results": [{
            "place_id": "trusted-city-id",
            "types": ["locality", "political"],
            "address_components": [
                {"long_name": "Sample City", "short_name": "Sample",
                 "types": ["locality", "political"]},
                {"long_name": "Shared State", "short_name": "SS",
                 "types": ["administrative_area_level_1", "political"]},
                {"long_name": "Country", "short_name": "CC",
                 "types": ["country", "political"]},
            ],
        }]})
        identity = self.service._resolve_city_identity("示例城")
        self.assertEqual(identity.place_id, "trusted-city-id")
        self.assertEqual(identity.names, frozenset({"sample"}))

    def test_malformed_unavailable_ambiguous_and_wrong_level_fail_closed(self):
        payloads = (
            {},
            {"results": []},
            {"results": [{}, {}]},
            {"results": [{
                "place_id": "state-id",
                "types": ["administrative_area_level_1", "political"],
                "address_components": [{
                    "long_name": "Shared State", "short_name": "SS",
                    "types": ["administrative_area_level_1", "political"],
                }],
            }]},
            {"results": [{
                "place_id": "city-id", "types": ["locality"],
                "address_components": "malformed",
            }]},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                self._set_payload(payload)
                self.assertIsNone(self.service._resolve_city_identity("示例城"))

    def test_one_valid_locality_among_irrelevant_results_is_accepted(self):
        self._set_payload({"results": [
            self._city_result(),
            {"place_id": "route-id", "types": ["route"], "address_components": []},
        ]})
        identity = self.service._resolve_city_identity("示例城")
        self.assertEqual(identity.place_id, "trusted-city-id")
        self.assertEqual(identity.result_type, "locality")

    def test_one_valid_postal_town_among_irrelevant_results_is_accepted(self):
        self._set_payload({"results": [
            {"place_id": "country-id", "types": ["country"], "address_components": []},
            self._city_result(result_type="postal_town"),
        ]})
        identity = self.service._resolve_city_identity("示例城")
        self.assertEqual(identity.place_id, "trusted-city-id")
        self.assertEqual(identity.result_type, "postal_town")

    def test_conflicting_valid_city_identities_fail_closed(self):
        self._set_payload({"results": [
            self._city_result(place_id="city-a", name="City A"),
            self._city_result(place_id="city-b", name="City B"),
        ]})
        self.assertIsNone(self.service._resolve_city_identity("示例城"))

    def test_duplicate_equivalent_city_identities_are_deduplicated(self):
        self._set_payload({"results": [
            self._city_result(name="Sample City"),
            self._city_result(name="Sample Metropolis"),
        ]})
        identity = self.service._resolve_city_identity("示例城")
        self.assertEqual(identity.place_id, "trusted-city-id")
        self.assertEqual(identity.names, frozenset({"sample"}))

    def test_malformed_result_is_ignored_when_one_valid_identity_remains(self):
        self._set_payload({"results": [
            {"place_id": "malformed", "types": "locality", "address_components": {}},
            self._city_result(),
        ]})
        identity = self.service._resolve_city_identity("示例城")
        self.assertEqual(identity.place_id, "trusted-city-id")

    def test_zero_valid_city_level_results_fail_closed(self):
        self._set_payload({"results": [
            {"place_id": "state-id", "types": ["administrative_area_level_1"],
             "address_components": []},
            {"place_id": "ward-id", "types": ["sublocality_level_1"],
             "address_components": []},
        ]})
        self.assertIsNone(self.service._resolve_city_identity("示例城"))


if __name__ == "__main__":
    unittest.main()
