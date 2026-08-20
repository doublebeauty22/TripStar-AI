import asyncio
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.api.routes.poi import get_attraction_photo
from backend.app.models.schemas import Location
from backend.app.services import xhs_service


def _completion(note_count: int):
    items = []
    for index in range(note_count):
        name = f"Place {index}"
        quote = f"recommendation {index}"
        items.append({
            "name": name,
            "identity_text": name,
            "name_zh": name,
            "name_en": name,
            "recommendation": quote,
            "duration": 60,
            "reservation_required": False,
            "reservation_tips": "",
            "evidence_ids": [f"note-{index}"],
            "evidence_support": [{
                "evidence_id": f"note-{index}",
                "identity_quote": name,
                "recommendation_quote": quote,
            }],
        })
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(items)),
    )])


class _ConcurrentResearchClient:
    def __init__(self, count: int = 4, *, failing_note: str = ""):
        self.count = count
        self.failing_note = failing_note
        self.lock = threading.Lock()
        self.release_first_pair = threading.Event()
        self.active = 0
        self.max_active = 0
        self.completed = 0

    def search_notes(self, **_kwargs):
        return {"success": True, "data": {"items": [
            {
                "model_type": "note",
                "id": f"note-{index}",
                "xsec_token": f"token-{index}",
                "note_card": {"display_title": f"Place {index}"},
            }
            for index in range(self.count)
        ]}}

    def get_note_detail(self, note_id, _token):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == 2:
                self.release_first_pair.set()
        self.release_first_pair.wait(timeout=1)
        # Deliberately finish in a different order from the search results.
        index = int(note_id.rsplit("-", 1)[1])
        time.sleep((self.count - index) * 0.003)
        try:
            if note_id == self.failing_note:
                raise RuntimeError("offline detail failure")
            return {"data": {"items": [{"note_card": {
                "desc": f"Place {index} recommendation {index}",
            }}]}}
        finally:
            with self.lock:
                self.active -= 1
                self.completed += 1


class ResearchDetailConcurrencyTests(unittest.TestCase):
    def test_two_way_overlap_preserves_order_and_waits_before_extraction(self):
        client = _ConcurrentResearchClient()
        extraction_completed_counts = []
        geocoded = []

        def extract(**_kwargs):
            extraction_completed_counts.append(client.completed)
            return _completion(4)

        def geocode(name, *_args, **_kwargs):
            geocoded.append(name)
            return None

        with patch.object(
            xhs_service, "get_xhs_client", return_value=client
        ), patch.object(
            xhs_service, "get_llm", return_value=SimpleNamespace(model="fake")
        ), patch(
            "backend.app.services.llm_service.create_chat_completion",
            side_effect=extract,
        ), patch.object(xhs_service, "geocode_amap", side_effect=geocode):
            result = xhs_service.search_xhs_attractions("Private City", "Private Query")

        self.assertEqual(client.max_active, 2)
        self.assertEqual(extraction_completed_counts, [4])
        self.assertEqual([item.note_id for item in result.evidence], [
            "note-0", "note-1", "note-2", "note-3",
        ])
        self.assertEqual(geocoded, ["Place 0", "Place 1", "Place 2", "Place 3"])

    def test_detail_failure_keeps_per_note_ssr_and_other_successes(self):
        client = _ConcurrentResearchClient(count=2, failing_note="note-1")
        ssr_calls = []

        def ssr(note_id):
            ssr_calls.append(note_id)
            return {"desc": "Place 1 recommendation 1"}

        with patch.object(
            xhs_service, "get_xhs_client", return_value=client
        ), patch.object(xhs_service, "get_note_detail_ssr", side_effect=ssr), patch.object(
            xhs_service, "get_llm", return_value=SimpleNamespace(model="fake")
        ), patch(
            "backend.app.services.llm_service.create_chat_completion",
            return_value=_completion(2),
        ), patch.object(xhs_service, "geocode_amap", return_value=None):
            result = xhs_service.search_xhs_attractions("Private City", "Private Query")

        self.assertEqual(ssr_calls, ["note-1"])
        self.assertEqual([item.note_id for item in result.evidence], ["note-0", "note-1"])
        self.assertTrue(all(item.status == "detail_available" for item in result.evidence))

    def test_shared_signing_context_is_serialized_but_http_is_not_locked(self):
        active_signing = 0
        max_signing = 0
        counter_lock = threading.Lock()

        def sign(*_args):
            nonlocal active_signing, max_signing
            with counter_lock:
                active_signing += 1
                max_signing = max(max_signing, active_signing)
            time.sleep(0.02)
            with counter_lock:
                active_signing -= 1
            return {}, {}, "{}"

        response = SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"success": True, "data": {"items": []}},
        )
        client = xhs_service.XhsNativeClient("not-logged")
        with patch.object(xhs_service, "_sign_request", side_effect=sign), patch.object(
            xhs_service.requests, "post", return_value=response
        ) as request:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(
                    lambda index: client.get_note_detail(f"note-{index}", "token"),
                    range(2),
                ))

        self.assertEqual(len(results), 2)
        self.assertEqual(max_signing, 1)
        self.assertEqual([call.kwargs["timeout"] for call in request.call_args_list], [15, 15])


