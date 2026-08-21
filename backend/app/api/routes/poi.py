"""POI相关API路由"""

import asyncio
import threading
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from ...services.amap_service import get_amap_service
from ...services.timing import timed_async_stage, timed_stage

router = APIRouter(prefix="/poi", tags=["POI"])

# Image requests arrive concurrently from the result page. Bound only the
# fallback Text Search matcher; trusted Place Details/Photo calls stay outside.
_GOOGLE_PHOTO_GROUNDING_SEMAPHORE = threading.Semaphore(2)


def _match_poi_for_photo(service, *args):
    with _GOOGLE_PHOTO_GROUNDING_SEMAPHORE:
        return service.match_poi(*args)


def _trusted_task_place_id(
    *, plan_id: str, name: str, city: str, client_place_id: str
) -> str:
    """Resolve a Place ID only from the server-owned completed TripPlan.

    Client fields are selectors used to prove association; the returned value
    always comes from the persisted/in-memory task result.
    """
    if not plan_id or not client_place_id:
        return ""
    if len(plan_id) > 64 or any(not (char.isalnum() or char in "-_") for char in plan_id):
        return ""
    try:
        from .trip import _get_task, _task_trip_plan
        from ...models.schemas import has_valid_verified_coordinates

        task = _get_task(plan_id)
        if not task or task.get("status") != "completed":
            return ""
        plan = _task_trip_plan(task)
        expected_name = name.strip().casefold()
        expected_city = city.strip().casefold()
        candidates = []
        for day in plan.days:
            day_city = (day.city or plan.city or "").strip().casefold()
            for attraction in day.attractions:
                if attraction.name.strip().casefold() != expected_name:
                    continue
                if expected_city and day_city != expected_city:
                    continue
                if (
                    attraction.poi_match_status == "verified"
                    and attraction.map_data_source == "google_places"
                    and attraction.place_id
                    and attraction.place_id == client_place_id
                    and has_valid_verified_coordinates(attraction.location)
                ):
                    candidates.append(attraction.place_id)
        return candidates[0] if candidates and len(set(candidates)) == 1 else ""
    except Exception:
        return ""


def _log_photo_event(
    *, request_id: str, provider: str, stage: str, category: str,
    match_status: str, retryable: bool
) -> None:
    """Emit stable image telemetry without request data or provider details."""
    print(
        "PHOTO_EVENT "
        f"request_id={request_id} provider={provider} stage={stage} "
        f"category={category} match_status={match_status} "
        f"retryable={str(retryable).lower()}",
        flush=True,
    )


def _log_photo_terminal(
    *, request_id: str, outcome: str, source: str, category: str,
    match_status: str, retryable: bool
) -> None:
    """Emit one safe, machine-parseable backend retrieval outcome."""
    print(
        "PHOTO_TERMINAL "
        f"request_id={request_id} outcome={outcome} source={source} "
        f"category={category} match_status={match_status} "
        f"retryable={str(retryable).lower()}",
        flush=True,
    )


_GOOGLE_GROUNDING_CATEGORIES = {
    "no_candidates", "name_mismatch", "city_mismatch", "type_mismatch",
    "scope_conflict", "invalid_place_id", "invalid_coordinates",
    "insufficient_multilingual_evidence", "ambiguous_candidates",
    "provider_failure",
}


def _google_grounding_category(match: dict) -> str:
    """Map existing deterministic gates to one bounded diagnostic reason."""
    evidence = match.get("evidence") if isinstance(match, dict) else None
    evidence = evidence if isinstance(evidence, dict) else {}
    explicit = evidence.get("reason")
    if explicit in {"no_candidates", "provider_failure"}:
        return explicit
    if evidence.get("city_consistent") is False:
        return "city_mismatch"
    if evidence.get("type_compatible") is False:
        return "type_mismatch"
    if evidence.get("scope_compatible") is False:
        return "scope_conflict"
    if evidence.get("place_id_valid") is False:
        return "invalid_place_id"
    if evidence.get("coordinate_valid") is False:
        return "invalid_coordinates"
    if evidence.get("name_score", 0.0) < 0.6:
        return "name_mismatch"
    if evidence.get("runner_up_margin", 1.0) < 0.08:
        return "ambiguous_candidates"
    return "insufficient_multilingual_evidence"


def _log_google_grounding_event(*, request_id: str, category: str) -> None:
    safe_request_id = request_id if len(request_id) == 12 and request_id.isalnum() else "unknown"
    safe_category = category if category in _GOOGLE_GROUNDING_CATEGORIES else "no_candidates"
    print(
        "GOOGLE_GROUNDING_EVENT "
        f"request_id={safe_request_id} category={safe_category} retryable=false",
        flush=True,
    )


