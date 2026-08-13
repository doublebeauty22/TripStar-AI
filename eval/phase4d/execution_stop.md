# Phase 4D Execution Stop Record

Date: 2026-08-13 (Australia/Sydney)

## Pre-flight

- Phase 4B/4C/4D targeted tests: 21 passed.
- Full backend regression: 288 passed, 1 skipped.
- Four baseline identities and manifest hashes verified.
- Baseline artifacts remained immutable.
- Production replay is not implemented; live-provider execution was therefore selected with provider-drift limitation.
- Code-freeze worktree-status digest: `e771af973dea84b2f6fd70bb38d06135f0e1ce6fafa11773b48fb2bfa14831c3`.

## Authorized execution attempted

Case: `gc_shenzhen_overbudget_revision`

- Planner identity: `planner_pacing_v1` / `planner_prompt_pacing_v1`
- Policy: `pacing.daily_load.v0.proposed`
- Revision: `pacing_revision.v1`
- Whole-case attempts: 1; no retry.
- Execution reached Planner, POI enrichment, initial Validator, typed pacing revision proposal and affected-day enrichment.
- Initial validation: degraded, 7 risks, 2 route API calls.
- Targeted revision candidate was rejected fail-closed as `grounding_regression`; original plan was retained.
- Capture then failed schema validation because typed operation dictionaries were written into the legacy `revision_instructions_metadata: list[str]` field.

Observed real LLM usage from the guarded execution log:

| Stage | Logical calls | Prompt tokens | Completion tokens | Total tokens |
|---|---:|---:|---:|---:|
| XHS research refinement | 1 | 3,590 | 9,406 | 12,996 |
| Planner | 1 | 15,472 | 3,935 | 19,407 |
| Pacing revision | 1 | 1,808 | 370 | 2,178 |
| Total | 3 | 20,870 | 13,711 | 34,581 |

Observed latency recorded by failure manifest: 123,698 ms. LLM retries: 0.

Observed provider operations: one XHS search flow; Google weather attempted and returned 404; AMap weather fallback used; Google hotel search used; 9 initial plus 4 affected-day POI enrichment lookups; 2 initial route calls. These are operation counts from logs, not guaranteed raw HTTP request counts.

## STOP condition

Triggered: `candidate artifact incomplete` / `capture_validation_failure`.

Per protocol, Shenzhen was not retried. Chengdu, Lijiang and Kyoto were not started because the same known capture schema defect could invalidate their artifacts, and fixing production/evaluation code during the declared execution code freeze would invalidate the run.

Failure artifact: `cases/gc_shenzhen_overbudget_revision/gc_shenzhen_overbudget_revision.failure.json` (`sha256:3781215f786c3882f8d3367863cf6a9e501d80d27f67cfbeb3c844ed49516469`). Its generic failure telemetry contains zero usage because the exception occurred after the executor returned, during capture-model construction; the guarded stage logs above are the available usage evidence.

## Consequences

- Comparable pairs completed: 0/4.
- Candidate captures completed: 0/4.
- Offline paired evaluation: not run.
- Blind human review: not generated; no candidate artifact exists.
- Human evidence: none collected.
- Product decision: pending; no BETTER/MIXED/WORSE claim is valid.
- Phase 4D status: stopped, incomplete.

Before a newly authorized run, fix and test both issues without reusing this failed attempt as a completed pair:

1. Make typed revision instruction capture schema-compatible without losing operation metadata.
2. Treat deterministic grounding improvements during affected-day re-enrichment as allowed while still rejecting degradation or invented grounding.
3. Add an end-to-end capture test that exercises a targeted pacing revision event through `build_revision_capture` and artifact serialization.
4. Preserve this failure artifact and request explicit authorization for any Shenzhen rerun.
