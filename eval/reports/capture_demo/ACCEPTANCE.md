# Phase 3D-1 Capture Acceptance

No real provider or LLM was called.

| Case | Evidence | Result |
|---|---|---|
| Dry-run | `dry_run/gc_beijing_baseline.json` | Complete contract; Planner/provider/usage fields are explicit `unknown` or `not_applicable` |
| Mock production run | `mock_record.json` | Provider states, route checks, usage, latency and model identity captured |
| Secret injection | `test_secret_sanitization_rejects_artifact_and_snapshot` | Rejected before artifact output |
| Record/replay | `mock_record.json`, `mock_replay.json`, `snapshots/` | Same canonical snapshot hashes in both artifacts |
| Real call without opt-in | capture CLI in `record` mode without `--allow-real-api` | Rejected with `real_api_not_allowed` before executor invocation |

Recorded and replayed mock hashes:

- XHS: `sha256:9e3ce5c5e5778334bc0d302c876a0fb2cbc3520082a3567db4dea35098845434`
- Google Places: `sha256:8016332b58bb32dc2ff09841db8a7d65670f56788791339aa843d749b020e89b`

These artifacts prove capture mechanics only. They are synthetic and must not be used as a Planner quality baseline.