class POIDetailResponse(BaseModel):
    """POI详情响应"""
    success: bool
    message: str
    data: Optional[dict] = None


@router.get(
    "/detail/{poi_id}",
    response_model=POIDetailResponse,
    summary="获取POI详情",
    description="根据POI ID获取详细信息,包括图片"
)
async def get_poi_detail(poi_id: str):
    """
    获取POI详情
    
    Args:
        poi_id: POI ID
        
    Returns:
        POI详情响应
    """
    try:
        amap_service = get_amap_service()
        
        # 调用高德地图POI详情API
        result = amap_service.get_poi_detail(poi_id)
        
        return POIDetailResponse(
            success=True,
            message="获取POI详情成功",
            data=result
        )
        
    except Exception as e:
        print(f"❌ 获取POI详情失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取POI详情失败: {str(e)}"
        )


@router.get(
    "/search",
    summary="搜索POI",
    description="根据关键词搜索POI"
)
async def search_poi(keywords: str, city: str = "北京"):
    """
    搜索POI

    Args:
        keywords: 搜索关键词
        city: 城市名称

    Returns:
        搜索结果
    """
    try:
        amap_service = get_amap_service()
        result = amap_service.search_poi(keywords, city)

        return {
            "success": result.data_available,
            "message": "搜索成功" if result.data_available else f"搜索数据暂不可用 ({result.reason})",
            "data": result.data,
            "provider": result.provider,
            "request_success": result.request_success,
            "data_available": result.data_available,
            "degraded": result.degraded,
            "reason": result.reason,
        }

    except Exception as e:
        print(f"❌ 搜索POI失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"搜索POI失败: {str(e)}"
        )


