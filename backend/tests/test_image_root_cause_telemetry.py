import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.app.api.routes.poi import get_attraction_photo
from backend.app.services import xhs_service
from backend.app.services.google_map_service import GoogleMapService


REQUEST_ID = "abcdef123456"


def _event_lines(output, prefix):
    return [line for line in output.splitlines() if line.startswith(prefix)]


class _XHSClient:
    def __init__(self, items, detail=None, detail_error=None):
        self.items = items
        self.detail = detail
        self.detail_error = detail_error

    def search_notes(self, **_kwargs):
        return {"data": {"items": self.items}}

    def get_note_detail(self, *_args):
        if self.detail_error:
            raise self.detail_error
        return self.detail


def _note():
    return {"model_type": "note", "id": "private-note", "xsec_token": "private-token"}


def _detail(images=None, *, items=True):
    detail_items = [{"note_card": {"image_list": images or []}}] if items else []
    return {"data": {"items": detail_items}}


def _ssr_response(*, state=None):
    text = "no-state"
    if state is not None:
        import json
        text = f"window.__INITIAL_STATE__={json.dumps(state)}</script>"
    return SimpleNamespace(text=text)


def _ssr_state(images):
    return {
        "note": {"noteDetailMap": {
            "private-note": {"note": {"imageList": images}}
        }}
    }


class XHSImageRootCauseTelemetryTests(unittest.TestCase):
    def _run(self, client, ssr=None):
        output = io.StringIO()
        with redirect_stdout(output), patch.object(
            xhs_service, "get_xhs_client", return_value=client,
        ), patch.object(
            xhs_service.httpx, "get", return_value=ssr or _ssr_response(),
        ):
            result = xhs_service._get_xhs_photo_sync_unbounded(
                "PRIVATE_QUERY", request_id=REQUEST_ID,
            )
        return result, output.getvalue()

    def _assert_one_empty_category(self, client, expected, ssr=None):
        result, output = self._run(client, ssr)
        self.assertEqual(result, "")
        lines = _event_lines(output, "XHS_IMAGE_EVENT ")
        self.assertEqual(len(lines), 1)
        self.assertIn(f"category={expected}", lines[0])
        self.assertIn(f"request_id={REQUEST_ID}", lines[0])
        self.assertNotIn("PRIVATE", output)

    def test_search_empty(self):
        self._assert_one_empty_category(_XHSClient([]), "xhs_search_empty")

    def test_no_eligible_note(self):
        self._assert_one_empty_category(
            _XHSClient([{"model_type": "ad", "id": "private-note"}]),
            "xhs_no_eligible_note",
        )

    def test_detail_url_missing_bypasses_ssr(self):
        self._assert_one_empty_category(
            _XHSClient([_note()], _detail([{"info_list": []}])),
            "xhs_detail_url_missing",
        )

    def test_detail_portrait_rejection_bypasses_ssr(self):
        image = {"width": 600, "height": 1200, "info_list": [{"url": "private-url"}]}
        self._assert_one_empty_category(
            _XHSClient([_note()], _detail([image])),
            "xhs_detail_image_rejected",
        )

    def test_ssr_state_missing_after_detail_empty(self):
        self._assert_one_empty_category(
            _XHSClient([_note()], _detail(items=False)),
            "xhs_ssr_state_missing",
        )

    def test_ssr_image_empty(self):
        self._assert_one_empty_category(
            _XHSClient([_note()], _detail(items=False)),
            "xhs_ssr_image_empty",
            _ssr_response(state=_ssr_state([])),
        )

    def test_ssr_url_missing_and_portrait_rejection_are_distinct(self):
        cases = (
            ([{}], "xhs_ssr_url_missing"),
            ([{"urlDefault": "private-url", "width": 600, "height": 1200}],
             "xhs_ssr_image_rejected"),
        )
        for images, category in cases:
            with self.subTest(category=category):
                self._assert_one_empty_category(
                    _XHSClient([_note()], _detail(items=False)), category,
                    _ssr_response(state=_ssr_state(images)),
                )

    def test_detail_failure_then_ssr_empty(self):
        self._assert_one_empty_category(
            _XHSClient([_note()], detail_error=TimeoutError("PRIVATE_EXCEPTION")),
            "xhs_ssr_empty_after_detail_failed",
        )

    def test_detail_failure_then_ssr_success(self):
        client = _XHSClient([_note()], detail_error=TimeoutError("PRIVATE_EXCEPTION"))
        result, output = self._run(
            client,
            _ssr_response(state=_ssr_state([{"urlDefault": "safe-result"}])),
        )
        self.assertEqual(result, "safe-result")
        lines = _event_lines(output, "XHS_IMAGE_EVENT ")
        self.assertEqual(len(lines), 1)
        self.assertIn("category=xhs_ssr_success_after_detail_failed", lines[0])
        self.assertNotIn("PRIVATE", output)


class GoogleGroundingRootCauseTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unverified_contract_and_safe_reason_log(self):
        match = {
            "status": "unverified", "poi": None,
            "evidence": {"city_consistent": False},
        }
        service = SimpleNamespace(match_poi=lambda *_args: match)
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(return_value=""),
        ):
            result = await get_attraction_photo(
                "PRIVATE_POI", "PRIVATE_CITY", address="PRIVATE_ADDRESS",
            )
        self.assertEqual(result["data"]["reason"], "xhs_no_result")
        self.assertIn("grounding_unverified", result["data"]["failure_reasons"])
        lines = _event_lines(output.getvalue(), "GOOGLE_GROUNDING_EVENT ")
        self.assertEqual(len(lines), 1)
        self.assertIn("category=city_mismatch", lines[0])
        self.assertNotIn("PRIVATE", output.getvalue())

    def test_provider_failure_is_distinct_from_valid_empty_search(self):
        service = GoogleMapService("fake-key")
        try:
            service._client = SimpleNamespace(
                post=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("PRIVATE_PROVIDER_BODY")
                ),
                close=lambda: None,
            )
            failed = service.match_poi("PRIVATE_POI", "PRIVATE_CITY")
            self.assertEqual(failed["evidence"]["reason"], "provider_failure")
            with patch.object(service, "search_poi", return_value=[]):
                empty = service.match_poi("PRIVATE_POI", "PRIVATE_CITY")
            self.assertEqual(empty["evidence"]["reason"], "no_candidates")
        finally:
            service.close()

    def test_existing_gate_categories_are_bounded(self):
        from backend.app.api.routes.poi import _google_grounding_category
        cases = {
            "city_mismatch": {"city_consistent": False},
            "type_mismatch": {"city_consistent": True, "type_compatible": False},
            "scope_conflict": {
                "city_consistent": True, "type_compatible": True,
                "scope_compatible": False,
            },
            "invalid_place_id": {
                "city_consistent": True, "type_compatible": True,
                "scope_compatible": True, "place_id_valid": False,
            },
            "invalid_coordinates": {
                "city_consistent": True, "type_compatible": True,
                "scope_compatible": True, "place_id_valid": True,
                "coordinate_valid": False,
            },
            "name_mismatch": {
                "city_consistent": True, "type_compatible": True,
                "scope_compatible": True, "place_id_valid": True,
                "coordinate_valid": True, "name_score": 0.1,
            },
            "ambiguous_candidates": {
                "city_consistent": True, "type_compatible": True,
                "scope_compatible": True, "place_id_valid": True,
                "coordinate_valid": True, "name_score": 0.8,
                "runner_up_margin": 0.01,
            },
        }
        for category, evidence in cases.items():
            with self.subTest(category=category):
                self.assertEqual(
                    _google_grounding_category({"evidence": evidence}), category,
                )


if __name__ == "__main__":
    unittest.main()
