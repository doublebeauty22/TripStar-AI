# TripStar Debug / Badcase Log

## Purpose

This document is the long-term knowledge layer for important Debug cases, AI/Product badcases, root-cause analysis, fixes, verification, and reusable lessons. It exists to improve future diagnosis, prevent recurrence, support evaluation and product/architecture decisions, and provide evidence candidates for interviews or resume work.

It is not a raw terminal log, an issue tracker for every typo, or a place to make the project appear more complex. A case belongs here only when repository evidence supports the problem and its lasting value. Unknown facts remain `Unknown`; unavailable historical investigation is labelled explicitly rather than reconstructed.

## Relationship to project memory

- `docs/DEBUG_LOG.md`: important problem, impact, root cause, fix/mitigation, verification, and reusable lesson.
- `docs/CHANGELOG.md`: what changed in each project STEP.
- `docs/DECISIONS.md`: why an important product or architecture choice was made.
- `docs/METRICS.md`: measured and reproducible quantitative results.
- `docs/RESUME_EVIDENCE.md`: only sufficiently supported outcomes that may be used conservatively in a resume or interview.
- `docs/PROJECT_BASELINE.md`: audit-time repository state and later verification notes.
- `docs/PROJECT_MASTER.md`: concise current project state and memory map.

The evidence lifecycle is:

```text
Problem / Badcase
        ↓
DEBUG_LOG
        ↓
Fix / Product Decision
        ↓
CHANGELOG / DECISIONS
        ↓
Evaluation
        ↓
METRICS
        ↓
RESUME_EVIDENCE
```

Entry into `DEBUG_LOG.md` does not automatically justify a resume claim. Promotion requires reliable verification and, for quantitative claims, a defined metric, methodology, sample size, baseline, result, date, and evidence location.

## What is worth recording

Record issues with durable product, AI, architecture, reliability, evaluation, or engineering value, including:

- LLM hallucination or unsupported recommendation.
- Structured-output or data-contract failure.
- Itinerary feasibility or important validation failure.
- POI grounding or evidence attribution failure.
- Provider/API failure that materially affects user experience.
- Patch/version consistency or state-management failure.
- Async task, recovery, or state-loss failure.
- Material performance or reliability problem.
- Architecture-level bug or recurring regression.
- A problem that causes an important product/technical decision.
- A problem with a reproducibly measured before/after result.

Usually do not record ordinary syntax/spelling errors, one-off package installation issues, routine shell mistakes, local path typos, or isolated environment configuration errors. Record one only when it reveals a deeper reusable architecture, product, AI-system, or engineering-process issue.

## Evidence and writing rules

1. Use sequential IDs: `DBG-001`, `DBG-002`, and so on. Never reuse an ID.
2. Status must be `OPEN`, `FIXED`, `MITIGATED`, or `MONITORING`.
3. Describe the product/system symptom and impact, not only an exception string.
4. Do not reconstruct historical investigation from final code. Use `Historical investigation details unavailable.` when appropriate.
5. Root cause must be evidenced; otherwise write `Unknown`.
6. Metrics must be measured. When none exist, write `Not measured`.
7. Evidence must use repository-relative paths, test identifiers, commits, or project-memory/evaluation artifacts. Do not store machine-local absolute paths or secrets.
8. Resume relevance must be `HIGH`, `MEDIUM`, or `LOW`, with one conservative sentence. It is not a resume bullet.
9. At the end of every future Codex STEP, ask: “Did this STEP reveal a badcase/root cause with long-term value?” If yes and evidence is sufficient, update this file. If no, do not create a case merely to satisfy process. This file is not required to change in every STEP.

## Debug Case template

