"""Cross-case aggregation for a real, controlled Planner baseline."""

from collections import Counter
from datetime import datetime, timezone
from statistics import mean, median
from typing import Callable, Iterable

from .models import (
    AggregateMetric, BaselineManifest, BatchEvaluationReport, EvalCase,
    EvalRunArtifact, HumanReviewRecord, MetricResult, ScenarioBreakdown,
)


class BaselineError(ValueError):
    pass


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def aggregate_metric(name: str, metrics: list[MetricResult], failed_cases: int = 0) -> AggregateMetric:
    relevant = [item for item in metrics if item.metric == name]
    known = [item for item in relevant if item.status == "known"]
    unknown = sum(item.status == "unknown" for item in relevant)
    not_applicable = sum(item.status == "not_applicable" for item in relevant)
    metric_failed = sum(item.status == "failed" for item in relevant)
    values = [float(item.value) for item in known]
    weighted = [item for item in known if item.numerator is not None and item.denominator is not None]
    numerator = denominator = aggregate = None
    if weighted and len(weighted) == len(known):
        numerator = sum(float(item.numerator) for item in weighted)
        denominator = sum(float(item.denominator) for item in weighted)
        aggregate = numerator / denominator if denominator else None
    elif values:
        numerator = sum(values)
        denominator = float(len(values))
        aggregate = numerator / denominator
    return AggregateMetric(
        metric=name, known_cases=len(known), unknown_cases=unknown,
        not_applicable_cases=not_applicable, failed_cases=failed_cases + metric_failed,
        aggregate_value=aggregate, numerator=numerator, denominator=denominator,
        mean=mean(values) if values else None, median=median(values) if values else None,
        p90=_percentile(values, .90), p95=_percentile(values, .95),
        minimum=min(values) if values else None, maximum=max(values) if values else None,
    )


def _aggregate_runs(runs: list[EvalRunArtifact]) -> list[AggregateMetric]:
    completed = [run for run in runs if run.run_status == "completed"]
    failed = len(runs) - len(completed)
    names = [item.metric for item in completed[0].metrics] if completed else []
    all_metrics = [metric for run in completed for metric in run.metrics]
    return [aggregate_metric(name, all_metrics, failed) for name in names]


def _badcases(runs: Iterable[EvalRunArtifact], evidence_type: str) -> Counter:
    return Counter(
        finding.label for run in runs for finding in run.badcases
        if finding.evidence_type == evidence_type
        and (finding.detected is True if evidence_type == "automatic" else True)
    )


def _breakdown(group: str, pairs: list[tuple[EvalCase, EvalRunArtifact]]) -> ScenarioBreakdown:
    runs = [run for _, run in pairs]
    return ScenarioBreakdown(
        group=group, case_ids=[case.case_id for case, _ in pairs],
        metrics=_aggregate_runs(runs), automatic_badcase_frequency=dict(_badcases(runs, "automatic")),
    )


def _group(pairs, key: Callable[[EvalCase], Iterable[str]]) -> list[ScenarioBreakdown]:
    groups = {}
    for case, run in pairs:
        for value in key(case):
            groups.setdefault(value, []).append((case, run))
    return [_breakdown(name, groups[name]) for name in sorted(groups)]


def _comparison_groups(pairs: list[tuple[EvalCase, EvalRunArtifact]]) -> list[ScenarioBreakdown]:
    """Build the product cohorts required by Phase 3C, including control complements."""
    definitions = {
        "pace_relaxed": lambda tags: "relaxed" in tags,
        "pace_intensive": lambda tags: "intensive" in tags,
        "budget_constrained": lambda tags: "budget_limit" in tags,
        "budget_unconstrained": lambda tags: "budget_limit" not in tags,
        "mobility": lambda tags: "mobility" in tags,
        "mobility_normal": lambda tags: "mobility" not in tags,
        "food_constraint": lambda tags: "food_constraint" in tags,
        "food_normal": lambda tags: "food_constraint" not in tags,
        "provider_degraded": lambda tags: bool({
            "xhs_unavailable", "google_places_partial", "google_places_unavailable",
            "route_unavailable", "weather_fallback",
        } & tags),
        "provider_normal": lambda tags: not ({
            "xhs_unavailable", "google_places_partial", "google_places_unavailable",
            "route_unavailable", "weather_fallback",
        } & tags),
        "revision_trigger": lambda tags: "revision_trigger" in tags,
        "no_revision_trigger": lambda tags: "revision_trigger" not in tags,
    }
    output = []
    for name, predicate in definitions.items():
        members = [(case, run) for case, run in pairs if predicate(set(case.scenario_tags))]
        output.append(_breakdown(name, members))
    return output


