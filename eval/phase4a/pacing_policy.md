# Phase 4A — Pacing Policy

Status: **PROPOSED / evaluation-only**. This document defines a Phase 4B contract; it does not change production behavior. Machine-readable constants are in `pacing_policy.json`.

## 1. Repository audit and current data-flow

```text
User pace
→ PreferenceParseRequest.pace
→ PreferenceProfile.pace on TripRequest
→ Planner query: one translated text label
→ TripPlan / DayPlan / Attraction / Meal
→ deterministic POI enrichment
→ deterministic route validation
→ Validator risks
→ actionable-risk filter → Critic → one Revision → enrichment → revalidation
→ metrics.v2 + human.v1
```

| Boundary | Current behavior | Pacing significance |
|---|---|---|
| Request/profile | `TravelPace = intensive | balanced | relaxed`; explicit pace survives preference parsing | Reliable structured input |
| Planner | Query contains only `旅行节奏: 特种兵/适中/松弛度假`; global prompt still says 2–3 POIs/day | Pace is a text hint; no load budget or threshold |
| Plan | `DayPlan.start_time`, `is_transfer_day`, `transfer_info`, `transportation`, attractions and meals; `Attraction.visit_duration` | Deterministic structured evidence after parse, but visit duration originates from Planner and is an estimate |
| Meals | Type/name/cost, no duration or scheduled time | Presence is evidence; meal minutes must be policy assumptions |
| Enrichment | Verified/partial/unverified identity, map source and coordinates | Reliable eligibility evidence; does not verify visit duration |
| Routes | Adjacent verified Google POIs can produce duration/distance/mode; unavailable observations are preserved | Verified duration is strong evidence; unavailable/unknown is not zero and not infeasible |
| Validator | Earliest start, budget, route feasibility, mobility; warns above 180 verified route minutes or 10h visit+verified-route load | Deterministic seam exists, but no pace-aware meals/rest/access/unknown-route policy |
| Revision | Actionable types are only earliest_start/budget/mobility/route_feasibility; one critic/revision/revalidation cycle | No `pacing` risk type; affected-day-only preservation is prompt-level, not day-deep-equality enforcement |
| Patch | Deterministic local operations and protected-day deep equality; `update_day_pace` simply removes the last POI | Strong scope evidence, but no post-patch pacing contract or global semantic repair |
| metrics.v2 | Route, grounding, provenance, constraints, revision, preservation, usage | `overpacked/underpacked` remain human-only in badcases v1 |
| human.v1 | Explicit pacing rubric covers density, duration, rest and mobility | Correct outcome measure, not a deterministic evaluator |

Existing reusable evidence: pace enum, start time, attraction duration, meal presence, transfer-day flag/text, transportation mode, POI grounding, route duration/distance/status, mobility notes, earliest-start constraint, risks, revision targets, protected days and capture identity/snapshots. Unreliable or unavailable: route for unverified POIs/provider failures; meal/access/queue/rest duration; finish time; hotel↔first/last POI legs; internal attraction mobility; inter-city duration as structured evidence; altitude/elevation; whether broad/nested POIs overlap. `transfer_info` and descriptions are unverified text and may only trigger conservative flags, not fabricated exact minutes.

Golden Cases cover relaxed, balanced and intensive. Explicit earliest-start exists for Kyoto; mobility notes for Hangzhou; transfer-day fields exist for multi-city plans. There is no production `packed` value: the equivalent current value is `intensive`.

## 2. Formal problem definition

Pacing is daily execution load, not POI count. It combines:

- attraction load: planned POI visit duration;
- mobility: verified duration, estimated/fallback duration, walking burden and long transfers;
- meals: time allocation for meals occurring in the day window;
- access overhead: queue, security, ticketing, reservation, entrance/exit and local transfer;
- recovery: ordinary breaks plus pace-specific slack;
- special burdens: early/late day, altitude, full-day venue, suburban/inter-city excursion, repeated walking, uncertain routes and high internal mobility.

The model keeps six concepts separate: `planned activity time`, `verified mobility time`, `estimated mobility time`, `fixed buffers`, `uncertainty buffer`, and `special burden`. Special burdens do not all become fake minutes.

