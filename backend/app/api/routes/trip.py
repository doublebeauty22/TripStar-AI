"""旅行规划 API 路由 - WebSocket 同步 + 轮询兼容模式"""

import asyncio
import hashlib
import json
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from ...agents.trip_planner_agent import get_trip_planner_agent
from ...models.schemas import (
    TripPatchRequest, TripPatchResult, TripPlan, TripPlanResponse, TripRequest,
)
from ...services.knowledge_graph_service import build_knowledge_graph
from ...services.llm_service import (
    generation_llm_execution,
    get_generation_usage,
    get_or_create_generation_usage,
    llm_execution,
    release_generation_usage,
)
from ...config import get_settings
from ...services.public_demo_guard import client_identity, public_demo_guard, public_error

router = APIRouter(prefix="/trip", tags=["旅行规划"])

# 内存任务存储（单实例部署足够）
_tasks: Dict[str, Dict[str, Any]] = {}
_active_trip_fingerprints: Dict[str, str] = {}
_trip_patch_locks: Dict[str, asyncio.Lock] = {}
_FINAL_TASK_STATUS = {"completed", "failed"}
_TASKS_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "trip_tasks"


def _create_task_state(task_id: str) -> Dict[str, Any]:
    """初始化任务状态。"""
    return {
        "task_id": task_id,
        "plan_id": task_id,
        "status": "processing",
        "stage": "submitted",
        "progress": 0,
        "message": "任务已提交，等待执行...",
        "result": None,
        "error": None,
        "request_payload": None,
        "subscribers": [],  # list[asyncio.Queue]
        "request_fingerprint": None,
        "logical_llm_calls": 0,
        "llm_stage": "",
        "llm_model": "",
        "llm_retry_count": 0,
        "generation_id": None,
        "llm_stage_calls": {},
        "llm_prompt_tokens": 0,
        "llm_completion_tokens": 0,
        "llm_total_tokens": 0,
        "deduplicated_generation_ids": [],
        "plan_version": 1,
        "patch_history": [],
        "patch_requests": {},
        "public_client_id": None,
    }


def _serialize_result(result: Any) -> Any:
    if result is None:
        return None
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return result


def _sanitize_request_payload_for_persistence(payload: Any) -> Any:
    """Remove free-form preference text from the task's disk representation."""
    if not isinstance(payload, dict):
        return payload
    sanitized = dict(payload)
    if "free_text_input" in sanitized:
        sanitized["free_text_input"] = ""
    profile = sanitized.get("preference_profile")
    if isinstance(profile, dict):
        sanitized_profile = dict(profile)
        if "special_requirements" in sanitized_profile:
            sanitized_profile["special_requirements"] = ""
        sanitized["preference_profile"] = sanitized_profile
    return sanitized


def _log_generation_summary(
    generation_id: str,
    task_id: str,
    snapshot: Dict[str, Any],
) -> None:
    """Emit one prompt-free, non-sensitive cost summary for a generation."""
    stage_calls = snapshot.get("stage_calls", {})
    print(
        "LLM_GENERATION_SUMMARY "
        f"generation_id={generation_id} task_id={task_id} "
        f"preference_calls={stage_calls.get('preference', 0)} "
        f"xhs_calls={stage_calls.get('xhs_research', 0)} "
        f"planner_calls={stage_calls.get('planner', 0)} "
        f"critic_calls={stage_calls.get('critic', 0)} "
        f"revision_calls={stage_calls.get('revision', 0)} "
        f"repair_calls={stage_calls.get('json_repair', 0)} "
        f"total_logical_calls={snapshot.get('logical_llm_calls', 0)} "
        f"prompt_tokens={snapshot.get('prompt_tokens', 0)} "
        f"completion_tokens={snapshot.get('completion_tokens', 0)} "
        f"total_tokens={snapshot.get('total_tokens', 0)} "
        f"retry_count={snapshot.get('retry_count', 0)} "
        f"model={snapshot.get('model', '')}"
    )


def _task_file_path(task_id: str) -> Path:
    """获取任务持久化文件路径。"""
    return _TASKS_DATA_DIR / f"{task_id}.json"


