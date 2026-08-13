"""Strict Phase 3A data contracts for fully offline product evaluation."""

import math
import re
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models.schemas import TripPlan, TripRequest


ScenarioTag = Literal[
    "single_city", "multi_city", "relaxed", "intensive", "avoid_early_start",
    "budget_limit", "mobility", "food_constraint", "multi_interest",
    "xhs_unavailable", "google_places_partial", "google_places_unavailable",
    "route_unavailable", "weather_fallback", "zh_input", "en_input",
    "local_patch", "revision_trigger",
]

DeterministicCheck = Literal[
    "schema_valid", "date_day_consistency", "explicit_constraint_satisfaction_rate",
    "earliest_start_satisfaction", "budget_arithmetic_consistency",
    "budget_limit_satisfaction", "grounded_poi_rate", "unverified_poi_rate",
    "provenance_coverage", "route_check_coverage", "route_feasibility_rate",
    "actionable_risk_count", "revision_risk_resolution_rate",
    "unaffected_day_preservation_rate", "logical_llm_calls", "prompt_tokens",
    "completion_tokens", "total_tokens", "latency_ms",
]

ProviderName = Literal[
    "xhs", "google_places", "google_directions", "google_weather", "amap",
    "hotel_google_places", "hotel_amap",
]
FixtureState = Literal["available", "partial", "unavailable", "degraded"]
HumanFocus = Literal[
    "preference_satisfaction", "itinerary_coherence", "pacing_quality",
    "usefulness", "explanation_quality",
]
MetricName = DeterministicCheck
MetricStatus = Literal["known", "unknown", "not_applicable", "failed"]


class StrictEvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FixtureRequirement(StrictEvalModel):
    provider: ProviderName
    state: FixtureState
    fixture_ref: str = Field(..., pattern=r"^fixtures/[a-z0-9_./-]+\.json$")
    fixture_version: str = Field(..., pattern=r"^v\d+$")
    notes: str = Field(default="", max_length=300)


class ExpectedConstraints(StrictEvalModel):
    earliest_start_time: Optional[str] = Field(
        default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )
    budget_limit_cny: Optional[int] = Field(default=None, gt=0)
    mobility_required: bool = False
    food_requirements: List[str] = Field(default_factory=list)
    protected_day_indices: List[int] = Field(default_factory=list)
    expected_actionable_risk_types: List[
        Literal["earliest_start", "mobility", "budget", "route_feasibility"]
    ] = Field(default_factory=list)


class EvalCase(StrictEvalModel):
    contract_version: Literal["phase3a.v1"] = "phase3a.v1"
    case_id: str = Field(..., pattern=r"^gc_[a-z0-9_]+$")
    name: str = Field(..., min_length=3, max_length=120)
    scenario_tags: List[ScenarioTag] = Field(..., min_length=1)
    language: Literal["zh", "en"]
    trip_request: TripRequest
    expected_constraints: ExpectedConstraints = Field(default_factory=ExpectedConstraints)
    expected_deterministic_checks: List[DeterministicCheck] = Field(..., min_length=1)
    provider_fixtures: List[FixtureRequirement] = Field(..., min_length=1)
    human_review_focus: List[HumanFocus] = Field(..., min_length=1)
    patch_instruction: Optional[str] = Field(default=None, max_length=1000)

    @field_validator(
        "scenario_tags", "expected_deterministic_checks", "human_review_focus"
    )
    @classmethod
    def unique_values(cls, values: List[str]) -> List[str]:
        if len(values) != len(set(values)):
            raise ValueError("evaluation contract lists must not contain duplicates")
        return values

    @model_validator(mode="after")
    def validate_case_consistency(self):
        request = self.trip_request
        start = date.fromisoformat(request.start_date)
        end = date.fromisoformat(request.end_date)
        if (end - start).days + 1 != request.travel_days:
            raise ValueError("trip_request dates must match travel_days")
        if sum(city.days for city in request.cities) != request.travel_days:
            raise ValueError("city stay days must sum to travel_days")
        if self.language != (request.language or "zh"):
            raise ValueError("case language must match trip_request.language")
        if "local_patch" in self.scenario_tags and not self.patch_instruction:
            raise ValueError("local_patch cases require patch_instruction")
        if "local_patch" not in self.scenario_tags and self.patch_instruction:
            raise ValueError("patch_instruction is only valid for local_patch cases")
        if self.expected_constraints.earliest_start_time:
            profile = request.preference_profile
            actual = profile.constraints.earliest_start_time if profile else None
            if actual != self.expected_constraints.earliest_start_time:
                raise ValueError("expected earliest start must match the request profile")
        if self.expected_constraints.budget_limit_cny:
            profile = request.preference_profile
            actual = profile.budget_cny if profile else None
            if actual != self.expected_constraints.budget_limit_cny:
                raise ValueError("expected budget limit must match the request profile")
        return self


