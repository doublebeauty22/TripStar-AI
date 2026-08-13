"""Deterministic and explicitly non-deterministic badcase classification."""

from .models import ArtifactEvaluationInput, BadcaseFinding, EvalCase, MetricResult


def label_badcases(case: EvalCase, artifact: ArtifactEvaluationInput, metrics: list[MetricResult]) -> list[BadcaseFinding]:
    values = {item.metric: item for item in metrics}
    def detected(label, condition, reason):
        return BadcaseFinding(label=label, evidence_type="automatic", detected=bool(condition), reason=reason)
    def human(label, reason):
        return BadcaseFinding(label=label, evidence_type="human_required", reason=reason)
    def skipped(label, reason):
        return BadcaseFinding(label=label, evidence_type="not_evaluated", reason=reason)

    def is_failure(name):
        item = values[name]
        return item.status == "known" and item.value == 0

    route_checks = artifact.route_checks or []
    findings = [
        detected("constraint_violation", is_failure("explicit_constraint_satisfaction_rate"), "deterministically measurable explicit constraint result"),
        detected("ungrounded_poi", values["grounded_poi_rate"].status == "known" and values["grounded_poi_rate"].value < 1, "one or more POIs are not grounded"),
        detected("route_infeasible", any(item.status == "checked" and item.feasible is False for item in route_checks), "checked route leg is infeasible"),
        detected("route_unavailable", any(item.status == "unavailable" for item in route_checks) or (values["route_check_coverage"].status == "known" and values["route_check_coverage"].value == 0), "one or more required routes are unavailable"),
        detected("budget_overrun", is_failure("budget_limit_satisfaction"), "plan exceeds explicit budget limit"),
        detected("budget_inconsistent", is_failure("budget_arithmetic_consistency"), "budget total differs from component sum"),
        detected("revision_failed", values["revision_risk_resolution_rate"].status == "known" and values["revision_risk_resolution_rate"].value < 1, "one or more targeted revision risks remain"),
        detected("provenance_missing", values["provenance_coverage"].status == "known" and values["provenance_coverage"].value < 1, "one or more applicable structured facts lack provenance"),
        detected("patch_scope_drift", values["unaffected_day_preservation_rate"].status == "known" and values["unaffected_day_preservation_rate"].value < 1, "one or more protected days changed"),
        skipped("excessive_cost", "comparison threshold is provisional/config-required"),
        skipped("excessive_latency", "comparison threshold is provisional/config-required"),
        human("preference_miss", "requires human preference review"),
        human("unsupported_fact", "unrestricted prose has no claim/evidence contract in v1"),
        human("overpacked", "v1 requires human pacing review"),
        human("underpacked", "v1 requires human pacing review"),
        human("unnecessary_revision", "requires human judgment of repair necessity"),
    ]
    return findings
