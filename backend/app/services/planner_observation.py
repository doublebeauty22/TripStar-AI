"""Request-scoped, read-only observations for evaluation capture."""

import contextvars
from contextlib import contextmanager
from typing import Any, Iterator


_weather_observations: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "tripstar_weather_observations", default=None
)
_route_observations: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "tripstar_route_observations", default=None
)
_revision_observations: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "tripstar_revision_observations", default=None
)
_hotel_observations: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "tripstar_hotel_observations", default=None
)
_validation_scope: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "tripstar_validation_scope",
    default={"validation_pass_id": "legacy_unscoped", "validation_phase": "legacy_unscoped"},
)


@contextmanager
def capture_planner_observations() -> Iterator[dict[str, list[dict[str, Any]]]]:
    weather: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    revisions: list[dict[str, Any]] = []
    hotels: list[dict[str, Any]] = []
    weather_token = _weather_observations.set(weather)
    route_token = _route_observations.set(routes)
    revision_token = _revision_observations.set(revisions)
    hotel_token = _hotel_observations.set(hotels)
    try:
        yield {"weather": weather, "routes": routes, "revisions": revisions, "hotels": hotels}
    finally:
        _weather_observations.reset(weather_token)
        _route_observations.reset(route_token)
        _revision_observations.reset(revision_token)
        _hotel_observations.reset(hotel_token)


@contextmanager
def validation_pass(validation_pass_id: str, validation_phase: str) -> Iterator[None]:
    """Attach a stable pass scope to route observations without changing validation."""
    token = _validation_scope.set({
        "validation_pass_id": validation_pass_id,
        "validation_phase": validation_phase,
    })
    try:
        yield
    finally:
        _validation_scope.reset(token)


def current_validation_scope() -> dict[str, str]:
    """Expose the active scope for deterministic validation observability."""
    return dict(_validation_scope.get())


def observe_weather(provider: str, city: str, result: Any) -> None:
    target = _weather_observations.get()
    if target is not None:
        target.append({"provider": provider, "city": city, "result": result})


def observe_route(value: dict[str, Any]) -> None:
    target = _route_observations.get()
    if target is not None:
        target.append({**_validation_scope.get(), **value})


def observe_revision(event: str, **metadata: Any) -> None:
    target = _revision_observations.get()
    if target is not None:
        target.append({"event": event, **metadata})


def observe_hotel(provider: str, city: str, *, status: str, candidate_count: int,
                  reason: str | None = None) -> None:
    target = _hotel_observations.get()
    if target is not None:
        target.append({
            "provider": provider, "city": city, "status": status,
            "candidate_count": candidate_count, "reason": reason,
        })
