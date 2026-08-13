"""Contracts for production-compatible, sanitized Planner artifact capture."""

import re
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import Field, model_validator

from ..models.schemas import TripPlan, TripRequest
from .models import EvalCase, ProviderName, StrictEvalModel


CaptureState = Literal["known", "unknown", "not_applicable"]
ProviderCaptureState = Literal["success", "degraded", "unavailable", "partial", "not_called"]


class CapturedValue(StrictEvalModel):
    status: CaptureState
    value: Optional[Any] = None
    reason: Optional[str] = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_state(self):
        if self.status == "known" and self.value is None:
            raise ValueError("known capture fields require a value")
        if self.status != "known" and self.value is not None:
            raise ValueError("unknown/not_applicable fields cannot contain a value")
        if self.status != "known" and not self.reason:
            raise ValueError("unknown/not_applicable fields require a reason")
        return self


class CaptureIdentity(StrictEvalModel):
    eval_run_id: str = Field(..., pattern=r"^capture_[A-Za-z0-9._-]+$")
    case_id: str = Field(..., pattern=r"^gc_[a-z0-9_]+$")
    planner_version: str
    prompt_version: str
    model: CapturedValue
    code_revision: str
    eval_contract_version: Literal["phase3a.v1"] = "phase3a.v1"
    golden_case_version: Literal["golden.v1"] = "golden.v1"
    capture_version: Literal["capture.v1", "capture.v2"] = "capture.v2"
    fixture_version: str = "fixtures.v1"


class ProviderStatusCapture(StrictEvalModel):
    provider: ProviderName
    status: ProviderCaptureState
    reason: Optional[str] = Field(default=None, max_length=500)
    data_available: bool
    evidence_count: int = Field(..., ge=0)
    summary: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_provider_state(self):
        if self.status in {"degraded", "unavailable", "partial", "not_called"} and not self.reason:
            raise ValueError("non-success provider states require a reason")
        if self.status in {"unavailable", "not_called"} and self.data_available:
            raise ValueError("unavailable/not-called provider cannot claim available data")
        return self


class CapturedRouteCheck(StrictEvalModel):
    day_index: int = Field(..., ge=0)
    origin_stable_id: str = Field(..., min_length=1)
    destination_stable_id: str = Field(..., min_length=1)
    provider: Literal["google_directions", "amap"]
    request_attempted: bool
    data_available: bool
    distance_m: CapturedValue
    duration_s: CapturedValue
    feasible: CapturedValue
    route_mode: str = Field(..., min_length=1)
    verification_status: Literal["verified", "unverified", "unavailable"]
    reason: Optional[str] = Field(default=None, max_length=500)
    validation_pass_id: str = "legacy_unscoped"
    validation_phase: Literal[
        "initial", "post_revision", "post_patch", "legacy_unscoped"
    ] = "legacy_unscoped"
    leg_type: Literal["intra_city_poi_leg", "inter_city_transfer"] = "intra_city_poi_leg"

    @model_validator(mode="after")
    def validate_availability(self):
        if not self.data_available:
            if self.verification_status != "unavailable" or not self.reason:
                raise ValueError("unavailable route data requires status and reason")
            if self.distance_m.status == "known" or self.duration_s.status == "known":
                raise ValueError("unavailable route cannot contain verified metrics")
            if self.feasible.status == "known":
                raise ValueError("unavailable route cannot claim feasibility")
        return self


class StageUsage(StrictEvalModel):
    stage: str
    logical_llm_calls: CapturedValue
    prompt_tokens: CapturedValue
    completion_tokens: CapturedValue
    total_tokens: CapturedValue
    retries: CapturedValue
    latency_ms: CapturedValue


class CaptureUsage(StrictEvalModel):
    logical_llm_calls: CapturedValue
    prompt_tokens: CapturedValue
    completion_tokens: CapturedValue
    total_tokens: CapturedValue
    retries: CapturedValue
    stages: List[StageUsage] = Field(default_factory=list)


class CapturedRemoveOptionalPOI(StrictEvalModel):
    operation: Literal["remove_optional_poi"]
    day_index: int = Field(..., ge=0)
    target_id: str = Field(..., min_length=1)
    target_name: str = Field(..., min_length=1)
    reason: str = Field(default="", max_length=300)


class CapturedReduceOptionalDuration(StrictEvalModel):
    operation: Literal["reduce_optional_duration"]
    day_index: int = Field(..., ge=0)
    target_id: str = Field(..., min_length=1)
    target_name: str = Field(..., min_length=1)
    old_minutes: int = Field(..., ge=15, le=600)
    new_minutes: int = Field(..., ge=15, le=600)
    reason: str = Field(default="", max_length=300)


class CapturedDelayStartTime(StrictEvalModel):
    operation: Literal["delay_start_time"]
    day_index: int = Field(..., ge=0)
    old_value: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    new_value: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    reason: str = Field(default="", max_length=300)


