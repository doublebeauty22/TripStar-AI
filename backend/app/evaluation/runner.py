"""Minimal standard-library CLI for deterministic artifact evaluation."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .badcases import label_badcases
from .comparison import compare_runs
from .fixtures import FrozenFixtureResolver, write_canonical_json
from .metrics import calculate_metrics
from .models import ArtifactEvaluationInput, EvalCase, EvalRunArtifact, PlannerVersionMetadata
from .network import NetworkAccessBlocked, deny_network
from .reports import write_json_report, write_markdown_report


RUNNER_VERSION = "runner.v1"
GOLDEN_VERSION = "golden.v1"


def load_cases(path: Path) -> list[EvalCase]:
    return [EvalCase.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def evaluate_artifact(case: EvalCase, artifact: ArtifactEvaluationInput, metadata: PlannerVersionMetadata,
                      fixture_hashes: dict[str, str], output_reference: str,
                      started_at: str | None = None) -> EvalRunArtifact:
    start = started_at or datetime.now(timezone.utc).isoformat()
    try:
        with deny_network():
            metrics = calculate_metrics(case, artifact)
            badcases = label_badcases(case, artifact, metrics)
        status, error = "completed", None
    except NetworkAccessBlocked as exc:
        metrics, badcases = [], []
        status, error = "network_access_blocked", str(exc)
    completed = datetime.now(timezone.utc).isoformat()
    latency = artifact.latency_ms if artifact.latency_ms is not None else 0
    return EvalRunArtifact(
        runner_version=RUNNER_VERSION, metadata=metadata, fixture_hashes=fixture_hashes,
        started_at=start, completed_at=completed, latency_ms=latency,
        output_artifact_reference=output_reference, run_status=status,
        metrics=metrics, badcases=badcases, error=error,
    )


def _case(cases: list[EvalCase], case_id: str) -> EvalCase:
    try: return next(item for item in cases if item.case_id == case_id)
    except StopIteration as exc: raise ValueError(f"unknown case_id: {case_id}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tripstar-eval")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-cases")
    validate.add_argument("--cases", type=Path, default=Path("eval/cases/golden_cases_v1.json"))
    evaluate = sub.add_parser("evaluate-artifact")
    for command in (evaluate,):
        command.add_argument("--cases", type=Path, default=Path("eval/cases/golden_cases_v1.json"))
        command.add_argument("--eval-root", type=Path, default=Path("eval"))
        command.add_argument("--case-id", required=True)
        command.add_argument("--artifact", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--planner-version", required=True)
        command.add_argument("--prompt-version", required=True)
        command.add_argument("--model", required=True)
        command.add_argument("--eval-run-id", required=True)
        command.add_argument("--fixture-set-version", default="fixtures.v1")
    compare = sub.add_parser("compare-runs")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--comparison-id", required=True)
    compare.add_argument("--json-output", type=Path, required=True)
    compare.add_argument("--markdown-output", type=Path, required=True)
    report = sub.add_parser("generate-report")
    report.add_argument("--comparison", type=Path, required=True)
    report.add_argument("--baseline", type=Path, required=True)
    report.add_argument("--candidate", type=Path, required=True)
    report.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "validate-cases":
        cases = load_cases(args.cases); print(json.dumps({"valid": True, "case_count": len(cases)})); return 0
    if args.command == "evaluate-artifact":
        case = _case(load_cases(args.cases), args.case_id)
        _, hashes = FrozenFixtureResolver(args.eval_root).resolve(case)
        artifact = ArtifactEvaluationInput.model_validate_json(args.artifact.read_bytes())
        metadata = PlannerVersionMetadata(planner_version=args.planner_version, prompt_version=args.prompt_version,
            model=args.model, eval_run_id=args.eval_run_id, case_id=args.case_id,
            fixture_set_version=args.fixture_set_version)
        run = evaluate_artifact(case, artifact, metadata, hashes, str(args.artifact))
        write_json_report(args.output, run); return 0 if run.run_status == "completed" else 2
    baseline = EvalRunArtifact.model_validate_json(args.baseline.read_bytes())
    candidate = EvalRunArtifact.model_validate_json(args.candidate.read_bytes())
    if args.command == "compare-runs":
        comparison = compare_runs(baseline, candidate, args.comparison_id)
        write_json_report(args.json_output, comparison)
        write_markdown_report(args.markdown_output, comparison, baseline, candidate); return 0
    from .models import PairedComparison
    comparison = PairedComparison.model_validate_json(args.comparison.read_bytes())
    write_markdown_report(args.markdown_output, comparison, baseline, candidate); return 0


if __name__ == "__main__":
    raise SystemExit(main())
