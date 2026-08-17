import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from backend.app import config
from backend.app.agents.trip_planner_agent import MultiAgentTripPlanner
from backend.app.api.routes.poi import get_attraction_photo
from backend.app.api.routes.settings import (
    RuntimeSettingsPayload,
    get_settings as get_settings_route,
    save_settings,
)
from backend.app.models.schemas import (
    AmapGeocodeResult, Attraction, DayPlan, Location, TripPlan, TripPlanResponse,
    WeatherInfo, WeatherResult, XHSEvidence, XHSResearchResult,
)
from backend.app.services import xhs_service
from backend.app.services.google_map_service import GoogleMapService
from backend.app.services.amap_service import AmapService


def _plan_with_malicious_map_facts():
    return TripPlan(
        city="东京", start_date="2026-08-01", end_date="2026-08-01",
        days=[DayPlan(
            date="2026-08-01", day_index=0, city="东京", description="test",
            transportation="步行", accommodation="酒店", meals=[],
            attractions=[Attraction(
                name="候选", address="Planner text address",
                location=Location(longitude=139.7, latitude=35.6),
                visit_duration=60, description="test", place_id="fake", poi_id="fake",
                poi_match_status="verified", map_data_source="google_places", rating=5,
                photos=["fake-photo"], image_url="fake-image",
            )],
        )], weather_info=[], overall_suggestions="test",
    )


class _Response:
    def __init__(self, status=200, payload=None, malformed=False):
        self.status_code = status
        self.payload = payload or {}
        self.malformed = malformed

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.test")
            raise httpx.HTTPStatusError("status", request=request, response=httpx.Response(self.status_code, request=request))

    def json(self):
        if self.malformed:
            raise json.JSONDecodeError("bad", "", 0)
        return self.payload


class Phase2DSettingsTests(unittest.TestCase):
    def test_settings_get_never_returns_server_secrets(self):
        originals = (
            config.settings.openai_api_key, config.settings.xhs_cookie,
            config.settings.amap_web_service_key, config.settings.google_maps_proxy,
        )
        try:
            config.settings.openai_api_key = "openai-secret"
            config.settings.xhs_cookie = "xhs-secret"
            config.settings.amap_web_service_key = "amap-secret"
            config.settings.google_maps_proxy = "http://proxy-user:proxy-password@proxy.invalid:8080"
            response = asyncio.run(get_settings_route())
            serialized = str(response)
            for secret in (
                "openai-secret", "xhs-secret", "amap-secret",
                "proxy-user", "proxy-password", "proxy.invalid",
            ):
                self.assertNotIn(secret, serialized)
            self.assertTrue(response["data"]["openai_configured"])
            self.assertTrue(response["data"]["xhs_configured"])
            self.assertTrue(response["data"]["amap_server_configured"])
            self.assertTrue(response["data"]["google_maps_proxy_configured"])
        finally:
            (
                config.settings.openai_api_key, config.settings.xhs_cookie,
                config.settings.amap_web_service_key, config.settings.google_maps_proxy,
            ) = originals

    def test_secret_put_is_disabled_by_default(self):
        with self.assertRaises(ValidationError):
            RuntimeSettingsPayload.model_validate({"xhs_cookie": "fake-secret"})
        with self.assertRaises(ValidationError):
            RuntimeSettingsPayload.model_validate({
                "google_maps_proxy": "http://user:password@proxy.invalid"
            })

    def test_proxy_credentials_are_redacted_from_logs(self):
        authenticated_proxy = "http://proxy-user:proxy-password@proxy.invalid:8080"
        original = config.settings.google_maps_proxy
        output = io.StringIO()
        try:
            config.settings.google_maps_proxy = authenticated_proxy
            with redirect_stdout(output):
                config.print_config()
                with patch("backend.app.services.google_map_service.httpx.Client"):
                    service = GoogleMapService("test-key", authenticated_proxy)
                    service.close()
        finally:
            config.settings.google_maps_proxy = original
        rendered = output.getvalue()
        self.assertIn("Proxy", rendered)
        for secret in ("proxy-user", "proxy-password", "proxy.invalid"):
            self.assertNotIn(secret, rendered)

    def test_runtime_settings_file_is_owner_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime_settings.json"
            with patch.object(config, "_RUNTIME_SETTINGS_FILE", path):
                config._persist_runtime_overrides({
                    "openai_model": "test",
                    "google_maps_proxy": "http://user:password@proxy.invalid",
                })
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            persisted = path.read_text(encoding="utf-8")
            self.assertNotIn("google_maps_proxy", persisted)
            self.assertNotIn("user", persisted)
            self.assertNotIn("password", persisted)


