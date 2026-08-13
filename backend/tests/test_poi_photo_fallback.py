import unittest
from unittest.mock import AsyncMock, patch

from backend.app.api.routes.poi import get_attraction_photo
from backend.app.models.schemas import Location


class _GooglePhotoService:
    def __init__(self, photo_url="", match_status="verified"):
        self.photo_url = photo_url
        self.match_status = match_status

    def get_place_photo(self, **_kwargs):
        return {
            "photo_url": self.photo_url,
            "place_id": "place-asakusa",
            "attributions": [{"displayName": "Photographer"}],
            "match_status": self.match_status,
        }

    def match_poi(self, *_args):
        poi = type("POI", (), {
            "id": "place-asakusa",
            "location": Location(longitude=12.5, latitude=45.5),
        })()
        return {"status": self.match_status, "poi": poi}


class PoiPhotoFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_photo_is_first_choice(self):
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=_GooglePhotoService("https://google.example/photo"),
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(side_effect=AssertionError("XHS must not be called")),
        ):
            result = await get_attraction_photo("浅草寺", "东京", "place-asakusa")

        self.assertEqual(result["data"]["source"], "google_places")
        self.assertEqual(result["data"]["photo_url"], "https://google.example/photo")

    async def test_xhs_is_used_when_google_has_no_photo(self):
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=_GooglePhotoService(),
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(return_value="https://xhs.example/photo"),
        ):
            result = await get_attraction_photo("浅草寺", "东京", "place-asakusa")

        self.assertEqual(result["data"]["source"], "xhs")

    async def test_partial_google_candidate_is_labeled_photo_only(self):
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=_GooglePhotoService("https://google.example/partial", "partial_match"),
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(side_effect=AssertionError("XHS must not be called")),
        ):
            result = await get_attraction_photo("浅草文化中心", "东京", None)

        self.assertEqual(result["data"]["source"], "google_places")
        self.assertEqual(result["data"]["match_status"], "partial_match")

    async def test_placeholder_is_local_and_not_returned_as_photo_url(self):
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=None,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(return_value=""),
        ):
            result = await get_attraction_photo("浅草寺", "东京", "")

        self.assertEqual(result["data"]["source"], "placeholder")
        self.assertEqual(result["data"]["photo_url"], "")
        self.assertTrue(result["degraded"])

    async def test_invalid_coordinate_match_cannot_trust_client_place_id(self):
        invalid_poi = type("POI", (), {
            "id": "synthetic-id", "location": Location(longitude=0, latitude=0),
        })()
        service = type("Service", (), {
            "match_poi": lambda *_args: {"status": "verified", "poi": invalid_poi},
            "get_place_photo": lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("invalid coordinate match must not reach Google photo lookup")
            ),
        })()
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(return_value=""),
        ):
            result = await get_attraction_photo(
                "Synthetic Landmark", "Synthetic City", "client-place-id"
            )
        self.assertEqual(result["data"]["source"], "placeholder")
        self.assertNotEqual(result["data"].get("match_status"), "verified")


if __name__ == "__main__":
    unittest.main()
