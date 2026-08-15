# TripStar Decisions

This file is the lightweight Architecture/Product Decision Record. It records decisions evidenced by current implementation; it does not claim access to original discussions.

## Decision recording rules

Use one immutable ID per decision. Amend status/consequences with a dated note rather than silently rewriting history.

```text
## ADR-NNN — Title
- ID:
- Date:
- Status: Proposed | Accepted | Superseded | Rejected
- Context:
- Decision:
- Alternatives:
- Rationale:
- Consequences:
- Evidence:
```

When original discussion is absent, write: **Rationale reconstructed from current implementation; original decision discussion unavailable.** Do not infer authors, meetings, business outcomes, or rejected alternatives without repository evidence.

## ADR-001 — Use bounded sequential orchestration

- **ID:** ADR-001
- **Date:** 2026-08-16 (recorded from baseline)
- **Status:** Accepted in current implementation
- **Context:** Travel generation requires preference interpretation, provider context, structured planning, enrichment, validation, and possible repair/revision.
- **Decision:** Execute these stages through explicit sequential orchestration with a main HelloAgents planner and optional bounded LLM stages. Do not rely on autonomous peer-agent coordination or unbounded reflection.
- **Alternatives:** Autonomous multi-agent scheduling is not implemented; a fully deterministic planner is not implemented.
- **Rationale:** Rationale reconstructed from current implementation; original decision discussion unavailable.
- **Consequences:** Flow and cost stages are inspectable and bounded, but orchestration is centralized and the “multi-agent” label must be used conservatively.
- **Evidence:** `backend/app/agents/trip_planner_agent.py`; `backend/app/services/llm_service.py`; `docs/PROJECT_BASELINE.md` §§6, 14.

## ADR-002 — Validate structured LLM output with typed schemas

- **ID:** ADR-002
- **Date:** 2026-08-16 (recorded from baseline)
- **Status:** Accepted in current implementation
- **Context:** Planner and revision calls return machine-consumed itinerary/operation data.
- **Decision:** Parse output into Pydantic schemas and typed patch/revision contracts, with deterministic cleanup and bounded repair before rejection.
- **Alternatives:** Accepting unstructured prose or unchecked JSON is not the current implementation.
- **Rationale:** Rationale reconstructed from current implementation; original decision discussion unavailable.
- **Consequences:** Consumers receive stable contracts, while repair logic adds complexity and cannot guarantee recovery of missing semantics.
- **Evidence:** `backend/app/models/schemas.py`; `MultiAgentTripPlanner._parse_response()`; patch/revision services.

## ADR-003 — Surround LLM generation with deterministic grounding and validation

- **ID:** ADR-003
- **Date:** 2026-08-16 (recorded from baseline)
- **Status:** Accepted in current implementation
- **Context:** LLM-generated map facts, budgets, pacing, and route assumptions are not inherently trustworthy.
- **Decision:** Strip/replace untrusted map fields through provider enrichment and run deterministic validation for grounding, pacing, budget, start-time, route, and mobility risks. Commit targeted revisions only after safety gates/revalidation.
- **Alternatives:** Trusting planner output directly is not the current implementation; complete deterministic itinerary generation is not implemented.
- **Rationale:** Rationale reconstructed from current implementation; original decision discussion unavailable.
- **Consequences:** The result exposes risk/degraded states and rejects unsafe revisions, but grounding remains incomplete for several narrative and estimated fields.
- **Evidence:** `google_map_service.py`; `trip_validator_service.py`; `pacing_policy.py`; `pacing_revision_service.py`; `trip_revision_service.py`.

## ADR-004 — Use asynchronous tasks with WebSocket progress and polling recovery

- **ID:** ADR-004
- **Date:** 2026-08-16 (recorded from baseline)
- **Status:** Accepted in current implementation
- **Context:** Trip generation is long-running relative to a normal HTTP request.
- **Decision:** Return a task ID immediately, execute planning in an asyncio background task, broadcast progress through WebSocket queues, and keep HTTP status polling as the frontend recovery path.
- **Alternatives:** A single blocking request and durable external job queue are not implemented.
- **Rationale:** Rationale reconstructed from current implementation; original decision discussion unavailable.
- **Consequences:** Users receive progress and can recover from socket loss/refresh, but coordination is process-local and in-flight work cannot resume after restart.
- **Evidence:** `backend/app/api/routes/trip.py`; `frontend/src/services/tripTaskLifecycle.ts`; frontend lifecycle tests.

## ADR-005 — Abstract optional map/research providers and expose degraded states

- **ID:** ADR-005
- **Date:** 2026-08-16 (recorded from baseline)
- **Status:** Accepted in current implementation
- **Context:** Provider credentials and availability vary, and map facts need explicit provenance.
- **Decision:** Dispatch between Google and AMap where applicable, keep XHS optional, classify provider failures, and return verified/partial/unverified/unavailable or fallback states rather than treating all data as equally trusted.
- **Alternatives:** A mandatory single provider or silent fabricated provider success is not the intended current path.
- **Rationale:** Rationale reconstructed from current implementation; original decision discussion unavailable.
- **Consequences:** The application can degrade and expose provenance, but behavior depends on external APIs and is only mock-tested in this baseline.
- **Evidence:** `map_dispatcher.py`; `google_map_service.py`; `amap_service.py`; `xhs_service.py`; `poi.py`; provider tests.

## ADR-006 — Use local JSON persistence and one worker for the portfolio deployment path

- **ID:** ADR-006
- **Date:** 2026-08-16 (recorded from baseline)
- **Status:** Accepted in current implementation
- **Context:** Task results and refresh recovery require persistence while coordination remains in memory.
- **Decision:** Persist task snapshots to local JSON, mount a Docker volume, and run one Gunicorn/Uvicorn worker.
- **Alternatives:** Database-backed persistence and distributed task coordination are not implemented.
- **Rationale:** Rationale reconstructed from current implementation; original decision discussion unavailable.
- **Consequences:** The deployment recipe matches in-memory coordination, but cannot safely scale horizontally and cannot resume unfinished work.
- **Evidence:** `backend/app/api/routes/trip.py`; `Dockerfile`; `docker-compose.yaml`; `start.sh`.
