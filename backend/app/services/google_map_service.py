"""Google Maps API 服务封装

提供与 AmapService 对等的 API 接口:
  - search_poi       → Places API (Text Search)
  - geocode          → Geocoding API
  - plan_route       → Routes / Directions API
  - get_poi_detail   → Places Details API
  - get_weather      → Weather API (current + forecast + history)
"""

import json
import math
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, Any, List, Optional

import httpx

from ..config import get_google_maps_server_api_key, get_settings
from ..models.schemas import (
    Location, POIInfo, WeatherInfo, WeatherResult, has_valid_verified_coordinates,
)


def _log_http_failure(endpoint: str, exc: Exception) -> None:
    """Log HTTP failure metadata without rendering request URLs or headers."""
    status = None
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
    if isinstance(exc, httpx.TimeoutException):
        category = "timeout"
    elif isinstance(exc, httpx.HTTPStatusError):
        category = "http_status"
    elif isinstance(exc, httpx.RequestError):
        category = "network_error"
    else:
        category = "unexpected_error"
    retryable = bool(
        isinstance(exc, (httpx.TimeoutException, httpx.RequestError))
        or (isinstance(status, int) and (status == 429 or status >= 500))
    )
    status_text = str(status) if status is not None else "none"
    print(
        f"❌ provider=google endpoint={endpoint} category={category} "
        f"status={status_text} retryable={str(retryable).lower()}"
    )


