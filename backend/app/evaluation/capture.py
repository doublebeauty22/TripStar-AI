"""Observation-only capture around a production-compatible Planner executor."""

import copy
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from .capture_models import (
    CaptureBudget, CaptureFailureManifest, CaptureIdentity, CaptureUsage, CapturedValue, HumanReviewHook,
    PatchCapture, PlannerCaptureArtifact, ProductionCaptureResult,
    RevisionCapture, reject_personal_identifiers,
)
from .fixtures import write_canonical_json
from .models import EvalCase, SanitizedProviderFixture
from .models import ArtifactEvaluationInput, RouteCheck, UsageMetadata
from .network import deny_network
from .snapshots import ProviderSnapshotStore, commit_capture_set
from ..services.llm_service import LLMCallBudgetExceeded
from ..models.schemas import TripPlan


class CaptureGuardError(RuntimeError):
    pass


CaptureExecutor = Callable[[EvalCase, Dict[str, Any]], Awaitable[ProductionCaptureResult]]
_SECRET_KEY = re.compile(r"(api.?key|secret|password|cookie|authorization|credential|proxy_url)", re.I)
_SECRET_VALUE = re.compile(
    r"(bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}|xsec_token=|"
    r"https?://[^/@\s]+:[^/@\s]+@|[?&](api_?key|token|auth|signature|secret)=)", re.I
)


def current_code_revision(repo_root: Path) -> str:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_root,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return f"{revision}{'-dirty' if dirty else ''}"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def reject_secrets(value: Any, path: str = "artifact") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _SECRET_KEY.search(str(key)):
                raise ValueError(f"secret-bearing field is forbidden: {path}.{key}")
            reject_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_secrets(child, f"{path}[{index}]")
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        raise ValueError(f"secret-like value is forbidden: {path}")


def _missing(status: str, reason: str) -> CapturedValue:
    return CapturedValue(status=status, reason=reason)


def _known(value: Any) -> CapturedValue:
    return CapturedValue(status="known", value=value)


def sanitized_plan_for_evaluation(plan):
    """Remove raw XHS retrieval text while retaining structured provenance."""
    safe = plan.model_copy(deep=True)
    for research in safe.xhs_research:
        research.context = ""
        for evidence in research.evidence:
            evidence.extracted_text = ""
    return safe


def _dry_run_artifact(case: EvalCase, identity: CaptureIdentity) -> PlannerCaptureArtifact:
    patch_case = "local_patch" in case.scenario_tags
    revision_case = "revision_trigger" in case.scenario_tags
    return PlannerCaptureArtifact(
        identity=identity, trip_request=case.trip_request,
        preference_profile=(_known(case.trip_request.preference_profile.model_dump(mode="json"))
                            if case.trip_request.preference_profile else
                            _missing("not_applicable", "request has no structured preference profile")),
        scenario_tags=list(case.scenario_tags),
        final_trip_plan=_missing("unknown", "dry-run does not execute Planner"),
        final_validation_result=_missing("unknown", "dry-run does not execute Validator"),
        risks=_missing("unknown", "dry-run does not execute Validator"),
        revision=RevisionCapture(status="unknown" if revision_case else "not_applicable",
                                 reason="dry-run does not execute revision" if revision_case else "case does not require revision"),
        patch=PatchCapture(status="unknown" if patch_case else "not_applicable",
                           reason="dry-run does not execute patch" if patch_case else "case is not a patch scenario"),
        provider_statuses_state=_missing("unknown", "dry-run does not call providers"),
        provider_statuses=[], xhs_evidence_metadata=_missing("unknown", "dry-run does not call XHS"),
        route_checks_state=_missing("unknown", "dry-run does not execute route checks"),
        route_checks=[],
        usage=CaptureUsage(
            **{name: _missing("unknown", "dry-run does not call an LLM") for name in
               ("logical_llm_calls", "prompt_tokens", "completion_tokens", "total_tokens", "retries")}),
        execution_started_at=_missing("not_applicable", "dry-run performs no Planner execution"),
        execution_completed_at=_missing("not_applicable", "dry-run performs no Planner execution"),
        total_latency_ms=_missing("not_applicable", "dry-run performs no Planner execution"),
        stage_latency_ms=_missing("not_applicable", "dry-run performs no Planner stages"),
        human_review=HumanReviewHook(), capture_mode="dry-run",
    )


