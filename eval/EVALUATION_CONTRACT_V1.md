# TripStar Phase 3A Evaluation Contract v1

## 1. Existing production data reused by evaluation

Evaluation reuses production models; it does not create an evaluation copy of
`TripRequest` or `TripPlan`.

| Existing data | Direct evaluation use |
|---|---|
| `TripRequest`, `CityStay` | Dates, duration, cities, transport, accommodation and language |
| `PreferenceProfile`, `PreferenceConstraints` | Explicit interests, budget cap, pace, party, earliest start, mobility and food requirements |
| `TripPlan`, `DayPlan` | Schema validity, date/day structure, daily start, activities and plan stability |
| `Attraction` | POI denominator, `poi_match_status`, `map_data_source`, place identity and coordinates |
| `WeatherInfo`, `WeatherResult` | Weather source, verification, availability and degradation |
| `XHSResearchResult` | Research status, evidence, extracted item support and degradation |
| `Budget` | Component arithmetic and comparison with explicit budget cap |
| `RiskItem`, `ValidationResult` | Checked rules, actionable risks, unknown route checks and deterministic evidence |
| `revision_count`, `revision_summary` | Whether bounded revision occurred; paired pre/post artifacts are still required for effectiveness |
| `TripPatchResult.diff`, patch history | Changed/protected days, scope drift and patch outcome |
| task usage fields | LLM calls, stage calls, prompt/completion/total tokens and retries |
| provider result state | request success, data availability, degraded state and failure reason |

Current data is insufficient for price accuracy, implicit preference satisfaction,
unsupported claims in unrestricted prose, human usefulness, full latency breakdown,
plan diversity, or revision effectiveness without a pre-revision artifact. These are
reported as unknown or human-reviewed; unknown is not silently converted to failure.

## 2. TripStar Quality Model

There is no composite “AI quality score”. Release decisions preserve each dimension.

| Dimension | Product problem represented | Method | Inputs | Outputs | Release gate v1 |
|---|---|---|---|---|---|
| Structural Validity | A malformed or date-inconsistent itinerary cannot be consumed safely | Deterministic | Request, parsed plan | schema validity; date/day consistency | Hard |
| Explicit Constraint Satisfaction | The system must honor what the user explicitly confirmed | Hybrid: deterministic for encoded rules, human for unencoded food/interest semantics | Profile, plan, risks | satisfaction rate plus per-constraint status | Hard for measurable blocking constraints |
| Preference Satisfaction | A technically valid plan may ignore interests or desired experience | Human | Request/profile and plan | 1–5 score and rationale | Investigate; not hard v1 |
| POI Grounding | Invented or mismatched places make the plan unsafe | Deterministic | Attractions and map trust metadata | grounded and unverified rates | Hard minimum coverage |
| Provenance Coverage | Users and reviewers need to know which external claims have support | Deterministic for structured facts; human for prose | XHS, weather, POI and route metadata | known-source / applicable-fact coverage | Hard only against regression |
| Route Feasibility | Attractions may be individually real but impossible to combine | Hybrid | Verified legs, Directions results, risks, plan | check coverage; feasible checked-leg rate | Coverage regression hard; feasibility investigated/blocked by severity |
| Budget Consistency | Precise-looking totals may be internally wrong or exceed explicit cap | Deterministic | Budget and profile cap | arithmetic and limit satisfaction | Hard |
| Revision Effectiveness | Revision may fail to resolve risks or introduce new ones | Deterministic paired pre/post plus human review | Pre/post plan and risk sets | target-risk resolution; new risks; stability | Hard for scope/structure; threshold for risk resolution |
| Plan Stability | A local change must not rewrite unrelated days | Deterministic | Pre/post plans and patch diff | unaffected-day preservation | Hard for local patch cases |
| Cost / Latency | Quality gains may be too slow or expensive | Deterministic telemetry | Run metadata and usage | calls, tokens, latency and deltas | Regression threshold, not absolute quality |

## 3. Deterministic Metrics v1

All rates are in `[0, 1]`. A metric result needs `value`, `status` (`known`,
`unknown`, or `not_applicable`), numerator/denominator where relevant, and reasons.

