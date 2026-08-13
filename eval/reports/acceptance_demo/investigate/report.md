# TripStar Paired Evaluation Report

## Run metadata

- Case: `gc_beijing_baseline`
- Baseline: `eval_demo_baseline` (`planner.demo` / `prompt.demo` / `offline-artifact`)
- Candidate: `eval_demo_investigate` (`planner.demo` / `prompt.demo` / `offline-artifact`)
- Fixture set: `fixtures.v1`
- Metric policy: `metrics.v1`
- Deterministic release decision: **INVESTIGATE**
- Threshold status: **provisional / config-required**

## Deterministic metric deltas

| Metric | Baseline | Candidate | Delta | Classification |
|---|---:|---:|---:|---|
| `schema_valid` | 1.0 | 1.0 | 0.0 | unchanged |
| `date_day_consistency` | 1.0 | 1.0 | 0.0 | unchanged |
| `explicit_constraint_satisfaction_rate` | not_applicable | not_applicable | — | unchanged |
| `earliest_start_satisfaction` | not_applicable | not_applicable | — | unchanged |
| `budget_arithmetic_consistency` | 1.0 | 1.0 | 0.0 | unchanged |
| `budget_limit_satisfaction` | not_applicable | not_applicable | — | unchanged |
| `grounded_poi_rate` | 1.0 | 1.0 | 0.0 | unchanged |
| `unverified_poi_rate` | 0.0 | 0.0 | 0.0 | unchanged |
| `provenance_coverage` | 1.0 | 1.0 | 0.0 | unchanged |
| `route_check_coverage` | not_applicable | not_applicable | — | unchanged |
| `route_feasibility_rate` | unknown | unknown | — | unchanged |
| `actionable_risk_count` | 0.0 | 0.0 | 0.0 | unchanged |
| `revision_risk_resolution_rate` | not_applicable | not_applicable | — | unchanged |
| `unaffected_day_preservation_rate` | not_applicable | not_applicable | — | unchanged |
| `logical_llm_calls` | 2.0 | 2.0 | 0.0 | unchanged |
| `prompt_tokens` | 100.0 | 100.0 | 0.0 | unchanged |
| `completion_tokens` | 50.0 | 50.0 | 0.0 | unchanged |
| `total_tokens` | 150.0 | 151.0 | 1.0 | regression |
| `latency_ms` | 500.0 | 500.0 | 0.0 | unchanged |

## Per-case failures and badcases

- Automatic detected badcases: None
- Unknown metrics: route_feasibility_rate
- Not applicable metrics: explicit_constraint_satisfaction_rate, earliest_start_satisfaction, budget_limit_satisfaction, route_check_coverage, revision_risk_resolution_rate, unaffected_day_preservation_rate

## Cost / latency

- Logical LLM calls delta: 0.0
- Total tokens delta: 1.0
- Latency delta: 0.0 ms

## Blocking reasons

- None

## Release decision

**INVESTIGATE**. Investigation reasons: non-hard regression: total_tokens.

## Human review

Human review is a separate evidence type. No human scores or LLM-as-a-Judge results
were supplied for this deterministic report.
