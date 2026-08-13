# Chengdu Paired Result

Case: `gc_chengdu_budget`

## Identity and human result

- Plan A: baseline — `planner_baseline_v1` / `planner_prompt_v1`
- Plan B: candidate — `planner_pacing_v1` / `planner_prompt_pacing_v1`
- Blind paired verdict: Plan B better
- Pair-level outcome: **BETTER**
- Blind-order integrity: `limitation`

| Dimension | Baseline | Candidate | Candidate − Baseline |
|---|---:|---:|---:|
| Preference Satisfaction | 4 | 4 | 0 |
| Itinerary Coherence | 4 | 4 | 0 |
| Pacing Quality | 3 | 4 | +1 |
| Usefulness | 3 | 4 | +1 |
| Explanation Quality | 4 | 4 | 0 |

Both plans received `unsupported_fact=uncertain`. The reviewer found no obvious underpacking or sacrifice of core food/city-exploration content.

## Constraints and deterministic evidence

The final candidate retained the peanut-avoidance guidance and a CNY 3,225 total under the CNY 3,500 ceiling. Both plans passed schema, date/day, budget arithmetic and budget-limit checks. Candidate grounding and provenance rates were 0.833 and 0.882, versus baseline 0.727 and 0.813. Baseline route metadata was absent; candidate route coverage was 0.5 and checked-route feasibility was 1.0.

## Revision and attribution

The final candidate contains a committed targeted pacing revision on zero-based Day 2. Its production load ratio moved from 1.032 (`revisable_overload`) to 0.986 (`warning`); protected days 0, 1 and 3 remained deeply equal. Day 4 still has material public-transport, transfer and internal-walking burden.

This revision is part of the final candidate, but the paired human difference may also arise from the initial pacing-aware Planner. It cannot be attributed to Revision alone.

## Known limitations

- `evaluation_semantics_limitation=true`: production resolution is 1.0, while the offline evaluator reports resolution 0 and `revision_failed=true` because it treats a downgraded warning with the same risk ID as unresolved. The offline result does not override production commit evidence.
- `grounding_outcome_capture=unavailable`: the successful grounding-gate outcome was not propagated into capture and is not inferred after the fact.
- Comparability is `limited_provider_drift`; live-provider differences prevent strict causal attribution.

Resume-safe claim: “In one Chengdu paired review, the pacing-aware candidate received higher human ratings for pacing and usefulness while preserving preference satisfaction, budget compliance, and the peanut-avoidance constraint. The final candidate included a committed targeted pacing revision; causal attribution remains limited by live-provider drift.”
