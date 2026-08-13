# TripStar-AI

Grounded, pacing-aware AI travel planning with deterministic validation, targeted revision, and reproducible evaluation.

[Main portfolio README](README.md) · [日本語](README_ja.md) · Public demo: **coming soon**

## What I changed

This derivative extends the upstream TripStar foundation with structured preference parsing, provider observability, grounded POIs and provenance, a deterministic daily-load model, pacing-aware planning, rule-based validation, fail-closed targeted revision, protected-day patching, offline capture, blind paired review, public-demo security controls, and a Render-compatible single-service deployment path.

## Controlled evaluation snapshot

- 4 controlled paired evaluations.
- Mean human pacing delta: **+1.00**.
- Positive pacing delta in **3/4** pairs.
- Explicit constraints preserved in **4/4** evaluable pairs.
- Blind outcomes: **3 BETTER / 1 MIXED**.
- Targeted revisions: **2 committed / 2 safely rejected**.
- The Kyoto control detected duration-compression over-correction risk.

These are descriptive controlled results, not statistical significance, causal proof, production-traffic results, or a claim of universal superiority. See the [complete portfolio README](README.md) and [Phase 4D report](eval/phase4d/phase4d_final_report.md).

## Run

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

The default local URL is `http://localhost:7860`. Credentials are supplied through ignored environment files or deployment secrets; never commit them.

## License and attribution

TripStar-AI is licensed under GNU GPL v2. It is a substantially modified derivative of the open-source [TripStar project by 1sdv](https://github.com/1sdv/TripStar). The repository preserves upstream history and applicable third-party notices. See [LICENSE](LICENSE).
