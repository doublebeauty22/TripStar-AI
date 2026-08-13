# Phase 3D-4 Partial Expansion Summary

- Target: 8 controlled artifacts
- Evaluated complete artifacts: 6
- Failed: 1 (`gc_beijing_xian_multi_city`)
- Capture-limited: 1 (`gc_nanjing_local_patch`)
- 8-case baseline: **NOT ESTABLISHED**
- Evidence scope: preliminary controlled baseline evidence

## Aggregate metrics

| Metric | Value | Numerator | Denominator | Known | Unknown | N/A | Failed |
|---|---:|---:|---:|---:|---:|---:|---:|
| `schema_valid` | 1.0 | — | — | 6 | 0 | 0 | 0 |
| `date_day_consistency` | 0.8333333333333334 | — | — | 6 | 0 | 0 | 0 |
| `explicit_constraint_satisfaction_rate` | 1.0 | 3.0 | 3.0 | 3 | 0 | 3 | 0 |
| `earliest_start_satisfaction` | 1.0 | 3.0 | 3.0 | 1 | 0 | 5 | 0 |
| `budget_arithmetic_consistency` | 1.0 | — | — | 6 | 0 | 0 | 0 |
| `budget_limit_satisfaction` | 1.0 | — | — | 2 | 0 | 4 | 0 |
| `grounded_poi_rate` | 0.6111111111111112 | 33.0 | 54.0 | 6 | 0 | 0 | 0 |
| `unverified_poi_rate` | 0.2222222222222222 | 12.0 | 54.0 | 6 | 0 | 0 | 0 |
| `provenance_coverage` | 0.7236842105263158 | 55.0 | 76.0 | 6 | 0 | 0 | 0 |
| `route_check_coverage` | 0.6285714285714286 | 22.0 | 35.0 | 6 | 0 | 0 | 0 |
| `route_feasibility_rate` | 0.7727272727272727 | 17.0 | 22.0 | 4 | 2 | 0 | 0 |
| `actionable_risk_count` | 0.5 | — | — | 6 | 0 | 0 | 0 |
| `revision_risk_resolution_rate` | — | — | — | 0 | 2 | 4 | 0 |
| `unaffected_day_preservation_rate` | — | — | — | 0 | 0 | 6 | 0 |
| `logical_llm_calls` | 2.6666666666666665 | — | — | 6 | 0 | 0 | 0 |
| `prompt_tokens` | 22323.833333333332 | — | — | 6 | 0 | 0 | 0 |
| `completion_tokens` | 12077.166666666666 | — | — | 6 | 0 | 0 | 0 |
| `total_tokens` | 34401.0 | — | — | 6 | 0 | 0 | 0 |
| `latency_ms` | 115139.5 | — | — | 6 | 0 | 0 | 0 |

## Grounding → route observations

| Case | Grounded POIs | Eligible legs | Attempted | Available |
|---|---:|---:|---:|---:|
| `gc_beijing_baseline` | 9 | 6 | 6 | 4 |
| `gc_kyoto_no_early_start` | 2 | 6 | 0 | 0 |
| `gc_osaka_places_partial` | 1 | 5 | 0 | 0 |
| `gc_chengdu_budget` | 8 | 15 | 9 | 9 |
| `gc_shenzhen_overbudget_revision` | 7 | 6 | 4 | 2 |
| `gc_lijiang_places_unavailable` | 6 | 11 | 8 | 7 |

Assessment: **MIXED** (association is visible in some cases, but not monotonic; no causal claim).

## Metric integrity warning

- `route_check_coverage` exceeds 1.0 for Chengdu and Lijiang due to revision observation/denominator mismatch. Aggregate route coverage is not reliable until the capture/metric seam is repaired.

## Badcases

- Automatic detected: {"route_unavailable": 6, "ungrounded_poi": 5, "provenance_missing": 5, "route_infeasible": 2}
- Human required (pending, not detected): {"preference_miss": 6, "unsupported_fact": 6, "overpacked": 6, "underpacked": 6, "unnecessary_revision": 6}
- Not evaluated: {"excessive_cost": 6, "excessive_latency": 6}
