from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.endpoint_manifest import (
    KEYRING_SCHEMA,
    EndpointManifestError,
    build_signed_endpoint_manifest,
    load_runtime_endpoint_manifest,
)


def _write(path: Path, value: bytes, mode: int) -> None:
    path.write_bytes(value)
    path.chmod(mode)


def _identity_files(
    tmp_path: Path, origin: str
) -> tuple[Path, Path, Path, Path, bytes]:
    artifact = tmp_path / "artifact.zip"
    _write(artifact, b"real immutable release artifact", 0o444)
    digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    release = tmp_path / "release.json"
    private = tmp_path / "endpoint.key"
    endpoint = tmp_path / "endpoint.json"
    keyring = tmp_path / "endpoint-keys.json"
    signer = Ed25519PrivateKey.from_private_bytes(b"e" * 32)
    public = signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    _write(release, json.dumps({"artifact_digest": digest}).encode(), 0o444)
    _write(private, b"e" * 32, 0o600)
    body = build_signed_endpoint_manifest(
        release_manifest_path=release,
        origin=origin,
        key_id="endpoint-2026-07",
        private_key_path=private,
    )
    _write(endpoint, body, 0o444)
    _write(
        keyring,
        json.dumps(
            {
                "schema": KEYRING_SCHEMA,
                "keys": {"endpoint-2026-07": public.hex()},
            }
        ).encode(),
        0o444,
    )
    return endpoint, release, artifact, keyring, body


def test_runtime_manifest_rejects_tampered_release_binding(tmp_path: Path) -> None:
    origin = "http://127.0.0.1:18081"
    endpoint, release, artifact, keyring, _body = _identity_files(tmp_path, origin)
    release.chmod(0o644)
    release.write_text(json.dumps({"artifact_digest": "sha256:" + "b" * 64}))
    release.chmod(0o444)
    with pytest.raises(EndpointManifestError, match="does not match release"):
        load_runtime_endpoint_manifest(
            endpoint_manifest_path=endpoint,
            release_manifest_path=release,
            release_artifact_path=artifact,
            public_keyring_path=keyring,
            expected_origin=origin,
        )


def test_runtime_manifest_rejects_writable_identity_file(tmp_path: Path) -> None:
    origin = "http://127.0.0.1:18082"
    endpoint, release, artifact, keyring, _body = _identity_files(tmp_path, origin)
    endpoint.chmod(0o666)
    with pytest.raises(EndpointManifestError, match="permissions"):
        load_runtime_endpoint_manifest(
            endpoint_manifest_path=endpoint,
            release_manifest_path=release,
            release_artifact_path=artifact,
            public_keyring_path=keyring,
            expected_origin=origin,
        )


def test_runtime_manifest_rejects_artifact_bytes_not_matching_digest(
    tmp_path: Path,
) -> None:
    origin = "http://127.0.0.1:18083"
    endpoint, release, artifact, keyring, _body = _identity_files(tmp_path, origin)
    artifact.chmod(0o644)
    artifact.write_bytes(b"substituted artifact")
    artifact.chmod(0o444)
    with pytest.raises(EndpointManifestError, match="does not match release"):
        load_runtime_endpoint_manifest(
            endpoint_manifest_path=endpoint,
            release_manifest_path=release,
            release_artifact_path=artifact,
            public_keyring_path=keyring,
            expected_origin=origin,
        )


def test_real_production_web_entrypoint_serves_signed_artifact_identity(
    tmp_path: Path,
) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    endpoint, release, artifact, keyring, expected = _identity_files(tmp_path, origin)
    env = os.environ.copy()
    env.update(
        {
            "PORT": str(port),
            "TRUSTFORGE_BIND_HOST": "127.0.0.1",
            "TRUSTFORGE_RELEASE_IDENTITY_REQUIRED": "1",
            "TRUSTFORGE_ENDPOINT_MANIFEST_PATH": str(endpoint),
            "TRUSTFORGE_RELEASE_MANIFEST_PATH": str(release),
            "TRUSTFORGE_RELEASE_ARTIFACT_PATH": str(artifact),
            "TRUSTFORGE_ENDPOINT_MANIFEST_KEYRING_PATH": str(keyring),
            "TRUSTFORGE_RELEASE_ORIGIN": origin,
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "trustforge.web"],
        cwd=Path(__file__).parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(f"production web exited early: {stdout}\n{stderr}")
            try:
                with urllib.request.urlopen(origin + "/healthz", timeout=0.1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.02)
        else:
            pytest.fail("production web did not become healthy")
        with urllib.request.urlopen(
            origin + "/.well-known/trustforge-release-manifest", timeout=1
        ) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "application/json; charset=utf-8"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.read() == expected
    finally:
        process.terminate()
        process.wait(timeout=5)
