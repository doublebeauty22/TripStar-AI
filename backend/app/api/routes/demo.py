"""Read-only portfolio example; isolated from evaluation and live generation."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from ...models.schemas import TripPlanResponse
from ...services.public_demo_guard import public_error

router = APIRouter(prefix="/demo", tags=["Portfolio demo"])
_EXAMPLE_PATH = Path(__file__).resolve().parents[4] / "portfolio" / "examples" / "example_trip_v1.json"
_FORBIDDEN_KEYS = {
    "golden_case_id", "reviewer", "baseline", "candidate", "prompt_version",
    "model_identity", "revision_history", "raw_xhs_context",
}


def _assert_sanitized(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = _FORBIDDEN_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"forbidden portfolio fields: {sorted(forbidden)}")
        for item in value.values():
            _assert_sanitized(item)
    elif isinstance(value, list):
        for item in value:
            _assert_sanitized(item)


@lru_cache(maxsize=1)
def load_example_trip() -> Dict[str, Any]:
    payload = json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8"))
    _assert_sanitized(payload)
    if payload.get("example") is not True:
        raise ValueError("portfolio example must be explicitly marked")
    TripPlanResponse.model_validate(payload.get("result"))
    return payload


@router.get("/example-trip")
async def get_example_trip():
    try:
        return load_example_trip()
    except Exception as exc:
        print(f"PORTFOLIO_EXAMPLE_LOAD_FAILED type={type(exc).__name__}")
        raise HTTPException(
            status_code=503,
            detail=public_error(
                "example_unavailable", "示例行程暂时不可用。", True
            ),
        ) from exc
