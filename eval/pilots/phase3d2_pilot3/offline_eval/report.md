# gc_beijing_baseline — Real Offline Evaluation

- Run: `eval_pilot3_beijing`
- Planner: `planner_baseline_v1`
- Prompt: `planner_prompt_v1`
- Model: `gpt-5.6-luna`
- Run status: **completed**
- Evidence: deterministic metrics; human review **PENDING**

| Metric | Status | Value | Numerator/denominator | Reason |
|---|---|---:|---:|---|
| `schema_valid` | known | 1 | — | — |
| `date_day_consistency` | known | 1 | — | — |
| `explicit_constraint_satisfaction_rate` | not_applicable | — | — | no deterministically measurable explicit constraints |
| `earliest_start_satisfaction` | not_applicable | — | — | no explicit earliest_start_time |
| `budget_arithmetic_consistency` | known | 1 | — | — |
| `budget_limit_satisfaction` | not_applicable | — | — | no explicit budget limit |
| `grounded_poi_rate` | known | 1 | 9/9 | — |
| `unverified_poi_rate` | known | 0 | 0/9 | — |
| `provenance_coverage` | known | 1 | 14/14 | — |
| `route_check_coverage` | known | 0.666667 | 4/6 | — |
| `route_feasibility_rate` | known | 1 | 4/4 | — |
| `actionable_risk_count` | known | 0 | — | — |
| `revision_risk_resolution_rate` | not_applicable | — | — | no revision and no targeted risks |
| `unaffected_day_preservation_rate` | not_applicable | — | — | case has no protected days |
| `logical_llm_calls` | known | 2 | — | — |
| `prompt_tokens` | known | 16113 | — | — |
| `completion_tokens` | known | 11274 | — | — |
| `total_tokens` | known | 27387 | — | — |
| `latency_ms` | known | 105634 | — | — |

## Badcases

- Automatic detected: route_unavailable
- Human required: preference_miss, unsupported_fact, overpacked, underpacked, unnecessary_revision
- Not evaluated: excessive_cost, excessive_latency
