"""运行时配置 API 路由"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ...config import get_runtime_settings, update_runtime_settings, get_settings as get_app_settings
from ...services.public_demo_guard import public_error
from ...services.amap_service import reset_amap_service
from ...services.google_map_service import reset_google_map_service
from ...services.llm_service import reset_llm
from ...agents.trip_planner_agent import reset_trip_planner_agent

router = APIRouter(prefix="/settings", tags=["运行时配置"])


class RuntimeSettingsPayload(BaseModel):
    """前端设置页提交的运行时配置。"""

    model_config = ConfigDict(extra="forbid")

    vite_amap_web_js_key: Optional[str] = Field(default=None, description="高德 JS SDK Key")
    openai_base_url: Optional[str] = Field(default=None, description="LLM Base URL")
    openai_model: Optional[str] = Field(default=None, description="LLM 模型")


@router.get("")
async def get_settings():
    """获取当前运行时配置。"""
    data = get_runtime_settings()
    if get_app_settings().runtime_settings_read_only:
        for field in ("vite_amap_web_js_key", "openai_base_url", "openai_model"):
            data.pop(field, None)
    return {
        "success": True,
        "message": "ok",
        "data": data,
    }


@router.put("")
async def save_settings(payload: RuntimeSettingsPayload):
    """保存运行时配置并立即生效。"""
    if get_app_settings().runtime_settings_read_only:
        raise HTTPException(
            status_code=403,
            detail=public_error(
                "runtime_settings_read_only",
                "公开演示环境的运行配置为只读。",
                False,
            ),
        )
    try:
        updates = payload.model_dump(exclude_unset=True)
        updated = update_runtime_settings(updates)

        # 重置单例，确保新配置立即生效
        reset_llm()
        reset_amap_service()
        reset_google_map_service()
        reset_trip_planner_agent()

        return {
            "success": True,
            "message": "配置已保存并立即生效",
            "data": updated,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存配置失败: {str(e)}") from e
