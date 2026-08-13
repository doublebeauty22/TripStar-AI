# Lijiang Paired Result

Case: `gc_lijiang_places_unavailable`

## Identity and human result

- Plan A: baseline — `planner_baseline_v1` / `planner_prompt_v1`
- Plan B: candidate — `planner_pacing_v1` / `planner_prompt_pacing_v1`
- Blind paired verdict: Plan B better
- Pair-level outcome: **BETTER**
- Blind-order integrity: `limitation`

| Dimension | Baseline | Candidate | Candidate − Baseline |
|---|---:|---:|---:|
| Preference Satisfaction | 4 | 5 | +1 |
| Itinerary Coherence | 4 | 5 | +1 |
| Pacing Quality | 3 | 4 | +1 |
| Usefulness | 4 | 4 | 0 |
| Explanation Quality | 4 | 4 | 0 |

Both plans received `unsupported_fact=uncertain`. The reviewer found neither plan clearly underpacked and found no clear sacrifice of the core natural-landscape experience solely to improve pacing.

## Revision and attribution

Targeted pacing revision was triggered for affected days 1 and 2, but the proposal failed closed as `invalid_revision_output`. No typed operation was committed, affected-day enrichment and grounding gates were not reached, and the original candidate was retained. The overload therefore remained unresolved.

Any descriptive human-rating improvement belongs to the initial pacing-aware Planner candidate. It cannot be attributed to targeted Revision.

## Residual pacing and evidence limits

The candidate better reflects the requested relaxed structure, but does not fully solve pacing. Day 2 remains potentially dense because suburban travel, scenic-area access, queues, internal mobility and altitude burden are not captured by attraction durations alone. Candidate route coverage is 0, with route feasibility unknown; its grounding rate is 0.875 and provenance coverage 0.923.

Comparability remains `limited_provider_drift`. The TripRequest is identical, but provider snapshots differ, so the pair supports descriptive—not strict causal—comparison.

Resume-safe claim: “In one Lijiang paired review, the pacing-aware candidate received higher human ratings for preference satisfaction, itinerary coherence, and pacing while preserving the couple-oriented natural-landscape experience; relaxed pacing remained imperfect on the mountain day, and causal attribution remains limited by live-provider drift.”