class PlannerArtifactCapture:
    def __init__(self, repo_root: Path, snapshot_store: ProviderSnapshotStore):
        self.repo_root = repo_root.resolve()
        self.snapshot_store = snapshot_store

    def _identity(self, case: EvalCase, run_id: str, planner_version: str,
                  prompt_version: str, model: Optional[str]) -> CaptureIdentity:
        return CaptureIdentity(
            eval_run_id=run_id, case_id=case.case_id, planner_version=planner_version,
            prompt_version=prompt_version,
            model=_known(model) if model else _missing("unknown", "model is not known before execution"),
            code_revision=current_code_revision(self.repo_root),
        )

    def _write_failure(self, output_path: Path, failure: CaptureFailureManifest) -> None:
        serialized = failure.model_dump(mode="json")
        reject_secrets(serialized); reject_personal_identifiers(serialized)
        write_canonical_json(output_path.with_name(output_path.stem + ".failure.json"), serialized)

    async def capture(self, case: EvalCase, *, run_id: str, mode: str,
                      planner_version: str, prompt_version: str,
                      output_path: Path, budget: CaptureBudget,
                      allow_real_api: bool = False,
                      executor: Optional[CaptureExecutor] = None,
                      executor_context: Optional[Dict[str, Any]] = None) -> PlannerCaptureArtifact:
        if case.case_id not in budget.case_allowlist:
            raise CaptureGuardError("case_not_in_allowlist")
        if mode == "record" and not allow_real_api:
            raise CaptureGuardError("real_api_not_allowed")
        identity = self._identity(case, run_id, planner_version, prompt_version, None)
        if mode == "dry-run":
            artifact = _dry_run_artifact(case, identity)
        else:
            if executor is None:
                raise CaptureGuardError("production_executor_required")
            replay_snapshots, snapshot_hashes, record_fixtures = {}, {}, {}
            if mode == "replay":
                for requirement in case.provider_fixtures:
                    fixture, digest = self.snapshot_store.replay(case.case_id, requirement.provider)
                    replay_snapshots[requirement.provider] = fixture.model_dump(mode="json")
                    snapshot_hashes[requirement.provider] = digest
            original_request = copy.deepcopy(case.trip_request)
            context = {"mode": mode, "snapshots": replay_snapshots,
                       "max_llm_calls": budget.max_llm_calls,
                       "max_total_tokens": budget.max_total_tokens}
            context.update(executor_context or {})
            started = time.monotonic()
            started_at = datetime.now(timezone.utc).isoformat()
            try:
                if mode == "replay":
                    with deny_network():
                        result = await executor(case, context)
                else:
                    result = await executor(case, context)
            except LLMCallBudgetExceeded as exc:
                completed_at = datetime.now(timezone.utc).isoformat()
                snapshot = exc.snapshot
                reason = (
                    "post_call_token_ceiling_exceeded" if exc.failed_after_stage else
                    "pre_stage_token_admission_blocked" if any(
                        event.get("reason") == "known_stage_exposure_exceeds_remaining_budget"
                        for event in snapshot.get("admission_events", [])
                    ) else
                    "budget_already_exceeded" if snapshot.get("budget_exceeded") else
                    "max_llm_calls_exceeded"
                )
                failure = CaptureFailureManifest(
                    run_status="budget_exceeded", failure_type="budget_exceeded",
                    case_id=case.case_id, planner_version=planner_version,
                    prompt_version=prompt_version, code_revision=identity.code_revision,
                    model=_known(snapshot["model"]) if snapshot.get("model") else
                          _missing("unknown", "model was not observed before budget failure"),
                    calls_completed=snapshot.get("logical_llm_calls", 0),
                    prompt_tokens=snapshot.get("prompt_tokens", 0),
                    completion_tokens=snapshot.get("completion_tokens", 0),
                    total_tokens=snapshot.get("total_tokens", 0),
                    retries=snapshot.get("retry_count", 0),
                    failed_before_stage=exc.failed_before_stage,
                    failed_after_stage=exc.failed_after_stage,
                    configured_max_llm_calls=budget.max_llm_calls,
                    configured_max_total_tokens=budget.max_total_tokens,
                    execution_started_at=started_at, execution_completed_at=completed_at,
                    failed_stage=exc.failed_before_stage or exc.failed_after_stage,
                    elapsed_latency_ms=int((time.monotonic() - started) * 1000),
                    failure_reason=reason,
                    admission_events=snapshot.get("admission_events", []),
                )
                self._write_failure(output_path, failure)
                raise
            except Exception as exc:
                from .production_capture import ProductionExecutionError
                completed_at = datetime.now(timezone.utc).isoformat()
                if isinstance(exc, ProductionExecutionError):
                    snapshot = exc.usage
                    failure_type = exc.failure_type
                    failed_stage = exc.failed_stage
                    elapsed = exc.elapsed_ms
                else:
                    snapshot = {}
                    failure_type = "capture_validation_failure"
                    failed_stage = "capture_execution"
                    elapsed = int((time.monotonic() - started) * 1000)
                self._write_failure(output_path, CaptureFailureManifest(
                    run_status="failed", failure_type=failure_type,
                    case_id=case.case_id, planner_version=planner_version,
                    prompt_version=prompt_version, code_revision=identity.code_revision,
                    model=_known(snapshot["model"]) if snapshot.get("model") else
                          _missing("unknown", "model identity was not observed"),
                    calls_completed=int(snapshot.get("logical_llm_calls", 0)),
                    prompt_tokens=int(snapshot.get("prompt_tokens", 0)),
                    completion_tokens=int(snapshot.get("completion_tokens", 0)),
                    total_tokens=int(snapshot.get("total_tokens", 0)),
                    retries=int(snapshot.get("retry_count", 0)),
                    failed_stage=failed_stage, elapsed_latency_ms=elapsed,
                    configured_max_llm_calls=budget.max_llm_calls,
                    configured_max_total_tokens=budget.max_total_tokens,
                    execution_started_at=started_at, execution_completed_at=completed_at,
                    failure_reason=failure_type,
                ))
                raise
            elapsed_ms = int((time.monotonic() - started) * 1000)
            completed_at = datetime.now(timezone.utc).isoformat()
            if case.trip_request != original_request:
                raise CaptureGuardError("production executor modified TripRequest")
            calls = result.usage.logical_llm_calls
            tokens = result.usage.total_tokens
            if calls.status == "known" and calls.value > budget.max_llm_calls:
                raise CaptureGuardError("max_llm_calls_exceeded")
            if budget.max_total_tokens is not None and tokens.status == "known" and tokens.value > budget.max_total_tokens:
                self._write_failure(output_path, CaptureFailureManifest(
                    run_status="budget_exceeded", failure_type="budget_exceeded",
                    case_id=case.case_id, planner_version=planner_version,
                    prompt_version=prompt_version, code_revision=identity.code_revision,
                    model=_known(result.model) if result.model else _missing("unknown", "model not captured"),
                    calls_completed=int(calls.value),
                    prompt_tokens=int(result.usage.prompt_tokens.value) if result.usage.prompt_tokens.status == "known" else 0,
                    completion_tokens=int(result.usage.completion_tokens.value) if result.usage.completion_tokens.status == "known" else 0,
                    total_tokens=int(tokens.value),
                    retries=int(result.usage.retries.value) if result.usage.retries.status == "known" else 0,
                    failed_after_stage="capture_execution",
                    configured_max_llm_calls=budget.max_llm_calls,
                    configured_max_total_tokens=budget.max_total_tokens,
                    execution_started_at=result.execution_started_at or started_at,
                    execution_completed_at=result.execution_completed_at or completed_at,
                    failed_stage="capture_execution", elapsed_latency_ms=elapsed_ms,
                    failure_reason="post_call_token_ceiling_exceeded",
                ))
                raise CaptureGuardError("max_total_tokens_exceeded")
            if mode == "record":
                for provider, payload in result.provider_snapshots.items():
                    status = next((item.status for item in result.provider_statuses if item.provider == provider), "unavailable")
                    fixture_state = {"success": "available", "partial": "partial", "degraded": "degraded", "unavailable": "unavailable"}[status]
                    fixture = SanitizedProviderFixture(fixture_version="v1", provider=provider,
                        state=fixture_state, sanitized=True, payload=payload)
                    record_fixtures[provider] = fixture
            identity.model = _known(result.model) if result.model else _missing("unknown", "executor did not capture model")
            revision = result.revision.model_copy(deep=True)
            if revision.before is not None:
                revision.before = sanitized_plan_for_evaluation(revision.before)
            if revision.after is not None:
                revision.after = sanitized_plan_for_evaluation(revision.after)
            if revision.candidate is not None:
                revision.candidate = sanitized_plan_for_evaluation(revision.candidate)
            patch_capture = result.patch.model_copy(deep=True)
            if patch_capture.before is not None:
                patch_capture.before = sanitized_plan_for_evaluation(patch_capture.before)
            if patch_capture.after is not None:
                patch_capture.after = sanitized_plan_for_evaluation(patch_capture.after)
            artifact = PlannerCaptureArtifact(
                identity=identity, trip_request=case.trip_request,
                preference_profile=(_known(case.trip_request.preference_profile.model_dump(mode="json"))
                                    if case.trip_request.preference_profile else
                                    _missing("not_applicable", "request has no structured preference profile")),
                scenario_tags=list(case.scenario_tags), final_trip_plan=_known(sanitized_plan_for_evaluation(result.final_trip_plan).model_dump(mode="json")),
                final_validation_result=(_known(result.final_validation_result) if result.final_validation_result is not None else _missing("unknown", "validation result not captured")),
                risks=(_known(result.risks) if result.risks is not None else _missing("unknown", "risks not captured")),
                revision=revision, patch=patch_capture,
                provider_statuses_state=(_known(True) if result.provider_statuses_complete else _missing("unknown", "provider status set is incomplete")),
                provider_statuses=result.provider_statuses,
                xhs_evidence_metadata=(_known(result.xhs_evidence_metadata) if result.xhs_evidence_metadata is not None else _missing("unknown", "XHS evidence metadata not captured")),
                route_checks_state=(_known(True) if result.route_checks_complete else _missing("unknown", "route check set is incomplete")),
                route_checks=result.route_checks, usage=result.usage,
                execution_started_at=_known(result.execution_started_at or started_at),
                execution_completed_at=_known(result.execution_completed_at or completed_at),
                total_latency_ms=_known(result.total_latency_ms if result.total_latency_ms is not None else elapsed_ms),
                stage_latency_ms=(_known(result.stage_latency_ms) if result.stage_latency_ms is not None else _missing("unknown", "stage latency not captured")),
                human_review=HumanReviewHook(), snapshot_hashes=snapshot_hashes, capture_mode=mode,
                pacing_policy_version=result.pacing_policy_version,
                daily_load_assessments=result.daily_load_assessments,
                pacing_risk_ids=result.pacing_risk_ids,
                validation_pass_scope=result.validation_pass_scope,
            )
        serialized = artifact.model_dump(mode="json")
        reject_secrets(serialized); reject_personal_identifiers(serialized)
        if mode == "record":
            snapshot_hashes = commit_capture_set(
                output_path=output_path, snapshot_store=self.snapshot_store,
                case_id=case.case_id, artifact=serialized, fixtures=record_fixtures,
            )
            artifact.snapshot_hashes = snapshot_hashes
        else:
            write_canonical_json(output_path, serialized)
        return artifact


