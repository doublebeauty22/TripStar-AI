"""Adapter that observes the existing MultiAgentTripPlanner execution path."""

import time
from datetime import datetime, timezone

from ..agents.trip_planner_agent import get_trip_planner_agent
from ..services.llm_service import LLMCallBudgetExceeded, llm_execution
from ..services.planner_observation import capture_planner_observations
from ..models.schemas import WeatherResult, has_valid_verified_coordinates
from .capture_models import (
    CaptureUsage, CapturedRouteCheck, CapturedValue, PatchCapture, ProductionCaptureResult,
    ProviderStatusCapture, RevisionCapture,
)


class ProductionExecutionError(RuntimeError):
    """Safe telemetry carrier; never includes raw model/provider output."""
    def __init__(self, failure_type: str, failed_stage: str | None, usage: dict, elapsed_ms: int):
        super().__init__(failure_type)
        self.failure_type = failure_type
        self.failed_stage = failed_stage
        self.usage = usage
        self.elapsed_ms = elapsed_ms


def _known(value):
    return CapturedValue(status="known", value=value)


def _eligible_final_route_legs(plan) -> int:
    def verified(poi):
        return bool(poi.poi_match_status == "verified"
                    and poi.map_data_source in {"google_places", "amap"}
                    and (poi.place_id or poi.poi_id)
                    and has_valid_verified_coordinates(poi.location))
    return sum(sum(verified(a) and verified(b) for a, b in zip(day.attractions, day.attractions[1:]))
               for day in plan.days)


def build_revision_capture(plan, revision_events) -> RevisionCapture:
    pacing = next((item for item in revision_events if item["event"] == "pacing_revision_result"), None)
    proposal = next((item for item in revision_events if item["event"] == "pacing_revision_proposal"), None)
    initial = next((item for item in revision_events if item["event"] == "initial_validation"), None)
    if pacing:
        return RevisionCapture(
            status="known", revision_kind="targeted_pacing",
            revision_status=pacing["status"], before=pacing["before"],
            candidate=pacing.get("candidate"), after=pacing["after"],
            target_risk_ids=pacing["target_risk_ids"],
            affected_day_indices=pacing["affected_day_indices"],
            protected_day_indices=pacing["protected_day_indices"],
            protected_day_equality=pacing["protected_day_equality"],
            revision_instructions_metadata=(proposal["revision_instructions"] if proposal else []),
            initial_validation_result=initial["validation_result"] if initial else None,
            initial_risks=([risk.model_dump(mode="json") for risk in initial["risks"]]
                           if initial else []),
            revalidation_result=pacing.get("post_validation"),
            post_pacing_risk_ids=pacing["post_pacing_risk_ids"],
            resolution_outcome=pacing["resolution_outcome"],
            failure_reason=pacing.get("failure_reason"),
            pacing_policy_version=pacing.get("pacing_policy_version"),
            pacing_revision_metrics=pacing.get("metrics", {}),
            grounding_outcome=pacing.get("grounding_outcome"),
            grounding_details=pacing.get("grounding_details", {}),
        )
    critic = next((item for item in revision_events if item["event"] == "critic"), None)
    enrichment = next((item for item in revision_events if item["event"] == "post_revision_enrichment"), None)
    post = next((item for item in revision_events if item["event"] == "post_revision_validation"), None)
    if plan.revision_count and initial and critic and post:
        return RevisionCapture(
            status="known", before=initial["plan"], target_risk_ids=critic["target_risk_ids"],
            after=post["plan"], initial_validation_result=initial["validation_result"],
            initial_risks=[risk.model_dump(mode="json") for risk in initial["risks"]],
            protected_elements=critic["protected_elements"],
            revision_instructions_metadata=critic["revision_instructions"],
            post_revision_enrichment_state=enrichment["state"] if enrichment else "unknown",
            revalidation_result=post["validation_result"],
            post_revision_risks=[risk.model_dump(mode="json") for risk in post["risks"]],
        )
    if plan.revision_count:
        return RevisionCapture(status="unknown", reason="revision observation set is incomplete")
    return RevisionCapture(status="not_applicable", reason="revision was not triggered")


def build_revision_capture_safely(plan, revision_events, usage, elapsed_ms) -> RevisionCapture:
    """Preserve already-known execution telemetry when capture construction fails."""
    try:
        return build_revision_capture(plan, revision_events)
    except Exception as exc:
        raise ProductionExecutionError(
            "capture_validation_failure", "capture_serialization", usage, elapsed_ms,
        ) from exc


