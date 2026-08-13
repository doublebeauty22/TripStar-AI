# Phase 3B Offline Eval Runner

The runner evaluates saved artifacts only. It does not execute the production Planner,
call an LLM, or contact a provider.

From the repository root:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m app.evaluation.runner validate-cases

PYTHONPATH=backend backend/.venv/bin/python -m app.evaluation.runner evaluate-artifact \
  --case-id gc_beijing_baseline \
  --artifact /path/to/artifact-input.json \
  --output eval/runs/eval_example/run.json \
  --planner-version planner.v1 \
  --prompt-version prompt.v1 \
  --model saved-artifact \
  --eval-run-id eval_example

PYTHONPATH=backend backend/.venv/bin/python -m app.evaluation.runner compare-runs \
  --baseline eval/runs/eval_baseline/run.json \
  --candidate eval/runs/eval_candidate/run.json \
  --comparison-id comparison_example \
  --json-output eval/reports/comparison_example/report.json \
  --markdown-output eval/reports/comparison_example/report.md

PYTHONPATH=backend backend/.venv/bin/python -m app.evaluation.runner generate-report \
  --comparison eval/reports/comparison_example/report.json \
  --baseline eval/runs/eval_baseline/run.json \
  --candidate eval/runs/eval_candidate/run.json \
  --markdown-output eval/reports/comparison_example/report-regenerated.md
```

`ArtifactEvaluationInput` contains a saved TripPlan-shaped `output`, optional usage and
latency telemetry, optional per-leg `route_checks`, and optional revision/patch
before/after state. Missing optional evidence produces `unknown` or `not_applicable`.

Every resolved fixture is schema-validated and hashed. Paired comparison rejects
different case IDs, Golden Case versions, fixture-set versions, fixture hashes, or
metric policies. Network sockets are blocked inside evaluation execution and an
accidental attempt yields `run_status=network_access_blocked`.

## Acceptance demo

```bash
PYTHONPATH=backend backend/.venv/bin/python -m app.evaluation.demo
```

This writes three synthetic comparisons beneath `eval/reports/acceptance_demo/`:

- `pass`: equivalent candidate → PASS
- `investigate`: non-hard token regression → INVESTIGATE
- `block`: schema hard-gate regression → BLOCK

These are mechanism demonstrations, not evidence of Planner quality.
