"""Authenticated identity served by an immutable TrustForge release process.

The signing private key is deliberately a build/release-plane capability.  The
web process receives only the signed endpoint manifest, the artifact's release
manifest, and a public verification keyring.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import stat
import urllib.parse
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from trustforge.agent.shadow_contracts import canonical_json

SCHEMA = "trustforge.endpoint-manifest/v1"
KEYRING_SCHEMA = "trustforge.endpoint-manifest-keyring/v1"
_DOMAIN = b"trustforge.endpoint-manifest.v1\x00"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_MAX_FILE_BYTES = 32_768
_MAX_ARTIFACT_BYTES = 512 * 1024 * 1024


class EndpointManifestError(RuntimeError):
    """The running release cannot prove its immutable endpoint identity."""


def _read_config_file(path: str | Path, *, private: bool = False) -> bytes:
    target = Path(path)
    if not target.is_absolute():
        raise EndpointManifestError("release identity paths must be absolute")
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags)
    except OSError as exc:
        raise EndpointManifestError(f"cannot open release identity file: {target}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_FILE_BYTES:
            raise EndpointManifestError("release identity input is not a bounded regular file")
        if before.st_uid not in {0, os.geteuid()}:
            raise EndpointManifestError("release identity input has an unsafe owner")
        forbidden = 0o077 if private else 0o022
        if before.st_mode & forbidden:
            raise EndpointManifestError("release identity input permissions are unsafe")
        chunks: list[bytes] = []
        remaining = _MAX_FILE_BYTES + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if len(raw) > _MAX_FILE_BYTES or (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise EndpointManifestError("release identity input changed while being read")
    return raw


def _object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EndpointManifestError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise EndpointManifestError(f"{label} must be a JSON object")
    return value


def _artifact_digest(path: str | Path) -> str:
    target = Path(path)
    if not target.is_absolute():
        raise EndpointManifestError("release artifact path must be absolute")
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags)
    except OSError as exc:
        raise EndpointManifestError("cannot open immutable release artifact") from exc
    digest = hashlib.sha256()
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > _MAX_ARTIFACT_BYTES
            or before.st_uid not in {0, os.geteuid()}
            or before.st_mode & 0o022
        ):
            raise EndpointManifestError("release artifact permissions or type are unsafe")
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise EndpointManifestError("release artifact changed while being verified")
    return "sha256:" + digest.hexdigest()


def _validate_origin(origin: object) -> str:
    if not isinstance(origin, str):
        raise EndpointManifestError("endpoint origin is invalid")
    parsed = urllib.parse.urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as exc:
        raise EndpointManifestError("endpoint origin port is invalid") from exc
    if (
        parsed.scheme != "http"
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or port is None
    ):
        raise EndpointManifestError("endpoint origin must be a local HTTP origin")
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as exc:
        raise EndpointManifestError("endpoint origin must use an explicit IP") from exc
    if not address.is_loopback:
        raise EndpointManifestError("endpoint origin must be loopback")
    return origin.rstrip("/")


def create_signed_endpoint_manifest(
    *,
    artifact_digest: str,
    origin: str,
    key_id: str,
    private_key: bytes,
) -> bytes:
    """Create canonical endpoint identity; callers must keep the key off runtime."""
    if not _DIGEST.fullmatch(artifact_digest):
        raise EndpointManifestError("artifact digest is invalid")
    origin = _validate_origin(origin)
    if not _KEY_ID.fullmatch(key_id):
        raise EndpointManifestError("endpoint manifest key id is invalid")
    try:
        signer = Ed25519PrivateKey.from_private_bytes(private_key)
    except ValueError as exc:
        raise EndpointManifestError("endpoint manifest private key is invalid") from exc
    unsigned = {
        "schema": SCHEMA,
        "artifact_digest": artifact_digest,
        "origin": origin,
        "key_id": key_id,
    }
    signature = signer.sign(_DOMAIN + canonical_json(unsigned)).hex()
    return canonical_json(unsigned | {"signature": signature})


def build_signed_endpoint_manifest(
    *,
    release_manifest_path: str | Path,
    origin: str,
    key_id: str,
    private_key_path: str | Path,
) -> bytes:
    """Bind an endpoint identity to the digest emitted by the real build."""
    release = _object(_read_config_file(release_manifest_path), "release manifest")
    digest = release.get("artifact_digest")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise EndpointManifestError("release manifest artifact digest is invalid")
    key = _read_config_file(private_key_path, private=True)
    if len(key) != 32:
        raise EndpointManifestError("endpoint manifest private key must be 32 raw bytes")
    return create_signed_endpoint_manifest(
        artifact_digest=digest,
        origin=origin,
        key_id=key_id,
        private_key=key,
    )


def load_runtime_endpoint_manifest(
    *,
    endpoint_manifest_path: str | Path,
    release_manifest_path: str | Path,
    release_artifact_path: str | Path,
    public_keyring_path: str | Path,
    expected_origin: str,
) -> bytes:
    """Verify and freeze the exact bytes served by the production web process."""
    endpoint = _object(
        _read_config_file(endpoint_manifest_path), "endpoint manifest"
    )
    if set(endpoint) != {
        "schema",
        "artifact_digest",
        "origin",
        "key_id",
        "signature",
    }:
        raise EndpointManifestError("endpoint manifest schema is invalid")
    release = _object(_read_config_file(release_manifest_path), "release manifest")
    keyring = _object(_read_config_file(public_keyring_path), "public keyring")
    if set(keyring) != {"schema", "keys"} or keyring.get("schema") != KEYRING_SCHEMA:
        raise EndpointManifestError("endpoint manifest public keyring schema is invalid")
    keys = keyring.get("keys")
    if not isinstance(keys, dict) or not keys:
        raise EndpointManifestError("endpoint manifest public keyring is empty")

    origin = _validate_origin(endpoint.get("origin"))
    configured_origin = _validate_origin(expected_origin)
    digest = endpoint.get("artifact_digest")
    key_id = endpoint.get("key_id")
    if (
        endpoint.get("schema") != SCHEMA
        or not isinstance(digest, str)
        or not _DIGEST.fullmatch(digest)
        or not isinstance(key_id, str)
        or not _KEY_ID.fullmatch(key_id)
        or origin != configured_origin
        or not hmac.compare_digest(str(release.get("artifact_digest", "")), digest)
        or not hmac.compare_digest(_artifact_digest(release_artifact_path), digest)
    ):
        raise EndpointManifestError("served endpoint identity does not match release")
    encoded_key = keys.get(key_id)
    if not isinstance(encoded_key, str):
        raise EndpointManifestError("endpoint manifest verification key is absent")
    try:
        public_key = bytes.fromhex(encoded_key)
        signature = bytes.fromhex(str(endpoint.get("signature", "")))
        if len(public_key) != 32:
            raise ValueError
        unsigned: Mapping[str, Any] = {
            key: value for key, value in endpoint.items() if key != "signature"
        }
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, _DOMAIN + canonical_json(unsigned)
        )
    except (InvalidSignature, ValueError) as exc:
        raise EndpointManifestError("endpoint manifest signature is invalid") from exc
    # Re-encode rather than serving attacker-controlled whitespace or duplicate
    # JSON keys from the source file.
    return canonical_json(endpoint)


def load_runtime_endpoint_manifest_from_env() -> bytes | None:
    """Load A/B identity when enabled; all required inputs then fail closed."""
    required = os.getenv("TRUSTFORGE_RELEASE_IDENTITY_REQUIRED", "") == "1"
    names = {
        "endpoint_manifest_path": "TRUSTFORGE_ENDPOINT_MANIFEST_PATH",
        "release_manifest_path": "TRUSTFORGE_RELEASE_MANIFEST_PATH",
        "release_artifact_path": "TRUSTFORGE_RELEASE_ARTIFACT_PATH",
        "public_keyring_path": "TRUSTFORGE_ENDPOINT_MANIFEST_KEYRING_PATH",
        "expected_origin": "TRUSTFORGE_RELEASE_ORIGIN",
    }
    values = {name: os.getenv(env, "").strip() for name, env in names.items()}
    configured = any(values.values())
    if not required and not configured:
        return None
    if not required:
        raise EndpointManifestError(
            "release identity inputs require TRUSTFORGE_RELEASE_IDENTITY_REQUIRED=1"
        )
    missing = [names[name] for name, value in values.items() if not value]
    if missing:
        raise EndpointManifestError(
            "required release identity configuration is absent: " + ", ".join(missing)
        )
    return load_runtime_endpoint_manifest(**values)
