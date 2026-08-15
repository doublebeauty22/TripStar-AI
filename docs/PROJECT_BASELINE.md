# TripStar Project Baseline

## 1. Audit metadata

- Audit date: 2026-08-16 (Australia/Sydney).
- Repository root: repository top level (`.`).
- Scope: repository baseline audit only. No application or business logic was changed and no discovered issue was fixed.
- Evidence standard: claims below come from repository files and safe local checks. External APIs and any live deployment were not contacted. Secret values were not recorded.
- Initial state: branch `main`, HEAD `449d0fa584b99f61386b980fe76aac53848de871`, clean working tree (`git status --short` produced no output).
- Classification terms: **IMPLEMENTED** means executable code and/or local verification exists; **PARTIAL** means a usable path exists with material limitations; **MOCK/STUB** means only fixtures, fallback placeholders, or non-production test behavior exists; **CONFIGURED BUT UNVERIFIED** means integration/deployment configuration exists but was not proven live; **NOT IMPLEMENTED** means repository evidence explicitly lacks it; **UNKNOWN** means the repository cannot establish it.

## 2. Git baseline

- Root: repository top level (`.`).
- Branch: `main`.
- HEAD: `449d0fa584b99f61386b980fe76aac53848de871` (`fix: improve grounded image retrieval consistency`, 2026-08-16).
- Pre-audit status: clean; no staged, modified, deleted, or untracked files.
- Remotes:
  - `origin` fetch/push: `git@github.com:doublebeauty22/TripStar-AI.git`
  - `upstream` fetch/push: `https://github.com/1sdv/TripStar.git`
- Recent relevant commits (newest first): `449d0fa` grounded image retrieval consistency; `955da74` chat/LLM compatibility; `ca211d8` example trip in production image; `b89feaf` frontend lock synchronization; `419a70f` Phase 5C portfolio demo UX; `957a225` portfolio polish; `d0ee76d` public portfolio deployment preparation; `96b9c5e` README revision.

## 3. Repository structure

- Frontend: `frontend/src/` contains the Vue application. `views/Landing.vue` collects and submits preferences; `views/Result.vue` renders the result, maps, risks, graph, and image export; `components/AIChat.vue` provides Q&A and patch modes; `services/api.ts` and `services/tripTaskLifecycle.ts` implement HTTP/WebSocket/polling access. `frontend/tests/` contains source-contract tests. `frontend/dist/` exists locally but is ignored by Git.
- Backend: `backend/app/api/main.py` creates FastAPI and mounts route modules from `backend/app/api/routes/`. `backend/app/agents/trip_planner_agent.py` contains planner orchestration and embedded prompts. `backend/app/services/` contains LLM, provider, validation, revision, patch, pacing, graph, configuration-guard, and chat services. `backend/app/models/schemas.py` defines Pydantic contracts.
- Configuration: `backend/app/config.py`, `backend/.env.example`, `frontend/.env.example`, `backend/runtime_settings.json`, `frontend/vite.config.ts`, `backend/requirements.txt`, and the two `package.json` files. Local ignored `.env` files exist; only their documented variable names were considered.
- Prompts: no separate prompt directory. Prompts are embedded mainly in `backend/app/agents/trip_planner_agent.py`, `backend/app/services/preference_service.py`, `backend/app/services/chat_service.py`, `backend/app/services/trip_revision_service.py`, `backend/app/services/pacing_revision_service.py`, and `backend/app/services/trip_patch_service.py`.
- Agents: `backend/app/agents/trip_planner_agent.py` is the only application agent module. Provider access and most validation/revision logic live in ordinary services.
- APIs: trip, preferences, chat, POI/photo, map, runtime settings, and example-trip routes are under `backend/app/api/routes/`.
- Tests: 35 Python test files under `backend/tests/` and 7 Node test files under `frontend/tests/`; evaluation contracts, fixtures, captures, reports, and historical results are under `eval/`.
- Scripts: `backend/run.py` and `start.sh` start the application. Evaluation CLIs/modules live in `backend/app/evaluation/`. There is no general `scripts/` directory.
- Deployment: `Dockerfile`, `docker-compose.yaml`, `.dockerignore`, and `start.sh`. No Render manifest, Kubernetes manifests, Terraform, or CI workflow was found.
- Documentation: `README.md`, localized `README_en.md`/`README_ja.md`, `CURRENT_ARCHITECTURE.md`, phase plans, refactor plan, and evaluation documentation.
- Example data: `portfolio/examples/example_trip_v1.json`. Local task JSON is stored under `backend/data/trip_tasks/` and is not a database.