class HumanScoreAnchor(StrictEvalModel):
    score: Literal[1, 2, 3, 4, 5]
    anchor: str = Field(..., min_length=10, max_length=500)


class HumanReviewDimension(StrictEvalModel):
    dimension: HumanFocus
    question: str = Field(..., min_length=10, max_length=500)
    anchors: List[HumanScoreAnchor] = Field(..., min_length=5, max_length=5)

    @model_validator(mode="after")
    def require_all_scores(self):
        if sorted(anchor.score for anchor in self.anchors) != [1, 2, 3, 4, 5]:
            raise ValueError("human rubric must define exactly one anchor for scores 1-5")
        return self


class HumanReviewRubric(StrictEvalModel):
    rubric_version: Literal["human.v1"] = "human.v1"
    dimensions: List[HumanReviewDimension] = Field(..., min_length=5, max_length=5)
    reviewer_id: Optional[str] = Field(default=None, max_length=100)
    rationale_required: bool = True

    @model_validator(mode="after")
    def require_unique_dimensions(self):
        names = [item.dimension for item in self.dimensions]
        if len(names) != len(set(names)):
            raise ValueError("human rubric dimensions must be unique")
        return self


class PlannerVersionMetadata(StrictEvalModel):
    planner_version: str = Field(..., pattern=r"^[A-Za-z0-9._-]+$")
    prompt_version: str = Field(..., pattern=r"^[A-Za-z0-9._-]+$")
    model: str = Field(..., min_length=1, max_length=200)
    eval_run_id: str = Field(..., pattern=r"^eval_[A-Za-z0-9._-]+$")
    case_id: str = Field(..., pattern=r"^gc_[a-z0-9_]+$")
    fixture_set_version: str = Field(..., pattern=r"^fixtures\.v\d+$")


class MetricResult(StrictEvalModel):
    """One non-composite metric result with explicit missing-data semantics."""

    metric: MetricName
    status: MetricStatus
    value: Optional[float] = None
    numerator: Optional[float] = None
    denominator: Optional[float] = Field(default=None, ge=0)
    reason: Optional[str] = Field(default=None, max_length=500)
    policy_version: str = Field(..., pattern=r"^metrics\.v\d+$")

    @model_validator(mode="after")
    def enforce_status_semantics(self):
        numeric = (self.value, self.numerator, self.denominator)
        if any(item is not None and not math.isfinite(item) for item in numeric):
            raise ValueError("metric values must be finite")
        if self.status == "known" and self.value is None:
            raise ValueError("known metrics require a value")
        if self.status != "known" and self.value is not None:
            raise ValueError("unknown/not_applicable metrics cannot carry a value")
        if self.status != "known" and not self.reason:
            raise ValueError("unknown/not_applicable metrics require a reason")
        if self.denominator == 0 and self.status == "known":
            raise ValueError("zero-denominator rates cannot be reported as known")
        return self


_SENSITIVE_KEY = re.compile(
    r"(apikey|secret|token|password|cookie|authorization|credential)", re.I
)
_SENSITIVE_VALUE = re.compile(
    r"(bearer\s+[A-Za-z0-9._-]+|sk-[A-Za-z0-9_-]{12,}|xsec_token=|"
    r"https?://[^/@\s]+:[^/@\s]+@|[?&](api_?key|token|auth|signature|secret)=)", re.I
)


