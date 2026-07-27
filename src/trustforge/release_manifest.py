from __future__ import annotations

import hashlib
import json
import os
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from trustforge import __version__
from trustforge.upgrade_control import _core_hash


MANIFEST_VERSION = "trustforge.release-manifest/v1"

ARTIFACT_INDEX_KEY = "artifacts/index.jsonl"
POINTERS_PREFIX = "pointers/"
ACTIVE_POINTER = "pointers/active.json"
CANDIDATE_POINTER = "pointers/candidate.json"
PREVIOUS_POINTER = "pointers/previous.json"


@dataclass(frozen=True)
class ReleaseManifest:
    artifact_digest: str
    git_sha: str
    app_version: str
    kernel_contract_version: str
    kernel_resolution_version: str
    core_content_hash: str
    config_snapshot_identity: str
    build_timestamp: str
    build_host: str
    manifest_version: str = MANIFEST_VERSION

    def to_json(self) -> str:
        d = {
            "artifact_digest": self.artifact_digest,
            "git_sha": self.git_sha,
            "app_version": self.app_version,
            "kernel_contract_version": self.kernel_contract_version,
            "kernel_resolution_version": self.kernel_resolution_version,
            "core_content_hash": self.core_content_hash,
            "config_snapshot_identity": self.config_snapshot_identity,
            "build_timestamp": self.build_timestamp,
            "build_host": self.build_host,
            "manifest_version": self.manifest_version,
        }
        return json.dumps(d, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> ReleaseManifest:
        d = json.loads(raw)
        return cls(**d)


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _kernel_versions() -> tuple[str, str]:
    try:
        from trustforge.trust.kernel import KERNEL_CONTRACT_VERSION
    except ImportError:
        KERNEL_CONTRACT_VERSION = "0"
    try:
        from trustforge.direction_resolution import DIRECTION_POLICY_VERSION as _RES_VERSION
    except ImportError:
        _RES_VERSION = "0"
    return KERNEL_CONTRACT_VERSION, _RES_VERSION


def _sha256_of_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _zip_entry_sha256(zf: zipfile.ZipFile, name: str) -> str:
    with zf.open(name) as f:
        digest = hashlib.sha256()
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compute_all_zip_sha256s(zip_path: str) -> dict[str, str]:
    result: dict[str, str] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in sorted(zf.namelist()):
            info = zf.getinfo(name)
            if info.is_dir():
                continue
            result[name] = _zip_entry_sha256(zf, name)
    return result


def compute_manifest(
    zip_path: str,
    config_snapshot_bytes: bytes,
    *,
    build_host: str | None = None,
) -> ReleaseManifest:
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"zip not found: {zip_path}")

    artifact_digest = _sha256_of_file(zip_path)
    git_sha = _git_sha()
    contract_ver, resolution_ver = _kernel_versions()
    core_hash = _core_hash()
    config_identity = "sha256:" + hashlib.sha256(config_snapshot_bytes).hexdigest()
    build_timestamp = datetime.now(timezone.utc).isoformat()
    if build_host is None:
        build_host = os.uname().nodename if hasattr(os, "uname") else "unknown"

    return ReleaseManifest(
        artifact_digest=artifact_digest,
        git_sha=git_sha,
        app_version=__version__,
        kernel_contract_version=contract_ver,
        kernel_resolution_version=resolution_ver,
        core_content_hash=core_hash,
        config_snapshot_identity=config_identity,
        build_timestamp=build_timestamp,
        build_host=build_host,
    )


def validate_manifest(manifest: ReleaseManifest, zip_path: str) -> tuple[bool, list[str]]:
    errors: list[str] = []

    if manifest.manifest_version != MANIFEST_VERSION:
        errors.append(f"manifest_version mismatch: expected {MANIFEST_VERSION}, got {manifest.manifest_version}")

    if not manifest.artifact_digest.startswith("sha256:"):
        errors.append(f"artifact_digest missing sha256: prefix: {manifest.artifact_digest}")
    else:
        if not os.path.isfile(zip_path):
            errors.append(f"zip file not found: {zip_path}")
        else:
            actual_digest = _sha256_of_file(zip_path)
            if actual_digest != manifest.artifact_digest:
                errors.append(f"artifact_digest mismatch: manifest={manifest.artifact_digest} actual={actual_digest}")

    if manifest.core_content_hash != _core_hash():
        errors.append(f"core_content_hash mismatch: manifest={manifest.core_content_hash} actual={_core_hash()}")

    if _is_dirty():
        errors.append("dirty build detected: uncommitted changes present")

    return len(errors) == 0, errors


def _is_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-uno"],
            capture_output=True, text=True, check=True,
        )
        return bool(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def manifest_to_json(manifest: ReleaseManifest) -> str:
    return manifest.to_json()


def manifest_from_json(raw: str) -> ReleaseManifest:
    return ReleaseManifest.from_json(raw)


def artifact_index_key(digest: str) -> str:
    hex_part = digest.split(":", 1)[-1] if ":" in digest else digest
    return f"artifacts/{hex_part}/"