class GoogleMapService:
    """Google Maps Platform 服务封装类"""

    # Small, product-owned alias set for the Phase 1.5 Tokyo demo. Keys are
    # normalized exact aliases; values identify the same main POI entity.
    # This is deliberately not a general-purpose entity-resolution system.
    _PLACE_NAME_ALIASES = {
        "东京晴空塔": "tokyoskytree",
        "东京天空树": "tokyoskytree",
        "tokyoskytree": "tokyoskytree",
        "東京スカイツリー": "tokyoskytree",
        "阿美横丁": "ameyoko",
        "阿美横商店街": "ameyoko",
        "ameyoko": "ameyoko",
        "东京都厅展望室": "tokyometropolitanobservationdeck",
        "东京都厅观景台": "tokyometropolitanobservationdeck",
        "tokyometropolitangovernmentbuildingobservationdeck": "tokyometropolitanobservationdeck",
        "涩谷十字路口": "shibuyacrossing",
        "shibuyacrossing": "shibuyacrossing",
        "shibuyascramblecrossing": "shibuyacrossing",
    }

    # Generic entity vocabulary only; never add individual POI names here.
    _GENERIC_NAME_TERMS = (
        ("observationdeck", ("observationdeck", "观景台", "觀景台", "展望台", "展望室")),
        ("museum", ("museum", "博物馆", "博物館")),
        ("shrine", ("shrine", "神宫", "神宮")),
        ("park", ("park", "公园", "公園")),
        ("building", ("building", "站舍", "駅舎", "建筑", "建築")),
        ("station", ("station", "駅", "车站", "車站", "站")),
        ("market", ("market", "市场", "市場", "商店街", "购物街", "購物街", "shoppingstreet")),
        ("pier", (
            "waterbuspier", "waterbusstop", "cruisepier", "ferryterminal",
            "pier", "wharf", "码头", "碼頭", "船着场", "船着場", "桟橋",
            "乗り場", "乘船处", "乘船處",
        )),
        ("town", ("town", "タウン", "城")),
        ("sky", ("sky", "天空", "スカイ")),
    )
    _FACILITY_MODIFIERS = {
        "town": ("town", "城", "小镇", "小鎮"),
        "mall": ("mall", "商场", "商場", "购物中心", "購物中心"),
        "east_tower": ("easttower", "东塔", "東塔"),
        "west_tower": ("westtower", "西塔"),
        "observation_deck": ("observationdeck", "观景台", "觀景台", "展望台", "展望室"),
        "building": ("building", "站舍", "駅舎", "建筑", "建築"),
        "station": ("station", "车站", "車站", "駅", "站"),
        "pier": ("pier", "wharf", "码头", "碼頭", "船着场", "船着場", "桟橋", "乗り場"),
        "plaza": ("plaza", "广场", "廣場"),
        "parking": ("parking", "停车场", "停車場", "駐車場"),
        "store": ("store", "shop", "商店", "店"),
        "hotel": ("hotel", "酒店", "旅馆", "旅館"),
    }

    # --------------- 基础常量 ---------------
    PLACES_BASE = "https://places.googleapis.com/v1/places"
    GEOCODING_BASE = "https://maps.googleapis.com/maps/api/geocode/json"
    DIRECTIONS_BASE = "https://maps.googleapis.com/maps/api/directions/json"
    WEATHER_BASE = "https://weather.googleapis.com/v1/currentConditions"
    WEATHER_FORECAST_BASE = "https://weather.googleapis.com/v1/forecast/days"

    def __init__(self, api_key: str, proxy: str = ""):
        self.api_key = api_key
        # 创建带代理的持久化 HTTP 客户端
        # httpx 原生支持 http/https/socks5 代理
        client_kwargs: Dict[str, Any] = {"timeout": 15}
        if proxy:
            client_kwargs["proxy"] = proxy
            print("  - Google Maps 代理: 已配置")
        self._client = httpx.Client(**client_kwargs)

    def close(self) -> None:
        """关闭 HTTP 客户端连接池。"""
        self._client.close()

    # ======================== POI 搜索 ========================

    def search_poi(
        self,
        keywords: str,
        city: str,
        citylimit: bool = True,
        language_code: str = "zh-CN",
        address_hint: str = "",
        _diagnostics: Optional[Dict[str, bool]] = None,
        _containing_places: Optional[Dict[str, set[str]]] = None,
    ) -> List[POIInfo]:
        """
        使用 Places API (New) Text Search 搜索 POI

        https://developers.google.com/maps/documentation/places/web-service/text-search
        """
        url = f"{self.PLACES_BASE}:searchText"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,places.location,"
                "places.types,places.internationalPhoneNumber,places.rating,"
                "places.userRatingCount,places.photos,places.containingPlaces"
            ),
        }
        query_parts = [city, keywords, address_hint] if citylimit else [keywords, address_hint]
        body = {
            "textQuery": " ".join(part.strip() for part in query_parts if part and part.strip()),
            "languageCode": language_code,
            "maxResultCount": 5,
        }
        try:
            resp = self._client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            results: List[POIInfo] = []
            places = data.get("places", []) if isinstance(data, dict) else []
            if not isinstance(places, list):
                return []
            for place in places:
                if not isinstance(place, dict):
                    continue
                loc = place.get("location")
                display_name = place.get("displayName")
                place_id = place.get("id")
                name = display_name.get("text") if isinstance(display_name, dict) else ""
                if (
                    not place_id
                    or not name
                    or not isinstance(loc, dict)
                    or not has_valid_verified_coordinates(loc)
                ):
                    continue
                photos = place.get("photos") or []
                first_photo = photos[0] if photos else {}
                if _containing_places is not None:
                    raw_containing = place.get("containingPlaces") or []
                    if isinstance(raw_containing, list):
                        _containing_places[str(place_id)] = {
                            str(item.get("id") or "").strip()
                            for item in raw_containing
                            if isinstance(item, dict) and str(item.get("id") or "").strip()
                        }
                results.append(POIInfo(
                    id=str(place_id),
                    name=str(name),
                    type=",".join(place.get("types", [])[:3]),
                    address=place.get("formattedAddress", ""),
                    location=Location(
                        longitude=float(loc["longitude"]),
                        latitude=float(loc["latitude"]),
                    ),
                    tel=place.get("internationalPhoneNumber"),
                    rating=place.get("rating"),
                    user_rating_count=place.get("userRatingCount"),
                    photo_name=first_photo.get("name"),
                    photo_attributions=first_photo.get("authorAttributions") or [],
                    data_source="google_places",
                    verification_status="verified",
                ))
            return results
        except Exception as exc:
            if _diagnostics is not None:
                _diagnostics["provider_failure"] = True
            _log_http_failure("places_text_search", exc)
            return []

    # ======================== 地理编码 ========================

    def geocode(self, address: str, city: Optional[str] = None) -> Optional[Location]:
        """
        地理编码 (地址 → 坐标)

        https://developers.google.com/maps/documentation/geocoding
        """
        params: Dict[str, str] = {
            "address": f"{address}, {city}" if city else address,
            "key": self.api_key,
            "language": "zh-CN",
        }
        try:
            resp = self._client.get(self.GEOCODING_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if results:
                loc = results[0]["geometry"]["location"]
                return Location(longitude=loc["lng"], latitude=loc["lat"])
        except Exception as exc:
            _log_http_failure("geocoding", exc)
        return None

    def _geocode_place_id(self, place: str) -> str:
        """Resolve one trusted Google Place ID for geographic containment checks."""
        params: Dict[str, str] = {
            "address": place,
            "key": self.api_key,
            "language": "en",
        }
        try:
            resp = self._client.get(self.GEOCODING_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", []) if isinstance(data, dict) else []
            if isinstance(results, list) and results and isinstance(results[0], dict):
                return str(results[0].get("place_id") or "").strip()
        except Exception as exc:
            _log_http_failure("geocoding_city_containment", exc)
        return ""

    # ======================== 路线规划 ========================

    def plan_route(
        self,
        origin_address: str,
        destination_address: str,
        origin_city: Optional[str] = None,
        destination_city: Optional[str] = None,
        route_type: str = "walking",
    ) -> Dict[str, Any]:
        """
        路线规划 — 使用 Directions API

        https://developers.google.com/maps/documentation/directions
        """
        mode_map = {
            "walking": "walking",
            "driving": "driving",
            "transit": "transit",
        }
        params = {
            "origin": f"{origin_address}, {origin_city}" if origin_city else origin_address,
            "destination": f"{destination_address}, {destination_city}" if destination_city else destination_address,
            "mode": mode_map.get(route_type, "walking"),
            "key": self.api_key,
            "language": "zh-CN",
        }
        try:
            resp = self._client.get(self.DIRECTIONS_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("routes"):
                leg = data["routes"][0]["legs"][0]
                return {
                    "distance": leg["distance"]["value"],
                    "duration": leg["duration"]["value"],
                    "distance_text": leg["distance"]["text"],
                    "duration_text": leg["duration"]["text"],
                    "steps": [s["html_instructions"] for s in leg.get("steps", [])[:5]],
                    "route_type": mode_map.get(route_type, "walking"),
                    "data_source": "google_directions",
                }
        except Exception as exc:
            _log_http_failure("directions", exc)
        return {}

    # ======================== POI 详情 ========================

    def get_poi_detail(self, poi_id: str) -> Dict[str, Any]:
        """
        获取 Place 详情

        https://developers.google.com/maps/documentation/places/web-service/place-details
        """
        url = f"{self.PLACES_BASE}/{poi_id}"
        headers = {
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "id,displayName,formattedAddress,location,types,photos,editorialSummary,rating,userRatingCount",
        }
        try:
            resp = self._client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            _log_http_failure("place_details", exc)
            return {}

    @staticmethod
    def _normalize_place_name_text(value: str) -> str:
        """Normalize a display name for transparent, deterministic matching."""
        text = unicodedata.normalize("NFKC", value or "").casefold()
        # Small script-variant normalization, not a POI dictionary.
        text = text.translate(str.maketrans({
            "園": "园", "宮": "宫", "館": "馆", "觀": "观", "臺": "台",
            "澀": "涩", "渋": "涩", "賜": "赐", "廣": "广", "車": "车",
        }))
        return re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "", text)

    @classmethod
    def _normalize_generic_terms(cls, value: str) -> str:
        text = cls._normalize_place_name_text(value)
        # Reviewed main-entity aliases may be followed by a generic entity
        # category (for example the main POI's "Town" complex). Applying the
        # existing alias compositionally avoids adding a new POI-specific
        # alias while preserving the category for the scope guard.
        for alias, canonical in sorted(
            cls._PLACE_NAME_ALIASES.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if alias and alias in text:
                text = text.replace(alias, canonical)
        for canonical, variants in cls._GENERIC_NAME_TERMS:
            for variant in variants:
                normalized_variant = cls._normalize_place_name_text(variant)
                if normalized_variant:
                    text = text.replace(normalized_variant, canonical)
        return text

    @classmethod
    def _normalize_place_name(cls, value: str) -> str:
        """Normalize text, then collapse only exact, explicitly listed aliases."""
        normalized = cls._normalize_place_name_text(value)
        if normalized in cls._PLACE_NAME_ALIASES:
            return cls._PLACE_NAME_ALIASES[normalized]
        return cls._normalize_generic_terms(value)

    @classmethod
    def _facility_scopes(cls, value: str) -> set[str]:
        normalized = cls._normalize_place_name_text(value)
        scopes: set[str] = set()
        for scope, variants in cls._FACILITY_MODIFIERS.items():
            scope_text = normalized
            if scope == "store":
                # A shopping street/market is a main place category, not an
                # individual store facility. Avoid treating the embedded
                # Chinese/Japanese word for "store" as an entity modifier.
                for compound in ("商店街", "购物街", "購物街", "shoppingstreet"):
                    scope_text = scope_text.replace(
                        cls._normalize_place_name_text(compound), ""
                    )
            if any(cls._normalize_place_name_text(item) in scope_text for item in variants):
                scopes.add(scope)
        return scopes

    @classmethod
    def _scope_conflict(cls, requested_name: str, candidate_names: List[str]) -> bool:
        requested = cls._facility_scopes(requested_name)
        for candidate_name in candidate_names:
            candidate = cls._facility_scopes(candidate_name)
            # Extra facility scope changes a requested main entity; a missing
            # explicit requested scope changes a requested sub-entity.
            if candidate - requested or requested - candidate:
                return True
        return False

    @classmethod
    def _city_consistent(
        cls,
        city: str,
        address: str,
        *,
        containing_place_ids: Optional[set[str]] = None,
        requested_city_place_id: str = "",
    ) -> bool:
        city_text = cls._normalize_place_name_text(city)
        address_text = cls._normalize_place_name_text(address)
        if not city_text:
            return False
        if address_text:
            if city_text in {"东京", "東京", "tokyo"}:
                if any(token in address_text for token in ("东京", "東京", "東京都", "tokyo")):
                    return True
            elif city_text in address_text:
                return True
        return bool(
            requested_city_place_id
            and containing_place_ids
            and requested_city_place_id in containing_place_ids
        )

    @classmethod
    def _requested_entity_kinds(cls, name: str) -> set[str]:
        normalized = cls._normalize_generic_terms(name)
        return {
            kind for kind in (
                "park", "museum", "shrine", "observationdeck", "station", "building", "pier"
            )
            if kind in normalized
        }

    @classmethod
    def _type_compatible(cls, requested_name: str, candidate_names: List[str], types: List[str]) -> bool:
        kinds = cls._requested_entity_kinds(requested_name)
        type_set = set(types or [])
        candidate_text = " ".join(candidate_names)
        candidate_kinds = cls._requested_entity_kinds(candidate_text)
        if "park" in kinds and not ({"park", "state_park", "national_park"} & type_set):
            return False
        if "museum" in kinds and not any("museum" in item for item in type_set):
            return False
        if "shrine" in kinds and not ({"shinto_shrine", "place_of_worship"} & type_set):
            return False
        if "observationdeck" in kinds and "observation_deck" not in type_set:
            return False
        if "pier" in kinds:
            candidate_normalized = cls._normalize_generic_terms(candidate_text)
            water_types = {"ferry_terminal", "ferry_service"}
            incompatible_types = {
                "airport", "international_airport", "airport_terminal",
                "parking", "parking_lot", "corporate_office",
            }
            if type_set & incompatible_types:
                return False
            if (
                type_set & {"train_station", "subway_station"}
                and not type_set & water_types
            ):
                return False
            # Generic companies/offices and unrelated terminals are not piers.
            if any(token in candidate_normalized for token in ("airportterminal", "office")):
                return False
            if "pier" not in candidate_normalized and not type_set & water_types:
                return False
        if "building" in kinds:
            if "building" not in candidate_kinds:
                return False
            if type_set and type_set <= {
                "transit_station", "train_station", "subway_station",
                "transportation_service", "point_of_interest", "establishment",
            }:
                return False
        return True

    @staticmethod
    def _distance_meters(a: Location, b: Location) -> float:
        radius = 6371000.0
        lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
        dlat = lat2 - lat1
        dlon = math.radians(b.longitude - a.longitude)
        value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * radius * math.asin(math.sqrt(value))

    @classmethod
    def _address_score(
        cls,
        expected_address: str,
        candidate_address: str,
        expected_location: Optional[Location],
        candidate_location: Location,
    ) -> float:
        if not expected_address:
            return 0.0
        if expected_location:
            distance = cls._distance_meters(expected_location, candidate_location)
            if distance <= 500:
                return 1.0
            if distance <= 1200:
                return 0.55
            if distance <= 3000:
                return 0.2
            return 0.0
        expected_numbers = re.findall(r"\d+", unicodedata.normalize("NFKC", expected_address))
        candidate_numbers = re.findall(r"\d+", unicodedata.normalize("NFKC", candidate_address))
        if len(expected_numbers) >= 2 and expected_numbers[:3] == candidate_numbers[:3]:
            return 0.8
        expected_text = cls._normalize_place_name_text(expected_address)
        candidate_text = cls._normalize_place_name_text(candidate_address)
        return 0.4 if expected_text and (expected_text in candidate_text or candidate_text in expected_text) else 0.0

    @classmethod
    def _name_match_score(cls, requested_name: str, candidate_name: str) -> float:
        requested = cls._normalize_place_name(requested_name)
        candidate = cls._normalize_place_name(candidate_name)
        if not requested or not candidate:
            return 0.0
        if requested == candidate:
            return 1.0

        # A known main POI must not be verified through substring similarity
        # against a related facility such as "East Tower", "Town" or "Deck".
        requested_raw = cls._normalize_place_name_text(requested_name)
        candidate_raw = cls._normalize_place_name_text(candidate_name)
        requested_alias = cls._PLACE_NAME_ALIASES.get(requested_raw)
        candidate_alias = cls._PLACE_NAME_ALIASES.get(candidate_raw)
        if (
            requested_alias
            and candidate_alias != requested_alias
            and cls._scope_conflict(requested_name, [candidate_name])
        ):
            return min(0.59, SequenceMatcher(None, requested_raw, candidate_raw).ratio())

        if min(len(requested), len(candidate)) >= 3 and (requested in candidate or candidate in requested):
            return 0.9
        return SequenceMatcher(None, requested, candidate).ratio()

    @classmethod
    def _candidate_matches_city_context(cls, poi: POIInfo, city: str) -> bool:
        """Conservative city check for photo-only partial candidates."""
        city_text = cls._normalize_place_name_text(city)
        address_text = cls._normalize_place_name_text(poi.address)
        if not city_text or not address_text:
            return False
        if city_text in address_text:
            return True
        if city_text in {"东京", "東京", "tokyo"}:
            return any(alias in address_text for alias in ("东京", "東京都", "東京", "tokyo"))
        return False

    def match_poi(
        self,
        name: str,
        city: str,
        expected_address: str = "",
        expected_category: str = "",
    ) -> Dict[str, Any]:
        """Evidence-based deterministic POI matching with a bounded multilingual fallback."""
        del expected_category  # reserved for a later bounded type mapping; no LLM inference
        languages = ("zh-CN", "ja", "en")
        aggregated: Dict[str, Dict[str, Any]] = {}
        search_calls = 0
        diagnostics: Dict[str, bool] = {}
        containing_places: Dict[str, set[str]] = {}
        requested_city_place_id = ""

        def add_results(language: str) -> None:
            nonlocal search_calls
            results = self.search_poi(
                name,
                city,
                citylimit=True,
                language_code=language,
                address_hint=expected_address,
                _diagnostics=diagnostics,
                _containing_places=containing_places,
            )
            search_calls += 1
            for rank, poi in enumerate(results):
                entry = aggregated.setdefault(poi.id, {
                    "poi": poi,
                    "names": [],
                    "top_languages": set(),
                    "appearances": 0,
                    "containing_place_ids": set(),
                })
                entry["containing_place_ids"].update(containing_places.get(poi.id, set()))
                if poi.name and poi.name not in entry["names"]:
                    entry["names"].append(poi.name)
                entry["appearances"] += 1
                if rank == 0:
                    entry["top_languages"].add(language)

        add_results(languages[0])
        if not aggregated:
            return {
                "status": "unverified", "score": 0.0, "poi": None,
                "evidence": {
                    "search_calls": search_calls,
                    "reason": (
                        "provider_failure"
                        if diagnostics.get("provider_failure") else "no_candidates"
                    ),
                },
            }

        def base_evidence(entry: Dict[str, Any], address_score: float = 0.0) -> Dict[str, Any]:
            poi = entry["poi"]
            names = entry["names"]
            name_score = max((self._name_match_score(name, item) for item in names), default=0.0)
            literal_city_ok = self._city_consistent(city, poi.address)
            city_ok = literal_city_ok or self._city_consistent(
                city,
                poi.address,
                containing_place_ids=entry["containing_place_ids"],
                requested_city_place_id=requested_city_place_id,
            )
            type_ok = self._type_compatible(name, names, poi.type.split(",") if poi.type else [])
            scope_ok = not self._scope_conflict(name, names)
            place_id_ok = bool(str(poi.id or "").strip())
            provider_ok = poi.data_source == "google_places"
            coordinate_ok = has_valid_verified_coordinates(poi.location)
            top_ratio = len(entry["top_languages"]) / len(languages)
            evidence_score = 0.45 * name_score + 0.20 * top_ratio + 0.25 * address_score + 0.10 * float(type_ok)
            gates_ok = city_ok and type_ok and scope_ok and place_id_ok and provider_ok and coordinate_ok
            return {
                "name_score": name_score,
                "city_consistent": city_ok,
                "city_match_path": (
                    "literal" if literal_city_ok
                    else "containing_place" if city_ok else "unverified"
                ),
                "type_compatible": type_ok,
                "scope_compatible": scope_ok,
                "place_id_valid": place_id_ok,
                "provider_trusted": provider_ok,
                "coordinate_valid": coordinate_ok,
                "top_language_count": len(entry["top_languages"]),
                "address_score": address_score,
                "evidence_score": evidence_score,
                "ranking_score": evidence_score if gates_ok else evidence_score - 1.0,
            }

        initial_ranked = sorted(
            ((base_evidence(entry), entry) for entry in aggregated.values()),
            key=lambda item: item[0]["name_score"],
            reverse=True,
        )
        initial_evidence, initial_entry = initial_ranked[0]
        initial_other_gates = (
            initial_evidence["name_score"] >= 0.88
            and initial_evidence["type_compatible"]
            and initial_evidence["scope_compatible"]
            and initial_evidence["place_id_valid"]
            and initial_evidence["provider_trusted"]
            and initial_evidence["coordinate_valid"]
        )
        if (
            not initial_evidence["city_consistent"]
            and initial_other_gates
            and initial_entry["containing_place_ids"]
        ):
            requested_city_place_id = self._geocode_place_id(city)
            initial_evidence = base_evidence(initial_entry)
        if (
            initial_evidence["name_score"] >= 0.88
            and initial_evidence["city_consistent"]
            and initial_evidence["type_compatible"]
            and initial_evidence["scope_compatible"]
            and initial_evidence["place_id_valid"]
            and initial_evidence["provider_trusted"]
            and initial_evidence["coordinate_valid"]
        ):
            initial_evidence.update({"path": "strong_name", "search_calls": search_calls})
            return {
                "status": "verified",
                "score": round(initial_evidence["name_score"], 3),
                "poi": initial_entry["poi"],
                "evidence": initial_evidence,
            }

        # Ambiguous/cross-language only: two bounded additional searches and at
        # most one Geocoding request to corroborate the Planner address.
        add_results(languages[1])
        add_results(languages[2])
        expected_location = self.geocode(expected_address, city) if expected_address else None

        if not requested_city_place_id:
            needs_containment = any(
                entry["containing_place_ids"]
                and max(
                    (self._name_match_score(name, item) for item in entry["names"]),
                    default=0.0,
                ) >= 0.6
                and self._type_compatible(
                    name, entry["names"],
                    entry["poi"].type.split(",") if entry["poi"].type else [],
                )
                and not self._scope_conflict(name, entry["names"])
                and bool(str(entry["poi"].id or "").strip())
                and entry["poi"].data_source == "google_places"
                and has_valid_verified_coordinates(entry["poi"].location)
                for entry in aggregated.values()
            )
            if needs_containment:
                requested_city_place_id = self._geocode_place_id(city)

        ranked = []
        for entry in aggregated.values():
            poi = entry["poi"]
            address_score = self._address_score(expected_address, poi.address, expected_location, poi.location)
            evidence = base_evidence(entry, address_score)
            ranked.append((evidence, entry))
        ranked.sort(key=lambda item: item[0]["ranking_score"], reverse=True)
        best_evidence, best_entry = ranked[0]
        runner_score = ranked[1][0]["ranking_score"] if len(ranked) > 1 else 0.0
        margin = best_evidence["ranking_score"] - runner_score
        best_evidence.update({
            "path": "multilingual_evidence",
            "search_calls": search_calls,
            "runner_up_margin": margin,
        })

        path_a = (
            best_evidence["name_score"] >= 0.88
            and best_evidence["city_consistent"]
            and best_evidence["type_compatible"]
            and best_evidence["scope_compatible"]
            and best_evidence["place_id_valid"]
            and best_evidence["provider_trusted"]
            and best_evidence["coordinate_valid"]
        )
        path_b = (
            best_evidence["top_language_count"] >= 2
            and best_evidence["address_score"] >= 0.55
            and best_evidence["evidence_score"] >= 0.62
            and margin >= 0.08
            and best_evidence["city_consistent"]
            and best_evidence["type_compatible"]
            and best_evidence["scope_compatible"]
            and best_evidence["place_id_valid"]
            and best_evidence["provider_trusted"]
            and best_evidence["coordinate_valid"]
        )
        if path_a or path_b:
            status = "verified"
            best_evidence["path"] = "strong_name" if path_a else "multilingual_consensus"
        elif (
            best_evidence["city_consistent"]
            and best_evidence["type_compatible"]
            and best_evidence["scope_compatible"]
            and (best_evidence["name_score"] >= 0.6 or best_evidence["evidence_score"] >= 0.4)
        ):
            status = "partial_match"
        else:
            status = "unverified"
        return {
            "status": status,
            "score": round(best_evidence["name_score"], 3),
            "poi": best_entry["poi"],
            "evidence": best_evidence,
        }

    def get_place_photo(
        self,
        *,
        place_id: str = "",
        name: str = "",
        city: str = "",
        max_width_px: int = 1200,
        match_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Resolve one Google Place photo URI and its required attribution.

        ``match_result`` may only contain a result produced by this service in
        the current request. Reusing it avoids a second Text Search for
        photo-only partial matches without trusting a client Place ID.
        """
        photo_name = ""
        attributions: List[Dict[str, Any]] = []
        resolved_place_id = place_id
        match_status = "verified" if place_id else "unverified"

        if place_id:
            try:
                detail_url = f"{self.PLACES_BASE}/{place_id}"
                detail_response = self._client.get(
                    detail_url,
                    headers={
                        "X-Goog-Api-Key": self.api_key,
                        "X-Goog-FieldMask": "id,photos",
                    },
                )
                detail_response.raise_for_status()
                detail = detail_response.json()
                photos = detail.get("photos") or []
                if photos:
                    photo_name = photos[0].get("name", "")
                    attributions = photos[0].get("authorAttributions") or []
            except Exception as exc:
                _log_http_failure("place_details_photo", exc)
                return {
                    "photo_url": "",
                    "place_id": resolved_place_id,
                    "attributions": [],
                    "match_status": match_status,
                    "reason": "google_provider_error",
                }
        elif name:
            match = match_result if match_result is not None else self.match_poi(name, city)
            poi = match.get("poi")
            can_use_for_photo = bool(
                poi
                and poi.id
                and poi.photo_name
                and poi.data_source == "google_places"
                and has_valid_verified_coordinates(poi.location)
                and (
                    match["status"] == "verified"
                    or (
                        match["status"] == "partial_match"
                        and (
                            bool((match.get("evidence") or {}).get("city_consistent"))
                            or self._candidate_matches_city_context(poi, city)
                        )
                    )
                )
            )
            if can_use_for_photo:
                poi = match["poi"]
                resolved_place_id = poi.id
                photo_name = poi.photo_name or ""
                attributions = poi.photo_attributions
                match_status = match["status"]

        if not photo_name:
            return {
                "photo_url": "",
                "place_id": resolved_place_id,
                "attributions": attributions,
                "match_status": match_status,
                "reason": "google_no_photo",
            }

        media_url = f"https://places.googleapis.com/v1/{photo_name}/media"
        params = {
            "key": self.api_key,
            "maxWidthPx": max(1, min(max_width_px, 4800)),
            "skipHttpRedirect": "true",
        }
        try:
            resp = self._client.get(media_url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            photo_url = payload.get("photoUri", "")
            return {
                "photo_url": photo_url,
                "place_id": resolved_place_id,
                "attributions": attributions,
                "match_status": match_status,
                "reason": None if photo_url else "google_no_photo",
            }
        except Exception as exc:
            _log_http_failure("place_photo", exc)
            return {
                "photo_url": "",
                "place_id": resolved_place_id,
                "attributions": attributions,
                "match_status": match_status,
                "reason": "google_provider_error",
            }

    # ======================== 天气查询 ========================

    _WEATHER_CONDITION_LABELS = {
        "CLEAR": "晴", "MOSTLY_CLEAR": "晴",
        "PARTLY_CLOUDY": "多云", "MOSTLY_CLOUDY": "多云",
        "CLOUDY": "阴", "OVERCAST": "阴",
        "LIGHT_RAIN": "小雨", "LIGHT_RAIN_SHOWERS": "小雨",
        "RAIN": "中雨", "MODERATE_RAIN": "中雨",
        "HEAVY_RAIN": "大雨", "HEAVY_RAIN_SHOWERS": "大雨",
        "LIGHT_SNOW": "小雪", "SNOW": "中雪", "HEAVY_SNOW": "大雪",
        "THUNDERSTORM": "雷阵雨", "DRIZZLE": "毛毛雨",
        "FOG": "雾", "HAZE": "霾", "WIND": "大风", "WINDY": "大风",
        "WIND_AND_RAIN": "风雨",
    }

    @classmethod
    def _parse_weather_condition(cls, raw: Any) -> str:
        """Return provider text/type without inventing a condition."""
        if isinstance(raw, str):
            return cls._WEATHER_CONDITION_LABELS.get(raw, raw)
        if not isinstance(raw, dict):
            return ""
        description = raw.get("description")
        if isinstance(description, dict):
            text = description.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        condition_type = raw.get("type")
        if isinstance(condition_type, str) and condition_type:
            return cls._WEATHER_CONDITION_LABELS.get(condition_type, condition_type)
        return ""

    @staticmethod
    def _parse_temperature(raw: Any) -> Optional[int]:
        if not isinstance(raw, dict):
            return None
        degrees = raw.get("degrees")
        if isinstance(degrees, bool) or not isinstance(degrees, (int, float)):
            return None
        return int(round(float(degrees)))

    @staticmethod
    def _parse_precipitation_probability(part: Dict[str, Any]) -> Optional[int]:
        precipitation = part.get("precipitation")
        if not isinstance(precipitation, dict):
            return None
        probability = precipitation.get("probability")
        if not isinstance(probability, dict):
            return None
        percent = probability.get("percent")
        if isinstance(percent, bool) or not isinstance(percent, (int, float)):
            return None
        parsed = int(round(float(percent)))
        return parsed if 0 <= parsed <= 100 else None

    @staticmethod
    def _parse_wind(part: Dict[str, Any]) -> tuple[str, str]:
        wind = part.get("wind")
        if not isinstance(wind, dict):
            return "", ""
        direction = wind.get("direction")
        cardinal = direction.get("cardinal", "") if isinstance(direction, dict) else ""
        cardinal = cardinal if isinstance(cardinal, str) else ""
        speed = wind.get("speed")
        if not isinstance(speed, dict):
            return cardinal, ""
        value, unit = speed.get("value"), speed.get("unit", "")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return cardinal, ""
        unit_labels = {"KILOMETERS_PER_HOUR": "km/h", "MILES_PER_HOUR": "mph"}
        unit_text = unit_labels.get(unit, unit if isinstance(unit, str) else "")
        value_text = f"{float(value):g}"
        return cardinal, f"{value_text} {unit_text}".strip()

    @classmethod
    def _parse_forecast_day(cls, day_data: Any, city: str) -> Optional[WeatherInfo]:
        if not isinstance(day_data, dict):
            return None
        date_info = day_data.get("displayDate")
        if not isinstance(date_info, dict):
            return None
        year, month, day = date_info.get("year"), date_info.get("month"), date_info.get("day")
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (year, month, day)):
            return None
        try:
            date_str = f"{year:04d}-{month:02d}-{day:02d}"
            # Validate calendar semantics as well as JSON types.
            from datetime import date as _date
            _date(year, month, day)
        except (TypeError, ValueError):
            return None

        high = cls._parse_temperature(day_data.get("maxTemperature"))
        low = cls._parse_temperature(day_data.get("minTemperature"))
        if high is None or low is None:
            return None
        daytime = day_data.get("daytimeForecast")
        nighttime = day_data.get("nighttimeForecast")
        daytime = daytime if isinstance(daytime, dict) else {}
        nighttime = nighttime if isinstance(nighttime, dict) else {}
        wind_direction, wind_power = cls._parse_wind(daytime)
        return WeatherInfo(
            date=date_str, city=city,
            day_weather=cls._parse_weather_condition(daytime.get("weatherCondition")),
            night_weather=cls._parse_weather_condition(nighttime.get("weatherCondition")),
            day_temp=high, night_temp=low,
            precipitation_probability=cls._parse_precipitation_probability(daytime),
            wind_direction=wind_direction, wind_power=wind_power,
            data_source="google_weather", verification_status="verified", degraded=False,
        )

    def get_weather(self, city: str) -> WeatherResult:
        """
        使用 Google Maps Weather API 查询天气

        API 文档: https://developers.google.com/maps/documentation/weather

        策略:
        1. 先通过 Geocoding 把城市名解析为坐标
        2. 调用 Weather API 的 forecast/days 端点获取未来数天预报
        3. 解析为与高德兼容的 WeatherInfo 列表
        """
        # Step 1: 获取城市坐标
        loc = self.geocode(city)
        if not loc:
            print(f"⚠️ [Google] 天气查询: 无法解析城市 '{city}' 的坐标")
            return WeatherResult(
                provider="google_weather", request_success=False, data_available=False,
                degraded=True, reason="unsupported_location",
            )

        # Step 2: 调用 Weather API - 每日预报
        forecast_url = f"https://weather.googleapis.com/v1/forecast/days:lookup"
        params = {
            "key": self.api_key,
            "location.latitude": loc.latitude,
            "location.longitude": loc.longitude,
            "days": 7,
            "languageCode": "zh-CN",
            "unitsSystem": "METRIC",
        }
        try:
            resp = self._client.get(forecast_url, params=params)
            status = getattr(resp, "status_code", 200)
            if status == 401:
                return WeatherResult(provider="google_weather", request_success=False, data_available=False, degraded=True, reason="authentication_failed")
            if status == 403:
                return WeatherResult(provider="google_weather", request_success=False, data_available=False, degraded=True, reason="permission_denied")
            if status == 404:
                print(
                    "❌ provider=google endpoint=weather_forecast "
                    "category=unsupported_location status=404 retryable=false"
                )
                return WeatherResult(
                    provider="google_weather", request_success=True,
                    data_available=False, degraded=True,
                    reason="unsupported_location",
                )
            if status == 429:
                return WeatherResult(provider="google_weather", request_success=False, data_available=False, degraded=True, reason="rate_limited")
            resp.raise_for_status()
            try:
                data = resp.json()
            except Exception:
                return WeatherResult(provider="google_weather", request_success=True, data_available=False, degraded=True, reason="malformed_response")
            if not isinstance(data, dict) or "forecastDays" not in data:
                return WeatherResult(provider="google_weather", request_success=True, data_available=False, degraded=True, reason="malformed_response")
            forecast_days = data.get("forecastDays")
            if not isinstance(forecast_days, list):
                return WeatherResult(provider="google_weather", request_success=True, data_available=False, degraded=True, reason="malformed_response")
            weather_list = [
                parsed for item in forecast_days
                if (parsed := self._parse_forecast_day(item, city)) is not None
            ]

            if not weather_list:
                print(f"⚠️ [Google] 天气请求成功但无预报: {city}")
                reason = "empty_forecast" if not forecast_days else "malformed_response"
                return WeatherResult(provider="google_weather", request_success=True, data_available=False, degraded=True, reason=reason)
            print(f"✅ [Google] 天气查询成功: {city}, {len(weather_list)} 天预报")
            return WeatherResult(provider="google_weather", request_success=True, data_available=True, degraded=False, days=weather_list)

        except httpx.TimeoutException as exc:
            _log_http_failure("weather_forecast", exc)
            return WeatherResult(provider="google_weather", request_success=False, data_available=False, degraded=True, reason="timeout")
        except httpx.RequestError as exc:
            _log_http_failure("weather_forecast", exc)
            return WeatherResult(provider="google_weather", request_success=False, data_available=False, degraded=True, reason="network_error")
        except Exception as exc:
            _log_http_failure("weather_forecast", exc)
            return WeatherResult(provider="google_weather", request_success=False, data_available=False, degraded=True, reason="malformed_response")


# ============ 单例管理 ============

_google_map_service: Optional[GoogleMapService] = None


def get_google_map_service() -> Optional[GoogleMapService]:
    """获取 Google Maps 服务实例 (单例模式)。如果 API Key 未配置则返回 None。"""
    global _google_map_service

    if _google_map_service is None:
        settings = get_settings()
        server_api_key = get_google_maps_server_api_key()
        if not server_api_key:
            return None
        _google_map_service = GoogleMapService(
            api_key=server_api_key,
            proxy=settings.google_maps_proxy,
        )
        print("✅ Google Maps 服务初始化成功")

    return _google_map_service


def reset_google_map_service() -> None:
    """重置 Google Maps 服务实例（用于运行时配置更新后热生效）。"""
    global _google_map_service
    if _google_map_service is not None:
        _google_map_service.close()
    _google_map_service = None
