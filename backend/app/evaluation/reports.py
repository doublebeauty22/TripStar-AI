"""Machine-readable and human-readable eval reports."""

import json
from pathlib import Path

from .fixtures import write_canonical_json
from .models import EvalRunArtifact, PairedComparison


def write_json_report(path: Path, value: EvalRunArtifact | PairedComparison) -> None:
    write_canonical_json(path, value.model_dump(mode="json"))


def comparison_markdown(comparison: PairedComparison, baseline: EvalRunArtifact, candidate: EvalRunArtifact) -> str:
    rows = []
    for item in comparison.metric_deltas:
        left = item.baseline_value if item.baseline_status == "known" else item.baseline_status
        right = item.candidate_value if item.candidate_status == "known" else item.candidate_status
        rows.append(f"| `{item.metric}` | {left} | {right} | {item.delta if item.delta is not None else '—'} | {item.classification} |")
    badcases = [item.label for item in candidate.badcases if item.evidence_type == "automatic" and item.detected]
    unknown = [item.metric for item in candidate.metrics if item.status == "unknown"]
    na = [item.metric for item in candidate.metrics if item.status == "not_applicable"]
    blocking = "\n".join(f"- {item}" for item in comparison.blocking_reasons) or "- None"
    return f"""# TripStar Paired Evaluation Report

## Run metadata

- Case: `{comparison.case_id}`
- Baseline: `{comparison.baseline_run_id}` (`{baseline.metadata.planner_version}` / `{baseline.metadata.prompt_version}` / `{baseline.metadata.model}`)
- Candidate: `{comparison.candidate_run_id}` (`{candidate.metadata.planner_version}` / `{candidate.metadata.prompt_version}` / `{candidate.metadata.model}`)
- Fixture set: `{candidate.metadata.fixture_set_version}`
- Metric policy: `{candidate.metric_policy_version}`
- Deterministic release decision: **{comparison.release_decision}**
- Threshold status: **provisional / config-required**

## Deterministic metric deltas

| Metric | Baseline | Candidate | Delta | Classification |
|---|---:|---:|---:|---|
{chr(10).join(rows)}

## Per-case failures and badcases

- Automatic detected badcases: {', '.join(badcases) if badcases else 'None'}
- Unknown metrics: {', '.join(unknown) if unknown else 'None'}
- Not applicable metrics: {', '.join(na) if na else 'None'}

## Cost / latency

- Logical LLM calls delta: {_delta(comparison, 'logical_llm_calls')}
- Total tokens delta: {_delta(comparison, 'total_tokens')}
- Latency delta: {_delta(comparison, 'latency_ms')} ms

## Blocking reasons

{blocking}

## Release decision

**{comparison.release_decision}**. Investigation reasons: {', '.join(comparison.investigation_reasons) if comparison.investigation_reasons else 'None'}.

## Human review

Human review is a separate evidence type. No human scores or LLM-as-a-Judge results
were supplied for this deterministic report.
"""


def _delta(comparison: PairedComparison, name: str):
    item = next(value for value in comparison.metric_deltas if value.metric == name)
    return item.delta if item.delta is not None else "unknown/N/A"


def write_markdown_report(path: Path, comparison: PairedComparison, baseline: EvalRunArtifact, candidate: EvalRunArtifact) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(comparison_markdown(comparison, baseline, candidate), encoding="utf-8")
