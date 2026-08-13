# Phase 3D-4.2 Offline Evaluation — gc_chengdu_budget

- capture: `capture.v2`
- metric policy: `metrics.v2`
- LLM/provider/network calls: 0

| Metric | Status | Value | Numerator | Denominator |
|---|---|---:|---:|---:|
| `schema_valid` | known | 1.0 | — | — |
| `date_day_consistency` | known | 1.0 | — | — |
| `explicit_constraint_satisfaction_rate` | known | 1.0 | 1.0 | 1.0 |
| `earliest_start_satisfaction` | not_applicable | — | — | — |
| `budget_arithmetic_consistency` | known | 1.0 | — | — |
| `budget_limit_satisfaction` | known | 1.0 | — | — |
| `grounded_poi_rate` | known | 0.875 | 14.0 | 16.0 |
| `unverified_poi_rate` | known | 0.0 | 0.0 | 16.0 |
| `provenance_coverage` | known | 0.9047619047619048 | 19.0 | 21.0 |
| `route_check_coverage` | known | 0.75 | 6.0 | 8.0 |
| `route_feasibility_rate` | known | 1.0 | 6.0 | 6.0 |
| `actionable_risk_count` | known | 0.0 | — | — |
| `revision_risk_resolution_rate` | not_applicable | — | — | — |
| `unaffected_day_preservation_rate` | not_applicable | — | — | — |
| `logical_llm_calls` | known | 2.0 | — | — |
| `prompt_tokens` | known | 15441.0 | — | — |
| `completion_tokens` | known | 12145.0 | — | — |
| `total_tokens` | known | 27586.0 | — | — |
| `latency_ms` | known | 115369.0 | — | — |
