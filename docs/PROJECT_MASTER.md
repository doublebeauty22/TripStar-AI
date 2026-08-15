# TripStar Project Master

## Project identity

- **Name:** TripStar-AI (repository: TripStar).
- **Purpose:** A travel-planning application that converts structured trip details and natural-language preferences into a structured itinerary, then exposes grounding state, validation risks, progress, follow-up Q&A, and bounded local modifications.
- **Repository status:** The audited application/code baseline is `main` at `449d0fa584b99f61386b980fe76aac53848de871`. STEP 0 began from a clean working tree; later documentation-only commits do not change that code baseline.
- **Evidence boundary:** The repository proves local implementation and deployment configuration. It does not prove a live deployment or working external credentials.

## Product goal

TripStar addresses the difficulty of turning travel preferences and constraints into a usable day-by-day plan. It combines optional provider research with LLM itinerary generation and deterministic checks. It does not provide bookings, authoritative prices, universal factual grounding, or guaranteed itinerary feasibility.

## Current verified capabilities

- **Implemented:** single- and multi-city trip requests; structured preference collection and fallback parsing; structured `TripPlan` output; budget estimates and budget-limit validation; deterministic pacing, route/mobility, grounding, and start-time checks; asynchronous task progress; WebSocket with polling recovery; refresh recovery for persisted tasks; contextual trip Q&A; presentation knowledge graph; sanitized example trip.
- **Partial:** external research, maps, weather, hotels, source attribution, local patch-based itinerary modification, image export, and user/task persistence. These paths have material provider, grounding, export, or single-instance limitations.
- **Not implemented:** authentication/authorization, user accounts, database, durable queue, distributed task coordination, in-flight restart recovery, undo, booking/inventory integrations, and live production verification.

## Current AI architecture

The system is a bounded sequential orchestration pipeline, not a network of autonomous agents. A HelloAgents `SimpleAgent` performs the main planner call. Optional LLM stages parse free text, extract XHS material, interpret patches, critique/revise eligible output, or repair malformed JSON. Provider retrieval, POI enrichment, validation, pacing calculations, patch application, graph construction, persistence, and task coordination are deterministic services. LLM calls share stage-level accounting, a default per-trip logical-call limit of five, and bounded retry behavior.

## Current technical architecture

- **Frontend:** Vue 3, TypeScript, Vite, Vue Router/I18n, Ant Design Vue, Axios, ECharts, and html2canvas.
- **Backend:** Python/FastAPI, Pydantic schemas, asyncio background tasks, Uvicorn/Gunicorn deployment path, HTTPX/AIOHTTP provider adapters.
- **Providers:** OpenAI-compatible LLM, Google Maps services, AMap, and XHS are referenced/configurable; live validity was not verified.
- **State:** process-local tasks, subscriber queues, locks, dedupe, and rate guards; local JSON terminal-task persistence; browser session/local storage. No database, shared cache, broker, or durable worker system.
- **Deployment:** a single-service multi-stage Docker build serves Vue and FastAPI with one worker. Configuration exists; no deployed service was verified.

## Current quality baseline

STEP 0 at HEAD `449d0fa` recorded:

- Backend: 327 tests run; 325 passed, 1 failed, 1 skipped. The failure is a stale assertion expecting commit `96b9c5e`.
- Frontend: 34/34 Node tests passed.
- Frontend `npm run build`: passed, with unresolved legacy asset/font paths and oversized chunk warnings.
- `git diff --check`: passed.

## Known limitations

- Single-process coordination does not safely support multiple workers or horizontal scaling.
- Unfinished tasks are marked failed after restart rather than resumed.
- No authentication, task ownership enforcement, database, reliable queue, or user accounts.
- External integrations and deployment health are not live-verified; most provider tests use mocks.
- Grounding is strongest for POIs/XHS evidence but incomplete for planner prose, meals, hotels, ticket prices, budgets, and chat suggestions.
- One backend regression test is stale; test imports require a specific `PYTHONPATH` because import styles are mixed.
- No full browser E2E suite; build warnings and large bundles remain.
- The root `CURRENT_ARCHITECTURE.md` is a compatibility entry; `docs/ARCHITECTURE.md` is the canonical architecture reference.

## Project phase

**Project Memory / Baseline Consolidation**

## Next planned step

**STEP 2 — Resolve baseline inconsistencies and define the next product improvement phase.**

## Source-of-truth rule

Future AI-assisted work must inspect current code and Git state before contradicting or updating this document. Evidence priority is: current code/Git, `docs/PROJECT_BASELINE.md`, current documentation, then historical documentation/comments. Update this file only when repository evidence changes, and record the change in `docs/CHANGELOG.md` and, where applicable, `docs/DECISIONS.md` or `docs/METRICS.md`.