| Metric | Formula | Source | Direction | Missing-data rule | Hard gate |
|---|---|---|---|---|---|
| `schema_valid` | 1 when output validates as `TripPlan`, else 0 | Parse result | Higher | Parse failure is a real failure, not unknown | Yes: must be 1 |
| `date_day_consistency` | 1 iff plan dates match request, number of days equals `travel_days`, dates are consecutive, day indices are unique/ordered, and city-day allocation is valid | Request + plan | Higher | Missing plan after parse failure = 0 | Yes: must be 1 |
| `explicit_constraint_satisfaction_rate` | satisfied deterministically measurable explicit constraints / measurable explicit constraints | Profile + plan + risks | Higher | No measurable constraints = not applicable; unencoded food/interest constraints excluded and sent to human review | Yes for known blocking constraints |
| `earliest_start_satisfaction` | days with start time at/after explicit earliest time / applicable days | Profile + days | Higher | No explicit time = not applicable; missing day start = unknown for that day, not satisfied | Yes when fully known |
| `budget_arithmetic_consistency` | 1 iff `total` equals all five component totals | Budget | Higher | Missing budget = unknown, separately tagged `budget_missing` | Yes: known value must be 1 |
| `budget_limit_satisfaction` | 1 iff plan total ≤ explicit `budget_cny` | Profile + budget | Higher | No cap = not applicable; missing budget with cap = unknown | Yes when known |
| `grounded_poi_rate` | verified POIs with trusted source, valid provider identity and valid coordinates / all itinerary POIs | Attractions | Higher | No POIs = unknown and structural badcase; partial/unverified remain known non-grounded | Threshold gate |
| `unverified_poi_rate` | POIs marked unverified / all itinerary POIs | Attractions | Lower | No POIs = unknown | Threshold gate |
| `provenance_coverage` | applicable structured external facts with an allowed source + verification state / applicable structured external facts | XHS, weather, POI, route metadata | Higher | Provider explicitly unavailable removes facts from supported numerator but preserves known degraded status; absent applicability = not applicable | Regression gate |
| `route_check_coverage` | adjacent POI legs with a verified route result / adjacent POI legs | Days + route artifacts/validator evidence | Higher | Fewer than two POIs in every day = not applicable; provider unavailable yields known 0 coverage, not infeasible | Regression/threshold gate |
| `route_feasibility_rate` | checked legs below configured feasibility limits / checked legs | Route artifacts + policy version | Higher | Zero checked legs = unknown, never 0 | Threshold only when coverage sufficient |
| `actionable_risk_count` | count of warning/blocking, revisable risks in actionable risk types | Risks | Lower | Missing Validator result = unknown | Regression gate; blocking increase may block |
| `revision_risk_resolution_rate` | targeted pre-revision risk IDs absent after revalidation / targeted pre-revision risk IDs | Critic target IDs + pre/post risks | Higher | No revision/targets = not applicable; missing pre/post artifact = unknown | Threshold for revision cases |
| `unaffected_day_preservation_rate` | byte-canonical equivalent protected days / protected days | Pre/post plan + patch contract | Higher | No protected days = not applicable; missing pre/post = unknown | Yes: must be 1 for local patch |
| `logical_llm_calls` | recorded logical application calls for the evaluated execution | Task/run usage | Lower at equal quality | Missing telemetry = unknown | Cost regression only |
| `prompt_tokens` | sum of provider-reported prompt/input tokens | Task/run usage | Lower at equal quality | Provider omits usage = unknown | Cost regression only |
| `completion_tokens` | sum of provider-reported completion/output tokens | Task/run usage | Lower at equal quality | Provider omits usage = unknown | Cost regression only |
| `total_tokens` | provider total, or prompt + completion only when both are known | Task/run usage | Lower at equal quality | Partial token telemetry = unknown | Cost regression only |
| `latency_ms` | monotonic end timestamp − start timestamp for the declared run scope | Eval runner | Lower at equal quality | Missing timestamps = unknown | Latency regression only |

Price accuracy is intentionally absent: current production data contains estimates,
not frozen price truth. Food constraint satisfaction remains human-reviewed until a
deterministic meal-attribute contract exists.

## 4. Human Review Rubric v1

The canonical anchored rubric is `contracts/human_rubric_v1.json`. Reviewers score
preference satisfaction, itinerary coherence, pacing quality, usefulness and
explanation quality from 1–5. Every score requires rationale. Reports retain reviewer
identity and individual scores; they do not present reviewer opinion as objective fact.
Phase 3A does not use an LLM judge.

## 5. Golden Cases v1

`cases/golden_cases_v1.json` contains 16 cases:

1. Beijing baseline culture
2. Shanghai relaxed pacing
3. Tokyo intensive English
4. Kyoto explicit no-early-start and revision trigger
5. Chengdu budget plus food constraint
6. Hangzhou mobility constraint
7. Beijing XHS unavailable
8. Osaka Google Places partial
9. Lijiang Places and route unavailable
10. Guangzhou route unavailable
11. Sydney weather fallback English
12. Beijing–Xi'an multi-city
13. Seoul food constraint English
14. Nanjing local patch and protected days
15. Shenzhen over-budget revision
16. Hong Kong multi-interest English