## 4. Verified technology stack

| Area | Repository evidence | Status |
|---|---|---|
| Frontend | Vue 3.5, TypeScript 5.7, Vite 6, Vue Router, Vue I18n, Ant Design Vue, Axios, ECharts, html2canvas, Swiper | IMPLEMENTED; build verified locally |
| Backend | Python, FastAPI, Uvicorn, Pydantic 2, pydantic-settings, asyncio, HTTPX/AIOHTTP | IMPLEMENTED |
| Agent/LLM library | `hello-agents` / `HelloAgentsLLM`, plus an OpenAI-compatible client wrapper in `llm_service.py` | IMPLEMENTED locally; live provider unverified |
| LLM models | Model is configured by `LLM_MODEL_ID`/`OPENAI_MODEL`; default is `gpt-4`. Compatibility code handles GPT-5-style parameters. No single deployed model can be proven from the repository | CONFIGURED BUT UNVERIFIED |
| JavaScript runtime | Node is used for frontend tooling and XHS signature code; backend has `crypto-js` and `jsdom` dependencies | IMPLEMENTED/configured |
| Package managers | npm lockfile for frontend; pip requirements and `uv` use in Docker for Python; npm for backend JS dependencies | IMPLEMENTED |
| Storage | Process memory plus local JSON files in `backend/data/trip_tasks/`; browser `sessionStorage`/`localStorage`; Docker named volume `trip_data` | IMPLEMENTED, single-instance/local only |
| Database/cache/queue | No SQL/NoSQL database, Redis, broker, or durable job queue found | NOT IMPLEMENTED |
| Maps/location | Google Places/Geocoding/Directions/Weather REST service; AMap REST service; Google Maps and AMap browser SDKs | Referenced and configured; mocked tests pass; live credentials/provider behavior unverified |
| Search/retrieval | XHS native HTTP/signature adapter and Google/AMap place search | Referenced and configured; live behavior unverified |
| Weather/hotel/travel | Weather and hotel retrieval through Google/AMap adapters; route calls for validation/rendering. No booking, inventory, price feed, flight, rail, or hotel-booking API | PARTIAL; provider facts optional and unverified live |
| Async transport | `asyncio.create_task`, in-memory queues, WebSocket task events, HTTP status polling fallback | IMPLEMENTED for one process |
| Deployment | Multi-stage Docker image serving built Vue through FastAPI; Gunicorn/Uvicorn single worker; Compose volume and `/health` route | CONFIGURED BUT UNVERIFIED live |

External credentials are accepted for an OpenAI-compatible LLM, Google Maps server/browser APIs, AMap server/browser APIs, and XHS. Presence of local ignored configuration does not prove validity, account permissions, quota, billing, or production availability.

## 5. Product capability matrix

