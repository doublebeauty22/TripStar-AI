"""Phase 3C batch JSON/Markdown serialization."""

from pathlib import Path

from .fixtures import write_canonical_json
from .models import BatchEvaluationReport


def write_batch_json(path: Path, report: BatchEvaluationReport):
    write_canonical_json(path, report.model_dump(mode="json"))


def batch_markdown(report: BatchEvaluationReport) -> str:
    metric_rows = []
    for item in report.aggregate_metrics:
        value = f"{item.aggregate_value:.4f}" if item.aggregate_value is not None else "unknown/N/A"
        fraction = f"{item.numerator:g}/{item.denominator:g}" if item.numerator is not None and item.denominator is not None else "—"
        metric_rows.append(f"| `{item.metric}` | {value} | {fraction} | {item.known_cases} | {item.unknown_cases} | {item.not_applicable_cases} | {item.failed_cases} |")
    badcases = "\n".join(f"- `{label}`: {count}" for label, count in report.automatic_badcase_frequency.items()) or "- None"
    scenario = "\n".join(f"- **{item.group}**: {len(item.case_ids)} cases" for item in report.scenario_breakdown) or "- None"
    return f"""# TripStar Planner Quality Baseline

## Baseline identity

- Status: **{report.manifest.baseline_status.upper()}**
- Reason: {report.manifest.reason or 'None'}
- Planner: `{report.manifest.planner_version}`
- Prompt: `{report.manifest.prompt_version}`
- Model: `{report.manifest.model}`
- Code revision: `{report.manifest.code_revision}`
- Golden Cases: `{report.manifest.golden_case_version}`
- Fixture set: `{report.manifest.fixture_set_version}`
- Metric policy: `{report.manifest.metric_policy_version}`

This report is a baseline measurement, not an optimization result.

## Coverage

- Cases total: {report.cases_total}
- Cases evaluated: {report.cases_evaluated}
- Cases failed: {report.cases_failed}
- Cases containing unknown metrics: {report.cases_unknown}

## Deterministic aggregate metrics

| Metric | Aggregate | Numerator/denominator | Known | Unknown | N/A | Failed |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(metric_rows)}

Unknown and not-applicable values are reported separately and are not silently removed.

## Automatic badcase distribution

{badcases}

## Scenario breakdown

{scenario}

## Human review

Human review status: **PENDING**. No LLM-generated or simulated human scores are included.
Pending records: {sum(item.status == 'pending' for item in report.human_reviews)}.

## Cost and latency

Token, call and latency distributions appear in the deterministic metrics table only
when real telemetry is available. Monetary cost is not estimated without a versioned
price source.
"""


def write_batch_markdown(path: Path, report: BatchEvaluationReport):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(batch_markdown(report), encoding="utf-8")
