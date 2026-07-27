"""EC2-side deployed manifest verification (fail-closed)."""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

from trustforge.release_manifest import (
    manifest_from_json,
    validate_manifest,
    _sha256_of_file,
)
from trustforge.config_snapshot import current_config_identity
from trustforge.upgrade_control import _core_hash


def verify_deployed(
    app_dir: str,
    *,
    zip_path: str | None = None,
    manifest_path: str | None = None,
    require_config_match: bool = True,
) -> tuple[bool, list[str]]:
    app = Path(app_dir)
    errors: list[str] = []

    if zip_path is None:
        zip_path = str(app / "app.zip")

    if manifest_path is None:
        manifest_path = str(app / "manifest.json")

    if not Path(zip_path).is_file():
        errors.append(f"artifact zip not found: {zip_path}")
        return False, errors

    if not Path(manifest_path).is_file():
        errors.append(f"manifest not found: {manifest_path}")
        return False, errors

    raw = Path(manifest_path).read_text()
    try:
        manifest = manifest_from_json(raw)
    except Exception as exc:
        errors.append(f"invalid manifest: {exc}")
        return False, errors

    ok, msgs = validate_manifest(manifest, zip_path)
    if not ok:
        errors.extend(msgs)

    actual_artifact_digest = _sha256_of_file(zip_path)
    if actual_artifact_digest != manifest.artifact_digest:
        errors.append(f"artifact_digest mismatch on deployed artifact: manifest={manifest.artifact_digest} actual={actual_artifact_digest}")

    actual_core_hash = _core_hash()
    if actual_core_hash != manifest.core_content_hash:
        errors.append(f"core_content_hash mismatch: manifest={manifest.core_content_hash} actual={actual_core_hash}")

    if require_config_match:
        actual_config_identity = current_config_identity()
        if actual_config_identity != manifest.config_snapshot_identity:
            errors.append(f"config_snapshot mismatch: manifest={manifest.config_snapshot_identity} actual={actual_config_identity}")

    try:
        zip_ok, zip_errors = _verify_zip_entries(app_dir)
        if not zip_ok:
            errors.extend(zip_errors)
    except Exception as exc:
        errors.append(f"zip entry verification error: {exc}")

    if errors:
        return False, errors
    return True, []


def _verify_zip_entries(app_dir: str) -> tuple[bool, list[str]]:
    app = Path(app_dir)
    zip_path = str(app / "app.zip")
    errors: list[str] = []

    if not Path(zip_path).is_file():
        return True, []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile as exc:
        errors.append(f"corrupted zip: {exc}")
        return False, errors

    required = ["trustforge/__init__.py", "trustforge/web.py"]
    for r in required:
        if r not in names:
            errors.append(f"missing required entry in zip: {r}")

    return len(errors) == 0, errors


def verify_config_drift(
    manifest_identity: str,
) -> tuple[bool, str | None]:
    actual_identity = current_config_identity()
    if actual_identity != manifest_identity:
        return False, f"config drift: expected={manifest_identity} actual={actual_identity}"
    return True, None


def main() -> int:
    app_dir = os.environ.get("TRUSTFORGE_HOME", "/opt/trustforge")
    ok, errors = verify_deployed(app_dir)
    if not ok:
        print("DEPLOYED VERIFICATION FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("DEPLOYED VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
