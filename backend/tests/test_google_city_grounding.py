import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.app.api.routes.poi import get_attraction_photo
from backend.app.models.schemas import Location, POIInfo
from backend.app.services.google_map_service import GoogleMapService


def _poi(
    *, place_id="candidate-id", name="Metro Beach", address="Suburb Region Country",
    types=("tourist_attraction",), longitude=151.0, latitude=-33.0,
):
    return POIInfo(
        id=place_id, name=name, type=",".join(types), address=address,
        location=Location(longitude=longitude, latitude=latitude),
        photo_name=f"places/{place_id}/photos/photo-1",
    )


class GoogleCityGroundingTests(unittest.TestCase):
    def setUp(self):
        self.service = GoogleMapService("fake-key")

    def tearDown(self):
        self.service.close()

    def _search(self, candidate, containing=()):
        def search(*_args, _containing_places=None, **_kwargs):
            if _containing_places is not None:
                _containing_places[candidate.id] = set(containing)
            return [candidate]
        return search

    def test_text_search_captures_trusted_containing_place_ids(self):
        captured = {}
        payload = {"places": [{
            "id": "candidate-id",
            "displayName": {"text": "Metro Beach"},
            "formattedAddress": "Suburb Region Country",
            "location": {"longitude": 151.0, "latitude": -33.0},
            "types": ["tourist_attraction"],
            "containingPlaces": [
                {"id": "trusted-parent-city-id"}, {"id": ""}, "malformed",
            ],
        }]}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return payload

        class Client:
            def post(self, _url, **kwargs):
                captured.update(kwargs)
                return Response()

            def close(self):
                return None

        self.service._client = Client()
        containing = {}
        results = self.service.search_poi(
            "Metro Beach", "Parent City", _containing_places=containing,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(containing, {"candidate-id": {"trusted-parent-city-id"}})
        self.assertIn(
            "places.containingPlaces", captured["headers"]["X-Goog-FieldMask"],
        )

    def test_city_geocode_returns_only_google_place_id(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": [{
                    "place_id": "trusted-parent-city-id",
                    "types": ["locality", "political"],
                    "address_components": [{
                        "long_name": "Parent City", "short_name": "Parent City",
                        "types": ["locality", "political"],
                    }],
                }]}

        self.service._client = type("Client", (), {
            "get": lambda *_args, **_kwargs: Response(),
            "close": lambda _self: None,
        })()
        identity = self.service._resolve_city_identity("PRIVATE_CITY")
        self.assertEqual(identity.place_id, "trusted-parent-city-id")
        self.assertEqual(identity.names, frozenset({"parent"}))

    def test_exact_city_match_still_verifies_without_corroboration_call(self):
        candidate = _poi(address="Parent City Region Country")
        with patch.object(self.service, "search_poi", side_effect=self._search(candidate)), patch.object(
            self.service, "_resolve_city_identity",
            side_effect=AssertionError("literal city match must not geocode"),
        ):
            match = self.service.match_poi("Metro Beach", "Parent City")
        self.assertEqual(match["status"], "verified")
        self.assertEqual(match["evidence"]["city_match_path"], "literal")

    def test_trusted_containing_place_allows_metropolitan_suburb(self):
        candidate = _poi()
        with patch.object(
            self.service, "search_poi",
            side_effect=self._search(candidate, {"trusted-parent-city-id"}),
        ), patch.object(
            self.service, "_resolve_city_identity", return_value=SimpleNamespace(
                place_id="trusted-parent-city-id", names=frozenset({"parentcity"}),
            ),
        ) as geocode:
            match = self.service.match_poi("Metro Beach", "Parent City")
        self.assertEqual(match["status"], "verified")
        self.assertEqual(match["evidence"]["city_match_path"], "containing_place")
        geocode.assert_called_once_with("Parent City")

    def test_same_name_wrong_region_stays_unverified(self):
        candidate = _poi(address="Other Region Other Country")
        with patch.object(
            self.service, "search_poi",
            side_effect=self._search(candidate, {"other-parent-id"}),
        ), patch.object(
            self.service, "_resolve_city_identity", return_value=SimpleNamespace(
                place_id="trusted-parent-city-id", names=frozenset({"parentcity"}),
            ),
        ):
            match = self.service.match_poi("Metro Beach", "Parent City")
        self.assertEqual(match["status"], "unverified")
        self.assertFalse(match["evidence"]["city_consistent"])

    def test_wrong_country_cannot_pass_containment(self):
        candidate = _poi(address="Other Country")
        with patch.object(
            self.service, "search_poi",
            side_effect=self._search(candidate, {"foreign-city-id"}),
        ), patch.object(
            self.service, "_resolve_city_identity", return_value=SimpleNamespace(
                place_id="requested-city-id", names=frozenset({"parentcity"}),
            ),
        ):
            match = self.service.match_poi("Metro Beach", "Parent City")
        self.assertEqual(match["status"], "unverified")

    def test_scope_type_coordinate_and_place_id_gates_remain_required(self):
        candidates = (
            _poi(name="Metro Beach Mall"),
            _poi(name="Metro Park", types=("train_station",)),
            _poi(longitude=0, latitude=0),
            _poi(place_id=""),
        )
        requested = ("Metro Beach", "Metro Park", "Metro Beach", "Metro Beach")
        for candidate, requested_name in zip(candidates, requested):
            with self.subTest(candidate=candidate.name), patch.object(
                self.service, "search_poi",
                side_effect=self._search(candidate, {"trusted-parent-city-id"}),
            ), patch.object(
                self.service, "_resolve_city_identity", return_value=SimpleNamespace(
                    place_id="trusted-parent-city-id", names=frozenset({"parentcity"}),
                ),
            ):
                match = self.service.match_poi(requested_name, "Parent City")
                self.assertNotEqual(match["status"], "verified")

    def test_ambiguous_candidate_rules_remain_unchanged(self):
        first = _poi(place_id="one", name="Metropolitan Beach East")
        second = _poi(place_id="two", name="Metropolitan Beach West")

        def search(*_args, _containing_places=None, **_kwargs):
            if _containing_places is not None:
                _containing_places.update({
                    "one": {"trusted-parent-city-id"},
                    "two": {"trusted-parent-city-id"},
                })
            return [first, second]

        with patch.object(self.service, "search_poi", side_effect=search), patch.object(
            self.service, "_resolve_city_identity", return_value=SimpleNamespace(
                place_id="trusted-parent-city-id", names=frozenset({"parentcity"}),
            ),
        ):
            match = self.service.match_poi("Metro Beach", "Parent City")
        self.assertNotEqual(match["status"], "verified")

    def test_partial_server_match_with_structured_city_reaches_photo(self):
        partial = _poi()
        self.service._client = type("Client", (), {
            "get": lambda *_args, **_kwargs: type("Response", (), {
                "raise_for_status": lambda _self: None,
                "json": lambda _self: {"photoUri": "safe-photo"},
            })(),
            "close": lambda _self: None,
        })()
        result = self.service.get_place_photo(
            name="Metro Beach", city="Parent City",
            match_result={
                "status": "partial_match", "poi": partial,
                "evidence": {"city_consistent": True},
            },
        )
        self.assertEqual(result["photo_url"], "safe-photo")


class GoogleCityPhotoRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_unverified_still_falls_back_to_xhs_with_safe_event(self):
        service = type("Service", (), {
            "match_poi": lambda *_args: {
                "status": "unverified", "poi": None,
                "evidence": {"city_consistent": False},
            },
        })()
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(return_value="safe-xhs-photo"),
        ):
            result = await get_attraction_photo("PRIVATE_POI", "PRIVATE_CITY")
        self.assertEqual(result["data"]["source"], "xhs")
        self.assertIn("category=city_mismatch", output.getvalue())
        self.assertNotIn("PRIVATE", output.getvalue())


if __name__ == "__main__":
    unittest.main()
