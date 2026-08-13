"""Small, process-local cost guard for the single-worker portfolio demo."""

import asyncio
import time
from dataclasses import dataclass
from typing import Dict

from fastapi import HTTPException, Request

from ..config import get_settings


def public_error(code: str, message: str, retryable: bool) -> dict:
    return {"error": {"code": code, "message": message, "retryable": retryable}}


def client_identity(request: Request | None) -> str:
    """Use the ASGI peer only; arbitrary forwarding headers are not trusted."""
    if request is None or request.client is None:
        return "direct-call"
    return request.client.host or "unknown"


@dataclass(frozen=True)
class GenerationReservation:
    task_id: str
    client_id: str


class PublicDemoGuard:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active: Dict[str, str] = {}
        self._last_action: Dict[tuple[str, str], float] = {}

    async def reserve_generation(
        self, request: Request | None, task_id: str
    ) -> GenerationReservation | None:
        settings = get_settings()
        if not settings.is_public_deployment:
            return None
        if not settings.live_generation_available:
            raise HTTPException(
                status_code=503,
                detail=public_error(
                    "generation_unavailable",
                    "实时生成暂不可用，请稍后重试或查看示例行程。",
                    True,
                ),
            )
        client_id = client_identity(request)
        now = time.monotonic()
        async with self._lock:
            last = self._last_action.get((client_id, "generation"))
            if last is not None and now - last < settings.public_generation_cooldown_seconds:
                raise HTTPException(
                    status_code=429,
                    detail=public_error(
                        "rate_limited",
                        "实时生成请求过于频繁，请稍后再试。",
                        True,
                    ),
                )
            if len(self._active) >= settings.public_max_concurrent_generations:
                raise HTTPException(
                    status_code=429,
                    detail=public_error(
                        "generation_capacity_reached",
                        "当前已有行程正在生成，请稍后再试或查看示例行程。",
                        True,
                    ),
                )
            self._active[task_id] = client_id
            self._last_action[(client_id, "generation")] = now
        return GenerationReservation(task_id=task_id, client_id=client_id)

    async def release_generation(self, task_id: str) -> None:
        async with self._lock:
            self._active.pop(task_id, None)

    async def check_auxiliary(self, request: Request | None, action: str) -> None:
        settings = get_settings()
        if not settings.is_public_deployment:
            return
        client_id = client_identity(request)
        now = time.monotonic()
        key = (client_id, action)
        async with self._lock:
            last = self._last_action.get(key)
            if last is not None and now - last < settings.public_auxiliary_cooldown_seconds:
                raise HTTPException(
                    status_code=429,
                    detail=public_error(
                        "rate_limited",
                        "请求过于频繁，请稍后再试。",
                        True,
                    ),
                )
            self._last_action[key] = now

    def active_count(self) -> int:
        return len(self._active)

    def reset_for_tests(self) -> None:
        self._active.clear()
        self._last_action.clear()


public_demo_guard = PublicDemoGuard()
