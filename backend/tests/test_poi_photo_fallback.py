import io
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from backend.app.api.routes.poi import (
    _PHOTO_CLIENT_EVENT_LOCK,
    _PHOTO_CLIENT_EVENT_MAX_TICKETS,
    _PHOTO_CLIENT_EVENT_TICKETS,
    _PHOTO_CLIENT_EVENT_TTL_SECONDS,
    PhotoClientEventRequest,
    _consume_photo_client_event_ticket,
    _register_photo_client_event_ticket,
    get_attraction_photo,
    record_photo_client_event,
)
from backend.app.models.schemas import Location


def _terminal_lines(output):
    return [line for line in output.splitlines() if line.startswith("PHOTO_TERMINAL ")]


def _field(line, name):
    match = re.search(rf"(?:^| ){name}=([^ ]+)", line)
    return match.group(1) if match else None


class _GooglePhotoService:
    def __init__(self, photo_url="", match_status="verified"):
        self.photo_url = photo_url
        self.match_status = match_status
        self.match_calls = []
        self.photo_calls = []

    def get_place_photo(self, **kwargs):
        self.photo_calls.append(kwargs)
        return {
            "photo_url": self.photo_url,
            "place_id": "place-asakusa",
            "attributions": [{"displayName": "Photographer"}],
            "match_status": self.match_status,
            "reason": None if self.photo_url else "google_no_photo",
        }

    def match_poi(self, *args):
        self.match_calls.append(args)
        poi = type("POI", (), {
            "id": "place-asakusa",
            "location": Location(longitude=12.5, latitude=45.5),
        })()
        return {"status": self.match_status, "poi": poi}


class PoiPhotoFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_photo_is_first_choice(self):
        service = _GooglePhotoService("https://google.example/photo")
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(side_effect=AssertionError("XHS must not be called")),
        ):
            result = await get_attraction_photo("浅草寺", "东京", "place-asakusa")

        self.assertEqual(result["data"]["source"], "google_places")
        self.assertEqual(result["data"]["photo_url"], "https://google.example/photo")
        self.assertEqual(len(service.match_calls), 1)
        terminals = _terminal_lines(output.getvalue())
        self.assertEqual(len(terminals), 1)
        self.assertEqual(_field(terminals[0], "outcome"), "success")
        self.assertEqual(_field(terminals[0], "source"), "google_places")
        self.assertEqual(_field(terminals[0], "category"), "success")
        self.assertEqual(
            result["data"]["photo_request_id"],
            _field(terminals[0], "request_id"),
        )

    async def test_full_context_is_forwarded_to_photo_grounding(self):
        service = _GooglePhotoService("https://google.example/photo")
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(side_effect=AssertionError("XHS must not be called")),
        ):
            await get_attraction_photo(
                "八坂神社", "京都", "client-id", "京都市东山区", "神社"
            )

        self.assertEqual(
            service.match_calls,
            [("八坂神社", "京都", "京都市东山区", "神社")],
        )

    async def test_name_and_city_only_remain_backward_compatible(self):
        service = _GooglePhotoService("https://google.example/photo")
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(side_effect=AssertionError("XHS must not be called")),
        ):
            result = await get_attraction_photo("浅草寺", "东京")

        self.assertEqual(result["data"]["source"], "google_places")
        self.assertEqual(service.match_calls, [("浅草寺", "东京", "", "")])

    async def test_xhs_is_used_when_google_has_no_photo(self):
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=_GooglePhotoService(),
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(return_value="https://xhs.example/photo"),
        ):
            result = await get_attraction_photo("浅草寺", "东京", "place-asakusa")

        self.assertEqual(result["data"]["source"], "xhs")
        terminals = _terminal_lines(output.getvalue())
        self.assertEqual(len(terminals), 1)
        self.assertEqual(_field(terminals[0], "source"), "xhs")
        self.assertEqual(_field(terminals[0], "outcome"), "success")
        self.assertEqual(
            result["data"]["photo_request_id"],
            _field(terminals[0], "request_id"),
        )

    async def test_partial_google_candidate_is_labeled_photo_only(self):
        service = _GooglePhotoService("https://google.example/partial", "partial_match")
        with patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(side_effect=AssertionError("XHS must not be called")),
        ):
            result = await get_attraction_photo("浅草文化中心", "东京", None)

        self.assertEqual(result["data"]["source"], "google_places")
        self.assertEqual(result["data"]["match_status"], "partial_match")
        self.assertEqual(len(service.match_calls), 1)
        self.assertEqual(service.photo_calls[0]["match_result"]["status"], "partial_match")

    async def test_placeholder_is_local_and_not_returned_as_photo_url(self):
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=None,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(return_value=""),
        ):
            result = await get_attraction_photo("浅草寺", "东京", "")

        self.assertEqual(result["data"]["source"], "placeholder")
        self.assertEqual(result["data"]["photo_url"], "")
        self.assertEqual(result["data"]["reason"], "xhs_no_result")
        self.assertIn("xhs_no_result", result["data"]["failure_reasons"])
        self.assertTrue(result["degraded"])
        terminals = _terminal_lines(output.getvalue())
        self.assertEqual(len(terminals), 1)
        self.assertEqual(_field(terminals[0], "outcome"), "placeholder")
        self.assertEqual(_field(terminals[0], "source"), "placeholder")
        self.assertEqual(_field(terminals[0], "category"), "xhs_no_result")
        self.assertEqual(
            result["data"]["photo_request_id"],
            _field(terminals[0], "request_id"),
        )

    async def test_unverified_grounding_xhs_success_has_one_terminal(self):
        service = _GooglePhotoService(match_status="unverified")
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=service,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(return_value="https://xhs.example/photo"),
        ):
            result = await get_attraction_photo("Example POI", "Example City")

        self.assertEqual(result["data"]["source"], "xhs")
        terminals = _terminal_lines(output.getvalue())
        self.assertEqual(len(terminals), 1)
        self.assertEqual(_field(terminals[0], "source"), "xhs")
        process_lines = [
            line for line in output.getvalue().splitlines()
            if line.startswith("PHOTO_EVENT ")
        ]
        self.assertTrue(process_lines)
        request_id = _field(terminals[0], "request_id")
        self.assertRegex(request_id or "", r"^[0-9a-f]{12}$")
        self.assertTrue(all(_field(line, "request_id") == request_id for line in process_lines))

    async def test_xhs_provider_error_is_safely_classified(self):
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=None,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(side_effect=RuntimeError("raw-sensitive-provider-detail")),
        ):
            result = await get_attraction_photo("浅草寺", "东京")

        self.assertEqual(result["data"]["source"], "placeholder")
        self.assertEqual(result["data"]["reason"], "xhs_provider_error")
        terminals = _terminal_lines(output.getvalue())
        self.assertEqual(len(terminals), 1)
        self.assertEqual(_field(terminals[0], "source"), "placeholder")
        self.assertEqual(_field(terminals[0], "category"), "xhs_provider_error")
        self.assertEqual(_field(terminals[0], "retryable"), "true")

    async def test_terminal_log_excludes_request_and_provider_secrets(self):
        sensitive_values = [
            "Sensitive Attraction", "Sensitive City", "Sensitive Address",
            "Sensitive Category", "raw-sensitive-provider-detail",
            "https://sensitive.example/photo", "authorization-sensitive-marker",
        ]
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=None,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(side_effect=RuntimeError(sensitive_values[4])),
        ):
            await get_attraction_photo(
                sensitive_values[0], sensitive_values[1], "client-id",
                sensitive_values[2], sensitive_values[3],
            )

        log_output = output.getvalue()
        self.assertEqual(len(_terminal_lines(log_output)), 1)
        for value in sensitive_values:
            self.assertNotIn(value, log_output)

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
        self.assertIn("grounding_unverified", result["data"]["failure_reasons"])


class PhotoClientTelemetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with _PHOTO_CLIENT_EVENT_LOCK:
            _PHOTO_CLIENT_EVENT_TICKETS.clear()

    def tearDown(self):
        with _PHOTO_CLIENT_EVENT_LOCK:
            _PHOTO_CLIENT_EVENT_TICKETS.clear()

    async def test_xhs_response_and_browser_outcome_share_only_the_safe_request_id(self):
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "backend.app.services.google_map_service.get_google_map_service",
            return_value=None,
        ), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs",
            new=AsyncMock(return_value="https://private.example/photo"),
        ):
            result = await get_attraction_photo(
                "Private Attraction", "Private City",
            )
            request_id = result["data"]["photo_request_id"]
            await record_photo_client_event(PhotoClientEventRequest(
                request_id=request_id,
                source="xhs",
                outcome="load_error",
                surface="day",
            ))

        terminal = _terminal_lines(output.getvalue())
        client = [
            line for line in output.getvalue().splitlines()
            if line.startswith("PHOTO_CLIENT_EVENT ")
        ]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(len(client), 1)
        self.assertEqual(_field(terminal[0], "request_id"), request_id)
        self.assertEqual(_field(client[0], "request_id"), request_id)
        for private_value in (
            "Private Attraction", "Private City", "https://private.example/photo",
        ):
            self.assertNotIn(private_value, output.getvalue())

    async def test_fixed_client_event_is_logged_without_request_content(self):
        output = io.StringIO()
        _register_photo_client_event_ticket(
            request_id="abcdef123456", source="xhs",
        )
        event = PhotoClientEventRequest(
            request_id="abcdef123456",
            source="xhs",
            outcome="load_error",
            surface="overview",
        )
        with redirect_stdout(output):
            result = await record_photo_client_event(event)

        self.assertEqual(result, {"success": True})
        self.assertEqual(
            output.getvalue().strip(),
            "PHOTO_CLIENT_EVENT request_id=abcdef123456 source=xhs "
            "outcome=load_error surface=overview",
        )

    async def test_unissued_mismatched_and_duplicate_events_are_not_logged(self):
        output = io.StringIO()
        _register_photo_client_event_ticket(
            request_id="abcdef123456", source="xhs",
        )
        events = (
            PhotoClientEventRequest(
                request_id="000000000000", source="xhs",
                outcome="load_error", surface="overview",
            ),
            PhotoClientEventRequest(
                request_id="abcdef123456", source="google_places",
                outcome="load_error", surface="overview",
            ),
            PhotoClientEventRequest(
                request_id="abcdef123456", source="xhs",
                outcome="load_success", surface="overview",
            ),
            PhotoClientEventRequest(
                request_id="abcdef123456", source="xhs",
                outcome="load_error", surface="overview",
            ),
        )
        with redirect_stdout(output):
            for event in events:
                self.assertEqual(
                    await record_photo_client_event(event), {"success": True},
                )

        lines = [
            line for line in output.getvalue().splitlines()
            if line.startswith("PHOTO_CLIENT_EVENT ")
        ]
        self.assertEqual(len(lines), 1)
        self.assertIn(
            "request_id=abcdef123456 source=xhs outcome=load_success "
            "surface=overview",
            lines[0],
        )

    async def test_expired_ticket_is_not_logged(self):
        output = io.StringIO()
        with patch(
            "backend.app.api.routes.poi.time.monotonic",
            side_effect=(100.0, 100.0 + _PHOTO_CLIENT_EVENT_TTL_SECONDS + 1.0),
        ):
            _register_photo_client_event_ticket(
                request_id="abcdef123456", source="xhs",
            )
            with redirect_stdout(output):
                result = await record_photo_client_event(PhotoClientEventRequest(
                    request_id="abcdef123456",
                    source="xhs",
                    outcome="load_error",
                    surface="overview",
                ))

        self.assertEqual(result, {"success": True})
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(len(_PHOTO_CLIENT_EVENT_TICKETS), 0)

    def test_ticket_registry_evicts_the_oldest_entry_at_capacity(self):
        for index in range(_PHOTO_CLIENT_EVENT_MAX_TICKETS + 1):
            _register_photo_client_event_ticket(
                request_id=f"{index:012x}", source="xhs",
            )

        self.assertEqual(
            len(_PHOTO_CLIENT_EVENT_TICKETS),
            _PHOTO_CLIENT_EVENT_MAX_TICKETS,
        )
        self.assertNotIn("000000000000", _PHOTO_CLIENT_EVENT_TICKETS)
        self.assertIn(
            f"{_PHOTO_CLIENT_EVENT_MAX_TICKETS:012x}",
            _PHOTO_CLIENT_EVENT_TICKETS,
        )

    def test_concurrent_duplicate_surface_is_consumed_once(self):
        _register_photo_client_event_ticket(
            request_id="abcdef123456", source="xhs",
        )

        def consume_once(_index):
            return _consume_photo_client_event_ticket(
                request_id="abcdef123456",
                source="xhs",
                surface="overview",
            )

        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(consume_once, range(32)))

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 31)

    def test_client_event_schema_rejects_unbounded_or_private_fields(self):
        valid = {
            "request_id": "abcdef123456",
            "source": "google_places",
            "outcome": "load_success",
            "surface": "day",
        }
        invalid_payloads = (
            {**valid, "request_id": "unsafe"},
            {**valid, "source": "placeholder"},
            {**valid, "outcome": "raw-browser-error"},
            {**valid, "surface": "export"},
            {**valid, "name": "private-attraction"},
            {**valid, "url": "https://private.example/image"},
        )
        for payload in invalid_payloads:
            with self.subTest(fields=tuple(sorted(payload))):
                with self.assertRaises(ValidationError):
                    PhotoClientEventRequest.model_validate(payload)


if __name__ == "__main__":
    from network_guard import guarded_unittest_main

    guarded_unittest_main()
