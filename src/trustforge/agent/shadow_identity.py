"""Measured release identity and dedicated-runtime startup attestation."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from dataclasses import dataclass

import trustforge_core

from trustforge.release_manifest import manifest_from_json

from .shadow_contracts import (
    CONTRACT_VERSION,
    ShadowContractError,
    ShadowPolicy,
    ShadowReleaseIdentity,
    policy_digest,
)

ATTESTATION_VERSION = "trustforge.shadow-runtime-attestation/v1"
_ATTESTATION_FLAG = "TRUSTFORGE_SHADOW_DEDICATED_RUNTIME"
_ATTESTATION_PATH = "TRUSTFORGE_SHADOW_RUNTIME_ATTESTATION_PATH"


@dataclass(frozen=True, slots=True)
class MeasuredShadowRelease:
    identity: ShadowReleaseIdentity
    candidate_contract_version: str


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_STAT_FIELDS = ("st_dev", "st_ino", "st_mode", "st_uid", "st_size", "st_mtime_ns", "st_ctime_ns")


def _snapshot(value: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(value, name) for name in _STAT_FIELDS)


def _trusted_owner(value: os.stat_result) -> bool:
    return value.st_uid in {0, os.geteuid()}


def _trusted_directory(value: os.stat_result) -> bool:
    if not stat.S_ISDIR(value.st_mode) or not _trusted_owner(value):
        return False
    if not value.st_mode & 0o022:
        return True
    return value.st_uid == 0 and bool(value.st_mode & stat.S_ISVTX)


def _open_secure(path: Path, *, owner_only: bool) -> int:
    if not path.is_absolute():
        raise ShadowContractError("identity path must be absolute")
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise ShadowContractError("identity path cannot be resolved") from exc
    directory_fd = os.open("/", os.O_RDONLY | _DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
                dir_fd=directory_fd,
            )
            value = os.fstat(next_fd)
            if not _trusted_directory(value):
                os.close(next_fd)
                raise ShadowContractError("identity parent is not trusted")
            os.close(directory_fd)
            directory_fd = next_fd
        descriptor = os.open(
            path.name, os.O_RDONLY | _NOFOLLOW, dir_fd=directory_fd,
        )
    except OSError as exc:
        raise ShadowContractError("identity path cannot be opened safely") from exc
    finally:
        os.close(directory_fd)
    value = os.fstat(descriptor)
    invalid_mode = value.st_mode & (0o077 if owner_only else 0o022)
    required_owner = value.st_uid == os.geteuid() if owner_only else _trusted_owner(value)
    if not stat.S_ISREG(value.st_mode) or not required_owner or invalid_mode:
        os.close(descriptor)
        raise ShadowContractError("identity file metadata is not trusted")
    return descriptor


def _read_descriptor(descriptor: int, *, max_bytes: int | None) -> bytes:
    before = os.fstat(descriptor)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 65_536)
        if not chunk:
            break
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise ShadowContractError("identity file exceeds size limit")
        chunks.append(chunk)
    after = os.fstat(descriptor)
    if _snapshot(before) != _snapshot(after) or total != before.st_size:
        raise ShadowContractError("identity file changed during snapshot")
    return b"".join(chunks)


def _read_secure(path: Path, *, owner_only: bool, max_bytes: int) -> bytes:
    descriptor = _open_secure(path, owner_only=owner_only)
    try:
        return _read_descriptor(descriptor, max_bytes=max_bytes)
    finally:
        os.close(descriptor)


def _hash_secure(path: Path) -> str:
    descriptor = _open_secure(path, owner_only=False)
    try:
        digest = hashlib.sha256()
        before = os.fstat(descriptor)
        total = 0
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        if _snapshot(before) != _snapshot(after) or total != before.st_size:
            raise ShadowContractError("artifact changed during digest")
        return "sha256:" + digest.hexdigest()
    finally:
        os.close(descriptor)


def measured_release_identity(policy: ShadowPolicy) -> MeasuredShadowRelease:
    if os.environ.get(_ATTESTATION_FLAG) != "1":
        raise ShadowContractError("dedicated shadow runtime is not attested")
    attestation_path = Path(os.environ[_ATTESTATION_PATH])
    raw = _read_secure(attestation_path, owner_only=True, max_bytes=16_384)
    payload = json.loads(raw)
    if set(payload) != {
        "version", "dedicated_runtime", "active_manifest_path", "active_artifact_path",
    }:
        raise ShadowContractError("runtime attestation fields are not exact")
    if (
        payload["version"] != ATTESTATION_VERSION
        or payload["dedicated_runtime"] is not True
    ):
        raise ShadowContractError("runtime attestation is invalid")

    manifest_path = Path(payload["active_manifest_path"])
    artifact_path = Path(payload["active_artifact_path"])
    manifest = manifest_from_json(
        _read_secure(
            manifest_path, owner_only=False, max_bytes=65_536,
        ).decode("utf-8")
    )
    measured_active_digest = _hash_secure(artifact_path)
    if measured_active_digest != manifest.artifact_digest:
        raise ShadowContractError("active artifact does not match release manifest")
    if len(manifest.git_sha) != 40 or any(
        character not in "0123456789abcdef" for character in manifest.git_sha
    ):
        raise ShadowContractError("active manifest git SHA is not immutable")

    root = Path(__file__).resolve(strict=True).parents[3]
    reviewed_raw = _read_secure(
        root / "data/contracts/reviewed-shadow-candidate.v1.json",
        owner_only=False,
        max_bytes=65_536,
    )
    reviewed = json.loads(reviewed_raw)
    if (
        set(reviewed) != {
            "manifest_version", "release", "contract_version", "files",
        }
        or reviewed["manifest_version"]
        != "trustforge.reviewed-shadow-candidate/v1"
        or not isinstance(reviewed["files"], dict)
        or not reviewed["files"]
    ):
        raise ShadowContractError("reviewed candidate manifest is invalid")
    for relative, expected_digest in reviewed["files"].items():
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or _hash_secure(root / relative) != expected_digest
        ):
            raise ShadowContractError("reviewed candidate file digest mismatch")
    measured_candidate_digest = "sha256:" + hashlib.sha256(reviewed_raw).hexdigest()
    active_release = f"release:trustforge@{manifest.app_version}+{manifest.git_sha[:12]}"
    candidate_contract_version = reviewed["contract_version"]
    candidate_release = (
        f"{reviewed['release']}+"
        f"{measured_candidate_digest.removeprefix('sha256:')[:12]}"
    )
    expected = {
        "TRUSTFORGE_SHADOW_ACTIVE_RELEASE": active_release,
        "TRUSTFORGE_SHADOW_CANDIDATE_RELEASE": candidate_release,
        "TRUSTFORGE_SHADOW_ACTIVE_ARTIFACT_DIGEST": measured_active_digest,
        "TRUSTFORGE_SHADOW_CANDIDATE_ARTIFACT_DIGEST": measured_candidate_digest,
    }
    if any(os.environ.get(name) != value for name, value in expected.items()):
        raise ShadowContractError(
            "configured shadow identity does not match measured release metadata"
        )
    return MeasuredShadowRelease(
        identity=ShadowReleaseIdentity(
            active_release=active_release,
            candidate_release=candidate_release,
            active_artifact_digest=measured_active_digest,
            candidate_artifact_digest=measured_candidate_digest,
            policy_digest=policy_digest(policy),
            contract_version=CONTRACT_VERSION,
        ),
        candidate_contract_version=candidate_contract_version,
    )


def verify_reviewed_loaded_candidate(
    kernel_fn, mapper_fn, expected_contract_version: str,
) -> None:
    """Prove the executed callables originate in the reviewed in-release tree."""
    root = Path(__file__).resolve(strict=True).parents[3]
    expected = {
        "trustforge_core.scoring": root / "src/trustforge_core/scoring.py",
        "trustforge.agent.kernel_mapper": (
            root / "src/trustforge/agent/kernel_mapper.py"
        ),
    }
    for module_name, expected_path in expected.items():
        module = sys.modules.get(module_name)
        origin = getattr(module, "__file__", None)
        if (
            module is None
            or origin is None
            or Path(origin).resolve(strict=True) != expected_path.resolve(strict=True)
        ):
            raise ShadowContractError("loaded candidate module origin is not reviewed")
    if (
        kernel_fn.__module__ != "trustforge_core.scoring"
        or mapper_fn.__module__ != "trustforge.agent.kernel_mapper"
        or trustforge_core.KERNEL_CONTRACT_VERSION != expected_contract_version
    ):
        raise ShadowContractError("loaded candidate callable is not reviewed")
