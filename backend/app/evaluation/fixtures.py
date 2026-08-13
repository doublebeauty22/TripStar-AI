"""Frozen fixture loading and hashing; never performs network I/O."""

import hashlib
import json
from pathlib import Path
from typing import Dict, Tuple

from .models import EvalCase, SanitizedProviderFixture


class FixtureError(ValueError):
    pass


def content_hash(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class FrozenFixtureResolver:
    def __init__(self, eval_root: Path):
        self.eval_root = eval_root.resolve()

    def resolve(self, case: EvalCase) -> Tuple[Dict[str, SanitizedProviderFixture], Dict[str, str]]:
        fixtures: Dict[str, SanitizedProviderFixture] = {}
        hashes: Dict[str, str] = {}
        for requirement in case.provider_fixtures:
            relative = Path(requirement.fixture_ref)
            path = (self.eval_root / relative).resolve()
            if self.eval_root not in path.parents or not path.is_file():
                raise FixtureError(f"unresolved fixture: {requirement.fixture_ref}")
            raw = path.read_bytes()
            try:
                fixture = SanitizedProviderFixture.model_validate_json(raw)
            except Exception as exc:
                raise FixtureError(f"invalid fixture {requirement.fixture_ref}: {exc}") from exc
            if fixture.provider != requirement.provider or fixture.state != requirement.state:
                raise FixtureError(f"fixture contract mismatch: {requirement.fixture_ref}")
            if fixture.fixture_version != requirement.fixture_version:
                raise FixtureError(f"fixture version mismatch: {requirement.fixture_ref}")
            key = requirement.fixture_ref
            fixtures[key] = fixture
            hashes[key] = content_hash(raw)
        return fixtures, hashes


def write_canonical_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
