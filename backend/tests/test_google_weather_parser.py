import json
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from backend.app.models.schemas import Location
from backend.app.services.google_map_service import GoogleMapService


class Response:
    def __init__(self, payload=None, status=200, malformed=False):
        self.payload = payload
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


def current_day(**overrides):
    day = {
        "displayDate": {"year": 2026, "month": 8, "day": 12},
        "daytimeForecast": {
            "weatherCondition": {
                "type": "LIGHT_RAIN_SHOWERS",
                "description": {"text": "阵雨", "languageCode": "zh-CN"},
                "iconBaseUri": "https://example.invalid/icon",
            },
            "precipitation": {
                "probability": {"type": "RAIN", "percent": 65},
                "qpf": {"unit": "MILLIMETERS", "quantity": 2.4},
            },
            "wind": {
                "direction": {"cardinal": "EAST", "degrees": 90},
                "speed": {"unit": "KILOMETERS_PER_HOUR", "value": 18.5},
            },
            "unknownFutureField": {"safe": True},
        },
        "nighttimeForecast": {
            "weatherCondition": {
                "type": "CLOUDY", "description": {"text": "阴"},
            },
        },
        "maxTemperature": {"degrees": 24.6, "unit": "CELSIUS"},
        "minTemperature": {"degrees": 17.4, "unit": "CELSIUS"},
        "unexpectedExtra": "ignored",
    }
    day.update(overrides)
    return day


class GoogleWeatherParserTests(unittest.TestCase):
    def setUp(self):
        self.service = GoogleMapService("fake-key")
        self.location = Location(longitude=151.2, latitude=-33.8)

    def tearDown(self):
        self.service.close()

    def result(self, response):
        self.service._client = SimpleNamespace(
            get=lambda *_args, **_kwargs: response, close=lambda: None,
        )
        with patch.object(self.service, "geocode", return_value=self.location):
            return self.service.get_weather("test city")

    def test_current_nested_daily_response(self):
        result = self.result(Response({"forecastDays": [current_day()]}))
        self.assertTrue(result.request_success)
        self.assertTrue(result.data_available)
        self.assertEqual(result.provider, "google_weather")
        self.assertFalse(result.degraded)
        self.assertIsNone(result.reason)
        day = result.days[0]
        self.assertEqual(day.date, "2026-08-12")
        self.assertEqual((day.day_weather, day.night_weather), ("阵雨", "阴"))
        self.assertEqual((day.day_temp, day.night_temp), (25, 17))
        self.assertEqual(day.precipitation_probability, 65)
        self.assertEqual((day.wind_direction, day.wind_power), ("EAST", "18.5 km/h"))
        self.assertEqual(day.data_source, "google_weather")
        self.assertEqual(day.verification_status, "verified")
        self.assertFalse(day.degraded)

    def test_optional_precipitation_and_wind_can_be_absent(self):
        item = current_day()
        item["daytimeForecast"].pop("precipitation")
        item["daytimeForecast"].pop("wind")
        result = self.result(Response({"forecastDays": [item]}))
        self.assertTrue(result.data_available)
        self.assertIsNone(result.days[0].precipitation_probability)
        self.assertEqual(result.days[0].wind_direction, "")
        self.assertEqual(result.days[0].wind_power, "")

    def test_optional_day_parts_can_be_missing(self):
        item = current_day(daytimeForecast={}, nighttimeForecast={})
        result = self.result(Response({"forecastDays": [item]}))
        self.assertTrue(result.data_available)
        self.assertEqual(result.days[0].day_weather, "")
        self.assertEqual(result.days[0].night_weather, "")

    def test_one_malformed_day_does_not_drop_other_valid_days(self):
        malformed = current_day(maxTemperature={})
        result = self.result(Response({"forecastDays": [malformed, current_day()]}))
        self.assertTrue(result.data_available)
        self.assertEqual(len(result.days), 1)

    def test_empty_and_malformed_top_level_are_distinct(self):
        empty = self.result(Response({"forecastDays": []}))
        self.assertEqual(empty.reason, "empty_forecast")
        for payload in ({}, {"forecastDays": {}}, [], None):
            with self.subTest(payload=payload):
                malformed = self.result(Response(payload))
                self.assertEqual(malformed.reason, "malformed_response")
                self.assertTrue(malformed.request_success)
                self.assertFalse(malformed.data_available)

    def test_nonempty_but_entirely_invalid_days_are_malformed(self):
        result = self.result(Response({"forecastDays": [{"displayDate": {}}]}))
        self.assertEqual(result.reason, "malformed_response")
        self.assertEqual(result.days, [])

    def test_http_regressions(self):
        self.assertEqual(self.result(Response({}, 401)).reason, "authentication_failed")
        self.assertEqual(self.result(Response({}, 403)).reason, "permission_denied")
        self.assertEqual(self.result(Response({}, 429)).reason, "rate_limited")

    def test_unsupported_location_404_is_safe_and_non_retryable(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = self.result(Response({"message": "provider detail"}, 404))

        self.assertEqual(result.provider, "google_weather")
        self.assertTrue(result.request_success)
        self.assertFalse(result.data_available)
        self.assertTrue(result.degraded)
        self.assertEqual(result.reason, "unsupported_location")
        rendered = output.getvalue()
        self.assertIn(
            "provider=google endpoint=weather_forecast "
            "category=unsupported_location status=404 retryable=false",
            rendered,
        )
        for forbidden in (
            "fake-key", "example.invalid", "provider detail", "test city",
            str(self.location.latitude), str(self.location.longitude),
        ):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
