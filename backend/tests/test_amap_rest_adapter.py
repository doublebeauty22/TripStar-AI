import json
import unittest
from unittest.mock import patch

import httpx

from backend.app.models.schemas import AmapGeocodeResult, Location
from backend.app.services.amap_service import AmapService
from backend.app.services import xhs_service


class FakeResponse:
    def __init__(self, payload=None, status=200, malformed=False):
        self.payload = {} if payload is None else payload
        self.status_code = status
        self.malformed = malformed

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.invalid")
            raise httpx.HTTPStatusError(
                "status", request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self):
        if self.malformed:
            raise json.JSONDecodeError("bad", "", 0)
        return self.payload


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


def success(payload):
    return FakeResponse({"status": "1", **payload})


class AmapPOITests(unittest.TestCase):
    def test_success_is_structured_and_verified(self):
        client = FakeClient(success({"pois": [{
            "id": "poi-1", "name": "测试地点", "type": "风景名胜",
            "address": "测试路", "adname": "测试区", "location": "121.5,31.2",
        }]}))
        result = AmapService("fake-key", client).search_poi("测试", "测试市")
        self.assertTrue(result.request_success)
        self.assertTrue(result.data_available)
        self.assertEqual(result.provider, "amap")
        self.assertEqual(result.data[0].data_source, "amap")
        self.assertEqual(result.data[0].verification_status, "verified")
        self.assertEqual(result.data[0].district, "测试区")

    def test_empty_auth_timeout_and_malformed(self):
        cases = [
            (FakeClient(), "key_missing", False),
            (FakeClient(success({"pois": []})), "empty_result", True),
            (FakeClient(FakeResponse(status=401)), "authentication_failed", False),
            (FakeClient(httpx.TimeoutException("timeout")), "timeout", False),
            (FakeClient(httpx.ConnectError("offline")), "network_error", False),
            (FakeClient(FakeResponse(malformed=True)), "malformed_response", True),
            (FakeClient(success({"pois": {}})), "malformed_response", True),
        ]
        for client, expected, request_success in cases:
            with self.subTest(reason=expected):
                key = "" if expected == "key_missing" else "fake-key"
                result = AmapService(key, client).search_poi("q", "c")
                self.assertFalse(result.data_available)
                self.assertEqual(result.reason, expected)
                self.assertEqual(result.request_success, request_success)
                self.assertEqual(result.data, [])

    def test_business_reason_classification(self):
        cases = {"10001": "authentication_failed", "10005": "permission_denied", "10003": "rate_limited", "20000": "business_error"}
        for infocode, expected in cases.items():
            result = AmapService("fake-key", FakeClient(FakeResponse({
                "status": "0", "infocode": infocode,
            }))).search_poi("q", "c")
            self.assertEqual(result.reason, expected)

    def test_bad_location_never_creates_default_coordinate(self):
        result = AmapService("fake-key", FakeClient(success({"pois": [{
            "id": "poi-1", "name": "bad", "type": "type", "address": "addr",
            "location": "not-a-location",
        }]}))).search_poi("q", "c")
        self.assertFalse(result.data_available)
        self.assertEqual(result.reason, "malformed_response")
        self.assertEqual(result.data, [])

    def test_coordinate_trust_boundary_rejects_only_invalid_values(self):
        self.assertIsNone(AmapService._parse_location("0,0"))
        self.assertIsNotNone(AmapService._parse_location("0,30"))
        self.assertIsNotNone(AmapService._parse_location("30,0"))
        for value in ("nan,30", "30,inf", "181,30", "30,91"):
            with self.subTest(value=value):
                self.assertIsNone(AmapService._parse_location(value))

    def test_zero_zero_poi_is_not_verified(self):
        result = AmapService("fake-key", FakeClient(success({"pois": [{
            "id": "poi-1", "name": "invalid", "type": "type",
            "address": "addr", "location": "0,0",
        }]}))).search_poi("q", "c")
        self.assertFalse(result.data_available)
        self.assertEqual(result.data, [])