def _safe_persisted_plan_version(payload: Dict[str, Any]) -> int | None:
    """Resolve a version without ever resetting an already-patched task to v1."""
    result = payload.get("result")
    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    data = result.get("data") if isinstance(result, dict) else None
    candidates: list[int] = []
    if isinstance(data, dict):
        candidates.append(data.get("plan_version"))
    candidates.append(payload.get("plan_version"))
    for item in payload.get("patch_history") or []:
        if isinstance(item, dict):
            candidates.append(item.get("plan_version"))
    for item in (payload.get("patch_requests") or {}).values():
        if isinstance(item, dict):
            candidates.append(item.get("plan_version"))
            updated = item.get("updated_plan")
            if isinstance(updated, dict):
                candidates.append(updated.get("plan_version"))
    valid = [value for value in candidates if isinstance(value, int) and value >= 1]
    if valid:
        return max(valid)
    if payload.get("patch_history") or payload.get("patch_requests"):
        # Existing patch metadata without a recoverable version is ambiguous.
        return None
    return 1


def _ensure_task_plan_version(task: Dict[str, Any]) -> bool:
    """Normalize only version metadata; never touch TripPlan business fields."""
    resolved = _safe_persisted_plan_version(task)
    if resolved is None:
        return False
    changed = task.get("plan_version") != resolved
    task["plan_version"] = resolved
    result = task.get("result")
    if hasattr(result, "model_dump"):
        # New Pydantic results already carry the schema default/version.
        return changed
    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        if result["data"].get("plan_version") != resolved:
            result["data"]["plan_version"] = resolved
            changed = True
    return changed


def _normalize_loaded_task(task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """将磁盘中的任务结构恢复为内存可用格式。"""
    task = _create_task_state(task_id)
    task.update(
        {
            "plan_id": payload.get("plan_id", task_id),
            "status": payload.get("status", "failed"),
            "stage": payload.get("stage", "failed"),
            "progress": payload.get("progress", 100),
            "message": payload.get("message", ""),
            "result": payload.get("result"),
            "error": payload.get("error"),
            "request_payload": payload.get("request_payload"),
            "logical_llm_calls": payload.get("logical_llm_calls", 0),
            "llm_stage": payload.get("llm_stage", ""),
            "llm_model": payload.get("llm_model", ""),
            "llm_retry_count": payload.get("llm_retry_count", 0),
            "generation_id": payload.get("generation_id"),
            "llm_stage_calls": payload.get("llm_stage_calls", {}),
            "llm_prompt_tokens": payload.get("llm_prompt_tokens", 0),
            "llm_completion_tokens": payload.get("llm_completion_tokens", 0),
            "llm_total_tokens": payload.get("llm_total_tokens", 0),
            "plan_version": payload.get("plan_version"),
            "patch_history": payload.get("patch_history", []),
            "patch_requests": payload.get("patch_requests", {}),
        }
    )
    task["subscribers"] = []
    task["_plan_version_needs_persistence"] = _ensure_task_plan_version(task)

    # 服务重启后，处理中任务无法恢复执行，直接标记为失败，避免前端无限等待。
    if task["status"] not in _FINAL_TASK_STATUS:
        task["status"] = "failed"
        task["stage"] = "failed"
        task["progress"] = 100
        task["error"] = "服务已重启，未完成的旅行规划任务无法恢复，请重新生成。"
        task["message"] = task["error"]

    return task


def _persist_task_state(task_id: str, task: Dict[str, Any]) -> bool:
    """将任务状态持久化到本地 JSON 文件。"""
    try:
        _TASKS_DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task_id,
            "plan_id": task.get("plan_id", task_id),
            "status": task.get("status", "processing"),
            "stage": task.get("stage", ""),
            "progress": task.get("progress", 0),
            "message": task.get("message", ""),
            "result": _serialize_result(task.get("result")),
            "error": task.get("error"),
            "request_payload": _sanitize_request_payload_for_persistence(
                task.get("request_payload")
            ),
            "logical_llm_calls": task.get("logical_llm_calls", 0),
            "llm_stage": task.get("llm_stage", ""),
            "llm_model": task.get("llm_model", ""),
            "llm_retry_count": task.get("llm_retry_count", 0),
            "generation_id": task.get("generation_id"),
            "llm_stage_calls": task.get("llm_stage_calls", {}),
            "llm_prompt_tokens": task.get("llm_prompt_tokens", 0),
            "llm_completion_tokens": task.get("llm_completion_tokens", 0),
            "llm_total_tokens": task.get("llm_total_tokens", 0),
            "plan_version": task.get("plan_version", 1),
            "patch_history": task.get("patch_history", []),
            "patch_requests": task.get("patch_requests", {}),
        }
        target = _task_file_path(task_id)
        tmp = target.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp.replace(target)
        return True
    except Exception as e:
        print(f"⚠️  持久化任务 {task_id} 失败: {e}")
        return False


