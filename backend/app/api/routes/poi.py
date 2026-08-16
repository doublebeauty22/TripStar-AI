"""POI相关API路由"""

import asyncio
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from ...services.amap_service import get_amap_service

router = APIRouter(prefix="/poi", tags=["POI"])


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
async def get_attraction_photo(
    name: str,
    city: Optional[str] = None,
    place_id: Optional[str] = None,
    address: Optional[str] = None,
    category: Optional[str] = None,
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
            trusted_place_id = ""
            match = await asyncio.to_thread(
                google_service.match_poi,
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
                # A client-supplied place_id is only a hint. The server-side
                # deterministic matcher is the trust boundary.
                trusted_place_id = matched_poi.id
            elif server_match_status == "verified":
                # A defensive boundary for malformed/custom matcher results:
                # a verified label without a valid server-side coordinate is
                # not sufficient to start a second Google lookup.
                server_match_status = "unverified"
                match_status = "unverified"
            if server_match_status in {"verified", "partial_match"}:
                google_photo = await asyncio.to_thread(
                    google_service.get_place_photo,
                    place_id=trusted_place_id,
                    name="" if trusted_place_id else name,
                    city=city or "",
                    match_result=match,
                )
            else:
                failure_reasons.append("grounding_unverified")
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
