# Phase 4C — Targeted Pacing Revision

## Architecture audit and root cause

The legacy Phase 2B path asks an LLM to return a complete `TripPlan`. Its prompt asks for minimal changes, but the deterministic gate checks only cities, dates, day count and budget arithmetic. It neither restricts editable days nor deep-compares unaffected days. Re-enrichment then processes the whole returned plan. Therefore prompt compliance cannot prove affected-day-only preservation.

Reusable seams are strong: stable pacing risk IDs, initial/revised plans, critic target IDs, revision observations, POI stable IDs, patch-style day deep equality, schema validation, enrichment, full revalidation and immutable capture. Phase 4C reuses these seams without copying the Planner or changing Patch behavior.

## Targeted contract

Input includes the before plan, target risk IDs, affected/protected indices, requested pace, embedded `DailyLoadAssessment`, explicit constraints and policy version. The LLM sees only compact affected-day fields and returns `PacingRevisionProposal`, not a free-form plan. Production deterministically applies typed operations to a deep copy.

Phase 4C v1 allows:

- `remove_optional_poi`;
- `reduce_optional_duration`, strictly lower and schema-bounded;
- `delay_start_time`, never earlier and never before an explicit earliest start.

Deferred because evidence is insufficient: reordering, replacement/new POIs, cross-day movement, hotel/city changes and broad replanning. A POI named in structured `special_requirements`/`other_notes` is protected from removal. Other “must-have” semantics are not reliably structured, so every real removal/duration change requires human review in Phase 4D.

## Trigger policy

Automatic targeted revision requires all of:

```text
type == pacing
overload_status == revisable_overload
revisable == true
confidence in {HIGH, MEDIUM}
revision_execution_supported == true
```

Warning-only and LOW confidence assessments never trigger. Route unavailable alone never triggers. When pacing triggers, it takes the single revision slot; legacy full-plan revision is not also run.

## Commit gates

Protected days are every day outside the target risk indices. Equality is checked after typed apply and again after affected-day enrichment. Any drift rejects the candidate. Additional gates cover city/date/day count, explicit earliest-start validation, budget arithmetic/limit, retained grounding, schema parse, affected-day enrichment and full Validator execution. Existing route unavailable remains an independent validation-unavailable result.

Success means all affected-day revisable pacing risks become absent or warning-only. `unresolved` is not committed. All failures retain the original plan:

- `protected_day_drift`
- `target_risk_unresolved`
- `invalid_revision_output`
- `enrichment_failure`
- `constraint_regression`
- `budget_regression`
- `grounding_regression`
- `pacing_revision_unsupported`

## Observation, capture and metrics

Capture stores before, typed instruction metadata, candidate, committed after, target IDs, affected/protected indices, per-day equality, post-validation, post-revision assessments/risk IDs, resolution outcome, failure reason, policy version and metrics. No hidden reasoning is stored. `RevisionCapture` remains backward compatible through defaulted fields and a `legacy_full_plan | targeted_pacing` discriminator.

Per-revision metrics are recorded without changing `metrics.v2`:

- `pacing_revision_triggered`
- target/resolved counts and resolution rate
- protected-day preservation rate
- affected-day load ratio before/after
- affected-day load delta

No overall quality score is produced.

## Synthetic acceptance

| Case | Result |
|---|---|
| A balanced overload, remove optional POI | resolved; committed; protected day deep-equal |
| B relaxed overload remains overload | `unresolved`; original retained |
| C operation targets protected day | hard rejected as `protected_day_drift` |
| D earlier start violates no-early-start | rejected as `constraint_regression` |
| E pacing improves but budget arithmetic regresses | rejected as `budget_regression` |
| F route unavailable after duration repair | pacing resolved; validation-unavailable remains |
| G warning only | no trigger |
| H LOW-confidence transfer day | no trigger |

## Known-case offline simulation

This is a theoretical typed-operation simulation over immutable artifacts, not an LLM/provider/Golden Case run. Suggested POI removals are not automatically authorized; they require Phase 4D human review for preference/usefulness impact.

| Case/day | Trigger? | Protected days | Generic minimum theoretical change | Ratio before → after | Theoretically resolves? | Product risk |
|---|---|---|---|---|---|---|
| Shenzhen D1 | yes | D2,D3 | remove one 120-min optional activity | 1.020 → 0.780 | yes | may weaken city-exploration preference |
| Shenzhen D2 | yes | D1,D3 | remove one 90-min optional activity | 1.036 → 0.833 | yes | likely modest content loss |
| Shenzhen D3 | no, warning | all | none | 0.913 unchanged | n/a | early/suburban uncertainty still needs disclosure |
| Chengdu D1 | yes | D2–D4 | remove two smallest optional activities, 150 min total | 1.289 → 0.944 | yes, warning remains | high usefulness/preference risk; human review mandatory |
| Chengdu D2 | no, warning | all | none | 0.917 unchanged | n/a | 07:30 burden remains |
| Chengdu D3 | yes | D1,D2,D4 | remove one 150-min optional activity | 1.092 → 0.780 | yes | cultural-interest loss possible |
| Chengdu D4 | yes | D1–D3 | remove one 150-min optional activity | 1.163 → 0.938 | yes, warning remains | excursion completeness may decline |
| Lijiang D1 | yes | D2,D3 | remove one 120-min optional activity | 1.439 → 1.000 | yes, warning remains | relaxed experience improves but preference breadth drops |
| Lijiang D2 | yes | D1,D3 | remove one 150-min optional activity | 1.232 → 0.884 | yes, warning remains | may remove a core mountain component |
| Lijiang D3 | yes | D1,D2 | remove one 90-min optional activity | 1.217 → 0.917 | yes, warning remains | fallback-dependent decision |
| Kyoto D1–D3 | no, warning only | all | none | 0.930/1.035/0.930 | n/a | no revisable false positive |

The simulation chooses the smallest-duration removal set that reaches warning-or-better under conservative remaining-route fallback. It does not claim those POIs are truly optional and does not hardcode any case in production.

## Remaining limitations and Phase 4D readiness

The typed commit path is production-safe under its narrow operations and fail-closed gates. Real paired evaluation is now technically possible, but removals/duration reductions need blinded human review because must-have/optional status and usefulness are not fully structured. Reordering/replacement should remain disabled.

Recommended initial Phase 4D set: four paired cases—Shenzhen, Chengdu, Lijiang and Kyoto control. If budget permits, add Osaka as a fifth normalization/control case, reviewed separately because theme-park nesting differs from ordinary pacing. Reviewers must examine preference preservation, usefulness, plausibility of duration reduction, loss of core POIs and whether warning-level residual load remains acceptable.