## 3. Deterministic daily-load model

```text
effective_load_minutes =
  attraction_minutes
  + verified_travel_minutes
  + estimated_travel_minutes
  + meal_minutes
  + access_buffer_minutes
  + rest_buffer_minutes
  + uncertainty_buffer_minutes
```

`available_day_window_minutes = policy_finish(pace) - planned_start_time`, clamped to 360–810 minutes. Proposed finish boundaries are 19:30 relaxed, 20:30 balanced and 21:30 intensive. They are product policy assumptions, not claims about attraction opening hours. A future explicit user finish constraint must override the default. Starting earlier increases the arithmetic window but can independently violate early-start policy; it must not silently “buy” permission for an unwanted early start.

Required output fields are `attraction_minutes`, `verified_travel_minutes`, `estimated_travel_minutes`, `meal_minutes`, `access_buffer_minutes`, `rest_buffer_minutes`, `uncertainty_buffer_minutes`, `effective_load_minutes`, `available_day_window_minutes`, `load_ratio`, `confidence`, `overload_status`, and `evidence_sources`.

Evidence versus assumptions:

- Evidence: pace, start time, visit-duration values, meal presence, day/transfer flags, route status/duration/distance/mode, grounding and explicit constraints.
- Assumptions: finish boundary, meal allocation, access/rest buffers, fallback route class/duration, uncertainty allowance and thresholds. Every report must expose both lists.

POI count is only a secondary warning (4 relaxed, 5 balanced, 6 intensive) and can never determine overload alone.

## 4. Pace policy

| Pace | Target ratio | Maximum / revisable | Rest minimum | Early-start policy | Long-transfer / uncertainty tolerance |
|---|---:|---:|---:|---|---|
| relaxed | 0.55–0.78 | 1.05 | 75 min | before 09:00 discouraged; ≤08:00 strong warning | low / low; >1 fallback leg warns |
| balanced | 0.62–0.86 | 1.00 | 50 min | before 08:30 discouraged; ≤08:00 strong warning | medium / medium; >2 fallback legs warns |
| intensive | 0.72–0.98 | 1.10 | 30 min | before 07:30 discouraged | high / medium; >3 fallback legs warns |

Below target is not automatically “underpacked”: free time can be intentional, especially relaxed travel. Above the target is a warning; at/above the revisable threshold is an actionable overload. A hard constraint violation (for example explicit earliest start) remains independently blocking.

These are calibration candidates, not proven universal limits. Phase 4D must report sensitivity around thresholds and may revise them without city-specific hardcoding.

## 5. Confidence-aware fallback

- **HIGH:** all required adjacent mobility durations are verified. Replace them directly if a newer provider snapshot is deliberately selected.
- **MEDIUM:** one or more credible estimates are used, with explicit route class and range/bucket provenance.
- **LOW:** an unbounded inter-city leg, missing endpoints, or multiple weak fallbacks remains. It is still evaluable with a conservative allowance, but the report must say the overload conclusion is estimate-dependent.

Fallback classification is urban/suburban/inter-city. Proposed point values for offline arithmetic are 30/60 minutes per unknown intra-day leg; inter-city remains unbounded unless structured duration evidence exists. Uncertainty adds 10/20/30 minutes respectively. Product UI/reporting must label these `estimated` and show a bucket (for example “urban fallback, approximately 20–40 min”), not present `30` as provider precision. When a provider recovers, replace the estimate and its uncertainty entry by the verified value, retain evidence lineage, then recompute.

`route unavailable` and `route unknown` lower confidence; neither means infeasible. `route infeasible` is separate verified evidence and remains a route risk even when pacing arithmetic happens to fit.

## 6. Special-burden policy

