# Shenzhen Paired Result

Case: `gc_shenzhen_overbudget_revision`

## Identities and blind result

- Plan A: baseline — `planner_baseline_v1` / `planner_prompt_v1`
- Plan B: candidate — `planner_pacing_v1` / `planner_prompt_pacing_v1`
- Blind paired verdict: Plan B better
- Pair-level outcome: **BETTER**
- Blind-order integrity remains `limitation`.

| Human dimension | Baseline | Candidate | Candidate − Baseline |
|---|---:|---:|---:|
| Preference Satisfaction | 4 | 4 | 0 |
| Itinerary Coherence | 3 | 4 | +1 |
| Pacing Quality | 2 | 4 | +2 |
| Usefulness | 3 | 4 | +1 |
| Explanation Quality | 4 | 4 | 0 |

Both plans received `unsupported_fact=uncertain`; some external facts still require verification.

## Deterministic comparison

Both plans passed schema, budget arithmetic, and budget-limit checks. The candidate passed date/day consistency while the baseline did not. Grounded POI rate was 1.00 candidate versus 0.778 baseline; provenance coverage was 1.00 versus 0.857. Route evidence did not improve: candidate route coverage was 0.00 with feasibility unknown because no route legs were checked, versus baseline coverage 0.50 with known feasibility for checked legs.

## Revision and attribution

The targeted pacing revision triggered but was safely rejected as `retained_poi_grounding_regression`. It was not committed; the original candidate plan was retained, and the protected day remained unchanged. Therefore the human-rating difference describes the pacing-aware initial Planner candidate. It must not be attributed to targeted Revision.

## Comparability limitation

Comparability is `limited_provider_drift`: the TripRequest is identical, but provider snapshots are not exact. The result supports a descriptive single-case comparison, not a strict causal claim.

Resume-safe claim: “In one Shenzhen paired review, the pacing-aware candidate received higher human ratings for pacing, coherence, and usefulness while preserving preference satisfaction; causal attribution remains limited by live-provider drift.”

Prohibited claims include statistical significance, global Planner superiority, overall user-satisfaction improvement, cross-user proof, targeted-revision causality, or Phase 4D completion.