| Capability | Classification | Evidence and boundary |
|---|---|---|
| Trip planning | IMPLEMENTED | `POST /api/trip/plan` creates an async task; `MultiAgentTripPlanner.plan_trip()` generates single- or multi-city `TripPlan` output. Requires a working LLM for live generation. |
| User preference collection | IMPLEMENTED | `Landing.vue` collects party, budget, pace, interests, transport, accommodation, dates/cities, and free text. `parse_preference_profile()` uses an LLM when free text exists and fails open to explicit fields. |
| Research/search | PARTIAL | XHS and map-place adapters are called where configured and degrade when unavailable. No live provider check was performed. |
| Itinerary generation | IMPLEMENTED | Planner prompt and Pydantic parsing produce day plans, attractions, meals, hotels, weather, suggestions, and budget. Factual completeness depends on provider/LLM availability. |
| Structured output | IMPLEMENTED | `TripPlan`, `DayPlan`, `Attraction`, `Budget`, `ValidationResult`, patch schemas, and graph schemas are Pydantic models. |
| Validation | IMPLEMENTED | `TripValidatorService` deterministically checks start time, budget, route/mobility, grounding, and pacing, returning typed risks and degraded states. It does not prove opening hours or universal feasibility. |
| Recommendation | PARTIAL | The planner chooses POIs/meals/hotels using preferences and retrieved context, but no general scoring/ranking model or complete per-recommendation rationale exists. |
| Budget handling | IMPLEMENTED | User budget is collected, planner outputs category estimates, and validator can mark over-budget estimates as blocking. Values are estimates, not live prices. |
| Maps | PARTIAL | Verified coordinates, provider source states, frontend JS maps/routes, and fallbacks exist. Map display requires valid browser keys and live APIs. |
| Weather | PARTIAL | Google/AMap weather adapters and source/unavailable UI exist. Live forecast availability was not verified. |
| Hotels | PARTIAL | Deterministic provider hotel search feeds planner context and hotel fields; no availability, booking, or authoritative live pricing integration exists. |
| Evidence/source attribution | PARTIAL | POI match/source fields, XHS evidence IDs/quotes/support, photo source/attribution, and validation confidence exist. Planner prose, meal/hotel estimates, and many suggestions are not uniformly source-cited. |
| Chat Q&A | IMPLEMENTED | `/api/chat/ask` sends the current plan and bounded history to an LLM. It permits clearly labelled common-knowledge inference, so it is not fully grounded. |
| Follow-up modification | PARTIAL | `/api/trip/{task_id}/patch` interprets typed local changes, applies locks/version checks, enriches affected POIs, validates, returns diffs, and can require regeneration. Undo and distributed concurrency are absent. |
| Long-running experience | IMPLEMENTED | Async task submission, stage/progress events, WebSocket, continuously armed polling recovery, refresh resume, terminal errors, and deduplication exist. In-flight work cannot resume after process restart. |
| Progress updates | IMPLEMENTED | `_update_task_state()`, WebSocket queues, `monitorTripTask()`, and Landing progress UI. |
| Export | PARTIAL | `Result.vue#exportAsImage()` uses html2canvas for image export. No PDF, calendar, document, or share-link export was found. |
| Knowledge graph/visualization | IMPLEMENTED | `build_knowledge_graph()` deterministically derives nodes/edges/categories from the completed plan; `Result.vue` renders with ECharts. It is a presentation graph, not an external knowledge base or learned graph. |
| Authentication | NOT IMPLEMENTED | No login, session identity, authorization middleware, or account model found. Public guard identity is rate-control metadata, not authentication. |
| User persistence | PARTIAL | Trip tasks persist as local JSON and UI state uses browser storage. There are no users, tenant ownership checks, cross-device accounts, or database persistence. |
| Example trip | IMPLEMENTED | Read-only sanitized file served by `demo.py`; frontend explicitly labels example mode and does not create a planner task. |

## 6. AI/Agent architecture

### Entry points and stages