def build_weather_capture(weather_observations):
    statuses, snapshots = [], {}
    for provider in ("google_weather", "amap"):
        captured = [item["result"] for item in weather_observations if item["provider"] == provider]
        typed = [item for item in captured if isinstance(item, WeatherResult)]
        if not captured:
            statuses.append(ProviderStatusCapture(provider=provider, status="not_called",
                reason="provider was not called", data_available=False, evidence_count=0,
                summary={"endpoint": "weather"}))
            continue
        usable = sum(item.data_available for item in typed)
        malformed = len(captured) - len(typed)
        if usable == len(captured):
            degraded_fallback = provider == "amap" and any(item.degraded for item in typed)
            state = "degraded" if degraded_fallback else "success"
            reason = "fallback provider result consumed" if degraded_fallback else None
        elif usable:
            state, reason = "partial", "some weather results were unavailable or malformed"
        else:
            state, reason = "unavailable", "weather result unavailable or malformed"
        statuses.append(ProviderStatusCapture(provider=provider, status=state, reason=reason,
            data_available=usable > 0, evidence_count=sum(len(item.days) for item in typed),
            summary={"endpoint": "weather", "calls": len(captured), "usable": usable,
                     "malformed": malformed}))
        snapshots[provider] = {"endpoint": "weather", "results": [item.model_dump(mode="json") for item in typed],
                               "malformed_count": malformed}
    return statuses, snapshots


