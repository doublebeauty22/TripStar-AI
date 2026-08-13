"""Versioned deterministic pacing policy used by Planner and Validator.

All thresholds are product assumptions proposed in Phase 4A. They are not
scientific or industry standards and must remain observable/versioned.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional, Sequence

from ..models.schemas import DayPlan, TravelPace


PACING_POLICY_VERSION = "pacing.daily_load.v0.proposed"
PRODUCTION_PACES: tuple[TravelPace, ...] = ("intensive", "balanced", "relaxed")
Confidence = Literal["HIGH", "MEDIUM", "LOW"]
OverloadStatus = Literal["within_target", "warning", "revisable_overload"]
RouteState = Literal["verified", "unavailable", "unknown", "infeasible"]


PACE_POLICY: dict[str, dict[str, Any]] = {
    "relaxed": {"finish": "19:30", "target": .78, "revisable": 1.05,
                "meal": 105, "rest": 75, "early": "09:00", "unknown_warning": 1},
    "balanced": {"finish": "20:30", "target": .86, "revisable": 1.00,
                 "meal": 105, "rest": 50, "early": "08:30", "unknown_warning": 2},
    "intensive": {"finish": "21:30", "target": .98, "revisable": 1.10,
                  "meal": 90, "rest": 30, "early": "07:30", "unknown_warning": 3},
}


@dataclass(frozen=True)
class RouteLegEvidence:
    state: RouteState
    duration_minutes: Optional[int] = None
    source: str = ""
    route_class: Literal["urban", "suburban", "inter_city"] = "urban"
    reason: Optional[str] = None


@dataclass(frozen=True)
class NormalizedAttractionLoad:
    raw_minutes: int
    effective_minutes: int
    effective_access_units: int
    classification: str = "ordinary_day"
    confidence_reduced: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DailyLoadBreakdown:
    attraction_minutes: int
    raw_attraction_minutes: int
    verified_travel_minutes: int
    estimated_travel_minutes: Optional[int]
    meal_minutes: int
    access_buffer_minutes: int
    rest_buffer_minutes: int
    uncertainty_buffer_minutes: int
    effective_load_minutes: Optional[int]
    available_day_window_minutes: int
    load_ratio: Optional[float]


@dataclass(frozen=True)
class DailyLoadAssessment:
    day_index: int
    requested_pace: TravelPace
    policy_version: str
    breakdown: DailyLoadBreakdown
    confidence: Confidence
    overload_status: OverloadStatus
    reasons: list[str]
    evidence: list[dict[str, Any]]
    policy_assumptions: list[dict[str, Any]]
    normalization: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clock_minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def compact_planner_contract(pace: TravelPace) -> dict[str, Any]:
    policy = PACE_POLICY[pace]
    return {
        "pacing_policy_version": PACING_POLICY_VERSION,
        "requested_pace": pace,
        "daily_behavior": {
            "relaxed": "leave substantial recovery time and avoid tightly chained activities",
            "balanced": "balance activities with meals, mobility, access time and recovery",
            "intensive": "allow a fuller day while retaining executable meals and mobility",
        }[pace],
        "early_start_expectation": f"avoid starts before {policy['early']} unless explicitly requested",
        "long_transfer_caution": "reduce activity load on suburban or transfer-heavy days",
        "rest_slack_minutes_at_least": policy["rest"],
        "unknown_route_rule": "reserve conservative mobility and uncertainty time; never treat unknown as zero",
    }


def _is_full_day_category(text: str) -> bool:
    tokens = ("theme park", "amusement park", "主题乐园", "主题公园", "大型景区", "large scenic")
    return any(token in text for token in tokens)


def _is_internal_activity(text: str) -> bool:
    tokens = ("ride", "attraction ride", "游乐设施", "园内项目", "内部项目", "演出项目")
    return any(token in text for token in tokens)


def normalize_attractions(day: DayPlan) -> NormalizedAttractionLoad:
    """Protect only obvious, evidence-backed overlap; ambiguous overlap lowers confidence."""
    attractions = list(day.attractions)
    raw = sum(max(0, item.visit_duration) for item in attractions)
    if not attractions:
        return NormalizedAttractionLoad(0, 0, 0)

    stable_groups: dict[str, list[Any]] = {}
    ungrouped: list[Any] = []
    for item in attractions:
        stable_id = item.place_id or item.poi_id
        if stable_id:
            stable_groups.setdefault(stable_id, []).append(item)
        else:
            ungrouped.append(item)
    deduplicated = [max(group, key=lambda item: item.visit_duration) for group in stable_groups.values()]
    deduplicated.extend(ungrouped)
    reasons = ["same_verified_complex_duration_deduplicated"] if len(deduplicated) < len(attractions) else []

    dominant = max(deduplicated, key=lambda item: item.visit_duration)
    dominant_text = f"{dominant.category or ''} {dominant.description or ''}".casefold()
    if dominant.visit_duration >= 420 and _is_full_day_category(dominant_text):
        internal = [item for item in deduplicated if item is not dominant and _is_internal_activity(
            f"{item.category or ''} {item.description or ''}".casefold()
        )]
        external = [item for item in deduplicated if item is not dominant and item not in internal]
        effective = dominant.visit_duration + sum(item.visit_duration for item in external)
        return NormalizedAttractionLoad(
            raw, effective, 1 + len(external), "full_day_attraction",
            confidence_reduced=bool(external),
            reasons=reasons + ["nested_internal_activities_not_double_counted"],
        )

    ambiguous = False
    verified = [item for item in deduplicated if item.place_id and item.location]
    for index, left in enumerate(verified):
        for right in verified[index + 1:]:
            if (abs(left.location.latitude - right.location.latitude) < .0015
                    and abs(left.location.longitude - right.location.longitude) < .0015):
                ambiguous = True
    if ambiguous:
        reasons.append("possible_same_area_overlap_not_normalized")
    return NormalizedAttractionLoad(
        raw, sum(item.visit_duration for item in deduplicated), len(deduplicated),
        confidence_reduced=ambiguous, reasons=reasons,
    )


def infer_route_class(day: DayPlan) -> Literal["urban", "suburban", "inter_city"]:
    if day.is_transfer_day:
        return "inter_city"
    text = " ".join([day.description or "", day.transfer_info or ""] + [
        f"{item.category or ''} {item.description or ''}" for item in day.attractions
    ]).casefold()
    generic = ("suburban", "excursion", "郊区", "远郊", "mountain", "雪山", "高山", "自然保护区")
    return "suburban" if any(token in text for token in generic) else "urban"


def calculate_daily_load(
    day: DayPlan,
    pace: TravelPace,
    route_legs: Sequence[RouteLegEvidence],
    *,
    day_position: Optional[int] = None,
) -> DailyLoadAssessment:
    policy = PACE_POLICY[pace]
    normalized = normalize_attractions(day)
    route_class = infer_route_class(day)
    verified = sum(leg.duration_minutes or 0 for leg in route_legs if leg.state in {"verified", "infeasible"})
    unresolved = [leg for leg in route_legs if leg.state in {"unavailable", "unknown"}]
    unbounded = [leg for leg in unresolved if leg.route_class == "inter_city" or route_class == "inter_city"]
    estimated: Optional[int]
    if unbounded:
        estimated = None
    else:
        estimate_units = {"urban": 30, "suburban": 60}
        estimated = sum(estimate_units.get(leg.route_class or route_class, estimate_units[route_class]) for leg in unresolved)
    uncertainty = sum(30 if leg.route_class == "inter_city" else 20 if leg.route_class == "suburban" else 10 for leg in unresolved)
    meal = policy["meal"] if day.meals else 0
    access = normalized.effective_access_units * 10 + sum(bool(item.reservation_required) for item in day.attractions) * 10
    rest = policy["rest"]
    start = day.start_time or {"relaxed": "09:30", "balanced": "09:00", "intensive": "08:00"}[pace]
    available = max(360, min(810, _clock_minutes(policy["finish"]) - _clock_minutes(start)))
    effective = None if estimated is None else normalized.effective_minutes + verified + estimated + meal + access + rest + uncertainty
    ratio = round(effective / available, 3) if effective is not None else None
    confidence: Confidence = "LOW" if unbounded else "MEDIUM" if unresolved or normalized.confidence_reduced else "HIGH"
    reasons = list(normalized.reasons)
    if ratio is None:
        status: OverloadStatus = "warning"
        reasons.append("unbounded_inter_city_mobility")
    elif ratio >= policy["revisable"]:
        status = "revisable_overload"; reasons.append("effective_load_exceeds_proposed_maximum")
    elif ratio >= policy["target"]:
        status = "warning"; reasons.append("effective_load_above_proposed_target")
    else:
        status = "within_target"
    if _clock_minutes(start) <= 8 * 60:
        reasons.append("start_at_or_before_08_00")
    if route_class == "suburban":
        reasons.append("suburban_excursion")
    if len(unresolved) > policy["unknown_warning"]:
        reasons.append("multiple_unknown_routes")
        if status == "within_target": status = "warning"
    evidence = [
        {"field": "DayPlan.start_time", "value": day.start_time},
        {"field": "Attraction.visit_duration", "raw_minutes": normalized.raw_minutes},
        {"field": "route_legs", "states": [leg.state for leg in route_legs]},
        {"field": "DayPlan.meals", "count": len(day.meals)},
    ]
    assumptions = [
        {"name": "policy_finish", "value": policy["finish"]},
        {"name": "route_fallback", "class": route_class, "estimated_minutes": estimated,
         "source": PACING_POLICY_VERSION},
        {"name": "meal_access_rest_uncertainty_buffers", "source": PACING_POLICY_VERSION},
    ]
    return DailyLoadAssessment(
        day_index=day_position if day_position is not None else day.day_index,
        requested_pace=pace, policy_version=PACING_POLICY_VERSION,
        breakdown=DailyLoadBreakdown(
            attraction_minutes=normalized.effective_minutes, raw_attraction_minutes=normalized.raw_minutes,
            verified_travel_minutes=verified, estimated_travel_minutes=estimated,
            meal_minutes=meal, access_buffer_minutes=access, rest_buffer_minutes=rest,
            uncertainty_buffer_minutes=uncertainty, effective_load_minutes=effective,
            available_day_window_minutes=available, load_ratio=ratio,
        ), confidence=confidence, overload_status=status, reasons=reasons,
        evidence=evidence, policy_assumptions=assumptions, normalization=asdict(normalized),
    )