1. Optional preference parsing enters at `preferences.parse_preferences()` and calls `preference_service.parse_preference_profile()`. Explicit fields remain authoritative. Free text causes one budgeted `preference` chat completion; parse/provider failure returns a non-blocking fallback profile.
2. Planning enters at `trip.plan_trip()`. It fingerprints semantic input, deduplicates active equal requests, persists initial task state, reserves public-demo capacity, and starts `_run_trip_planning()` with `asyncio.create_task`.
3. `_run_trip_planning()` obtains the singleton `MultiAgentTripPlanner`, binds per-generation LLM usage/call accounting, and invokes `MultiAgentTripPlanner.plan_trip()` with a progress callback.
4. The planner gathers attraction research per city through `search_xhs_attractions()` with fallback behavior; weather and hotel retrieval are deterministic direct Google/AMap service calls, not autonomous LLM agents.
5. `_run_planner_with_retry()` creates a fresh HelloAgents `SimpleAgent` for the planning call, builds a prompt containing request/profile/provider context and pacing policy, and invokes the OpenAI-compatible model. A planner timeout receives at most one application retry.
6. `_parse_response()` extracts and sanitizes JSON, tries deterministic quote/truncation/regex repairs, validates through `TripPlan`, and may spend one `json_repair` LLM call as a last resort. `_create_fallback_plan()` exists but repository tracing did not establish it as the normal planning failure path; treat it as dormant fallback code rather than a proven user capability.
7. Deterministic map enrichment removes/overwrites untrusted map facts using provider results. `TripValidatorService.validate()` calculates typed risks, route evidence, budget and pacing outcomes.
8. When eligible pacing risks exist, `PacingRevisionService` may request typed targeted revision operations, applies protected-day and grounding gates, and revalidates. Other actionable risks can go through the bounded critic/revision service. Revision is fail-open/fail-closed by stage: unsafe revisions are rejected and the candidate is retained; no open-ended autonomous loop exists.
9. The final `TripPlan` is passed to deterministic `build_knowledge_graph()`, wrapped in `TripPlanResponse`, persisted as JSON, broadcast, and exposed through status retrieval.
10. Q&A (`chat_with_trip_context`) is a separate single LLM call with plan JSON and recent conversation context. Patch mode is a separate LLM interpretation call followed by a deterministic typed patch engine, enrichment, validation, graph rebuild, optimistic plan versioning, and persistence.

### Data, calls, validation, and errors

- Primary schemas are in `backend/app/models/schemas.py`: request/profile, itinerary, evidence, budget, validation/risk, patch/diff, graph, and chat models.
- LLM calls funnel through `llm_service.create_chat_completion()`, which records stage, model, tokens when supplied, duration, retry count, and enforces a logical-call budget. Only classified transient failures receive one provider retry; auth/quota/invalid-request failures do not.
- Default per-trip logical call limit is 5. Stages found include `preference`, `xhs_research`, `planner`, `critic`, `revision`, `json_repair`, `trip_patch`, and `chat`; not every trip uses every stage.
- The architecture is agent-assisted but predominantly orchestrated sequentially. The planner uses HelloAgents `SimpleAgent`; provider search, weather, hotel lookup, enrichment, validation, graph building, persistence, progress, and patch application are ordinary deterministic/service code. There is no independent agent scheduler, durable agent state, planning tree, or unbounded self-reflection loop.
- Errors are recorded on task state; public deployments receive sanitized errors. Provider adapters explicitly classify unavailable/auth/rate-limit/malformed cases and frequently degrade. Process restart marks unfinished persisted tasks failed rather than resuming them.

## 7. Primary execution flow

The main confirmed user journey is:

1. `frontend/src/views/Landing.vue#handleSubmit()` validates the base form and calls `parsePreferenceProfile()` in `frontend/src/services/api.ts` -> `POST /api/preferences/parse` -> `backend/app/api/routes/preferences.py#parse_preferences()` -> `preference_service.parse_preference_profile()`.
2. The user confirms the parsed profile; `Landing.vue#generateConfirmedTrip()` builds `TripFormData`, and `startTripGeneration()` calls `api.ts#generateTripPlan()`.
3. Axios submits `POST /api/trip/plan`; `trip.py#plan_trip()` returns `task_id`, `plan_id`, and WebSocket URL immediately and schedules `_run_trip_planning()`.
4. `tripTaskLifecycle.ts#monitorTripTask()` subscribes to `/api/trip/ws/{task_id}` and also arms HTTP polling to `/api/trip/status/{task_id}`. Events update `Landing.vue#updateTaskProgress()`. WebSocket failure/early close falls back to polling.
5. `_run_trip_planning()` calls `MultiAgentTripPlanner.plan_trip()`, which performs provider research, planner generation, parse/repair, enrichment, deterministic validation, and bounded revision as applicable. It then builds graph data and persists/broadcasts the `TripPlanResponse`.
6. `Landing.vue#saveCompletedTrip()` stores plan/graph identifiers in `sessionStorage` and routes to `/result?plan_id=...`.
7. `frontend/src/views/Result.vue` treats the backend task result as canonical when `plan_id` exists, renders itinerary/budget/weather/evidence/risks/maps/graph, fetches photos from `/api/poi/photo`, and supplies plan/task/version to `AIChat.vue`.

