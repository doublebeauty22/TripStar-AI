import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from backend.app.api.routes.poi import get_attraction_photo
from backend.app.models.schemas import Location
from backend.app.services.google_map_service import GoogleMapService


class _PhotoService:
    def __init__(self):
        self.match_calls = 0
        self.photo_calls = []

    def match_poi(self, *_args):
        self.match_calls += 1
        poi = SimpleNamespace(
            id="server-place", location=Location(longitude=1, latitude=1),
        )
        return {"status": "verified", "poi": poi}

    def get_place_photo(self, **kwargs):
        self.photo_calls.append(kwargs)
        return {
            "photo_url": "safe-photo", "place_id": kwargs["place_id"],
            "attributions": [], "match_status": "verified", "reason": None,
        }


def _task(*, name="Safe POI", city="Safe City", place_id="server-place"):
    attraction = SimpleNamespace(
        name=name, place_id=place_id, poi_match_status="verified",
        map_data_source="google_places",
        location=Location(longitude=1, latitude=1),
    )
    plan = SimpleNamespace(
        city=city, days=[SimpleNamespace(city=city, attractions=[attraction])],
    )
    return {"status": "completed", "result": SimpleNamespace(data=plan)}


class TrustedGroundingReuseTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, service, task, *, client_id="server-place", plan_id="task-1"):
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), patch(
            "backend.app.api.routes.trip._get_task", return_value=task,
        ), patch(
            "backend.app.api.routes.trip._task_trip_plan",
            return_value=task["result"].data if task else None,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(side_effect=AssertionError("XHS must not run")),
        ):
            return await get_attraction_photo(
                "Safe POI", "Safe City", client_id, plan_id=plan_id,
            )

    async def test_trusted_completed_task_skips_text_search(self):
        service = _PhotoService()
        result = await self._call(service, _task())
        self.assertEqual(result["data"]["source"], "google_places")
        self.assertEqual(service.match_calls, 0)
        self.assertEqual(service.photo_calls[0]["place_id"], "server-place")

    async def test_client_place_id_without_task_is_not_trusted(self):
        service = _PhotoService()
        await self._call(service, None, plan_id="")
        self.assertEqual(service.match_calls, 1)

    async def test_wrong_task_or_attraction_association_falls_back_to_matcher(self):
        for task in (_task(name="Other POI"), _task(place_id="other-place")):
            service = _PhotoService()
            await self._call(service, task)
            self.assertEqual(service.match_calls, 1)

    async def test_unsafe_task_identifier_is_never_loaded(self):
        service = _PhotoService()
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), patch(
            "backend.app.api.routes.trip._get_task",
            side_effect=AssertionError("unsafe task identifier must not be loaded"),
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(side_effect=AssertionError("XHS must not run")),
        ):
            await get_attraction_photo(
                "Safe POI", "Safe City", "server-place", plan_id="../unsafe",
            )
        self.assertEqual(service.match_calls, 1)

    async def test_untrusted_task_status_and_source_fall_back(self):
        pending = _task()
        pending["status"] = "processing"
        untrusted = _task()
        untrusted["result"].data.days[0].attractions[0].map_data_source = "llm_unverified"
        for task in (pending, untrusted):
            service = _PhotoService()
            await self._call(service, task)
            self.assertEqual(service.match_calls, 1)

    async def test_persisted_partial_match_keeps_existing_fresh_match_policy(self):
        partial = _task()
        attraction = partial["result"].data.days[0].attractions[0]
        attraction.poi_match_status = "partial_match"
        attraction.place_id = ""
        attraction.map_data_source = "llm_unverified"
        service = _PhotoService()
        await self._call(service, partial)
        self.assertEqual(service.match_calls, 1)

    async def test_fallback_grounding_is_bounded_to_two(self):
        service = _PhotoService()
        active = 0
        peak = 0

        def match(*_args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            import time
            time.sleep(0.03)
            active -= 1
            return _PhotoService().match_poi()

        service.match_poi = match
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(side_effect=AssertionError("XHS must not run")),
        ):
            await asyncio.gather(*[
                get_attraction_photo(f"POI {index}", "City") for index in range(4)
            ])
        self.assertLessEqual(peak, 2)


class TextSearchRetryTests(unittest.TestCase):
    def setUp(self):
        self.service = GoogleMapService("fake-key")

    def tearDown(self):
        self.service.close()

    @staticmethod
    def _response(status, payload=None):
        request = httpx.Request("POST", "https://safe.invalid")
        return httpx.Response(status, request=request, json=payload or {})

    def test_429_retries_once_then_succeeds(self):
        client = SimpleNamespace(
            post=unittest.mock.Mock(side_effect=[
                self._response(429), self._response(200, {"places": []}),
            ]),
            close=lambda: None,
        )
        self.service._client = client
        with patch("backend.app.services.google_map_service.random.uniform", return_value=0.3), patch(
            "backend.app.services.google_map_service.time.sleep",
        ) as sleep:
            self.assertEqual(self.service.search_poi("Safe", "City"), [])
        self.assertEqual(client.post.call_count, 2)
        sleep.assert_called_once_with(0.3)

    def test_429_retries_at_most_once(self):
        client = SimpleNamespace(
            post=unittest.mock.Mock(side_effect=[self._response(429), self._response(429)]),
            close=lambda: None,
        )
        self.service._client = client
        with patch("backend.app.services.google_map_service.time.sleep"):
            self.service.search_poi("Safe", "City")
        self.assertEqual(client.post.call_count, 2)

    def test_non_retryable_statuses_are_not_retried(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                client = SimpleNamespace(
                    post=unittest.mock.Mock(return_value=self._response(status)),
                    close=lambda: None,
                )
                self.service._client = client
                self.service.search_poi("Safe", "City")
                self.assertEqual(client.post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
