"""Minimal guarded CLI for Phase 3D-1 artifact capture."""

import argparse
import asyncio
import os
from pathlib import Path


class CaptureBootstrapError(RuntimeError):
    pass


def bootstrap_capture_configuration(repo_root: Path) -> Path:
    """Load the same backend configuration for every supported capture target."""
    from dotenv import load_dotenv

    env_path = repo_root.resolve() / "backend" / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")):
        raise CaptureBootstrapError("capture_configuration_missing: llm_api_key")
    return env_path


async def _run(args) -> int:
    if args.mode != "dry-run":
        bootstrap_capture_configuration(args.repo_root)
    from ..agents.trip_planner_agent import PLANNER_PROMPT_VERSION, PLANNER_VERSION
    from .capture import PlannerArtifactCapture, validate_batch_selection
    from .capture_models import CaptureBudget, PlannerCaptureArtifact
    from .runner import load_cases
    from .snapshots import ProviderSnapshotStore

    cases = load_cases(args.cases)
    budget = CaptureBudget(
        max_cases=args.max_cases, max_llm_calls=args.max_llm_calls,
        max_total_tokens=args.max_total_tokens, stop_on_error=args.stop_on_error,
        case_allowlist=args.case or [],
    )
    selected = validate_batch_selection(cases, budget, args.allow_multiple_cases)
    if not selected:
        raise SystemExit("no allowlisted Golden Case selected")
    capture = PlannerArtifactCapture(args.repo_root, ProviderSnapshotStore(args.snapshot_directory))
    executor = None
    executor_context = None
    if args.mode != "dry-run":
        from .production_capture import execute_production_patch, execute_production_planner
        is_patch_case = any("local_patch" in case.scenario_tags for case in selected)
        target = args.capture_target
        if target == "auto":
            target = "patch" if is_patch_case else "planner"
        if target == "patch":
            if not is_patch_case:
                raise SystemExit("patch capture target requires a local_patch Golden Case")
            if len(selected) != 1 or args.base_artifact is None:
                raise SystemExit("local patch capture requires exactly one case and --base-artifact")
            base = PlannerCaptureArtifact.model_validate_json(args.base_artifact.read_bytes())
            if base.final_trip_plan.status != "known":
                raise SystemExit("base artifact has no known TripPlan")
            manifest_path = args.base_artifact.with_name(args.base_artifact.stem + ".manifest.json")
            if not manifest_path.is_file():
                raise SystemExit("base artifact manifest is required")
            import hashlib, json
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            actual_hash = "sha256:" + hashlib.sha256(args.base_artifact.read_bytes()).hexdigest()
            if manifest.get("artifact_hash") != actual_hash:
                raise SystemExit("base artifact hash mismatch")
            if base.identity.case_id != selected[0].case_id:
                raise SystemExit("base artifact case mismatch")
            executor = execute_production_patch
            executor_context = {
                "base_plan": base.final_trip_plan.value,
                "base_artifact_identity": base.identity.model_dump(mode="json"),
                "base_artifact_hash": actual_hash,
            }
        else:
            if target == "controlled-base" and not is_patch_case:
                raise SystemExit("controlled-base target requires a local_patch Golden Case")
            executor = execute_production_planner
    for index, case in enumerate(selected):
        await capture.capture(
            case, run_id=f"capture_{args.run_id}_{index}", mode=args.mode,
            planner_version=PLANNER_VERSION, prompt_version=PLANNER_PROMPT_VERSION,
            output_path=args.output_directory / f"{case.case_id}.json",
            budget=budget, allow_real_api=args.allow_real_api, executor=executor,
            executor_context=executor_context,
        )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tripstar-eval-capture")
    parser.add_argument("--cases", type=Path, default=Path("eval/cases/golden_cases_v1.json"))
    parser.add_argument("--case", action="append", help="Golden case allowlist; repeat for multiple cases")
    parser.add_argument("--mode", choices=("dry-run", "record", "replay"), default="dry-run")
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--snapshot-directory", type=Path, default=Path("eval/capture_snapshots/v1"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--run-id", default="manual")
    parser.add_argument("--max-cases", type=int, default=1)
    parser.add_argument("--max-llm-calls", type=int, default=4)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--stop-on-error", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-multiple-cases", action="store_true")
    parser.add_argument("--allow-real-api", action="store_true")
    parser.add_argument("--base-artifact", type=Path)
    parser.add_argument("--capture-target", choices=("auto", "planner", "controlled-base", "patch"), default="auto")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
