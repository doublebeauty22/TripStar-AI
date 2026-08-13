"""Phase 2B deterministic trigger, critic, and single targeted revision."""

import asyncio
import json
import re
from typing import Any, Optional

from ..models.schemas import CriticResult, PreferenceProfile, RiskItem, TripPlan, TripRequest
from .llm_service import create_chat_completion, get_llm


ACTIONABLE_RISK_TYPES = {"earliest_start", "budget", "mobility", "route_feasibility"}
_FORBIDDEN_DETERMINISTIC_FIELDS = {
    "place_id", "poi_id", "poi_match_status", "map_data_source", "location",
    "risks", "validation_status", "revision_count", "revision_summary",
    "pacing_policy_version", "daily_load_assessments",
}


def filter_actionable_risks(risks: list[RiskItem]) -> list[RiskItem]:
    """The LLM never controls whether the critic is invoked."""
    return [
        risk for risk in risks
        if risk.revisable
        and risk.type in ACTIONABLE_RISK_TYPES
        and risk.severity in {"warning", "blocking"}
    ]


def _json_object(text: str) -> dict[str, Any]:
    content = (text or "").strip()
    if "```" in content:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.I)
        if match:
            content = match.group(1).strip()
    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        raise ValueError("LLM response did not contain a JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("LLM response JSON must be an object")
    return value


def _strip_deterministic_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_deterministic_fields(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_deterministic_fields(item)
            for key, item in value.items()
            if key not in _FORBIDDEN_DETERMINISTIC_FIELDS
        }
    return value


def _prepare_revision_plan(value: dict[str, Any]) -> dict[str, Any]:
    """Strip LLM map facts and add a non-factual schema placeholder for enrichment."""
    cleaned = _strip_deterministic_fields(value)
    for day in cleaned.get("days", []):
        for attraction in day.get("attractions", []):
            # Attraction.location is legacy-required. This sentinel is never
            # treated as verified and is replaced only by fresh POI enrichment.
            attraction["location"] = {"longitude": 0.0, "latitude": 0.0}
    return cleaned


def _profile_payload(profile: Optional[PreferenceProfile]) -> dict[str, Any]:
    return profile.model_dump(mode="json") if profile else {}


def _plan_summary(plan: TripPlan) -> dict[str, Any]:
    return {
        "city": plan.city,
        "cities": plan.cities,
        "start_date": plan.start_date,
        "end_date": plan.end_date,
        "days": [{
            "day_index": day.day_index,
            "date": day.date,
            "city": day.city,
            "start_time": day.start_time,
            "attractions": [item.name for item in day.attractions],
            "meals": [item.name for item in day.meals],
            "hotel": day.hotel.name if day.hotel else None,
        } for day in plan.days],
        "budget": plan.budget.model_dump(mode="json") if plan.budget else None,
    }


def _protected_constraints(request: TripRequest, plan: TripPlan) -> list[str]:
    profile = request.preference_profile
    protected = [
        f"cities: {', '.join(plan.cities or [plan.city])}",
        f"dates: {plan.start_date} to {plan.end_date}",
        f"day_count: {len(plan.days)}",
    ]
    if profile:
        protected.extend([
            f"party_type: {profile.party_type}",
            f"explicit_interests: {', '.join(profile.interests)}",
        ])
        if profile.constraints.earliest_start_time:
            protected.append(f"earliest_start_time: {profile.constraints.earliest_start_time}")
        if profile.budget_cny is not None:
            protected.append(f"budget_cny: {profile.budget_cny}")
        if profile.special_requirements:
            protected.append("mandatory_user_requirements")
    protected.extend(["unaffected_days", "reasonable_existing_POIs_meals_and_hotels"])
    return protected


class TripRevisionService:
    """Runs at most one critic and one revision, always failing open."""

    def __init__(self, llm: Any = None):
        self.llm = llm

    def _completion(self, stage: str, messages: list[dict[str, str]], max_tokens: int) -> str:
        llm = self.llm or get_llm()
        response = create_chat_completion(
            stage=stage,
            model=llm.model,
            messages=messages,
            llm_instance=llm,
            temperature=0.0,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    async def run_critic(
        self, request: TripRequest, plan: TripPlan, risks: list[RiskItem]
    ) -> CriticResult:
        payload = {
            "preference_profile": _profile_payload(request.preference_profile),
            "trip_plan_summary": _plan_summary(plan),
            "actionable_risks": [risk.model_dump(mode="json") for risk in risks],
            "protected_user_constraints": _protected_constraints(request, plan),
            "revision_count": 0,
        }
        prompt = (
            "You are a trip-plan critic. Consume only the supplied deterministic risks; "
            "do not recalculate or invent distance, duration, price, coordinates, place IDs, "
            "or map facts. Recommend a minimal targeted repair, protect unaffected content, "
            "and return JSON only with should_revise, revision_instructions, "
            "protected_elements, summary, target_risk_ids.\nINPUT:\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        text = await asyncio.to_thread(
            self._completion, "critic", [{"role": "user", "content": prompt}], 1000
        )
        result = CriticResult.model_validate(_json_object(text))
        defaults = _protected_constraints(request, plan)
        result.protected_elements = list(dict.fromkeys(defaults + result.protected_elements))
        return result

    async def run_revision(
        self,
        request: TripRequest,
        plan: TripPlan,
        risks: list[RiskItem],
        critic: CriticResult,
        research_context: dict[str, Any],
    ) -> TripPlan:
        original = plan.model_dump(mode="json", exclude={"risks", "validation_status", "revision_count", "revision_summary"})
        payload = {
            "original_trip_plan": original,
            "preference_profile": _profile_payload(request.preference_profile),
            "actionable_risks": [risk.model_dump(mode="json") for risk in risks],
            "critic_result": critic.model_dump(mode="json"),
            "compact_original_research_context": research_context,
            "revision_count": 0,
        }
        prompt = (
            "Return one COMPLETE TripPlan JSON, not a patch. Make only the critic-requested "
            "minimal repair; preserve protected elements and unaffected days. Keep cities, dates, "
            "and day count. Budget component sum must equal budget.total. Never output or invent "
            "place_id, poi_id, poi_match_status, map_data_source, location/coordinates, route "
            "distance/duration, validation_status, risks, revision_count, or revision_summary. "
            "Do not delete core user preferences merely to remove a warning. JSON only.\nINPUT:\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        text = await asyncio.to_thread(
            self._completion, "revision", [{"role": "user", "content": prompt}], 6000
        )
        revised = TripPlan.model_validate(_prepare_revision_plan(_json_object(text)))
        if (revised.cities or [revised.city]) != (plan.cities or [plan.city]):
            raise ValueError("revision changed protected cities")
        if revised.start_date != plan.start_date or revised.end_date != plan.end_date:
            raise ValueError("revision changed protected dates")
        if len(revised.days) != len(plan.days):
            raise ValueError("revision changed protected day count")
        if revised.budget:
            components = (
                revised.budget.total_attractions + revised.budget.total_hotels
                + revised.budget.total_meals + revised.budget.total_transportation
                + revised.budget.total_inter_city_transport
            )
            if components != revised.budget.total:
                raise ValueError("revision budget arithmetic is inconsistent")
        revised.revision_count = 1
        revised.revision_summary = critic.summary[:240] or None
        return revised


_trip_revision_service: Optional[TripRevisionService] = None


def get_trip_revision_service() -> TripRevisionService:
    global _trip_revision_service
    if _trip_revision_service is None:
        _trip_revision_service = TripRevisionService()
    return _trip_revision_service