def _load_task_from_disk(task_id: str) -> Dict[str, Any] | None:
    """从磁盘加载单个任务。"""
    path = _task_file_path(task_id)
    if not path.exists():
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None
        task = _normalize_loaded_task(task_id, payload)
        _tasks[task_id] = task
        return task
    except Exception as e:
        print(f"⚠️  读取任务 {task_id} 失败: {e}")
        return None


def _load_persisted_tasks() -> None:
    """服务启动时预加载历史任务。"""
    if not _TASKS_DATA_DIR.exists():
        return

    loaded = 0
    for path in sorted(_TASKS_DATA_DIR.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                continue
            task_id = str(payload.get("task_id") or path.stem)
            _tasks[task_id] = _normalize_loaded_task(task_id, payload)
            loaded += 1
        except Exception as e:
            print(f"⚠️  加载历史任务 {path.name} 失败: {e}")

    if loaded:
        print(f"📦 已加载 {loaded} 个持久化旅行任务")


def _get_task(task_id: str) -> Dict[str, Any] | None:
    """优先从内存读取任务，不存在时回退到磁盘。"""
    return _tasks.get(task_id) or _load_task_from_disk(task_id)


def _build_history_item(task_id: str, payload: Dict[str, Any], updated_at: str) -> Dict[str, Any] | None:
    """从持久化任务中提取首页历史列表所需的摘要。"""
    if payload.get("status") != "completed":
        return None

    result = payload.get("result") or {}
    plan = result.get("data") or {}
    request_payload = payload.get("request_payload") or {}

    city = plan.get("city") or request_payload.get("city") or ""
    cities = plan.get("cities") or []
    start_date = plan.get("start_date") or request_payload.get("start_date") or ""
    end_date = plan.get("end_date") or request_payload.get("end_date") or ""
    days = plan.get("days") or []
    travel_days = request_payload.get("travel_days") or (len(days) if isinstance(days, list) else 0)
    overall_suggestions = plan.get("overall_suggestions") or result.get("message") or ""

    if not city and not cities:
        return None

    # 多城市时 city 显示为 "北京 → 西安" 形式
    display_city = ' → '.join(cities) if len(cities) > 1 else city

    return {
        "plan_id": payload.get("plan_id", task_id),
        "task_id": task_id,
        "city": display_city,
        "cities": cities,
        "start_date": start_date,
        "end_date": end_date,
        "travel_days": travel_days,
        "updated_at": updated_at,
        "overall_suggestions": overall_suggestions,
    }


def _load_history_items(limit: int = 10) -> list[Dict[str, Any]]:
    """按最近更新时间返回已完成的历史计划摘要。"""
    if not _TASKS_DATA_DIR.exists():
        return []

    items: list[Dict[str, Any]] = []
    for path in sorted(_TASKS_DATA_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                continue
            updated_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
            item = _build_history_item(str(payload.get("task_id") or path.stem), payload, updated_at)
            if item:
                items.append(item)
            if len(items) >= limit:
                break
        except Exception as e:
            print(f"⚠️  读取历史任务 {path.name} 失败: {e}")

    return items


def _build_task_event(task_id: str, task: Dict[str, Any], include_result: bool = True) -> Dict[str, Any]:
    """从任务状态构建对前端可消费的事件对象。"""
    event = {
        "task_id": task_id,
        "plan_id": task.get("plan_id", task_id),
        "status": task.get("status", "processing"),
        "stage": task.get("stage", ""),
        "progress": task.get("progress", 0),
        "message": task.get("message", ""),
    }
    if task.get("error"):
        if get_settings().is_public_deployment:
            safe_message = "本次实时生成暂时失败，请稍后重试或查看示例行程。"
            event["message"] = safe_message
            event["error"] = safe_message
        else:
            event["error"] = task["error"]
    if task.get("status") == "failed" and task.get("request_payload") is not None:
        event["request_payload"] = task["request_payload"]
    if include_result and task.get("result") is not None:
        event["result"] = _serialize_result(task["result"])
    return event


def _broadcast_task_event(task_id: str, event: Dict[str, Any]) -> None:
    """将任务事件广播给当前所有 WebSocket 订阅者。"""
    task = _tasks.get(task_id)
    if not task:
        return

    dead_queues = []
    for queue in task.get("subscribers", []):
        try:
            queue.put_nowait(event)
        except Exception:
            dead_queues.append(queue)

    if dead_queues:
        task["subscribers"] = [q for q in task.get("subscribers", []) if q not in dead_queues]


async def _update_task_state(
    task_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    result: Any = None,
    error: str | None = None,
) -> None:
    """更新任务状态并广播事件。"""
    task = _tasks.get(task_id)
    if not task:
        return

    if status is not None:
        task["status"] = status
    if stage is not None:
        task["stage"] = stage
    if progress is not None:
        task["progress"] = progress
    if message is not None:
        task["message"] = message
    if result is not None:
        task["result"] = result
    if error is not None:
        task["error"] = error

    _persist_task_state(task_id, task)
    event = _build_task_event(task_id, task, include_result=True)
    _broadcast_task_event(task_id, event)


@router.post(
    "/plan",
    summary="提交旅行规划任务",
    description="异步提交旅行规划请求，立即返回 task_id；可通过 WebSocket 或 /trip/status/{task_id} 获取执行状态",
)
async def plan_trip(request: TripRequest, http_request: Request = None):
    """提交旅行规划任务（立即返回 task_id）。"""
    fingerprint_payload = request.model_dump(mode="json")
    # Correlation metadata must not change semantic request deduplication.
    fingerprint_payload.pop("generation_id", None)
    request_json = json.dumps(
        fingerprint_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
    existing_task_id = _active_trip_fingerprints.get(fingerprint)
    existing_task = _tasks.get(existing_task_id) if existing_task_id else None
    incoming_client_id = client_identity(http_request)
    if (
        existing_task
        and get_settings().is_public_deployment
        and existing_task.get("public_client_id") != incoming_client_id
    ):
        # Never reveal another visitor's active task ID through deduplication.
        existing_task = None
    if existing_task and existing_task.get("status") not in _FINAL_TASK_STATUS:
        incoming_generation_id = (request.generation_id or "").strip()
        existing_generation_id = (existing_task.get("generation_id") or "").strip()
        if incoming_generation_id and incoming_generation_id != existing_generation_id:
            aliases = existing_task.setdefault("deduplicated_generation_ids", [])
            if incoming_generation_id not in aliases:
                aliases.append(incoming_generation_id)
                # Ensure a direct-to-trip generation also receives a terminal summary.
                get_or_create_generation_usage(incoming_generation_id)
        print(f"♻️ [TRIP_DEDUPE] fingerprint={fingerprint[:12]} task_id={existing_task_id}")
        return {
            "task_id": existing_task_id,
            "plan_id": existing_task.get("plan_id", existing_task_id),
            "status": existing_task.get("status", "processing"),
            "ws_url": f"/api/trip/ws/{existing_task_id}",
            "message": "相同旅行规划任务正在执行，已返回现有任务。",
            "deduplicated": True,
        }

    # Full UUID entropy is a privacy boundary improvement, but not authorization.
    task_id = str(uuid.uuid4())
    await public_demo_guard.reserve_generation(http_request, task_id)
    _tasks[task_id] = _create_task_state(task_id)
    _tasks[task_id]["public_client_id"] = incoming_client_id
    generation_id = (request.generation_id or "").strip() or task_id
    _tasks[task_id]["generation_id"] = generation_id
    _tasks[task_id]["request_fingerprint"] = fingerprint
    _tasks[task_id]["request_payload"] = request.model_dump(mode="json")
    _active_trip_fingerprints[fingerprint] = task_id
    _persist_task_state(task_id, _tasks[task_id])

    _city_display = ' → '.join(cs.city for cs in request.cities) if request.cities else request.city
    print(f"\n{'=' * 60}")
    print(f"📥 收到旅行规划请求 (task_id={task_id}):")
    print(f"   城市: {_city_display}")
    print(f"   日期: {request.start_date} - {request.end_date}")
    print(f"   天数: {request.travel_days}")
    print(f"{'=' * 60}\n")

    await _update_task_state(
        task_id,
        status="processing",
        stage="submitted",
        progress=5,
        message="任务已提交，正在初始化流程...",
    )

    # 启动后台任务
    asyncio.create_task(_run_trip_planning(task_id, request))

    return {
        "task_id": task_id,
        "plan_id": task_id,
        "status": "processing",
        "ws_url": f"/api/trip/ws/{task_id}",
        "message": f"任务已提交，可通过 WebSocket /api/trip/ws/{task_id} 实时订阅状态",
    }


async def _run_trip_planning(task_id: str, request: TripRequest):
    """后台执行旅行规划并推送进度。"""
    try:
        await _update_task_state(
            task_id,
            status="processing",
            stage="initializing",
            progress=10,
            message="正在获取多智能体系统实例...",
        )
        agent = get_trip_planner_agent()

        async def progress_callback(stage: str, message: str, progress: int) -> None:
            await _update_task_state(
                task_id,
                status="processing",
                stage=stage,
                progress=progress,
                message=message,
            )

        def usage_update(usage) -> None:
            task = _tasks.get(task_id)
            if task is None:
                return
            snapshot = usage.snapshot()
            task["logical_llm_calls"] = snapshot["logical_llm_calls"]
            task["llm_stage"] = snapshot["llm_stage"]
            task["llm_model"] = snapshot["model"]
            task["llm_retry_count"] = snapshot["retry_count"]
            task["generation_id"] = snapshot["generation_id"]
            task["llm_stage_calls"] = snapshot["stage_calls"]
            task["llm_prompt_tokens"] = snapshot["prompt_tokens"]
            task["llm_completion_tokens"] = snapshot["completion_tokens"]
            task["llm_total_tokens"] = snapshot["total_tokens"]

        generation_id = _tasks.get(task_id, {}).get("generation_id") or task_id
        if request.generation_id:
            with generation_llm_execution(
                generation_id,
                task_id=task_id,
                on_update=usage_update,
            ):
                trip_plan = await agent.plan_trip(request, progress_callback=progress_callback)
        else:
            with llm_execution(task_id, on_update=usage_update):
                trip_plan = await agent.plan_trip(request, progress_callback=progress_callback)

        await _update_task_state(
            task_id,
            status="processing",
            stage="graph_building",
            progress=95,
            message="正在构建知识图谱...",
        )
        graph_data = build_knowledge_graph(trip_plan, language=getattr(request, 'language', 'zh') or 'zh')

        trip_result = TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            plan_id=task_id,
            data=trip_plan,
            graph_data=graph_data,
        )

        print(f"✅ 任务 {task_id} 完成")
        await _update_task_state(
            task_id,
            status="completed",
            stage="completed",
            progress=100,
            message="旅行计划生成成功",
            result=trip_result,
        )
    except Exception as e:
        print(f"❌ 任务 {task_id} 失败: {e}")
        traceback.print_exc()

        error_msg = (
            "本次实时生成暂时失败，请稍后重试或查看示例行程。"
            if get_settings().is_public_deployment
            else str(e)
        )

        await _update_task_state(
            task_id,
            status="failed",
            stage="failed",
            progress=100,
            message=error_msg,
            error=error_msg,
        )
    finally:
        task = _tasks.get(task_id, {})
        generation_id = task.get("generation_id") or task_id
        usage = get_generation_usage(generation_id) if request.generation_id else None
        snapshot = usage.snapshot() if usage is not None else {
            "stage_calls": task.get("llm_stage_calls", {}),
            "logical_llm_calls": task.get("logical_llm_calls", 0),
            "prompt_tokens": task.get("llm_prompt_tokens", 0),
            "completion_tokens": task.get("llm_completion_tokens", 0),
            "total_tokens": task.get("llm_total_tokens", 0),
            "retry_count": task.get("llm_retry_count", 0),
            "model": task.get("llm_model", ""),
        }
        stage_calls = snapshot.get("stage_calls", {})
        if task:
            task["logical_llm_calls"] = snapshot.get("logical_llm_calls", 0)
            task["llm_stage"] = snapshot.get("llm_stage", task.get("llm_stage", ""))
            task["llm_model"] = snapshot.get("model", task.get("llm_model", ""))
            task["llm_retry_count"] = snapshot.get("retry_count", 0)
            task["llm_stage_calls"] = stage_calls
            task["llm_prompt_tokens"] = snapshot.get("prompt_tokens", 0)
            task["llm_completion_tokens"] = snapshot.get("completion_tokens", 0)
            task["llm_total_tokens"] = snapshot.get("total_tokens", 0)
            _persist_task_state(task_id, task)
        _log_generation_summary(generation_id, task_id, snapshot)
        if request.generation_id:
            release_generation_usage(generation_id)
        # A semantically identical request can be deduplicated across two UI
        # generations. Its own Preference cost remains separately attributable;
        # the shared Planner request is charged only to the generation that ran it.
        for alias_generation_id in task.get("deduplicated_generation_ids", []):
            alias_usage = get_generation_usage(alias_generation_id)
            if alias_usage is not None:
                _log_generation_summary(
                    alias_generation_id,
                    task_id,
                    alias_usage.snapshot(),
                )
            release_generation_usage(alias_generation_id)
        fingerprint = _tasks.get(task_id, {}).get("request_fingerprint")
        if fingerprint and _active_trip_fingerprints.get(fingerprint) == task_id:
            _active_trip_fingerprints.pop(fingerprint, None)
        await public_demo_guard.release_generation(task_id)


@router.websocket("/ws/{task_id}")
async def trip_task_ws(websocket: WebSocket, task_id: str):
    """WebSocket 订阅任务状态。"""
    await websocket.accept()
    task = _get_task(task_id)
    if not task:
        await websocket.send_json(
            {
                "task_id": task_id,
                "plan_id": task_id,
                "status": "failed",
                "stage": "failed",
                "progress": 100,
                "message": "任务不存在",
                "error": "任务不存在",
            }
        )
        await websocket.close(code=1008)
        return

    queue: asyncio.Queue = asyncio.Queue()
    task["subscribers"].append(queue)

    # 先发送快照，保证前端后连也能同步当前状态
    snapshot = _build_task_event(task_id, task, include_result=True)
    await websocket.send_json(snapshot)
    if snapshot["status"] in _FINAL_TASK_STATUS:
        try:
            await websocket.close()
        except Exception:
            pass
        task["subscribers"] = [q for q in task.get("subscribers", []) if q is not queue]
        return

    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("status") in _FINAL_TASK_STATUS:
                break
    except WebSocketDisconnect:
        pass
    finally:
        task = _tasks.get(task_id)
        if task:
            task["subscribers"] = [q for q in task.get("subscribers", []) if q is not queue]
        try:
            await websocket.close()
        except Exception:
            pass


@router.get(
    "/history",
    summary="最近历史计划",
    description="返回最近成功生成的旅行计划摘要，供首页快速找回历史计划",
)
async def get_trip_history(limit: int = 10):
    """查询最近的历史计划摘要。"""
    settings = get_settings()
    if settings.is_public_deployment and not settings.public_history_enabled:
        raise HTTPException(
            status_code=403,
            detail=public_error(
                "feature_disabled", "公开演示环境不提供历史行程列表。", False
            ),
        )
    safe_limit = max(1, min(int(limit or 10), 50))
    return {
        "items": _load_history_items(safe_limit),
    }


@router.get(
    "/status/{task_id}",
    summary="查询任务状态",
    description="轮询旅行规划任务的执行状态和结果（兼容旧客户端）",
)
async def get_task_status(task_id: str):
    """查询任务执行状态。"""
    task = _get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=public_error("task_not_found", "未找到该行程任务。", False),
        )

    version_changed = _ensure_task_plan_version(task)
    version_changed = bool(task.pop("_plan_version_needs_persistence", False)) or version_changed
    if version_changed:
        _persist_task_state(task_id, task)

    if task["status"] == "completed":
        return {
            "task_id": task_id,
            "plan_id": task.get("plan_id", task_id),
            "status": "completed",
            "result": _serialize_result(task.get("result")),
        }
    if task["status"] == "failed":
        error_message = (
            "本次实时生成暂时失败，请稍后重试或查看示例行程。"
            if get_settings().is_public_deployment
            else task.get("error", "")
        )
        response = {
            "task_id": task_id,
            "plan_id": task.get("plan_id", task_id),
            "status": "failed",
            "error": error_message,
        }
        if not get_settings().is_public_deployment:
            response["request_payload"] = task.get("request_payload")
        return response
    return {
        "task_id": task_id,
        "plan_id": task.get("plan_id", task_id),
        "status": "processing",
        "stage": task.get("stage", ""),
        "progress": task.get("progress", 0),
        "progress_text": task.get("message", "处理中..."),
    }


def _task_trip_plan(task: Dict[str, Any]) -> TripPlan:
    result = task.get("result")
    if hasattr(result, "data"):
        data = result.data
    elif isinstance(result, dict):
        data = result.get("data")
    else:
        data = None
    if data is None:
        raise ValueError("任务没有可编辑的旅行计划")
    return data if isinstance(data, TripPlan) else TripPlan.model_validate(data)


async def _enrich_patch_pois(
    plan: TripPlan,
    patch,
) -> TripPlan:
    """Ground only added/replaced POIs; untouched identities are never rematched."""
    from ...models.schemas import AddPOIOperation, ReplacePOIOperation

    target_names: Dict[int, list[str]] = {}
    for operation in patch.operations:
        if isinstance(operation, (AddPOIOperation, ReplacePOIOperation)):
            target_names.setdefault(operation.day_index, []).append(operation.new_poi.name)
    if not target_names:
        return plan

    enriched_plan = plan.model_copy(deep=True)
    enriched_plan.days = []
    target_positions: list[tuple[int, int]] = []
    for day_index, names in target_names.items():
        day = plan.days[day_index].model_copy(deep=True)
        selected = []
        for name in names:
            positions = [
                position for position, attraction in enumerate(plan.days[day_index].attractions)
                if attraction.name == name
            ]
            if len(positions) != 1:
                raise ValueError(f"替换景点无法唯一定位: {name}")
            position = positions[0]
            selected.append(plan.days[day_index].attractions[position].model_copy(deep=True))
            target_positions.append((day_index, position))
        day.attractions = selected
        enriched_plan.days.append(day)

    agent = get_trip_planner_agent()
    enriched_plan = await agent._enrich_trip_plan_pois(enriched_plan)
    flattened = [poi for day in enriched_plan.days for poi in day.attractions]
    if len(flattened) != len(target_positions):
        raise ValueError("景点重新匹配结果不完整")
    updated = plan.model_copy(deep=True)
    for (day_index, position), attraction in zip(target_positions, flattened):
        if attraction.poi_match_status != "verified":
            raise ValueError(f"替换景点未能通过地图验证: {attraction.name}")
        updated.days[day_index].attractions[position] = attraction
    return updated


@router.post(
    "/{task_id}/patch",
    response_model=TripPatchResult,
    summary="对已生成行程执行一次局部修改",
)
async def patch_trip(task_id: str, request: TripPatchRequest, http_request: Request = None):
    """Interpret once, apply deterministically, validate, then atomically commit."""
    from ...services.trip_patch_service import (
        PATCH_MAX_LLM_CALLS, TripPatchEngine, get_trip_patch_interpreter,
    )
    from ...services.trip_validator_service import get_trip_validator_service

    await public_demo_guard.check_auxiliary(http_request, "patch")
    task = _get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    lock = _trip_patch_locks.setdefault(task_id, asyncio.Lock())
    async with lock:
        task = _get_task(task_id)
        _ensure_task_plan_version(task)
        if not isinstance(task.get("plan_version"), int):
            raise HTTPException(
                status_code=409,
                detail="行程存在无法安全恢复的版本历史，请刷新或联系支持。",
            )
        cached = task.get("patch_requests", {}).get(request.patch_request_id)
        if cached:
            return TripPatchResult.model_validate(cached)
        current_plan = _task_trip_plan(task)
        current_version = int(task.get("plan_version") or current_plan.plan_version or 1)
        if request.current_plan_version != current_version:
            raise HTTPException(
                status_code=409,
                detail=f"行程版本已更新（当前版本 {current_version}），请刷新后重试。",
            )

        original = current_plan.model_copy(deep=True)
        previous_result = task.get("result")
        previous_version = current_version
        previous_history = list(task.get("patch_history", []))
        trip_request = TripRequest.model_validate(task.get("request_payload") or {})
        engine = TripPatchEngine()
        usage_snapshot: Dict[str, Any] = {
            "logical_llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "retry_count": 0, "stage_calls": {}, "model": "",
        }
        operation_types: list[str] = []
        affected_days: list[int] = []
        requires_regeneration = False
        success = False
        result: TripPatchResult
        try:
            patch_usage = None
            try:
                with llm_execution(request.patch_request_id, max_calls=PATCH_MAX_LLM_CALLS) as patch_usage:
                    patch = await get_trip_patch_interpreter().interpret(
                        request.instruction, original, trip_request
                    )
            finally:
                if patch_usage is not None:
                    usage_snapshot = patch_usage.snapshot()
            operation_types = [operation.operation for operation in patch.operations]
            requires_regeneration = patch.requires_regeneration
            if patch.requires_regeneration:
                result = TripPatchResult(
                    success=False,
                    patch=patch,
                    requires_regeneration=True,
                    regeneration_reason=patch.regeneration_reason or "该修改影响整体行程结构，建议重新生成。",
                    plan_version=current_version,
                    patch_request_id=request.patch_request_id,
                )
            else:
                updated, affected_days = engine.apply_patch(original, patch)
                updated = await _enrich_patch_pois(updated, patch)
                validation = await get_trip_validator_service().validate(trip_request, updated)
                updated.risks = validation.risks
                updated.validation_status = validation.status
                # Phase 2C explicitly stops here; Phase 2B trigger is not called.
                updated.plan_version = current_version + 1
                diff = engine.compare_before_after(original, updated)
                if diff.changed_day_indices != affected_days:
                    raise ValueError("实际变更天数与 Patch scope 不一致")
                for index in diff.unchanged_day_indices:
                    if original.days[index].model_dump(mode="json") != updated.days[index].model_dump(mode="json"):
                        raise ValueError("未受影响天数发生变化")
                summary = engine.change_summary(diff)
                graph = build_knowledge_graph(
                    updated, language=getattr(trip_request, "language", "zh") or "zh"
                )
                trip_result = TripPlanResponse(
                    success=True,
                    message="行程局部修改成功",
                    plan_id=task_id,
                    data=updated,
                    graph_data=graph,
                )
                result = TripPatchResult(
                    success=True,
                    updated_plan=updated,
                    graph_data=graph.model_dump(mode="json") if hasattr(graph, "model_dump") else graph,
                    patch=patch,
                    changed_day_indices=diff.changed_day_indices,
                    change_summary=summary,
                    diff=diff,
                    validation_status=validation.status,
                    risks=validation.risks,
                    plan_version=updated.plan_version,
                    patch_request_id=request.patch_request_id,
                )
                # Commit only after interpretation, apply, enrichment, validation,
                # diff and graph construction have all succeeded.
                task["result"] = trip_result
                task["plan_version"] = updated.plan_version
                task.setdefault("patch_history", []).append({
                    "patch_request_id": request.patch_request_id,
                    "plan_version": updated.plan_version,
                    "changed_day_indices": diff.changed_day_indices,
                    "operation_types": operation_types,
                    "change_summary": summary,
                    "validation_status": validation.status,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                })
                success = True
            task.setdefault("patch_requests", {})[request.patch_request_id] = result.model_dump(mode="json")
            if not _persist_task_state(task_id, task):
                success = False
                task["result"] = previous_result
                task["plan_version"] = previous_version
                task["patch_history"] = previous_history
                task.get("patch_requests", {}).pop(request.patch_request_id, None)
                return TripPatchResult(
                    success=False,
                    error="行程修改未能安全持久化，原行程保持不变。",
                    plan_version=previous_version,
                    patch_request_id=request.patch_request_id,
                )
            return result
        except HTTPException:
            raise
        except Exception as exc:
            result = TripPatchResult(
                success=False,
                error=(
                    "行程修改暂时失败，原行程保持不变。"
                    if get_settings().is_public_deployment
                    else str(exc)
                ),
                plan_version=current_version,
                patch_request_id=request.patch_request_id,
            )
            task.setdefault("patch_requests", {})[request.patch_request_id] = result.model_dump(mode="json")
            _persist_task_state(task_id, task)
            return result
        finally:
            print(
                "TRIP_PATCH_SUMMARY "
                f"patch_request_id={request.patch_request_id} task_id={task_id} stage=trip_patch "
                f"logical_llm_calls={usage_snapshot.get('logical_llm_calls', 0)} "
                f"prompt_tokens={usage_snapshot.get('prompt_tokens', 0)} "
                f"completion_tokens={usage_snapshot.get('completion_tokens', 0)} "
                f"total_tokens={usage_snapshot.get('total_tokens', 0)} "
                f"retry_count={usage_snapshot.get('retry_count', 0)} "
                f"affected_days={affected_days} operation_types={operation_types} "
                f"requires_regeneration={str(requires_regeneration).lower()} "
                f"success={str(success).lower()}"
            )


@router.get(
    "/health",
    summary="健康检查",
    description="检查旅行规划服务是否正常",
)
async def health_check():
    """健康检查。"""
    try:
        agent = get_trip_planner_agent()
        return {
            "status": "healthy",
            "service": "trip-planner",
            "agent_name": agent.planner_agent_name,
            "tools_count": 0,
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"服务不可用: {str(e)}")


_load_persisted_tasks()
