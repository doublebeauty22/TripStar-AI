"""Phase 2A deterministic trip validation.

This module validates only facts present in the request/plan or returned by the
configured Google Directions service. It never asks an LLM to infer a risk.
"""

import asyncio
from typing import Dict, Optional, Tuple

from ..models.schemas import (
    Attraction,
    RiskItem,
    TripPlan,
    TripRequest,
    ValidationResult,
    has_valid_verified_coordinates,
)
from .pacing_policy import (
    PACING_POLICY_VERSION, RouteLegEvidence, calculate_daily_load, infer_route_class,
)


ROUTE_LEG_WARNING_SECONDS = 90 * 60
ROUTE_LEG_HIGH_WARNING_SECONDS = 150 * 60
ROUTE_DAY_WARNING_SECONDS = 180 * 60
ROUTE_DAY_LOAD_WARNING_SECONDS = 10 * 60 * 60
MOBILITY_LEG_WARNING_METERS = 800
MOBILITY_DAY_WARNING_METERS = 4000


def _risk(
    risk_id: str,
    risk_type: str,
    severity: str,
    title: str,
    message: str,
    *,
    day_index: Optional[int] = None,
    poi_names: Optional[list[str]] = None,
    evidence: Optional[dict] = None,
    suggestion: str = "",
    revisable: bool = True,
) -> RiskItem:
    return RiskItem(
        id=risk_id,
        type=risk_type,
        severity=severity,
        day_index=day_index,
        related_poi_names=poi_names or [],
        title=title,
        message=message,
        evidence=evidence or {},
        suggestion=suggestion,
        revisable=revisable,
    )


def _is_verified_google_poi(attraction: Attraction) -> bool:
    return bool(
        attraction.place_id
        and attraction.poi_match_status == "verified"
        and attraction.map_data_source == "google_places"
        and has_valid_verified_coordinates(attraction.location)
    )


def _route_mode(transportation: str) -> str:
    text = (transportation or "").casefold()
    if any(token in text for token in ("步行", "walking", "walk")):
        return "walking"
    if any(token in text for token in ("自驾", "驾车", "出租", "打车", "driving", "taxi", "car")):
        return "driving"
    return "transit"