def validate_batch_selection(cases: list[EvalCase], budget: CaptureBudget, allow_multiple: bool) -> list[EvalCase]:
    selected = [case for case in cases if case.case_id in budget.case_allowlist]
    if len(selected) > budget.max_cases:
        raise CaptureGuardError("max_cases_exceeded")
    if len(selected) > 1 and not allow_multiple:
        raise CaptureGuardError("multiple_cases_require_explicit_opt_in")
    return selected


def to_offline_evaluation_input(artifact: PlannerCaptureArtifact) -> ArtifactEvaluationInput:
    """Cross the offline boundary without guessing any missing capture value."""
    if artifact.final_trip_plan.status != "known":
        raise CaptureGuardError("final_trip_plan_unknown")
    usage_fields = artifact.usage
    usage = None
    if all(getattr(usage_fields, name).status == "known" for name in
           ("logical_llm_calls", "prompt_tokens", "completion_tokens", "total_tokens")):
        usage = UsageMetadata(**{name: int(getattr(usage_fields, name).value) for name in
            ("logical_llm_calls", "prompt_tokens", "completion_tokens", "total_tokens")})
    routes = None
    if artifact.route_checks_state.status == "known":
        plan = TripPlan.model_validate(artifact.final_trip_plan.value)
        phases = {item.validation_phase for item in artifact.route_checks}
        if artifact.patch.status == "known":
            final_phase = "post_patch"
        elif artifact.revision.status == "known":
            final_phase = "post_revision"
        elif plan.revision_count > 0 and phases == {"legacy_unscoped"}:
            final_phase = None
        elif "initial" in phases:
            final_phase = "initial"
        else:
            final_phase = "legacy_unscoped"
        routes = []
        for item in artifact.route_checks:
            if final_phase is None:
                routes = None
                break
            if item.validation_phase != final_phase or item.leg_type != "intra_city_poi_leg":
                continue
            if item.data_available and item.feasible.status != "known":
                raise CaptureGuardError("route_feasibility_unknown")
            checked = (item.data_available and item.verification_status == "verified"
                       and item.feasible.status == "known")
            routes.append(RouteCheck(
                day_index=item.day_index, origin=item.origin_stable_id,
                destination=item.destination_stable_id,
                status="checked" if checked else "unavailable",
                feasible=bool(item.feasible.value) if checked else None,
                duration_s=int(item.duration_s.value) if item.duration_s.status == "known" else None,
                distance_m=float(item.distance_m.value) if item.distance_m.status == "known" else None,
                data_source=item.provider if checked else None,
                validation_pass_id=item.validation_pass_id,
                validation_phase=item.validation_phase, leg_type=item.leg_type,
            ))
    return ArtifactEvaluationInput(
        output=copy.deepcopy(artifact.final_trip_plan.value), usage=usage,
        latency_ms=(int(artifact.total_latency_ms.value)
                    if artifact.total_latency_ms.status == "known" else None),
        route_checks=routes,
        revision_before=artifact.revision.before if artifact.revision.status == "known" else None,
        revision_target_risk_ids=(artifact.revision.target_risk_ids
                                  if artifact.revision.status == "known" else []),
        revision_after=artifact.revision.after if artifact.revision.status == "known" else None,
        revision_revalidation_result=(artifact.revision.revalidation_result
                                      if artifact.revision.status == "known" else None),
        patch_before=artifact.patch.before if artifact.patch.status == "known" else None,
        patch_after=artifact.patch.after if artifact.patch.status == "known" else None,
    )
