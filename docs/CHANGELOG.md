# TripStar Development Changelog

This changelog tracks evidence-backed development steps from the project-memory baseline forward. It is not a reconstruction of every historical commit.

## Future entry format

```text
## STEP N — Name
- Goal:
- Changes:
- Verification:
- Result:
- Commit:
- Notes:
```

## Historical baseline

- **Baseline commit:** `449d0fa584b99f61386b980fe76aac53848de871`
- Earlier repository history remains in Git and historical phase/evaluation documents. Those sources do not override current code or `docs/PROJECT_BASELINE.md`.

## STEP 0 — Repository baseline audit

- **Goal:** Establish an evidence-based snapshot of Git, structure, stack, product capabilities, AI/runtime flow, tests, deployment evidence, risks, and documentation consistency.
- **Changes:** Created `docs/PROJECT_BASELINE.md`; no application/business code changes.
- **Verification:** Backend 327 tests: 325 passed, 1 failed due to stale SHA assertion, 1 skipped. Frontend 34/34 passed. Frontend production build passed with warnings. `git diff --check` passed.
- **Result:** **COMPLETE.** The baseline is the primary evidence source for project-memory initialization.
- **Commit:** Not committed; audited HEAD was `449d0fa584b99f61386b980fe76aac53848de871`.
- **Notes:** No external provider or live deployment verification was performed.

## STEP 1 — Project memory initialization

- **Goal:** Initialize concise current-state, architecture, decision, changelog, metrics, and resume-evidence documentation.
- **Changes:** Created `docs/PROJECT_MASTER.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/CHANGELOG.md`, `docs/METRICS.md`, and `docs/RESUME_EVIDENCE.md`; no application/business code changes.
- **Verification:** Confirmed all six required files exist; cross-checked them against `docs/PROJECT_BASELINE.md`; searched new documents for contradictory capability, live-deployment, invented metric, and overstated multi-agent claims; confirmed phase/next-step wording; `git diff --check` passed.
- **Result:** **COMPLETE.** The six documents initialize the long-term project-memory system without changing application/business code.
- **Commit:** Not committed; HEAD remains `449d0fa584b99f61386b980fe76aac53848de871`.
- **Notes:** Current phase is **Project Memory / Baseline Consolidation**. Next planned step is **STEP 2 — Resolve baseline inconsistencies and define the next product improvement phase.**

## STEP 2A — Freeze Project Memory Baseline

- **Goal:** Review and version-control the evidence-backed project documentation baseline.
- **Changes:** Reviewed all seven project-memory documents; removed machine-local absolute paths; corrected transient uncommitted-state wording; prepared only `docs/` for version control.
- **Verification:** Confirmed document consistency with the audited code baseline; scanned for unsupported claims, fabricated metrics, unverified live-deployment claims, autonomous multi-agent claims, incorrect baseline references, obvious secrets, and machine-local paths; verified Markdown/diff safety and intended Git scope.
- **Result:** **COMPLETE.** All required checks passed before the documentation-only commit.
- **Commit:** Created by STEP 2A with message `docs: establish project memory baseline`; see Git history for the resulting SHA.
- **Notes:** No application/business code changed. Known baseline inconsistencies remain intentionally deferred.

## STEP 2B — Documentation Consistency Cleanup

- **Goal:** Align legacy documentation and explanatory terminology with the verified repository baseline.
- **Changes:** Updated `CURRENT_ARCHITECTURE.md`, `README.md`, `README_en.md`, `README_ja.md`, Planner explanatory docstrings/comments, `docs/PROJECT_BASELINE.md`, `docs/PROJECT_MASTER.md`, and this changelog. Replaced the stale root architecture narrative with a current compatibility reference, clarified provider/deployment evidence boundaries, and corrected autonomy-overstating explanatory terminology without renaming code identifiers or changing runtime behavior.
- **Verification:** Cross-checked README, root/current architecture, project baseline/master, ADRs, metrics, and resume evidence; searched for stale capability statements, autonomy overstatement, unsupported metrics, unverified live-deployment claims, machine-local paths, and common secret/token patterns; inspected the Python diff to confirm docstring/comment-only changes; `git diff --check` passed; intended Git scope verified.
- **Result:** **COMPLETE.** Legacy documentation and explanatory terminology now align with the verified project-memory baseline.
- **Commit:** Created by STEP 2B with message `docs: align legacy documentation with verified architecture`; see Git history for the resulting SHA.
- **Notes:** The stale backend commit assertion remains intentionally unchanged.