class SanitizedProviderFixture(StrictEvalModel):
    """Envelope for synthetic/sanitized provider payloads used by future runners."""

    fixture_version: str = Field(..., pattern=r"^v\d+$")
    provider: ProviderName
    state: FixtureState
    captured_at: Optional[str] = None
    sanitized: Literal[True] = True
    payload: Dict[str, Any]

    @model_validator(mode="after")
    def reject_secrets(self):
        def inspect(value: Any, path: str = "payload") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
                    if _SENSITIVE_KEY.search(normalized_key):
                        raise ValueError(f"sensitive fixture key is forbidden: {path}.{key}")
                    inspect(child, f"{path}.{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    inspect(child, f"{path}[{index}]")
            elif isinstance(value, str) and _SENSITIVE_VALUE.search(value):
                raise ValueError(f"secret-like fixture value is forbidden: {path}")

        inspect(self.payload)
        return self


BadcaseLabel = Literal[
    "constraint_violation", "preference_miss", "ungrounded_poi", "unsupported_fact",
    "route_infeasible", "route_unavailable", "budget_overrun", "budget_inconsistent",
    "overpacked", "underpacked", "revision_failed", "unnecessary_revision",
    "provenance_missing", "excessive_cost", "excessive_latency", "patch_scope_drift",
]
BadcaseEvidenceType = Literal["automatic", "human_required", "not_evaluated"]


class UsageMetadata(StrictEvalModel):
    logical_llm_calls: Optional[int] = Field(default=None, ge=0)
    prompt_tokens: Optional[int] = Field(default=None, ge=0)
    completion_tokens: Optional[int] = Field(default=None, ge=0)
    total_tokens: Optional[int] = Field(default=None, ge=0)


class RouteCheck(StrictEvalModel):
    day_index: int = Field(..., ge=0)
    origin: str = Field(..., min_length=1)
    destination: str = Field(..., min_length=1)
    status: Literal["checked", "unavailable"]
    feasible: Optional[bool] = None
    duration_s: Optional[int] = Field(default=None, ge=0)
    distance_m: Optional[float] = Field(default=None, ge=0)
    data_source: Optional[Literal["google_directions", "amap"]] = None
    validation_pass_id: str = "legacy_unscoped"
    validation_phase: Literal[
        "initial", "post_revision", "post_patch", "legacy_unscoped"
    ] = "legacy_unscoped"
    leg_type: Literal["intra_city_poi_leg", "inter_city_transfer"] = "intra_city_poi_leg"

    @model_validator(mode="after")
    def validate_route_state(self):
        if self.status == "checked" and (self.feasible is None or not self.data_source):
            raise ValueError("checked routes require feasible and data_source")
        if self.status == "unavailable" and self.feasible is not None:
            raise ValueError("unavailable routes cannot claim feasibility")
        return self


class ArtifactEvaluationInput(StrictEvalModel):
    output: Dict[str, Any]
    usage: Optional[UsageMetadata] = None
    latency_ms: Optional[int] = Field(default=None, ge=0)
    route_checks: Optional[List[RouteCheck]] = None
    revision_before: Optional[TripPlan] = None
    revision_target_risk_ids: List[str] = Field(default_factory=list)
    revision_after: Optional[TripPlan] = None
    revision_revalidation_result: Optional[Dict[str, Any]] = None
    patch_before: Optional[TripPlan] = None
    patch_after: Optional[TripPlan] = None


class BadcaseFinding(StrictEvalModel):
    label: BadcaseLabel
    evidence_type: BadcaseEvidenceType
    detected: Optional[bool] = None
    reason: str = Field(..., min_length=1, max_length=500)

    @model_validator(mode="after")
    def enforce_detection_semantics(self):
        if self.evidence_type == "automatic" and self.detected is None:
            raise ValueError("automatic findings require detected true/false")
        if self.evidence_type != "automatic" and self.detected is not None:
            raise ValueError("non-automatic findings cannot claim detection")
        return self


class EvalRunArtifact(StrictEvalModel):
    eval_contract_version: Literal["phase3a.v1"] = "phase3a.v1"
    golden_case_version: Literal["golden.v1"] = "golden.v1"
    metric_policy_version: Literal["metrics.v1", "metrics.v2"] = "metrics.v2"
    runner_version: str = Field(..., pattern=r"^runner\.v\d+$")
    metadata: PlannerVersionMetadata
    fixture_hashes: Dict[str, str]
    started_at: str
    completed_at: str
    latency_ms: int = Field(..., ge=0)
    output_artifact_reference: str
    run_status: Literal["completed", "failed", "network_access_blocked"]
    artifact_origin: Literal["real_planner", "historical_uncontrolled", "synthetic"] = "synthetic"
    metrics: List[MetricResult]
    badcases: List[BadcaseFinding]
    error: Optional[str] = None


class MetricDelta(StrictEvalModel):
    metric: MetricName
    baseline_status: MetricStatus
    candidate_status: MetricStatus
    baseline_value: Optional[float] = None
    candidate_value: Optional[float] = None
    delta: Optional[float] = None
    classification: Literal[
        "improvement", "regression", "unchanged", "known_to_unknown",
        "unknown_to_known", "not_comparable",
    ]


class PairedComparison(StrictEvalModel):
    comparison_id: str
    case_id: str
    baseline_run_id: str
    candidate_run_id: str
    metric_deltas: List[MetricDelta]
    improvements: List[str] = Field(default_factory=list)
    regressions: List[str] = Field(default_factory=list)
    unchanged: List[str] = Field(default_factory=list)
    release_decision: Literal["PASS", "INVESTIGATE", "BLOCK"]
    blocking_reasons: List[str] = Field(default_factory=list)
    investigation_reasons: List[str] = Field(default_factory=list)
    thresholds_provisional: bool = True


class AggregateMetric(StrictEvalModel):
    metric: MetricName
    known_cases: int = Field(..., ge=0)
    unknown_cases: int = Field(..., ge=0)
    not_applicable_cases: int = Field(..., ge=0)
    failed_cases: int = Field(..., ge=0)
    aggregate_value: Optional[float] = None
    numerator: Optional[float] = None
    denominator: Optional[float] = None
    mean: Optional[float] = None
    median: Optional[float] = None
    p90: Optional[float] = None
    p95: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None


class HumanReviewRecord(StrictEvalModel):
    review_id: Optional[str] = Field(default=None, pattern=r"^review_[A-Za-z0-9._-]+$")
    case_id: str = Field(..., pattern=r"^gc_[a-z0-9_]+$")
    planner_version: str
    status: Literal["complete", "pending"] = "pending"
    artifact_reference: Optional[str] = None
    artifact_sha256: Optional[str] = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    rubric_version: Literal["human.v1"] = "human.v1"
    reviewer: Optional[str] = None
    timestamp: Optional[str] = None
    reviewed_at: Optional[str] = None
    scores: Dict[HumanFocus, int] = Field(default_factory=dict)
    rationale: Optional[str] = None
    rationale_by_dimension: Dict[HumanFocus, str] = Field(default_factory=dict)
    unsupported_fact: Literal["yes", "no", "uncertain", "pending"] = "pending"
    unsupported_fact_rationale: Optional[str] = None
    additional_notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_review(self):
        if self.status == "pending":
            if (
                self.scores or self.reviewer or self.timestamp or self.reviewed_at or self.rationale
                or self.rationale_by_dimension
                or self.unsupported_fact != "pending"
                or self.unsupported_fact_rationale
            ):
                raise ValueError("pending human review cannot contain simulated review data")
        else:
            if not self.review_id or not self.artifact_reference or not self.artifact_sha256:
                raise ValueError("complete review requires immutable artifact identity")
            if not self.reviewer or not (self.reviewed_at or self.timestamp):
                raise ValueError("complete review requires reviewer and reviewed_at")
            if set(self.scores) != set(HumanFocus.__args__) or any(not 1 <= value <= 5 for value in self.scores.values()):
                raise ValueError("complete review requires all five scores in range 1-5")
            if set(self.rationale_by_dimension) != set(HumanFocus.__args__) or any(
                not value.strip() for value in self.rationale_by_dimension.values()
            ):
                raise ValueError("complete review requires rationale for all five dimensions")
            if self.unsupported_fact == "pending" or not self.unsupported_fact_rationale:
                raise ValueError("complete review requires unsupported-fact verdict and rationale")
        return self


class PairedPlanHumanReview(StrictEvalModel):
    scores: Dict[HumanFocus, int]
    rationale_by_dimension: Dict[HumanFocus, str]
    unsupported_fact: Literal["yes", "no", "uncertain"]
    unsupported_fact_rationale: str = Field(..., min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_complete_review(self):
        dimensions = set(HumanFocus.__args__)
        if set(self.scores) != dimensions or any(not 1 <= value <= 5 for value in self.scores.values()):
            raise ValueError("paired plan review requires all five scores in range 1-5")
        if set(self.rationale_by_dimension) != dimensions or any(
            not value.strip() for value in self.rationale_by_dimension.values()
        ):
            raise ValueError("paired plan review requires rationale for all five dimensions")
        return self


class RevealedPlanIdentity(StrictEvalModel):
    role: Literal["baseline", "candidate"]
    artifact_reference: str = Field(..., min_length=1)
    artifact_sha256: str = Field(..., pattern=r"^sha256:[a-f0-9]{64}$")
    planner_version: str = Field(..., min_length=1)
    prompt_version: str = Field(..., min_length=1)
    pacing_policy_version: Optional[str] = None
    pacing_revision_version: Optional[str] = None


class PairedReviewReveal(StrictEvalModel):
    plan_a_identity: RevealedPlanIdentity
    plan_b_identity: RevealedPlanIdentity
    baseline_label: Literal["Plan A", "Plan B"]
    candidate_label: Literal["Plan A", "Plan B"]

    @model_validator(mode="after")
    def require_opposite_roles(self):
        if self.baseline_label == self.candidate_label:
            raise ValueError("baseline and candidate labels must differ")
        identities = {"Plan A": self.plan_a_identity, "Plan B": self.plan_b_identity}
        if identities[self.baseline_label].role != "baseline" or identities[self.candidate_label].role != "candidate":
            raise ValueError("revealed labels must agree with plan roles")
        return self


class PairedHumanReviewRecord(StrictEvalModel):
    review_type: Literal["paired_human_review"] = "paired_human_review"
    review_id: str = Field(..., pattern=r"^paired_review_[A-Za-z0-9._-]+$")
    case_id: str = Field(..., pattern=r"^gc_[a-z0-9_]+$")
    rubric_version: Literal["human.v1"] = "human.v1"
    status: Literal["pending", "blind_complete", "revealed_complete"] = "pending"
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None
    blind_material_reference: Optional[str] = None
    blind_material_sha256: Optional[str] = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    blind_plan_a_label: Literal["Plan A"] = "Plan A"
    blind_plan_b_label: Literal["Plan B"] = "Plan B"
    identity_revealed: bool = False
    identity_revealed_at: Optional[str] = None
    blind_review_completed_at: Optional[str] = None
    blind_order_integrity: Literal["verified", "limitation", "unknown"] = "unknown"
    plan_a_review: Optional[PairedPlanHumanReview] = None
    plan_b_review: Optional[PairedPlanHumanReview] = None
    paired_verdict: Optional[Literal["plan_a_better", "plan_b_better", "mixed", "equivalent"]] = None
    paired_rationale: Optional[str] = Field(default=None, max_length=4000)
    more_balanced_plan: Optional[Literal["Plan A", "Plan B", "equivalent", "uncertain"]] = None
    more_executable_plan: Optional[Literal["Plan A", "Plan B", "equivalent", "uncertain"]] = None
    core_preference_sacrifice_detected: Optional[bool] = None
    underpacked_detected: Optional[bool] = None
    metric_improvement_but_ux_regression: Optional[bool] = None
    additional_notes: Optional[str] = Field(default=None, max_length=2000)
    reveal: Optional[PairedReviewReveal] = None

    @staticmethod
    def _timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @model_validator(mode="after")
    def enforce_blind_workflow(self):
        review_fields = (self.reviewer, self.reviewed_at, self.blind_review_completed_at,
                         self.plan_a_review, self.plan_b_review, self.paired_verdict,
                         self.paired_rationale)
        question_fields = (self.more_balanced_plan, self.more_executable_plan,
                           self.core_preference_sacrifice_detected, self.underpacked_detected,
                           self.metric_improvement_but_ux_regression)
        if self.status == "pending":
            if any(value is not None for value in review_fields + question_fields) or self.identity_revealed \
                    or self.identity_revealed_at or self.reveal:
                raise ValueError("pending paired review cannot contain review or reveal evidence")
            return self
        if not all(review_fields) or not self.blind_material_reference or not self.blind_material_sha256:
            raise ValueError("completed blind review requires reviewer, timestamps, two reviews, verdict and immutable material")
        try:
            reviewed = self._timestamp(self.reviewed_at)
            completed = self._timestamp(self.blind_review_completed_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("paired review timestamps must be ISO-8601") from exc
        if reviewed < completed:
            raise ValueError("reviewed_at cannot precede blind review completion")
        if self.status == "blind_complete":
            if self.identity_revealed or self.identity_revealed_at or self.reveal:
                raise ValueError("unrevealed blind review cannot contain identity fields")
            return self
        if not self.identity_revealed or not self.identity_revealed_at or not self.reveal:
            raise ValueError("revealed complete review requires complete reveal identity")
        try:
            revealed = self._timestamp(self.identity_revealed_at)
        except ValueError as exc:
            raise ValueError("identity reveal timestamp must be ISO-8601") from exc
        if revealed < completed:
            raise ValueError("identity cannot be revealed before blind review completion")
        return self


FailureSeverity = Literal["low", "medium", "high", "critical"]
FailureRecoverability = Literal["easy", "moderate", "difficult", "unknown"]
RetryGuidanceStatus = Literal["yes", "no", "unknown"]
FailureUserImpact = Literal["low", "medium", "high", "critical"]


class ProductFailureReviewRecord(StrictEvalModel):
    """Human product assessment for a failed run that produced no TripPlan."""

    review_type: Literal["product_failure"] = "product_failure"
    status: Literal["complete", "pending"] = "pending"
    case_id: str = Field(..., pattern=r"^gc_[a-z0-9_]+$")
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None
    artifact_reference: str
    artifact_sha256: str = Field(..., pattern=r"^sha256:[a-f0-9]{64}$")

    severity: Optional[FailureSeverity] = None
    severity_rationale: Optional[str] = None
    recoverability: Optional[FailureRecoverability] = None
    recoverability_rationale: Optional[str] = None
    retry_guidance_present: Optional[RetryGuidanceStatus] = None
    retry_guidance_rationale: Optional[str] = None
    user_impact: Optional[FailureUserImpact] = None
    user_impact_rationale: Optional[str] = None
    additional_notes: Optional[str] = None

    # Immutable objective context copied from the sanitized failure manifest.
    failure_type: Optional[str] = None
    failed_stage: Optional[str] = None
    logical_llm_calls: Optional[int] = Field(default=None, ge=0)
    prompt_tokens: Optional[int] = Field(default=None, ge=0)
    completion_tokens: Optional[int] = Field(default=None, ge=0)
    total_tokens: Optional[int] = Field(default=None, ge=0)
    latency_ms: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_failure_review(self):
        human_fields = (
            self.reviewer, self.reviewed_at, self.severity,
            self.severity_rationale, self.recoverability,
            self.recoverability_rationale, self.retry_guidance_present,
            self.retry_guidance_rationale, self.user_impact,
            self.user_impact_rationale, self.additional_notes,
        )
        if self.status == "pending":
            if any(value is not None for value in human_fields):
                raise ValueError("pending product failure review cannot contain human judgments")
            return self

        if not self.reviewer or not self.reviewed_at:
            raise ValueError("complete product failure review requires reviewer and reviewed_at")
        judgments = (
            self.severity, self.recoverability,
            self.retry_guidance_present, self.user_impact,
        )
        if any(value is None for value in judgments):
            raise ValueError("complete product failure review requires all judgments")
        rationales = (
            self.severity_rationale, self.recoverability_rationale,
            self.retry_guidance_rationale, self.user_impact_rationale,
        )
        if any(not value or not value.strip() for value in rationales):
            raise ValueError("complete product failure review requires all rationales")
        return self


class ScenarioBreakdown(StrictEvalModel):
    group: str
    case_ids: List[str]
    metrics: List[AggregateMetric]
    automatic_badcase_frequency: Dict[BadcaseLabel, int]


class BaselineManifest(StrictEvalModel):
    baseline_id: str
    baseline_status: Literal["established", "not_established"]
    reason: Optional[str] = None
    planner_version: str
    prompt_version: str
    model: str
    code_revision: str
    eval_contract_version: Literal["phase3a.v1"] = "phase3a.v1"
    golden_case_version: Literal["golden.v1"] = "golden.v1"
    fixture_set_version: str
    metric_policy_version: Literal["metrics.v1"] = "metrics.v1"
    runner_version: str
    generated_at: str
    case_ids: List[str]
    fixture_hashes_by_case: Dict[str, Dict[str, str]]


class BatchEvaluationReport(StrictEvalModel):
    manifest: BaselineManifest
    cases_total: int = Field(..., ge=0)
    cases_evaluated: int = Field(..., ge=0)
    cases_failed: int = Field(..., ge=0)
    cases_unknown: int = Field(..., ge=0)
    aggregate_metrics: List[AggregateMetric]
    automatic_badcase_frequency: Dict[BadcaseLabel, int]
    human_required_frequency: Dict[BadcaseLabel, int]
    not_evaluated_frequency: Dict[BadcaseLabel, int]
    scenario_breakdown: List[ScenarioBreakdown]
    language_breakdown: List[ScenarioBreakdown]
    city_scope_breakdown: List[ScenarioBreakdown]
    human_reviews: List[HumanReviewRecord]
    top_automatic_badcases: List[str]
