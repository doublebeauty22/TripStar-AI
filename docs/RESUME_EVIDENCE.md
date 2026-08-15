# TripStar Resume and Interview Evidence

This document separates repository-supported facts from future measurement opportunities. It is not marketing copy. `docs/PROJECT_BASELINE.md` and current code are authoritative when a claim changes.

## Verified project facts

- TripStar-AI is a Vue 3/TypeScript and FastAPI/Python travel-planning application.
- It accepts structured single- or multi-city trip requests and preference profiles, including party, pace, interests, budget, transport, accommodation, dates, and free text.
- It uses an OpenAI-compatible LLM through HelloAgents for bounded planning and optional parsing/repair/revision/patch/chat stages.
- It contains Google Maps, AMap, and XHS adapters, but STEP 0 did not verify their live credentials or production behavior.
- It uses process-local async task coordination, WebSocket progress, HTTP polling recovery, local JSON task persistence, and browser storage.
- Docker configuration builds and serves the Vue frontend and FastAPI backend as one single-worker service. No live deployment was verified.

## Verified technical and product capabilities

- Structured Pydantic itinerary, preference, evidence, risk, validation, patch/diff, graph, and chat contracts.
- Bounded sequential LLM orchestration with logical-call accounting and classified retry handling.
- Deterministic POI map-fact enrichment and explicit verified/partial/unverified/unavailable states.
- Deterministic validation for budget, start time, pacing, route/mobility, and grounding concerns.
- Guarded targeted revision and versioned local patch workflows with safety checks, diffs, and revalidation.
- Long-running task UX with progress events, polling fallback, refresh recovery, terminal errors, and active-request deduplication.
- Result rendering for itineraries, budgets, weather/source states, risks, maps, photos, and a deterministic presentation knowledge graph.
- Separate contextual Q&A and patch modes, plus a read-only provider-independent example trip.

These capabilities have different evidence boundaries: core code and mocked tests exist, while live provider quality and production behavior remain unverified.

## Verified quality evidence

- Backend STEP 0 suite: 327 run, 325 passed, 1 failed, 1 skipped. The single failure is a stale commit-SHA assertion, not a passing-suite claim.
- Frontend STEP 0 tests: 34/34 passed; these are not browser E2E tests.
- Frontend TypeScript/Vite production build passed with unresolved asset/font and oversized-chunk warnings.
- `git diff --check` passed in STEP 0.
- Committed evaluation contracts, fixtures, captures, reports, and human-review artifacts exist under `eval/`; their historical quantitative findings were not independently reproduced during STEP 0.

## Potential future resume metrics

Values are deliberately absent until measured with a dated, reproducible protocol.

- Itinerary constraint satisfaction rate.
- Grounded POI rate, split by verified/partial/unverified state and provider.
- Unsupported recommendation rate and per-field source coverage.
- Itinerary generation success rate.
- Median and P95 end-to-end planning latency, plus stage-level/provider latency.
- Patch interpretation, safe-commit, rejection, and regeneration-required rates.
- Provider failure recovery/degradation rate.
- Task completion, WebSocket recovery, refresh recovery, and restart-loss rates.
- Budget adherence and pacing-risk rates on a versioned evaluation set.
- JSON parse/repair/retry rates and LLM calls/tokens/cost per completed plan.
- Browser performance, accessibility, export success, and map/photo load rates.

Every quantitative resume claim must include:

- Metric definition.
- Methodology/instrumentation.
- Sample size and selection criteria.
- Baseline/comparator.
- Result, including uncertainty where appropriate.
- Measurement date and code/model/provider versions.
- Evidence location in the repository or approved external system.

## Claims currently safe to use

- “Built an evidence-aware travel-planning prototype with a Vue/FastAPI interface, structured itinerary schemas, and bounded OpenAI-compatible LLM orchestration.”
- “Implemented deterministic checks around generated itineraries for budget, pacing, start-time, route/mobility, and POI grounding risks.”
- “Designed long-running task handling with WebSocket progress, polling recovery, refresh recovery, and local JSON result persistence for a single-process deployment model.”
- “Added guarded, typed itinerary revision/patch workflows that validate changes and expose diffs rather than silently replacing the plan.”
- “Created versioned offline evaluation contracts, fixtures, captures, reports, and human-review artifacts; current-head historical evaluation findings require revalidation before quantitative use.”
- “At the audited baseline, 34/34 frontend tests passed and the production frontend build succeeded; the backend suite had 325 passing, 1 failing stale-SHA assertion, and 1 skipped test out of 327.”

Claims should retain qualifiers such as prototype, single-process, configured integration, or mocked verification where material.

## Claims not safe to use yet

- User growth, active-user, adoption, retention, conversion, engagement, or satisfaction claims.
- Revenue, cost savings, bookings, commercial impact, or other business outcomes.
- Production uptime, availability, scale, throughput, task completion, or provider-recovery percentages.
- Latency or cost improvement percentages, including median/P95 claims.
- Recommendation accuracy, itinerary quality, grounding, hallucination, or constraint-satisfaction improvements without a current controlled methodology.
- Claims of fully autonomous or multi-agent decision-making.
- Claims that all recommendations are sourced, all POIs are verified, or plans are guaranteed feasible/current.
- Claims of a currently healthy public deployment or verified live Google/AMap/XHS/LLM integration.
- Statistical significance, causal impact, universal superiority, or production-traffic validation from the committed Phase 4D artifacts.
- A fully passing backend suite at the STEP 0 baseline.