@router.get(
    "/photo",
    summary="获取景点图片",
    description="按 Google Places → 小红书 → 前端本地占位图的顺序获取图片"
)
@timed_async_stage("image_stage_timing", "image_total")
async def get_attraction_photo(
    name: str,
    city: Optional[str] = None,
    place_id: Optional[str] = None,
    address: Optional[str] = None,
    category: Optional[str] = None,
    plan_id: Optional[str] = None,
):
    """
    获取景点图片

    Args:
        name: 景点名称
        city: 所在城市

    Returns:
        图片URL
    """
    # Never echo or trust a client-provided Place ID. Only a value returned by
    # the server-side matcher/photo lookup may cross this trust boundary.
    resolved_place_id = ""
    match_status = "unverified"
    failure_reasons: List[str] = []
    request_id = uuid.uuid4().hex[:12]
    terminal_emitted = False

    def emit_terminal(
        *, outcome: str, source: str, category: str, retryable: bool
    ) -> None:
        """Guard the request-level terminal outcome against duplicate emission."""
        nonlocal terminal_emitted
        if terminal_emitted:
            return
        terminal_emitted = True
        safe_match_status = (
            match_status
            if match_status in {"verified", "partial_match", "unverified"}
            else "unknown"
        )
        _log_photo_terminal(
            request_id=request_id,
            outcome=outcome,
            source=source,
            category=category,
            match_status=safe_match_status,
            retryable=retryable,
        )

    # 1. Google Places 是首选图片事实源。
    try:
        from ...services.google_map_service import get_google_map_service

        google_service = get_google_map_service()
        if google_service is not None:
            trusted_place_id = _trusted_task_place_id(
                plan_id=plan_id or "", name=name, city=city or "",
                client_place_id=place_id or "",
            )
            match = {"status": "verified", "poi": None, "evidence": {}}
            server_match_status = "verified" if trusted_place_id else "unverified"
            match_status = server_match_status
            if not trusted_place_id:
                with timed_stage("image_stage_timing", "google_grounding"):
                    match = await asyncio.to_thread(
                        _match_poi_for_photo,
                        google_service,
                        name,
                        city or "",
                        address or "",
                        category or "",
                    )
                matched_poi = match.get("poi")
                server_match_status = match.get("status", "unverified")
                match_status = server_match_status
                from ...models.schemas import has_valid_verified_coordinates
                if (
                    match.get("status") == "verified"
                    and matched_poi is not None
                    and matched_poi.id
                    and has_valid_verified_coordinates(matched_poi.location)
                ):
                    trusted_place_id = matched_poi.id
                elif server_match_status == "verified":
                    server_match_status = "unverified"
                    match_status = "unverified"
            if server_match_status in {"verified", "partial_match"}:
                with timed_stage("image_stage_timing", "google_photo"):
                    google_photo = await asyncio.to_thread(
                        google_service.get_place_photo,
                        place_id=trusted_place_id,
                        name="" if trusted_place_id else name,
                        city=city or "",
                        match_result=match,
                    )
            else:
                failure_reasons.append("grounding_unverified")
                _log_google_grounding_event(
                    request_id=request_id,
                    category=_google_grounding_category(match),
                )
                _log_photo_event(
                    request_id=request_id,
                    provider="google", stage="grounding",
                    category="grounding_unverified", match_status="unverified",
                    retryable=False,
                )
                google_photo = {
                    "photo_url": "", "place_id": "", "attributions": [],
                    "match_status": "unverified", "reason": "grounding_unverified",
                }
            resolved_place_id = google_photo.get("place_id") or ""
            if google_photo.get("photo_url"):
                match_status = google_photo.get("match_status") or match_status
                emit_terminal(
                    outcome="success", source="google_places",
                    category="success", retryable=False,
                )
                return {
                    "success": True,
                    "message": "Google Places 图片获取成功",
                    "degraded": google_photo.get("match_status") != "verified",
                    "data": {
                        "name": name,
                        "place_id": resolved_place_id,
                        "photo_url": google_photo["photo_url"],
                        "source": "google_places",
                        "match_status": google_photo.get("match_status") or "unverified",
                        "attributions": google_photo.get("attributions") or [],
                        "reason": None,
                        "failure_reasons": [],
                    },
                }
            google_reason = google_photo.get("reason")
            if google_reason and google_reason not in failure_reasons:
                failure_reasons.append(google_reason)
                _log_photo_event(
                    request_id=request_id,
                    provider="google", stage="photo", category=google_reason,
                    match_status=google_photo.get("match_status") or match_status,
                    retryable=google_reason == "google_provider_error",
                )
        else:
            failure_reasons.append("google_provider_error")
            _log_photo_event(
                request_id=request_id,
                provider="google", stage="configuration",
                category="google_provider_error", match_status=match_status,
                retryable=False,
            )
    except Exception:
        failure_reasons.append("google_provider_error")
        _log_photo_event(
            request_id=request_id,
            provider="google", stage="photo", category="google_provider_error",
            match_status=match_status, retryable=True,
        )

    # 2. XHS 仅作为可选的用户经验图片增强源。
    try:
        from ...services.xhs_service import get_photo_from_xhs

        with timed_stage("image_stage_timing", "xhs_image"):
            photo_url = await get_photo_from_xhs(
                f"{name} 风景", request_id=request_id
            )
        if photo_url:
            emit_terminal(
                outcome="success", source="xhs",
                category="success", retryable=False,
            )
            return {
                "success": True,
                "message": "已使用小红书图片降级方案",
                "degraded": True,
                "data": {
                    "name": name,
                    "place_id": resolved_place_id,
                    "photo_url": photo_url,
                    "source": "xhs",
                    "attributions": [],
                    "reason": failure_reasons[-1] if failure_reasons else None,
                    "failure_reasons": failure_reasons,
                },
            }
        failure_reasons.append("xhs_no_result")
        _log_photo_event(
            request_id=request_id,
            provider="xhs", stage="photo", category="xhs_no_result",
            match_status=match_status, retryable=False,
        )
    except Exception:
        failure_reasons.append("xhs_provider_error")
        _log_photo_event(
            request_id=request_id,
            provider="xhs", stage="photo", category="xhs_provider_error",
            match_status=match_status, retryable=True,
        )

    # 3. placeholder 由前端本地渲染；这里不伪造真实 photo_url。
    final_reason = failure_reasons[-1] if failure_reasons else "xhs_no_result"
    _log_photo_event(
        request_id=request_id,
        provider="frontend", stage="fallback", category=final_reason,
        match_status=match_status, retryable=False,
    )
    emit_terminal(
        outcome="placeholder", source="placeholder", category=final_reason,
        retryable=final_reason in {"google_provider_error", "xhs_provider_error"},
    )
    return {
        "success": True,
        "message": "没有可用的真实图片，使用本地占位图",
        "degraded": True,
        "data": {
            "name": name,
            "place_id": resolved_place_id,
            "photo_url": "",
            "source": "placeholder",
            "attributions": [],
            "reason": final_reason,
            "failure_reasons": failure_reasons,
        },
    }
