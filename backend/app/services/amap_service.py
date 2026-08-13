"""Typed AMap Web Service REST adapter with explicit failure semantics."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import httpx

from ..config import get_amap_web_service_key
from ..models.schemas import (
    AmapFailureReason,
    AmapGeocodeResult,
    AmapPOISearchResult,
    AmapRouteResult,
    Location,
    POIInfo,
    WeatherInfo,
    WeatherResult,
    has_valid_verified_coordinates,
)


def _log_failure(endpoint: str, reason: str, status: Optional[int] = None) -> None:
    """Log metadata only; never render a request URL, parameters, or secret."""
    print(
        f"❌ provider=amap endpoint={endpoint} category={reason} "
        f"status={status if status is not None else 'none'}"
    )


class AmapService:
    """Production AMap adapter; no MCP/uvx runtime is required."""

    PLACE_TEXT_URL = "https://restapi.amap.com/v3/place/text"
    PLACE_DETAIL_URL = "https://restapi.amap.com/v3/place/detail"
    GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
    WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
    ROUTE_URLS = {
        "walking": "https://restapi.amap.com/v3/direction/walking",
        "driving": "https://restapi.amap.com/v3/direction/driving",
        "transit": "https://restapi.amap.com/v3/direction/transit/integrated",
    }

    def __init__(self, api_key: Optional[str] = None, client: Optional[Any] = None):
        self.api_key = get_amap_web_service_key() if api_key is None else api_key
        self._client = client or httpx.Client(timeout=10)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _http_reason(status: int) -> Optional[AmapFailureReason]:
        if status == 401:
            return "authentication_failed"
        if status == 403:
            return "permission_denied"
        if status == 429:
            return "rate_limited"
        return None

    @staticmethod
    def _business_reason(infocode: str) -> AmapFailureReason:
        if infocode in {"10001", "10002", "10007", "10008", "10012"}:
            return "authentication_failed"
        if infocode in {"10005", "10006", "10009", "10011", "10014"}:
            return "permission_denied"
        if infocode in {"10003", "10004", "10010", "10019", "10020", "10021"}:
            return "rate_limited"
        return "business_error"

    def _get_json(
        self, endpoint: str, url: str, params: Dict[str, Any]
    ) -> Tuple[Optional[dict], bool, Optional[AmapFailureReason]]:
        if not self.api_key:
            return None, False, "key_missing"
        safe_params = {**params, "key": self.api_key, "output": "JSON"}
        try:
            response = self._client.get(url, params=safe_params)
            status = int(getattr(response, "status_code", 200))
            reason = self._http_reason(status)
            if reason:
                _log_failure(endpoint, reason, status)
                return None, False, reason
            response.raise_for_status()
            try:
                payload = response.json()
            except Exception:
                _log_failure(endpoint, "malformed_response", status)
                return None, True, "malformed_response"
            if not isinstance(payload, dict):
                return None, True, "malformed_response"
            if payload.get("status") != "1":
                infocode = str(payload.get("infocode", ""))
                reason = self._business_reason(infocode)
                _log_failure(endpoint, reason, status)
                return payload, False, reason
            return payload, True, None
        except httpx.TimeoutException:
            _log_failure(endpoint, "timeout")
            return None, False, "timeout"
        except httpx.HTTPStatusError as exc:
            status = int(exc.response.status_code) if exc.response is not None else 0
            reason = self._http_reason(status) or "business_error"
            _log_failure(endpoint, reason, status or None)
            return None, False, reason
        except httpx.RequestError:
            _log_failure(endpoint, "network_error")
            return None, False, "network_error"
        except Exception:
            _log_failure(endpoint, "malformed_response")
            return None, False, "malformed_response"

    @staticmethod
    def _parse_location(value: Any) -> Optional[Location]:
        try:
            longitude, latitude = str(value).split(",", 1)
            lon, lat = float(longitude), float(latitude)
            if not has_valid_verified_coordinates({"longitude": lon, "latitude": lat}):
                return None
            return Location(longitude=lon, latitude=lat)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _unavailable_poi(request_success: bool, reason: AmapFailureReason) -> AmapPOISearchResult:
        return AmapPOISearchResult(
            provider="unavailable", request_success=request_success,
            data_available=False, degraded=True, reason=reason,
        )

    def search_poi(self, keywords: str, city: str, citylimit: bool = True) -> AmapPOISearchResult:
        payload, request_success, reason = self._get_json(
            "places_text_search", self.PLACE_TEXT_URL,
            {
                "keywords": keywords, "city": city,
                "citylimit": str(citylimit).lower(), "offset": 20, "page": 1,
            },
        )
        if reason:
            return self._unavailable_poi(request_success, reason)
        raw_pois = payload.get("pois", []) if payload else []
        if not isinstance(raw_pois, list):
            return self._unavailable_poi(True, "malformed_response")
        if not raw_pois:
            return self._unavailable_poi(True, "empty_result")
        results = []
        for raw in raw_pois:
            if not isinstance(raw, dict):
                continue
            location = self._parse_location(raw.get("location"))
            if not location or not raw.get("id") or not raw.get("name"):
                continue
            address = raw.get("address", "")
            if isinstance(address, list):
                address = "".join(str(part) for part in address)
            results.append(POIInfo(
                id=str(raw.get("id", "")), name=str(raw.get("name", "")),
                type=str(raw.get("type", "")), address=str(address or ""),
                district=str(raw.get("adname", "") or ""), location=location,
                tel=str(raw.get("tel", "") or "") or None,
                data_source="amap", verification_status="verified",
            ))
        if not results:
            return self._unavailable_poi(True, "malformed_response")
        return AmapPOISearchResult(
            provider="amap", request_success=True, data_available=True,
            degraded=False, data=results,
        )

    def geocode(self, address: str, city: Optional[str] = None) -> AmapGeocodeResult:
        payload, request_success, reason = self._get_json(
            "geocoding", self.GEOCODE_URL,
            {"address": address, "city": city or ""},
        )
        if reason:
            return AmapGeocodeResult(
                provider="unavailable", request_success=request_success,
                data_available=False, degraded=True, reason=reason,
            )
        geocodes = payload.get("geocodes", []) if payload else []
        if not isinstance(geocodes, list):
            return AmapGeocodeResult(
                provider="unavailable", request_success=True,
                data_available=False, degraded=True, reason="malformed_response",
            )
        if not geocodes:
            return AmapGeocodeResult(
                provider="unavailable", request_success=True,
                data_available=False, degraded=True, reason="empty_result",
            )
        first = geocodes[0]
        if not isinstance(first, dict):
            return AmapGeocodeResult(
                provider="unavailable", request_success=True,
                data_available=False, degraded=True, reason="malformed_response",
            )
        location = self._parse_location(first.get("location"))
        if not location:
            return AmapGeocodeResult(
                provider="unavailable", request_success=True,
                data_available=False, degraded=True, reason="malformed_response",
            )
        return AmapGeocodeResult(
            provider="amap", request_success=True, data_available=True,
            location=location, formatted_address=str(first.get("formatted_address", "") or ""),
            resolution_path="geocoding",
        )

    def resolve_place(
        self, value: str, city: Optional[str] = None, *, prefer_poi: bool = False
    ) -> AmapGeocodeResult:
        """Resolve an address or named place without inventing coordinates.

        Callers already know whether their input is a POI candidate or an address,
        so this method only controls endpoint order; it does not guess input type.
        """
        def from_poi() -> AmapGeocodeResult:
            search = self.search_poi(value, city or "", True)
            if not search.data_available:
                return AmapGeocodeResult(
                    provider="unavailable", request_success=search.request_success,
                    data_available=False, degraded=True, reason=search.reason,
                )
            poi = search.data[0]
            return AmapGeocodeResult(
                provider="amap", request_success=True, data_available=True,
                location=poi.location, poi_id=poi.id,
                formatted_address=poi.address, resolution_path="poi_search",
            )

        primary = from_poi() if prefer_poi else self.geocode(value, city)
        if primary.data_available:
            return primary
        fallback = self.geocode(value, city) if prefer_poi else from_poi()
        if fallback.data_available:
            return fallback
        return fallback

    def get_weather(self, city: str, *, degraded: bool = True) -> WeatherResult:
        payload, request_success, reason = self._get_json(
            "weather", self.WEATHER_URL,
            {"city": city.split("-")[-1].strip(), "extensions": "all"},
        )
        if reason:
            weather_reason = reason if reason in {
                "key_missing", "authentication_failed", "permission_denied",
                "rate_limited", "timeout", "network_error", "malformed_response",
            } else "malformed_response"
            return WeatherResult(
                provider="unavailable", city=city, request_success=request_success,
                data_available=False, degraded=True, reason=weather_reason,
            )
        forecasts = payload.get("forecasts", []) if payload else []
        if not isinstance(forecasts, list):
            forecasts = None
        if forecasts is None or (forecasts and not isinstance(forecasts[0], dict)):
            return WeatherResult(
                provider="unavailable", city=city, request_success=True,
                data_available=False, degraded=True, reason="malformed_response",
            )
        casts = forecasts[0].get("casts", []) if forecasts else []
        if not isinstance(casts, list):
            return WeatherResult(
                provider="unavailable", city=city, request_success=True,
                data_available=False, degraded=True, reason="malformed_response",
            )
        days = []
        for cast in casts:
            if not isinstance(cast, dict) or not str(cast.get("date") or "").strip():
                continue
            day = WeatherInfo(
                date=str(cast["date"]).strip(), city=city,
                day_weather=str(cast.get("dayweather") or ""),
                night_weather=str(cast.get("nightweather") or ""),
                day_temp=cast.get("daytemp"), night_temp=cast.get("nighttemp"),
                wind_direction=str(cast.get("daywind") or ""),
                wind_power=str(cast.get("daypower") or ""),
                data_source="amap", verification_status="verified", degraded=degraded,
            )
            if day.day_temp is None or day.night_temp is None:
                continue
            days.append(day)
        if not days:
            return WeatherResult(
                provider="unavailable", city=city, request_success=True,
                data_available=False, degraded=True, reason="empty_forecast",
            )
        return WeatherResult(
            provider="amap", city=city, request_success=True, data_available=True,
            degraded=degraded, days=days,
        )

    @staticmethod
    def _coordinate_string(location: Location) -> str:
        return f"{location.longitude},{location.latitude}"

    def _resolve_location(
        self, value: Any, city: Optional[str]
    ) -> Tuple[Optional[Location], bool, Optional[AmapFailureReason]]:
        if isinstance(value, Location):
            if has_valid_verified_coordinates(value):
                return value, True, None
            return None, False, "malformed_response"
        parsed = self._parse_location(value)
        if parsed:
            return parsed, True, None
        geocoded = self.resolve_place(str(value), city)
        if geocoded.data_available and geocoded.location:
            return geocoded.location, True, None
        return None, geocoded.request_success, geocoded.reason or "empty_result"

    def plan_route(
        self,
        origin_address: Any,
        destination_address: Any,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        route_type: str = "walking",
    ) -> AmapRouteResult:
        if route_type not in self.ROUTE_URLS:
            return AmapRouteResult(
                provider="unavailable", request_success=False, data_available=False,
                degraded=True, reason="unsupported_mode", route_mode=route_type,
            )
        if not self.api_key:
            return AmapRouteResult(
                provider="unavailable", request_success=False, data_available=False,
                degraded=True, reason="key_missing", route_mode=route_type,
            )
        origin, origin_success, origin_reason = self._resolve_location(origin_address, origin_city)
        if not origin:
            return AmapRouteResult(
                provider="unavailable", request_success=origin_success, data_available=False,
                degraded=True, reason=origin_reason, route_mode=route_type,
            )
        destination, destination_success, destination_reason = self._resolve_location(
            destination_address, destination_city
        )
        if not destination:
            return AmapRouteResult(
                provider="unavailable", request_success=destination_success, data_available=False,
                degraded=True, reason=destination_reason, route_mode=route_type,
            )
        params: Dict[str, Any] = {
            "origin": self._coordinate_string(origin),
            "destination": self._coordinate_string(destination),
        }
        if route_type == "transit":
            params["city"] = origin_city or ""
            params["cityd"] = destination_city or origin_city or ""
        payload, request_success, reason = self._get_json(
            f"directions_{route_type}", self.ROUTE_URLS[route_type], params,
        )
        if reason:
            return AmapRouteResult(
                provider="unavailable", request_success=request_success,
                data_available=False, degraded=True, reason=reason, route_mode=route_type,
            )
        route = payload.get("route", {}) if payload else {}
        if not isinstance(route, dict):
            return AmapRouteResult(
                provider="unavailable", request_success=True, data_available=False,
                degraded=True, reason="malformed_response", route_mode=route_type,
            )
        candidates = route.get("transits" if route_type == "transit" else "paths", [])
        if not isinstance(candidates, list):
            return AmapRouteResult(
                provider="unavailable", request_success=True, data_available=False,
                degraded=True, reason="malformed_response", route_mode=route_type,
            )
        if not candidates:
            return AmapRouteResult(
                provider="unavailable", request_success=True, data_available=False,
                degraded=True, reason="empty_result", route_mode=route_type,
            )
        try:
            first = candidates[0]
            distance = float(first["distance"])
            duration = int(float(first["duration"]))
        except (KeyError, TypeError, ValueError, IndexError):
            return AmapRouteResult(
                provider="unavailable", request_success=True, data_available=False,
                degraded=True, reason="malformed_response", route_mode=route_type,
            )
        return AmapRouteResult(
            provider="amap", request_success=True, data_available=True,
            degraded=False, distance=distance, duration=duration, route_mode=route_type,
        )

    def get_poi_detail(self, poi_id: str) -> Dict[str, Any]:
        payload, _, reason = self._get_json(
            "place_details", self.PLACE_DETAIL_URL, {"id": poi_id},
        )
        if reason or not payload:
            return {}
        pois = payload.get("pois", [])
        return pois[0] if pois else {}


_amap_service: Optional[AmapService] = None


def get_amap_service() -> AmapService:
    global _amap_service
    if _amap_service is None:
        _amap_service = AmapService()
    return _amap_service


def reset_amap_service() -> None:
    global _amap_service
    if _amap_service is not None:
        _amap_service.close()
    _amap_service = None
