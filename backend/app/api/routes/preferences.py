"""用户旅行偏好解析 API。"""

import asyncio

from fastapi import APIRouter, Request

from ...models.schemas import PreferenceParseRequest, PreferenceParseResponse
from ...services.preference_service import parse_preference_profile
from ...services.llm_service import generation_llm_execution
from ...services.public_demo_guard import public_demo_guard


router = APIRouter(prefix="/preferences", tags=["旅行偏好"])


@router.post("/parse", response_model=PreferenceParseResponse, summary="解析旅行偏好")
async def parse_preferences(request: PreferenceParseRequest, http_request: Request = None):
    await public_demo_guard.check_auxiliary(http_request, "preference_parse")
    generation_id = (request.generation_id or "").strip()
    if generation_id:
        with generation_llm_execution(generation_id):
            profile, used_llm, message = await asyncio.to_thread(parse_preference_profile, request)
    else:
        profile, used_llm, message = await asyncio.to_thread(parse_preference_profile, request)
    return PreferenceParseResponse(
        success=True,
        profile=profile,
        used_llm=used_llm,
        message=message,
        generation_id=generation_id or None,
    )