def build_batch_report(cases: list[EvalCase], runs: list[EvalRunArtifact], *,
                       baseline_id: str, code_revision: str,
                       planner_version: str = "unavailable",
                       prompt_version: str = "unavailable",
                       model: str = "unavailable",
                       require_real_artifacts: bool = True) -> BatchEvaluationReport:
    case_map = {case.case_id: case for case in cases}
    run_map = {run.metadata.case_id: run for run in runs}
    if len(run_map) != len(runs):
        raise BaselineError("duplicate case runs")
    unknown_ids = set(run_map) - set(case_map)
    if unknown_ids:
        raise BaselineError(f"runs reference non-Golden cases: {sorted(unknown_ids)}")
    pairs = [(case, run_map[case.case_id]) for case in cases if case.case_id in run_map]
    identities = {(run.metadata.planner_version, run.metadata.prompt_version, run.metadata.model,
                   run.metadata.fixture_set_version, run.metric_policy_version) for _, run in pairs}
    if len(identities) > 1:
        raise BaselineError("batch mixes planner/model/fixture/policy identities")
    all_real = bool(pairs) and all(run.artifact_origin == "real_planner" for _, run in pairs)
    complete_case_set = len(pairs) == len(cases)
    established = all_real and complete_case_set and all(run.run_status == "completed" for _, run in pairs)
    if require_real_artifacts and not all_real and pairs:
        established = False
    if identities:
        planner_version, prompt_version, model, fixture_version, _ = next(iter(identities))
    else:
        fixture_version = "fixtures.v1"
    reason = None if established else (
        "real controlled Planner artifacts are missing for one or more Golden Cases"
    )
    manifest = BaselineManifest(
        baseline_id=baseline_id, baseline_status="established" if established else "not_established",
        reason=reason, planner_version=planner_version, prompt_version=prompt_version,
        model=model, code_revision=code_revision, fixture_set_version=fixture_version,
        runner_version="runner.v1", generated_at=datetime.now(timezone.utc).isoformat(),
        case_ids=[case.case_id for case, _ in pairs],
        fixture_hashes_by_case={case.case_id: run.fixture_hashes for case, run in pairs},
    )
    aggregate = _aggregate_runs([run for _, run in pairs])
    cases_unknown = sum(any(metric.status == "unknown" for metric in run.metrics) for _, run in pairs if run.run_status == "completed")
    automatic = _badcases((run for _, run in pairs), "automatic")
    human = _badcases((run for _, run in pairs), "human_required")
    not_evaluated = _badcases((run for _, run in pairs), "not_evaluated")
    top = [label for label, _ in automatic.most_common()]
    return BatchEvaluationReport(
        manifest=manifest, cases_total=len(cases), cases_evaluated=len(pairs),
        cases_failed=sum(run.run_status != "completed" for _, run in pairs),
        cases_unknown=cases_unknown, aggregate_metrics=aggregate,
        automatic_badcase_frequency=dict(automatic), human_required_frequency=dict(human),
        not_evaluated_frequency=dict(not_evaluated),
        scenario_breakdown=(
            _group(pairs, lambda case: case.scenario_tags) + _comparison_groups(pairs)
        ),
        language_breakdown=_group(pairs, lambda case: [case.language]),
        city_scope_breakdown=_group(pairs, lambda case: ["multi_city" if "multi_city" in case.scenario_tags else "single_city"]),
        human_reviews=[HumanReviewRecord(case_id=case.case_id, planner_version=planner_version) for case in cases],
        top_automatic_badcases=top,
    )
