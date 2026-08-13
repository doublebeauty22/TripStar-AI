# Phase 4A — Known-case Offline Simulation

Status: **PROPOSED policy simulation**, not a new Golden Case run. Network/API/LLM calls: 0. Inputs are existing immutable capture artifacts referenced by human reviews. The evaluation-only prototype is `backend/app/evaluation/pacing_analysis.py`.

## Artifact selection

| Case | Pace | Human pacing | Artifact |
|---|---|---:|---|
| Shenzhen | balanced | 2/5 | `phase3d4/.../gc_shenzhen_overbudget_revision.json` |
| Chengdu | balanced | 2/5 | `phase3d42/.../gc_chengdu_budget.json` |
| Lijiang | relaxed | 2/5 | `phase3d42/.../gc_lijiang_places_unavailable.json` |
| Kyoto control | relaxed | 4/5 | `phase3d3/.../gc_kyoto_no_early_start.json` |

The Chengdu/Lijiang paths deliberately match the review records rather than later same-name pilot copies.

## Daily results

Components are `A/V/E/M/X/R/U = attraction / verified travel / estimated travel / meals / access / rest / uncertainty`, all in minutes. `effective/window` gives the ratio.

| Case/day | Start | A | V | E | M | X | R | U | Effective / window | Confidence | Result | Main explanation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| Shenzhen D1 (artifact index 1) | 09:30 | 420 | 68 | 0 | 105 | 30 | 50 | 0 | 673/660 = 1.020 | HIGH | revisable overload | attractions plus verified mobility exceed balanced maximum |
| Shenzhen D2 (index 2) | 09:00 | 450 | 0 | 60 | 105 | 30 | 50 | 20 | 715/690 = 1.036 | MEDIUM | revisable overload | 450 activity minutes leave insufficient mobility/meal/rest capacity |
| Shenzhen D3 (index 3) | 08:00 | 420 | 0 | 120 | 105 | 30 | 50 | 40 | 765/750 = 1.020 | MEDIUM | revisable overload | early start + suburban excursion + unknown legs |
| Chengdu D1 | 10:00 | 480 | 47 | 60 | 105 | 50 | 50 | 20 | 812/630 = 1.289 | MEDIUM | revisable overload | five POIs and route/buffer load |
| Chengdu D2 | 07:30 | 450 | 0 | 60 | 105 | 30 | 50 | 20 | 715/780 = 0.917 | MEDIUM | warning | arithmetic fits only through a very early start; early-start burden remains |
| Chengdu D3 | 09:30 | 450 | 86 | 0 | 105 | 30 | 50 | 0 | 721/660 = 1.092 | HIGH | revisable overload | verified mobility pushes the day over maximum |
| Chengdu D4 | 08:30 | 510 | 42 | 120 | 105 | 50 | 50 | 40 | 917/720 = 1.274 | MEDIUM | revisable overload | five POIs plus suburban movement |
| Lijiang D1 | 10:00 | 450 | 0 | 60 | 105 | 30 | 75 | 20 | 740/570 = 1.298 | MEDIUM | revisable overload | relaxed window cannot absorb activity plus unknown mobility |
| Lijiang D2 | 08:00 | 480 | 0 | 120 | 105 | 30 | 75 | 40 | 850/690 = 1.232 | MEDIUM | revisable overload | early start, mountain excursion, altitude and unknown legs |
| Lijiang D3 | 09:30 | 360 | 0 | 60 | 105 | 30 | 75 | 20 | 650/600 = 1.083 | MEDIUM | revisable overload | fallback movement and relaxed recovery push it just beyond maximum |
| Kyoto D1 control | 10:00 | 240 | 0 | 60 | 105 | 30 | 75 | 20 | 530/570 = 0.930 | MEDIUM | warning only | unknown routes prevent a clean pass, but no automatic revision |
| Kyoto D2 control | 10:00 | 300 | 0 | 60 | 105 | 30 | 75 | 20 | 590/570 = 1.035 | MEDIUM | warning only | near limit and estimate-dependent, below relaxed revision threshold |
| Kyoto D3 control | 10:00 | 240 | 0 | 60 | 105 | 30 | 75 | 20 | 530/570 = 0.930 | MEDIUM | warning only | same confidence caveat; no automatic revision |

## Interpretation

Direction agrees with Yi Huang's review: all three 2/5 badcases contain at least one revisable overload; Kyoto 4/5 has no revisable overload. The policy was not tuned for perfect day-by-day agreement. Chengdu D2 is the clearest partial false negative: human review considered the early 07:30 start and long movement part of multi-day overpacking, while arithmetic status is only warning. The independent early-start flag preserves the concern but would not trigger pacing revision alone under balanced policy unless product chooses to make it actionable.

Potential false positives: Lijiang D3 is only 3.3 percentage points over the proposed relaxed threshold and depends on fallback route assumptions; it should be treated as estimate-dependent and included in threshold sensitivity review. Kyoto's three warnings are expected confidence warnings, not overload false positives. No control day becomes `revisable_overload`.

Potential false negatives: internal scenic-area movement, hotel legs, queues and meal timing are absent, so days can be heavier than computed. Conversely, overlapping districts or nested theme-park rides can double-count Planner durations; theme-park/continuous-area days require normalization rules before Phase 4B enforcement. Route observations can also contain validation-pass duplicates; the prototype deterministically de-duplicates by endpoint and prefers a known duration, but production needs explicit pass-scope selection.

Conclusion: the model identifies meaningful known badcases without declaring unknown routes infeasible or zero. Results are calibration evidence, not proof of general precision/recall.
