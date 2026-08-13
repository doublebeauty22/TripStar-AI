# TripStar-AI

Grounded, pacing-aware AI travel planning with deterministic validation, targeted revision, and reproducible product evaluation.

[GitHub repository](https://github.com/doublebeauty22/TripStar-AI) · [English](README_en.md) · [日本語](README_ja.md) · Public demo: **coming soon — Render URL will be added here**

## Product overview

TripStar-AI turns a structured trip request and natural-language preferences into an executable itinerary. The product does more than generate prose: it preserves explicit constraints, grounds POIs against map providers, models daily load, exposes uncertainty, validates risks deterministically, and applies narrow fail-closed revisions only when their safety gates pass.

The current portfolio build includes:

- Preference parsing for pace, party, interests, transport, accommodation, budget, earliest start, mobility, dietary needs, must-have POIs, and special requirements.
- Grounded POI enrichment with explicit verified, partial, unverified, and unavailable states.
- A pacing-aware Planner backed by a deterministic daily-load policy.
- Validator rules for start time, mobility, budget, route feasibility, and pacing.
- Targeted pacing revision with typed operations, protected-day preservation, grounding gates, and post-revision validation.
- A local patch workflow that limits changes to affected days and checks semantic drift.
- Offline capture, immutable baselines, deterministic metrics, manifests, blind paired review, and product-impact reporting.
- A single-service Docker deployment path with FastAPI, Vue, WebSocket progress, polling recovery, public cost guards, sanitized errors, and a provider-independent Example Trip.

## Evaluation evidence

Phase 4D completed four controlled paired evaluations using identical TripRequests within each pair, immutable baselines, the same rubric, and blinded Plan A/Plan B review.

| Result | Descriptive finding |
|---|---:|
| Completed paired evaluations | 4 |
| Mean human pacing delta | +1.00 |
| Pairs with a positive pacing delta | 3/4 |
| Explicit constraints preserved | 4/4 evaluable pairs |
| Blind outcomes | 3 BETTER / 1 MIXED |
| Targeted revisions | 2 committed / 2 safely rejected |

The Kyoto control exposed a useful failure mode: duration compression reduced modeled load without improving human pacing and lowered usefulness by one point. The project therefore records the pacing-aware Planner as a passed evaluation milestone while keeping targeted pacing Revision in an iterate state.

These results are **descriptive controlled evaluations**. They are not statistical significance, causal proof, production-traffic results, or evidence of universal planner superiority. Every pair had limited live-provider comparability.

Evidence is committed in a resume-safe, inspectable form:

- [Four-pair product impact report](eval/phase4d/phase4d_final_report.md)
- [Aggregate machine-readable results](eval/phase4d/phase4d_aggregate.json)
- [Paired result records](eval/phase4d/results)
- [Offline deterministic metrics](eval/phase4d/offline)
- [Candidate manifests and captures](eval/phase4d/cases)
- [Pacing policy and experiment design](eval/phase4a)

The public README reports only aggregate findings. Raw review records remain evaluation evidence and are not used as marketing testimonials.

## How the system works

```mermaid
flowchart LR
    A["Trip request + preference profile"] --> B["Provider research"]
    B --> C["Pacing-aware Planner"]
    C --> D["POI enrichment"]
    D --> E["Deterministic Validator"]
    E -->|"eligible pacing risk"| F["Targeted revision"]
    E -->|"no safe trigger"| H["Final plan + disclosed risks"]
    F --> G["Affected-day enrichment + safety gates"]
    G -->|"pass"| I["Post-revision validation"]
    G -->|"reject"| H
    I --> H
    H --> J["Vue itinerary, budget, evidence, pacing, map"]
```

Long-running generation uses an asynchronous task endpoint. WebSocket events provide progress, while HTTP polling is always armed as a recovery path. Completed tasks are serialized locally; an interrupted in-flight task fails clearly after process restart rather than spinning forever.

## Pacing and revision design

Daily load is not reduced to POI count. The deterministic policy combines:

- Attraction visit duration.
- Verified or estimated travel time.
- Meals, access overhead, rest, and uncertainty buffers.
- Special burdens such as early starts, long transfers, intensive walking, altitude, theme parks, and transfer days.

Route unavailable is kept distinct from route infeasible. Unknown travel never becomes zero: conservative fallback estimates and explicit confidence labels remain visible to validation and evaluation.

Revision is deliberately narrow. It can operate only on eligible overloaded days, preserves must-have POIs and protected days, and commits only after typed-operation, grounding, preservation, and post-validation gates pass. A rejected revision leaves the original candidate intact.

## Public demo architecture

The deployment target is one Render Docker Web Service:

```text
Browser (Vue static build)
        │ HTTPS / WSS, same origin
        ▼
FastAPI + Gunicorn/Uvicorn, one worker
        ├── background trip task
        ├── WebSocket + polling fallback
        ├── local task persistence
        ├── public rate/concurrency guards
        └── LLM / XHS / Google Maps / AMap adapters
```

Public-demo mode makes runtime settings read-only, disables shared history, sanitizes provider errors, limits live generations, and exposes a clearly labelled pre-generated Example Trip that never calls the Planner or providers.

## Tech stack

- Frontend: Vue 3, TypeScript, Vite, Ant Design Vue, ECharts, Google Maps JS, AMap JS.
- Backend: Python 3.10+, FastAPI, Pydantic, Gunicorn/Uvicorn, HTTPX/AIOHTTP.
- AI and data: OpenAI-compatible LLM, XHS research adapter, Google Places/Directions/Weather, AMap fallback.
- Quality: deterministic validators, unittest-based regression suite, offline evaluation contracts, canonical JSON captures and manifests.
- Deployment: multi-stage Docker build, same-origin static/API hosting, Render-compatible `PORT` binding.

## Run locally

### Docker

Copy the documented environment template and provide your own credentials. Never commit `.env` files.

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

The application is served on `http://localhost:7860` by default.

### Development

Backend:

```bash
cd backend
npm install
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Frontend:

```bash
cd frontend
npm ci
cp .env.example .env
npm run dev
```

Core live generation requires an OpenAI-compatible model credential. XHS, Google Maps, and AMap credentials enable optional research, grounding, route, weather, hotel, image, and map capabilities. Browser map keys must use referrer restrictions, API scope restrictions, and billing caps.

## Major modifications

The repository preserves upstream history. The following describes subsequent work in this derivative, not authorship of inherited code.

| Date / phase | Major modification |
|---|---|
| 2026-08 · Phases 1–2 | Preference Profile, provider observability, POI grounding/provenance, deterministic Validator, guarded Critic/Revision loop, and affected-day patch workflow |
| 2026-08 · Phase 3 | Offline capture contracts, immutable baselines, provider snapshots, deterministic metrics, human-review schemas, and product-quality synthesis |
| 2026-08 · Phase 4A–4B | Deterministic daily-load model, confidence-aware route fallback, pacing policy, pacing-aware Planner, and pacing validation |
| 2026-08 · Phase 4C | Typed targeted pacing revision, protected-day preservation, grounding gates, fail-closed commit, and revalidation |
| 2026-08 · Phase 4D | Four controlled paired evaluations, blind human review, comparability records, and aggregate product-impact report |
| 2026-08 · Phase 5 | Public-demo hardening, cost/abuse guards, error sanitization, isolated Example Trip, and single-service Render deployment support |

## Repository layout

```text
backend/app/agents/       Planner orchestration
backend/app/services/     Providers, validation, pacing, revision, patching
backend/app/evaluation/   Offline capture and evaluation contracts
backend/tests/            Deterministic regression tests
frontend/src/             Vue application
eval/                     Versioned evaluation evidence and reports
portfolio/examples/       Sanitized, provider-independent demo data
```

## License

TripStar-AI is distributed under the GNU General Public License v2. See [LICENSE](LICENSE). Applicable third-party copyright and license notices in retained source files remain in place.

## Attribution

TripStar-AI is a substantially modified derivative of the open-source [TripStar project by 1sdv](https://github.com/1sdv/TripStar), distributed under GNU GPL v2. The current repository includes extensive modifications to planning, validation, pacing, revision, evaluation, and deployment behavior. See the repository history and [LICENSE](LICENSE) for details.