CapturedPacingOperation = Annotated[
    Union[CapturedRemoveOptionalPOI, CapturedReduceOptionalDuration, CapturedDelayStartTime],
    Field(discriminator="operation"),
]
RevisionInstructionMetadata = Union[str, CapturedPacingOperation]


class RevisionCapture(StrictEvalModel):
    status: CaptureState
    before: Optional[TripPlan] = None
    target_risk_ids: List[str] = Field(default_factory=list)
    after: Optional[TripPlan] = None
    revalidation_result: Optional[Dict[str, Any]] = None
    initial_validation_result: Optional[Dict[str, Any]] = None
    initial_risks: List[Dict[str, Any]] = Field(default_factory=list)
    protected_elements: List[str] = Field(default_factory=list)
    revision_instructions_metadata: List[RevisionInstructionMetadata] = Field(default_factory=list)
    post_revision_enrichment_state: Optional[str] = None
    post_revision_risks: List[Dict[str, Any]] = Field(default_factory=list)
    reason: Optional[str] = None
    revision_kind: Literal["legacy_full_plan", "targeted_pacing"] = "legacy_full_plan"
    revision_status: Optional[Literal["success", "unresolved", "rejected", "unsupported"]] = None
    candidate: Optional[TripPlan] = None
    affected_day_indices: List[int] = Field(default_factory=list)
    protected_day_indices: List[int] = Field(default_factory=list)
    protected_day_equality: Dict[int, bool] = Field(default_factory=dict)
    post_pacing_risk_ids: List[str] = Field(default_factory=list)
    resolution_outcome: Optional[str] = None
    failure_reason: Optional[str] = None
    pacing_policy_version: Optional[str] = None
    pacing_revision_metrics: Dict[str, Any] = Field(default_factory=dict)
    grounding_outcome: Optional[Literal["valid_grounding_change", "grounding_improvement"]] = None
    grounding_details: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_revision(self):
        values = (self.before, self.after, self.revalidation_result)
        if self.revision_kind == "targeted_pacing":
            if self.status != "known" or self.before is None or self.after is None:
                raise ValueError("targeted pacing capture requires known before/after")
            if self.revision_status in {"success", "unresolved"} and self.revalidation_result is None:
                raise ValueError("validated pacing outcome requires revalidation")
            return self
        if self.status == "known" and any(value is None for value in values):
            raise ValueError("known revision requires before/after/revalidation")
        if self.status != "known" and any(value is not None for value in values):
            raise ValueError("missing revision state cannot contain before/after data")
        if self.status != "known" and not self.reason:
            raise ValueError("missing revision state requires a reason")
        return self


class PatchCapture(StrictEvalModel):
    status: CaptureState
    before: Optional[TripPlan] = None
    after: Optional[TripPlan] = None
    affected_day_indices: List[int] = Field(default_factory=list)
    protected_day_indices: List[int] = Field(default_factory=list)
    patch_request: Optional[str] = None
    typed_operations: List[Dict[str, Any]] = Field(default_factory=list)
    validation_result: Optional[Dict[str, Any]] = None
    plan_version_before: Optional[int] = None
    plan_version_after: Optional[int] = None
    base_artifact_identity: Optional[Dict[str, Any]] = None
    base_artifact_hash: Optional[str] = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_patch(self):
        if self.status == "known" and (self.before is None or self.after is None):
            raise ValueError("known patch requires before and after")
        if self.status != "known" and (self.before is not None or self.after is not None):
            raise ValueError("missing patch state cannot contain plans")
        if self.status != "known" and not self.reason:
            raise ValueError("missing patch state requires a reason")
        return self


class HumanReviewHook(StrictEvalModel):
    status: Literal["pending", "complete"] = "pending"
    review_id: Optional[str] = None
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None
    rubric_version: Literal["human.v1"] = "human.v1"

    @model_validator(mode="after")
    def no_fake_review(self):
        if self.status == "pending" and any((self.review_id, self.reviewer, self.reviewed_at)):
            raise ValueError("pending review cannot contain reviewer identity")
        if self.status == "complete" and not all((self.review_id, self.reviewer, self.reviewed_at)):
            raise ValueError("complete review requires review identity and timestamp")
        return self


class PlannerCaptureArtifact(StrictEvalModel):
    identity: CaptureIdentity
    trip_request: TripRequest
    preference_profile: CapturedValue
    scenario_tags: List[str]
    final_trip_plan: CapturedValue
    final_validation_result: CapturedValue
    risks: CapturedValue
    revision: RevisionCapture
    patch: PatchCapture
    provider_statuses_state: CapturedValue
    provider_statuses: List[ProviderStatusCapture]
    xhs_evidence_metadata: CapturedValue
    route_checks_state: CapturedValue
    route_checks: List[CapturedRouteCheck]
    usage: CaptureUsage
    execution_started_at: CapturedValue
    execution_completed_at: CapturedValue
    total_latency_ms: CapturedValue
    stage_latency_ms: CapturedValue
    human_review: HumanReviewHook = Field(default_factory=HumanReviewHook)
    snapshot_hashes: Dict[str, str] = Field(default_factory=dict)
    capture_mode: Literal["dry-run", "record", "replay"]
    sanitized: Literal[True] = True
    pacing_policy_version: Optional[str] = None
    daily_load_assessments: List[Dict[str, Any]] = Field(default_factory=list)
    pacing_risk_ids: List[str] = Field(default_factory=list)
    validation_pass_scope: Optional[str] = None


