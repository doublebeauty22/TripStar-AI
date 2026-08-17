"""Small, log-safe timing helpers for bounded production stages."""

from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
import time
from typing import Callable, Iterator, TypeVar


_ALLOWED_STAGES = {
    "trip_stage_timing": {
        "total_trip", "xhs_research", "weather", "hotel_search", "planner",
        "poi_enrichment", "validator", "revision", "knowledge_graph",
        "persistence",
    },
    "image_stage_timing": {
        "image_total", "google_grounding", "google_photo", "xhs_image",
    },
}


@dataclass
class TimingOutcome:
    """Allow callers that handle an error internally to mark the event failed."""

    success: bool = True

    def mark_failed(self) -> None:
        self.success = False


def _emit_timing(event: str, stage: str, started: float, success: bool) -> None:
    duration_ms = max(0, int((time.perf_counter() - started) * 1000))
    print(
        f"event={event} stage={stage} duration_ms={duration_ms} "
        f"success={str(success).lower()}",
        flush=True,
    )


@contextmanager
def timed_stage(event: str, stage: str) -> Iterator[TimingOutcome]:
    """Time one allow-listed stage, logging no request or exception details."""
    if stage not in _ALLOWED_STAGES.get(event, set()):
        raise ValueError("unsupported timing event or stage")
    started = time.perf_counter()
    outcome = TimingOutcome()
    try:
        yield outcome
    except BaseException:
        outcome.mark_failed()
        raise
    finally:
        _emit_timing(event, stage, started, outcome.success)


_AsyncCallable = TypeVar("_AsyncCallable", bound=Callable)


def timed_async_stage(event: str, stage: str) -> Callable[[_AsyncCallable], _AsyncCallable]:
    """Decorate an async function with the same bounded timing contract."""
    def decorator(function: _AsyncCallable) -> _AsyncCallable:
        @wraps(function)
        async def wrapped(*args, **kwargs):
            with timed_stage(event, stage):
                return await function(*args, **kwargs)

        return wrapped  # type: ignore[return-value]

    return decorator
