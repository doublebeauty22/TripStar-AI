"""Evaluation-only Phase 4A pacing simulation; not imported by production paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY = ROOT / "eval" / "phase4a" / "pacing_policy.json"


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _known(field: Any) -> int | None:
    if isinstance(field, dict) and field.get("status") == "known":
        value = field.get("value")
        return int(value) if isinstance(value, (int, float)) else None
    return int(field) if isinstance(field, (int, float)) else None


def _route_class(day: dict[str, Any]) -> str:
    text = " ".join(
        [day.get("city", ""), day.get("description", ""), day.get("transfer_info", "")]
        + [item.get("name", "") + " " + item.get("category", "") for item in day.get("attractions", [])]
    ).casefold()
    if day.get("is_transfer_day"):
        return "inter_city"
    suburban_tokens = (
        "郊", "雪山", "都江堰", "大鹏", "较场尾", "桔钓沙", "杨梅坑",
        "suburban", "excursion", "mountain",
    )
    return "suburban" if any(token in text for token in suburban_tokens) else "urban"


def _routes_for_day(artifact: dict[str, Any], day_index: int) -> list[dict[str, Any]]:
    routes = [item for item in artifact.get("route_checks", []) if item.get("day_index") == day_index]
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for route in routes:
        key = (str(route.get("origin_stable_id")), str(route.get("destination_stable_id")))
        current = unique.get(key)
        if current is None or (_known(current.get("duration_s")) is None and _known(route.get("duration_s")) is not None):
            unique[key] = route
    return list(unique.values())


def simulate_day(
    day: dict[str, Any], artifact: dict[str, Any], pace: str, policy: dict[str, Any],
    *, day_position: int | None = None,
) -> dict[str, Any]:
    pace_policy = policy["pace"][pace]
    attractions = day.get("attractions", [])
    attraction_minutes = sum(max(0, int(item.get("visit_duration") or 0)) for item in attractions)
    required_legs = max(0, len(attractions) - 1)
    route_day_index = day_position if day_position is not None else day.get("day_index", 0)
    routes = _routes_for_day(artifact, route_day_index)
    verified = [round(value / 60) for item in routes if (value := _known(item.get("duration_s"))) is not None]
    verified = verified[:required_legs]
    unknown_legs = max(0, required_legs - len(verified))
    route_class = _route_class(day)
    estimate_unit = policy["buffers"]["estimated_route_minutes"][route_class]
    estimated_travel = unknown_legs * estimate_unit if estimate_unit is not None else 0
    uncertainty_unit = policy["buffers"]["uncertainty_per_estimated_leg_minutes"][route_class]
    uncertainty = unknown_legs * uncertainty_unit
    access = len(attractions) * policy["buffers"]["access_per_attraction_minutes"]
    access += sum(bool(item.get("reservation_required")) for item in attractions) * policy["buffers"]["reservation_extra_minutes"]
    meal = pace_policy["meal_minutes"] if day.get("meals") else 0
    rest = pace_policy["minimum_rest_minutes"]
    effective = attraction_minutes + sum(verified) + estimated_travel + meal + access + rest + uncertainty
    start = day.get("start_time") or policy["time_policy"]["nominal_start"][pace]
    available = _minutes(policy["time_policy"]["day_finish"][pace]) - _minutes(start)
    available = max(policy["time_policy"]["minimum_window_minutes"], min(available, policy["time_policy"]["maximum_window_minutes"]))
    ratio = effective / available
    confidence = "HIGH" if unknown_legs == 0 else ("MEDIUM" if route_class != "inter_city" else "LOW")
    reasons: list[str] = []
    if ratio >= pace_policy["revisable_load_ratio"]:
        status = "revisable_overload"
        reasons.append("effective_load_exceeds_maximum")
    elif ratio >= pace_policy["warning_load_ratio"]:
        status = "warning"
        reasons.append("effective_load_above_target")
    else:
        status = "within_target"
    if _minutes(start) <= 8 * 60:
        reasons.append("start_at_or_before_08_00")
    if route_class == "suburban":
        reasons.append("suburban_excursion")
    if unknown_legs > pace_policy["max_low_confidence_legs_before_warning"]:
        reasons.append("multiple_unknown_routes")
        if status == "within_target":
            status = "warning"
    if any(token in (item.get("category") or "").casefold() for item in attractions for token in ("高山", "雪山", "高海拔")):
        reasons.append("altitude_burden")
    return {
        "day_index": day.get("day_index"), "day_position": route_day_index, "pace": pace,
        "attraction_minutes": attraction_minutes,
        "verified_travel_minutes": sum(verified), "estimated_travel_minutes": estimated_travel,
        "meal_minutes": meal, "access_buffer_minutes": access, "rest_buffer_minutes": rest,
        "uncertainty_buffer_minutes": uncertainty, "effective_load_minutes": effective,
        "available_day_window_minutes": available, "load_ratio": round(ratio, 3),
        "confidence": confidence, "overload_status": status, "overload_reasons": reasons,
        "evidence_sources": ["TripPlan.visit_duration", "TripPlan.start_time", "TripPlan.meals", "capture.route_checks"],
        "policy_assumptions": [f"route_class={route_class}", "meal/access/rest/uncertainty buffers"],
    }


def simulate_artifact(path: str | Path, policy_path: str | Path = DEFAULT_POLICY) -> list[dict[str, Any]]:
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    plan = artifact["final_trip_plan"]["value"]
    pace = artifact["trip_request"]["preference_profile"]["pace"]
    return [
        simulate_day(day, artifact, pace, policy, day_position=position)
        for position, day in enumerate(plan["days"])
    ]