## 8. Tests and verification results

| Command | Result |
|---|---|
| `PYTHONPATH=.:backend ./backend/.venv/bin/python -m unittest discover -s backend/tests` (repository root) | **FAIL**: 327 run; 325 passed, 1 failed, 1 skipped. Failure: `test_phase3d1_capture.CaptureTests.test_dry_run_contract_identity_revision_and_unknown_semantics` expects historical SHA `96b9c5e`, but captured code revision is current `449d0fa`. The skip is the live Tokyo/Google E2E test. No fix made. |
| `node --test tests/*.test.cjs` (`frontend/`) | **PASS**: 34 tests, 34 passed, 0 failed/skipped/todo. These are primarily source/contract tests, not browser E2E tests. |
| `npm run build` (`frontend/`) | **PASS**: `vue-tsc` and Vite build completed; 3,848 modules transformed. Warnings: unresolved `../img/ilya-yakover.jpg`, `../img/city.jpg`, and Nucleo font paths remain runtime-resolved; two generated JS chunks exceed 500 kB. |
| `./backend/.venv/bin/python -m unittest discover -s backend/tests -v` from root without `PYTHONPATH` | **FAIL (diagnostic invocation)**: 315 tests discovered, 1 failure, 3 import errors, 1 skipped. Three tests import `app.*` and need `backend` on `PYTHONPATH`; same stale-SHA failure. Superseded by the correctly pathed run above. |
| `./.venv/bin/python -m unittest discover -s tests` from `backend/` | **FAIL (diagnostic invocation)**: mixed import styles caused 34 `ModuleNotFoundError: backend` errors. Superseded by the correctly pathed run above. |
| `npm test -- --test-reporter=spec` (`frontend/`) | **NOT RUN**: package has no `test` script; npm returned “Missing script: test”. Tests were then run directly with Node. |

No lint script, backend static type check, Playwright/Cypress browser E2E suite, or repository CI workflow was found. Backend tests are unittest-based and heavily mock external providers. Evaluation modules and frozen artifacts exist, but this audit did not re-run paid/live evaluations or reinterpret committed human-review results.

## 9. Deployment evidence

- `Dockerfile` proves a build recipe: Node 18 builds the Vue app; Python 3.10 installs backend dependencies and Gunicorn/Uvicorn; backend JS dependencies support XHS signing; the example trip is copied; built static files and API share one image.
- `start.sh` runs Gunicorn with a single Uvicorn worker and binds `${PORT:-7860}`. This matches the in-memory task/WebSocket architecture but prevents horizontal scaling without redesign.
- `docker-compose.yaml` maps port 7860, injects documented environment variables, enables production/public-demo defaults, and mounts a named volume at `/app/backend/data`.
- `backend/app/api/main.py#/health` reports process configuration and example-file presence. It does not probe the LLM, Google, AMap, XHS, persistent volume, or frontend browser keys, so `status: healthy` is only a shallow application-process check.
- The FastAPI app serves `frontend/dist` when present and provides an SPA catch-all. Local production build succeeded.
- No platform manifest or evidence of a current Render service/URL exists; README says the public demo URL is “coming soon.” No live health check, TLS/WSS check, production logs, deployed image digest, environment inventory, or uptime evidence was available. Deployment is therefore **configured but not live-verified**.

## 10. Known risks / technical debt