```markdown
## DBG-XXX — Problem name

### Status
OPEN | FIXED | MITIGATED | MONITORING

### Category
AI / Hallucination | Grounding | Validation | Provider | State Management | Async / Recovery | Performance | Data Contract | Evaluation | Infrastructure

### Related STEP
STEP N

### Related Commit
Commit SHA or `Not committed`

### Symptom
What was actually observed from the product or system perspective?

### User / Product Impact
Why did the problem matter?

### Investigation
Only evidence-backed investigation. If unavailable: `Historical investigation details unavailable.`

### Root Cause
Confirmed root cause, or `Unknown`.

### Fix / Mitigation
What changed?

### Verification
Test/evaluation/manual method, command where useful, and sample size.

### Result
Current state after the fix or mitigation.

### Metrics
Measured values, or `Not measured`.

### Evidence
- Repository-relative paths, test identifiers, commits, and project-memory/evaluation artifacts.

### Reusable Lesson
The durable product, AI, architecture, or engineering lesson.

### Resume / Interview Relevance
HIGH | MEDIUM | LOW — one conservative sentence explaining why.
```

## Recorded cases

## DBG-001 — Evaluation capture test pinned to a historical Git revision

### Status

FIXED

### Category

Evaluation / Data Contract

### Related STEP

STEP 2C — Repair Stale Baseline Assertion

### Related Commit

`89be13bfa4344c37bf41ae93e30a68012a145ce6`

### Symptom

The canonical backend suite failed one evaluation-capture test after the repository advanced beyond commit `96b9c5e`. The artifact correctly reported the current repository revision, while the test required the historical revision to appear in `artifact.identity.code_revision`.

### User / Product Impact

The failure did not change itinerary behavior, but it made the verified backend baseline fail and reduced confidence in evaluation reproducibility. A capture identity test coupled to an old commit can falsely report a regression whenever documentation or code legitimately advances.

### Investigation

Historical investigation details unavailable.

During STEP 2C, repository evidence established that the failing assertion was in `CaptureTests.test_dry_run_contract_identity_revision_and_unknown_semantics`, while production capture metadata came from `current_code_revision()`, which executes `git rev-parse --short HEAD` and appends `-dirty` when the worktree has changes.

### Root Cause

The test asserted that `artifact.identity.code_revision` contained hard-coded historical commit `96b9c5e`, but the production contract intentionally derives revision identity from current Git state. The test therefore encoded a transient repository value instead of the metadata behavior it was meant to verify.

### Fix / Mitigation

The test now independently queries the current short Git HEAD and accepts exactly that revision or the documented `{revision}-dirty` form. Production capture code and application behavior were not changed.

### Verification

- Targeted unittest: 1 executed, 1 passed, 0 failed, 0 skipped.
- Full backend suite: 327 executed, 326 passed, 0 failed, 1 skipped.
- Frontend suite: 34/34 passed.
- Frontend production build: passed with existing unresolved asset/font and oversized-chunk warnings.
- `git diff --check`: passed.

### Result

The repository-revision identity test remains meaningful across future commits, and STEP 2C restored a failure-free canonical backend suite while preserving one explicitly skipped live E2E test.

### Metrics

- Targeted verification sample: 1 test, 1 passed.
- Backend verification sample: 327 tests, 326 passed, 1 skipped, 0 failed.
- Frontend verification sample: 34 tests, 34 passed, 0 failed/skipped.

These are verification counts, not product-performance or user-impact metrics.

### Evidence

- `backend/tests/test_phase3d1_capture.py`
- `backend/app/evaluation/capture.py#current_code_revision`
- Commit `89be13bfa4344c37bf41ae93e30a68012a145ce6`
- `docs/CHANGELOG.md` — STEP 2C
- `docs/METRICS.md` — STEP 2C verification
- `docs/PROJECT_BASELINE.md` — STEP 0 result and later STEP 2C verification

### Reusable Lesson

Tests for reproducibility metadata should verify how identity is derived, not pin a transient repository value. Historical artifacts may retain their original revision, while current tests should remain valid as Git history advances.

### Resume / Interview Relevance

MEDIUM — it is a well-evidenced example of protecting evaluation integrity and reproducibility, but it is test-maintenance work rather than demonstrated user or business impact.

## DBG-002 — XHS Search used a stale browser contract

### Status

FIXED

### Category

Provider / Data Contract

### Related STEP

Production XHS Search v2 contract migration and controlled validation

### Related Commit

`a1a99cfd6bd84b2ccd864dd9502cce87fc35eb96`

### Symptom

