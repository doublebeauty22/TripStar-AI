# TripStar Planner Quality Baseline

## Baseline identity

- Status: **NOT_ESTABLISHED**
- Reason: real controlled Planner artifacts are missing for one or more Golden Cases
- Planner: `planner_baseline_v1`
- Prompt: `production-unversioned@96b9c5e-dirty`
- Model: `runtime-configured-not-captured`
- Code revision: `worktree:96b9c5e-dirty`
- Golden Cases: `golden.v1`
- Fixture set: `fixtures.v1`
- Metric policy: `metrics.v1`

This report is a baseline measurement, not an optimization result.

## Coverage

- Cases total: 16
- Cases evaluated: 0
- Cases failed: 0
- Cases containing unknown metrics: 0

## Deterministic aggregate metrics

| Metric | Aggregate | Numerator/denominator | Known | Unknown | N/A | Failed |
|---|---:|---:|---:|---:|---:|---:|


Unknown and not-applicable values are reported separately and are not silently removed.

## Automatic badcase distribution

- None

## Scenario breakdown

- **pace_relaxed**: 0 cases
- **pace_intensive**: 0 cases
- **budget_constrained**: 0 cases
- **budget_unconstrained**: 0 cases
- **mobility**: 0 cases
- **mobility_normal**: 0 cases
- **food_constraint**: 0 cases
- **food_normal**: 0 cases
- **provider_degraded**: 0 cases
- **provider_normal**: 0 cases
- **revision_trigger**: 0 cases
- **no_revision_trigger**: 0 cases

## Human review

Human review status: **PENDING**. No LLM-generated or simulated human scores are included.
Pending records: 16.

## Cost and latency

Token, call and latency distributions appear in the deterministic metrics table only
when real telemetry is available. Monetary cost is not estimated without a versioned
price source.
