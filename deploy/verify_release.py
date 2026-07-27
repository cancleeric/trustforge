#!/usr/bin/env python3
"""Local pre-deploy verification gate: fail-closed.

Usage:
    python deploy/verify_release.py <zip_path> <manifest.json>
    python deploy/verify_release.py <zip_path> --compute-manifest
    python deploy/verify_release.py <zip_path> --compute-manifest --config-snapshot <snapshot.json>

Exit codes:
    0   verified
    1   verification failed (mismatch, tamper, dirty build, etc.)
    2   usage error
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from trustforge.release_manifest import (
    ReleaseManifest,
    compute_manifest,
    validate_manifest,
    manifest_to_json,
    manifest_from_json,
)
from trustforge.config_snapshot import current_config_identity


def _git_sha_short() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _is_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-uno"],
            capture_output=True, text=True, check=True,
        )
        return bool(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _verify(
    zip_path: str,
    manifest: ReleaseManifest,
    *,
    require_git_match: bool = True,
    require_clean: bool = True,
    require_config_match: bool = True,
) -> tuple[bool, list[str]]:
    errors: list[str] = []

    ok, errs = validate_manifest(manifest, zip_path)
    if not ok:
        errors.extend(errs)

    if require_git_match:
        git_sha = _git_sha_short()
        if git_sha != "unknown" and manifest.git_sha != git_sha:
            errors.append(f"git_sha mismatch: manifest={manifest.git_sha} current={git_sha}")

    if require_clean:
        if _is_dirty():
            errors.append("dirty build detected: uncommitted changes present")

    if require_config_match:
        actual_config_identity = current_config_identity()
        if manifest.config_snapshot_identity != actual_config_identity:
            errors.append(
                f"config_snapshot mismatch: manifest={manifest.config_snapshot_identity} "
                f"current={actual_config_identity}"
            )

    if not errors:
        return True, []
    return False, errors


def _verify_inside_zip(zip_path: str, manifest: ReleaseManifest) -> tuple[bool, list[str]]:
    errors: list[str] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        try:
            raw = zf.read("trustforge/__init__.py")
        except KeyError:
            raw = None
        if raw is not None:
            ver_line = None
            for line in raw.decode("utf-8").splitlines():
                if "__version__" in line:
                    ver_line = line.strip()
                    break
            if ver_line and manifest.app_version not in ver_line:
                errors.append(f"app_version not found in zip __init__.py: expected {manifest.app_version}")
    return len(errors) == 0, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="TrustForge pre-deploy verification gate")
    parser.add_argument("zip", help="Path to the artifact zip")
    parser.add_argument("manifest", nargs="?", help="Path to manifest JSON")
    parser.add_argument("--compute-manifest", action="store_true", help="Compute manifest from zip")
    parser.add_argument("--config-snapshot", help="Path to config snapshot JSON")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow dirty builds")
    parser.add_argument("--output-manifest", help="Write computed manifest to file")
    args = parser.parse_args()

    zip_path = args.zip
    if not os.path.isfile(zip_path):
        print(f"ERROR: zip not found: {zip_path}", file=sys.stderr)
        return 1

    if args.compute_manifest:
        config_bytes = b"{}"
        if args.config_snapshot:
            if os.path.isfile(args.config_snapshot):
                config_bytes = Path(args.config_snapshot).read_bytes()
            else:
                print(f"ERROR: config snapshot not found: {args.config_snapshot}", file=sys.stderr)
                return 1
        manifest = compute_manifest(zip_path, config_bytes)
        if args.output_manifest:
            Path(args.output_manifest).write_text(manifest_to_json(manifest))
            print(f"Manifest written to {args.output_manifest}")
        print(manifest_to_json(manifest))
        return 0

    if not args.manifest:
        print("ERROR: manifest path required (or use --compute-manifest)", file=sys.stderr)
        return 2

    if not os.path.isfile(args.manifest):
        print(f"ERROR: manifest not found: {args.manifest}", file=sys.stderr)
        return 1

    raw = Path(args.manifest).read_text()
    try:
        manifest = manifest_from_json(raw)
    except Exception as exc:
        print(f"ERROR: invalid manifest JSON: {exc}", file=sys.stderr)
        return 1

    ok, errors = _verify(
        zip_path, manifest,
        require_clean=not args.allow_dirty,
    )
    if not ok:
        print("VERIFICATION FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print("VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
