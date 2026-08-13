"""FastAPI主应用"""

import sys
import os

# 强制 stdout/stderr 使用 UTF-8，防止非 UTF-8 控制台（如 cp932）输出中文时崩溃
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from ..config import get_settings, validate_config, print_config
from .routes import trip, poi, map as map_routes, chat, preferences, settings as settings_routes, demo
from ..services.public_demo_guard import public_error

# 获取配置
settings = get_settings()

# 创建FastAPI应用
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="基于HelloAgents框架的智能旅行规划助手API",
    docs_url="/docs",
    redoc_url="/redoc"
)


def _public_error_response(status_code: int, detail):
    if isinstance(detail, dict) and set(detail) == {"error"}:
        return JSONResponse(status_code=status_code, content=detail)
    mapping = {
        400: ("invalid_input", "请求内容无效，请检查后重试。", False),
        403: ("feature_disabled", "该功能在公开演示环境中不可用。", False),
        404: ("not_found", "未找到请求的资源。", False),
        409: ("request_conflict", "行程状态已变化，请刷新后重试。", True),
        429: ("rate_limited", "请求过于频繁，请稍后再试。", True),
    }
    code, message, retryable = mapping.get(
        status_code,
        ("service_unavailable", "服务暂时不可用，请稍后重试或查看示例行程。", True),
    )
    return JSONResponse(
        status_code=status_code,
        content=public_error(code, message, retryable),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    if settings.is_public_deployment:
        return _public_error_response(exc.status_code, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, _exc: RequestValidationError):
    if settings.is_public_deployment:
        return JSONResponse(
            status_code=422,
            content=public_error(
                "invalid_input", "请求内容无效，请检查后重试。", False
            ),
        )
    return JSONResponse(status_code=422, content={"detail": _exc.errors()})


@app.exception_handler(Exception)
async def unexpected_exception_handler(_request: Request, exc: Exception):
    print(f"UNHANDLED_REQUEST_ERROR type={type(exc).__name__}")
    if settings.is_public_deployment:
        return JSONResponse(
            status_code=500,
            content=public_error(
                "internal_error", "服务暂时不可用，请稍后重试。", True
            ),
        )
    raise exc

@app.middleware("http")
async def intercept_proxy_path(request: Request, call_next):
    """
    解决云部署环境或前端代理会在路径前拼接一段动态 ID 的问题。
    例如自动将 /5985f5334705698/api/trip/plan 重写为后端的真实路径 /api/trip/plan
    """
    path = request.scope.get("path", "")
    if "/api/" in path and not path.startswith("/api/"):
        api_index = path.find("/api/")
        request.scope["path"] = path[api_index:]
        
    return await call_next(request)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(trip.router, prefix="/api")
app.include_router(poi.router, prefix="/api")
app.include_router(map_routes.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(preferences.router, prefix="/api")
app.include_router(settings_routes.router, prefix="/api")
app.include_router(demo.router, prefix="/api")


@app.on_event("startup")
async def startup_event():
    """应用启动事件"""
    print("\n" + "="*60)
    print(f"🚀 {settings.app_name} v{settings.app_version}")
    print("="*60)
    
    # 打印配置信息
    print_config()
    
    # 验证配置
    try:
        validate_config()
        print("\n✅ 配置验证通过")
    except ValueError as e:
        print(f"\n❌ 配置验证失败:\n{e}")
        print("\n请检查环境变量配置；本地开发可通过 .env 提供，Docker 部署请通过容器环境变量提供")
        raise
    
    print("\n" + "="*60)
    print("📚 API文档: http://localhost:8000/docs")
    print("📖 ReDoc文档: http://localhost:8000/redoc")
    print("="*60 + "\n")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭事件"""
    print("\n" + "="*60)
    print("👋 应用正在关闭...")
    print("="*60 + "\n")


@app.get("/")
async def root():
    """根路径 - 生产环境返回前端页面，开发环境返回API信息"""
    # 检查前端构建产物是否存在（Docker 部署时会有）
    dist_index = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist" / "index.html"
    if dist_index.exists():
        return FileResponse(str(dist_index))
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "production": settings.app_env.strip().lower() == "production",
        "public_demo_mode": settings.public_demo_mode,
        "live_generation_available": settings.live_generation_available,
        "example_trip_available": demo._EXAMPLE_PATH.is_file(),
    }

# 挂载前端静态文件（生产环境 Docker 部署时）
_frontend_dist = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    # 挂载 assets 目录
    _assets_dir = _frontend_dist / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")
    # SPA catch-all: 未匹配的前端路由一律返回 index.html
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """SPA 前端路由 fallback"""
        file_path = _frontend_dist / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_frontend_dist / "index.html"))


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=True
    )
