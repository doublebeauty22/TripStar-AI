# Offline Evaluation — gc_shenzhen_overbudget_revision

- Evidence: deterministic metrics
- Human review: pending
- Network/API/LLM/provider calls during evaluation: 0

## Metrics

| Metric | Status | Value | Numerator | Denominator |
|---|---|---:|---:|---:|
| `schema_valid` | known | 1.0 | — | — |
| `date_day_consistency` | known | 0.0 | — | — |
| `explicit_constraint_satisfaction_rate` | known | 1.0 | 1.0 | 1.0 |
| `earliest_start_satisfaction` | not_applicable | — | — | — |
| `budget_arithmetic_consistency` | known | 1.0 | — | — |
| `budget_limit_satisfaction` | known | 1.0 | — | — |
| `grounded_poi_rate` | known | 0.7777777777777778 | 7.0 | 9.0 |
| `unverified_poi_rate` | known | 0.0 | 0.0 | 9.0 |
| `provenance_coverage` | known | 0.8571428571428571 | 12.0 | 14.0 |
| `route_check_coverage` | known | 0.3333333333333333 | 2.0 | 6.0 |
| `route_feasibility_rate` | known | 1.0 | 2.0 | 2.0 |
| `actionable_risk_count` | known | 0.0 | — | — |
| `revision_risk_resolution_rate` | not_applicable | — | — | — |
| `unaffected_day_preservation_rate` | not_applicable | — | — | — |
| `logical_llm_calls` | known | 2.0 | — | — |
| `prompt_tokens` | known | 17811.0 | — | — |
| `completion_tokens` | known | 13103.0 | — | — |
| `total_tokens` | known | 30914.0 | — | — |
| `latency_ms` | known | 115158.0 | — | — |

## Badcases

- `constraint_violation`: evidence=automatic, detected=False, reason=deterministically measurable explicit constraint result
- `ungrounded_poi`: evidence=automatic, detected=True, reason=one or more POIs are not grounded
- `route_infeasible`: evidence=automatic, detected=False, reason=checked route leg is infeasible
- `route_unavailable`: evidence=automatic, detected=True, reason=one or more required routes are unavailable
- `budget_overrun`: evidence=automatic, detected=False, reason=plan exceeds explicit budget limit
- `budget_inconsistent`: evidence=automatic, detected=False, reason=budget total differs from component sum
- `revision_failed`: evidence=automatic, detected=False, reason=one or more targeted revision risks remain
- `provenance_missing`: evidence=automatic, detected=True, reason=one or more applicable structured facts lack provenance
- `patch_scope_drift`: evidence=automatic, detected=False, reason=one or more protected days changed
- `excessive_cost`: evidence=not_evaluated, detected=None, reason=comparison threshold is provisional/config-required
- `excessive_latency`: evidence=not_evaluated, detected=None, reason=comparison threshold is provisional/config-required
- `preference_miss`: evidence=human_required, detected=None, reason=requires human preference review
- `unsupported_fact`: evidence=human_required, detected=None, reason=unrestricted prose has no claim/evidence contract in v1
- `overpacked`: evidence=human_required, detected=None, reason=v1 requires human pacing review
- `underpacked`: evidence=human_required, detected=None, reason=v1 requires human pacing review
- `unnecessary_revision`: evidence=human_required, detected=None, reason=requires human judgment of repair necessity