| Situation | Treatment |
|---|---|
| Start at/before 08:00 | categorical warning; hard constraint only when earlier than explicit `earliest_start_time`; soft pace constraint otherwise |
| Attraction duration >480 balanced/relaxed or >600 intensive | soft/revisable warning; use total load for final decision |
| Long suburban excursion | larger route/uncertainty buffer plus categorical warning; no invented round-trip precision |
| High altitude | categorical warning + required recovery review; human-review flag until structured elevation/health inputs exist |
| Theme-park full day | categorical day type; avoid double-counting nested rides as independent external POIs; queue burden is a range/flag, not a universal minute penalty |
| Large/repeated walking | verified distance threshold remains mobility risk; pace severity can be stricter for relaxed/mobility-constrained users |
| Multiple unknown routes | confidence downgrade + uncertainty allowance; warning after pace-specific count; never infeasible by default |
| Inter-city transfer day | hard requirement for structured transfer-duration evidence before HIGH/MEDIUM confidence; otherwise LOW plus warning; attractions use transfer-day policy |
| “Switching day not too full” | explicit hard planner/validator constraint: transfer plus only optional light activity; violation revisable |
| Significant internal mobility | categorical warning unless structured internal duration exists; altitude/theme-park/large scenic areas require human review during calibration |

## 7. Future Validator contract

Add `RiskType = pacing` and `rule_id = pacing_daily_load`. One risk per affected day:

```json
{
  "rule_id": "pacing_daily_load",
  "day_index": 3,
  "pace": "balanced",
  "effective_load": 765,
  "available_window": 750,
  "load_ratio": 1.02,
  "confidence": "MEDIUM",
  "overload_reasons": ["effective_load_exceeds_maximum", "suburban_excursion", "start_at_or_before_08_00"],
  "evidence": {"attraction_minutes": 420, "estimated_travel_minutes": 120, "assumption_policy": "pacing.daily_load.v0.proposed"},
  "severity": "warning",
  "revisable": true
}
```

Message form: “Day 4 is overloaded: 420 attraction minutes plus estimated suburban mobility and meal/access/rest/uncertainty buffers produce 765 effective minutes, above the balanced daily-load policy for the available window.” Confidence and estimated/verified labels are mandatory. Missing evidence produces a degraded-confidence pacing result, not a fabricated pass.

## 8. Revision contract

Revision receives only pacing risks selected by the deterministic trigger and may change only `risk.day_index` days. Proposed order:

1. Preserve must-haves; identify optional activities and duplicated/nested items.
2. Reorder geographically only when verified/credible spatial evidence supports it.
3. Remove or shorten an optional activity whose duration is explicitly an estimate.
4. Replace a distant optional POI with a grounded closer alternative when evidence exists.
5. Move an optional activity to a genuinely underloaded compatible day, only if that day is not protected and no duplicate/city/date conflict results.
6. Adjust start time only within explicit earliest/no-early-start constraints; never use an unwanted earlier start to solve overload.

Protected: must-have POIs, special constraints, budget cap/arithmetic, protected patch days, city allocation, accommodation assumptions and no-early-start requirements. The revised plan must be re-enriched and rerun pacing plus all existing validation. Phase 4B should enforce affected-day deep equality, not rely only on prompt prose.

## 9. Patch and route interaction

After a local patch, calculate pacing for affected days and scan global duplicate/conflict keys:

- A — repair stays within affected day and respects the user instruction: offer/apply an affected-day-only follow-up operation, then validate.
- B — repair would alter a protected day: do not modify; warn, explain the protected conflict, request confirmation and suggest a follow-up patch.
- C — cross-day duplicate/factual conflict: do not silently edit either day; emit a global-consistency warning with both days and suggested alternatives.

Phase 4 does not repair provider availability. It consumes `verified`, `unavailable`, `unknown`, and `infeasible` distinctly, preserves route metrics/unknown rate, and must not make route quality look better merely by removing evidence.

## 10. Non-goals and limitations

No multi-city parser fix, route-provider fix, planner refactor, hotel optimization, provenance UX redesign, budget overhaul, patch-engine change, UI redesign, ML pacing model, LLM pacing judge, or case/city-specific tuning. Current data lacks structured finish times, meal schedules, access/queue duration, hotel legs, elevation, internal mobility and reliable inter-city duration. Phase 4B can start with explicit policy assumptions, but these gaps cap confidence and should be added incrementally as structured optional fields, not blockers for all pacing evaluation.
