"""Pure deterministic Phase 3B metric calculators."""

from datetime import date, timedelta
from typing import Dict, Optional

from pydantic import ValidationError

from ..models.schemas import TripPlan, has_valid_verified_coordinates
from .models import ArtifactEvaluationInput, EvalCase, MetricResult


POLICY = "metrics.v2"
ACTIONABLE = {"earliest_start", "budget", "mobility", "route_feasibility"}


def known(name, value, numerator=None, denominator=None, reason=None):
    return MetricResult(metric=name, status="known", value=float(value), numerator=numerator,
                        denominator=denominator, reason=reason, policy_version=POLICY)


def unknown(name, reason):
    return MetricResult(metric=name, status="unknown", reason=reason, policy_version=POLICY)


def na(name, reason):
    return MetricResult(metric=name, status="not_applicable", reason=reason, policy_version=POLICY)


def failed(name, reason="metric_integrity_error", numerator=None, denominator=None):
    return MetricResult(metric=name, status="failed", reason=reason, numerator=numerator,
                        denominator=denominator, policy_version=POLICY)


def rate(name, numerator, denominator, *, zero_reason):
    if denominator == 0:
        return unknown(name, zero_reason)
    if numerator < 0 or numerator > denominator:
        return failed(name, "metric_integrity_error", numerator, denominator)
    return known(name, numerator / denominator, numerator, denominator)


def _date_consistent(case: EvalCase, plan: TripPlan) -> bool:
    request = case.trip_request
    if plan.start_date != request.start_date or plan.end_date != request.end_date:
        return False
    if len(plan.days) != request.travel_days:
        return False
    expected = date.fromisoformat(request.start_date)
    city_counts: Dict[str, int] = {}
    for position, day in enumerate(plan.days):
        if day.day_index != position or date.fromisoformat(day.date) != expected + timedelta(days=position):
            return False
        city_counts[day.city or plan.city] = city_counts.get(day.city or plan.city, 0) + 1
    return all(city_counts.get(stay.city, 0) == stay.days for stay in request.cities)


def classify_plan_route_legs(plan: TripPlan) -> list[dict]:
    """Evaluation-only route taxonomy; transfers never enter POI route metrics v1/v2."""
    legs = []
    for day_position, day in enumerate(plan.days):
        for left, right in zip(day.attractions, day.attractions[1:]):
            legs.append({"leg_type": "intra_city_poi_leg", "day_index": day_position,
                         "origin": left.name, "destination": right.name})
        if day_position and (plan.days[day_position - 1].city or plan.city) != (day.city or plan.city):
            legs.append({"leg_type": "inter_city_transfer", "day_index": day_position,
                         "origin": plan.days[day_position - 1].city or plan.city,
                         "destination": day.city or plan.city})
    return legs