class _GoogleSuccessService:
    def __init__(self):
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.active = 0
        self.max_active = 0

    def match_poi(self, *_args):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == 2:
                self.release.set()
        self.release.wait(timeout=1)
        with self.lock:
            self.active -= 1
        poi = SimpleNamespace(
            id="server-place-id",
            location=Location(longitude=10.0, latitude=20.0),
        )
        return {"status": "verified", "poi": poi}

    def get_place_photo(self, **_kwargs):
        return {
            "photo_url": "https://safe.invalid/google-photo",
            "place_id": "server-place-id",
            "attributions": [],
            "match_status": "verified",
            "reason": None,
        }


class ImageFallbackConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_xhs_image_chains_are_process_local_serial(self):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def chain(keyword, *, request_id=""):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return keyword

        with patch.object(
            xhs_service, "_get_xhs_photo_sync_unbounded", side_effect=chain
        ):
            results = await asyncio.gather(
                xhs_service.get_photo_from_xhs("one"),
                xhs_service.get_photo_from_xhs("two"),
            )

        self.assertEqual(results, ["one", "two"])
        self.assertEqual(max_active, 1)

    async def test_xhs_permit_releases_after_empty_result_and_exception(self):
        outcomes = ["", RuntimeError("offline"), "recovered"]

        def chain(*_args, **_kwargs):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(
            xhs_service, "_get_xhs_photo_sync_unbounded", side_effect=chain
        ):
            self.assertEqual(await xhs_service.get_photo_from_xhs("empty"), "")
            with self.assertRaises(RuntimeError):
                await xhs_service.get_photo_from_xhs("failure")
            self.assertEqual(await xhs_service.get_photo_from_xhs("success"), "recovered")

    async def test_google_success_does_not_wait_and_google_stages_overlap(self):
        google = _GoogleSuccessService()
        acquired = xhs_service._XHS_IMAGE_FALLBACK_SEMAPHORE.acquire(timeout=1)
        self.assertTrue(acquired)
        try:
            with patch(
                "backend.app.services.google_map_service.get_google_map_service",
                return_value=google,
            ):
                results = await asyncio.wait_for(asyncio.gather(
                    get_attraction_photo("one", "city"),
                    get_attraction_photo("two", "city"),
                ), timeout=1)
        finally:
            xhs_service._XHS_IMAGE_FALLBACK_SEMAPHORE.release()

        self.assertEqual(google.max_active, 2)
        self.assertTrue(all(item["data"]["source"] == "google_places" for item in results))


if __name__ == "__main__":
    unittest.main()