async def execute_production_planner(case, context) -> ProductionCaptureResult:
    """Run the real Planner once; it does not implement snapshot replay itself."""
    if context.get("mode") == "replay":
        raise RuntimeError("production_replay_adapter_not_configured")
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    with capture_planner_observations() as observations:
        try:
            with llm_execution(case.case_id, max_calls=context["max_llm_calls"],
                               max_total_tokens=context.get("max_total_tokens")) as observed:
                plan = await get_trip_planner_agent().plan_trip(case.trip_request)
                usage = observed.snapshot()
        except Exception as exc:
            usage = observed.snapshot()
            last = usage.get("admission_events", [{}])[-1] if usage.get("admission_events") else {}
            if usage.get("budget_exceeded") or last.get("admitted") is False:
                raise LLMCallBudgetExceeded(
                    "evaluation LLM budget stopped production-compatible execution",
                    snapshot=usage,
                    failed_before_stage=last.get("stage") if last.get("admitted") is False else None,
                    failed_after_stage=usage.get("llm_stage") if usage.get("budget_exceeded") else None,
                ) from exc
            message = str(exc).casefold()
            failure_type = ("planner_output_parse_failure" if any(token in message for token in
                            ("json", "响应中未找到", "did not contain")) else
                            "provider_execution_error" if "provider" in message else
                            "planner_execution_error")
            raise ProductionExecutionError(
                failure_type, usage.get("llm_stage"), usage,
                int((time.monotonic() - started) * 1000),
            ) from exc
    latency = int((time.monotonic() - started) * 1000)
    completed_at = datetime.now(timezone.utc).isoformat()

    statuses = []
    snapshots = {}
    xhs_metadata = {"cities": []}
    if plan.xhs_research:
        available = sum(item.status == "available" for item in plan.xhs_research)
        state = "success" if available == len(plan.xhs_research) else ("partial" if available else "unavailable")
        reason = None if state == "success" else "one or more XHS city results unavailable"
        count = sum(len(item.evidence) for item in plan.xhs_research)
        statuses.append(ProviderStatusCapture(provider="xhs", status=state,
            reason=reason, data_available=count > 0, evidence_count=count,
            summary={"cities": len(plan.xhs_research), "available": available}))
        xhs_metadata = {"cities": [{"status": item.status, "verification_status": item.verification_status,
            "reason": item.reason, "evidence": [{"note_id": evidence.note_id,
            "source_url": evidence.source_url, "status": evidence.status} for evidence in item.evidence]}
            for item in plan.xhs_research]}
        snapshots["xhs"] = xhs_metadata
    else:
        statuses.append(ProviderStatusCapture(provider="xhs", status="not_called",
            reason="no XHS research observation was present", data_available=False,
            evidence_count=0, summary={"cities": 0, "available": 0}))

    weather_statuses, weather_snapshots = build_weather_capture(observations["weather"])
    statuses.extend(weather_statuses); snapshots.update(weather_snapshots)

    attractions = [poi for day in plan.days for poi in day.attractions]
    verified = [poi for poi in attractions if getattr(poi, "poi_match_status", None) == "verified"]
    if attractions:
        state = "success" if len(verified) == len(attractions) else ("partial" if verified else "unavailable")
        statuses.append(ProviderStatusCapture(provider="google_places", status=state,
            reason=None if state == "success" else "one or more POIs were not verified",
            data_available=bool(verified), evidence_count=len(verified),
            summary={"eligible": len(attractions), "verified": len(verified)}))
        snapshots["google_places"] = {"places": [{"name": poi.name,
            "place_id": getattr(poi, "place_id", None),
            "match_status": getattr(poi, "poi_match_status", None),
            "location": poi.location.model_dump(mode="json") if poi.location else None}
            for poi in attractions]}
    else:
        statuses.append(ProviderStatusCapture(provider="google_places", status="not_called",
            reason="no POIs were eligible for enrichment", data_available=False,
            evidence_count=0, summary={"eligible": 0, "verified": 0}))

    revision_events = observations["revisions"]
    post_event = next((item for item in revision_events if item["event"] == "post_revision_validation"), None)
    final_phase = "post_revision" if post_event else "initial"

    route_checks = []
    for item in observations["routes"]:
        available = bool(item["data_available"])
        route_checks.append(CapturedRouteCheck(
            day_index=item["day_index"], origin_stable_id=item["origin_stable_id"],
            destination_stable_id=item["destination_stable_id"], provider="google_directions",
            request_attempted=item["request_attempted"], data_available=available,
            distance_m=_known(item["distance_m"]) if available else CapturedValue(status="unknown", reason=item["reason"]),
            duration_s=_known(item["duration_s"]) if available else CapturedValue(status="unknown", reason=item["reason"]),
            feasible=_known(item["feasible"]) if available else CapturedValue(status="unknown", reason=item["reason"]),
            route_mode=item["route_mode"], verification_status="verified" if available else "unavailable",
            reason=None if available else item["reason"],
            validation_pass_id=item.get("validation_pass_id", "legacy_unscoped"),
            validation_phase=item.get("validation_phase", "legacy_unscoped"),
            leg_type=item.get("leg_type", "intra_city_poi_leg"),
        ))
    final_route_checks = [item for item in route_checks if item.validation_phase == final_phase]
    attempted = [item for item in final_route_checks if item.request_attempted]
    available = [item for item in final_route_checks if item.data_available]
    direction_state = ("not_called" if not attempted else
                       "success" if len(available) == len(final_route_checks) else
                       "partial" if available else "unavailable")
    statuses.append(ProviderStatusCapture(provider="google_directions", status=direction_state,
        reason=None if direction_state == "success" else
               "provider was not called" if direction_state == "not_called" else "one or more route legs unavailable",
        data_available=bool(available), evidence_count=len(available),
        summary={"eligible_final_intra_city_legs": _eligible_final_route_legs(plan),
                 "observed_final_legs": len(final_route_checks), "attempted": len(attempted),
                 "available": len(available), "validation_phase": final_phase}))
    if attempted:
        snapshots["google_directions"] = {"routes": [{
            "day_index": item.day_index, "origin_stable_id": item.origin_stable_id,
            "destination_stable_id": item.destination_stable_id, "route_mode": item.route_mode,
            "request_attempted": item.request_attempted, "data_available": item.data_available,
            "distance_m": item.distance_m.model_dump(mode="json"),
            "duration_s": item.duration_s.model_dump(mode="json"),
            "feasible": item.feasible.model_dump(mode="json"),
            "verification_status": item.verification_status, "reason": item.reason,
            "validation_pass_id": item.validation_pass_id,
            "validation_phase": item.validation_phase, "leg_type": item.leg_type,
        } for item in final_route_checks]}

    hotel_observations = observations["hotels"]
    if hotel_observations:
        available_hotels = sum(item["candidate_count"] for item in hotel_observations)
        hotel_states = {item["status"] for item in hotel_observations}
        hotel_state = ("unavailable" if not available_hotels else
                       "degraded" if "degraded" in hotel_states else
                       "partial" if "unavailable" in hotel_states else "success")
        statuses.append(ProviderStatusCapture(
            provider=hotel_observations[0]["provider"], status=hotel_state,
            reason=None if hotel_state == "success" else "one or more hotel searches degraded or unavailable",
            data_available=available_hotels > 0, evidence_count=available_hotels,
            summary={"endpoint": "hotel_search", "cities": len(hotel_observations),
                     "candidate_count": available_hotels},
        ))
        snapshots[hotel_observations[0]["provider"]] = {"searches": hotel_observations}

    stage_calls = usage.get("stage_calls", {})
    stage_usage = []
    for stage, calls in stage_calls.items():
        from .capture_models import StageUsage
        stage_usage.append(StageUsage(stage=stage, logical_llm_calls=_known(calls),
            prompt_tokens=CapturedValue(status="unknown", reason="tokens are aggregated across stages"),
            completion_tokens=CapturedValue(status="unknown", reason="tokens are aggregated across stages"),
            total_tokens=CapturedValue(status="unknown", reason="tokens are aggregated across stages"),
            retries=CapturedValue(status="unknown", reason="retries are aggregated across stages"),
            latency_ms=CapturedValue(status="unknown", reason="stage latency is not instrumented")))
    captured_usage = CaptureUsage(
        logical_llm_calls=_known(usage["logical_llm_calls"]),
        prompt_tokens=_known(usage["prompt_tokens"]), completion_tokens=_known(usage["completion_tokens"]),
        total_tokens=_known(usage["total_tokens"]), retries=_known(usage["retry_count"]), stages=stage_usage,
    )
    revision = build_revision_capture_safely(plan, revision_events, usage, latency)
    return ProductionCaptureResult(
        final_trip_plan=plan, model=usage.get("model") or None,
        final_validation_result={"status": plan.validation_status} if plan.validation_status else None,
        risks=[risk.model_dump(mode="json") for risk in plan.risks],
        provider_statuses_complete=True, provider_statuses=statuses,
        xhs_evidence_metadata=xhs_metadata, route_checks=route_checks, route_checks_complete=True,
        usage=captured_usage, total_latency_ms=latency, stage_latency_ms=None,
        execution_started_at=started_at, execution_completed_at=completed_at,
        revision=revision, patch=PatchCapture(status="not_applicable", reason="initial generation is not a patch"),
        provider_snapshots=snapshots,
        pacing_policy_version=plan.pacing_policy_version,
        daily_load_assessments=list(plan.daily_load_assessments),
        pacing_risk_ids=[risk.id for risk in plan.risks if risk.type == "pacing"],
        validation_pass_scope=(post_event["validation_result"].get("validation_pass_scope")
                               if post_event else "validation.initial"),
    )