class CaptureBudget(StrictEvalModel):
    max_cases: int = Field(default=1, ge=1, le=16)
    max_llm_calls: int = Field(default=4, ge=0)
    max_total_tokens: Optional[int] = Field(default=None, ge=0)
    stop_on_error: bool = True
    case_allowlist: List[str] = Field(default_factory=list)


class CaptureFailureManifest(StrictEvalModel):
    capture_status: Literal["failed"] = "failed"
    run_status: Literal["failed", "budget_exceeded"] = "failed"
    failure_type: Literal[
        "planner_output_parse_failure", "planner_execution_error",
        "provider_execution_error", "capture_validation_failure", "budget_exceeded",
    ]
    case_id: str = Field(..., pattern=r"^gc_[a-z0-9_]+$")
    planner_version: str
    prompt_version: str
    code_revision: str
    model: CapturedValue
    calls_completed: int = Field(..., ge=0)
    prompt_tokens: int = Field(..., ge=0)
    completion_tokens: int = Field(..., ge=0)
    total_tokens: int = Field(..., ge=0)
    retries: int = Field(..., ge=0)
    failed_before_stage: Optional[str] = None
    failed_after_stage: Optional[str] = None
    failed_stage: Optional[str] = None
    elapsed_latency_ms: int = Field(default=0, ge=0)
    configured_max_llm_calls: int = Field(..., ge=0)
    configured_max_total_tokens: Optional[int] = Field(default=None, ge=0)
    execution_started_at: str
    execution_completed_at: str
    failure_reason: Literal[
        "planner_output_parse_failure", "planner_execution_error",
        "provider_execution_error", "capture_validation_failure",
        "max_llm_calls_exceeded", "pre_stage_token_admission_blocked",
        "post_call_token_ceiling_exceeded", "budget_already_exceeded",
    ]
    admission_events: List[Dict[str, Any]] = Field(default_factory=list)
    sanitized: Literal[True] = True

    @model_validator(mode="after")
    def reject_sensitive_failure_telemetry(self):
        secret = re.compile(
            r"bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}|xsec_token=|"
            r"https?://[^/@\s]+:[^/@\s]+@|[?&](api_?key|token|auth|signature|secret)=",
            re.I,
        )
        values = [self.planner_version, self.prompt_version, self.code_revision,
                  str(self.model.value or ""), self.failed_before_stage or "",
                  self.failed_after_stage or "", self.failed_stage or ""]
        if any(secret.search(value) for value in values):
            raise ValueError("failure telemetry contains secret-like data")
        reject_personal_identifiers(self.model.model_dump(mode="json"), "failure.model")
        return self


class ProductionCaptureResult(StrictEvalModel):
    """Observation payload returned beside the production Planner result."""

    final_trip_plan: TripPlan
    model: Optional[str] = None
    final_validation_result: Optional[Dict[str, Any]] = None
    risks: Optional[List[Dict[str, Any]]] = None
    provider_statuses_complete: bool = False
    provider_statuses: List[ProviderStatusCapture] = Field(default_factory=list)
    xhs_evidence_metadata: Optional[Dict[str, Any]] = None
    route_checks: List[CapturedRouteCheck] = Field(default_factory=list)
    route_checks_complete: bool = False
    usage: CaptureUsage
    execution_started_at: Optional[str] = None
    execution_completed_at: Optional[str] = None
    total_latency_ms: Optional[int] = Field(default=None, ge=0)
    stage_latency_ms: Optional[Dict[str, int]] = None
    revision: RevisionCapture
    patch: PatchCapture
    provider_snapshots: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    pacing_policy_version: Optional[str] = None
    daily_load_assessments: List[Dict[str, Any]] = Field(default_factory=list)
    pacing_risk_ids: List[str] = Field(default_factory=list)
    validation_pass_scope: Optional[str] = None


_PII_KEYS = re.compile(r"(^|_)(email|phone|user_id|session_id|passport|full_name)($|_)", re.I)
_PII_VALUE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"\+\d[\d ()-]{8,}\d|\b1[3-9]\d{9}\b|"
    r"\b\d{3}[- ]\d{3}[- ]\d{4}\b",
    re.I,
)


def reject_personal_identifiers(value: Any, path: str = "artifact") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _PII_KEYS.search(str(key)):
                raise ValueError(f"personal identifier is forbidden: {path}.{key}")
            reject_personal_identifiers(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_personal_identifiers(child, f"{path}[{index}]")
    elif isinstance(value, str) and _PII_VALUE.search(value):
        raise ValueError(f"personal identifier value is forbidden: {path}")
