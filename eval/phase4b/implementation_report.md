# Phase 4B — Implementation Record

## Production architecture

`pacing_policy.py` owns the versioned proposed policy, normalization, route evidence contract, daily breakdown, confidence and status. Planner receives `compact_planner_contract`; Validator creates route evidence during its existing pass and then calculates each day without additional provider calls. `TripPlan` and `ValidationResult` carry policy version and assessments. Capture persists version, assessments, pacing risk IDs and validation pass scope with optional/default fields so old capture artifacts remain readable.

## Normalization

- Exact verified same-complex IDs use the maximum duration rather than summing duplicates.
- A generic full-day category with duration ≥420 minutes absorbs generic internal ride/project categories; external activities remain additive and lower confidence.
- Nearby verified coordinates without reliable parent evidence are not silently collapsed; they lower confidence with `possible_same_area_overlap_not_normalized`.
- No known-case city or POI name appears in production policy source.

## Route fallback

Verified/infeasible routes retain provider duration and source. Unavailable/unknown urban and suburban legs receive explicit policy estimates plus uncertainty while the original route-unavailable risk remains. Inter-city movement without structured duration yields LOW confidence and `effective_load_minutes=null`; it does not fabricate a precise total. A later verified value replaces the fallback input on recomputation.

## Revision boundary

Pacing overload risks have `revisable=true` as a future contract, but include `revision_execution_supported=false`. They are deliberately absent from the Phase 4B actionable trigger because the current Revision engine emits a complete plan and cannot enforce affected-day-only preservation. This fail-closed boundary leaves targeted execution to Phase 4C; existing earliest/budget/mobility/route revision behavior is unchanged.

## Identity

- Active Planner: `planner_pacing_v1`
- Active prompt: `planner_prompt_pacing_v1`
- Policy: `pacing.daily_load.v0.proposed`
- Frozen baseline constants remain `planner_baseline_v1` / `planner_prompt_v1`; no baseline artifact is modified.

## Scope boundaries

No Patch behavior, provider integration, route availability, multi-city parser, UI design, metrics policy or Golden Case execution is changed. Frontend type compatibility only adds the new backend `pacing` risk type.
