"""Repository-integrity checks for the reviewed shadow candidate manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def test_reviewed_shadow_candidate_manifest_pins_current_file_bytes() -> None:
    root = Path(__file__).parents[1]
    manifest_path = root / "data/contracts/reviewed-shadow-candidate.v1.json"
    manifest = json.loads(manifest_path.read_bytes())

    assert manifest["files"]
    for relative_path, expected_digest in manifest["files"].items():
        candidate_path = root / relative_path
        actual_digest = "sha256:" + hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        assert actual_digest == expected_digest, (
            f"{relative_path} changed without a reviewed-candidate repin"
        )
