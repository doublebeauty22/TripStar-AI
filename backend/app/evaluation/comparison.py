"""Version-aware paired comparison and release decision."""

from .models import EvalRunArtifact, MetricDelta, PairedComparison


class ComparisonError(ValueError):
    pass


LOWER_BETTER = {"unverified_poi_rate", "actionable_risk_count", "logical_llm_calls", "prompt_tokens", "completion_tokens", "total_tokens", "latency_ms"}
HARD = {"schema_valid", "date_day_consistency", "budget_arithmetic_consistency", "unaffected_day_preservation_rate"}


def _validate_pair(baseline: EvalRunArtifact, candidate: EvalRunArtifact):
    pairs = [
        (baseline.metadata.case_id, candidate.metadata.case_id, "case_id"),
        (baseline.golden_case_version, candidate.golden_case_version, "golden_case_version"),
        (baseline.metadata.fixture_set_version, candidate.metadata.fixture_set_version, "fixture_set_version"),
        (baseline.fixture_hashes, candidate.fixture_hashes, "fixture_hashes"),
        (baseline.metric_policy_version, candidate.metric_policy_version, "metric_policy_version"),
    ]
    for left, right, name in pairs:
        if left != right:
            raise ComparisonError(f"paired comparison rejected: {name} mismatch")


def compare_runs(baseline: EvalRunArtifact, candidate: EvalRunArtifact, comparison_id: str) -> PairedComparison:
    _validate_pair(baseline, candidate)
    base = {item.metric: item for item in baseline.metrics}
    cand = {item.metric: item for item in candidate.metrics}
    deltas = []
    improvements, regressions, unchanged = [], [], []
    for name in base:
        left, right = base[name], cand[name]
        delta = None
        if left.status == "known" and right.status == "known":
            delta = right.value - left.value
            if delta == 0:
                classification = "unchanged"
            else:
                positive = delta > 0
                better = (not positive) if name in LOWER_BETTER else positive
                classification = "improvement" if better else "regression"
        elif left.status == "known" and right.status != "known":
            classification = "known_to_unknown"
        elif left.status != "known" and right.status == "known":
            classification = "unknown_to_known"
        elif left.status == right.status:
            classification = "unchanged"
        else:
            classification = "not_comparable"
        deltas.append(MetricDelta(metric=name, baseline_status=left.status, candidate_status=right.status,
                                  baseline_value=left.value, candidate_value=right.value, delta=delta,
                                  classification=classification))
        if classification in {"regression", "known_to_unknown"}: regressions.append(name)
        elif classification in {"improvement", "unknown_to_known"}: improvements.append(name)
        elif classification == "unchanged": unchanged.append(name)

    blocking = []
    investigate = []
    for item in deltas:
        if item.candidate_status == "failed":
            blocking.append(f"metric integrity failure: {item.metric}")
            continue
        if item.metric in HARD and item.candidate_status == "known" and item.candidate_value == 0 and item.baseline_value != 0:
            blocking.append(f"hard gate regression: {item.metric}")
        if item.metric == "explicit_constraint_satisfaction_rate" and item.classification == "regression" and item.candidate_value == 0:
            blocking.append("blocking explicit constraint regression")
        if item.metric == "grounded_poi_rate" and item.classification == "regression" and item.candidate_value == 0:
            blocking.append("critical grounding regression")
        if item.classification == "known_to_unknown":
            investigate.append(f"coverage regression: {item.metric}")
        elif item.classification == "regression" and not any(item.metric in text for text in blocking):
            investigate.append(f"non-hard regression: {item.metric}")
    candidate_labels = {finding.label for finding in candidate.badcases if finding.evidence_type == "automatic" and finding.detected}
    baseline_labels = {finding.label for finding in baseline.badcases if finding.evidence_type == "automatic" and finding.detected}
    if "patch_scope_drift" in candidate_labels - baseline_labels:
        blocking.append("new patch_scope_drift badcase")
    decision = "BLOCK" if blocking else "INVESTIGATE" if investigate else "PASS"
    return PairedComparison(
        comparison_id=comparison_id, case_id=baseline.metadata.case_id,
        baseline_run_id=baseline.metadata.eval_run_id, candidate_run_id=candidate.metadata.eval_run_id,
        metric_deltas=deltas, improvements=improvements, regressions=regressions,
        unchanged=unchanged, release_decision=decision, blocking_reasons=blocking,
        investigation_reasons=investigate, thresholds_provisional=True,
    )