class Phase2DMapSanitationTests(unittest.TestCase):
    def test_initial_planner_external_facts_are_stripped(self):
        result = MultiAgentTripPlanner._sanitize_external_facts(_plan_with_malicious_map_facts())
        poi = result.days[0].attractions[0]
        self.assertFalse(poi.place_id)
        self.assertFalse(poi.poi_id)
        self.assertEqual(poi.poi_match_status, "unverified")
        self.assertEqual(poi.map_data_source, "llm_unverified")
        self.assertEqual((poi.location.longitude, poi.location.latitude), (0, 0))
        self.assertIsNone(poi.rating)
        self.assertEqual(poi.photos, [])
        self.assertIsNone(poi.image_url)

    def test_amap_failure_never_returns_default_beijing_coordinate(self):
        missing = AmapGeocodeResult(
            provider="unavailable", request_success=False, data_available=False,
            degraded=True, reason="key_missing",
        )
        timeout = AmapGeocodeResult(
            provider="unavailable", request_success=False, data_available=False,
            degraded=True, reason="timeout",
        )
        fake_missing = SimpleNamespace(resolve_place=lambda *_args, **_kwargs: missing)
        fake_timeout = SimpleNamespace(resolve_place=lambda *_args, **_kwargs: timeout)
        with patch("backend.app.services.amap_service.get_amap_service", return_value=fake_missing):
            self.assertIsNone(xhs_service._geocode_amap_raw("浅草寺", "东京"))
        with patch("backend.app.services.amap_service.get_amap_service", return_value=fake_timeout):
            self.assertIsNone(xhs_service._geocode_amap_raw("浅草寺", "东京"))


class Phase2DWeatherTests(unittest.TestCase):
    def setUp(self):
        self.service = GoogleMapService("test")
        self.location = Location(longitude=139.7, latitude=35.6)

    def tearDown(self):
        self.service.close()

    def _result(self, response):
        self.service._client = SimpleNamespace(get=lambda *_args, **_kwargs: response, close=lambda: None)
        with patch.object(self.service, "geocode", return_value=self.location):
            return self.service.get_weather("东京")

    def test_google_weather_valid_and_empty(self):
        day = {
            "displayDate": {"year": 2026, "month": 8, "day": 1},
            "daytimeForecast": {"weatherCondition": "CLEAR", "wind": {}},
            "nighttimeForecast": {"weatherCondition": "CLEAR"},
            "maxTemperature": {"degrees": 30}, "minTemperature": {"degrees": 24},
        }
        valid = self._result(_Response(payload={"forecastDays": [day]}))
        self.assertTrue(valid.data_available)
        self.assertEqual(valid.provider, "google_weather")
        empty = self._result(_Response(payload={"forecastDays": []}))
        self.assertFalse(empty.data_available)
        self.assertEqual(empty.reason, "empty_forecast")

    def test_google_weather_http_classification(self):
        expected = {401: "authentication_failed", 403: "permission_denied", 429: "rate_limited"}
        for status, reason in expected.items():
            with self.subTest(status=status):
                self.assertEqual(self._result(_Response(status=status)).reason, reason)

    def test_google_weather_timeout_and_malformed(self):
        self.service._client = SimpleNamespace(
            get=lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.TimeoutException("timeout")),
            close=lambda: None,
        )
        with patch.object(self.service, "geocode", return_value=self.location):
            self.assertEqual(self.service.get_weather("东京").reason, "timeout")
        self.assertEqual(self._result(_Response(malformed=True)).reason, "malformed_response")

    def test_no_provider_means_no_weather_days(self):
        result = WeatherResult(
            provider="unavailable", city="东京", request_success=False,
            data_available=False, degraded=True, reason="key_missing",
        )
        self.assertEqual(result.days, [])