1. **Test baseline drift:** one backend test hard-codes commit `96b9c5e`, so the current suite cannot pass at HEAD.
2. **Import-path fragility:** test files mix `backend.app.*` and `app.*` imports. The suite requires `PYTHONPATH=.:backend`; common discovery invocations fail with import errors, and no canonical backend test command is documented in package metadata.
3. **Single-process task architecture:** active tasks, subscriber queues, dedupe maps, locks, cooldowns, and generation accounting are process-local. JSON persistence records terminal data but cannot resume work. Multi-worker/horizontal deployment is unsafe without shared coordination.
4. **No authentication/authorization:** persisted task IDs are effectively locators, not ownership credentials. UUID entropy and public-client dedupe isolation do not provide access control.
5. **Local JSON persistence:** file replacement is atomic per write, but there is no database transaction model, retention policy, migration system, backup evidence, or multi-process concurrency control.
6. **External dependence and unverifiable freshness:** LLM, maps, weather, XHS, hotel and photo features depend on credentials, quota, network and third-party response contracts. Tests mostly mock these integrations.
7. **Grounding remains partial:** source/match states are strong for POIs and XHS evidence, but generated descriptions, meal advice, hotel price estimates, ticket prices, budget totals, and general chat advice are not uniformly externally attributed.
8. **Planner JSON repair risk:** deterministic repairs include arithmetic evaluation and truncation closure; the last-resort LLM repair receives only the first 500 and last 2,000 characters, so semantic recovery of omitted middle content cannot be guaranteed.
9. **Dormant fabricated fallback:** `_create_fallback_plan()` constructs generic POIs and Beijing-like coordinates even for other cities. It was not found in the normal execution path, but retaining it is hazardous if later wired in without provenance controls.
10. **Frontend build warnings/size:** unresolved legacy images/fonts can fail at runtime, and main/result chunks are over 1.4 MB minified, increasing load and memory risk.
11. **No full browser E2E/production smoke coverage:** frontend tests inspect source/contracts and task lifecycle with fakes. The live Google E2E is skipped by default.
12. **Settings are intentionally unauthenticated locally:** public mode makes them read-only and server secrets are excluded, but non-public runtime settings can be changed through an unauthenticated endpoint and persisted locally.
13. **Incomplete product operations:** no task cancellation, retry/resume after restart, undo, distributed idempotency, user accounts, share permissions, booking, or reliable analytics/event store.
14. **Large central modules:** `trip_planner_agent.py`, `trip.py`, `Landing.vue`, and `Result.vue` combine many responsibilities, increasing regression and ownership risk.

## 11. Documentation inconsistencies

### Matches current code

- `README.md` accurately describes Vue/FastAPI, structured preferences, deterministic validation/pacing, guarded revision, WebSocket plus polling recovery, local completed-task serialization, example trip, public cost/error guards, and the single-service Docker path.
- README correctly limits Phase 4D results to descriptive controlled evaluation and does not claim production traffic or statistical proof.
- README correctly says the public demo is not yet linked, consistent with the absence of live deployment evidence.

### Outdated or overstated

- At the STEP 0 audit, `CURRENT_ARCHITECTURE.md` was a pre-refactor snapshot that incorrectly said structured party/budget/pace preferences, evidence models, validation, patch/version workflows, LLM usage observation, and frontend polling fallback did not exist. STEP 2B replaced that stale narrative with a current compatibility reference; `docs/ARCHITECTURE.md` remains canonical.
- At the STEP 0 audit, `CURRENT_ARCHITECTURE.md` also said AMap service parsing remained TODO. STEP 2B corrected the legacy document to reflect the current REST adapter and tests while retaining the live-provider verification boundary.
- The term “multi-agent” in code/docstrings can imply more autonomy than exists. Current runtime is a bounded orchestration pipeline: one planner agent plus optional LLM extraction/critic/revision calls and mostly deterministic services.
- `README.md` calls Python 3.10+ supported. Docker proves Python 3.10 and local checks ran under Python 3.13, but the repository has no version matrix proving every intermediate/current Python release.
- README’s provider feature list describes integration capability, not confirmed availability. No local evidence proves live credentials, quotas, billing, or production calls.
- Phase plan documents are design/history artifacts, not authoritative current-state specifications; several “not implemented” limitations in earlier plans have since changed, while other planned items (for example undo/distributed locking) remain absent.

### Cannot be independently verified here

- Historical Phase 4D human/evaluation outcomes are supported by committed artifacts, but this audit did not reproduce live captures, reviewer identity/blinding, provider comparability, or model calls.
- “Render-compatible” is supported by container/PORT conventions, not by a deployed Render service.