Production XHS image fallback repeatedly reached the Search provider but received a parsed business rejection with bounded telemetry `category=business_rejected`, `business_code=-100`, and `retryable=false`. The repository does not establish the provider-specific meaning of `-100`.

### User / Product Impact

When Google photo retrieval could not satisfy an attraction image request, the XHS fallback could not produce usable Search results and the image path degraded to placeholders.

### Investigation

Repository inspection showed that TripStar sent Search to `edith.xiaohongshu.com` at `/api/sns/web/v1/search/notes`. A user-supplied, value-redacted browser capture from a successful current XHS web Search showed POST requests to `so.xiaohongshu.com` at `/api/sns/web/v2/search/notes` with a different minimal payload contract.

Structural browser evidence showed that `session_id` had canonical UUID shape, remained stable across searches in the same page session, and changed after a page refresh, while `search_id` changed for each new search. No captured identifier, Cookie, signature, query, header value, or response body was stored in the repository.

### Root Cause

TripStar's Search adapter used a stale host, API-version path, and payload contract relative to the successful current browser Search contract. The provider-specific semantic meaning of business code `-100` remains unknown, so it is not classified more narrowly.

### Fix / Mitigation

Search alone was migrated to `so.xiaohongshu.com/api/sns/web/v2/search/notes`. Each `XhsNativeClient` now creates a private in-memory UUIDv4-compatible `session_id`, reuses it across that client's Search calls, and continues generating an independent `search_id` for every Search. The transmitted minimal v2 payload is signed with the exact v2 path and payload. Search authority was aligned with the new host. Detail, SSR, Cookie handling, signing assets, timeout, retry, concurrency, provider order, frontend, and public schemas were unchanged; no v1 automatic fallback was added.

### Verification

- Focused XHS v2 contract tests: 5 passed.
- Targeted XHS/image/research/Planner/Google regression set: 147 passed.
- Complete backend suite: 443 passed, 1 skipped, 0 failed.
- Python compileall, `git diff --check`, and secret/privacy scan passed.
- Real provider calls during offline verification: 0.
- One controlled production trip after deployment completed successfully.
- The supplied production evidence contained 4 observed `PHOTO_TERMINAL` events with `source=xhs`, `outcome=success`, `category=success`, and `retryable=false`.

### Result

The controlled production run provides direct downstream evidence that the deployed migrated Search chain can produce usable XHS image results. The previous deterministic Search rejection was not observed in the supplied validation evidence. This does not establish universal provider compatibility or prove that `-100` cannot recur.

### Metrics

For the single controlled production trip:

- `total_trip`: 103,201 ms, success.
- `xhs_research`: 61,478 ms, success.
- `planner`: 31,997 ms, success.
- `poi_enrichment`: 6,177 ms, success.
- `weather`: 1,369 ms, success.
- `hotel_search`: 570 ms, success.
- `validator`: 0 ms, success.
- `knowledge_graph`: 0 ms, success.
- `persistence`: 3 ms, success.
- Observed successful XHS image terminal outcomes: 4.

This is a one-run production observation, not a latency benchmark or reliability rate. `xhs_research success=true` proves that the fail-open stage completed, not that XHS research quality or correctness was verified.

### Evidence

- `backend/app/services/xhs_service.py`
- `backend/tests/test_xhs_v2_search_contract.py`
- Commit `a1a99cfd6bd84b2ccd864dd9502cce87fc35eb96`
- User-supplied structural browser-contract observations and controlled production telemetry dated 2026-08-23; raw identifiers, credentials, queries, and provider bodies were intentionally not retained.
- `docs/CHANGELOG.md` — XHS Search v2 migration and production validation entry.

### Reusable Lesson

For browser-backed provider integrations, an HTTP-successful but business-rejected response can indicate contract drift. Compare current request structure without retaining secrets, sign the exact path and payload transmitted, preserve bounded failure telemetry, and validate recovery through downstream terminal outcomes rather than treating missing error logs as proof.

### Resume / Interview Relevance

MEDIUM — this is a production-validated provider-contract migration with explicit privacy boundaries, but the evidence is one controlled run and does not support universal reliability or performance claims.
