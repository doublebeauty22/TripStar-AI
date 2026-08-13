# TripStar Planner Quality Baseline

## Baseline identity

- Status: **ESTABLISHED**
- Reason: None
- Planner: `planner_baseline_v1`
- Prompt: `planner_prompt_v1`
- Model: `gpt-5.6-luna`
- Code revision: `96b9c5e-dirty`
- Golden Cases: `golden.v1`
- Fixture set: `fixtures.v1`
- Metric policy: `metrics.v1`

This report is a baseline measurement, not an optimization result.

## Coverage

- Cases total: 3
- Cases evaluated: 3
- Cases failed: 0
- Cases containing unknown metrics: 2

## Deterministic aggregate metrics

| Metric | Aggregate | Numerator/denominator | Known | Unknown | N/A | Failed |
|---|---:|---:|---:|---:|---:|---:|
| `schema_valid` | 1.0000 | 3/3 | 3 | 0 | 0 | 0 |
| `date_day_consistency` | 1.0000 | 3/3 | 3 | 0 | 0 | 0 |
| `explicit_constraint_satisfaction_rate` | 1.0000 | 1/1 | 1 | 0 | 2 | 0 |
| `earliest_start_satisfaction` | 1.0000 | 3/3 | 1 | 0 | 2 | 0 |
| `budget_arithmetic_consistency` | 1.0000 | 3/3 | 3 | 0 | 0 | 0 |
| `budget_limit_satisfaction` | unknown/N/A | — | 0 | 0 | 3 | 0 |
| `grounded_poi_rate` | 0.4615 | 12/26 | 3 | 0 | 0 | 0 |
| `unverified_poi_rate` | 0.3846 | 10/26 | 3 | 0 | 0 | 0 |
| `provenance_coverage` | 0.5758 | 19/33 | 3 | 0 | 0 | 0 |
| `route_check_coverage` | 0.2353 | 4/17 | 3 | 0 | 0 | 0 |
| `route_feasibility_rate` | 1.0000 | 4/4 | 1 | 2 | 0 | 0 |
| `actionable_risk_count` | 0.0000 | 0/3 | 3 | 0 | 0 | 0 |
| `revision_risk_resolution_rate` | unknown/N/A | — | 0 | 0 | 3 | 0 |
| `unaffected_day_preservation_rate` | unknown/N/A | — | 0 | 0 | 3 | 0 |
| `logical_llm_calls` | 2.0000 | 6/3 | 3 | 0 | 0 | 0 |
| `prompt_tokens` | 12866.0000 | 38598/3 | 3 | 0 | 0 | 0 |
| `completion_tokens` | 9482.3333 | 28447/3 | 3 | 0 | 0 | 0 |
| `total_tokens` | 22348.3333 | 67045/3 | 3 | 0 | 0 | 0 |
| `latency_ms` | 99429.3333 | 298288/3 | 3 | 0 | 0 | 0 |

Unknown and not-applicable values are reported separately and are not silently removed.

## Automatic badcase distribution

- `route_unavailable`: 3
- `ungrounded_poi`: 2
- `provenance_missing`: 2

## Scenario breakdown

- **avoid_early_start**: 1 cases
- **google_places_partial**: 1 cases
- **multi_interest**: 1 cases
- **revision_trigger**: 1 cases
- **single_city**: 3 cases
- **zh_input**: 3 cases
- **pace_relaxed**: 0 cases
- **pace_intensive**: 0 cases
- **budget_constrained**: 0 cases
- **budget_unconstrained**: 3 cases
- **mobility**: 0 cases
- **mobility_normal**: 3 cases
- **food_constraint**: 0 cases
- **food_normal**: 3 cases
- **provider_degraded**: 1 cases
- **provider_normal**: 2 cases
- **revision_trigger**: 1 cases
- **no_revision_trigger**: 2 cases

## Human review

Human review status: **PENDING**. No LLM-generated or simulated human scores are included.
Pending records: 3.

## Cost and latency

Token, call and latency distributions appear in the deterministic metrics table only
when real telemetry is available. Monetary cost is not estimated without a versioned
price source.