## 12. Unknowns / items requiring future verification

- Whether any live production deployment exists, and its URL, image revision, health, TLS/WSS behavior, uptime, logs, scaling configuration, and persistent-volume durability.
- Which LLM provider/model/base URL is actually used outside this checkout, and whether credentials, structured-output compatibility, limits, latency, and cost are acceptable.
- Validity, API enablement, billing, quota, restrictions, geographic behavior, and terms compliance for Google Maps, AMap, and XHS credentials.
- Real end-to-end quality for current destinations, dates, languages, multi-city routes, weather, hotel results, photos, and evidence freshness.
- Browser behavior across devices, accessibility, responsive rendering, image export completeness, map SDK loading, and unresolved build assets.
- Data retention/privacy expectations for local task JSON, request metadata, browser storage, logs, and XHS-derived evidence.
- Whether committed evaluation results remain representative of current HEAD after subsequent changes.
- Operational recovery, backup/restore, secret rotation, abuse resistance, and incident ownership.

## 13. Evidence references

- Application/bootstrap: `backend/app/api/main.py` (`app`, middleware, handlers, `/health`, static serving); `backend/run.py`; `start.sh`.
- Trip task lifecycle: `backend/app/api/routes/trip.py` (`plan_trip`, `_run_trip_planning`, `trip_task_ws`, `get_task_status`, `patch_trip`).
- Planner/orchestration/prompts: `backend/app/agents/trip_planner_agent.py` (`MultiAgentTripPlanner`, `plan_trip`, `_run_planner_with_retry`, `_parse_response`).
- LLM compatibility/budgets/retries: `backend/app/services/llm_service.py` (`create_chat_completion`, `TaskScopedLLM`, `OpenAICompatibilityClient`, `get_llm`).
- Models/contracts: `backend/app/models/schemas.py`.
- Preferences: `backend/app/api/routes/preferences.py`; `backend/app/services/preference_service.py`.
- Providers: `backend/app/services/google_map_service.py`; `amap_service.py`; `map_dispatcher.py`; `xhs_service.py`; `backend/app/api/routes/map.py`; `poi.py`.
- Validation/revision/patch: `trip_validator_service.py`; `pacing_policy.py`; `pacing_revision_service.py`; `trip_revision_service.py`; `trip_patch_service.py`.
- Chat/graph: `chat_service.py`; `knowledge_graph_service.py`; `backend/app/api/routes/chat.py`.
- Frontend flow: `frontend/src/views/Landing.vue`; `frontend/src/services/api.ts`; `frontend/src/services/tripTaskLifecycle.ts`; `frontend/src/views/Result.vue`; `frontend/src/components/AIChat.vue`; `frontend/src/types/index.ts`.
- Deployment/config: `Dockerfile`; `docker-compose.yaml`; `backend/app/config.py`; `backend/.env.example`; `frontend/.env.example`; `frontend/vite.config.ts`.
- Tests/evaluation: `backend/tests/`; `frontend/tests/`; `backend/app/evaluation/`; `eval/`.
- Documentation compared: `README.md`; `README_en.md`; `README_ja.md`; `CURRENT_ARCHITECTURE.md`; phase/refactor plans.

## 14. Baseline summary

TripStar is a functioning local portfolio-grade travel-planning application, not merely a mock: it has a buildable Vue frontend, FastAPI APIs, structured preference and itinerary contracts, bounded LLM orchestration, optional real provider adapters, deterministic validation/pacing, guarded revisions and patches, asynchronous progress/recovery, local persistence, visualization, Q&A, and an isolated example trip. Local frontend tests and build pass. The backend regression suite is broad but currently fails one stale commit-identity assertion.

The strongest boundary is operational: the repository proves implementation and deployment configuration, not a healthy production service or valid external integrations. It is deliberately single-process, has no authentication or database/job queue, cannot resume in-flight tasks after restart, and provides partial rather than universal evidence grounding. STEP 2B aligned the root README and legacy architecture entry with these boundaries; `docs/ARCHITECTURE.md` is the canonical architecture reference.