class Phase2DAmapWeatherTests(unittest.IsolatedAsyncioTestCase):
    async def _fetch(self, response=None, error=None, key="key"):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        client = SimpleNamespace(
            get=lambda *_args, **_kwargs: (_ for _ in ()).throw(error) if error else response,
            close=lambda: None,
        )
        planner._amap_service = AmapService(key, client)
        with patch("backend.app.services.amap_service.get_amap_service", return_value=planner._amap_service):
            return await planner._fallback_amap_weather("东京")

    async def test_amap_weather_valid_missing_invalid_empty_timeout(self):
        valid_payload = {"status": "1", "forecasts": [{"casts": [{
            "date": "2026-08-01", "dayweather": "晴", "nightweather": "多云",
            "daytemp": "31", "nighttemp": "25", "daywind": "东", "daypower": "3",
        }]}]}
        valid = await self._fetch(_Response(payload=valid_payload))
        self.assertTrue(valid.data_available)
        self.assertEqual(valid.provider, "amap")
        self.assertTrue(valid.degraded)

        missing = await self._fetch(key="")
        self.assertEqual(missing.reason, "key_missing")
        invalid = await self._fetch(_Response(payload={"status": "0", "infocode": "10001"}))
        self.assertEqual(invalid.reason, "authentication_failed")
        empty = await self._fetch(_Response(payload={"status": "1", "forecasts": []}))
        self.assertEqual(empty.reason, "empty_forecast")
        timeout = await self._fetch(error=httpx.TimeoutException("timeout"))
        self.assertEqual(timeout.reason, "timeout")