def calculate_metrics(case: EvalCase, artifact: ArtifactEvaluationInput) -> list[MetricResult]:
    original = artifact.model_dump(mode="json")
    try:
        plan = TripPlan.model_validate(artifact.output)
        schema = known("schema_valid", 1)
    except ValidationError:
        plan = None
        schema = known("schema_valid", 0)

    results = [schema]
    if plan is None:
        results.extend([
            known("date_day_consistency", 0), unknown("explicit_constraint_satisfaction_rate", "output is not a valid TripPlan"),
            unknown("earliest_start_satisfaction", "output is not a valid TripPlan"), unknown("budget_arithmetic_consistency", "output is not a valid TripPlan"),
            unknown("budget_limit_satisfaction", "output is not a valid TripPlan"), unknown("grounded_poi_rate", "output is not a valid TripPlan"),
            unknown("unverified_poi_rate", "output is not a valid TripPlan"), unknown("provenance_coverage", "output is not a valid TripPlan"),
            unknown("route_check_coverage", "output is not a valid TripPlan"), unknown("route_feasibility_rate", "output is not a valid TripPlan"),
            unknown("actionable_risk_count", "output is not a valid TripPlan"), unknown("revision_risk_resolution_rate", "output is not a valid TripPlan"),
            unknown("unaffected_day_preservation_rate", "output is not a valid TripPlan"),
        ])
    else:
        results.append(known("date_day_consistency", int(_date_consistent(case, plan))))
        profile = case.trip_request.preference_profile
        measurable = []
        earliest = profile.constraints.earliest_start_time if profile else None
        if earliest:
            starts = [day.start_time for day in plan.days]
            if any(value is None for value in starts):
                earliest_metric = unknown("earliest_start_satisfaction", "one or more applicable days have no start_time")
                measurable.append(None)
            else:
                count = sum(value >= earliest for value in starts if value is not None)
                earliest_metric = rate("earliest_start_satisfaction", count, len(starts), zero_reason="no applicable days")
                measurable.append(count == len(starts))
        else:
            earliest_metric = na("earliest_start_satisfaction", "no explicit earliest_start_time")
        results.append(earliest_metric)

        budget = plan.budget
        if budget is None:
            arithmetic = unknown("budget_arithmetic_consistency", "plan has no budget")
        else:
            component_sum = budget.total_attractions + budget.total_hotels + budget.total_meals + budget.total_transportation + budget.total_inter_city_transport
            arithmetic = known("budget_arithmetic_consistency", int(component_sum == budget.total))
        results.append(arithmetic)
        cap = profile.budget_cny if profile else None
        if cap is None:
            limit = na("budget_limit_satisfaction", "no explicit budget limit")
        elif budget is None:
            limit = unknown("budget_limit_satisfaction", "explicit budget limit exists but plan has no budget")
            measurable.append(None)
        else:
            satisfied = budget.total <= cap
            limit = known("budget_limit_satisfaction", int(satisfied))
            measurable.append(satisfied)
        results.append(limit)
        if not measurable:
            results.append(na("explicit_constraint_satisfaction_rate", "no deterministically measurable explicit constraints"))
        elif any(value is None for value in measurable):
            results.append(unknown("explicit_constraint_satisfaction_rate", "one or more measurable explicit constraints are unknown"))
        else:
            count = sum(bool(value) for value in measurable)
            results.append(rate("explicit_constraint_satisfaction_rate", count, len(measurable), zero_reason="no measurable constraints"))

        pois = [poi for day in plan.days for poi in day.attractions]
        if not pois:
            results.extend([unknown("grounded_poi_rate", "plan has no POIs"), unknown("unverified_poi_rate", "plan has no POIs")])
        else:
            grounded = sum(bool(poi.poi_match_status == "verified" and poi.map_data_source in {"google_places", "amap"} and (poi.place_id or poi.poi_id) and has_valid_verified_coordinates(poi.location)) for poi in pois)
            unverified = sum(poi.poi_match_status == "unverified" for poi in pois)
            results.extend([rate("grounded_poi_rate", grounded, len(pois), zero_reason="plan has no POIs"), rate("unverified_poi_rate", unverified, len(pois), zero_reason="plan has no POIs")])

        provenance_items = []
        provenance_items.extend(poi.poi_match_status == "verified" and poi.map_data_source in {"google_places", "amap"} for poi in pois)
        provenance_items.extend(w.verification_status in {"verified", "partial"} and w.data_source in {"google_weather", "amap"} for w in plan.weather_info)
        provenance_items.extend(item.verification_status in {"verified", "partial"} and bool(item.evidence) for item in plan.xhs_research if item.status != "unavailable")
        if provenance_items:
            supported = sum(provenance_items)
            results.append(rate("provenance_coverage", supported, len(provenance_items), zero_reason="no applicable structured external facts"))
        else:
            results.append(na("provenance_coverage", "no applicable structured external facts"))

        # v2 denominator: adjacent final-plan POIs eligible under the same verified
        # identity rules used by Validator. Inter-city transfers are excluded.
        possible_legs = sum(
            sum(
                bool(
                    left.poi_match_status == right.poi_match_status == "verified"
                    and left.map_data_source in {"google_places", "amap"}
                    and right.map_data_source in {"google_places", "amap"}
                    and (left.place_id or left.poi_id) and (right.place_id or right.poi_id)
                    and has_valid_verified_coordinates(left.location)
                    and has_valid_verified_coordinates(right.location)
                )
                for left, right in zip(day.attractions, day.attractions[1:])
            ) for day in plan.days
        )
        checks = artifact.route_checks
        if possible_legs == 0:
            results.extend([na("route_check_coverage", "no eligible intra-city verified POI legs"), unknown("route_feasibility_rate", "no checked route legs")])
        elif checks is None:
            results.extend([unknown("route_check_coverage", "route check metadata absent"), unknown("route_feasibility_rate", "route check metadata absent")])
        else:
            checked = [item for item in checks if item.status == "checked" and item.leg_type == "intra_city_poi_leg"]
            results.append(rate("route_check_coverage", len(checked), possible_legs, zero_reason="no eligible intra-city verified POI legs"))
            if not checked:
                results.append(unknown("route_feasibility_rate", "no checked route legs"))
            else:
                feasible = sum(item.feasible is True for item in checked)
                results.append(rate("route_feasibility_rate", feasible, len(checked), zero_reason="no checked route legs"))

        if plan.validation_status is None:
            results.append(unknown("actionable_risk_count", "validator result absent"))
        else:
            count = sum(r.revisable and r.type in ACTIONABLE and r.severity in {"warning", "blocking"} for r in plan.risks)
            results.append(known("actionable_risk_count", count))

        targets = artifact.revision_target_risk_ids
        if plan.revision_count == 0 and not targets:
            results.append(na("revision_risk_resolution_rate", "no revision and no targeted risks"))
        elif (not targets or artifact.revision_before is None or artifact.revision_after is None
              or artifact.revision_revalidation_result is None):
            results.append(unknown("revision_risk_resolution_rate", "revision pre-state or target risk IDs absent"))
        else:
            after_ids = {risk.id for risk in artifact.revision_after.risks}
            resolved = sum(target not in after_ids for target in targets)
            results.append(rate("revision_risk_resolution_rate", resolved, len(targets), zero_reason="no targeted risks"))

        protected = case.expected_constraints.protected_day_indices
        if not protected:
            results.append(na("unaffected_day_preservation_rate", "case has no protected days"))
        elif artifact.patch_before is None or artifact.patch_after is None:
            results.append(unknown("unaffected_day_preservation_rate", "patch before/after artifacts absent"))
        else:
            valid = [index for index in protected if index < len(artifact.patch_before.days) and index < len(artifact.patch_after.days)]
            if len(valid) != len(protected):
                results.append(unknown("unaffected_day_preservation_rate", "protected day index absent from artifact"))
            else:
                same = sum(artifact.patch_before.days[i].model_dump(mode="json") == artifact.patch_after.days[i].model_dump(mode="json") for i in valid)
                results.append(rate("unaffected_day_preservation_rate", same, len(valid), zero_reason="no protected days"))

    usage = artifact.usage
    for name in ("logical_llm_calls", "prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, name) if usage else None
        results.append(known(name, value) if value is not None else unknown(name, "usage telemetry absent"))
    results.append(known("latency_ms", artifact.latency_ms) if artifact.latency_ms is not None else unknown("latency_ms", "latency telemetry absent"))
    assert artifact.model_dump(mode="json") == original, "metric calculation mutated input"
    order = {name: index for index, name in enumerate([
        "schema_valid", "date_day_consistency", "explicit_constraint_satisfaction_rate", "earliest_start_satisfaction", "budget_arithmetic_consistency", "budget_limit_satisfaction", "grounded_poi_rate", "unverified_poi_rate", "provenance_coverage", "route_check_coverage", "route_feasibility_rate", "actionable_risk_count", "revision_risk_resolution_rate", "unaffected_day_preservation_rate", "logical_llm_calls", "prompt_tokens", "completion_tokens", "total_tokens", "latency_ms"
    ])}
    for item in results:
        if item.status == "known" and item.metric not in {
            "actionable_risk_count", "logical_llm_calls", "prompt_tokens",
            "completion_tokens", "total_tokens", "latency_ms",
        } and not 0 <= item.value <= 1:
            item.status = "failed"; item.reason = "metric_integrity_error"; item.value = None
    return sorted(results, key=lambda item: order[item.metric])
