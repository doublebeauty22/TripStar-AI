"""配置管理模块"""

import os
import json
from pathlib import Path
from typing import List, Dict, Any
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# 加载环境变量
# 首先尝试加载当前目录的.env
load_dotenv()

# 然后尝试加载HelloAgents的.env(如果存在)
helloagents_env = Path(__file__).parent.parent.parent.parent / "HelloAgents" / ".env"
if helloagents_env.exists():
    load_dotenv(helloagents_env, override=False)  # 不覆盖已有的环境变量


class Settings(BaseSettings):
    """应用配置"""

    # 应用基本配置
    app_name: str = "HelloAgents智能旅行助手"
    app_version: str = "2.0.0"
    debug: bool = False
    app_env: str = "development"
    public_demo_mode: bool = False

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS配置 - 使用字符串,在代码中分割
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"

    # Backend-only server credentials. These are loaded from the backend
    # environment and are never eligible for runtime/browser persistence.
    amap_web_service_key: str = ""
    vite_amap_web_js_key: str = ""

    # Google Maps API配置
    google_maps_server_api_key: str = ""
    google_maps_proxy: str = ""

    # 小红书配置
    xhs_cookie: str = ""

    # LLM配置 (从环境变量读取,由HelloAgents管理)
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_API_KEY", "LLM_API_KEY"),
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices("OPENAI_BASE_URL", "LLM_BASE_URL"),
    )
    openai_model: str = Field(
        default="gpt-4",
        validation_alias=AliasChoices("OPENAI_MODEL", "LLM_MODEL_ID"),
    )

    # 日志配置
    log_level: str = "INFO"

    # Portfolio deployment controls. These are deliberately process-local:
    # the public demo runs one worker and does not pretend to be distributed.
    public_max_concurrent_generations: int = Field(default=1, ge=1, le=20)
    public_generation_cooldown_seconds: int = Field(default=60, ge=0, le=86400)
    public_auxiliary_cooldown_seconds: int = Field(default=10, ge=0, le=3600)
    public_history_enabled: bool = False

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # 忽略额外的环境变量

    def get_cors_origins_list(self) -> List[str]:
        """获取CORS origins列表"""
        return [origin.strip() for origin in self.cors_origins.split(',')]

    @property
    def is_public_deployment(self) -> bool:
        return self.app_env.strip().lower() == "production" or self.public_demo_mode

    @property
    def runtime_settings_read_only(self) -> bool:
        return self.is_public_deployment

    @property
    def live_generation_available(self) -> bool:
        return bool(self.openai_api_key)


# 创建全局配置实例
settings = Settings()
_RUNTIME_SETTINGS_FILE = Path(__file__).resolve().parent.parent / "runtime_settings.json"
_RUNTIME_SETTING_KEYS = {
    "vite_amap_web_js_key",
    "openai_base_url",
    "openai_model",
}


def _load_runtime_overrides() -> Dict[str, Any]:
    """加载本地持久化的运行时配置覆盖项。"""
    if not _RUNTIME_SETTINGS_FILE.exists():
        return {}
    try:
        os.chmod(_RUNTIME_SETTINGS_FILE, 0o600)
        with open(_RUNTIME_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k: data[k] for k in _RUNTIME_SETTING_KEYS if k in data}
    except Exception as e:
        print(f"⚠️  读取运行时配置失败，已回退到环境变量: {e}")
    return {}


def _persist_runtime_overrides(overrides: Dict[str, Any]) -> None:
    """Persist browser-safe runtime configuration only."""
    safe_overrides = {
        key: value for key, value in overrides.items() if key in _RUNTIME_SETTING_KEYS
    }
    _RUNTIME_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_RUNTIME_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(safe_overrides, f, ensure_ascii=False, indent=2)
    os.chmod(_RUNTIME_SETTINGS_FILE, 0o600)


def _sync_env_from_settings() -> None:
    """Sync non-secret runtime LLM metadata for compatible components."""
    if settings.openai_base_url:
        os.environ["OPENAI_BASE_URL"] = settings.openai_base_url
        os.environ["LLM_BASE_URL"] = settings.openai_base_url
    if settings.openai_model:
        os.environ["OPENAI_MODEL"] = settings.openai_model
        os.environ["LLM_MODEL_ID"] = settings.openai_model


