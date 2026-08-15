# TripStar Metrics

Only measured or repository-verifiable values belong here. Each future quantitative entry should include date, command/methodology, scope/sample size, result, and evidence location. Configuration, mocked capability, and unverified deployment state are not product outcomes.

## Automated tests

### Backend

- **Date/HEAD:** STEP 0, 2026-08-16, `449d0fa584b99f61386b980fe76aac53848de871`.
- **Canonical command:** `PYTHONPATH=.:backend ./backend/.venv/bin/python -m unittest discover -s backend/tests`
- **Executed/discovered as reported:** 327.
- **Passed:** 325.
- **Failed:** 1.
- **Skipped:** 1.
- **Known failure:** `test_phase3d1_capture.CaptureTests.test_dry_run_contract_identity_revision_and_unknown_semantics` expects historical commit `96b9c5e`; current captured revision is `449d0fa`.
- **Boundary:** Provider tests are largely mocked; the skipped test is the live Tokyo/Google E2E path.
- **Evidence:** `docs/PROJECT_BASELINE.md` §8 and STEP 0 command output.

#### STEP 2C verification

- **Date/pre-step HEAD:** 2026-08-16, `ff37f93531bf03233bc8da86f0696ec2cb127da8`.
- **Repair:** Replaced the historical `96b9c5e` pin with an assertion against the independently queried current short Git HEAD and optional `-dirty` suffix.
- **Targeted command:** `PYTHONPATH=.:backend ./backend/.venv/bin/python -m unittest backend.tests.test_phase3d1_capture.CaptureTests.test_dry_run_contract_identity_revision_and_unknown_semantics -v`
- **Targeted result:** 1 executed, 1 passed, 0 failed, 0 skipped.
- **Full command:** `PYTHONPATH=.:backend ./backend/.venv/bin/python -m unittest discover -s backend/tests`
- **Full result:** 327 executed, 326 passed, 0 failed, 1 skipped.
- **Skipped test:** live Tokyo/Google E2E path.
- **Output note:** Provider error/degradation messages are emitted by mocked failure-path tests and did not fail the suite.

### Frontend

- **Date/HEAD:** STEP 0, 2026-08-16, `449d0fa584b99f61386b980fe76aac53848de871`.
- **Command:** `node --test tests/*.test.cjs` from `frontend/`.
- **Passed:** 34/34.
- **Failed/skipped/todo:** 0/0/0.
- **Boundary:** These are Node source/contract and lifecycle tests, not full browser E2E tests.
- **Evidence:** `docs/PROJECT_BASELINE.md` §8 and STEP 0 command output.

#### STEP 2C verification

- **Date/pre-step HEAD:** 2026-08-16, `ff37f93531bf03233bc8da86f0696ec2cb127da8`.
- **Command:** `node --test tests/*.test.cjs` from `frontend/`.
- **Result:** 34/34 passed; 0 failed, skipped, or todo.

### Repository diff validation

- **Command:** `git diff --check`.
- **Result:** Passed in STEP 0.
- **STEP 2C result:** Passed.

## Frontend build

- **Command:** `npm run build` from `frontend/`.
- **Result:** Passed in STEP 0 (`vue-tsc` plus Vite; 3,848 modules transformed).
- **Warnings:** unresolved legacy image/font paths remained for runtime resolution; two generated JavaScript chunks exceeded the 500 kB warning threshold.
- **Evidence:** `docs/PROJECT_BASELINE.md` §8 and STEP 0 command output.

### STEP 2C verification

- **Command:** `npm run build` from `frontend/`.
- **Result:** Passed; 3,848 modules transformed.
- **Warnings:** unchanged unresolved legacy image/font paths and two JavaScript chunks above the 500 kB warning threshold. Warnings did not prevent the build.

## Evaluation metrics

The repository contains committed controlled evaluation artifacts and aggregate claims in `eval/` and `README.md`. STEP 0 did not reproduce their live captures or independently validate reviewer/model/provider conditions. No current-HEAD evaluation value is promoted into this metrics baseline.

**Current reproducible evaluation metrics:** Not yet measured.

## Performance and latency

Not yet measured.

## Reliability

No production reliability, task completion, provider recovery, restart recovery, or uptime rate has been measured in the project-memory baseline.

**Status:** Not yet measured.

## Grounding and source quality

The repository implements source/match/evidence states, but STEP 0 did not run a current representative sample to calculate grounded POI rate, unsupported recommendation rate, or source coverage.

**Status:** Not yet measured.

## Product metrics

No verified user, funnel, adoption, retention, satisfaction, conversion, revenue, or business-impact measurements are available.

**Status:** Not yet measured.