class AmapGeocodeTests(unittest.TestCase):
    def test_geocode_success_and_empty(self):
        valid = AmapService("fake-key", FakeClient(success({"geocodes": [{
            "formatted_address": "测试地址", "location": "120.1,30.2",
        }]}))).geocode("测试地址", "测试市")
        self.assertTrue(valid.data_available)
        self.assertEqual(valid.provider, "amap")
        self.assertEqual(valid.location, Location(longitude=120.1, latitude=30.2))
        empty = AmapService("fake-key", FakeClient(success({"geocodes": []}))).geocode("q", "c")
        self.assertEqual(empty.reason, "empty_result")
        self.assertIsNone(empty.location)

    def test_structured_address_prefers_geocode(self):
        client = FakeClient(success({"geocodes": [{
            "formatted_address": "structured address", "location": "120.1,30.2",
        }]}))
        result = AmapService("fake-key", client).resolve_place(
            "structured address", "test city"
        )
        self.assertTrue(result.data_available)
        self.assertEqual(result.resolution_path, "geocoding")
        self.assertEqual(len(client.calls), 1)
        self.assertIn("/geocode/geo", client.calls[0][0])

    def test_landmark_geocode_failure_falls_back_to_verified_poi(self):
        client = FakeClient(
            FakeResponse({"status": "0", "infocode": "30001"}),
            success({"pois": [{
                "id": "poi-landmark", "name": "landmark", "type": "attraction",
                "address": "real address", "location": "121.5,31.2",
            }]}),
        )
        result = AmapService("fake-key", client).resolve_place("landmark", "test city")
        self.assertTrue(result.data_available)
        self.assertEqual(result.provider, "amap")
        self.assertEqual(result.poi_id, "poi-landmark")
        self.assertEqual(result.resolution_path, "poi_search")

    def test_poi_context_prefers_search_without_geocode(self):
        client = FakeClient(success({"pois": [{
            "id": "poi-1", "name": "landmark", "type": "attraction",
            "address": "real address", "location": "121.5,31.2",
        }]}))
        result = AmapService("fake-key", client).resolve_place(
            "landmark", "test city", prefer_poi=True
        )
        self.assertEqual(result.resolution_path, "poi_search")
        self.assertEqual(len(client.calls), 1)
        self.assertIn("/place/text", client.calls[0][0])

    def test_both_resolution_paths_unavailable(self):
        client = FakeClient(
            FakeResponse({"status": "0", "infocode": "30001"}),
            success({"pois": []}),
        )
        result = AmapService("fake-key", client).resolve_place("unknown", "test city")
        self.assertFalse(result.data_available)
        self.assertEqual(result.provider, "unavailable")
        self.assertEqual(result.reason, "empty_result")
        self.assertIsNone(result.location)
        self.assertEqual(result.resolution_path, "unavailable")

    def test_xhs_compatibility_helper_remains_fail_open(self):
        unavailable = AmapGeocodeResult(
            provider="unavailable", request_success=False, data_available=False,
            degraded=True, reason="timeout",
        )
        fake = type("FakeService", (), {"resolve_place": lambda *_args, **_kwargs: unavailable})()
        with patch("backend.app.services.amap_service.get_amap_service", return_value=fake):
            self.assertIsNone(xhs_service._geocode_amap_raw("q", "c"))

    def test_invalid_geocode_cannot_survive_place_resolution(self):
        client = FakeClient(
            success({"geocodes": [{"formatted_address": "invalid", "location": "0,0"}]}),
            success({"pois": []}),
        )
        result = AmapService("fake-key", client).resolve_place("invalid", "city")
        self.assertFalse(result.data_available)
        self.assertIsNone(result.location)


