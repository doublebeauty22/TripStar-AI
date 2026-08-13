# Kyoto Control Paired Result

- Plan A: baseline — `planner_baseline_v1 / planner_prompt_v1`
- Plan B: candidate — `planner_pacing_v1 / planner_prompt_pacing_v1`
- Blind verdict and case outcome: **MIXED**

| Dimension | Baseline | Candidate | Candidate − Baseline |
|---|---:|---:|---:|
| Preference Satisfaction | 4 | 4 | 0 |
| Itinerary Coherence | 5 | 5 | 0 |
| Pacing Quality | 4 | 4 | 0 |
| Usefulness | 4 | 3 | -1 |
| Explanation Quality | 4 | 4 | 0 |

Production committed duration reductions for Fushimi Inari (150→85 minutes) and Gion (120→70), resolving modeled overloads to warnings while preserving the 10:00 start constraint and protected Day 2. Human review found no actual pacing-score improvement and a one-point usefulness regression: a plausible over-correction where metric improvement did not improve user experience.

Grounding was weak (0.25 grounded POIs; provenance 0.333), routes were not verifiable, successful grounding outcome telemetry was unavailable, and offline revision semantics incorrectly reported failure despite production resolution 1.0. Comparability is `limited_provider_drift`, preventing causal attribution.

Resume-safe claim: “In one Kyoto control paired review, the candidate preserved preference satisfaction and coherence, but duration compression produced no human-rated pacing gain and reduced usefulness by one point; causal attribution remains limited by live-provider drift.”
