"""Generate the three required fully synthetic Phase 3B acceptance comparisons."""

import json
from copy import deepcopy
from pathlib import Path

from .comparison import compare_runs
from .fixtures import FrozenFixtureResolver
from .models import ArtifactEvaluationInput, EvalCase, PlannerVersionMetadata, UsageMetadata
from .reports import write_json_report, write_markdown_report
from .runner import evaluate_artifact


def _plan(case: EvalCase) -> dict:
    from datetime import date, timedelta
    days = []
    for index in range(case.trip_request.travel_days):
        days.append({
            "date": str(date.fromisoformat(case.trip_request.start_date) + timedelta(days=index)),
            "day_index": index, "start_time": "09:30", "city": case.trip_request.city,
            "description": "Synthetic offline acceptance artifact",
            "transportation": case.trip_request.transportation,
            "accommodation": case.trip_request.accommodation,
            "attractions": [{"name": f"Synthetic POI {index}", "address": "Synthetic address",
                "location": {"longitude": 116 + index, "latitude": 39 + index},
                "visit_duration": 60, "description": "Fixture-backed synthetic POI",
                "place_id": f"synthetic-{index}", "poi_match_status": "verified",
                "map_data_source": "google_places"}], "meals": []})
    return {"city": case.trip_request.city, "cities": [case.trip_request.city],
        "start_date": case.trip_request.start_date, "end_date": case.trip_request.end_date,
        "days": days, "overall_suggestions": "Synthetic artifact only",
        "budget": {"total_attractions": 1000, "total_hotels": 0, "total_meals": 0,
            "total_transportation": 0, "total_inter_city_transport": 0, "total": 1000},
        "validation_status": "passed", "risks": []}


def generate(eval_root: Path, output_root: Path) -> dict[str, str]:
    cases = [EvalCase.model_validate(item) for item in json.loads((eval_root / "cases/golden_cases_v1.json").read_text())]
    case = next(item for item in cases if item.case_id == "gc_beijing_baseline")
    _, hashes = FrozenFixtureResolver(eval_root).resolve(case)
    base_input = ArtifactEvaluationInput(output=_plan(case), usage=UsageMetadata(logical_llm_calls=2, prompt_tokens=100, completion_tokens=50, total_tokens=150), latency_ms=500)

    def run(run_id, artifact):
        return evaluate_artifact(case, artifact, PlannerVersionMetadata(
            planner_version="planner.demo", prompt_version="prompt.demo", model="offline-artifact",
            eval_run_id=run_id, case_id=case.case_id, fixture_set_version="fixtures.v1"),
            hashes, f"synthetic://{run_id}", "2026-08-12T00:00:00+00:00")

    baseline = run("eval_demo_baseline", base_input)
    candidates = {}
    candidates["pass"] = run("eval_demo_pass", base_input.model_copy(deep=True))
    soft = base_input.model_copy(deep=True); soft.usage.total_tokens = 151
    candidates["investigate"] = run("eval_demo_investigate", soft)
    blocked = base_input.model_copy(deep=True); blocked.output = {"invalid": "trip plan"}
    candidates["block"] = run("eval_demo_block", blocked)

    decisions = {}
    for label, candidate in candidates.items():
        directory = output_root / label
        comparison = compare_runs(baseline, candidate, f"comparison_demo_{label}")
        write_json_report(directory / "baseline.json", baseline)
        write_json_report(directory / "candidate.json", candidate)
        write_json_report(directory / "comparison.json", comparison)
        write_markdown_report(directory / "report.md", comparison, baseline, candidate)
        decisions[label] = comparison.release_decision
    return decisions


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[3]
    print(json.dumps(generate(root / "eval", root / "eval/reports/acceptance_demo"), sort_keys=True))