async def execute_production_patch(case, context) -> ProductionCaptureResult:
    """Reuse the production interpreter/engine/enrichment/validator as a capture seam."""
    from ..models.schemas import TripPatch, TripPlan
    from ..services.trip_patch_service import TripPatchEngine, get_trip_patch_interpreter
    from ..services.trip_validator_service import get_trip_validator_service
    from ..services.planner_observation import validation_pass

    if not case.patch_instruction:
        raise ValueError("patch case requires patch_instruction")
    base = context.get("base_plan")
    if base is None:
        raise ValueError("patch capture requires a controlled base_plan")
    before = base if isinstance(base, TripPlan) else TripPlan.model_validate(base)
    before = before.model_copy(deep=True)
    started = time.monotonic(); started_at = datetime.now(timezone.utc).isoformat()
    typed = context.get("typed_patch")
    if typed is None:
        with llm_execution(case.case_id, max_calls=1,
                           max_total_tokens=context.get("max_total_tokens")) as observed:
            patch = await get_trip_patch_interpreter().interpret(
                case.patch_instruction, before, case.trip_request
            )
        usage_snapshot = observed.snapshot()
    else:
        patch = typed if isinstance(typed, TripPatch) else TripPatch.model_validate(typed)
        usage_snapshot = {"logical_llm_calls": 0, "prompt_tokens": 0,
                          "completion_tokens": 0, "total_tokens": 0,
                          "retry_count": 0, "model": None, "stage_calls": {}}
    if patch.requires_regeneration:
        raise ValueError("patch_requires_regeneration")
    engine = TripPatchEngine()
    after, affected = engine.apply_patch(before, patch)
    enricher = context.get("patch_enricher")
    if enricher is None:
        from ..api.routes.trip import _enrich_patch_pois
        enricher = _enrich_patch_pois
    after = await enricher(after, patch)
    with capture_planner_observations() as observations:
        with validation_pass("validation.post_patch", "post_patch"):
            validation = await get_trip_validator_service().validate(case.trip_request, after)
    after.risks = validation.risks; after.validation_status = validation.status
    after.plan_version = max(before.plan_version, 1) + 1
    diff = engine.compare_before_after(before, after)
    if diff.changed_day_indices != affected:
        raise ValueError("patch_scope_validation_failure")

    route_checks = []
    for item in observations["routes"]:
        available = bool(item["data_available"])
        route_checks.append(CapturedRouteCheck(
            day_index=item["day_index"], origin_stable_id=item["origin_stable_id"],
            destination_stable_id=item["destination_stable_id"], provider="google_directions",
            request_attempted=item["request_attempted"], data_available=available,
            distance_m=_known(item["distance_m"]) if available else CapturedValue(status="unknown", reason=item["reason"]),
            duration_s=_known(item["duration_s"]) if available else CapturedValue(status="unknown", reason=item["reason"]),
            feasible=_known(item["feasible"]) if available else CapturedValue(status="unknown", reason=item["reason"]),
            route_mode=item["route_mode"], verification_status="verified" if available else "unavailable",
            reason=None if available else item["reason"], validation_pass_id=item["validation_pass_id"],
            validation_phase="post_patch", leg_type="intra_city_poi_leg",
        ))
    checked = [item for item in route_checks if item.data_available]
    affected_pois = [poi for index in affected for poi in after.days[index].attractions]
    verified_pois = [poi for poi in affected_pois if poi.poi_match_status == "verified"]
    statuses = [ProviderStatusCapture(
        provider="google_directions", status=("success" if route_checks and len(checked) == len(route_checks)
        else "partial" if checked else "not_called" if not any(x.request_attempted for x in route_checks) else "unavailable"),
        reason=None if route_checks and len(checked) == len(route_checks) else
               "provider was not called" if not any(x.request_attempted for x in route_checks) else "one or more patch routes unavailable",
        data_available=bool(checked), evidence_count=len(checked),
        summary={"eligible_final_intra_city_legs": _eligible_final_route_legs(after),
                 "observed_final_legs": len(route_checks), "attempted": sum(x.request_attempted for x in route_checks),
                 "available": len(checked), "validation_phase": "post_patch"},
    ), ProviderStatusCapture(
        provider="google_places",
        status=("success" if affected_pois and len(verified_pois) == len(affected_pois)
                else "partial" if verified_pois else "not_called" if not affected_pois else "unavailable"),
        reason=None if affected_pois and len(verified_pois) == len(affected_pois) else
               "patch did not affect POIs" if not affected_pois else "one or more affected POIs were not verified",
        data_available=bool(verified_pois), evidence_count=len(verified_pois),
        summary={"endpoint":"patch_poi_enrichment", "affected_pois":len(affected_pois),
                 "verified":len(verified_pois)},
    )]
    for provider in ("xhs", "google_weather", "amap", "hotel_google_places", "hotel_amap"):
        statuses.append(ProviderStatusCapture(
            provider=provider, status="not_called", reason="provider is outside local patch path",
            data_available=False, evidence_count=0, summary={"endpoint":"patch"},
        ))
    captured_usage = CaptureUsage(
        logical_llm_calls=_known(usage_snapshot["logical_llm_calls"]),
        prompt_tokens=_known(usage_snapshot["prompt_tokens"]),
        completion_tokens=_known(usage_snapshot["completion_tokens"]),
        total_tokens=_known(usage_snapshot["total_tokens"]),
        retries=_known(usage_snapshot["retry_count"]), stages=[],
    )
    return ProductionCaptureResult(
        final_trip_plan=after, model=usage_snapshot.get("model"),
        final_validation_result=validation.model_dump(mode="json"),
        risks=[risk.model_dump(mode="json") for risk in validation.risks],
        provider_statuses_complete=True, provider_statuses=statuses,
        xhs_evidence_metadata={"status": "not_called_for_patch"},
        route_checks=route_checks, route_checks_complete=True, usage=captured_usage,
        execution_started_at=started_at, execution_completed_at=datetime.now(timezone.utc).isoformat(),
        total_latency_ms=int((time.monotonic() - started) * 1000),
        revision=RevisionCapture(status="not_applicable", reason="patch execution does not run revision"),
        patch=PatchCapture(
            status="known", before=before, after=after,
            affected_day_indices=affected, protected_day_indices=patch.protected_day_indices,
            patch_request=case.patch_instruction,
            typed_operations=[item.model_dump(mode="json") for item in patch.operations],
            validation_result=validation.model_dump(mode="json"),
            plan_version_before=before.plan_version, plan_version_after=after.plan_version,
            base_artifact_identity=context.get("base_artifact_identity"),
            base_artifact_hash=context.get("base_artifact_hash"),
        ), provider_snapshots={},
        pacing_policy_version=validation.pacing_policy_version,
        daily_load_assessments=list(validation.daily_load_assessments),
        pacing_risk_ids=[risk.id for risk in validation.risks if risk.type == "pacing"],
        validation_pass_scope=validation.validation_pass_scope,
    )
