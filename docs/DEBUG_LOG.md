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