class TripValidatorService:
    """Validate one enriched TripPlan against explicit Phase 1 preferences."""

    async def validate(self, request: TripRequest, plan: TripPlan) -> ValidationResult:
        risks: list[RiskItem] = []
        unavailable: list[str] = []
        checked = ["earliest_start", "budget", "route_feasibility", "mobility", "pacing_daily_load"]
        daily_load_assessments: list[dict] = []
        route_api_calls = 0
        profile = request.preference_profile

        # Explicit earliest-start time is a hard constraint.
        if profile and profile.constraints.earliest_start_time:
            earliest = profile.constraints.earliest_start_time
            for day_position, day in enumerate(plan.days):
                if not day.start_time:
                    unavailable.append(f"earliest_start:day:{day_position}")
                    risks.append(_risk(
                        f"earliest_start:missing:{day_position}",
                        "earliest_start",
                        "warning",
                        "无法确认当天开始时间",
                        f"第 {day_position + 1} 天未提供第一个主要活动的预计开始时间，无法确认是否满足 {earliest} 的最早开始约束。",
                        day_index=day_position,
                        evidence={"constraint": earliest, "planned_start": None},
                        suggestion="请补充当天第一个主要活动的预计开始时间。",
                    ))
                elif day.start_time < earliest:
                    risks.append(_risk(
                        f"earliest_start:violation:{day_position}",
                        "earliest_start",
                        "blocking",
                        "行程开始时间早于你的明确要求",
                        f"第 {day_position + 1} 天第一个主要活动预计 {day.start_time} 开始，早于你确认的 {earliest}。",
                        day_index=day_position,
                        evidence={"constraint": earliest, "planned_start": day.start_time},
                        suggestion=f"将当天第一个主要活动调整到 {earliest} 或之后。",
                    ))
        elif profile and profile.constraints.avoid_early_start:
            risks.append(_risk(
                "earliest_start:needs_confirmation",
                "earliest_start",
                "info",
                "最早开始时间尚未确认",
                "你选择了不想早起，但还没有设置具体的最早开始时间，因此本次无法进行硬约束检查。",
                evidence={"avoid_early_start": True, "earliest_start_time": None},
                suggestion="返回偏好设置确认具体最早开始时间。",
                revisable=False,
            ))

        # Budget is an estimate; only the user's explicit cap is a hard constraint.
        if plan.budget is None:
            unavailable.append("budget")
            risks.append(_risk(
                "budget:missing",
                "budget",
                "warning",
                "缺少计划预算估算",
                "当前计划没有预算估算，无法检查是否满足你的预算上限。",
                evidence={"budget_scope": "destination_local_spend_excluding_round_trip"},
                suggestion="补充住宿、餐饮、景点和当地交通的计划估算。",
            ))
        else:
            budget = plan.budget
            component_sum = (
                budget.total_attractions
                + budget.total_hotels
                + budget.total_meals
                + budget.total_transportation
                + budget.total_inter_city_transport
            )
            if budget.total != component_sum:
                risks.append(_risk(
                    "budget:sum_mismatch",
                    "budget",
                    "warning",
                    "计划预算估算加总不一致",
                    f"预算分项合计为 ¥{component_sum}，但计划估算总额为 ¥{budget.total}。",
                    evidence={"plan_total_cny": budget.total, "component_sum_cny": component_sum},
                    suggestion="核对预算分项并统一计划估算总额。",
                ))
            if profile and profile.budget_cny is not None and budget.total > profile.budget_cny:
                risks.append(_risk(
                    "budget:over_limit",
                    "budget",
                    "blocking",
                    "计划预算估算超过明确上限",
                    f"目的地旅行期间的计划估算为 ¥{budget.total}，超过你的预算上限 ¥{profile.budget_cny}；两者均不包含往返目的地的大交通。",
                    evidence={
                        "budget_limit_cny": profile.budget_cny,
                        "plan_total_cny": budget.total,
                        "over_by_cny": budget.total - profile.budget_cny,
                        "budget_scope": "destination_local_spend_excluding_round_trip",
                    },
                    suggestion="降低住宿、餐饮或收费景点的计划估算。",
                ))

        mobility_enabled = bool(profile and profile.constraints.mobility_notes)
        google_service = None
        try:
            from .google_map_service import get_google_map_service
            google_service = get_google_map_service()
        except Exception:
            google_service = None

        route_cache: Dict[Tuple[str, str, str], dict] = {}

        async def fetch_route(origin: Attraction, destination: Attraction, city: str, mode: str) -> dict:
            nonlocal route_api_calls
            key = (origin.place_id or origin.name, destination.place_id or destination.name, mode)
            if key in route_cache:
                return route_cache[key]
            route_api_calls += 1
            result = await asyncio.to_thread(
                google_service.plan_route,
                origin.address,
                destination.address,
                city,
                city,
                mode,
            )
            route_cache[key] = result or {}
            return route_cache[key]

        for day_position, day in enumerate(plan.days):
            day_mode = _route_mode(day.transportation or request.transportation)
            day_route_seconds = 0
            day_walking_meters = 0
            visit_seconds = sum(max(0, attraction.visit_duration) * 60 for attraction in day.attractions)
            day_has_unknown_leg = False
            pacing_route_legs: list[RouteLegEvidence] = []
            pacing_route_class = infer_route_class(day)

            for index in range(len(day.attractions) - 1):
                origin = day.attractions[index]
                destination = day.attractions[index + 1]
                poi_names = [origin.name, destination.name]
                observation = {
                    "day_index": day_position,
                    "origin_stable_id": origin.place_id or origin.name,
                    "destination_stable_id": destination.place_id or destination.name,
                    "provider": "google_directions",
                    "route_mode": day_mode,
                    "request_attempted": False,
                    "data_available": False,
                    "reason": None,
                }
                if not (_is_verified_google_poi(origin) and _is_verified_google_poi(destination)):
                    day_has_unknown_leg = True
                    pacing_route_legs.append(RouteLegEvidence(
                        state="unknown", route_class=pacing_route_class,
                        source="poi_grounding", reason="invalid_or_unverified_poi",
                    ))
                    observation["reason"] = "invalid_or_unverified_poi"
                    from .planner_observation import observe_route
                    observe_route(observation)
                    continue
                if google_service is None:
                    day_has_unknown_leg = True
                    pacing_route_legs.append(RouteLegEvidence(
                        state="unavailable", route_class=pacing_route_class,
                        source="directions_provider", reason="directions_provider_unavailable",
                    ))
                    observation["reason"] = "directions_provider_unavailable"
                    from .planner_observation import observe_route
                    observe_route(observation)
                    continue

                try:
                    observation["request_attempted"] = True
                    route = await fetch_route(origin, destination, day.city or plan.city, day_mode)
                except Exception:
                    route = {}
                if not route or route.get("data_source") != "google_directions":
                    day_has_unknown_leg = True
                    pacing_route_legs.append(RouteLegEvidence(
                        state="unavailable", route_class=pacing_route_class,
                        source="google_directions", reason="route_unavailable",
                    ))
                    observation["reason"] = "route_unavailable"
                    from .planner_observation import observe_route
                    observe_route(observation)
                    continue

                duration = int(route.get("duration") or 0)
                distance = int(route.get("distance") or 0)
                observation.update({
                    "data_available": True, "duration_s": duration,
                    "distance_m": distance,
                    "feasible": duration <= ROUTE_LEG_WARNING_SECONDS,
                })
                from .planner_observation import observe_route
                observe_route(observation)
                day_route_seconds += duration
                pacing_route_legs.append(RouteLegEvidence(
                    state="infeasible" if duration > ROUTE_LEG_WARNING_SECONDS else "verified",
                    duration_minutes=round(duration / 60), route_class=pacing_route_class,
                    source="google_directions",
                ))
                if duration > ROUTE_LEG_WARNING_SECONDS:
                    level = "明显过长" if duration > ROUTE_LEG_HIGH_WARNING_SECONDS else "较长"
                    risks.append(_risk(
                        f"route_feasibility:leg:{day_position}:{index}",
                        "route_feasibility",
                        "warning",
                        f"相邻景点交通时间{level}",
                        f"{origin.name} 到 {destination.name} 的地图路线预计约 {round(duration / 60)} 分钟。",
                        day_index=day_position,
                        poi_names=poi_names,
                        evidence={
                            "distance_m": distance,
                            "duration_s": duration,
                            "route_type": day_mode,
                            "data_source": "google_directions",
                        },
                        suggestion="考虑调整同日景点分组或改变交通方式。",
                    ))

                if mobility_enabled:
                    if day_mode == "walking":
                        walking_route = route
                    else:
                        walking_observation = {
                            "day_index": day_position,
                            "origin_stable_id": origin.place_id or origin.name,
                            "destination_stable_id": destination.place_id or destination.name,
                            "provider": "google_directions", "route_mode": "walking",
                            "request_attempted": True, "data_available": False,
                            "reason": "route_unavailable",
                        }
                        try:
                            walking_route = await fetch_route(
                                origin, destination, day.city or plan.city, "walking"
                            )
                        except Exception:
                            walking_route = {}
                        if walking_route and walking_route.get("data_source") == "google_directions":
                            walking_duration = int(walking_route.get("duration") or 0)
                            walking_observation.update({
                                "data_available": True,
                                "duration_s": walking_duration,
                                "distance_m": int(walking_route.get("distance") or 0),
                                "feasible": walking_duration <= ROUTE_LEG_WARNING_SECONDS,
                                "reason": None,
                            })
                        from .planner_observation import observe_route
                        observe_route(walking_observation)
                    if walking_route and walking_route.get("data_source") == "google_directions":
                        walking_distance = int(walking_route.get("distance") or 0)
                        day_walking_meters += walking_distance
                        if walking_distance > MOBILITY_LEG_WARNING_METERS:
                            risks.append(_risk(
                                f"mobility:leg:{day_position}:{index}",
                                "mobility",
                                "warning",
                                "相邻景点步行距离可能偏长",
                                f"考虑到你的行动需求，{origin.name} 到 {destination.name} 的地图步行路线约 {walking_distance / 1000:.1f} 公里。",
                                day_index=day_position,
                                poi_names=poi_names,
                                evidence={
                                    "walking_distance_m": walking_distance,
                                    "threshold_m": MOBILITY_LEG_WARNING_METERS,
                                    "data_source": "google_directions",
                                    "policy": "conservative_mobility_heuristic",
                                },
                                suggestion="优先乘坐公共交通或出租车，并减少连续步行。",
                            ))
                    else:
                        day_has_unknown_leg = True

            if day_route_seconds > ROUTE_DAY_WARNING_SECONDS:
                risks.append(_risk(
                    f"route_feasibility:day_total:{day_position}",
                    "route_feasibility",
                    "warning",
                    "当天景点间交通时间偏长",
                    f"第 {day_position + 1} 天已验证路线的累计交通时间约 {round(day_route_seconds / 60)} 分钟。",
                    day_index=day_position,
                    evidence={"verified_route_duration_s": day_route_seconds, "threshold_s": ROUTE_DAY_WARNING_SECONDS},
                    suggestion="减少跨区域往返或把部分景点调整到其他日期。",
                ))
            if day_route_seconds + visit_seconds > ROUTE_DAY_LOAD_WARNING_SECONDS:
                risks.append(_risk(
                    f"route_feasibility:day_load:{day_position}",
                    "route_feasibility",
                    "warning",
                    "当天主要活动和交通负荷偏高",
                    f"第 {day_position + 1} 天景点游览时长与已验证交通时间合计超过 10 小时。",
                    day_index=day_position,
                    evidence={
                        "visit_duration_s": visit_seconds,
                        "verified_route_duration_s": day_route_seconds,
                        "threshold_s": ROUTE_DAY_LOAD_WARNING_SECONDS,
                    },
                    suggestion="减少一个景点或缩短跨区交通。",
                ))
            if mobility_enabled and day_walking_meters > MOBILITY_DAY_WARNING_METERS:
                risks.append(_risk(
                    f"mobility:day_total:{day_position}",
                    "mobility",
                    "warning",
                    "当天累计步行距离可能偏长",
                    f"第 {day_position + 1} 天已验证的景点间步行路线累计约 {day_walking_meters / 1000:.1f} 公里。",
                    day_index=day_position,
                    evidence={
                        "verified_walking_distance_m": day_walking_meters,
                        "threshold_m": MOBILITY_DAY_WARNING_METERS,
                        "policy": "conservative_mobility_heuristic",
                    },
                    suggestion="增加短途交通并预留休息时间。",
                ))
            if day_has_unknown_leg:
                unavailable.append(f"route:day:{day_position}")
                risks.append(_risk(
                    f"validation_unavailable:route:{day_position}",
                    "validation_unavailable",
                    "info",
                    "当天路线未能完整验证",
                    f"第 {day_position + 1} 天部分景点缺少 verified Google POI 或地图路线数据，因此不能判断整天路线是否合理。",
                    day_index=day_position,
                    evidence={"required_source": "verified_google_places_and_google_directions"},
                    suggestion="确认景点地图匹配后再次检查路线。",
                    revisable=False,
                ))

            if day.is_transfer_day:
                pacing_route_legs.append(RouteLegEvidence(
                    state="unknown", route_class="inter_city", source="DayPlan.is_transfer_day",
                    reason="structured_inter_city_duration_unavailable",
                ))
            requested_pace = profile.pace if profile else "balanced"
            assessment = calculate_daily_load(
                day, requested_pace, pacing_route_legs, day_position=day_position
            )
            assessment_payload = assessment.to_dict()
            daily_load_assessments.append(assessment_payload)
            if assessment.overload_status in {"warning", "revisable_overload"}:
                load = assessment.breakdown
                if load.effective_load_minutes is None:
                    detail = "城际移动缺少可靠结构化时长，因此不能形成精确总分钟数"
                else:
                    detail = (
                        f"{load.raw_attraction_minutes} 分钟景点活动，加上 verified/estimated 移动、"
                        f"用餐、出入口和恢复缓冲后为 {load.effective_load_minutes} 分钟，"
                        f"可用窗口为 {load.available_day_window_minutes} 分钟"
                    )
                is_overload = assessment.overload_status == "revisable_overload"
                execution_supported = is_overload and assessment.confidence in {"HIGH", "MEDIUM"}
                risks.append(_risk(
                    f"pacing_daily_load:day:{day_position}", "pacing", "warning",
                    "当天节奏负荷超过建议范围" if is_overload else "当天节奏负荷接近建议上限",
                    f"第 {day_position + 1} 天：{detail}。这是 {requested_pace} 的 proposed pacing policy 判断。",
                    day_index=day_position,
                    poi_names=[item.name for item in day.attractions],
                    evidence={
                        **assessment_payload,
                        "rule_id": "pacing_daily_load",
                        "revision_execution_supported": execution_supported,
                        "revision_boundary": ("phase4c_affected_day_only"
                                              if execution_supported else
                                              "confidence_or_status_not_auto_revisable"),
                    },
                    suggestion=("仅调整该日的可选活动；保护必去景点、城市/住宿/预算和显式约束。"
                                if is_overload else "确认移动时间并为用餐和休息保留余量。"),
                    revisable=is_overload,
                ))

        if unavailable:
            status = "degraded"
        elif risks:
            status = "issues_found"
        else:
            status = "passed"
        from .planner_observation import current_validation_scope
        scope = current_validation_scope()
        return ValidationResult(
            status=status,
            risks=risks,
            checked_rules=checked,
            unavailable_checks=list(dict.fromkeys(unavailable)),
            route_api_calls=route_api_calls,
            pacing_policy_version=PACING_POLICY_VERSION,
            daily_load_assessments=daily_load_assessments,
            validation_pass_scope=scope.get("validation_pass_id"),
        )


_trip_validator_service: Optional[TripValidatorService] = None


def get_trip_validator_service() -> TripValidatorService:
    global _trip_validator_service
    if _trip_validator_service is None:
        _trip_validator_service = TripValidatorService()
    return _trip_validator_service
