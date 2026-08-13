# Frozen provider fixtures

Phase 3A defines fixture requirements but does not capture or call providers. Phase 3B
will add only synthetic or sanitized payloads wrapped by `SanitizedProviderFixture`.

Rules:

- No API keys, cookies, authorization headers, tokens, user identifiers, raw prompts,
  or unrestricted provider dumps.
- Keep the minimum fields consumed by TripStar parsers and trust gates.
- Every fixture is immutable within its `vN`; corrections create a new version.
- Baseline and candidate in one paired run must share the same
  `fixture_set_version` and resolved fixture content hashes.
- Unavailable, partial and degraded behavior is represented by explicit fixture
  state and typed reason fields, never by network failure during an eval.

Planned subdirectories: `xhs/`, `google_places/`, `google_directions/`,
`google_weather/`, and `amap/`.
