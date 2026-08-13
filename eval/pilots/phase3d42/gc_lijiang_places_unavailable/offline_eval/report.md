# Phase 3D-4.2 Offline Evaluation — gc_lijiang_places_unavailable

- capture: `capture.v2`
- metric policy: `metrics.v2`
- LLM/provider/network calls: 0

| Metric | Status | Value | Numerator | Denominator |
|---|---|---:|---:|---:|
| `schema_valid` | known | 1.0 | — | — |
| `date_day_consistency` | known | 1.0 | — | — |
| `explicit_constraint_satisfaction_rate` | not_applicable | — | — | — |
| `earliest_start_satisfaction` | not_applicable | — | — | — |
| `budget_arithmetic_consistency` | known | 1.0 | — | — |
| `budget_limit_satisfaction` | not_applicable | — | — | — |
| `grounded_poi_rate` | known | 0.8888888888888888 | 8.0 | 9.0 |
| `unverified_poi_rate` | known | 0.1111111111111111 | 1.0 | 9.0 |
| `provenance_coverage` | known | 0.9285714285714286 | 13.0 | 14.0 |
| `route_check_coverage` | known | 0.0 | 0.0 | 4.0 |
| `route_feasibility_rate` | unknown | — | — | — |
| `actionable_risk_count` | known | 0.0 | — | — |
| `revision_risk_resolution_rate` | not_applicable | — | — | — |
| `unaffected_day_preservation_rate` | not_applicable | — | — | — |
| `logical_llm_calls` | known | 2.0 | — | — |
| `prompt_tokens` | known | 15087.0 | — | — |
| `completion_tokens` | known | 11494.0 | — | — |
| `total_tokens` | known | 26581.0 | — | — |
| `latency_ms` | known | 105077.0 | — | — |
