"""Offline adapter from immutable capture artifacts to production pacing policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models.schemas import TripPlan
from ..services.pacing_policy import RouteLegEvidence, calculate_daily_load, infer_route_class


def _known_minutes(value: Any) -> int | None:
    if isinstance(value, dict) and value.get("status") == "known":
        raw = value.get("value")
        return round(raw / 60) if isinstance(raw, (int, float)) else None
    return None


def assess_capture_artifact(path: str | Path) -> list[dict[str, Any]]:
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    plan = TripPlan.model_validate(artifact["final_trip_plan"]["value"])
    pace = artifact["trip_request"]["preference_profile"]["pace"]
    assessments = []
    for position, day in enumerate(plan.days):
        required = max(0, len(day.attractions) - 1)
        observations = [item for item in artifact.get("route_checks", [])
                        if item.get("day_index") == position]
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for item in observations:
            key = (str(item.get("origin_stable_id")), str(item.get("destination_stable_id")))
            current = unique.get(key)
            if current is None or (_known_minutes(current.get("duration_s")) is None
                                   and _known_minutes(item.get("duration_s")) is not None):
                unique[key] = item
        route_class = infer_route_class(day)
        legs = []
        for item in list(unique.values())[:required]:
            duration = _known_minutes(item.get("duration_s"))
            if duration is not None:
                feasible = item.get("feasible", {}).get("value")
                legs.append(RouteLegEvidence(
                    state="infeasible" if feasible is False else "verified",
                    duration_minutes=duration, source=item.get("provider", "capture"),
                    route_class=route_class,
                ))
            else:
                legs.append(RouteLegEvidence(
                    state="unavailable" if item.get("verification_status") == "unavailable" else "unknown",
                    source=item.get("provider", "capture"), route_class=route_class,
                    reason=item.get("reason"),
                ))
        while len(legs) < required:
            legs.append(RouteLegEvidence(
                state="unknown", source="capture_missing_leg", route_class=route_class,
                reason="route_observation_missing",
            ))
        if day.is_transfer_day:
            legs.append(RouteLegEvidence(
                state="unknown", source="DayPlan.is_transfer_day", route_class="inter_city",
                reason="structured_inter_city_duration_unavailable",
            ))
        assessments.append(calculate_daily_load(
            day, pace, legs, day_position=position
        ).to_dict())
    return assessments
