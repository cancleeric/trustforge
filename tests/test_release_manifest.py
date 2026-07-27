from __future__ import annotations

import hashlib
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trustforge.release_manifest import (
    MANIFEST_VERSION,
    ReleaseManifest,
    artifact_index_key,
    compute_manifest,
    manifest_from_json,
    manifest_to_json,
    validate_manifest,
)


def _make_test_zip(tmp_path: Path) -> str:
    zip_path = str(tmp_path / "test.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("trustforge/__init__.py", '__version__ = "0.0.1"\n')
        zf.writestr("trustforge/web.py", "# placeholder\n")
        zf.writestr("data/hello.txt", "hello\n")
    return zip_path


def test_compute_manifest_fields(tmp_path: Path) -> None:
    zip_path = _make_test_zip(tmp_path)
    config_bytes = b'{"csp_mode":"legacy"}'
    manifest = compute_manifest(zip_path, config_bytes, build_host="testhost")

    assert manifest.artifact_digest.startswith("sha256:")
    assert manifest.manifest_version == MANIFEST_VERSION
    assert manifest.app_version is not None
    assert manifest.build_host == "testhost"
    assert manifest.build_timestamp is not None
    assert manifest.config_snapshot_identity.startswith("sha256:")

    expected_config_id = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
    assert manifest.config_snapshot_identity == expected_config_id


def test_manifest_roundtrip() -> None:
    manifest = ReleaseManifest(
        artifact_digest="sha256:abc123",
        git_sha="deadbeef",
        app_version="0.0.1",
        kernel_contract_version="1.0.0",
        kernel_resolution_version="1.0.0",
        core_content_hash="sha256:coreabc",
        config_snapshot_identity="sha256:configabc",
        build_timestamp=datetime.now(timezone.utc).isoformat(),
        build_host="host",
    )
    raw = manifest_to_json(manifest)
    restored = manifest_from_json(raw)
    assert restored == manifest

    parsed = json.loads(raw)
    assert parsed["artifact_digest"] == "sha256:abc123"
    assert parsed["manifest_version"] == MANIFEST_VERSION
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_validate_manifest_passes(tmp_path: Path) -> None:
    zip_path = _make_test_zip(tmp_path)
    config_bytes = b'{}'
    manifest = compute_manifest(zip_path, config_bytes)

    ok, errors = validate_manifest(manifest, zip_path)
    dirty_errors = [e for e in errors if "dirty build" in e]
    non_dirty_errors = [e for e in errors if "dirty build" not in e]
    assert len(non_dirty_errors) == 0
    if dirty_errors:
        assert len(dirty_errors) == 1


def test_validate_manifest_tamper_digest(tmp_path: Path) -> None:
    zip_path = _make_test_zip(tmp_path)
    config_bytes = b'{}'
    manifest = compute_manifest(zip_path, config_bytes)

    tampered = ReleaseManifest(
        artifact_digest="sha256:0000000000000000000000000000000000000000000000000000000000000000",
        git_sha=manifest.git_sha,
        app_version=manifest.app_version,
        kernel_contract_version=manifest.kernel_contract_version,
        kernel_resolution_version=manifest.kernel_resolution_version,
        core_content_hash=manifest.core_content_hash,
        config_snapshot_identity=manifest.config_snapshot_identity,
        build_timestamp=manifest.build_timestamp,
        build_host=manifest.build_host,
    )

    ok, errors = validate_manifest(tampered, zip_path)
    assert ok is False
    assert any("artifact_digest mismatch" in e for e in errors)


def test_validate_manifest_tamper_core_hash(tmp_path: Path) -> None:
    zip_path = _make_test_zip(tmp_path)
    config_bytes = b'{}'
    manifest = compute_manifest(zip_path, config_bytes)

    tampered = ReleaseManifest(
        artifact_digest=manifest.artifact_digest,
        git_sha=manifest.git_sha,
        app_version=manifest.app_version,
        kernel_contract_version=manifest.kernel_contract_version,
        kernel_resolution_version=manifest.kernel_resolution_version,
        core_content_hash="sha256:badbeef",
        config_snapshot_identity=manifest.config_snapshot_identity,
        build_timestamp=manifest.build_timestamp,
        build_host=manifest.build_host,
    )

    ok, errors = validate_manifest(tampered, zip_path)
    assert ok is False
    assert any("core_content_hash mismatch" in e for e in errors)


def test_validate_manifest_missing_zip(tmp_path: Path) -> None:
    manifest = ReleaseManifest(
        artifact_digest="sha256:abc123",
        git_sha="deadbeef",
        app_version="0.0.1",
        kernel_contract_version="1.0.0",
        kernel_resolution_version="1.0.0",
        core_content_hash="sha256:coreabc",
        config_snapshot_identity="sha256:configabc",
        build_timestamp=datetime.now(timezone.utc).isoformat(),
        build_host="host",
    )

    ok, errors = validate_manifest(manifest, str(tmp_path / "nonexistent.zip"))
    assert ok is False
    assert any("zip file not found" in e for e in errors)


def test_validate_manifest_wrong_version() -> None:
    manifest = ReleaseManifest(
        artifact_digest="sha256:abc123",
        git_sha="deadbeef",
        app_version="0.0.1",
        kernel_contract_version="1.0.0",
        kernel_resolution_version="1.0.0",
        core_content_hash="sha256:coreabc",
        config_snapshot_identity="sha256:configabc",
        build_timestamp=datetime.now(timezone.utc).isoformat(),
        build_host="host",
        manifest_version="trustforge.release-manifest/v0",
    )

    ok, errors = validate_manifest(manifest, "nonexistent")
    assert ok is False
    assert any("manifest_version mismatch" in e for e in errors)


def test_compute_manifest_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        compute_manifest("/does/not/exist.zip", b"{}")


def test_artifact_index_key() -> None:
    assert artifact_index_key("sha256:abc123") == "artifacts/abc123/"
    assert artifact_index_key("abc123") == "artifacts/abc123/"


def test_manifest_json_sorts_keys() -> None:
    manifest = ReleaseManifest(
        artifact_digest="sha256:abc",
        git_sha="dead",
        app_version="1.0.0",
        kernel_contract_version="1.0.0",
        kernel_resolution_version="1.0.0",
        core_content_hash="sha256:core",
        config_snapshot_identity="sha256:cfg",
        build_timestamp="2024-01-01T00:00:00+00:00",
        build_host="h",
    )
    raw = manifest_to_json(manifest)
    keys = list(json.loads(raw).keys())
    assert keys == sorted(keys)
