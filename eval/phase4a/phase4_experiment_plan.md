# Phase 4 Experiment and Architecture Plan

## Decision gate

1. Can pacing be meaningfully deterministic? **Yes**, as an explainable policy over structured load components plus explicit assumptions and confidence.
2. Are current data sufficient for Phase 4B? **Yes for a minimum viable policy**, validator and targeted revision; not sufficient for high-confidence access/queue/internal/inter-city modeling.
3. Does route unknown block evaluation? **No.** It lowers confidence and invokes conservative fallback; it never becomes zero or infeasible.
4. Does the policy identify known badcases? **Yes:** Shenzhen, Chengdu and Lijiang each have revisable overload days.
5. Obvious control false positives? **None at revisable level** in Kyoto; all three days remain warning-only because route evidence is incomplete.
6. New production data required before implementation? **No blocking field.** Strongly recommended additions are optional `end_time`, structured transfer duration/evidence, meal timing/duration, day/POI burden tags and route pass scope.
7. Phase 4B: **GO**, subject to the minimum contract below and proposed thresholds remaining feature-configured/versioned.

## Phase breakdown

| Phase | Goal | Code scope | Evidence required | Exit criteria | Real API/LLM budget |
|---|---|---|---|---|---|
| 4A | policy, audit, offline simulation, experiment spec | eval docs/config/prototype/tests only | immutable Phase 3 artifacts + code audit | policy/schema-valid; badcase/control simulation; gate decision | 0 |
| 4B | generate and validate against daily-load budget | versioned policy module; Planner context/prompt version; validator/risk schema; capture fields | unit/integration fixtures; no case-specific rules | deterministic recomputation; explainable risk; all existing regressions; candidate version isolated | development mocks 0; separately approved controlled candidate calls only after code gate |
| 4C | repair only overloaded days | revision trigger/contract, affected-day enforcement, revalidation | synthetic overloads + protected constraints/patch fixtures | targeted risks resolve or remain explicitly unresolved; unaffected days deep-equal; one revision max unless separately approved | mocks 0; separately approved small pilot budget |
| 4D | paired re-evaluation and product impact report | evaluation artifacts/reports, not baseline mutation | frozen requests/cases/provider snapshots, deterministic metrics, blinded human review | complete paired artifacts, rationales, deltas and non-regression decision | exact call/token ceiling must be approved before run; no open-ended calls |

## Phase 4B minimum implementation contract

- Keep production enum exactly `intensive | balanced | relaxed`.
- Introduce versioned deterministic `daily_load` computation with component/evidence/assumption output and no network/LLM dependency.
- Pass a compact pace-policy budget to a new Planner/prompt version; do not mutate `planner_baseline_v1` identity.
- Add `pacing_daily_load` to Validator and `pacing` to actionable risks; distinguish warning, revisable overload and degraded confidence.
- Consume verified routes; fallback unknown/unavailable with explicit class, estimate and uncertainty; keep infeasible separate.
- Preserve explicit earliest start, mobility, must-have, budget, city/date/day, hotel assumptions and protected days.
- Revision may mutate only risk day indices; enforce unaffected-day deep equality; re-enrich and rerun all validation once.
- Capture policy version, daily component evidence, confidence, route pass scope and revision targets for offline replay.
- Do not alter Patch behavior in 4B; only expose post-patch warning contract for later work.

## Success metrics

Primary:

1. Human `pacing_quality` paired by case.
2. Human `usefulness` paired by case.
3. Overload-day detection/resolution: baseline/candidate detected days, targeted days resolved after revision, and unresolved reason.

Secondary:

4. Explicit pace-policy satisfaction: days below revisable threshold, with warning/unknown reported separately.
5. Pacing revision-risk resolution rate.
6. Affected-day-only preservation rate/deep equality.

Non-regression: preference satisfaction, route coverage and unknown rate, grounded POI, provenance, schema validity, date/day consistency, budget arithmetic, and explicit constraint satisfaction. Efficiency: logical LLM calls, tokens and latency, always reported as paired deltas.

**PROPOSED acceptance thresholds (not proven):** all candidate artifacts schema-valid/date-consistent/budget-consistent; no decrease in explicit constraint satisfaction; no candidate baseline overwrite; no improvement claimed when route/grounding/provenance evidence is merely removed; all pacing revisions preserve unaffected days; each known badcase must either resolve its targeted overload or expose an explicit unresolved reason; no control case may gain a revisable overload solely from a low-confidence fallback without reviewer adjudication. Human results are reported case-by-case; no unsupported percentage target or statistical claim.

## Phase 4D paired design

Baseline identity: immutable `planner_baseline_v1` artifacts. Candidate: a new named pacing-aware planner/prompt/policy version stored under a separate run directory. Priority pairs: Shenzhen, Chengdu, Lijiang. Controls: Kyoto plus Beijing or Osaka after special-day normalization is implemented.

Pairing requirements:

- byte-equivalent `TripRequest` and same Golden Case version;
- same frozen Google/XHS/AMap/weather/directions snapshot hashes wherever replay permits;
- same metric policy and human.v1 rubric; pacing metric policy version recorded separately;
- baseline read-only; candidate path cannot equal or replace baseline path;
- offline deterministic evaluator over both outputs;
- pre-register all priority/control cases and exclusions before candidate generation;
- record planner, prompt, model, fixture and policy identity; reject mixed/ambiguous batches;
- compare route coverage/unknown rate so POI deletion cannot masquerade as pacing quality;
- randomize A/B display order and blind labels/version/scores from reviewers; reviewer sees neither baseline score nor prior rationale;
- one complete review per side, with reconciliation or second reviewer only under a predeclared disagreement rule.

Provider drift is prevented by snapshot replay and hash equality; if a required snapshot cannot be held equal, mark the pair non-comparable rather than silently using live data. Cherry-picking is prevented by the preregistered three badcases plus named controls and reporting every attempted/failing run. Prompt ambiguity is prevented by immutable version IDs and captured hashes. Candidate overwrite is prevented by separate directories and immutable manifests.

## Limitations and non-goals

This phase does not prove threshold universality, causal product improvement, statistical significance or provider reliability. It does not fix multi-city parsing, route availability, hotels, provenance UX, budgets, patch semantics, UI, or introduce ML/LLM evaluation. Theme parks, altitude, large scenic areas, overlapping districts and inter-city transfers still need structured burden tags or conservative/human-review treatment; no city/case-specific hardcoding is permitted.