def _apply_runtime_overrides(overrides: Dict[str, Any]) -> None:
    """将覆盖项应用到全局 settings 实例。"""
    for key, value in overrides.items():
        if key in _RUNTIME_SETTING_KEYS and hasattr(settings, key):
            setattr(settings, key, value if value is not None else "")
    _sync_env_from_settings()


_runtime_overrides = _load_runtime_overrides()
_apply_runtime_overrides(_runtime_overrides)


def get_settings() -> Settings:
    """获取配置实例"""
    return settings


def get_google_maps_server_api_key() -> str:
    """Resolve the backend-only Google key from backend environment settings."""
    return settings.google_maps_server_api_key or ""


def get_amap_web_service_key() -> str:
    """Resolve the backend-only AMap Web Service key from the environment."""
    return settings.amap_web_service_key or ""


def get_runtime_settings() -> Dict[str, Any]:
    """Return browser-safe settings and secret presence metadata only."""
    return {
        "vite_amap_web_js_key": settings.vite_amap_web_js_key or "",
        "google_maps_proxy_configured": bool(settings.google_maps_proxy),
        "openai_base_url": settings.openai_base_url or "",
        "openai_model": settings.openai_model or "",
        "openai_configured": bool(settings.openai_api_key),
        "xhs_configured": bool(settings.xhs_cookie),
        "amap_server_configured": bool(get_amap_web_service_key()),
        "google_server_configured": bool(get_google_maps_server_api_key()),
        "public_demo_mode": settings.public_demo_mode,
        "runtime_settings_read_only": settings.runtime_settings_read_only,
        "public_history_enabled": (
            settings.public_history_enabled and not settings.is_public_deployment
        ),
        "live_generation_available": settings.live_generation_available,
    }


def update_runtime_settings(updates: Dict[str, Any]) -> Dict[str, str]:
    """更新并持久化运行时配置。"""
    global _runtime_overrides

    normalized: Dict[str, str] = {}
    for key, value in updates.items():
        if key not in _RUNTIME_SETTING_KEYS:
            continue
        normalized[key] = str(value).strip() if value is not None else ""

    _runtime_overrides = {
        key: value for key, value in _runtime_overrides.items() if key in _RUNTIME_SETTING_KEYS
    }
    _runtime_overrides.update(normalized)
    _persist_runtime_overrides(_runtime_overrides)
    _apply_runtime_overrides(_runtime_overrides)
    return get_runtime_settings()


# 验证必要的配置
def validate_config():
    """验证配置是否完整"""
    warnings = []

    if not get_amap_web_service_key():
        warnings.append("AMAP_WEB_SERVICE_KEY未配置，景点地理编码等功能将不可用")

    llm_api_key = settings.openai_api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not llm_api_key:
        warnings.append("LLM API Key未配置，AI 生成功能将不可用")

    if warnings:
        print("\n⚠️  配置警告:")
        for w in warnings:
            print(f"  - {w}")

    return True


# 打印配置信息(用于调试)
def print_config():
    """打印当前配置(隐藏敏感信息)"""
    print(f"应用名称: {settings.app_name}")
    print(f"版本: {settings.app_version}")
    print(f"服务器: {settings.host}:{settings.port}")
    print(f"高德地图API Key: {'已配置' if get_amap_web_service_key() else '未配置'}")
    print(f"高德地图JS Key: {'已配置' if settings.vite_amap_web_js_key else '未配置'}")
    print(f"Google Maps Server API Key: {'已配置' if get_google_maps_server_api_key() else '未配置'}")
    print(f"Google Maps Proxy: {'已配置' if settings.google_maps_proxy else '未配置'}")
    print(f"小红书Cookie: {'已配置' if settings.xhs_cookie else '未配置'}")

    # 检查LLM配置
    llm_api_key = settings.openai_api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    llm_base_url = settings.openai_base_url
    llm_model = settings.openai_model

    print(f"LLM API Key: {'已配置' if llm_api_key else '未配置'}")
    print(f"LLM Base URL: {llm_base_url}")
    print(f"LLM Model: {llm_model}")
    print(f"日志级别: {settings.log_level}")
