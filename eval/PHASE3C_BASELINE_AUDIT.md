# Phase 3C Planner Baseline Audit

Audit date: 2026-08-12. Repository revision: `96b9c5e` with a dirty worktree.

## Version and artifact findings

- The repository does not contain two reproducible historical Planner versions. The production prompt is a module constant without an independent version identifier, and the model is selected from runtime configuration. No V1/V2 comparison is therefore claimed.
- The intended current identity is `planner_baseline_v1`, prompt `production-unversioned@96b9c5e-dirty`, model `runtime-configured-not-captured`. This is an intended baseline identity, not evidence that a baseline was measured.
- Seventeen task files exist under `backend/data/trip_tasks`. They are historical, uncontrolled product artifacts: none is an exact execution of a Golden Case with the required frozen fixture set and fixture hashes.
- Some later task files contain model/usage, validation, revision or patch metadata. Across the set, latency, complete provider status, fixture identity, route checks and complete provenance evidence are absent or inconsistent. They cannot establish the controlled Phase 3C baseline.
- No saved artifact qualifies as `real_planner` for the sixteen Golden Cases. Acceptance-demo artifacts are synthetic and are excluded from product-quality measurement.

## Metric feasibility from current saved data

Directly computable when a valid `TripPlan` is present:

- `schema_valid`, `date_day_consistency`
- deterministic explicit constraints represented by the Golden Case contract
- `earliest_start_satisfaction`
- `budget_arithmetic_consistency`, and `budget_limit_satisfaction` when a limit exists
- POI grounding and provenance metrics when the structured match/source fields are present
- `actionable_risk_count`

Requires additional saved evaluation state or telemetry, without changing Planner behavior:

- `route_check_coverage` and `route_feasibility_rate`: sanitized route-check records and unavailable status
- `revision_risk_resolution_rate`: before/after risks plus targeted risk identity
- `unaffected_day_preservation_rate`: before/after patch plans plus protected day indices
- logical calls and token fields: complete usage metadata for every run
- `latency_ms`: monotonic execution timing captured around artifact generation
- provider-vs-Planner attribution: provider status and fixture hashes attached to the artifact

Cannot be inferred reliably from current artifacts and must remain `unknown`, `not_applicable`, or human pending as defined by Phase 3A:

- unrestricted prose `unsupported_fact`
- preference satisfaction, pacing/coherence, usefulness and explanation quality
- unnecessary revision, overpacked and underpacked
- monetary cost without captured model identity and a versioned price source
- route feasibility when no route check exists; provider unavailable is not route infeasible

## Golden Case execution boundary

- All 16 cases are structurally runnable only through a production-compatible artifact-generation process.
- The offline runner itself must never generate them because the production Planner requires an LLM and provider calls.
- No paid or network execution was authorized or performed in Phase 3C. Consequently, zero cases have qualifying real artifacts in this baseline attempt.
- A future controlled run can use the same frozen fixture set only after production-compatible dependency injection/execution seams can consume those fixtures outside the offline evaluator. The current repository does not prove this end-to-end path.

## Baseline decision

The batch/report implementation is ready to consume saved Phase 3B run artifacts, but `Planner Quality Baseline` is **NOT ESTABLISHED**. Product strengths, weaknesses, Top Badcases, scenario failures, grounding acceptability, route attribution, revision effectiveness and cost/quality trade-offs all have **insufficient evidence**.

The next evaluation step requires explicit authorization for controlled Planner artifact generation, deterministic sampling settings where supported, complete telemetry capture, sanitization, then offline evaluation. A real candidate comparison must wait for a genuinely changed and versioned Planner.
