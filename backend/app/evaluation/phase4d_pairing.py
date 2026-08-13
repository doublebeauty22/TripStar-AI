"""Phase 4D pairing and blinded-review material generation (offline only)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .capture_models import PlannerCaptureArtifact


EXPECTED_BASELINE = {
    "gc_shenzhen_overbudget_revision": ("planner_baseline_v1", "planner_prompt_v1"),
    "gc_chengdu_budget": ("planner_baseline_v1", "planner_prompt_v1"),
    "gc_lijiang_places_unavailable": ("planner_baseline_v1", "planner_prompt_v1"),
    "gc_kyoto_no_early_start": ("planner_baseline_v1", "planner_prompt_v1"),
}
EXPECTED_CANDIDATE = ("planner_pacing_v1", "planner_prompt_pacing_v1")


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_verified_baseline(path: Path) -> PlannerCaptureArtifact:
    artifact = PlannerCaptureArtifact.model_validate_json(path.read_bytes())
    expected = EXPECTED_BASELINE.get(artifact.identity.case_id)
    if expected is None or (artifact.identity.planner_version, artifact.identity.prompt_version) != expected:
        raise ValueError("baseline_identity_mismatch")
    manifest_path = path.with_name(path.stem + ".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("artifact_hash") != sha256_file(path):
        raise ValueError("baseline_hash_mismatch")
    return artifact


def compare_pair(baseline: PlannerCaptureArtifact,
                 candidate: PlannerCaptureArtifact) -> dict[str, Any]:
    if baseline.identity.case_id != candidate.identity.case_id:
        raise ValueError("case_identity_mismatch")
    if baseline.trip_request.model_dump(mode="json") != candidate.trip_request.model_dump(mode="json"):
        raise ValueError("trip_request_mismatch")
    if (candidate.identity.planner_version, candidate.identity.prompt_version) != EXPECTED_CANDIDATE:
        raise ValueError("candidate_identity_mismatch")
    shared = sorted(set(baseline.snapshot_hashes) & set(candidate.snapshot_hashes))
    mismatched = [name for name in shared
                  if baseline.snapshot_hashes[name] != candidate.snapshot_hashes[name]]
    missing = sorted(set(baseline.snapshot_hashes) ^ set(candidate.snapshot_hashes))
    exact = bool(shared) and not mismatched and not missing
    return {
        "case_id": baseline.identity.case_id,
        "trip_request_equal": True,
        "provider_snapshot_exact": exact,
        "shared_provider_snapshots": shared,
        "mismatched_provider_snapshots": mismatched,
        "missing_provider_snapshots": missing,
        "comparability": "controlled" if exact else "limited_provider_drift",
        "causal_claim_allowed": exact,
        "baseline_identity": baseline.identity.model_dump(mode="json"),
        "candidate_identity": candidate.identity.model_dump(mode="json"),
    }


def _plan_markdown(label: str, artifact: PlannerCaptureArtifact) -> str:
    plan = artifact.final_trip_plan.value
    lines = [f"## {label}", ""]
    for position, day in enumerate(plan["days"]):
        lines.extend([
            f"### Day {position + 1} — {day.get('date')} — start {day.get('start_time') or 'unspecified'}",
            "", day.get("description") or "", "",
            "Activities:", "",
        ])
        for poi in day.get("attractions", []):
            lines.append(f"- {poi.get('name')} — {poi.get('visit_duration')} min: {poi.get('description', '')}")
        lines.extend(["", "Meals:", ""])
        for meal in day.get("meals", []):
            lines.append(f"- {meal.get('type')}: {meal.get('name')} — {meal.get('description', '')}")
        lines.extend(["", f"Transport: {day.get('transportation', '')}", ""])
    lines.extend(["Overall suggestions:", "", plan.get("overall_suggestions", ""), ""])
    return "\n".join(lines)


def blind_order(case_id: str) -> tuple[str, str]:
    # Stable and concealed from the review material; identity is revealed separately.
    return ("candidate", "baseline") if hashlib.sha256(case_id.encode()).digest()[0] % 2 else ("baseline", "candidate")


def generate_blind_review(
    baseline: PlannerCaptureArtifact, candidate: PlannerCaptureArtifact,
) -> tuple[str, dict[str, str]]:
    order = blind_order(baseline.identity.case_id)
    artifacts = {"baseline": baseline, "candidate": candidate}
    mapping = {"Plan A": order[0], "Plan B": order[1]}
    sections = [
        f"# Blind Paired Review — {baseline.identity.case_id}", "",
        "Reviewer: Yi Huang", "Rubric: human.v1", "",
        "Do not inspect identity_reveal.json until this review is complete. Validator results and historical scores are intentionally hidden.", "",
        _plan_markdown("Plan A", artifacts[order[0]]),
        _plan_markdown("Plan B", artifacts[order[1]]),
        "## Independent scores", "",
        "Fill 1–5 with rationale for each plan: Preference Satisfaction, Itinerary Coherence, Pacing Quality, Usefulness, Explanation Quality.", "",
        "Unsupported fact verdict for each plan: yes / no / uncertain, with rationale.", "",
        "## Paired questions", "",
        "1. Which plan is less rushed?", "2. Did either plan improve metrics by deleting an important POI?",
        "3. Was any removed/shortened activity central to the user interest?", "4. Is either plan underpacked?",
        "5. Did coherence regress?", "6. Which is more executable?", "7. Is revision wording natural?",
        "8. Is there metric improvement with worse user experience?", "",
        "Final paired verdict before reveal: Plan A better / Plan B better / mixed / equivalent.", "",
    ]
    return "\n".join(sections), mapping
