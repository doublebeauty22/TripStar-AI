# Phase 4D — Four-pair Product Impact Report

All four pairs used identical TripRequests within each pair and immutable baselines. Every pair has `limited_provider_drift`; results are descriptive and non-causal.

| Case | Baseline scores P/C/P/U/E | Candidate scores P/C/P/U/E | Delta | Blind verdict | Outcome | Revision | Key limitation |
|---|---|---|---|---|---|---|---|
| Shenzhen | 4/3/2/3/4 | 4/4/4/4/4 | 0/+1/+2/+1/0 | Plan B better | BETTER | Rejected: grounding regression | Provider drift; route evidence weak |
| Chengdu | 4/4/3/3/4 | 4/4/4/4/4 | 0/0/+1/+1/0 | Plan B better | BETTER | Committed | Day 4 residual load; offline semantics conflict |
| Lijiang | 4/4/3/4/4 | 5/5/4/4/4 | +1/+1/+1/0/0 | Plan B better | BETTER | Rejected: invalid typed output | Mountain day remains dense; estimate dependence |
| Kyoto control | 4/5/4/4/4 | 4/5/4/3/4 | 0/0/0/-1/0 | Mixed | MIXED | Committed | Duration compression over-correction |

Dimension order is Preference Satisfaction / Itinerary Coherence / Pacing Quality / Usefulness / Explanation Quality.

## Descriptive aggregate

- Completed pairs: 4
- Outcomes: 3 BETTER, 1 MIXED, 0 WORSE, 0 EQUIVALENT
- Mean Candidate−Baseline delta: Preference +0.25; Coherence +0.50; Pacing +1.00; Usefulness +0.25; Explanation 0.00
- Positive pacing delta: 3/4; negative usefulness delta: 1/4
- Revisions: 4 triggered; 2 committed; 2 rejected
- Rejection reasons: one `retained_poi_grounding_regression`; one `invalid_revision_output`
- Clear initial-planner rather than revision improvements: Shenzhen and Lijiang
- Possible over-correction: Kyoto control
- Constraint preservation where evaluable: 4/4

No statistical significance or causal effect is claimed at n=4, especially because all pairs have live-provider drift.

## Product decisions

- **Pacing-aware Planner: PASS** for this evaluation milestone. Human pacing rose in three pairs and did not fall; preference and usefulness were preserved on average. This is not a production-release or causal claim.
- **Targeted pacing Revision: ITERATE.** Reliability is insufficient: only two revisions committed, typed output failed once, grounding rejected once, and Kyoto exposed metric-driven duration over-compression with a usefulness regression.
- **Phase 4D overall: ITERATE.** Keep the pacing-aware planner direction, but repair revision reliability, constrain duration reductions, propagate grounding outcome telemetry, fix offline warning-resolution semantics, and improve grounding/route evidence before stronger claims.

## Portfolio-safe claims

- Across four controlled paired evaluations, candidate plans had a mean human pacing delta of +1.00 and positive pacing deltas in 3 of 4 pairs.
- Mean human deltas were +0.25 Preference Satisfaction, +0.50 Itinerary Coherence, +1.00 Pacing Quality, +0.25 Usefulness, and 0.00 Explanation Quality.
- All four evaluable pairs preserved explicit constraints; two of four targeted revisions committed and two were safely rejected.
- The Kyoto control identified a concrete over-correction risk: lower modeled load with unchanged human pacing and a one-point usefulness decline.
- All claims are descriptive; every pair had limited live-provider comparability.

## Claims that must not be made

- Statistical significance or proven causality
- Overall customer satisfaction, conversion, retention or production-traffic improvement
- Global superiority of the planner or universal validation of pacing thresholds
- Targeted Revision alone caused human improvements
- Phase 4 is production-ready or validated across users

## Remaining blockers

- Typed revision output can fail.
- Retained POI grounding can regress during enrichment.
- Duration-only revision can over-optimize the metric.
- Successful grounding outcomes are not propagated to capture.
- Offline revision resolution mishandles overload-to-warning transitions.
- Grounding/provenance and route availability remain weak and drift across live captures.