## STEP 2C — Repair Stale Baseline Assertion

- **Goal:** Repair the stale repository-revision expectation and re-verify the complete baseline without changing application behavior.
- **Changes:** Updated `CaptureTests.test_dry_run_contract_identity_revision_and_unknown_semantics` to compare captured revision metadata with an independently queried current short Git HEAD, allowing only the function's documented optional `-dirty` suffix. Updated project-memory verification records while preserving the original STEP 0 failure.
- **Verification:** Targeted backend test: 1 passed, 0 failed/skipped. Full backend command `PYTHONPATH=.:backend ./backend/.venv/bin/python -m unittest discover -s backend/tests`: 327 executed, 326 passed, 0 failed, 1 skipped. Frontend `node --test tests/*.test.cjs`: 34/34 passed. Frontend `npm run build`: passed with existing unresolved legacy asset/font and oversized-chunk warnings. `git diff --check` passed; changed-file scope verified.
- **Result:** **COMPLETE.** The stale assertion was repaired without changing application behavior, and all required verification gates passed.
- **Commit:** Created by STEP 2C with message `test: repair stale baseline assertion`; see Git history for the resulting SHA.
- **Notes:** Provider failure lines in backend output are expected mocked failure-path logs. The live Tokyo/Google E2E test remains skipped.

## STEP 2D — Debug / Badcase Knowledge System

- **Goal:** Establish a long-term Debug and Badcase knowledge system so important issues can form a traceable Problem → Root Cause → Fix → Verification → Metrics → Resume Evidence chain.
- **Changes:** Created `docs/DEBUG_LOG.md`; defined case inclusion/exclusion standards, sequential `DBG-XXX` IDs, a uniform case template, evidence and maintenance rules, and the cross-document evidence lifecycle. Added `DBG-001` for the evidence-backed stale evaluation-capture revision assertion. Added DEBUG_LOG to the project memory map and clarified its conditional role as an input to future resume/interview evidence.
- **Verification:** Cross-checked DEBUG_LOG against PROJECT_MASTER, CHANGELOG, METRICS, RESUME_EVIDENCE, the STEP 2C test/implementation, and Git history; confirmed the historical investigation boundary and root cause evidence; found no fabricated metrics or exaggerated resume claims; common secret/token scan found no candidates; machine-local absolute path scan found none; evidence references are repository-relative; `git diff --check` passed; documentation-only Git scope verified.
- **Result:** **COMPLETE.** The Debug/Badcase knowledge layer is initialized with one evidence-backed historical case and durable maintenance rules.
- **Commit:** Created by STEP 2D with message `docs: add debug and badcase knowledge system`; see Git history for the resulting SHA.
- **Notes:** No application/business logic changes. No speculative provider, grounding, async recovery, or AI hallucination history was added.

## Production XHS Search v2 contract migration and validation

- **Goal:** Replace the stale XHS Search browser contract while preserving existing security boundaries, fallback behavior, request counts, and provider degradation semantics.
- **Changes:** Migrated Search from `edith.xiaohongshu.com/api/sns/web/v1/search/notes` to `so.xiaohongshu.com/api/sns/web/v2/search/notes`; added one private UUIDv4-compatible Search session identifier per `XhsNativeClient`; retained an independent per-Search `search_id`; signed the exact v2 path and minimal payload; aligned Search authority with the new host. Detail, SSR, Cookie handling, signing assets, timeouts, retries, concurrency, frontend, and public schemas were unchanged.
- **Verification:** Focused v2 contract tests: 5 passed. Targeted regressions: 147 passed. Complete backend suite: 443 passed, 1 skipped, 0 failed. Compileall, `git diff --check`, and secret/privacy scan passed with zero real provider calls. In one controlled production trip after deployment, 4 observed `PHOTO_TERMINAL` events reported successful XHS image outcomes, and the trip completed successfully.
- **Result:** **COMPLETE / MONITORING.** Production evidence establishes that the migrated Search chain can produce usable downstream XHS images. The supplied validation did not show the earlier deterministic Search rejection, but does not prove that business code `-100` can never recur or that the provider is universally compatible.
- **Commit:** `a1a99cfd6bd84b2ccd864dd9502cce87fc35eb96` (`fix: migrate xhs search to v2 contract`).
- **Notes:** The single controlled run recorded `total_trip=103201 ms`, `xhs_research=61478 ms`, and `planner=31997 ms`; performance remains a separate issue. `xhs_research success=true` is not evidence of research quality or correctness. Missing matching `XHS_EVENT` entries were not treated as additional evidence.
