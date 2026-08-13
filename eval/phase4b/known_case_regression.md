# Phase 4B — Production Policy Offline Regression

Status: offline deterministic acceptance evidence. Real API/LLM/network calls: 0. Calculator: `backend/app/services/pacing_policy.py`; adapter: `backend/app/evaluation/pacing_regression.py`. Thresholds remain **PROPOSED** under `pacing.daily_load.v0.proposed`.

Inputs are the immutable artifacts referenced by Phase 3E human review: Shenzhen `phase3d4`, Chengdu/Lijiang `phase3d42`, and Kyoto `phase3d3`. No Golden Case was rerun.

| Case/day | Human pacing | Raw/effective attraction | Verified / estimated travel | Effective/window | Confidence | Production result |
|---|---:|---:|---:|---:|---|---|
| Shenzhen D1 | 2/5 | 420/420 | 68/0 | 673/660 = 1.020 | HIGH | revisable overload |
| Shenzhen D2 | 2/5 | 450/450 | 0/60 | 715/690 = 1.036 | MEDIUM | revisable overload |
| Shenzhen D3 | 2/5 | 420/420 | 0/60 | 685/750 = 0.913 | MEDIUM | warning; early-start flag |
| Chengdu D1 | 2/5 | 480/480 | 47/60 | 812/630 = 1.289 | MEDIUM | revisable overload |
| Chengdu D2 | 2/5 | 450/450 | 0/60 | 715/780 = 0.917 | MEDIUM | warning; early-start flag |
| Chengdu D3 | 2/5 | 450/450 | 86/0 | 721/660 = 1.092 | HIGH | revisable overload |
| Chengdu D4 | 2/5 | 510/510 | 42/60 | 837/720 = 1.163 | MEDIUM | revisable overload |
| Lijiang D1 | 2/5 | 450/450 | 0/120 | 820/570 = 1.439 | MEDIUM | revisable overload |
| Lijiang D2 | 2/5 | 480/480 | 0/120 | 850/690 = 1.232 | MEDIUM | revisable overload; early/suburban burden |
| Lijiang D3 | 2/5 | 360/360 | 0/120 | 730/600 = 1.217 | MEDIUM | revisable overload |
| Kyoto D1 control | 4/5 | 240/240 | 0/60 | 530/570 = 0.930 | MEDIUM | warning only |
| Kyoto D2 control | 4/5 | 300/300 | 0/60 | 590/570 = 1.035 | MEDIUM | warning only |
| Kyoto D3 control | 4/5 | 240/240 | 0/60 | 530/570 = 0.930 | MEDIUM | warning only |

Directionality is retained: each 2/5 badcase has revisable overload days, while the 4/5 control has none. The production classifier intentionally removes Phase 4A's city/POI-specific suburban hints. Consequently Shenzhen D3 is warning rather than overload: this is a possible false negative, but preferable to hidden case hardcoding. Chengdu D2 is also warning-only because its larger arithmetic window comes from 07:30; the independent early-start reason prevents the burden from disappearing.

Possible false positive: Lijiang D3 depends on two suburban fallback estimates and should remain confidence-qualified. Kyoto produces uncertainty warnings on all days but no revisable false positive. Theme-park and verified same-complex synthetic tests demonstrate normalization; the selected four artifacts contain no confidently recognized nested full-day pair.

Limitations: capture artifacts do not provide hotel legs, exact meal schedule, finish time, queues, structured elevation/internal mobility or structured inter-city duration. Unknown routes remain unavailable in capture and are separately represented as policy estimates; they are never rewritten as verified or infeasible.
