"""Phase 3C batch baseline CLI; consumes only saved Phase 3B run artifacts."""

import argparse
from pathlib import Path

from .baseline_reports import write_batch_json, write_batch_markdown
from .batch import build_batch_report
from .models import EvalRunArtifact
from .runner import load_cases


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tripstar-eval-baseline")
    parser.add_argument("--cases", type=Path, default=Path("eval/cases/golden_cases_v1.json"))
    parser.add_argument("--runs", type=Path, nargs="*", default=[])
    parser.add_argument("--baseline-id", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--planner-version", default="unavailable")
    parser.add_argument("--prompt-version", default="unavailable")
    parser.add_argument("--model", default="unavailable")
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args(argv)
    cases = load_cases(args.cases)
    runs = [EvalRunArtifact.model_validate_json(path.read_bytes()) for path in args.runs]
    report = build_batch_report(
        cases, runs, baseline_id=args.baseline_id, code_revision=args.code_revision,
        planner_version=args.planner_version, prompt_version=args.prompt_version,
        model=args.model,
    )
    write_batch_json(args.json_output, report)
    write_batch_markdown(args.markdown_output, report)
    print(report.manifest.baseline_status)
    return 0 if report.manifest.baseline_status == "established" else 2


if __name__ == "__main__":
    raise SystemExit(main())
