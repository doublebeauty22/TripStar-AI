"""Phase 4C affected-day-only pacing revision with deterministic commit gates."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Awaitable, Callable, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from ..models.schemas import RiskItem, TripPlan, TripRequest, has_valid_verified_coordinates
from .llm_service import create_chat_completion, get_llm
from .pacing_policy import PACING_POLICY_VERSION


PACING_REVISION_VERSION = "pacing_revision.v1"
PacingRevisionFailure = Literal[
    "protected_day_drift", "target_risk_unresolved", "invalid_revision_output",
    "enrichment_failure", "constraint_regression", "budget_regression",
    "grounding_regression", "retained_poi_grounding_regression",
    "new_or_changed_poi_unverified", "pacing_revision_unsupported",
]


class StrictRevisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RemoveOptionalPOI(StrictRevisionModel):
    operation: Literal["remove_optional_poi"]
    day_index: int = Field(..., ge=0)
    target_id: str = Field(..., min_length=1)
    target_name: str = Field(..., min_length=1)
    reason: str = Field(default="", max_length=300)


class ReduceOptionalDuration(StrictRevisionModel):
    operation: Literal["reduce_optional_duration"]
    day_index: int = Field(..., ge=0)
    target_id: str = Field(..., min_length=1)
    target_name: str = Field(..., min_length=1)
    old_minutes: int = Field(..., ge=15, le=600)
    new_minutes: int = Field(..., ge=15, le=600)
    reason: str = Field(default="", max_length=300)


class DelayStartTime(StrictRevisionModel):
    operation: Literal["delay_start_time"]
    day_index: int = Field(..., ge=0)
    old_value: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    new_value: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    reason: str = Field(default="", max_length=300)


PacingOperation = Annotated[
    Union[RemoveOptionalPOI, ReduceOptionalDuration, DelayStartTime],
    Field(discriminator="operation"),
]


class PacingRevisionProposal(StrictRevisionModel):
    revision_version: Literal["pacing_revision.v1"] = PACING_REVISION_VERSION
    target_risk_ids: list[str] = Field(..., min_length=1)
    affected_day_indices: list[int] = Field(..., min_length=1)
    protected_day_indices: list[int] = Field(default_factory=list)
    operations: list[PacingOperation] = Field(..., min_length=1, max_length=8)
    summary: str = Field(default="", max_length=300)


@dataclass
class PacingRevisionOutcome:
    status: Literal["success", "unresolved", "rejected", "unsupported"]
    committed_plan: TripPlan
    candidate_plan: Optional[TripPlan]
    target_risk_ids: list[str]
    affected_day_indices: list[int]
    protected_day_indices: list[int]
    proposed_modifications: list[dict[str, Any]] = field(default_factory=list)
    protected_day_equality: dict[int, bool] = field(default_factory=dict)
    post_validation: Optional[dict[str, Any]] = None
    post_pacing_risk_ids: list[str] = field(default_factory=list)
    resolution_outcome: str = ""
    failure_reason: Optional[PacingRevisionFailure] = None
    pacing_policy_version: str = PACING_POLICY_VERSION
    metrics: dict[str, Any] = field(default_factory=dict)
    grounding_outcome: Optional[Literal["valid_grounding_change", "grounding_improvement"]] = None
    grounding_details: dict[str, Any] = field(default_factory=dict)

    def observation(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("committed_plan", None)
        value["candidate_plan"] = self.candidate_plan.model_copy(deep=True) if self.candidate_plan else None
        return value


def _json_object(text: str) -> dict[str, Any]:
    content = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.I)
    if fenced: content = fenced.group(1).strip()
    match = re.search(r"\{[\s\S]*\}", content)
    if not match: raise ValueError("targeted pacing revision did not contain JSON")
    value = json.loads(match.group(0))
    if not isinstance(value, dict): raise ValueError("targeted pacing revision must be an object")
    return value


def select_pacing_revision_risks(risks: list[RiskItem]) -> list[RiskItem]:
    """Only deterministic HIGH/MEDIUM revisable overloads may trigger."""
    return [risk for risk in risks if (
        risk.type == "pacing" and risk.revisable
        and risk.evidence.get("overload_status") == "revisable_overload"
        and risk.evidence.get("confidence") in {"HIGH", "MEDIUM"}
        and risk.evidence.get("revision_execution_supported") is True
    )]


def _stable_id(day_index: int, poi_index: int, poi: Any) -> str:
    return poi.place_id or poi.poi_id or f"day:{day_index}:poi:{poi_index}"


def _budget_consistent(plan: TripPlan) -> bool:
    if plan.budget is None: return True
    budget = plan.budget
    return budget.total == (budget.total_attractions + budget.total_hotels
                            + budget.total_meals + budget.total_transportation
                            + budget.total_inter_city_transport)


def _trusted_grounding(poi: Any) -> bool:
    return bool(
        poi.poi_match_status == "verified"
        and poi.map_data_source in {"google_places", "amap"}
        and (poi.place_id or poi.poi_id)
        and has_valid_verified_coordinates(poi.location)
    )


@dataclass(frozen=True)
class GroundingGateResult:
    accepted: bool
    outcome: Optional[Literal["valid_grounding_change", "grounding_improvement"]] = None
    failure_reason: Optional[PacingRevisionFailure] = None
    details: dict[str, Any] = field(default_factory=dict)


def validate_affected_day_grounding(
    before: TripPlan, after: TripPlan, affected: list[int], proposal: PacingRevisionProposal,
) -> GroundingGateResult:
    """Validate trust per POI after production enrichment, not by aggregate set inclusion."""
    removed = {(op.day_index, op.target_id, op.target_name) for op in proposal.operations
               if isinstance(op, RemoveOptionalPOI)}
    retained_checked = removed_count = new_count = improvements = 0
    for day_index in affected:
        before_day = before.days[day_index]
        after_day = after.days[day_index]
        unmatched = list(after_day.attractions)
        for position, baseline in enumerate(before_day.attractions):
            stable_id = _stable_id(day_index, position, baseline)
            if (day_index, stable_id, baseline.name) in removed:
                removed_count += 1
                if not _trusted_grounding(baseline):
                    improvements += 1
                continue
            match = next(((offset, item) for offset, item in enumerate(unmatched)
                          if item.name == baseline.name), None)
            if match is None and (baseline.place_id or baseline.poi_id):
                baseline_id = baseline.place_id or baseline.poi_id
                match = next(((offset, item) for offset, item in enumerate(unmatched)
                              if (item.place_id or item.poi_id) == baseline_id), None)
            if match is None:
                return GroundingGateResult(False, failure_reason="new_or_changed_poi_unverified",
                    details={"day_index": day_index, "poi_name": baseline.name,
                             "reason": "retained_poi_missing_without_remove_operation"})
            offset, candidate = match
            unmatched.pop(offset)
            retained_checked += 1
            if _trusted_grounding(baseline) and not _trusted_grounding(candidate):
                return GroundingGateResult(False, failure_reason="retained_poi_grounding_regression",
                    details={"day_index": day_index, "poi_name": baseline.name})
            if not _trusted_grounding(baseline) and _trusted_grounding(candidate):
                improvements += 1
        for candidate in unmatched:
            new_count += 1
            if not _trusted_grounding(candidate):
                return GroundingGateResult(False, failure_reason="new_or_changed_poi_unverified",
                    details={"day_index": day_index, "poi_name": candidate.name})
    outcome = "grounding_improvement" if improvements else "valid_grounding_change"
    return GroundingGateResult(True, outcome=outcome, details={
        "retained_checked": retained_checked, "removed_count": removed_count,
        "new_or_changed_count": new_count, "grounding_improvements": improvements,
    })


class PacingRevisionService:
    """Produces typed operations and commits only after deterministic validation."""

    def __init__(self, llm: Any = None): self.llm = llm

    def _completion(self, prompt: str) -> str:
        llm = self.llm or get_llm()
        response = create_chat_completion(
            stage="pacing_revision", model=llm.model,
            messages=[{"role": "user", "content": prompt}], llm_instance=llm,
            temperature=0.0, max_tokens=1400,
        )
        return response.choices[0].message.content or ""

    async def propose(self, request: TripRequest, plan: TripPlan,
                      risks: list[RiskItem]) -> PacingRevisionProposal:
        targets = select_pacing_revision_risks(risks)
        if not targets: raise ValueError("pacing_revision_unsupported")
        affected = sorted({risk.day_index for risk in targets if risk.day_index is not None})
        protected = [index for index in range(len(plan.days)) if index not in affected]
        affected_days = []
        for index in affected:
            day = plan.days[index]
            affected_days.append({
                "day_index": index, "start_time": day.start_time,
                "attractions": [{"stable_id": _stable_id(index, position, poi),
                                 "name": poi.name, "visit_duration": poi.visit_duration,
                                 "grounded": poi.poi_match_status == "verified"}
                                for position, poi in enumerate(day.attractions)],
                "assessment": next(risk.evidence for risk in targets if risk.day_index == index),
            })
        profile = request.preference_profile
        payload = {
            "revision_version": PACING_REVISION_VERSION,
            "target_pacing_risks": [risk.id for risk in targets],
            "affected_days_only": affected_days,
            "protected_day_indices": protected,
            "requested_pace": profile.pace if profile else "balanced",
            "pacing_policy_version": PACING_POLICY_VERSION,
            "explicit_constraints": profile.constraints.model_dump(mode="json") if profile else {},
            "allowed_operations": ["remove_optional_poi", "reduce_optional_duration", "delay_start_time"],
            "forbidden": ["cross_day_move", "hotel_change", "city_change", "budget_limit_change",
                          "protected_day_change", "new_poi", "earlier_start"],
            "schema": PacingRevisionProposal.model_json_schema(),
        }
        prompt = (
            "Return a typed targeted pacing revision JSON only. Modify affected days only. "
            "Use the smallest safe operation; do not remove a stated must-have. A duration "
            "reduction must remain realistic. Never start earlier. Do not invent map facts.\nINPUT:\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        text = await asyncio.to_thread(self._completion, prompt)
        return PacingRevisionProposal.model_validate(_json_object(text))

    def apply(self, before: TripPlan, proposal: PacingRevisionProposal,
              request: TripRequest) -> TripPlan:
        affected = sorted(set(proposal.affected_day_indices))
        expected_protected = [index for index in range(len(before.days)) if index not in affected]
        if proposal.protected_day_indices != expected_protected:
            raise ValueError("protected_day_drift")
        if any(operation.day_index not in affected for operation in proposal.operations):
            raise ValueError("protected_day_drift")
        candidate = before.model_copy(deep=True)
        for operation in proposal.operations:
            day = candidate.days[operation.day_index]
            if isinstance(operation, DelayStartTime):
                if day.start_time != operation.old_value or operation.new_value < (day.start_time or "00:00"):
                    raise ValueError("constraint_regression")
                earliest = (request.preference_profile.constraints.earliest_start_time
                            if request.preference_profile else None)
                if earliest and operation.new_value < earliest: raise ValueError("constraint_regression")
                day.start_time = operation.new_value
                continue
            matches = [(position, poi) for position, poi in enumerate(day.attractions)
                       if _stable_id(operation.day_index, position, poi) == operation.target_id
                       and poi.name == operation.target_name]
            if len(matches) != 1: raise ValueError("invalid_revision_output")
            position, poi = matches[0]
            if isinstance(operation, RemoveOptionalPOI):
                profile = request.preference_profile
                protected_text = " ".join([
                    profile.special_requirements if profile else "",
                    *(profile.constraints.other_notes if profile else []),
                ]).casefold()
                if poi.name.casefold() in protected_text:
                    raise ValueError("constraint_regression")
                day.attractions.pop(position)
            elif isinstance(operation, ReduceOptionalDuration):
                if poi.visit_duration != operation.old_minutes or operation.new_minutes >= operation.old_minutes:
                    raise ValueError("invalid_revision_output")
                poi.visit_duration = operation.new_minutes
        if candidate.budget:
            candidate.budget.total_attractions = sum(
                poi.ticket_price for day in candidate.days for poi in day.attractions
            )
            candidate.budget.total = (
                candidate.budget.total_attractions + candidate.budget.total_hotels
                + candidate.budget.total_meals + candidate.budget.total_transportation
                + candidate.budget.total_inter_city_transport
            )
        candidate.revision_count = 1
        candidate.revision_summary = proposal.summary[:240] or "Targeted pacing revision"
        return TripPlan.model_validate(candidate.model_dump(mode="json"))

    async def execute(
        self, request: TripRequest, before: TripPlan, risks: list[RiskItem],
        proposal: PacingRevisionProposal,
        *, enricher: Callable[[TripPlan, list[int]], Awaitable[TripPlan]],
        validator: Callable[[TripRequest, TripPlan], Awaitable[Any]],
    ) -> PacingRevisionOutcome:
        original = before.model_copy(deep=True)
        targets = select_pacing_revision_risks(risks)
        target_ids = [risk.id for risk in targets]
        affected = sorted({risk.day_index for risk in targets if risk.day_index is not None})
        protected = [index for index in range(len(before.days)) if index not in affected]
        base = dict(committed_plan=original, candidate_plan=None, target_risk_ids=target_ids,
                    affected_day_indices=affected, protected_day_indices=protected,
                    proposed_modifications=[item.model_dump(mode="json") for item in proposal.operations])
        if not targets or sorted(proposal.target_risk_ids) != sorted(target_ids) \
                or proposal.affected_day_indices != affected:
            return PacingRevisionOutcome(status="unsupported", failure_reason="pacing_revision_unsupported",
                                         resolution_outcome="not_triggered", **base)
        try:
            candidate = self.apply(before, proposal, request)
        except ValueError as exc:
            reason = str(exc) if str(exc) in PacingRevisionFailure.__args__ else "invalid_revision_output"
            return PacingRevisionOutcome(status="rejected", failure_reason=reason,
                                         resolution_outcome="rejected", **base)
        equality = {index: before.days[index].model_dump(mode="json")
                    == candidate.days[index].model_dump(mode="json") for index in protected}
        base.update(candidate_plan=candidate, protected_day_equality=equality)
        if not all(equality.values()):
            return PacingRevisionOutcome(status="rejected", failure_reason="protected_day_drift",
                                         resolution_outcome="rejected", **base)
        try:
            enriched = await enricher(candidate, affected)
        except Exception:
            return PacingRevisionOutcome(status="rejected", failure_reason="enrichment_failure",
                                         resolution_outcome="rejected", **base)
        equality = {index: before.days[index].model_dump(mode="json")
                    == enriched.days[index].model_dump(mode="json") for index in protected}
        base["protected_day_equality"] = equality
        if not all(equality.values()):
            return PacingRevisionOutcome(status="rejected", failure_reason="protected_day_drift",
                                         resolution_outcome="rejected", **base)
        grounding = validate_affected_day_grounding(before, enriched, affected, proposal)
        base.update(grounding_outcome=grounding.outcome, grounding_details=grounding.details)
        if not grounding.accepted:
            return PacingRevisionOutcome(status="rejected", failure_reason=grounding.failure_reason,
                                         resolution_outcome="rejected", **base)
        if ((enriched.cities or [enriched.city]) != (before.cities or [before.city])
                or enriched.start_date != before.start_date or enriched.end_date != before.end_date
                or len(enriched.days) != len(before.days)):
            return PacingRevisionOutcome(status="rejected", failure_reason="constraint_regression",
                                         resolution_outcome="rejected", **base)
        if not _budget_consistent(enriched):
            return PacingRevisionOutcome(status="rejected", failure_reason="budget_regression",
                                         resolution_outcome="rejected", **base)
        validation = await validator(request, enriched)
        after_ids = [risk.id for risk in validation.risks if risk.type == "pacing"
                     and risk.evidence.get("overload_status") == "revisable_overload"]
        unresolved_days = {risk.day_index for risk in validation.risks if risk.type == "pacing"
                           and risk.revisable and risk.day_index in affected}
        before_ratios = {risk.day_index: risk.evidence.get("breakdown", {}).get("load_ratio") for risk in targets}
        after_assessments = {item["day_index"]: item for item in validation.daily_load_assessments}
        after_ratios = {index: after_assessments.get(index, {}).get("breakdown", {}).get("load_ratio")
                        for index in affected}
        metrics = {
            "pacing_revision_triggered": 1, "pacing_target_risk_count": len(target_ids),
            "pacing_target_risk_resolved_count": len(target_ids) - len(unresolved_days),
            "pacing_revision_resolution_rate": ((len(target_ids) - len(unresolved_days)) / len(target_ids)),
            "protected_day_preservation_rate": sum(equality.values()) / len(equality) if equality else 1.0,
            "affected_day_load_ratio_before": before_ratios,
            "affected_day_load_ratio_after": after_ratios,
            "affected_day_load_delta": {index: (
                round(after_ratios[index] - before_ratios[index], 3)
                if before_ratios.get(index) is not None and after_ratios.get(index) is not None else None
            ) for index in affected},
        }
        base.update(candidate_plan=enriched, post_validation=validation.model_dump(mode="json"),
                    post_pacing_risk_ids=after_ids, metrics=metrics)
        blocking_regressions = [risk for risk in validation.risks if risk.severity == "blocking"
                                and risk.type in {"earliest_start", "budget"}]
        if blocking_regressions:
            return PacingRevisionOutcome(status="rejected", failure_reason="constraint_regression",
                                         resolution_outcome="rejected", **base)
        if unresolved_days:
            return PacingRevisionOutcome(status="unresolved", failure_reason="target_risk_unresolved",
                                         resolution_outcome="unresolved", **base)
        enriched.risks = validation.risks
        enriched.validation_status = validation.status
        enriched.pacing_policy_version = validation.pacing_policy_version
        enriched.daily_load_assessments = validation.daily_load_assessments
        base["committed_plan"] = enriched
        return PacingRevisionOutcome(status="success", resolution_outcome="resolved", **base)


_service: Optional[PacingRevisionService] = None


def get_pacing_revision_service() -> PacingRevisionService:
    global _service
    if _service is None: _service = PacingRevisionService()
    return _service