Together they cover all required scenario tags without making every case test every
dimension. Dates and names are stable synthetic evaluation inputs, not booked trips.

## 6. Frozen Fixture Strategy

Directory contract:

```text
eval/
  cases/golden_cases_v1.json
  contracts/human_rubric_v1.json
  fixtures/{xhs,google_places,google_directions,google_weather,amap}/
  runs/<eval_run_id>/
  reports/<comparison_id>/
```

Fixtures use `SanitizedProviderFixture`. Store only the minimum parser/trust-gate
fields. Remove keys, cookies, authorization, user identifiers, raw prompts and
irrelevant provider payload. Fixtures are immutable inside `vN`; update by adding a
new version. A future run manifest resolves every fixture ref to a content hash.
Baseline and candidate must use identical hashes and `fixture_set_version`.

Unavailable/partial/degraded cases are typed payload states with explicit reasons.
They are never produced by attempting a live request. The Phase 3B runner must deny
network by default and fail closed if a case has an unresolved fixture.

## 7. Planner Versioning Contract

Every run artifact uses `PlannerVersionMetadata`:

- `planner_version`: application-level planning behavior contract
- `prompt_version`: exact prompt/template contract
- `model`: model identifier recorded as metadata, not inferred later
- `eval_run_id`: immutable execution identity
- `case_id`: Golden Case identity
- `fixture_set_version`: shared frozen provider input set

Changing planner code or prompt semantics requires a new corresponding version.
Phase 3A does not add these fields to production Planner or refactor it.

## 8. Paired Comparison Contract

A baseline/candidate comparison is valid only when case ID, request, fixture hashes,
fixture-set version, metric policy version and sampling policy match. Model may differ
only when model change is the declared experiment. Each side retains raw artifacts.

Output preserves:

- per-case baseline value, candidate value, delta and known/unknown state;
- aggregate metric values with eligible denominator and coverage;
- regressions, improvements and unchanged metrics;
- logical-call, prompt/completion/total-token and latency deltas;
- human scores by dimension and reviewer, never merged into deterministic metrics.

Unknown-to-known is reported as a coverage change, not automatically an improvement.
Known-to-unknown is a coverage regression. No aggregate total quality score is allowed.

Initial release decision:

- **BLOCK**: any structural hard gate fails; a measurable explicit blocking constraint
  regresses; budget arithmetic is inconsistent; local patch preservation is below 1;
  grounded/provenance coverage crosses an approved minimum; or a new critical badcase
  appears.
- **INVESTIGATE**: no hard failure, but any material metric regression, known-to-unknown
  change, conflicting human review, >20% token increase, or >20% latency increase lacks
  a predeclared quality trade-off.
- **PASS**: all hard gates pass, no material uncovered regression exists, coverage does
  not decline, and cost/latency deltas are within thresholds or justified by explicit
  improvements. PASS means “eligible to release”, not universally better.

Threshold values must be versioned with the runner; Phase 3B establishes a baseline
before tuning them.

## 9. Badcase Taxonomy v1

| Label | Initial detection |
|---|---|
| `constraint_violation` | Deterministic for encoded earliest-start/budget rules; human for unencoded constraints |
| `preference_miss` | Human |
| `ungrounded_poi` | Deterministic |
| `unsupported_fact` | Human v1; future claim/evidence contract may make it hybrid |
| `route_infeasible` | Deterministic when route data is available |
| `route_unavailable` | Deterministic and distinct from infeasible |
| `budget_overrun` | Deterministic against explicit cap |
| `budget_inconsistent` | Deterministic arithmetic |
| `overpacked` | Hybrid: deterministic risk heuristic plus human pacing review |
| `underpacked` | Human v1 |
| `revision_failed` | Deterministic with pre/post targeted risks |
| `unnecessary_revision` | Human v1; trigger-policy violations can be deterministic |
| `provenance_missing` | Deterministic for structured external facts |
| `excessive_cost` | Deterministic against versioned experiment threshold |
| `excessive_latency` | Deterministic against versioned experiment threshold |
| `patch_scope_drift` | Deterministic protected-day comparison |

Automatic labels must include evidence and metric-policy version. Human labels require
rationale. `route_unavailable` must never be relabeled as `route_infeasible`.

## 10. Phase 3B boundary

Phase 3B should implement only the offline runner, metric result model/calculators,
fixture resolver with network denial, run manifests and paired JSON/Markdown report.
It should not add a dashboard, online A/B tests, production Planner changes, user
accounts, memory, or an LLM judge.