class Phase2DWeatherFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def _retrieve_after_google_404(self, amap_result):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner.map_provider = "google"
        planner._weather_results = {}
        planner._google_service = SimpleNamespace(get_weather=lambda _city: WeatherResult(
            provider="google_weather", city="京都", request_success=True,
            data_available=False, degraded=True, reason="unsupported_location",
        ))
        planner._amap_service = SimpleNamespace(
            get_weather=lambda _city, degraded=True: amap_result,
        )
        await planner._retrieve_weather_context("京都")
        return planner._weather_results["京都"]

    async def test_google_404_still_falls_back_to_successful_amap(self):
        amap = WeatherResult(
            provider="amap", city="京都", request_success=True,
            data_available=True, degraded=True, days=[WeatherInfo(
                date="2026-08-17", city="京都", day_weather="晴",
                night_weather="多云", day_temp=30, night_temp=24,
                data_source="amap", verification_status="verified", degraded=True,
            )],
        )
        result = await self._retrieve_after_google_404(amap)
        self.assertIs(result, amap)
        self.assertEqual(result.provider, "amap")
        self.assertTrue(result.data_available)
        self.assertEqual(len(result.days), 1)
        self.assertEqual(result.primary_failure_reason, "unsupported_location")

    async def test_google_404_still_preserves_failed_amap_fallback(self):
        amap = WeatherResult(
            provider="unavailable", city="京都", request_success=True,
            data_available=False, degraded=True, reason="empty_forecast",
        )
        result = await self._retrieve_after_google_404(amap)
        self.assertIs(result, amap)
        self.assertEqual(result.provider, "unavailable")
        self.assertFalse(result.data_available)
        self.assertEqual(result.reason, "empty_forecast")
        self.assertEqual(result.primary_failure_reason, "unsupported_location")

    async def test_ordinary_google_failure_does_not_become_unsupported(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner.map_provider = "google"
        planner._weather_results = {}
        planner._google_service = SimpleNamespace(get_weather=lambda _city: WeatherResult(
            provider="google_weather", city="测试市", request_success=False,
            data_available=False, degraded=True, reason="timeout",
        ))
        amap = WeatherResult(
            provider="unavailable", city="测试市", request_success=False,
            data_available=False, degraded=True, reason="network_error",
        )
        planner._amap_service = SimpleNamespace(
            get_weather=lambda _city, degraded=True: amap,
        )

        await planner._retrieve_weather_context("测试市")

        result = planner._weather_results["测试市"]
        self.assertEqual(result.reason, "network_error")
        self.assertEqual(result.primary_failure_reason, "timeout")

    async def test_supported_google_weather_reaches_trip_api_contract(self):
        day = WeatherInfo(
            date="2026-08-17", city="支持地区", day_weather="晴",
            night_weather="多云", day_temp=28, night_temp=21,
            data_source="google_weather", verification_status="verified",
        )
        google = WeatherResult(
            provider="google_weather", city="支持地区", request_success=True,
            data_available=True, degraded=False, days=[day],
        )
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner.map_provider = "google"
        planner._weather_results = {}
        planner._google_service = SimpleNamespace(get_weather=lambda _city: google)
        planner._amap_service = SimpleNamespace(
            get_weather=lambda *_args, **_kwargs: self.fail("AMap fallback must not run"),
        )

        await planner._retrieve_weather_context("支持地区")

        plan = _plan_with_malicious_map_facts()
        plan.weather_results = [planner._weather_results["支持地区"]]
        plan.weather_info = list(plan.weather_results[0].days)
        payload = TripPlanResponse(success=True, data=plan).model_dump(mode="json")
        self.assertEqual(payload["data"]["weather_info"][0]["date"], "2026-08-17")
        self.assertEqual(
            payload["data"]["weather_info"][0]["data_source"],
            "google_weather",
        )


class Phase2DXHSTests(unittest.IsolatedAsyncioTestCase):
    async def test_evidence_is_retained_and_required_for_xhs_claim(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        research = XHSResearchResult(
            status="available", verification_status="verified", degraded=False,
            evidence=[XHSEvidence(
                note_id="note-1", title="东京攻略",
                source_url="https://www.xiaohongshu.com/explore/note-1",
                status="detail_available", extracted_text="真实正文",
            )],
            context="来自有证据支持的小红书用户经验",
        )
        context = await planner._search_attractions_with_xhs_fallback(
            "东京", "景点", "zh", None, 10, search_func=lambda *_args: research,
        )
        self.assertIn("有证据", context)
        self.assertEqual(planner._xhs_results["东京"].evidence[0].note_id, "note-1")

    async def test_empty_research_falls_back_without_xhs_claim(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        empty = XHSResearchResult(
            status="unavailable", verification_status="unavailable", degraded=True,
            reason="empty_search", evidence=[], context="",
        )
        context = await planner._search_attractions_with_xhs_fallback(
            "东京", "景点", "zh", None, 10, search_func=lambda *_args: empty,
        )
        self.assertIn("没有来自小红书", context)
        self.assertIn("不得声称候选来自小红书", context)

    async def test_classified_risk_control_fails_open(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        def blocked(*_args):
            raise xhs_service.XHSRequestError("permission_denied", "risk controlled")
        context = await planner._search_attractions_with_xhs_fallback(
            "东京", "景点", "zh", None, 10, search_func=blocked,
        )
        self.assertIn("不得声称", context)
        self.assertEqual(planner._xhs_results["东京"].reason, "permission_denied")


class _XHSClient:
    def __init__(self, *, items=None, detail=None, detail_error=None):
        self.items = items or []
        self.detail = detail or {}
        self.detail_error = detail_error

    def search_notes(self, **_kwargs):
        return {"success": True, "data": {"items": self.items}}

    def get_note_detail(self, *_args):
        if self.detail_error:
            raise self.detail_error
        return self.detail


class Phase2DXHSConnectorTests(unittest.TestCase):
    def _item(self, note_id="note-1", title="东京攻略"):
        return {
            "model_type": "note", "id": note_id, "xsec_token": "token",
            "note_card": {"display_title": title},
        }

    def _completion(self, content='[{"name":"东京攻略","identity_text":"东京攻略","name_zh":"东京攻略","name_en":"Tokyo guide","recommendation":"真实游记建议","duration":90,"reservation_required":false,"reservation_tips":"","evidence_ids":["note-1"],"evidence_support":[{"evidence_id":"note-1","identity_quote":"东京攻略","recommendation_quote":"真实游记正文"}]}]'):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    def _extract(self, content, items=None, detail_text="浅草寺建议早到，也可以慢慢游览。真实游记正文"):
        detail = {"data": {"items": [{"note_card": {"desc": detail_text}}]}}
        client = _XHSClient(items=items or [self._item()], detail=detail)
        with patch.object(xhs_service, "get_xhs_client", return_value=client), patch.object(
            xhs_service, "get_llm", return_value=SimpleNamespace(model="fake")
        ), patch("backend.app.services.llm_service.create_chat_completion", return_value=self._completion(content)) as completion, patch.object(
            xhs_service, "geocode_amap", return_value=None
        ):
            result = xhs_service.search_xhs_attractions("东京", "景点")
        return result, completion

    def test_empty_search_is_unavailable(self):
        with patch.object(xhs_service, "get_xhs_client", return_value=_XHSClient(items=[])):
            result = xhs_service.search_xhs_attractions("东京", "景点")
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "empty_search")

    def test_detail_failure_uses_ssr_and_retains_evidence(self):
        client = _XHSClient(items=[self._item()], detail_error=RuntimeError("detail failed"))
        with patch.object(xhs_service, "get_xhs_client", return_value=client), patch.object(
            xhs_service, "get_note_detail_ssr", return_value={"desc": "真实游记正文"}
        ), patch.object(xhs_service, "get_llm", return_value=SimpleNamespace(model="fake")), patch(
            "backend.app.services.llm_service.create_chat_completion", return_value=self._completion()
        ), patch.object(xhs_service, "geocode_amap", return_value=None):
            result = xhs_service.search_xhs_attractions("东京", "景点")
        self.assertEqual(result.status, "available")
        self.assertEqual(result.evidence[0].note_id, "note-1")
        self.assertEqual(result.evidence[0].status, "detail_available")
        self.assertIn("/explore/note-1", result.evidence[0].source_url)

    def test_detail_and_ssr_failure_do_not_call_extraction(self):
        client = _XHSClient(items=[self._item()], detail_error=RuntimeError("detail failed"))
        with patch.object(xhs_service, "get_xhs_client", return_value=client), patch.object(
            xhs_service, "get_note_detail_ssr", return_value={}
        ), patch("backend.app.services.llm_service.create_chat_completion") as completion:
            result = xhs_service.search_xhs_attractions("东京", "景点")
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "detail_unavailable")
        completion.assert_not_called()

    def test_extraction_failure_preserves_evidence_but_is_unavailable(self):
        detail = {"data": {"items": [{"note_card": {"desc": "真实正文"}}]}}
        client = _XHSClient(items=[self._item()], detail=detail)
        with patch.object(xhs_service, "get_xhs_client", return_value=client), patch.object(
            xhs_service, "get_llm", return_value=SimpleNamespace(model="fake")
        ), patch("backend.app.services.llm_service.create_chat_completion", side_effect=RuntimeError("offline")):
            result = xhs_service.search_xhs_attractions("东京", "景点")
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "extraction_failed")
        self.assertEqual(result.evidence[0].note_id, "note-1")

    def test_item_can_reference_one_real_evidence_and_context_has_lookup(self):
        content = '[{"name":"浅草寺","identity_text":"浅草寺","recommendation":"建议早到","evidence_ids":["note-1"],"evidence_support":[{"evidence_id":"note-1","identity_quote":"浅草寺","recommendation_quote":"建议早到"}]}]'
        result, completion = self._extract(content)
        self.assertEqual(result.status, "available")
        self.assertEqual(result.extracted_items[0].evidence_ids, ["note-1"])
        self.assertIn("note-1", result.context)
        self.assertIn("/explore/note-1", result.context)
        completion.assert_called_once()

    def test_item_can_reference_multiple_real_evidence(self):
        content = '[{"name":"浅草寺","identity_text":"浅草寺","recommendation":"建议早到","evidence_ids":["note-1","note-2"],"evidence_support":[{"evidence_id":"note-1","identity_quote":"浅草寺","recommendation_quote":"建议早到"},{"evidence_id":"note-2","identity_quote":"浅草寺","recommendation_quote":"建议早到"}]}]'
        result, _ = self._extract(content, [self._item("note-1"), self._item("note-2")])
        self.assertEqual(result.extracted_items[0].evidence_ids, ["note-1", "note-2"])

    def test_unknown_evidence_id_is_removed_without_global_attachment(self):
        content = '[{"name":"浅草寺","identity_text":"浅草寺","recommendation":"建议早到","evidence_ids":["note-1","invented"],"evidence_support":[{"evidence_id":"note-1","identity_quote":"浅草寺","recommendation_quote":"建议早到"},{"evidence_id":"invented","identity_quote":"浅草寺","recommendation_quote":"建议早到"}]}]'
        result, _ = self._extract(content, [self._item("note-1"), self._item("note-2")])
        self.assertEqual(result.extracted_items[0].evidence_ids, ["note-1"])
        self.assertNotIn("note-2", result.extracted_items[0].evidence_ids)
        self.assertNotIn("invented", result.context)

    def test_only_unknown_or_empty_evidence_drops_item(self):
        for evidence_fragment in ('["invented"]', '[]'):
            with self.subTest(evidence_ids=evidence_fragment):
                content = f'{{"name":"浅草寺","identity_text":"浅草寺","recommendation":"建议早到","evidence_ids":{evidence_fragment},"evidence_support":[]}}'
                result, _ = self._extract(f'[{content}]')
                self.assertEqual(result.status, "unavailable")
                self.assertEqual(result.reason, "unsupported_extraction")
                self.assertEqual(result.extracted_items, [])

    def test_legacy_item_without_evidence_is_not_xhs_supported(self):
        content = '[{"name":"浅草寺","reason":"建议早到"}]'
        result, _ = self._extract(content)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "unsupported_extraction")
        self.assertEqual(result.context, "")

    def test_consensus_claim_is_dropped(self):
        content = '[{"name":"浅草寺","identity_text":"浅草寺","recommendation":"小红书普遍认为这是热门必去","evidence_ids":["note-1"],"evidence_support":[{"evidence_id":"note-1","identity_quote":"浅草寺","recommendation_quote":"建议早到"}]}]'
        result, _ = self._extract(content)
        self.assertEqual(result.status, "unavailable")
        self.assertNotIn("小红书普遍认为", result.context)

    def test_identity_cannot_be_more_specific_than_verbatim_evidence(self):
        content = '[{"name":"主题乐园分园","identity_text":"主题乐园分园","recommendation":"建议工作日去","evidence_ids":["note-1"],"evidence_support":[{"evidence_id":"note-1","identity_quote":"主题乐园","recommendation_quote":"建议早到"}]}]'
        result, _ = self._extract(content)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "unsupported_extraction")

    def test_ambiguous_identity_is_not_mapped_by_code(self):
        content = '[{"name":"浅草寺某分馆","identity_text":"浅草寺","recommendation":"建议早到","evidence_ids":["note-1"],"evidence_support":[{"evidence_id":"note-1","identity_quote":"浅草寺","recommendation_quote":"建议早到"}]}]'
        result, _ = self._extract(content)
        self.assertEqual(result.status, "unavailable")

    def test_irrelevant_extra_evidence_requires_its_own_real_quotes(self):
        content = '[{"name":"浅草寺","identity_text":"浅草寺","recommendation":"建议早到","evidence_ids":["note-1","note-2"],"evidence_support":[{"evidence_id":"note-1","identity_quote":"浅草寺","recommendation_quote":"建议早到"}]}]'
        result, _ = self._extract(content, [self._item("note-1"), self._item("note-2")])
        self.assertEqual(result.extracted_items[0].evidence_ids, ["note-1"])

    def test_free_recommendation_cannot_add_facts_beyond_reservation_quote(self):
        content = '[{"name":"观景台","identity_text":"观景台","recommendation":"天气好时看日落夜景，室外楼层视野很好","evidence_ids":["note-1"],"evidence_support":[{"evidence_id":"note-1","identity_quote":"观景台","recommendation_quote":"提前14天开票"}]}]'
        result, _ = self._extract(content, detail_text="观景台需要提前14天开票。")
        item = result.extracted_items[0]
        self.assertEqual(item.recommendation, "提前14天开票")
        self.assertEqual(item.evidence_summary, "提前14天开票")
        self.assertNotIn("夜景", result.context)
        self.assertNotIn("楼层", result.context)

    def test_name_only_evidence_cannot_create_route_recommendation(self):
        content = '[{"name":"河畔公园","identity_text":"河畔公园","recommendation":"适合与市中心安排在同一天","evidence_ids":["note-1"],"evidence_support":[{"evidence_id":"note-1","identity_quote":"河畔公园","recommendation_quote":"河畔公园"}]}]'
        result, _ = self._extract(content, detail_text="今天经过河畔公园。")
        self.assertEqual(result.extracted_items[0].recommendation, "河畔公园")
        self.assertNotIn("同一天", result.context)

    def test_explicit_sunset_and_night_quote_is_preserved(self):
        content = '[{"name":"山顶展望台","identity_text":"山顶展望台","recommendation":"适合看日落和夜景","evidence_ids":["note-1"],"evidence_support":[{"evidence_id":"note-1","identity_quote":"山顶展望台","recommendation_quote":"适合看日落和夜景"}]}]'
        result, _ = self._extract(content, detail_text="山顶展望台适合看日落和夜景。")
        self.assertEqual(result.extracted_items[0].recommendation, "适合看日落和夜景")

    def test_multi_evidence_summary_combines_only_verified_quotes(self):
        content = '[{"name":"旧城市场","identity_text":"旧城市场","recommendation":"建议早到并提前预约","evidence_ids":["note-1","note-2"],"evidence_support":[{"evidence_id":"note-1","identity_quote":"旧城市场","recommendation_quote":"建议早到"},{"evidence_id":"note-2","identity_quote":"旧城市场","recommendation_quote":"需要提前预约"}]}]'
        result, _ = self._extract(
            content,
            [self._item("note-1"), self._item("note-2")],
            detail_text="旧城市场建议早到，需要提前预约。",
        )
        self.assertEqual(result.extracted_items[0].recommendation, "建议早到；需要提前预约")


class Phase2DPhotoTrustTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_place_id_cannot_create_verified_photo(self):
        service = SimpleNamespace(
            match_poi=lambda *_args: {"status": "unverified", "poi": None},
            get_place_photo=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not call")),
        )
        with patch("backend.app.services.google_map_service.get_google_map_service", return_value=service), patch(
            "backend.app.services.xhs_service.get_photo_from_xhs", new=AsyncMock(return_value="")
        ):
            result = await get_attraction_photo("不存在", "东京", "fake-place-id")
        self.assertEqual(result["data"]["source"], "placeholder")
        self.assertNotEqual(result["data"].get("match_status"), "verified")


if __name__ == "__main__":
    unittest.main()