class AmapRouteTests(unittest.TestCase):
    def _route(self, mode, response):
        client = FakeClient(response)
        result = AmapService("fake-key", client).plan_route(
            "120.1,30.1", "120.2,30.2", route_type=mode,
        )
        return result, client

    def test_walking_and_driving_success(self):
        for mode in ("walking", "driving"):
            result, _ = self._route(mode, success({"route": {"paths": [{
                "distance": "1234", "duration": "567",
            }]}}))
            self.assertTrue(result.data_available)
            self.assertEqual(result.provider, "amap")
            self.assertEqual(result.route_mode, mode)
            self.assertEqual((result.distance, result.duration), (1234.0, 567))

    def test_existing_verified_coordinates_are_not_geocoded(self):
        client = FakeClient(success({"route": {"paths": [{
            "distance": "100", "duration": "60",
        }]}}))
        service = AmapService("fake-key", client)
        result = service.plan_route(
            Location(longitude=120.1, latitude=30.1),
            Location(longitude=120.2, latitude=30.2),
            route_type="walking",
        )
        self.assertTrue(result.data_available)
        self.assertEqual(len(client.calls), 1)
        self.assertIn("/direction/walking", client.calls[0][0])

    def test_transit_success_uses_real_provider_metrics(self):
        result, client = self._route("transit", success({"route": {"transits": [{
            "distance": "2345", "duration": "678",
        }]}}))
        self.assertTrue(result.data_available)
        self.assertEqual(result.provider, "amap")
        self.assertEqual((result.distance, result.duration), (2345.0, 678))
        self.assertIn("/transit/integrated", client.calls[0][0])

    def test_unsupported_mode_does_not_call_provider(self):
        client = FakeClient()
        result = AmapService("fake-key", client).plan_route("120,30", "121,31", route_type="cycling")
        self.assertEqual(result.reason, "unsupported_mode")
        self.assertEqual(client.calls, [])

    def test_empty_auth_timeout_never_fabricate_metrics(self):
        cases = [
            (success({"route": {"paths": []}}), "empty_result"),
            (FakeResponse(status=401), "authentication_failed"),
            (httpx.TimeoutException("timeout"), "timeout"),
        ]
        for response, reason in cases:
            result, _ = self._route("walking", response)
            self.assertFalse(result.data_available)
            self.assertEqual(result.reason, reason)
            self.assertIsNone(result.distance)
            self.assertIsNone(result.duration)

    def test_geocode_failure_reason_is_preserved(self):
        client = FakeClient(
            httpx.TimeoutException("timeout"),
            httpx.TimeoutException("timeout"),
        )
        result = AmapService("fake-key", client).plan_route(
            "origin address", "120.2,30.2", route_type="walking",
        )
        self.assertFalse(result.data_available)
        self.assertFalse(result.request_success)
        self.assertEqual(result.reason, "timeout")
        self.assertIsNone(result.distance)
        self.assertIsNone(result.duration)

    def test_route_rejects_invalid_existing_coordinates_without_request(self):
        client = FakeClient()
        result = AmapService("fake-key", client).plan_route(
            Location(longitude=0, latitude=0),
            Location(longitude=30, latitude=20),
            route_type="walking",
        )
        self.assertFalse(result.data_available)
        self.assertEqual(result.reason, "malformed_response")
        self.assertEqual(client.calls, [])


class AmapWeatherRegressionTests(unittest.TestCase):
    def test_weather_contract_is_unchanged(self):
        payload = success({"forecasts": [{"casts": [{
            "date": "2026-08-01", "dayweather": "晴", "nightweather": "多云",
            "daytemp": "31", "nighttemp": "25", "daywind": "东", "daypower": "3",
        }]}]})
        result = AmapService("fake-key", FakeClient(payload)).get_weather("测试市", degraded=True)
        self.assertTrue(result.data_available)
        self.assertEqual(result.provider, "amap")
        self.assertTrue(result.degraded)
        self.assertEqual(result.days[0].data_source, "amap")

    def test_partial_or_invalid_temperatures_never_become_verified_zero(self):
        invalid_values = [None, "", "bad", "NaN", "Infinity"]
        for field in ("daytemp", "nighttemp"):
            for invalid in invalid_values:
                with self.subTest(field=field, invalid=invalid):
                    cast = {
                        "date": "2026-08-01", "dayweather": "晴", "nightweather": "多云",
                        "daytemp": "31", "nighttemp": "25",
                    }
                    if invalid is None:
                        cast.pop(field)
                    else:
                        cast[field] = invalid
                    result = AmapService(
                        "fake-key", FakeClient(success({"forecasts": [{"casts": [cast]}]}))
                    ).get_weather("测试市")
                    self.assertFalse(result.data_available)
                    self.assertEqual(result.days, [])

    def test_invalid_day_is_skipped_when_another_day_is_valid(self):
        result = AmapService("fake-key", FakeClient(success({"forecasts": [{"casts": [
            {"date": "2026-08-01", "daytemp": "", "nighttemp": "20"},
            {"date": "2026-08-02", "daytemp": "30", "nighttemp": "21"},
        ]}]}))).get_weather("测试市")
        self.assertTrue(result.data_available)
        self.assertEqual(len(result.days), 1)
        self.assertEqual(result.days[0].date, "2026-08-02")


if __name__ == "__main__":
    unittest.main()
