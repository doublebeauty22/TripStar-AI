"""Sanitized provider snapshot storage and all-or-nothing capture commits."""

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from .fixtures import content_hash, write_canonical_json
from .models import SanitizedProviderFixture


class SnapshotError(ValueError):
    pass


class ProviderSnapshotStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, case_id: str, provider: str) -> Path:
        if not case_id.replace("_", "").isalnum() or not provider.replace("_", "").isalnum():
            raise SnapshotError("invalid snapshot identity")
        return self.root / case_id / f"{provider}.json"

    def path(self, case_id: str, provider: str) -> Path:
        return self._path(case_id, provider)

    def record(self, case_id: str, fixture: SanitizedProviderFixture) -> str:
        path = self._path(case_id, fixture.provider)
        write_canonical_json(path, fixture.model_dump(mode="json"))
        return content_hash(path.read_bytes())

    def replay(self, case_id: str, provider: str) -> tuple[SanitizedProviderFixture, str]:
        path = self._path(case_id, provider)
        if not path.is_file():
            raise SnapshotError(f"snapshot unavailable: {case_id}/{provider}")
        raw = path.read_bytes()
        try:
            fixture = SanitizedProviderFixture.model_validate_json(raw)
        except Exception as exc:
            raise SnapshotError(f"invalid snapshot: {exc}") from exc
        if fixture.provider != provider:
            raise SnapshotError("snapshot provider mismatch")
        return fixture, content_hash(raw)


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def commit_capture_set(*, output_path: Path, snapshot_store: ProviderSnapshotStore,
                       case_id: str, artifact: dict,
                       fixtures: dict[str, SanitizedProviderFixture],
                       hash_function: Callable[[bytes], str] = content_hash,
                       replace_function: Callable[[str, str], None] = os.replace,
                       artifact_validator: Callable[[dict], object] | None = None) -> dict[str, str]:
    """Stage, verify, then publish files; manifest is the commit marker."""
    output_path = output_path.resolve()
    manifest_path = output_path.with_name(output_path.stem + ".manifest.json")
    official_paths = [output_path, manifest_path] + [
        snapshot_store.path(case_id, provider) for provider in fixtures
    ]
    if any(path.exists() for path in official_paths):
        raise SnapshotError("capture target already exists")
    stage_parent = output_path.parent.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix="tripstar-capture-", dir=str(stage_parent)))
    published: list[Path] = []
    try:
        staged_artifact = stage_root / "artifact.json"
        staged_artifact.write_bytes(canonical_bytes(artifact))
        staged_snapshots: dict[str, Path] = {}
        hashes: dict[str, str] = {}
        for provider, fixture in fixtures.items():
            validated = SanitizedProviderFixture.model_validate(fixture.model_dump(mode="json"))
            staged = stage_root / "snapshots" / f"{provider}.json"
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(canonical_bytes(validated.model_dump(mode="json")))
            digest = hash_function(staged.read_bytes())
            if digest != content_hash(staged.read_bytes()):
                raise SnapshotError("snapshot hash verification failed")
            hashes[provider] = digest
            staged_snapshots[provider] = staged
        artifact_with_hashes = dict(artifact)
        artifact_with_hashes["snapshot_hashes"] = hashes
        if artifact_validator is None:
            from .capture_models import PlannerCaptureArtifact
            artifact_validator = PlannerCaptureArtifact.model_validate
        artifact_validator(artifact_with_hashes)
        staged_artifact.write_bytes(canonical_bytes(artifact_with_hashes))
        artifact_hash = content_hash(staged_artifact.read_bytes())
        manifest = {
            "capture_status": "complete", "case_id": case_id,
            "artifact": output_path.name, "artifact_hash": artifact_hash,
            "snapshot_hashes": hashes,
        }
        staged_manifest = stage_root / "manifest.json"
        staged_manifest.write_bytes(canonical_bytes(manifest))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        for provider, staged in staged_snapshots.items():
            target = snapshot_store.path(case_id, provider)
            target.parent.mkdir(parents=True, exist_ok=True)
            replace_function(str(staged), str(target)); published.append(target)
        replace_function(str(staged_artifact), str(output_path)); published.append(output_path)
        replace_function(str(staged_manifest), str(manifest_path)); published.append(manifest_path)
        return hashes
    except Exception:
        for path in reversed(published):
            path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
