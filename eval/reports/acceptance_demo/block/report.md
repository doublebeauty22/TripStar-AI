# TripStar Paired Evaluation Report

## Run metadata

- Case: `gc_beijing_baseline`
- Baseline: `eval_demo_baseline` (`planner.demo` / `prompt.demo` / `offline-artifact`)
- Candidate: `eval_demo_block` (`planner.demo` / `prompt.demo` / `offline-artifact`)
- Fixture set: `fixtures.v1`
- Metric policy: `metrics.v1`
- Deterministic release decision: **BLOCK**
- Threshold status: **provisional / config-required**

## Deterministic metric deltas

| Metric | Baseline | Candidate | Delta | Classification |
|---|---:|---:|---:|---|
| `schema_valid` | 1.0 | 0.0 | -1.0 | regression |
| `date_day_consistency` | 1.0 | 0.0 | -1.0 | regression |
| `explicit_constraint_satisfaction_rate` | not_applicable | unknown | — | not_comparable |
| `earliest_start_satisfaction` | not_applicable | unknown | — | not_comparable |
| `budget_arithmetic_consistency` | 1.0 | unknown | — | known_to_unknown |
| `budget_limit_satisfaction` | not_applicable | unknown | — | not_comparable |
| `grounded_poi_rate` | 1.0 | unknown | — | known_to_unknown |
| `unverified_poi_rate` | 0.0 | unknown | — | known_to_unknown |
| `provenance_coverage` | 1.0 | unknown | — | known_to_unknown |
| `route_check_coverage` | not_applicable | unknown | — | not_comparable |
| `route_feasibility_rate` | unknown | unknown | — | unchanged |
| `actionable_risk_count` | 0.0 | unknown | — | known_to_unknown |
| `revision_risk_resolution_rate` | not_applicable | unknown | — | not_comparable |
| `unaffected_day_preservation_rate` | not_applicable | unknown | — | not_comparable |
| `logical_llm_calls` | 2.0 | 2.0 | 0.0 | unchanged |
| `prompt_tokens` | 100.0 | 100.0 | 0.0 | unchanged |
| `completion_tokens` | 50.0 | 50.0 | 0.0 | unchanged |
| `total_tokens` | 150.0 | 150.0 | 0.0 | unchanged |
| `latency_ms` | 500.0 | 500.0 | 0.0 | unchanged |

## Per-case failures and badcases

- Automatic detected badcases: None
- Unknown metrics: explicit_constraint_satisfaction_rate, earliest_start_satisfaction, budget_arithmetic_consistency, budget_limit_satisfaction, grounded_poi_rate, unverified_poi_rate, provenance_coverage, route_check_coverage, route_feasibility_rate, actionable_risk_count, revision_risk_resolution_rate, unaffected_day_preservation_rate
- Not applicable metrics: None

## Cost / latency

- Logical LLM calls delta: 0.0
- Total tokens delta: 0.0
- Latency delta: 0.0 ms

## Blocking reasons

- hard gate regression: schema_valid
- hard gate regression: date_day_consistency

## Release decision

**BLOCK**. Investigation reasons: coverage regression: budget_arithmetic_consistency, coverage regression: grounded_poi_rate, coverage regression: unverified_poi_rate, coverage regression: provenance_coverage, coverage regression: actionable_risk_count.

## Human review

Human review is a separate evidence type. No human scores or LLM-as-a-Judge results
were supplied for this deterministic report.
